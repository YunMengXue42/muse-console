import asyncio
import importlib.util
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location(
    "public_proxy", Path(__file__).resolve().parents[1] / "tools/public_inference_proxy.py"
)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


def test_boundary_rejects_admin_unknown_keys_and_streaming():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    app = proxy.create_app("friend-secret", time.time() + 600, httpx.MockTransport(upstream))
    auth = {"Authorization": "Bearer friend-secret"}
    with TestClient(app) as client:
        for path in (
            "/dashboard",
            "/admin/status",
            "/credential-ingestions",
            "/docs",
            "/openapi.json",
        ):
            assert client.get(path, headers=auth).status_code == 404
        assert client.get("/v1/models").status_code == 401
        assert (
            client.get("/v1/models", headers={"Authorization": "Bearer admin"}).status_code == 401
        )
        assert client.get("/v1/models", headers=auth).status_code == 200
        assert len(calls) == 1
        assert calls[0].headers["authorization"] == "Bearer friend-secret"
        assert (
            client.post("/v1/chat/completions", json={"stream": True}, headers=auth).status_code
            == 400
        )
        assert client.get("/v1/videos/task_private", headers=auth).status_code == 404
        assert client.get("/v1/media/img_0123456789abcdef.png", headers=auth).status_code == 404
        assert len(calls) == 1


async def test_proxy_accepts_more_than_two_simultaneous_requests():
    active = peak = 0

    async def upstream(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return httpx.Response(200, json={"data": []})

    app = proxy.create_app("friend-secret", time.time() + 600, httpx.MockTransport(upstream))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *(
                    client.get("/v1/models", headers={"Authorization": "Bearer friend-secret"})
                    for _ in range(5)
                )
            )
    assert all(response.status_code == 200 for response in responses)
    assert peak == 5


def test_expired_temporary_access_is_closed():
    with TestClient(proxy.create_app("friend-secret", time.time() - 1)) as client:
        assert (
            client.get("/v1/models", headers={"Authorization": "Bearer friend-secret"}).status_code
            == 403
        )


def test_only_new_task_media_is_shared(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "ROOT", tmp_path)
    (tmp_path / "public-url.txt").write_text("https://example.trycloudflare.com")

    def upstream(request):
        if request.url.path == "/v1/videos":
            return httpx.Response(200, json={"id": "task_new", "status": "queued"})
        if request.url.path == "/v1/videos/task_new":
            return httpx.Response(
                200,
                json={
                    "result": {"url": "http://127.0.0.1:18778/v1/media/vid_0123456789abcdef.mp4"}
                },
            )
        return httpx.Response(200, content=b"video", headers={"content-type": "video/mp4"})

    with TestClient(
        proxy.create_app("friend-secret", time.time() + 600, httpx.MockTransport(upstream))
    ) as client:
        auth = {"Authorization": "Bearer friend-secret"}
        assert client.post("/v1/videos", headers=auth, json={"prompt": "test"}).status_code == 200
        result = client.get("/v1/videos/task_new", headers=auth).json()
        url = result["result"]["url"]
        assert url.startswith("https://example.trycloudflare.com/")
        assert "sig=" in url
        assert client.get(url).content == b"video"
        assert client.get(url.replace("sig=", "sig=bad")).status_code == 401
