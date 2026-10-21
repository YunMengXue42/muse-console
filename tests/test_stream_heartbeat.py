"""C4: optional SSE heartbeats while waiting for the first token."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from muse_console.api.heartbeat import HEARTBEAT, first_with_heartbeats, is_first
from muse_console.app import create_app
from muse_console.drivers.mock import MockDriver
from muse_console.errors import UpstreamTimeout


async def _slow(first_delay: float, chunks=("hi", " there")):
    await asyncio.sleep(first_delay)
    for c in chunks:
        yield c


async def test_heartbeats_until_first_token():
    out = [item async for item in first_with_heartbeats(_slow(0.12), 0.03)]
    assert out[-1] == next(i for i in out if is_first(i)) and out[-1][1] == "hi"
    assert out.count(HEARTBEAT) >= 2


async def test_heartbeat_propagates_errors_and_empty_streams():
    async def boom():
        await asyncio.sleep(0.05)
        raise UpstreamTimeout("first token timed out")
        yield ""  # pragma: no cover

    with pytest.raises(UpstreamTimeout):
        [item async for item in first_with_heartbeats(boom(), 0.01)]

    async def empty():
        return
        yield ""  # pragma: no cover

    assert [item async for item in first_with_heartbeats(empty(), 0.01)][-1][1] == ""


class SlowFirstDriver(MockDriver):
    async def chat_stream(self, account, req):
        await asyncio.sleep(0.25)
        async for delta in super().chat_stream(account, req):
            yield delta


class FailingDriver(MockDriver):
    async def chat_stream(self, account, req):
        await asyncio.sleep(0.1)
        raise UpstreamTimeout("first token timed out")
        yield ""  # pragma: no cover


def _client(settings, driver):
    settings.stream_heartbeat_interval = 0.05
    return TestClient(create_app(settings, driver))


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/chat/completions", {"messages": [{"role": "user", "content": "beat"}]}),
        ("/v1/responses", {"input": "beat"}),
    ],
)
def test_streams_send_keepalive_then_content(settings, auth, path, body):
    with _client(settings, SlowFirstDriver(delay=0)) as client:
        with client.stream(
            "POST", path, headers=auth, json={"model": "muse-chat", "stream": True, **body}
        ) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())
    assert ": keepalive" in text
    assert text.index(": keepalive") < text.index("[mock:")


def test_heartbeat_mode_reports_errors_in_stream(settings, auth):
    with _client(settings, FailingDriver(delay=0)) as client:
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers=auth,
            json={
                "model": "muse-chat",
                "stream": True,
                "messages": [{"role": "user", "content": "x"}],
            },
        ) as r:
            assert r.status_code == 200
            text = "".join(r.iter_text())
    assert '"error"' in text and "[DONE]" in text


def test_heartbeat_off_by_default(client, auth):
    with client.stream(
        "POST",
        "/v1/chat/completions",
        headers=auth,
        json={"model": "muse-chat", "stream": True, "messages": [{"role": "user", "content": "x"}]},
    ) as r:
        assert ": keepalive" not in "".join(r.iter_text())
