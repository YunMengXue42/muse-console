"""Tab pool of the browser driver, with Chromium replaced by fake tabs."""

from __future__ import annotations

import asyncio
import itertools

import pytest

from muse2api.accounts import Account
from muse2api.config import Settings
from muse2api.drivers.browser.driver import BrowserDriver, _Tab


class _FakeSession:
    closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def driver(tmp_path, monkeypatch) -> BrowserDriver:
    drv = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path, account_max_concurrency=2))
    ids = itertools.count()

    async def fake_open(account: Account) -> _Tab:
        return _Tab(account.id, "ctx", f"t{next(ids)}", _FakeSession())

    monkeypatch.setattr(drv, "_open_tab", fake_open)
    return drv


ACC = Account(id="a0", cookies={"hatch_sess": "x"})


async def test_parallel_checkouts_get_separate_tabs(driver):
    t1 = await driver._checkout(ACC)
    t2 = await driver._checkout(ACC)
    assert t1 is not t2 and t1.busy and t2.busy
    assert (await driver.health())["tabs"] == 2


async def test_checkout_waits_at_the_limit_until_checkin(driver):
    t1 = await driver._checkout(ACC)
    await driver._checkout(ACC)
    third = asyncio.create_task(driver._checkout(ACC))
    await asyncio.sleep(0.01)
    assert not third.done()
    await driver._checkin(t1)
    assert await asyncio.wait_for(third, 1) is t1


async def test_media_opens_a_new_tab_rather_than_wiping_a_chat(driver):
    chat = await driver._checkout(ACC)
    chat.turns = [("user", "hi")]
    await driver._checkin(chat)
    media = await driver._checkout(ACC, prefer=lambda t: not t.has_state)
    assert media is not chat


async def test_at_the_limit_a_chat_tab_is_reused(driver):
    a = await driver._checkout(ACC)
    b = await driver._checkout(ACC)
    a.turns = [("user", "hi")]
    await driver._checkin(a)
    got = await driver._checkout(ACC, prefer=lambda t: not t.has_state)
    assert got is a  # nothing else free and no room for another tab
    await driver._checkin(b)


async def test_chat_prefers_the_tab_holding_its_conversation(driver):
    a = await driver._checkout(ACC)
    b = await driver._checkout(ACC)
    b.hint, b.turns = "user-1", [("user", "hi")]
    await driver._checkin(a)
    await driver._checkin(b)
    got = await driver._checkout(ACC, prefer=lambda t: t.has_state and t.hint == "user-1")
    assert got is b


async def test_stale_tab_closes_on_checkin(driver):
    tab = await driver._checkout(ACC)
    tab.stale = True
    await driver._checkin(tab)
    assert tab.session.closed
    assert (await driver.health())["tabs"] == 0


async def test_failed_open_releases_its_slot(driver, monkeypatch):
    ok_open = driver._open_tab

    async def broken(account):
        raise RuntimeError("chromium went away")

    monkeypatch.setattr(driver, "_open_tab", broken)
    with pytest.raises(RuntimeError):
        await driver._checkout(ACC)
    monkeypatch.setattr(driver, "_open_tab", ok_open)
    a = await driver._checkout(ACC)
    b = await driver._checkout(ACC)  # both slots still usable
    assert a is not b


async def test_close_then_checkin_closes_once(driver, monkeypatch):
    tab = await driver._checkout(ACC)
    calls = []
    original = driver._dispose_tab

    async def counting(t):
        calls.append(t)
        await original(t)

    monkeypatch.setattr(driver, "_dispose_tab", counting)
    tab.stale = True
    await driver._close_tab(tab)  # e.g. a CDP error path
    await driver._checkin(tab)    # the finally block still checks it in
    assert calls == [tab]
    assert (await driver.health())["tabs"] == 0


async def test_cancelled_checkin_still_frees_the_tab(driver):
    tab = await driver._checkout(ACC)
    await driver._tab_cond.acquire()  # lock contended while the request is cancelled
    task = asyncio.create_task(driver._checkin(tab))
    await asyncio.sleep(0)
    task.cancel()
    driver._tab_cond.release()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not tab.busy
    assert await asyncio.wait_for(driver._checkout(ACC), 1) is tab
