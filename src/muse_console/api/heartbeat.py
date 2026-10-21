"""Optional SSE heartbeats while a streaming request waits for its first token."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

HEARTBEAT = b": keepalive\n\n"
_FIRST = object()


async def first_with_heartbeats(
    stream: AsyncIterator[str], interval: float
) -> AsyncIterator[bytes | tuple[object, str]]:
    """Yield HEARTBEAT every ``interval`` seconds until the first delta arrives.

    The first delta is yielded as ``(_FIRST, text)`` (empty text when the stream
    ended without output). Upstream exceptions propagate to the caller unchanged.
    """
    task = asyncio.ensure_future(anext(stream))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=interval)
            if done:
                try:
                    yield (_FIRST, task.result())
                except StopAsyncIteration:
                    yield (_FIRST, "")
                return
            yield HEARTBEAT
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, StopAsyncIteration, Exception):  # noqa: BLE001
                pass


def is_first(item: object) -> bool:
    return isinstance(item, tuple) and len(item) == 2 and item[0] is _FIRST
