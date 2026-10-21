"""Offline recovery and reply-completion regression tests."""

import asyncio
from types import SimpleNamespace

import pytest

from muse_console.accounts.model import Account
from muse_console.drivers.base import ChatRequest
from muse_console.drivers.browser.cdp import CDPError, CDPSession
from muse_console.drivers.browser.driver import BrowserDriver, _Tab
from muse_console.errors import UpstreamError, UpstreamTimeout


class Session:
    closed = False

    async def close(self):
        self.closed = True


async def test_generating_pause_is_not_final(monkeypatch, caplog):
    caplog.set_level("INFO", logger="muse_console.drivers.browser.driver")
    driver = BrowserDriver(SimpleNamespace())
    account = Account(id="a", cookies={})
    tab = _Tab("a", "ctx", "target", Session(), busy=True)
    polls = 0

    async def begin(*_):
        return tab, {}

    async def state(_):
        nonlocal polls
        polls += 1
        return {
            "agentCount": 1,
            "lastText": "First part" if polls < 25 else "First part, then the rest.",
            "generating": polls < 30,
        }

    async def href(_):
        return "https://muse.ai/thread/offline"

    real_sleep = asyncio.sleep
    monkeypatch.setattr(
        "muse_console.drivers.browser.driver.asyncio.sleep", lambda _: real_sleep(0)
    )
    monkeypatch.setattr(driver, "_begin_chat", begin)
    monkeypatch.setattr(driver, "_state", state)
    monkeypatch.setattr(driver, "_href", href)
    result = "".join(
        [
            chunk
            async for chunk in driver.chat_stream(
                account, ChatRequest(model="muse-chat", prompt="x")
            )
        ]
    )
    assert result == "First part, then the rest."
    assert polls >= 33 and not tab.stale
    assert "code=hot_page_saved" in caplog.text
    assert "https://muse.ai/thread/offline" not in caplog.text
    assert "First part" not in caplog.text


async def test_stuck_generating_times_out_and_retires(monkeypatch):
    driver = BrowserDriver(SimpleNamespace())
    tab = _Tab("a", "ctx", "target", Session(), busy=True)

    async def begin(*_):
        return tab, {}

    monkeypatch.setattr(driver, "_begin_chat", begin)
    with pytest.raises(UpstreamTimeout):
        _ = [
            part
            async for part in driver.chat_stream(
                Account(id="a"), ChatRequest(model="muse-chat", prompt="x", timeout=-1)
            )
        ]
    assert tab.closed and tab.session.closed


async def test_reconnect_is_serialized_and_uses_only_owned_endpoint(monkeypatch):
    driver = BrowserDriver(SimpleNamespace(account_max_concurrency=1))
    driver._browser = Session()
    driver._browser.closed = True
    driver._browser_ws_url = "ws://127.0.0.1:123/devtools/browser/owned-id"
    old = _Tab("old", "old-context", "old-target", Session(), busy=True)
    driver._tabs["old"] = [old]
    calls = []

    async def connect(url):
        calls.append(url)
        await asyncio.sleep(0)
        return Session()

    async def open_tab(account):
        return _Tab(account.id, "fresh", account.id, Session())

    monkeypatch.setattr(CDPSession, "connect", connect)
    monkeypatch.setattr(driver, "_open_tab", open_tab)
    first, second = await asyncio.gather(
        driver._checkout(Account(id="a")), driver._checkout(Account(id="b"))
    )
    assert calls == [driver._browser_ws_url]
    assert old.closed and old.stale
    assert first.busy and second.busy
    await driver._checkin(old)  # old request cleanup must not close fresh tabs
    assert not first.closed and not second.closed


async def test_failed_recovery_has_backoff_and_no_submission(monkeypatch):
    driver = BrowserDriver(SimpleNamespace(account_max_concurrency=1))
    driver._browser = Session()
    driver._browser.closed = True
    driver._browser_ws_url = "ws://127.0.0.1:123/devtools/browser/owned-id"
    calls = []

    async def fail(url):
        calls.append(url)
        raise CDPError("offline")

    monkeypatch.setattr(CDPSession, "connect", fail)
    for _ in range(2):
        with pytest.raises(UpstreamError):
            await driver._checkout(Account(id="a"))
    assert len(calls) == 1 and not driver._tabs


async def test_active_media_disconnect_is_not_replayed(monkeypatch):
    driver = BrowserDriver(SimpleNamespace(account_max_concurrency=1))
    tab = _Tab("a", "ctx", "target", Session(), busy=True)
    submissions = []

    async def checkout(*_, **__):
        return tab

    async def prepare(*_):
        submissions.append(1)
        raise CDPError("lost after submission")

    monkeypatch.setattr(driver, "_checkout", checkout)
    monkeypatch.setattr(driver, "_prepare", prepare)
    with pytest.raises(UpstreamError):
        await driver._generate(Account(id="a"), "x", [], "image", 10, None, None)
    assert submissions == [1] and tab.closed


def _retry_driver(monkeypatch, outcomes, **settings):
    from muse_console.errors import UpstreamRefused

    driver = BrowserDriver(SimpleNamespace(account_max_concurrency=1, **settings))
    tab = _Tab("a", "ctx", "target", Session(), busy=True)
    submissions = []

    async def checkout(*_, **__):
        return tab

    async def noop(*_, **__):
        return None

    async def prepare(*_):
        submissions.append(1)
        return {}

    async def wait_media(*_):
        if outcomes.pop(0) == "text":
            raise UpstreamRefused("upstream replied with text only (code=media_missing)")
        return {"src": "blob:x", "kind": "video"}

    async def download(*_):
        return "media"

    monkeypatch.setattr(driver, "_checkout", checkout)
    monkeypatch.setattr(driver, "_fresh_target", noop)
    monkeypatch.setattr(driver, "_prepare", prepare)
    monkeypatch.setattr(driver, "_wait_media", wait_media)
    monkeypatch.setattr(driver, "_download", download)
    monkeypatch.setattr(driver, "_checkin", lambda _tab: None)
    monkeypatch.setattr(driver, "_finish_cleanup", noop)
    return driver, submissions


async def test_video_text_only_reply_is_resubmitted_once(monkeypatch):
    driver, submissions = _retry_driver(monkeypatch, ["text", "ok"])
    result = await driver._generate(Account(id="a"), "x", [], "video", 600, None, None)
    assert result == "media" and submissions == [1, 1]


async def test_video_retry_is_bounded_and_image_never_retries(monkeypatch):
    from muse_console.errors import UpstreamRefused

    driver, submissions = _retry_driver(monkeypatch, ["text", "text", "ok"])
    with pytest.raises(UpstreamRefused):
        await driver._generate(Account(id="a"), "x", [], "video", 600, None, None)
    assert submissions == [1, 1]

    driver, submissions = _retry_driver(monkeypatch, ["text", "ok"])
    with pytest.raises(UpstreamRefused):
        await driver._generate(Account(id="a"), "x", [], "image", 600, None, None)
    assert submissions == [1]

    driver, submissions = _retry_driver(monkeypatch, ["text", "ok"], video_deadline_cap=50.0)
    with pytest.raises(UpstreamRefused):
        await driver._generate(Account(id="a"), "x", [], "video", 600, None, None)
    assert submissions == [1]


@pytest.mark.parametrize("link_found", [True, False])
async def test_media_logs_exclude_reply_html_and_links(monkeypatch, caplog, link_found):
    from muse_console.drivers.browser import dom
    from muse_console.errors import UpstreamRefused

    caplog.set_level("INFO", logger="muse_console.drivers.browser.driver")
    secret = "PRIVATE_REPLY_SENTINEL"
    url = "https://example.invalid/media/result.png?signature=PRIVATE_LINK_SENTINEL"
    state = {"agentCount": 1, "lastText": secret, "generating": False}

    class MediaSession(Session):
        async def evaluate(self, expression, **kwargs):
            if expression == dom.CHAT_STATE:
                return dict(state)
            if expression == dom.LAST_BUBBLE_MEDIA:
                return {"links": [url] if link_found else [], "html": secret + url}
            return {}

    driver = BrowserDriver(SimpleNamespace())
    driver._MEDIA_GRACE = {"image": 0}
    driver._ASK_GRACE = {"image": 0}
    tab = _Tab("a", "ctx", "target", MediaSession(), thread_url=url)
    sent = []

    async def send(*_):
        sent.append(1)
        state["agentCount"] += 1

    real_sleep = asyncio.sleep
    monkeypatch.setattr(
        "muse_console.drivers.browser.driver.asyncio.sleep", lambda _: real_sleep(0)
    )
    monkeypatch.setattr(driver, "_send", send)
    if link_found:
        assert (await driver._wait_media(tab, {}, "image", 1, None, None))["src"] == url
        assert "code=media_link_detected" in caplog.text
    else:
        with pytest.raises(UpstreamRefused) as caught:
            await driver._wait_media(tab, {}, "image", 1, None, None)
        assert secret not in str(caught.value) and url not in str(caught.value)
        assert "code=media_missing" in caplog.text
    assert sent == [1]
    assert secret not in caplog.text and url not in caplog.text
    assert "PRIVATE_LINK_SENTINEL" not in caplog.text
