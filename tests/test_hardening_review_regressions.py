from __future__ import annotations

import asyncio
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from muse_console.core.media import MediaStore
from muse_console.errors import CapacityExceeded
from muse_console.services.tasks import TaskManager, TaskStatus


def _create_key(client, admin, name):
    response = client.post("/admin/keys", headers=admin, json={"name": name})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['api_key']}"}


def test_foreign_media_prompt_cannot_mint_signature(client, admin):
    victim = _create_key(client, admin, "review-victim")
    attacker = _create_key(client, admin, "review-attacker")
    response = client.post(
        "/v1/images/generations", headers=victim, json={"prompt": "private image"}
    )
    assert response.status_code == 200
    foreign_path = urlsplit(response.json()["data"][0]["url"]).path
    assert client.get(foreign_path, headers=attacker).status_code == 404

    response = client.post(
        "/v1/images/generations",
        headers=attacker,
        json={"prompt": foreign_path, "async": True},
    )
    assert response.status_code == 200
    task_id = response.json()["id"]
    manager = client.app.state.services.tasks

    async def wait_for_worker():
        worker = manager._running.get(task_id)
        if worker is not None:
            await asyncio.wait_for(asyncio.shield(worker), 2)

    client.portal.call(wait_for_worker)
    response = client.get(f"/v1/images/generations/{task_id}", headers=attacker)
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    item = response.json()["result"]["data"][0]
    assert item["revised_prompt"] == foreign_path
    assert "signature=" not in item["revised_prompt"]
    assert client.get(item["revised_prompt"]).status_code == 401
    assert client.get(foreign_path, headers=attacker).status_code == 404
    # The fix must not disable renewal of the attacker's own structured result URL.
    assert "signature=" in item["url"]
    assert client.get(item["url"]).status_code == 200


@pytest.mark.parametrize("caller", ["attacker", "admin"])
def test_refresh_only_authorized_structured_urls(tmp_path, monkeypatch, caller):
    store = MediaStore(tmp_path / "media")
    own = store.save(b"own", "image/png", prefix="img", owner_key_id="attacker")
    foreign = store.save(b"foreign", "image/png", prefix="img", owner_key_id="victim")
    ownerless = store.save(b"legacy", "image/png", prefix="img")
    own_path = f"/v1/media/{own}"
    foreign_path = f"/v1/media/{foreign}"
    ownerless_path = f"/v1/media/{ownerless}"
    value = {
        "data": [
            {"url": own_path, "revised_prompt": foreign_path},
            {"url": foreign_path},
            {"nested": {"url": ownerless_path}},
        ],
        "revised_prompt": own_path,
        "text": foreign_path,
        "strings": [own_path, foreign_path, ownerless_path],
    }
    original = deepcopy(value)
    signed_names = []
    signed_url = store.signed_url

    def record_signature(name, base):
        signed_names.append(name)
        return signed_url(name, base)

    monkeypatch.setattr(store, "signed_url", record_signature)
    result = store.refresh_links(value, "https://offline.invalid", caller)
    assert value == original
    assert result["revised_prompt"] == own_path
    assert result["text"] == foreign_path
    assert result["strings"] == value["strings"]
    assert result["data"][0]["revised_prompt"] == foreign_path
    assert result["data"][2]["nested"]["url"] == ownerless_path
    assert ownerless not in signed_names
    assert signed_names == ([own, foreign] if caller == "admin" else [own])
    if caller != "admin":
        assert result["data"][1]["url"] == foreign_path

    for index in [0, 1] if caller == "admin" else [0]:
        parsed = urlsplit(result["data"][index]["url"])
        query = parse_qs(parsed.query)
        assert parsed.netloc == "offline.invalid"
        assert store.valid_signature(
            parsed.path.rsplit("/", 1)[1], int(query["expires"][0]), query["signature"][0]
        )
    assert store.refresh_links(foreign_path, "https://offline.invalid", caller) == foreign_path


@pytest.mark.asyncio
async def test_cancellation_keeps_capacity_until_cleanup_and_hook_finish(tmp_path):
    entered, cleaning, release_cleanup, hooked, release_hook = (asyncio.Event() for _ in range(5))

    async def runner(progress):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release_cleanup.wait()

    async def hook(task):
        hooked.set()
        await release_hook.wait()

    async def replacement(progress):
        return {"ok": True}

    manager = TaskManager(tmp_path / "tasks.json", max_pending=1, max_concurrent=1, on_finish=hook)
    task = manager.submit("test", {}, runner)
    cancelling = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        cancelling = asyncio.create_task(manager.cancel(task.id))
        await asyncio.wait_for(cleaning.wait(), 2)
        assert task.status == TaskStatus.CANCELLED
        assert not cancelling.done()
        assert not manager._running[task.id].done()
        assert manager.stats()["pending"] == 1
        with pytest.raises(CapacityExceeded):
            manager.submit("test", {}, replacement)

        release_cleanup.set()
        await asyncio.wait_for(hooked.wait(), 2)
        assert not cancelling.done()
        assert manager.stats()["pending"] == 1
        with pytest.raises(CapacityExceeded):
            manager.submit("test", {}, replacement)

        release_hook.set()
        assert await asyncio.wait_for(asyncio.shield(cancelling), 2)
        assert manager.stats()["pending"] == 0
        assert not manager._running
        assert not manager._started
        next_task = manager.submit("test", {}, replacement)
        await asyncio.wait_for(asyncio.shield(manager._running[next_task.id]), 2)
        assert next_task.status == TaskStatus.SUCCEEDED
        assert manager.stats()["pending"] == 0
    finally:
        release_cleanup.set()
        release_hook.set()
        if cancelling is not None:
            await asyncio.wait_for(asyncio.shield(cancelling), 2)
        await asyncio.wait_for(manager.shutdown(), 2)
