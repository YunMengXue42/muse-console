"""Temporary, loopback-only inference boundary for a friend-access tunnel."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1] / "data-preview" / "public-access"
UPSTREAM = "http://127.0.0.1:18778"
POST_PATHS = {"/v1/chat/completions", "/v1/responses", "/v1/images/generations", "/v1/videos"}
TASK = re.compile(r"^/v1/(?:videos|images/generations)/(task_[A-Za-z0-9_-]+)$")
MEDIA = re.compile(r"^/v1/media/((?:img|vid)_[a-f0-9]{16}\.[a-z0-9]+)$")
MAX_BODY = 8 * 1024 * 1024


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "access_error"}}, status_code=status)


def create_app(key: str, expires: float, transport=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            base_url=UPSTREAM,
            trust_env=False,
            timeout=600,
            transport=transport,
            follow_redirects=False,
        ) as client:
            app.state.client = client
            yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    tasks: set[str] = set()
    media: set[str] = set()
    requests: deque[float] = deque()

    def signature(name: str) -> str:
        return hmac.new(key.encode(), name.encode(), hashlib.sha256).hexdigest()

    def public_origin() -> str:
        origin = (ROOT / "public-url.txt").read_text().strip()
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".trycloudflare.com"):
            raise ValueError("invalid public origin")
        return origin.rstrip("/")

    def rewrite(value):
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        if isinstance(value, str):
            match = MEDIA.fullmatch(urlsplit(value).path)
            if match and value.startswith((UPSTREAM, "http://localhost:18778")):
                name = match[1]
                media.add(name)
                return f"{public_origin()}/v1/media/{name}?sig={signature(name)}"
        return value

    @app.api_route(
        "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
    )
    async def proxy(request: Request, path: str):
        route = "/" + path
        task_match, media_match = TASK.fullmatch(route), MEDIA.fullmatch(route)
        allowed = (
            request.method == "POST"
            and route in POST_PATHS
            or request.method == "GET"
            and (route == "/v1/models" or task_match or media_match)
        )
        if not allowed or "%" in request.scope.get("raw_path", b"").decode("ascii", "ignore"):
            return error(404, "Not found")
        if time.time() >= expires:
            return error(403, "Temporary access expired")
        supplied = request.headers.get("authorization", "")
        supplied = (
            supplied[7:].strip()
            if supplied.lower().startswith("bearer ")
            else request.headers.get("x-api-key", "")
        )
        signed = bool(
            media_match
            and media_match[1] in media
            and hmac.compare_digest(request.query_params.get("sig", ""), signature(media_match[1]))
        )
        if not signed and not hmac.compare_digest(supplied.encode(), key.encode()):
            return error(401, "Invalid or missing API key")
        if task_match and task_match[1] not in tasks:
            return error(404, "Task not found")
        if media_match and media_match[1] not in media:
            return error(404, "Media not found")
        body = b""
        if request.method == "POST":
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return error(415, "Use application/json")
            async for chunk in request.stream():
                body += chunk
                if len(body) > MAX_BODY:
                    return error(413, "Request too large")
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                return error(400, "Invalid JSON")
            if not isinstance(payload, dict):
                return error(400, "Expected JSON object")
            if payload.get("stream"):
                return error(400, "Temporary endpoint supports stream=false only")
            now = time.monotonic()
            while requests and now - requests[0] > 60:
                requests.popleft()
            if len(requests) >= 10:
                return error(429, "Limit: 10 generation requests per minute")
            requests.append(now)
        try:
            upstream = await app.state.client.request(
                request.method,
                route,
                content=body,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            if media_match:
                return Response(
                    upstream.content,
                    status_code=upstream.status_code,
                    media_type=upstream.headers.get("content-type", "application/octet-stream"),
                    headers={
                        "Cache-Control": "private, no-store",
                        "X-Content-Type-Options": "nosniff",
                    },
                )
            data = upstream.json()
            if upstream.is_success and request.method == "POST" and isinstance(data, dict):
                task_id = data.get("id", "")
                if isinstance(task_id, str) and task_id.startswith("task_"):
                    tasks.add(task_id)
            return JSONResponse(
                rewrite(data),
                status_code=upstream.status_code,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
        except (httpx.HTTPError, ValueError, OSError):
            return error(502, "Upstream unavailable; retry later")

    return app


if __name__ == "__main__":
    import uvicorn

    config = json.loads((ROOT / "friend-key.json").read_text())
    app = create_app(config["api_key"], config["key"]["created_at"] + 86400)
    uvicorn.run(app, host="127.0.0.1", port=18779, proxy_headers=False, access_log=False)
