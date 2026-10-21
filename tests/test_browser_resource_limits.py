"""Resource budgets, idle expiry and bounded media transfer without browsers."""

import asyncio
import base64
from types import SimpleNamespace

import pytest

from muse_console.accounts.model import Account
from muse_console.drivers.browser import dom
from muse_console.drivers.browser.cdp import CDPSession
from muse_console.drivers.browser.driver import BrowserDriver, _Tab
from muse_console.errors import CapacityExceeded, UpstreamError


class Session:
    closed = False

    async def close(self):
        self.closed = True


@pytest.fixture
def driver(monkeypatch):
    driver = BrowserDriver(
        SimpleNamespace(account_max_concurrency=2, browser_max_tabs=2, browser_idle_ttl=900)
    )

    async def open_tab(account):
        driver._contexts[account.id] = account.id
        return _Tab(account.id, account.id, account.id, Session())

    monkeypatch.setattr(driver, "_open_tab", open_tab)
    return driver


async def test_global_budget_rejects_without_account_failure_and_evicts_lru(driver):
    a = await driver._checkout(Account(id="a"))
    b = await driver._checkout(Account(id="b"))
    with pytest.raises(CapacityExceeded) as caught:
        await driver._checkout(Account(id="c"))
    assert not isinstance(caught.value, UpstreamError)
    assert caught.value.status_code == 429
    assert (await driver.health())["busy_tabs"] == 2
    assert "c" not in driver._tabs and "c" not in driver._fingerprints
    await driver._checkin(a)
    c = await driver._checkout(Account(id="c"))
    assert a.closed and not b.closed and c.busy
    assert len(driver._contexts) == 2
    assert "a" not in driver._fingerprints and "a" not in driver._tabs


async def test_idle_expiry_preserves_active_and_cleans_contexts(driver):
    idle = await driver._checkout(Account(id="idle"))
    active = await driver._checkout(Account(id="active"))
    await driver._checkin(idle)
    idle.idle_since -= 1000
    active.idle_since -= 1000
    async with driver._ctx_lock:
        await driver._prune_idle_locked()
    assert idle.closed and not active.closed
    assert "idle" not in driver._contexts
    assert "idle" not in driver._fingerprints
    assert "idle" not in driver._tabs


async def test_background_reaper_expires_without_new_requests(driver):
    driver.settings.browser_idle_ttl = 0
    idle = await driver._checkout(Account(id="idle"))
    await driver._checkin(idle)
    reaper = asyncio.create_task(driver._reap_idle())
    try:
        for _ in range(20):
            if idle.closed:
                break
            await asyncio.sleep(0.02)
        assert idle.closed
    finally:
        reaper.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reaper


async def test_cancelled_open_does_not_leak_global_slot(driver, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = driver._open_tab
    opened = []

    async def delayed(account):
        entered.set()
        await release.wait()
        tab = await original(account)
        opened.append(tab)
        return tab

    monkeypatch.setattr(driver, "_open_tab", delayed)
    task = asyncio.create_task(driver._checkout(Account(id="a")))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert opened[0].closed
    assert not driver._contexts and not driver._tabs and not driver._fingerprints


async def test_concurrent_checkouts_never_exceed_budget(driver):
    tabs = await asyncio.gather(*(driver._checkout(Account(id=str(i))) for i in range(2)))
    with pytest.raises(CapacityExceeded):
        await driver._checkout(Account(id="third"))
    await driver._checkin(tabs[0])
    third = await driver._checkout(Account(id="third"))
    assert third.busy and (await driver.health())["tabs"] == 2


async def test_media_byte_contract_and_decode_guard(driver, caplog):
    caplog.set_level("INFO", logger="muse_console.drivers.browser.driver")
    driver.settings.browser_media_max_bytes = 3
    calls = []

    class MediaSession(Session):
        async def evaluate(self, expression, **kwargs):
            calls.append(expression)
            return {"ok": True, "b64": base64.b64encode(b"abc").decode()}

    tab = _Tab("a", "ctx", "target", MediaSession())
    result = await driver._download(tab, {"src": "blob:https://muse.ai/offline-test"}, "image")
    assert result.data == b"abc" and calls == [
        driver._blob_download_script("blob:https://muse.ai/offline-test", 3)
    ]
    assert "code=media_downloaded bytes=3" in caplog.text
    assert "blob:https://muse.ai/offline-test" not in caplog.text
    assert "YWJj" not in caplog.text
    driver.settings.browser_media_max_bytes = 2
    with pytest.raises(UpstreamError, match="media_download_failed"):
        await driver._download(tab, {"src": "blob:https://muse.ai/offline-test"}, "image")


def test_streaming_fetch_script_checks_before_buffering_and_never_uses_blob():
    script = dom.fetch_as_base64('blob:https://muse.ai/offline-test"quoted', 17)
    assert 'fetch("blob:https://muse.ai/offline-test\\"quoted"' in script
    assert "const limit = 17;" in script
    assert script.index("if (total > limit)") < script.index("chunks.push(value)")
    assert "reader.cancel()" in script and "controller.abort()" in script
    assert ".blob()" not in script and ".arrayBuffer()" not in script


@pytest.mark.parametrize("kind", ["image", "video"])
async def test_http_media_uses_public_downloader_without_browser_credentials(
    driver, monkeypatch, kind
):
    calls = []

    async def download(url, **kwargs):
        calls.append((url, kwargs))
        return b"public", f"{kind}/test"

    monkeypatch.setattr("muse_console.drivers.browser.driver.download_public_media", download)
    # No evaluate method: a regression to browser fetch must fail this test.
    tab = _Tab("a", "ctx", "target", Session())
    url = "https://muse.ai/media/authenticated-or-public"
    result = await driver._download(tab, {"src": url}, kind)
    assert result.data == b"public"
    assert calls == [(url, {"timeout": 20, "max_bytes": 33554432, "kind": kind})]


@pytest.mark.parametrize(
    "url",
    [
        "blob:http://muse.ai/id",
        "blob:https://evil.test/id",
        "blob:null/id",
        "blob:https://muse.ai.evil.test/id",
        "blob:https://muse.ai:443/id",
        "blob:https://user@muse.ai/id",
        "blob:https://muse.ai/id#fragment",
        "blob:https://muse.ai/id?query",
        "blob:https://muse.ai/id/extra",
        "file:///private.png",
        "data:image/png;base64,eA==",
        "javascript:fetch('x')",
        "https://user:secret@muse.ai/file.png",
        "https://muse.ai/file.png#fragment",
    ],
)
async def test_untrusted_sources_never_reach_either_fetch(driver, monkeypatch, url):
    async def forbidden(*args, **kwargs):
        pytest.fail("untrusted source reached downloader")

    monkeypatch.setattr("muse_console.drivers.browser.driver.download_public_media", forbidden)
    with pytest.raises(UpstreamError) as caught:
        await driver._download(_Tab("a", "ctx", "target", Session()), {"src": url}, "image")
    assert caught.value.retryable is False
    assert url not in str(caught.value)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/media/x", "https://public.test/file.png", "https://muse.ai/media/x"]
)
async def test_public_downloader_failure_is_redacted_nonretryable_and_never_falls_back(
    driver, monkeypatch, url
):
    calls = []

    async def rejected(source, **kwargs):
        calls.append(source)
        raise ValueError("private DNS, redirect, or authentication failure: " + source)

    monkeypatch.setattr("muse_console.drivers.browser.driver.download_public_media", rejected)
    with pytest.raises(UpstreamError) as caught:
        await driver._download(_Tab("a", "ctx", "target", Session()), {"src": url}, "image")
    assert caught.value.retryable is False
    assert calls == [url] and url not in str(caught.value)


async def test_rejected_output_is_not_replayed_and_tab_is_retired(driver, monkeypatch):
    submissions = []
    downloads = []

    async def prepare(*args):
        submissions.append(1)
        return {}

    async def wait_media(*args):
        return {"src": "http://127.0.0.1/private.png"}

    async def rejected(url, **kwargs):
        downloads.append(url)
        raise ValueError("private destination")

    monkeypatch.setattr(driver, "_prepare", prepare)
    monkeypatch.setattr(driver, "_wait_media", wait_media)
    monkeypatch.setattr("muse_console.drivers.browser.driver.download_public_media", rejected)
    with pytest.raises(UpstreamError) as caught:
        await driver._generate(Account(id="a"), "offline", [], "image", 10, None, None)
    assert caught.value.retryable is False
    assert submissions == [1] and len(downloads) == 1
    assert not driver._tabs and not driver._contexts


def test_blob_script_enforces_origin_and_credentialless_no_redirect_fetch():
    script = BrowserDriver._blob_download_script("blob:https://muse.ai/offline-test", 17)
    assert "credentials: 'omit'" in script and "redirect: 'error'" in script
    assert "url !== source" in script and "untrusted media blob" in script
    assert "const limit = 17;" in script


async def test_cdp_transport_is_bounded_and_has_liveness_ping(monkeypatch):
    captured = {}

    async def connect(url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("muse_console.drivers.browser.cdp.connect", connect)
    monkeypatch.setattr(CDPSession, "__init__", lambda self, ws: None)
    await CDPSession.connect("ws://offline")
    assert captured["max_size"] == 48 * 1024 * 1024
    assert captured["ping_interval"] and captured["ping_timeout"]
