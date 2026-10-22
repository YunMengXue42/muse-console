from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from muse_console.accounts.credentials import CookieRecord, CredentialEnvelope
from muse_console.accounts.pool import AccountPool
from muse_console.services.credential_devices import CredentialDevices
from muse_console.services.credential_ingestion import CredentialIngestion
from muse_console.services.credential_validator import VerifiedCredential
from muse_console.upstream.muse import SESSION_COOKIES


class MemoryStore:
    def __init__(self):
        self.accounts = []

    async def save(self, accounts):
        self.accounts = accounts

    async def load(self):
        return self.accounts


def envelope(
    device, *, capture="capture_0001", revision=1, value="secret_cookie", base=0, store="0"
):
    return CredentialEnvelope(
        device_id=device,
        capture_id=capture,
        source_revision=revision,
        captured_at=time.time(),
        store_id=store,
        base_credential_version=base,
        cookies=[CookieRecord(name=n, value=value + n, domain=".muse.ai") for n in SESSION_COOKIES],
    )


async def setup_service(tmp_path, validator=None):
    devices = CredentialDevices(tmp_path / "devices.db")
    pool = AccountPool(MemoryStore())
    settings = SimpleNamespace(
        credential_ingestion_enabled=True,
        credential_server_origin="https://ingest.example",
        credential_allow_loopback_http=False,
        effective_admin_key="admin-secret",
    )

    async def validate(env):
        return VerifiedCredential(env.cookies)

    ingestion = CredentialIngestion(settings, pool, devices, validator or validate)
    await ingestion.startup()
    challenge = devices.challenge()
    grant = devices.pair(challenge["challenge"], challenge["server_id"])
    return ingestion, grant


async def test_idempotency_restart_and_private_receipts(tmp_path):
    svc, grant = await setup_service(tmp_path)
    env = envelope(grant["device_id"])
    result = await svc.ingest(env)
    assert result["status"] == "committed"
    for _ in range(8):
        assert await svc.ingest(env) == result
    assert len(svc.pool.all()) == 1
    assert svc.pool.all()[0].credential_records == env.cookies
    await svc.close()
    devices = CredentialDevices(tmp_path / "devices.db")
    restarted = CredentialIngestion(svc.settings, svc.pool, devices, svc.validator)
    await restarted.startup()
    assert devices.authenticate(grant["token"]) == grant["device_id"]
    assert await restarted.ingest(env) == result
    assert b"secret_cookie" not in (tmp_path / "devices.db").read_bytes()
    assert grant["token"].encode() not in (tmp_path / "devices.db").read_bytes()
    await restarted.close()


async def test_encrypted_account_recovery_after_fresh_pool_load(tmp_path):
    from muse_console.accounts.store import SQLiteAccountStore

    svc, grant = await setup_service(tmp_path)
    svc.pool = AccountPool(
        SQLiteAccountStore(tmp_path / "accounts.db", key_path=tmp_path / "credentials.key")
    )
    await svc.pool.load()
    env = envelope(grant["device_id"])
    result = await svc.ingest(env)
    with svc.devices.db:
        svc.devices.db.execute("DELETE FROM captures")
        svc.devices.db.execute("DELETE FROM bindings")
    await svc.close()
    pool = AccountPool(
        SQLiteAccountStore(tmp_path / "accounts.db", key_path=tmp_path / "credentials.key")
    )
    await pool.load()
    restarted = CredentialIngestion(
        svc.settings, pool, CredentialDevices(tmp_path / "devices.db"), svc.validator
    )
    await restarted.startup()
    assert await restarted.ingest(env) == result
    assert pool.all()[0].credential_records == env.cookies
    assert "credential_ingestion" not in str(pool.all()[0].public())
    assert b"secret_cookie" not in (tmp_path / "accounts.db").read_bytes()
    await restarted.close()


async def test_confirmation_is_capture_specific_and_base_version_required(tmp_path):
    svc, grant = await setup_service(tmp_path)
    first = await svc.ingest(envelope(grant["device_id"]))
    changed = envelope(
        grant["device_id"], capture="capture_0002", revision=2, value="changed", base=1
    )
    assert (await svc.ingest(changed))["status"] == "needs_identity_confirmation"
    svc.confirm(grant["device_id"], changed.capture_id, first["account_id"])
    from muse_console.errors import UpstreamAuthError

    svc.pool.get(first["account_id"]).record_error(UpstreamAuthError("private"))
    assert (await svc.ingest(changed))["credential_version"] == 2
    current = svc.pool.get(first["account_id"])
    assert current.last_error is current.last_error_code is current.last_error_at is None
    third = envelope(grant["device_id"], capture="capture_0003", revision=3, value="third", base=2)
    assert (await svc.ingest(third))["status"] == "needs_identity_confirmation"
    stale = envelope(grant["device_id"], capture="capture_0004", revision=4, value="fourth", base=1)
    assert (await svc.ingest(stale))["status"] == "conflict"
    assert svc.pool.all()[0].credential_version == 2
    await svc.close()


async def test_cas_detects_renewal_during_probe(tmp_path):
    svc, grant = await setup_service(tmp_path)
    result = await svc.ingest(envelope(grant["device_id"]))
    account = svc.pool.get(result["account_id"])
    account.credential_subject = "known-subject"

    async def validate(env):
        updated = account.model_copy(deep=True)
        updated.cookies = {k: "renewed" for k in updated.cookies}
        updated.credential_version = 2
        await svc.pool.upsert_if_current(updated, account)
        return VerifiedCredential(env.cookies, "known-subject")

    svc.validator = validate
    changed = envelope(
        grant["device_id"], capture="capture_0002", revision=2, value="changed", base=1
    )
    assert (await svc.ingest(changed))["status"] == "conflict"
    assert svc.pool.get(account.id).cookies["hatch_sess"] == "renewed"
    await svc.close()


async def test_recovery_when_account_commit_precedes_receipt(tmp_path):
    svc, grant = await setup_service(tmp_path)
    env = envelope(grant["device_id"])
    result = await svc.ingest(env)
    with svc.devices.db:
        svc.devices.db.execute("DELETE FROM captures")
        svc.devices.db.execute("DELETE FROM bindings")
    await svc.close()
    restarted = CredentialIngestion(
        svc.settings, svc.pool, CredentialDevices(tmp_path / "devices.db"), svc.validator
    )
    await restarted.startup()
    assert await restarted.ingest(env) == result
    assert restarted.devices.remaining(grant["device_id"]) == 0
    await restarted.close()


async def test_validator_exception_secret_not_returned_and_quota(tmp_path):
    svc, grant = await setup_service(tmp_path)
    await svc.ingest(envelope(grant["device_id"]))
    other = envelope(grant["device_id"], capture="capture_other", store="1")
    assert (await svc.ingest(other))["status"] == "needs_identity_confirmation"

    async def broken(env):
        raise RuntimeError("super-secret")

    svc.validator = broken
    changed = envelope(
        grant["device_id"], capture="capture_error", revision=2, value="changed", base=1
    )
    result = await svc.ingest(changed)
    assert result["status"] == "retry_wait"
    assert "super-secret" not in str(result)
    await svc.close()


async def test_revocation_while_validating(tmp_path):
    svc, grant = await setup_service(tmp_path)

    async def validate(env):
        svc.devices.revoke(env.device_id)
        return VerifiedCredential(env.cookies)

    svc.validator = validate
    from muse_console.services.credential_devices import CredentialBoundaryError

    with pytest.raises(CredentialBoundaryError):
        await svc.ingest(envelope(grant["device_id"]))
    assert svc.pool.all() == []
    await svc.close()


async def test_unchanged_cannot_ack_invalid_or_renewed_snapshot(tmp_path):
    from muse_console.accounts.model import AccountStatus

    svc, grant = await setup_service(tmp_path)
    initial = envelope(grant["device_id"])
    result = await svc.ingest(initial)
    account = svc.pool.get(result["account_id"])
    invalid = account.model_copy(deep=True)
    invalid.status = AccountStatus.INVALID
    await svc.pool.upsert(invalid)
    replay = envelope(grant["device_id"], capture="capture_invalid", revision=2, base=1)
    assert (await svc.ingest(replay))["status"] == "conflict"
    # Original capture receipt is historical, not a fresh login-health claim.
    assert await svc.ingest(initial) == result
    await svc.close()


async def test_receipt_pruning_preserves_source_quota_and_highwater(tmp_path):
    svc, grant = await setup_service(tmp_path)
    await svc.ingest(envelope(grant["device_id"]))
    with svc.devices.db:
        svc.devices.db.execute("UPDATE capture_times SET saved=0")
        svc.pool.all()[0].meta.pop("credential_ingestion")
    svc.devices.prune()
    assert svc.devices.capture(grant["device_id"], "capture_0001") is None
    assert svc.devices.remaining(grant["device_id"]) == 0
    stale = envelope(grant["device_id"], capture="capture_stale", value="other", revision=1, base=1)
    assert (await svc.ingest(stale))["status"] == "conflict"
    await svc.close()


async def test_admin_confirmation_reconciles_stale_base_without_mutating_payload(tmp_path):
    svc, grant = await setup_service(tmp_path)
    device = grant["device_id"]
    first = await svc.ingest(envelope(device))
    current = svc.pool.get(first["account_id"])
    renewed = current.model_copy(deep=True)
    renewed.cookies["hatch_gw"] = "server-renewal"
    await svc.pool.upsert(renewed)
    candidate = envelope(device, capture="capture_reconcile", revision=2, base=1, value="candidate")
    conflict = await svc.ingest(candidate)
    assert conflict["status"] == "conflict"
    assert conflict["account_id"] == current.id and conflict["credential_version"] == 2
    svc.confirm(device, candidate.capture_id, current.id)
    confirmation = svc.devices.confirmation(device, candidate.capture_id)
    assert confirmation["version"] == 2
    assert confirmation["fingerprint"] == candidate.fingerprint()
    # Confirmation survives restart and never raises the extension's base.
    await svc.close()
    svc = CredentialIngestion(
        svc.settings, svc.pool, CredentialDevices(tmp_path / "devices.db"), svc.validator
    )
    await svc.startup()
    result = await svc.ingest(candidate)
    assert result["status"] == "committed" and result["credential_version"] == 3
    assert candidate.base_credential_version == 1
    following = envelope(device, capture="capture_following", revision=3, base=1, value="other")
    assert (await svc.ingest(following))["status"] == "conflict"
    await svc.close()


async def test_confirmed_same_fingerprint_runs_full_validation(tmp_path):
    from muse_console.accounts.model import AccountStatus
    from muse_console.services.credential_validator import CredentialValidationError

    svc, grant = await setup_service(tmp_path)
    device = grant["device_id"]
    first = await svc.ingest(envelope(device))
    current = svc.pool.get(first["account_id"])
    updated = current.model_copy(deep=True)
    updated.status = AccountStatus.INVALID
    await svc.pool.upsert(updated)
    candidate = envelope(device, capture="capture_revalidate", revision=2, base=0)
    assert (await svc.ingest(candidate))["status"] == "conflict"
    svc.confirm(device, candidate.capture_id, current.id)
    calls = []

    async def not_assigned(env):
        calls.append(env.capture_id)
        raise CredentialValidationError("awaiting_login", "session_not_assigned")

    svc.validator = not_assigned
    result = await svc.ingest(candidate)
    assert result["status"] == "awaiting_login"
    assert calls == [candidate.capture_id]
    assert svc.pool.get(current.id).status == AccountStatus.INVALID
    await svc.close()


async def test_confirmation_invalidated_by_renewal_before_or_during_validation(tmp_path):
    svc, grant = await setup_service(tmp_path)
    device = grant["device_id"]
    first = await svc.ingest(envelope(device))
    candidate = envelope(device, capture="capture_reconcile", revision=2, base=0, value="candidate")
    assert (await svc.ingest(candidate))["status"] == "conflict"
    svc.confirm(device, candidate.capture_id, first["account_id"])

    async def renew():
        current = svc.pool.get(first["account_id"])
        updated = current.model_copy(deep=True)
        updated.cookies["hatch_gw"] += "-renewed"
        await svc.pool.upsert(updated)

    await renew()
    result = await svc.ingest(candidate)
    assert result["code"] == "confirmation_version_changed"
    assert result["credential_version"] == 2
    assert svc.devices.confirmation(device, candidate.capture_id) is None
    svc.confirm(device, candidate.capture_id, first["account_id"])

    async def racing(env):
        await renew()
        return VerifiedCredential(env.cookies)

    svc.validator = racing
    result = await svc.ingest(candidate)
    assert result["status"] == "conflict" and result["credential_version"] == 3
    assert svc.devices.confirmation(device, candidate.capture_id) is None
    assert svc.pool.get(first["account_id"]).cookies["hatch_sess"] != candidate.cookies[0].value
    await svc.close()


async def test_confirmation_does_not_authorize_changed_capture_payload(tmp_path):
    from muse_console.services.credential_devices import CredentialBoundaryError

    svc, grant = await setup_service(tmp_path)
    device = grant["device_id"]
    first = await svc.ingest(envelope(device))
    candidate = envelope(device, capture="capture_reconcile", revision=2, base=0, value="candidate")
    await svc.ingest(candidate)
    svc.confirm(device, candidate.capture_id, first["account_id"])
    for changed in (
        candidate.model_copy(update={"source_revision": 3}),
        candidate.model_copy(update={"store_id": "1"}),
        envelope(device, capture=candidate.capture_id, revision=2, base=0, value="tampered"),
    ):
        with pytest.raises(CredentialBoundaryError, match="capture_reused"):
            await svc.ingest(changed)
    assert svc.pool.get(first["account_id"]).credential_version == 1
    await svc.close()


async def test_browser_probe_context_and_cleanup():
    from muse_console.drivers.browser.session_auth import SessionProbeError, probe_session

    records = envelope("device").cookies
    calls = []

    class Browser:
        closed = False

        async def send(self, method, params=None, **kwargs):
            calls.append((method, params))
            if method == "Target.createBrowserContext":
                return {"browserContextId": "isolated"}
            if method == "Target.createTarget":
                return {"targetId": "page"}
            if method == "Storage.getCookies":
                return {
                    "cookies": [
                        dict(
                            name=r.name,
                            value=r.value,
                            domain=r.domain,
                            path=r.path,
                            secure=True,
                            httpOnly=True,
                            expires=-1,
                        )
                        for r in records
                    ]
                }
            return {}

    class Page:
        async def send(self, *args, **kwargs):
            return {}

        async def evaluate(self, expression, **kwargs):
            if "ready:" in expression:
                return {"origin": "https://muse.ai", "ready": "complete"}
            assert "redirect:'error'" in expression
            return {"http": 200, "assigned": True}

        async def close(self):
            pass

    async def connect(url):
        return Page()

    driver = SimpleNamespace(
        _browser=Browser(),
        _chromium=SimpleNamespace(port=1234, proc=SimpleNamespace(returncode=None)),
    )
    refreshed, subject = await probe_session(driver, records, connect=connect)
    assert refreshed == records and subject is None
    for method, params in calls:
        if method.startswith("Storage.") or method == "Target.disposeBrowserContext":
            assert params["browserContextId"] == "isolated"
    assert calls[-1][0] == "Target.disposeBrowserContext"

    async def rejected(expression, **kwargs):
        if "ready:" in expression:
            return {"origin": "https://muse.ai", "ready": "complete"}
        return {"http": 403}

    page = Page()
    page.evaluate = rejected

    async def reject_connect(url):
        return page

    with pytest.raises(SessionProbeError, match="session_not_assigned"):
        await probe_session(driver, records, connect=reject_connect)
    assert calls[-1][0] == "Target.disposeBrowserContext"

    async def cancelled(expression, **kwargs):
        raise asyncio.CancelledError

    page.evaluate = cancelled
    with pytest.raises(asyncio.CancelledError):
        await probe_session(driver, records, connect=reject_connect)
    assert calls[-1][0] == "Target.disposeBrowserContext"
    driver._chromium.proc = None
    with pytest.raises(SessionProbeError):
        await probe_session(driver, records, connect=connect)
