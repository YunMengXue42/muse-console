"""Exercise the administration boundary and compatible inference routes offline."""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from muse_console.app import create_app
from muse_console.drivers.mock import MockDriver


def credentials(name: str) -> dict[str, str]:
    """Produce distinct offline sessions without referring to a real account."""
    return {
        key: f"offline-{name}-{key}"
        for key in ("hatch_sess", "hatch_gw", "hatch_vml", "hatch_native_auth_device")
    }


def test_management_auth_and_mock_login(client, admin):
    for path in ("/admin/pool", "/admin/accounts"):
        assert client.get(path).status_code == 401
    assert client.post("/admin/accounts/import", json={"content": "[]"}).status_code == 401
    assert client.post("/admin/login-sessions", json={"label": "local"}).status_code == 401
    result = client.post("/admin/login-sessions", headers=admin, json={"label": "local"})
    assert result.status_code == 501


def test_preview_import_rotation_and_encrypted_restart(settings, admin, auth):
    app = create_app(settings, MockDriver(delay=0))
    payload = json.dumps(
        [{"label": name, "cookies": credentials(name)} for name in ("one", "two", "three")]
    )
    with TestClient(app) as client:
        preview = client.post(
            "/admin/accounts/import", headers=admin, json={"content": payload, "preview": True}
        )
        assert preview.status_code == 200
        assert preview.json()["summary"]["ready"] == 3
        assert client.get("/admin/accounts", headers=admin).json()["data"] == []
        result = client.post("/admin/accounts/import", headers=admin, json={"content": payload})
        assert result.json()["summary"]["imported"] == 3
        ids = sorted(a["id"] for a in client.get("/admin/accounts", headers=admin).json()["data"])
        assert (
            client.patch("/admin/pool", headers=admin, json={"strategy": "round_robin"}).status_code
            == 200
        )
        for _ in range(6):
            response = client.post(
                "/v1/chat/completions",
                headers=auth,
                json={"messages": [{"role": "user", "content": "offline"}]},
            )
            assert response.status_code == 200
        rows = client.get("/admin/accounts", headers=admin).json()["data"]
        assert sorted(a["success_count"] for a in rows) == [2, 2, 2]
        assert "offline-one" not in json.dumps(rows)
        assert all(a["inflight"] == 0 for a in rows)
        duplicate = client.post("/admin/accounts/import", headers=admin, json={"content": payload})
        assert duplicate.json()["summary"]["duplicate"] == 3
        raw = (settings.data_dir / "accounts.db").read_bytes()
        assert b"offline-one-hatch_sess" not in raw
    with TestClient(create_app(settings, MockDriver(delay=0))) as restarted:
        rows = restarted.get("/admin/accounts", headers=admin).json()["data"]
        assert sorted(a["id"] for a in rows) == ids
        assert sorted(a["success_count"] for a in rows) == [2, 2, 2]


def test_account_patch_is_transactional(client, admin):
    ids = []
    for label in ("first", "second"):
        result = client.post(
            "/admin/accounts", headers=admin, json={"label": label, "cookies": credentials(label)}
        )
        assert result.status_code == 200
        ids.append(result.json()["account"]["id"])
    rejected = client.patch(
        f"/admin/accounts/{ids[0]}",
        headers=admin,
        json={"label": "should-not-commit", "cookies": credentials("second")},
    )
    assert rejected.status_code == 400
    rows = client.get("/admin/accounts", headers=admin).json()["data"]
    assert next(row["label"] for row in rows if row["id"] == ids[0]) == "first"
    patched = client.patch(f"/admin/accounts/{ids[0]}", headers=admin, json={"max_concurrency": 3})
    assert patched.status_code == 200
    rows = client.get("/admin/accounts", headers=admin).json()["data"]
    assert next(r["effective_max_concurrency"] for r in rows if r["id"] == ids[0]) == 3
    assert (
        client.patch(
            f"/admin/accounts/{ids[0]}", headers=admin, json={"max_concurrency": None}
        ).status_code
        == 200
    )


def test_console_shell_and_assets(client):
    for route in ("/dashboard", "/admin"):
        response = client.get(route)
        assert response.status_code == 200
        assert "Muse Console" in response.text
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
    html = client.get("/dashboard").text
    sources = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
    assert sources
    for asset in sources:
        result = client.get(asset)
        assert result.status_code == 200, asset
        assert result.content
    font = client.get("/assets/fonts/muse-console-sans.woff2")
    assert font.status_code == 200
    assert font.content.startswith(b"wOF2")
    assert "SIL OPEN FONT LICENSE" in client.get("/assets/fonts/OFL.txt").text
    assert client.get("/assets/../../.env").status_code == 404
    assert client.get("/credentials.key").status_code == 404


def test_legacy_create_rejects_duplicate_session(client, admin):
    body = {"label": "first", "cookies": credentials("same")}
    assert client.post("/admin/accounts", headers=admin, json=body).status_code == 200
    body["label"] = "second"
    assert client.post("/admin/accounts", headers=admin, json=body).status_code == 400
    assert len(client.get("/admin/accounts", headers=admin).json()["data"]) == 1


def test_startup_failure_closes_owned_resources(settings):
    class FailingDriver(MockDriver):
        stopped = False

        async def startup(self):
            raise RuntimeError("offline startup failure")

        async def shutdown(self):
            self.stopped = True

    import pytest

    driver = FailingDriver(delay=0)
    with pytest.raises(RuntimeError, match="offline startup failure"):
        with TestClient(create_app(settings, driver)):
            pass
    assert driver.stopped
