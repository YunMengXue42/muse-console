"""Minimal asyncio Chrome DevTools Protocol client."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)


class CDPError(RuntimeError):
    pass


class CDPSession:
    def __init__(self, ws: ClientConnection) -> None:
        self._ws = ws
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
        self._reader = asyncio.create_task(self._read_loop())

    @classmethod
    async def connect(cls, ws_url: str, *, max_size: int = 48 * 1024 * 1024) -> CDPSession:
        ws = await connect(
            ws_url, max_size=max_size, ping_interval=20, ping_timeout=20, open_timeout=15
        )
        return cls(ws)

    @property
    def closed(self) -> bool:
        return self._reader.done()

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut and not fut.done():
                        if "error" in msg:
                            code = msg["error"].get("code")
                            code = code if type(code) is int else "unknown"
                            fut.set_exception(CDPError(f"cdp protocol error (code={code})"))
                        else:
                            fut.set_result(msg.get("result", {}))
                elif method := msg.get("method"):
                    for cb in self._listeners.get(method, ()):
                        try:
                            cb(msg.get("params", {}))
                        except Exception:  # noqa: BLE001
                            log.warning("code=cdp_listener_failed")
        except Exception:  # noqa: BLE001
            log.debug("code=cdp_reader_stopped")
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CDPError("cdp connection closed"))
            self._pending.clear()

    def on(self, method: str, callback: Callable[[dict], None]) -> None:
        self._listeners[method].append(callback)

    async def send(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        if self.closed:
            raise CDPError("cdp connection closed")
        msg_id = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._ws.send(
                json.dumps({"id": msg_id, "method": method, "params": params or {}})
            )
            return await asyncio.wait_for(fut, timeout)
        except ConnectionClosed as exc:
            raise CDPError("cdp connection closed") from exc
        finally:
            self._pending.pop(msg_id, None)
            if not fut.done():
                fut.cancel()

    async def evaluate(
        self, expression: str, *, await_promise: bool = False, timeout: float = 30.0
    ) -> Any:
        res = await self.send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": await_promise},
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            # JS descriptions/stacks can contain prompts, page text and signed URLs.
            raise CDPError("javascript evaluation failed (code=cdp_evaluation_failed)")
        return res.get("result", {}).get("value")

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._ws.close()
        self._reader.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._reader
