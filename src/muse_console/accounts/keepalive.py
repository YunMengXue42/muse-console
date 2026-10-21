"""single-writer renewal with scoped credentials and bounded, persisted retry scheduling."""

from __future__ import annotations

import asyncio
import logging
import random
import time

from ..drivers.base import MuseDriver
from ..errors import FeatureNotImplemented, UpstreamAuthError
from .credentials import project_records
from .model import Account, AccountStatus
from .pool import AccountPool

log = logging.getLogger(__name__)


def next_renewal(account: Account, interval: float, *, now: float | None = None) -> float:
    """schedule before real expiry with jitter; never extend the credential lifetime."""
    now = time.time() if now is None else now
    expirations = [r.expires for r in account.credential_records if r.expires]
    if not expirations:
        expirations = [float(v) for v in account.cookie_expires.values() if v > 0]
    delay = max(30.0, interval * random.uniform(0.85, 1.0))
    if expirations:
        remaining = min(expirations) - now
        delay = min(delay, max(5.0, remaining - min(300.0, max(30.0, remaining * 0.15))))
    return now + delay


async def renew_account(pool: AccountPool, driver: MuseDriver, account_id: str) -> dict:
    """serialize upstream refresh and commit only against the captured credential revision."""
    async with pool.credential_lock(account_id):
        expected = pool.get(account_id)
        if expected is None:
            return {"ok": False, "error": "not found"}
        account = expected.model_copy(deep=True)
        try:
            info = await asyncio.wait_for(driver.renew_session(account), timeout=45.0)
            if not info.ok:
                raise UpstreamAuthError("session is not assigned")
            if account.credential_records:
                if not info.credential_records:
                    raise FeatureNotImplemented("scope-preserving renewal is unavailable")
                projection = project_records(info.credential_records)
                account.credential_records = info.credential_records
                account.cookies = projection
                account.cookie_expires = {
                    r.name: int(r.expires) for r in info.credential_records if r.expires is not None
                }
            else:
                account.cookies.update(info.cookies)
                account.cookie_expires.update(info.cookie_expires)
        except FeatureNotImplemented:
            return {"ok": False, "error": "renewal is unsupported for these credentials"}
        except UpstreamAuthError:
            account.status = AccountStatus.INVALID
            account.last_error = "UpstreamAuthError"
            account.meta["renewal_state"] = "needs_reauth"
            await pool.upsert_if_current(account, expected)
            return {"ok": False, "error": "session rejected by upstream"}
        except asyncio.CancelledError:
            raise
        except Exception:
            account = expected.model_copy(deep=True)
            failures = min(int(account.meta.get("renewal_failures", 0)) + 1, 8)
            account.meta.update(
                renewal_failures=failures,
                renewal_state="retry_wait",
                next_renewal_at=time.time()
                + min(3600.0, 30 * 2 ** (failures - 1)) * random.uniform(0.85, 1.0),
            )
            await pool.upsert_if_current(account, expected)
            return {"ok": False, "error": "renewal temporarily unavailable"}

        subject = info.meta.get("credential_subject")
        if subject and account.credential_subject and subject != account.credential_subject:
            return {"ok": False, "error": "renewal identity changed"}
        account.meta.update({k: v for k, v in info.meta.items() if k != "credential_subject"})
        account.meta.update(renewed_at=time.time(), renewal_failures=0, renewal_state="ready")
        interval = getattr(getattr(driver, "settings", None), "keepalive_interval", 6 * 3600)
        account.meta["next_renewal_at"] = next_renewal(account, interval)
        if not await pool.upsert_if_current(account, expected):
            return {"ok": False, "error": "account changed during renewal"}
        return {"ok": True, "credential_version": pool.get(account_id).credential_version}


async def keepalive_loop(pool: AccountPool, driver: MuseDriver, interval: float) -> None:
    """perform due refreshes only; a closed user browser is not part of this lifecycle."""
    if interval <= 0:
        raise ValueError("keepalive interval must be positive")
    while True:
        await asyncio.sleep(min(30.0, interval))
        now = time.time()
        for account in pool.all():
            if not account.enabled or account.status == AccountStatus.INVALID:
                continue
            due = account.meta.get("next_renewal_at")
            if due is None:
                due = next_renewal(
                    account, interval, now=account.meta.get("renewed_at", account.created_at)
                )
            if now < due:
                continue
            try:
                result = await renew_account(pool, driver, account.id)
                log.info("keepalive %s -> %s", account.id, result.get("ok"))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("keepalive persistence failed for %s", account.id)
