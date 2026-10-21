from __future__ import annotations

import asyncio
import base64
from urllib.parse import urlsplit

import pytest

from muse_console.core.media import MediaStore, load_image_ref
from muse_console.errors import CapacityExceeded, InvalidRequest
from muse_console.services.tasks import TaskManager


def _key(client, admin, name):
    response = client.post("/admin/keys", headers=admin, json={"name": name})
    assert response.status_code == 200
    body = response.json()
    return body, {"Authorization": f"Bearer {body['api_key']}"}


def test_task_and_media_are_owned(client, admin, auth):
    _, first = _key(client, admin, "first")
    _, second = _key(client, admin, "second")
    response = client.post(
        "/v1/images/generations", headers=first, json={"prompt": "private", "async": True}
    )
    assert response.status_code == 200
    task_id = response.json()["id"]
    svc = client.app.state.services
    client.portal.call(asyncio.sleep, 0.05)
    path = f"/v1/images/generations/{task_id}"
    assert client.get(path, headers=second).status_code == 404
    assert client.get(path, headers=auth).status_code == 404
    assert client.get(path, headers=admin).status_code == 200
    task = client.get(path, headers=first).json()
    assert task["status"] == "succeeded"
    signed = task["result"]["data"][0]["url"]
    bare = urlsplit(signed).path
    assert client.get(bare).status_code == 401
    assert client.get(bare, headers=second).status_code == 404
    assert client.get(bare, headers=first).status_code == 200
    public = client.get(signed)
    assert public.status_code == 200
    assert public.headers["cache-control"] == "private, no-store"
    assert client.get(signed.replace("signature=", "signature=00")).status_code == 401
    assert svc.tasks.get(task_id).owner_key_id is not None


def test_revoked_key_invalidates_signed_media(client, admin):
    body, headers = _key(client, admin, "revocable")
    response = client.post("/v1/images/generations", headers=headers, json={"prompt": "private"})
    url = response.json()["data"][0]["url"]
    assert client.get(url).status_code == 200
    key_id = body["key"]["id"]
    assert (
        client.patch(f"/admin/keys/{key_id}", headers=admin, json={"revoked": True}).status_code
        == 200
    )
    assert client.get(url).status_code == 401


def test_ownerless_legacy_media_is_not_public(client, auth, admin):
    svc = client.app.state.services
    name = svc.media.save(b"old image", "image/png", prefix="img")
    path = f"/v1/media/{name}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=auth).status_code == 404
    assert client.get(path, headers=admin).status_code == 200


def test_media_link_expiry_and_restart(tmp_path, monkeypatch):
    store = MediaStore(tmp_path / "media", url_ttl=60)
    name = store.save(b"test", "image/png", prefix="img", owner_key_id="legacy")
    signature = store._signature(name, "legacy", 150)
    monkeypatch.setattr("muse_console.core.media.time.time", lambda: 100)
    assert store.valid_signature(name, 150, signature)
    again = MediaStore(tmp_path / "media", url_ttl=60)
    assert again.valid_signature(name, 150, signature)
    monkeypatch.setattr("muse_console.core.media.time.time", lambda: 151)
    assert not again.valid_signature(name, 150, signature)


def test_media_storage_limits_and_missing_key(tmp_path):
    store = MediaStore(tmp_path / "media", max_bytes=4, storage_max_bytes=5)
    with pytest.raises(CapacityExceeded):
        store.save(b"12345", "image/png")
    store.save(b"1234", "image/png")
    with pytest.raises(CapacityExceeded):
        store.save(b"12", "image/png")
    (tmp_path / "media-signing.key").unlink()
    with pytest.raises(ValueError, match="missing"):
        MediaStore(tmp_path / "media")


@pytest.mark.asyncio
async def test_task_queue_capacity_and_legacy_ownership(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", max_pending=1)
    release = asyncio.Event()

    async def runner(progress):
        await release.wait()
        return {}

    task = manager.submit("video", {}, runner, owner_key_id="first")
    with pytest.raises(CapacityExceeded):
        manager.submit("video", {}, runner)
    assert manager.get_owned(task.id, "second") is None
    assert manager.get_owned(task.id, "first") is task
    assert manager.get_owned(task.id, "admin") is task
    release.set()
    await asyncio.sleep(0.01)
    assert manager.stats()["pending"] == 0
    loaded = TaskManager(tmp_path / "tasks.json")
    await loaded.load()
    assert loaded.get_owned(task.id, "first").owner_key_id == "first"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_input_base64_size_and_mime():
    encoded = base64.b64encode(b"12345").decode()
    with pytest.raises(InvalidRequest):
        await load_image_ref(encoded, max_bytes=4)
    with pytest.raises(InvalidRequest):
        await load_image_ref("data:image/svg+xml;base64," + encoded)
    with pytest.raises(InvalidRequest):
        await load_image_ref("data:image/png;base64,%%%%")
    assert await load_image_ref(encoded, max_bytes=5) == (b"12345", "image/png")
