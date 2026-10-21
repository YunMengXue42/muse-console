from __future__ import annotations

import asyncio
import json

import pytest

from muse_console.api.resource_limits import ResourceLimitsMiddleware


def _scope(path="/v1/chat/completions", headers=None):
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers or [],
        "http_version": "1.1",
    }


@pytest.mark.asyncio
async def test_chunked_body_is_bounded_before_app():
    called = []
    sent = []
    chunks = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )

    async def app(scope, receive, send):
        called.append(True)

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    middleware = ResourceLimitsMiddleware(app, max_bytes=5, max_inflight=1)
    await middleware(_scope(), receive, send)
    assert not called
    assert sent[0]["status"] == 413
    assert middleware._inflight == 0


@pytest.mark.asyncio
async def test_inflight_rejected_without_consuming_body_and_released():
    entered = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def app(scope, receive, send):
        await receive()
        entered.set()
        await release.wait()
        raise RuntimeError("disconnect")

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def unused_receive():
        raise AssertionError("full capacity must reject before consuming")

    async def send(message):
        sent.append(message)

    middleware = ResourceLimitsMiddleware(app, max_bytes=5, max_inflight=1)
    first = asyncio.create_task(middleware(_scope(), receive, send))
    await entered.wait()
    await middleware(_scope(), unused_receive, send)
    assert sent[0]["status"] == 429
    body = json.loads(sent[1]["body"])
    assert body["error"]["code"] == "capacity_exceeded"
    release.set()
    with pytest.raises(RuntimeError):
        await first
    assert middleware._inflight == 0


def test_request_limits_return_api_errors(client, auth):
    svc = client.app.state.services
    response = client.post(
        "/v1/chat/completions",
        headers={**auth, "Content-Length": str(svc.settings.request_max_bytes + 1)},
        content=b"{}",
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert client.get("/healthz").status_code == 200
