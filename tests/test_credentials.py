from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from muse_console.accounts.credentials import (
    CookieRecord,
    CredentialEnvelope,
    PartitionKey,
    project_records,
    records_from_cdp,
    records_to_cdp,
)
from muse_console.accounts.model import Account
from muse_console.accounts.pool import AccountPool
from muse_console.accounts.store import SQLiteAccountStore
from muse_console.upstream.muse import SESSION_COOKIES


def records(**changes):
    return [
        CookieRecord(name=name, value=f"secret-{name}", domain=".muse.ai", **changes)
        for name in SESSION_COOKIES
    ]


def envelope(**changes):
    return CredentialEnvelope(
        device_id="device",
        capture_id="capture-001",
        source_revision=1,
        captured_at=time.time(),
        store_id="0",
        cookies=records(),
        **changes,
    )


def test_scope_roundtrip_preserves_partition_and_expiry():
    partition = PartitionKey(topLevelSite="https://muse.ai", hasCrossSiteAncestor=True)
    source = records(
        session=False, expires=time.time() + 3600, partitionKey=partition, sameSite="no_restriction"
    )
    encoded = records_to_cdp(source)
    assert encoded[0]["partitionKey"]["hasCrossSiteAncestor"] is True
    assert encoded[0]["httpOnly"] is True
    decoded = records_from_cdp(encoded)
    assert decoded == source
    assert project_records(decoded) == {r.name: r.value for r in source}


def test_no_expiry_fabricated():
    assert all("expires" not in p for p in records_to_cdp(records()))
    with pytest.raises(ValueError, match="expired"):
        records_to_cdp(records(session=False, expires=time.time() - 1))


@pytest.mark.parametrize(
    "changes",
    [
        {"domain": "evil-muse.ai"},
        {"value": "bad\r\nCookie:x"},
        {"value": "bad; x=1"},
        {"path": "no-slash"},
        {"session": False},
        {"secure": False},
    ],
)
def test_invalid_cookie_scope(changes):
    raw = {"name": SESSION_COOKIES[0], "value": "private-value", "domain": ".muse.ai", **changes}
    with pytest.raises(ValidationError) as exc:
        CookieRecord(**raw)
    assert "private-value" not in str(exc.value)


def test_snapshot_refuses_mixed_partitions_and_duplicate_names():
    source = records()
    source[0] = source[0].model_copy(
        update={"partitionKey": PartitionKey(topLevelSite="https://example.com")}
    )
    with pytest.raises(ValidationError):
        CredentialEnvelope(**{**envelope().model_dump(), "cookies": source})
    with pytest.raises(ValueError, match="ambiguous"):
        project_records(records() + [records()[0].model_copy(update={"path": "/api"})])


def test_fingerprint_order_independent_and_repr_secret_free():
    original = envelope()
    other = original.model_copy(
        update={"cookies": list(reversed(original.cookies)), "source_revision": 2}
    )
    assert other.fingerprint() == original.fingerprint()
    assert "secret-" not in repr(original)
    account = Account(
        cookies=original.projection(),
        credential_records=original.cookies,
        credential_subject="subject-secret",
    )
    assert "secret-" not in repr(account)
    assert "credential_records" not in account.public()
    assert "credential_subject" not in account.public()


async def test_encrypted_persistence_and_monotonic_version(tmp_path):
    path = tmp_path / "accounts.db"
    store = SQLiteAccountStore(path, key_path=tmp_path / "key")
    pool = AccountPool(store)
    account = await pool.upsert(
        Account(cookies=envelope().projection(), credential_records=records())
    )
    assert account.credential_version == 1
    updated = account.model_copy(deep=True)
    updated.credential_records[0] = updated.credential_records[0].model_copy(
        update={"path": "/api"}
    )
    assert await pool.upsert_if_current(updated, account)
    assert pool.get(account.id).credential_version == 2
    assert b"secret-" not in path.read_bytes()
    loaded = (await store.load())[0]
    assert loaded.credential_records[0].path == "/api"
    assert loaded.credential_version == 2
