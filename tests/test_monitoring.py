from __future__ import annotations

import json

from muse2api.auth.keys import hash_key


def _bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _requests(client, admin, **params) -> list[dict]:
    r = client.get("/admin/requests", headers=admin, params=params)
    assert r.status_code == 200
    return r.json()["data"]


def test_key_lifecycle(client, admin, settings):
    r = client.post("/admin/keys", headers=admin, json={"name": "alice", "note": "laptop"})
    assert r.status_code == 200
    body = r.json()
    plaintext, key = body["api_key"], body["key"]
    assert plaintext.startswith("m2a-")
    assert key["prefix"] == plaintext[:8]
    assert "hash" not in key

    # Only the hash is persisted.
    stored = settings.keys_file.read_text(encoding="utf-8")
    assert plaintext not in stored
    assert json.loads(stored)[0]["hash"] == hash_key(plaintext)

    assert client.get("/v1/models", headers=_bearer(plaintext)).status_code == 200
    listed = client.get("/admin/keys", headers=admin).json()["data"]
    assert [k["name"] for k in listed] == ["alice"]
    assert "hash" not in listed[0]
    assert listed[0]["last_used_at"] > 0

    r = client.patch(f"/admin/keys/{key['id']}", headers=admin, json={"revoked": True})
    assert r.json()["key"]["revoked"] is True
    assert client.get("/v1/models", headers=_bearer(plaintext)).status_code == 401

    r = client.patch(f"/admin/keys/{key['id']}", headers=admin,
                     json={"revoked": False, "name": "alice2"})
    assert r.json()["key"]["name"] == "alice2"
    assert client.get("/v1/models", headers=_bearer(plaintext)).status_code == 200

    assert client.delete(f"/admin/keys/{key['id']}", headers=admin).status_code == 200
    assert client.get("/v1/models", headers=_bearer(plaintext)).status_code == 401
    assert client.delete(f"/admin/keys/{key['id']}", headers=admin).status_code == 404


def test_keys_require_admin(client, auth):
    assert client.get("/admin/keys", headers=auth).status_code == 401
    assert client.post("/admin/keys", headers=auth, json={"name": "x"}).status_code == 401


def test_legacy_and_admin_keys_still_work(client, auth, admin):
    assert client.get("/v1/models", headers=auth).status_code == 200
    assert client.get("/v1/models", headers=admin).status_code == 200
    assert client.get("/v1/models", headers=_bearer("m2a-nope")).status_code == 401
    names = [r["key_name"] for r in _requests(client, admin)]
    assert names == [None, "admin", "legacy"]


def test_request_logged(client, admin):
    key = client.post("/admin/keys", headers=admin, json={"name": "bob"}).json()
    headers = {**_bearer(key["api_key"]), "cf-connecting-ip": "203.0.113.7"}
    r = client.post("/v1/chat/completions", headers=headers, json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200

    row = _requests(client, admin)[0]
    assert row["method"] == "POST"
    assert row["path"] == "/v1/chat/completions"
    assert row["model"] == "gpt-4o"
    assert row["key_id"] == key["key"]["id"]
    assert row["key_name"] == "bob"
    assert row["account_id"] == "anonymous"  # mock driver without accounts
    assert row["status_code"] == 200
    assert row["latency_ms"] >= 0
    assert row["client_ip"] == "203.0.113.7"
    assert row["stream"] is False
    assert row["error"] is None


def test_stream_and_error_logged(client, auth, admin):
    with client.stream("POST", "/v1/chat/completions", headers=auth, json={
        "messages": [{"role": "user", "content": "s"}], "stream": True}) as r:
        assert r.status_code == 200
        list(r.iter_lines())
    r = client.post("/v1/chat/completions", headers=auth, json={
        "model": "muse-video", "messages": [{"role": "user", "content": "x"}]})
    assert r.status_code == 400

    bad, streamed = _requests(client, admin)[:2]
    assert streamed["stream"] is True and streamed["status_code"] == 200
    assert bad["status_code"] == 400
    assert bad["model"] == "muse-video"
    assert bad["error"]

    assert [x["id"] for x in _requests(client, admin, status="4xx")] == [bad["id"]]
    assert [x["id"] for x in _requests(client, admin, status="2xx")] == [streamed["id"]]


def test_polls_flagged_and_media_skipped(client, auth, admin):
    task = client.post("/v1/videos", headers=auth, json={"prompt": "a wave"}).json()
    assert client.get(f"/v1/videos/{task['id']}", headers=auth).status_code == 200
    client.get("/v1/media/does-not-exist.png")

    rows = _requests(client, admin)
    assert [r["path"] for r in rows] == [f"/v1/videos/{task['id']}", "/v1/videos"]
    poll, submit = rows
    assert poll["poll"] is True and poll["task_id"] == task["id"]
    assert submit["poll"] is False and submit["task_id"] == task["id"]
    assert submit["model"] == "muse-video"
    assert [r["path"] for r in _requests(client, admin, hide_polls="true")] == ["/v1/videos"]


def test_stats_shape(client, auth, admin):
    client.get("/v1/models", headers=auth)
    client.get("/v1/models")  # 401
    r = client.get("/admin/stats", headers=admin, params={"window": "1h"})
    assert r.status_code == 200
    s = r.json()
    assert s["window"] == "1h"
    assert s["total"] == 2 and s["errors"] == 1
    assert s["error_rate"] == 0.5
    assert set(s["latency_ms"]) == {"p50", "p95"}
    for group in ("by_key", "by_account", "by_model", "by_status"):
        assert isinstance(s[group], list)
    assert {g["status_code"] for g in s["by_status"]} == {200, 401}
    assert len(s["series"]) >= 59
    assert sum(p["count"] for p in s["series"]) == 2
    assert client.get("/admin/stats", headers=admin, params={"window": "2y"}).status_code == 400


async def test_prune(settings):
    from muse2api.services.request_log import RequestLog

    log = RequestLog(settings.requests_db)
    base = {"method": "GET", "path": "/v1/models", "status_code": 200, "latency_ms": 1}
    await log.add({**base, "ts": 1.0})
    await log.add({**base, "ts": 9e12})
    assert await log.prune(14) == 1
    assert (await log.query())["total"] == 1
    await log.close()


def test_dashboard_html(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<title>muse2api dashboard</title>" in r.text
