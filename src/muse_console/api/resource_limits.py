"""bound request buffering and generation admission before parsing or downloading inputs."""

from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_GENERATION_PATHS = {
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/images/generations",
    "/v1/images/edits",
    "/v1/videos",
}


class ResourceLimitsMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int, max_inflight: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.max_inflight = max_inflight
        self._inflight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        generation = scope["method"] == "POST" and scope["path"] in _GENERATION_PATHS
        if generation and self._inflight >= self.max_inflight:
            await self._error(
                scope,
                receive,
                send,
                429,
                "capacity_exceeded",
                "generation capacity is full; retry later",
            )
            return
        if generation:
            self._inflight += 1
        try:
            body = bytearray()
            if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
                for key, value in scope.get("headers", []):
                    if key == b"content-length":
                        try:
                            length = int(value)
                        except ValueError:
                            length = -1
                        if length < 0 or length > self.max_bytes:
                            await self._error(
                                scope,
                                receive,
                                send,
                                413,
                                "request_too_large",
                                "request body exceeds the size limit",
                            )
                            return
                deadline = asyncio.get_running_loop().time() + 30
                while True:
                    try:
                        message = await asyncio.wait_for(
                            receive(),
                            timeout=max(0.001, deadline - asyncio.get_running_loop().time()),
                        )
                    except asyncio.TimeoutError:
                        await self._error(
                            scope, receive, send, 408, "request_timeout", "request body timed out"
                        )
                        return
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.max_bytes:
                        await self._error(
                            scope,
                            receive,
                            send,
                            413,
                            "request_too_large",
                            "request body exceeds the size limit",
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
                delivered = False

                async def buffered_receive():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()

                await self.app(scope, buffered_receive, send)
            else:
                await self.app(scope, receive, send)
        finally:
            if generation:
                self._inflight -= 1

    @staticmethod
    async def _error(scope, receive, send, status: int, code: str, message: str) -> None:
        response = JSONResponse(
            {
                "error": {
                    "type": "rate_limit_error" if status == 429 else "invalid_request_error",
                    "code": code,
                    "message": message,
                }
            },
            status_code=status,
            headers={"Retry-After": "5"} if status == 429 else None,
        )
        await response(scope, receive, send)
