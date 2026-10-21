"""exercise the shipped application boundary with fake upstreams, never real sessions."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from muse_console.accounts.credentials import CookieRecord
from muse_console.app import create_app
from muse_console.config import Settings
from muse_console.drivers.base import SessionInfo
from muse_console.drivers.mock import MockDriver
from muse_console.services.credential_validator import VerifiedCredential
from muse_console.upstream.muse import SESSION_COOKIES

ADMIN = {"Authorization": "Bearer workflow-admin"}
ORIGIN = "http://127.0.0.1:18612"


def make_app(tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "workflow",
        api_key="workflow-api",
        admin_key="workflow-admin",
        credential_ingestion_enabled=True,
        credential_server_origin=ORIGIN,
        credential_allow_loopback_http=True,
    )
    app = create_app(settings, MockDriver(delay=0))

    async def validate(env):
        return VerifiedCredential(env.cookies, "stable-fake-subject")

    app.state.services.credential_ingestion.validator = validate
    return app, settings


def pair(client):
    challenge = client.post("/admin/credential-devices/pairings", headers=ADMIN, json={})
    assert challenge.status_code == 200
    data = challenge.json()
    grant = client.post(
        "/credential-ingestions/pair", json={k: data[k] for k in ("server_id", "challenge")}
    )
    assert grant.status_code == 200
    return grant.json()


def payload(device, capture="workflow_capture_01", base=0, revision=1, value="synthetic-only"):
    return {
        "schema_version": 1,
        "source": "extension",
        "device_id": device,
        "capture_id": capture,
        "source_revision": revision,
        "captured_at": time.time(),
        "store_id": "0",
        "partition_key": None,
        "base_credential_version": base,
        "cookies": [
            CookieRecord(name=n, value=value + n, domain=".muse.ai").model_dump(mode="json")
            for n in SESSION_COOKIES
        ],
    }


def test_application_pair_import_renew_replay_restart_revoke(tmp_path):
    app, settings = make_app(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        grant = pair(client)
        auth = {"Authorization": "Bearer " + grant["token"]}
        body = payload(grant["device_id"])
        first = client.post("/credential-ingestions", headers=auth, json=body)
        assert first.status_code == 200 and first.json()["status"] == "committed"
        for _ in range(10):
            assert (
                client.post("/credential-ingestions", headers=auth, json=body).json()
                == first.json()
            )
        account_id = first.json()["account_id"]
        accounts = client.get("/admin/accounts", headers=ADMIN).json()["data"]
        assert len(accounts) == 1 and accounts[0]["credential_version"] == 1
        assert "synthetic-only" not in str(accounts)
        assert client.get("/admin/accounts", headers=auth).status_code == 401

        async def renew(account):
            return SessionInfo(
                ok=True,
                credential_records=[
                    r.model_copy(update={"value": "renewed-only" + r.name})
                    for r in account.credential_records
                ],
            )

        app.state.services.driver.renew_session = renew
        renewed = client.post(f"/admin/accounts/{account_id}/renew", headers=ADMIN)
        assert renewed.json()["credential_version"] == 2
        stale = payload(
            grant["device_id"],
            capture="workflow_capture_stale",
            base=1,
            revision=2,
            value="stale-only",
        )
        assert (
            client.post("/credential-ingestions", headers=auth, json=stale).json()["status"]
            == "conflict"
        )

    restarted = create_app(settings, MockDriver(delay=0))
    with TestClient(restarted, base_url=ORIGIN) as client:
        receipt = client.get("/credential-ingestions/" + body["capture_id"], headers=auth)
        assert receipt.json() == first.json()
        current = client.get("/admin/accounts", headers=ADMIN).json()["data"][0]
        assert current["credential_version"] == 2
        assert (
            client.delete(
                "/admin/credential-devices/" + grant["device_id"], headers=ADMIN
            ).status_code
            == 200
        )
        assert (
            client.get("/credential-ingestions/" + body["capture_id"], headers=auth).status_code
            == 401
        )
    assert b"synthetic-only" not in (settings.data_dir / "accounts.db").read_bytes()
    assert b"renewed-only" not in (settings.data_dir / "accounts.db").read_bytes()


def test_ingestion_disabled_by_default_and_no_cors(tmp_path):
    app = create_app(
        Settings(_env_file=None, data_dir=tmp_path / "disabled", api_key="key"), MockDriver()
    )
    with TestClient(app) as client:
        response = client.get("/credential-ingestions/server")
        assert response.status_code == 404
        assert "access-control-allow-origin" not in response.headers
