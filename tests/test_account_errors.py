from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from muse_console.accounts import Account, AccountPool, AccountStatus, JsonAccountStore
from muse_console.accounts.keepalive import renew_account
from muse_console.accounts.model import ERROR_REASONS, LEGACY_ERROR_CODES
from muse_console.api.routes.admin import reset_account
from muse_console.drivers.base import SessionInfo
from muse_console.errors import (
    UpstreamAuthError,
    UpstreamError,
    UpstreamQuotaError,
    UpstreamRefused,
    UpstreamTimeout,
)
from muse_console.services.account_manager import AccountManager


@pytest.fixture
def pool(tmp_path):
    return AccountPool(JsonAccountStore(tmp_path / "accounts.json"))


@pytest.mark.parametrize(
    "error,code,status",
    [
        (
            UpstreamTimeout("composer readiness timed out (code=composer_ready_timeout)"),
            "composer_ready_timeout",
            "active",
        ),
        (UpstreamTimeout("private"), "upstream_timeout", "active"),
        (UpstreamAuthError("private"), "upstream_auth_failed", "invalid"),
        (UpstreamQuotaError("private"), "upstream_quota_exhausted", "cooling"),
        (
            UpstreamRefused("upstream replied with text only (code=media_missing)"),
            "media_missing",
            "active",
        ),
        (UpstreamRefused("private"), "upstream_refused", "active"),
        (UpstreamError("private"), "upstream_error", "cooling"),
        (RuntimeError("private"), "request_failed", "active"),
    ],
)
async def test_result_classification_persistence_and_status(pool, error, code, status):
    await pool.upsert(Account(id="test"))
    before = time.time()
    held = await pool.acquire()
    await pool.release(held, error)
    current = pool.get("test")
    assert current.last_error_code == code
    assert current.last_error == ERROR_REASONS[code]
    assert before <= current.last_error_at <= time.time()
    assert current.status.value == status
    assert current.fail_count == 1
    assert "private" not in current.model_dump_json() + json.dumps(current.public())
    restarted = AccountPool(pool.store)
    await restarted.load()
    assert restarted.get("test") == current
    assert restarted.all_public()[0]["last_error_code"] == code


@pytest.mark.parametrize(
    "legacy,code",
    list(LEGACY_ERROR_CODES.items())
    + [
        ("private", "request_failed"),
        ("composer readiness timed out (code=composer_ready_timeout)", "request_failed"),
        ("media_missing", "request_failed"),
        ("upstream request failed", "request_failed"),
    ],
)
def test_legacy_mapping_does_not_guess_or_invent_time(legacy, code):
    account = Account.model_validate({"last_error": legacy})
    public = account.public()
    assert public["last_error_code"] == code
    assert public["last_error"] == ERROR_REASONS[code]
    assert public["last_error_at"] is None
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.parametrize(
    "error,code",
    [
        (UpstreamTimeout("private code=composer_ready_timeout"), "upstream_timeout"),
        (
            UpstreamTimeout("composer readiness timed out (code=composer_ready_timeout) private"),
            "upstream_timeout",
        ),
        (UpstreamTimeout("private", code="composer_ready_timeout"), "upstream_timeout"),
        (UpstreamRefused("private code=media_missing"), "upstream_refused"),
        (
            UpstreamRefused("upstream replied with text only (code=media_missing) private"),
            "upstream_refused",
        ),
        (UpstreamError("private", code="media_missing"), "upstream_error"),
        (
            RuntimeError("composer readiness timed out (code=composer_ready_timeout)"),
            "request_failed",
        ),
        (type("UpstreamAuthError", (Exception,), {})("private"), "request_failed"),
    ],
)
def test_untrusted_text_code_and_class_name_are_not_forwarded(error, code):
    account = Account()
    account.record_error(error)
    assert account.public()["last_error_code"] == code
    assert account.last_error == ERROR_REASONS[code]


@pytest.mark.parametrize("code", ["private", "UpstreamTimeout", "", [], {}])
def test_untrusted_stored_code_is_not_public(code):
    account = Account(last_error="private", last_error_code=code)
    assert account.last_error_code is None
    # Also defend against unvalidated model_copy/assignment.
    account.last_error_code = code
    assert account.public()["last_error_code"] == "request_failed"
    assert "private" not in json.dumps(account.public())


def assert_clear(account):
    assert account.last_error is account.last_error_code is account.last_error_at is None
    assert all(
        account.public()[key] is None for key in ("last_error", "last_error_code", "last_error_at")
    )


@pytest.mark.parametrize("path", ["success", "reset", "cookies", "renewal"])
async def test_clear_paths_clear_all_error_fields(pool, path):
    account = Account(cookies={"hatch_sess": "old"})
    account.record_error(UpstreamTimeout("private"))
    await pool.upsert(account)
    if path == "success":
        await pool.release(await pool.acquire())
    elif path == "reset":
        await reset_account(account.id, SimpleNamespace(pool=pool))
    elif path == "cookies":
        await AccountManager(pool).update(
            account.id, cookies={"hatch_sess": "new", "hatch_vml": "new-vml"}
        )
    else:

        class Driver:
            async def renew_session(self, snapshot):
                return SessionInfo(ok=True)

        assert (await renew_account(pool, Driver(), account.id))["ok"]
    assert_clear(pool.get(account.id))
    restarted = AccountPool(pool.store)
    await restarted.load()
    assert_clear(restarted.get(account.id))


async def test_history_is_not_account_invalidation_and_success_preserves_policy(pool):
    account = Account(status=AccountStatus.INVALID)
    account.record_error(UpstreamAuthError("private"))
    pool._apply_result(account, UpstreamTimeout("private"))
    assert account.status == AccountStatus.INVALID
    assert account.public()["last_error_code"] == "upstream_timeout"
    pool._apply_result(account, None)
    assert_clear(account)
    assert account.status == AccountStatus.INVALID


async def test_renewal_auth_records_safe_reason_and_time(pool):
    account = await pool.upsert(Account())

    class Driver:
        async def renew_session(self, snapshot):
            raise UpstreamAuthError("private", code="private")

    before = time.time()
    result = await renew_account(pool, Driver(), account.id)
    current = pool.get(account.id)
    assert not result["ok"]
    assert current.status == AccountStatus.INVALID
    assert current.last_error_code == "upstream_auth_failed"
    assert current.last_error == ERROR_REASONS["upstream_auth_failed"]
    assert before <= current.last_error_at <= time.time()
    assert "private" not in current.model_dump_json()
