from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from muse_console.accounts import Account
from muse_console.accounts.pool import AccountPool
from muse_console.errors import InvalidRequest, Unauthorized
from muse_console.services.account_manager import AccountManager
from muse_console.services.credential_devices import CredentialDevices
from muse_console.services.credential_ingestion import CredentialIngestion
from muse_console.services.credential_validator import VerifiedCredential
from test_account_management import cookies, store
from test_credential_ingestion import envelope, setup_service

SUBJECT = "muse-vm:322df828-738c-4523-9a49-7e766a9adfeb"
OTHER = "muse-vm:11111111-2222-3333-4444-555555555555"


def subject_validator(subject=SUBJECT):
    async def validate(env):
        return VerifiedCredential(env.cookies, subject)

    return validate


async def test_second_profile_of_same_muse_account_is_adopted(tmp_path):
    svc, grant = await setup_service(tmp_path, subject_validator())
    device = grant["device_id"]
    first = await svc.ingest(envelope(device))
    assert first["status"] == "committed"
    remaining = svc.devices.remaining(device)
    # Same device, another browser profile: fresh source with no cursor yet.
    adopted = await svc.ingest(envelope(device, capture="capture_0002", store="1"))
    assert adopted["status"] == "committed"
    assert adopted["account_id"] == first["account_id"]
    assert adopted["credential_version"] == 2
    assert [(a.id, a.credential_version, a.credential_subject) for a in svc.pool.all()] == [
        (first["account_id"], 2, SUBJECT)
    ]
    assert svc.devices.remaining(device) == remaining
    source = CredentialIngestion.source(envelope(device, capture="capture_0002", store="1"))
    binding = svc.devices.binding(source)
    assert binding["account"] == first["account_id"] and not binding["created"]
    await svc.close()


async def test_adopted_source_refreshes_after_adoption(tmp_path):
    svc, grant = await setup_service(tmp_path, subject_validator())
    device = grant["device_id"]
    first = await svc.ingest(envelope(device))
    await svc.ingest(envelope(device, capture="capture_0002", store="1"))
    refreshed = envelope(
        device, capture="capture_0003", store="1", revision=2, base=2, value="next"
    )
    result = await svc.ingest(refreshed)
    assert result["status"] == "committed"
    assert result["account_id"] == first["account_id"] and result["credential_version"] == 3
    assert len(svc.pool.all()) == 1
    await svc.close()


async def test_distinct_upstream_account_creates_its_own_entry(tmp_path):
    svc, grant = await setup_service(tmp_path, subject_validator())
    await svc.ingest(envelope(grant["device_id"]))
    with svc.devices.db:
        svc.devices.db.execute("UPDATE devices SET quota=2")
    svc.validator = subject_validator(OTHER)
    second = await svc.ingest(
        envelope(grant["device_id"], capture="capture_0002", store="1", value="other")
    )
    assert second["status"] == "committed"
    assert len(svc.pool.all()) == 2
    await svc.close()


async def test_conflicting_subject_on_bound_device_needs_confirmation(tmp_path):
    svc, grant = await setup_service(tmp_path, subject_validator())
    first = await svc.ingest(envelope(grant["device_id"]))
    svc.validator = subject_validator(OTHER)
    changed = envelope(grant["device_id"], capture="capture_0002", revision=2, base=1, value="x")
    assert (await svc.ingest(changed))["status"] == "needs_identity_confirmation"
    assert svc.pool.get(first["account_id"]).credential_version == 1
    assert len(svc.pool.all()) == 1
    await svc.close()


async def test_merge_keeps_newer_credentials_and_one_entry(tmp_path):
    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    duplicate = await manager.import_account(cookies("stale"), label="实号验收-2")
    keeper = await manager.import_account(cookies("fresh"))
    keeper = await pool.upsert(
        keeper.model_copy(update={"credential_version": 4, "success_count": 5, "fail_count": 2})
    )
    merged = await manager.merge(duplicate.id, keeper.id)
    assert merged.id == keeper.id
    assert [a.id for a in pool.all()] == [keeper.id]
    assert merged.cookies == cookies("fresh")
    assert merged.credential_version == 4
    assert merged.label == "实号验收-2"
    assert merged.success_count == 5 and merged.fail_count == 2
    assert merged.created_at == min(duplicate.created_at, keeper.created_at)
    assert "fresh" not in json.dumps(merged.public())
    # The merged record is what survives a restart.
    assert [a.id for a in await store(tmp_path).load()] == [keeper.id]


async def test_merge_accepts_identical_sessions_and_rebinds_devices(tmp_path):
    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    first = await pool.upsert(Account(cookies={"hatch_vml": "legacy-a"}))
    second = await pool.upsert(Account(cookies={"hatch_vml": "legacy-a"}))
    devices = CredentialDevices(tmp_path / "devices.db")
    await devices.startup()
    challenge = devices.challenge()
    grant = devices.pair(challenge["challenge"], challenge["server_id"])
    devices.bind(
        source="source-1",
        device=grant["device_id"],
        account=first.id,
        revision=1,
        fingerprint="fingerprint-1",
        created=True,
    )
    merged = await manager.merge(first.id, second.id)
    assert [a.id for a in pool.all()] == [second.id]
    assert merged.cookies == second.cookies
    assert devices.rebind(first.id, second.id) == 1
    assert devices.binding("source-1")["account"] == second.id
    assert devices.rebind("acc_unknown", second.id) == 0
    await devices.close()


async def test_merge_rejects_self_and_missing_accounts(tmp_path):
    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    account = await manager.import_account(cookies())
    with pytest.raises(ValueError):
        await manager.merge(account.id, account.id)
    with pytest.raises(ValueError):
        await manager.merge(account.id, "acc_missing")
    with pytest.raises(ValueError):
        await manager.merge("acc_missing", account.id)
    assert pool.get(account.id).cookies == cookies()


async def test_merge_route_requires_admin_and_reports_bindings(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from muse_console.api.routes.admin import router

    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    duplicate = await manager.import_account(cookies("stale"))
    keeper = await manager.import_account(cookies("fresh"))
    devices = CredentialDevices(tmp_path / "devices.db")
    await devices.startup()
    calls = []

    def rebind(source, target):
        calls.append((source, target))
        return 1

    fake_devices = SimpleNamespace(rebind=rebind)
    app = FastAPI()
    app.state.services = SimpleNamespace(
        pool=pool,
        account_manager=manager,
        credential_ingestion=SimpleNamespace(devices=fake_devices),
        driver=SimpleNamespace(),
        settings=SimpleNamespace(effective_admin_key="admin"),
    )
    app.include_router(router)
    headers = {"Authorization": "Bearer admin"}
    with TestClient(app) as client:
        with pytest.raises(Unauthorized):
            client.post(f"/admin/accounts/{duplicate.id}/merge", json={"into": keeper.id})
        response = client.post(
            f"/admin/accounts/{duplicate.id}/merge", headers=headers, json={"into": keeper.id}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["merged"] == duplicate.id and body["account"]["id"] == keeper.id
        assert body["bindings"] == 1
        assert calls == [(duplicate.id, keeper.id)]
        with pytest.raises(InvalidRequest):
            client.post(
                f"/admin/accounts/{keeper.id}/merge", headers=headers, json={"into": keeper.id}
            )
        listed = client.get("/admin/accounts", headers=headers).json()
        assert [a["id"] for a in listed["data"]] == [keeper.id]
    await devices.close()
