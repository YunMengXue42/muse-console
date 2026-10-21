"""Pre-submission target isolation, entirely fake CDP and in-memory state."""

import asyncio
from types import SimpleNamespace

import pytest

from muse_console.accounts.model import Account
from muse_console.drivers.base import ChatRequest, ImageRequest, MediaResult, VideoRequest
from muse_console.drivers.browser.cdp import CDPError, CDPSession
from muse_console.drivers.browser.driver import BrowserDriver
from muse_console.errors import UpstreamError
from muse_console.upstream import muse


class Session:
    closed = False

    async def send(self, *args, **kwargs):
        return {}

    async def close(self):
        self.closed = True


class Browser(Session):
    def __init__(self):
        self.calls = []
        self.targets = set()
        self.counter = 0
        self.peak = 0

    async def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Target.createBrowserContext":
            return {"browserContextId": "ctx"}
        if method == "Target.createTarget":
            self.counter += 1
            target = f"target-{self.counter}"
            self.targets.add(target)
            self.peak = max(self.peak, len(self.targets))
            return {"targetId": target}
        if method == "Target.closeTarget":
            self.targets.discard(params["targetId"])
        return {}


@pytest.fixture
def rig(monkeypatch):
    driver = BrowserDriver(
        SimpleNamespace(account_max_concurrency=1, browser_max_tabs=1, browser_idle_ttl=900)
    )
    browser = Browser()
    driver._browser = browser
    driver._chromium = SimpleNamespace(port=1)
    account = Account(id="offline", cookies={name: "fake" for name in muse.SESSION_COOKIES})
    sends = []

    async def connect(*args, **kwargs):
        return Session()

    async def noop(*args, **kwargs):
        pass

    async def state(*args):
        return {}

    async def send(tab, text):
        assert tab.used and tab.busy
        sends.append((tab.target_id, text))

    monkeypatch.setattr(CDPSession, "connect", connect)
    monkeypatch.setattr(driver, "_new_thread", noop)
    monkeypatch.setattr(driver, "_attach", noop)
    monkeypatch.setattr(driver, "_state", state)
    monkeypatch.setattr(driver, "_send", send)
    return driver, account, browser, sends


def request(turns=None):
    return ChatRequest(model="muse-chat", prompt="same", turns=turns or [("user", "same")])


async def test_independent_same_prompt_replaces_at_limit_in_same_context(rig):
    driver, account, browser, sends = rig
    tab, _ = await driver._begin_chat(account, request())
    old_session = tab.session
    tab.turns = [("user", "same"), ("assistant", "answer")]
    await driver._checkin(tab)
    again, _ = await driver._begin_chat(account, request())
    assert again is tab and again.context_id == "ctx"
    assert old_session.closed and len(browser.targets) == browser.peak == 1
    assert sends == [("target-1", "same"), ("target-2", "same")]
    assert not any(m == "Target.disposeBrowserContext" for m, _ in browser.calls)
    assert driver._tabs[account.id] == [tab] and tab.busy


async def test_true_followup_keeps_hot_target(rig, monkeypatch):
    driver, account, browser, sends = rig
    tab, _ = await driver._begin_chat(account, request())
    tab.turns = [("user", "same"), ("assistant", "answer")]
    tab.thread_url = "https://muse.ai/thread/saved"
    await driver._checkin(tab)

    async def on_thread(_):
        return True

    monkeypatch.setattr(driver, "_on_thread", on_thread)
    again, _ = await driver._begin_chat(account, request(tab.turns + [("user", "followup")]))
    assert again is tab and browser.counter == 1
    assert sends[-1] == ("target-1", "followup")


async def test_media_n_and_image_to_video_replace_historyless_targets(rig, monkeypatch):
    driver, account, browser, sends = rig

    async def wait(*args):
        return {}

    async def download(tab, att, kind):
        return MediaResult(data=b"offline", mime="fake", kind=kind)

    monkeypatch.setattr(driver, "_wait_media", wait)
    monkeypatch.setattr(driver, "_download", download)
    results = await driver.generate_image(account, ImageRequest(model="image", prompt="x", n=2))
    tab = driver._tabs[account.id][0]
    assert len(results) == 2 and not tab.has_state and tab.used
    await driver.generate_video(account, VideoRequest(model="video", prompt="x"))
    assert [t for t, _ in sends] == ["target-1", "target-2", "target-3"]
    assert browser.peak == 1 and not tab.busy


async def test_replacement_does_not_close_other_hot_or_busy_tabs(rig):
    driver, account, browser, _ = rig
    driver.settings.account_max_concurrency = 2
    driver.settings.browser_max_tabs = 2
    first = await driver._checkout(account)
    second = await driver._checkout(account)
    first.used = True
    second.used = True
    second.turns = [("user", "keep")]
    await driver._fresh_target(first, account)
    assert second.target_id in browser.targets and not second.session.closed and second.busy
    assert second.turns == [("user", "keep")]
    assert browser.peak == 2 and driver._contexts[account.id] == second.context_id


@pytest.mark.parametrize("media", [False, True])
async def test_submit_failure_is_not_replayed_and_retires_target(rig, monkeypatch, media):
    driver, account, browser, _ = rig
    calls = []

    async def fail(tab, text):
        assert tab.used
        calls.append(tab.target_id)
        raise CDPError("lost after click")

    monkeypatch.setattr(driver, "_send", fail)
    with pytest.raises(UpstreamError):
        if media:
            await driver.generate_image(account, ImageRequest(model="image", prompt="x"))
        else:
            await driver._begin_chat(account, request())
    assert calls == ["target-1"] and not browser.targets
    assert not driver._tabs.get(account.id)


async def test_failed_replacement_open_cleans_partial_target_and_reservation(rig, monkeypatch):
    driver, account, browser, sends = rig
    tab, _ = await driver._begin_chat(account, request())
    await driver._checkin(tab)

    async def fail(*args, **kwargs):
        raise CDPError("cannot connect replacement")

    monkeypatch.setattr(CDPSession, "connect", fail)
    with pytest.raises(UpstreamError):
        await driver._begin_chat(account, request())
    assert len(sends) == 1 and not browser.targets and tab.closed
    assert not driver._tabs.get(account.id) and not driver._contexts
    assert not driver._ctx_lock.locked()


@pytest.mark.parametrize("media", [False, True])
async def test_cancel_during_replacement_finishes_transaction_then_retires(rig, monkeypatch, media):
    driver, account, browser, sends = rig
    tab, _ = await driver._begin_chat(account, request())
    await driver._checkin(tab)
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect(*args, **kwargs):
        entered.set()
        await release.wait()
        return Session()

    monkeypatch.setattr(CDPSession, "connect", connect)
    operation = (
        driver.generate_image(account, ImageRequest(model="image", prompt="x"))
        if media
        else driver._begin_chat(account, request())
    )
    task = asyncio.create_task(operation)
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    assert tab.busy and driver._ctx_lock.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(sends) == 1 and not browser.targets and tab.closed
    assert not driver._tabs.get(account.id) and not driver._ctx_lock.locked()


async def test_cancel_waiting_for_lifecycle_lock_does_not_leak_slot(rig, monkeypatch):
    driver, account, browser, sends = rig
    tab, _ = await driver._begin_chat(account, request())
    await driver._checkin(tab)
    entered = asyncio.Event()
    original = driver._fresh_target

    async def blocked(tab, account):
        await driver._ctx_lock.acquire()
        entered.set()
        await original(tab, account)

    monkeypatch.setattr(driver, "_fresh_target", blocked)
    task = asyncio.create_task(driver._begin_chat(account, request()))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    driver._ctx_lock.release()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tab.closed and not tab.busy and not browser.targets
    assert browser.counter == 1 and len(sends) == 1
    assert not driver._ctx_lock.locked()
