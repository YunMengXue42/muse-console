"""Ensure failover never repeats emitted output or retains an upstream iterator."""

from __future__ import annotations

import pytest

from muse_console.accounts.model import Account
from muse_console.accounts.pool import AccountPool
from muse_console.accounts.store import JsonAccountStore
from muse_console.drivers.base import ChatRequest
from muse_console.drivers.mock import MockDriver
from muse_console.errors import UpstreamError
from muse_console.services.gateway import Gateway


class StreamingDriver(MockDriver):
    requires_account = True

    def __init__(self, *, fail: bool = False):
        super().__init__(delay=0)
        self.visited = []
        self.closed = []
        self.fail = fail

    async def chat_stream(self, account, req):
        """Emit a delta and expose deterministic finalization to the test."""
        self.visited.append(account.id)
        try:
            yield "first"
            if self.fail:
                raise UpstreamError("upstream failed after output")
            yield "second"
        finally:
            self.closed.append(account.id)


async def pool(tmp_path):
    """Build an offline pool with two distinct identities."""
    result = AccountPool(JsonAccountStore(tmp_path / "accounts.json"), strategy="round_robin")
    for index in range(2):
        await result.upsert(Account(id=f"a{index}", cookies={"hatch_sess": f"offline-{index}"}))
    return result


async def test_closing_stream_closes_driver_before_releasing_slot(tmp_path):
    accounts = await pool(tmp_path)
    driver = StreamingDriver()
    stream = Gateway(accounts, driver).chat_stream(ChatRequest(prompt="hi", model="m"))
    assert await anext(stream) == "first"
    await stream.aclose()
    assert driver.closed == ["a0"]
    assert accounts.stats()["inflight"] == 0
    assert accounts.get("a0").success_count == 0
    assert accounts.get("a0").fail_count == 0


async def test_failure_after_output_never_switches_accounts(tmp_path):
    accounts = await pool(tmp_path)
    driver = StreamingDriver(fail=True)
    stream = Gateway(accounts, driver).chat_stream(ChatRequest(prompt="hi", model="m"))
    assert await anext(stream) == "first"
    with pytest.raises(UpstreamError):
        await anext(stream)
    assert driver.visited == ["a0"]
    assert driver.closed == ["a0"]
    assert accounts.stats()["inflight"] == 0
    assert accounts.get("a0").fail_count == 1
