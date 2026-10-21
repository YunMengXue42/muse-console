"""Multipart video uploads, as sent by infinite-canvas and OpenAI-style SDKs."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import pytest

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001"
)


def _wait(client, auth, task_id, limit=50):
    task = {}
    for _ in range(limit):
        task = client.get(f"/v1/videos/{task_id}", headers=auth).json()
        if task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.02)
    return task


def test_multipart_video_round_trip(client, auth):
    r = client.post(
        "/v1/videos",
        headers=auth,
        data={
            "model": "",
            "prompt": "冰雪熊掌",
            "seconds": "10",
            "size": "1280x720",
            "mode": "reference",
            "resolution_name": "720p",
            "generate_audio": "true",
            "watermark": "false",
        },
        files={"image[]": ("ref.png", PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["object"] == "video"
    assert task["status"] in ("queued", "running")

    done = _wait(client, auth, task["id"])
    assert done["status"] == "succeeded", done
    assert done["video_url"] == done["result"]["url"]
    assert urlsplit(done["video_url"]).path.endswith(".mp4")

    content = client.get(f"/v1/videos/{task['id']}/content", headers=auth)
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("video/mp4")
    assert content.content == b"\x00\x00\x00\x18ftypmp42mock"


def test_multipart_first_frame_is_forwarded(client, auth, monkeypatch):
    seen = {}

    async def capture(req):
        from muse_console.drivers.base import MediaResult

        seen["first_frame"] = req.first_frame
        seen["duration"] = req.duration
        seen["size"] = req.size
        return MediaResult(data=b"\x00\x00\x00\x18ftypmp42mock", mime="video/mp4", kind="video")

    monkeypatch.setattr(client.app.state.services.gateway, "generate_video", capture)
    r = client.post(
        "/v1/videos",
        headers=auth,
        data={"prompt": "p", "seconds": "6", "size": "720x1280", "mode": "frames"},
        files={"first_frame": ("first.png", PNG, "image/png")},
    )
    assert r.status_code == 200, r.text
    _wait(client, auth, r.json()["id"])
    assert seen["duration"] == 6
    assert seen["size"] == "720x1280"
    assert seen["first_frame"] is not None


@pytest.mark.parametrize("part", ["last_frame", "video[]", "audio[]"])
def test_multipart_unsupported_references_rejected(client, auth, part):
    r = client.post(
        "/v1/videos",
        headers=auth,
        data={"prompt": "p"},
        files={part: ("x.bin", b"\x00\x01", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "not supported" in r.json()["error"]["message"]


def test_multipart_non_image_frame_rejected(client, auth):
    r = client.post(
        "/v1/videos",
        headers=auth,
        data={"prompt": "p"},
        files={"first_frame": ("x.txt", b"plain", "text/plain")},
    )
    assert r.status_code == 400
    assert "image" in r.json()["error"]["message"]


def test_multipart_missing_prompt_rejected(client, auth):
    r = client.post("/v1/videos", headers=auth, data={"model": "x"})
    assert r.status_code == 400
    assert "prompt" in r.json()["error"]["message"]


def test_multipart_empty_frame_rejected(client, auth):
    r = client.post(
        "/v1/videos",
        headers=auth,
        data={"prompt": "p"},
        files={"first_frame": ("empty.png", b"", "image/png")},
    )
    assert r.status_code == 400
    assert "empty" in r.json()["error"]["message"]


def test_multipart_oversized_frame_rejected(client, auth):
    client.app.state.services.settings.input_image_max_bytes = 1024
    r = client.post(
        "/v1/videos",
        headers=auth,
        data={"prompt": "p"},
        files={"first_frame": ("big.png", PNG + b"\x00" * 4096, "image/png")},
    )
    assert r.status_code == 400
    assert "size limit" in r.json()["error"]["message"]


@pytest.mark.parametrize("body", ["notjson", "[]", '"x"'])
def test_non_object_json_body_rejected(client, auth, body):
    r = client.post(
        "/v1/videos",
        headers=auth | {"Content-Type": "application/json"},
        content=body,
    )
    assert r.status_code == 400
    assert "dictionary" in r.json()["error"]["message"]


def test_video_content_unknown_task(client, auth):
    assert client.get("/v1/videos/task_none/content", headers=auth).status_code == 404


def test_video_content_before_completion(client, auth, monkeypatch):
    import asyncio

    from muse_console.drivers.base import MediaResult

    async def slow(req):
        await asyncio.sleep(1)
        return MediaResult(data=b"late", mime="video/mp4", kind="video")

    monkeypatch.setattr(client.app.state.services.gateway, "generate_video", slow)
    r = client.post("/v1/videos", headers=auth, json={"prompt": "p"})
    assert client.get(f"/v1/videos/{r.json()['id']}/content", headers=auth).status_code == 404


def test_multipart_requires_api_key(client):
    r = client.post("/v1/videos", data={"prompt": "p"})
    assert r.status_code == 401
