from __future__ import annotations

import json
import os
import stat

import pytest

from muse2api.accounts import Account, AccountPool, JsonAccountStore, SQLiteAccountStore
from muse2api.services.account_manager import AccountManager
from muse2api.upstream.muse import SESSION_COOKIES


def cookies(value="private-session"):
    return {name: value + name for name in SESSION_COOKIES}


def store(tmp_path):
    return SQLiteAccountStore(
        tmp_path / "accounts.db",
        key_path=tmp_path / "credentials.key",
        legacy_path=tmp_path / "accounts.json",
    )


async def test_encrypted_restart_and_no_public_secrets(tmp_path):
    original = Account(
        cookies=cookies(),
        meta={"nested": {"secret": "sensitive-metadata"}},
        last_error="sensitive-error",
    )
    await store(tmp_path).save([original])
    raw = (tmp_path / "accounts.db").read_bytes()
    assert b"private-session" not in raw and b"sensitive-metadata" not in raw
    loaded = await store(tmp_path).load()
    assert loaded[0] == original
    public = json.dumps(loaded[0].public())
    assert "private-session" not in public and "sensitive" not in public
    assert loaded[0].public()["cookies"]["hatch_sess"] == "***"
    assert loaded[0].public()["meta"] == {}
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "credentials.key").stat().st_mode) == 0o600


async def test_key_missing_and_wrong_key_fail_closed(tmp_path):
    from cryptography.fernet import Fernet

    await store(tmp_path).save([Account(cookies=cookies())])
    key = tmp_path / "credentials.key"
    key.rename(tmp_path / "saved.key")
    with pytest.raises(RuntimeError, match="missing"):
        await store(tmp_path).load()
    assert not key.exists()
    key.write_bytes(Fernet.generate_key())
    with pytest.raises(RuntimeError, match="decrypt"):
        await store(tmp_path).save([])
    with pytest.raises(RuntimeError, match="decrypt"):
        await store(tmp_path).load()


async def test_one_time_migration_preserves_original(tmp_path):
    legacy = tmp_path / "accounts.json"
    account = Account(cookies=cookies(), meta={"secret": "encrypted"})
    await JsonAccountStore(legacy).save([account])
    original = legacy.read_bytes()
    assert (await store(tmp_path).load())[0] == account
    await store(tmp_path).save([])
    assert await store(tmp_path).load() == []
    assert legacy.read_bytes() == original


async def test_existing_database_wins_and_legacy_duplicates_allowed(tmp_path):
    first = Account(cookies=cookies("one"))
    await store(tmp_path).save([first])
    await JsonAccountStore(tmp_path / "accounts.json").save([Account(cookies=cookies("two"))])
    duplicate = Account(cookies=first.cookies)
    await store(tmp_path).save([first, duplicate])
    assert await store(tmp_path).load() == [first, duplicate]


async def test_batch_preview_partial_errors_and_duplicates(tmp_path):
    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    content = json.dumps(
        [
            {"label": "one", "cookies": cookies()},
            {"cookies": cookies()},
            {"cookies": {"secret": "dont-echo"}},
            42,
        ]
    )
    preview = await manager.preview(content)
    assert preview["summary"] == {"ready": 1, "imported": 0, "duplicate": 1, "error": 2}
    assert pool.all() == []
    result = await manager.import_accounts(content)
    assert result["summary"] == {"ready": 0, "imported": 1, "duplicate": 1, "error": 2}
    assert "private-session" not in json.dumps(result) and "dont-echo" not in json.dumps(result)
    assert (await manager.import_accounts(content))["summary"]["duplicate"] == 2


@pytest.mark.parametrize("kind", ["browser", "header", "headers", "netscape", "wrapped"])
async def test_supported_import_formats(tmp_path, kind):
    values = cookies()
    browser = [{"name": k, "value": v, "expirationDate": 2000000000} for k, v in values.items()]
    header = "; ".join(f"{k}={v}" for k, v in values.items())
    content = {
        "browser": json.dumps(browser),
        "header": "Cookie: " + header,
        "headers": header + "\nmalformed",
        "netscape": "# Netscape HTTP Cookie File\n"
        + "\n".join(f".muse.ai\tTRUE\t/\tTRUE\t2000000000\t{k}\t{v}" for k, v in values.items()),
        "wrapped": json.dumps([{"label": "browser", "cookies": browser}]),
    }[kind]
    manager = AccountManager(AccountPool(store(tmp_path)))
    result = await manager.import_accounts(content)
    assert result["summary"]["imported"] == 1
    assert result["summary"]["error"] == (1 if kind == "headers" else 0)


async def test_update_validates_without_mutating_original(tmp_path):
    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    account = Account(cookies=cookies())
    await pool.upsert(account)
    with pytest.raises(ValueError):
        await manager.update(account.id, cookies={"hatch_sess": "bad"})
    with pytest.raises(ValueError):
        await manager.update(account.id, max_concurrency=17)
    assert pool.get(account.id).cookies == cookies()
    updated = await manager.update(account.id, max_concurrency=2, enabled=False)
    assert updated.max_concurrency == 2 and not updated.enabled


async def test_duplicate_legacy_is_preserved_without_touching_source(tmp_path):
    accounts = [Account(cookies=cookies()), Account(cookies=cookies())]
    legacy = tmp_path / "accounts.json"
    await JsonAccountStore(legacy).save(accounts)
    original = legacy.read_bytes()
    assert await store(tmp_path).load() == accounts
    assert legacy.read_bytes() == original


async def test_pool_routes_auth_preview_strategy(tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from muse2api.api.routes.account_pool import router
    from muse2api.errors import Unauthorized

    pool = AccountPool(store(tmp_path))
    app = FastAPI()
    app.state.services = SimpleNamespace(
        pool=pool,
        account_manager=AccountManager(pool),
        settings=SimpleNamespace(effective_admin_key="admin"),
    )
    app.include_router(router)
    with TestClient(app) as client:
        with pytest.raises(Unauthorized):
            client.get("/admin/pool")
        headers = {"Authorization": "Bearer admin"}
        response = client.patch("/admin/pool", headers=headers, json={"strategy": "round_robin"})
        assert response.json()["strategy"] == "round_robin"
        assert response.json()["strategy_persisted"] is True
        assert (
            client.patch("/admin/pool", headers=headers, json={"strategy": "unknown"}).status_code
            == 422
        )
        response = client.post(
            "/admin/accounts/import",
            headers=headers,
            json={"content": json.dumps([{"cookies": cookies()}]), "preview": True},
        )
        assert response.json()["summary"]["ready"] == 1
        assert client.get("/admin/pool", headers=headers).json()["stats"]["total"] == 0


async def test_login_legacy_update_contract_and_dedup(tmp_path):
    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    account = await manager.import_account(
        cookies(), label="login", cookie_expires={"hatch_sess": 123}
    )
    assert account.cookie_expires == {"hatch_sess": 123}
    with pytest.raises(ValueError, match="duplicate"):
        await manager.import_account(cookies())
    legacy = await manager.create_legacy({"cookies": {"hatch_sess": "legacy"}, "label": "old"})
    with pytest.raises(ValueError, match="duplicate"):
        await manager.create_legacy({"cookies": {"hatch_sess": "legacy"}})
    updated = await manager.update_account(
        account.id, enabled=False, cookie_expires={"hatch_sess": 456}, max_concurrency=3
    )
    assert updated.cookie_expires["hatch_sess"] == 456 and updated.max_concurrency == 3
    with pytest.raises(ValueError, match="duplicate"):
        await manager.update_account(legacy.id, cookies=cookies())
    assert pool.get(legacy.id).cookies == {"hatch_sess": "legacy"}


async def test_policy_survives_restart(tmp_path):
    pool = AccountPool(store(tmp_path))
    await pool.load()
    await pool.set_strategy("round_robin")
    restarted = AccountPool(store(tmp_path), strategy="lru")
    await restarted.load()
    assert restarted.strategy_name == "round_robin"


def test_public_quota_is_strict_allowlist():
    account = Account(
        cookies=cookies(),
        last_error="hatch_sess=private-session",
        meta={
            "secret": "hidden",
            "quota": {
                "plan": "free",
                "remaining": 12,
                "cookie": "hidden",
                "nested": {"secret": "hidden"},
                "used": "private-session",
            },
        },
    )
    public = account.public()
    assert public["meta"] == {"quota": {"plan": "free", "remaining": 12}}
    assert "private-session" not in json.dumps(public)


@pytest.mark.parametrize("error", [None, "auth"])
async def test_old_lease_cannot_change_new_credentials(tmp_path, error):
    from muse2api.accounts import AccountStatus
    from muse2api.errors import UpstreamAuthError

    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    account = await manager.import_account(cookies("old"))
    held = await pool.acquire()
    await manager.update(account.id, cookies=cookies("new"))
    await pool.release(held, UpstreamAuthError("old session") if error else None)
    current = pool.get(account.id)
    assert current.cookies == cookies("new")
    assert current.status == AccountStatus.ACTIVE
    assert current.success_count == current.fail_count == 0
    assert pool.stats()["inflight"] == 0


async def test_patch_null_and_empty_validation(tmp_path):
    pool = AccountPool(store(tmp_path), max_concurrency=3)
    manager = AccountManager(pool)
    account = await manager.import_account(cookies())
    await manager.update(account.id, max_concurrency=2)
    await manager.update(account.id, max_concurrency=None)
    assert pool.all_public()[0]["effective_max_concurrency"] == 3
    for changes in ({"label": None}, {"enabled": None}, {"cookies": {}}):
        with pytest.raises(ValueError):
            await manager.update(account.id, **changes)


async def test_pool_rejects_complete_duplicates_but_keeps_legacy(tmp_path):
    pool = AccountPool(store(tmp_path))
    await pool.upsert(Account(cookies={"hatch_sess": "legacy"}))
    await pool.upsert(Account(cookies={"hatch_sess": "legacy"}))
    await pool.upsert(Account(cookies=cookies()))
    with pytest.raises(ValueError, match="duplicate"):
        await pool.upsert(Account(cookies=cookies()))


async def test_renew_does_not_overwrite_concurrent_update(tmp_path):
    from muse2api.accounts.keepalive import renew_account
    from muse2api.drivers.base import SessionInfo

    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    account = await manager.import_account(cookies("old"))

    class Driver:
        async def renew_session(self, snapshot):
            await manager.update(account.id, cookies=cookies("new"))
            return SessionInfo(ok=True, cookies=cookies("rotated"))

    result = await renew_account(pool, Driver(), account.id)
    assert not result["ok"]
    assert pool.get(account.id).cookies == cookies("new")


@pytest.mark.parametrize(
    "domain", ["muse.ai.evil.test", "evilmuse.ai", "other.test", "", ".muse.ai.evil"]
)
async def test_browser_cookie_domains_cannot_supply_required_session(tmp_path, domain):
    manager = AccountManager(AccountPool(store(tmp_path)))
    exported = [{"name": k, "value": v, "domain": domain} for k, v in cookies().items()]
    assert (await manager.import_accounts(json.dumps(exported)))["summary"]["error"] == 1


@pytest.mark.parametrize("kind", ["browser", "netscape", "dict", "header"])
async def test_cookie_filter_keeps_only_muse_session_values(tmp_path, kind):
    pool = AccountPool(store(tmp_path))
    manager = AccountManager(pool)
    values = cookies()
    exported = [{"name": k, "value": v, "domain": ".api.muse.ai"} for k, v in values.items()]
    exported += [
        {"name": "hatch_sess", "value": "cross-site", "domain": "unrelated.test"},
        {"name": "tracking", "value": "unwanted", "domain": "muse.ai"},
    ]
    if kind == "browser":
        content = json.dumps(exported)
    elif kind == "netscape":
        content = "# Netscape HTTP Cookie File\n" + "\n".join(
            f"#HttpOnly_{item['domain']}\tTRUE\t/\tTRUE\t2000000000\t{item['name']}\t{item['value']}"
            for item in exported
        )
    elif kind == "dict":
        content = json.dumps([{"cookies": {**values, "tracking": "unwanted"}}])
    else:
        content = "; ".join(f"{k}={v}" for k, v in {**values, "tracking": "unwanted"}.items())
    assert (await manager.import_accounts(content))["summary"]["imported"] == 1
    assert pool.all()[0].cookies == values


async def test_policy_survives_restart_without_accounts(tmp_path):
    pool = AccountPool(store(tmp_path))
    await pool.load()
    await pool.set_strategy("affinity")
    restarted = AccountPool(store(tmp_path), strategy="round_robin")
    await restarted.load()
    assert restarted.strategy_name == "affinity"
