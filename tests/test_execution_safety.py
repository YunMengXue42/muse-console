"""Execution safety regressions; all browser operations are replaced by fakes."""

from __future__ import annotations

import asyncio

import pytest

from muse_console.accounts.model import Account
from muse_console.accounts.pool import AccountPool
from muse_console.accounts.store import JsonAccountStore
from muse_console.config import Settings
from muse_console.drivers.base import ChatRequest, ImageRequest, MediaResult, VideoRequest
from muse_console.drivers.browser.driver import BrowserDriver, _Tab
from muse_console.drivers.mock import MockDriver
from muse_console.errors import UpstreamError, UpstreamTimeout
from muse_console.services.gateway import Gateway


@pytest.fixture
async def accounts(tmp_path):
    pool = AccountPool(JsonAccountStore(tmp_path / "accounts.json"), strategy="round_robin")
    for i in range(2):
        await pool.upsert(Account(id=f"a{i}", cookies={"hatch_sess": f"offline-{i}"}))
    return pool


@pytest.mark.parametrize("kind", ["image", "video"])
@pytest.mark.parametrize("error_type", [UpstreamError, UpstreamTimeout])
async def test_media_gateway_never_replays_unknown_result(accounts, kind, error_type):
    failure = error_type("submitted, result unknown")

    class FailingMedia(MockDriver):
        requires_account = True

        def __init__(self):
            super().__init__(delay=0)
            self.visited = []

        async def generate_image(self, account, req):
            self.visited.append(account.id)
            raise failure

        async def generate_video(self, account, req):
            self.visited.append(account.id)
            raise failure

    driver = FailingMedia()
    gateway = Gateway(accounts, driver, max_failover=2)
    req = ImageRequest("draw", "m", n=3) if kind == "image" else VideoRequest("film", "m")
    with pytest.raises(error_type) as caught:
        await getattr(gateway, f"generate_{kind}")(req)
    assert caught.value is failure
    assert driver.visited == ["a0"]
    assert accounts.stats()["inflight"] == 0
    assert accounts.get("a0").success_count == 0
    assert accounts.get("a0").fail_count == 1


class FakeSession:
    closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def browser(tmp_path, monkeypatch):
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    opened = []

    async def open_tab(account):
        tab = _Tab(account.id, "fake-context", f"t{len(opened)}", FakeSession())
        opened.append(tab)
        return tab

    async def noop(*args):
        pass

    async def state(tab):
        return {"agentCount": 0, "lastText": ""}

    monkeypatch.setattr(driver, "_open_tab", open_tab)
    monkeypatch.setattr(driver, "_new_thread", noop)
    monkeypatch.setattr(driver, "_attach", noop)
    monkeypatch.setattr(driver, "_send", noop)
    monkeypatch.setattr(driver, "_state", state)
    return driver, opened


async def assert_discarded(driver, opened, account):
    old = opened[0]
    assert old.stale and old.closed and old.session.closed and not old.busy
    assert (await driver.health())["tabs"] == 0
    fresh = await asyncio.wait_for(driver._checkout(account), 1)
    assert fresh is not old
    fresh.stale = True
    await driver._checkin(fresh)


async def test_chat_cancel_event_is_not_success_and_releases_resources(accounts, browser):
    driver, opened = browser
    cancel = asyncio.Event()
    cancel.set()
    stream = Gateway(accounts, driver).chat_stream(ChatRequest("hi", "m", cancel=cancel))
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)
    assert accounts.stats()["inflight"] == 0
    assert accounts.get("a0").success_count == accounts.get("a0").fail_count == 0
    assert len(opened) == 1
    await assert_discarded(driver, opened, accounts.get("a0"))


@pytest.mark.parametrize("phase", ["begin", "stream", "save", "media"])
@pytest.mark.parametrize("failure_kind", ["timeout", "cancel"])
async def test_interrupted_execution_discards_tab(browser, monkeypatch, phase, failure_kind):
    driver, opened = browser
    account = Account(id="a0")
    entered = asyncio.Event()
    failure = UpstreamTimeout("unknown result")

    async def interrupt(*args):
        entered.set()
        if failure_kind == "timeout":
            raise failure
        await asyncio.Future()

    if phase == "begin":
        monkeypatch.setattr(driver, "_send", interrupt)
    elif phase == "stream":
        monkeypatch.setattr(driver, "_state", interrupt)
        # Keep setup successful, then fail in the polling loop.
        original_begin = driver._begin_chat

        async def begin(*args):
            async def base(tab):
                return {}

            monkeypatch.setattr(driver, "_state", base)
            result = await original_begin(*args)
            monkeypatch.setattr(driver, "_state", interrupt)
            return result

        monkeypatch.setattr(driver, "_begin_chat", begin)
    elif phase == "save":

        async def finished_state(tab):
            return {"agentCount": 1, "lastText": "done", "generating": False}

        original_begin = driver._begin_chat

        async def begin(*args):
            result = await original_begin(*args)
            monkeypatch.setattr(driver, "_state", finished_state)
            return result

        monkeypatch.setattr(driver, "_begin_chat", begin)
        monkeypatch.setattr(driver, "_href", interrupt)
    else:
        monkeypatch.setattr(driver, "_wait_media", interrupt)

    async def run():
        if phase == "media":
            await driver.generate_image(account, ImageRequest("draw", "m"))
        else:
            async for _ in driver.chat_stream(account, ChatRequest("hi", "m")):
                pass

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 3)
    if failure_kind == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(UpstreamTimeout) as caught:
            await task
        assert caught.value is failure
    await assert_discarded(driver, opened, account)


async def test_successful_chat_keeps_hot_tab_for_followup(accounts, browser, monkeypatch):
    driver, opened = browser
    sent = []

    async def send(tab, prompt):
        sent.append(prompt)

    async def state(tab):
        return {"agentCount": len(sent), "lastText": "answer" if sent else "", "generating": False}

    async def href(tab):
        return "https://muse.ai/thread/offline"

    async def on_thread(tab):
        return True

    monkeypatch.setattr(driver, "_send", send)
    monkeypatch.setattr(driver, "_state", state)
    monkeypatch.setattr(driver, "_href", href)
    monkeypatch.setattr(driver, "_on_thread", on_thread)
    gateway = Gateway(accounts, driver)
    req = ChatRequest("hi", "m", conversation_hint="owner", turns=[("user", "hi")])
    assert [delta async for delta in gateway.chat_stream(req)] == ["answer"]
    tab = opened[0]
    assert not tab.busy and not tab.stale and not tab.closed
    assert accounts.get("a0").success_count == 1
    followup = ChatRequest(
        "full history",
        "m",
        conversation_hint="owner",
        turns=[("user", "hi"), ("assistant", "answer"), ("user", "next")],
    )
    reused, _ = await driver._begin_chat(accounts.get("a0"), followup)
    assert reused is tab
    assert sent == ["hi", "next"]
    tab.stale = True
    await driver._checkin(tab)


async def test_partial_multi_image_failure_does_not_replay_completed_images(
    accounts, browser, monkeypatch
):
    driver, _ = browser
    calls = []
    failure = UpstreamTimeout("second image result unknown")

    async def generate(account, *args):
        calls.append(account.id)
        if len(calls) == 1:
            return MediaResult(data=b"first", mime="image/png", kind="image")
        raise failure

    monkeypatch.setattr(driver, "_generate", generate)
    with pytest.raises(UpstreamTimeout) as caught:
        await Gateway(accounts, driver).generate_image(ImageRequest("draw", "m", n=3))
    assert caught.value is failure
    assert calls == ["a0", "a0"]
    assert accounts.stats()["inflight"] == 0
