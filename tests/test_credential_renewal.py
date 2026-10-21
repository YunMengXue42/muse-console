from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from muse_console.accounts.keepalive import next_renewal, renew_account
from muse_console.accounts.model import Account, AccountStatus
from muse_console.accounts.pool import AccountPool
from muse_console.accounts.store import SQLiteAccountStore
from muse_console.drivers.base import SessionInfo
from muse_console.errors import UpstreamAuthError
from test_credentials import envelope, records


@pytest.fixture
def pool(tmp_path):
    return AccountPool(SQLiteAccountStore(tmp_path / "accounts.db", key_path=tmp_path / "key"))


async def test_refresh_serialized_with_candidate_ingestion(pool):
    account = await pool.upsert(
        Account(cookies=envelope().projection(), credential_records=records())
    )
    entered, proceed = asyncio.Event(), asyncio.Event()
    active = 0
    maximum = 0

    class Driver:
        async def renew_session(self, snapshot):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            entered.set()
            await proceed.wait()
            active -= 1
            return SessionInfo(ok=True, credential_records=records())

    first = asyncio.create_task(renew_account(pool, Driver(), account.id))
    await entered.wait()
    second = asyncio.create_task(renew_account(pool, Driver(), account.id))
    proceed.set()
    result = await asyncio.gather(first, second)
    assert all(row["ok"] for row in result)
    assert maximum == 1


async def test_scope_refresh_keeps_partition_and_increments_version(pool):
    source = records(session=False, expires=time.time() + 300)
    account = await pool.upsert(
        Account(cookies={r.name: r.value for r in source}, credential_records=source)
    )
    replacement = records(session=False, expires=time.time() + 3600)

    class Driver:
        async def renew_session(self, snapshot):
            return SessionInfo(ok=True, credential_records=replacement)

    assert (await renew_account(pool, Driver(), account.id))["ok"]
    current = pool.get(account.id)
    assert current.credential_version == 2
    assert current.credential_records == replacement


async def test_network_failure_preserves_credentials_and_backs_off(pool):
    account = await pool.upsert(
        Account(cookies=envelope().projection(), credential_records=records())
    )

    class Driver:
        async def renew_session(self, snapshot):
            raise RuntimeError("secret-upstream-message")

    result = await renew_account(pool, Driver(), account.id)
    current = pool.get(account.id)
    assert current.cookies == account.cookies
    assert current.status == AccountStatus.ACTIVE
    assert current.credential_version == account.credential_version
    assert current.meta["next_renewal_at"] > time.time()
    assert "secret-upstream" not in str(result) + str(current.meta)


async def test_failed_auth_needs_reauth_without_deleting_account(pool):
    account = await pool.upsert(Account(cookies=envelope().projection()))

    class Driver:
        async def renew_session(self, snapshot):
            raise UpstreamAuthError("private")

    assert not (await renew_account(pool, Driver(), account.id))["ok"]
    assert pool.get(account.id).status == AccountStatus.INVALID
    assert pool.get(account.id).cookies == account.cookies


def test_scheduling_uses_expiry_not_fixed_interval():
    account = Account(credential_records=records(session=False, expires=1100.0))
    assert 1000 < next_renewal(account, 21600, now=1000) < 1100


async def test_false_ok_does_not_merge_rejected_rotated_values(pool):
    account = await pool.upsert(Account(cookies=envelope().projection()))
    driver = SimpleNamespace()

    async def renew(snapshot):
        return SessionInfo(ok=False, cookies={"hatch_sess": "not-valid"})

    driver.renew_session = renew
    assert not (await renew_account(pool, driver, account.id))["ok"]
    assert pool.get(account.id).cookies == account.cookies


async def test_expired_scoped_cookie_is_auth_failure_not_network_retry(pool, tmp_path):
    from muse_console.config import Settings
    from muse_console.drivers.browser.driver import BrowserDriver

    source = records(session=False, expires=time.time() - 1)
    account = await pool.upsert(
        Account(
            cookies={r.name: r.value for r in source},
            credential_records=source,
        )
    )
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    result = await renew_account(pool, driver, account.id)
    assert not result["ok"]
    current = pool.get(account.id)
    assert current.status == AccountStatus.INVALID
    assert current.meta["renewal_state"] == "needs_reauth"
    assert "renewal_failures" not in current.meta
