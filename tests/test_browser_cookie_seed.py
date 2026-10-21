"""Context credentials are seeded once; target creation preserves rotated cookies."""

import asyncio
from types import SimpleNamespace

import pytest

from muse_console.accounts.credentials import CookieRecord
from muse_console.accounts.model import Account
from muse_console.drivers.browser.cdp import CDPError, CDPSession
from muse_console.drivers.browser.driver import BrowserDriver
from muse_console.upstream import muse


class Browser:
    closed = False

    def __init__(self):
        self.contexts = {}
        self.targets = {}
        self.seeds = []
        self.calls = []
        self.counter = 0
        self.fail = None
        self.block_seed = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Target.createBrowserContext":
            self.counter += 1
            ctx = f"context-{self.counter}"
            self.contexts[ctx] = {}
            return {"browserContextId": ctx}
        if method == "Target.createTarget":
            self.counter += 1
            target = f"target-{self.counter}"
            self.targets[target] = params["browserContextId"]
            return {"targetId": target}
        if method == "Target.closeTarget":
            self.targets.pop(params["targetId"], None)
        if method == "Target.disposeBrowserContext":
            self.contexts.pop(params["browserContextId"], None)
        return {}


class Page:
    closed = False

    def __init__(self, browser, target):
        self.browser, self.target = browser, target

    async def send(self, method, params=None):
        browser = self.browser
        if method == browser.fail:
            raise CDPError("scripted initialization failure")
        if method == "Network.setCookie":
            ctx = browser.targets[self.target]
            key = (params["name"], params.get("domain", params.get("url")), params["path"])
            browser.contexts[ctx][key] = params["value"]
            browser.seeds.append((ctx, key))
            if browser.block_seed:
                browser.entered.set()
                await browser.release.wait()
        return {"success": True}

    async def close(self):
        self.closed = True


@pytest.fixture(params=[False, True], ids=["legacy", "records"])
def rig(request, monkeypatch):
    browser = Browser()
    driver = BrowserDriver(
        SimpleNamespace(account_max_concurrency=2, browser_max_tabs=2, browser_idle_ttl=900)
    )
    driver._browser = browser
    driver._chromium = SimpleNamespace(port=1)
    cookies = {name: "original" for name in muse.SESSION_COOKIES}
    account = Account(
        id="offline",
        cookies=cookies,
        credential_records=(
            [
                CookieRecord(name=name, value=value, domain=".muse.ai")
                for name, value in cookies.items()
            ]
            if request.param
            else []
        ),
    )

    async def connect(url, **kwargs):
        return Page(browser, url.rsplit("/", 1)[-1])

    monkeypatch.setattr(CDPSession, "connect", connect)
    return driver, account, browser


async def test_second_target_and_fresh_replacement_preserve_rotation(rig):
    driver, account, browser = rig
    first = await driver._checkout(account)
    jar = browser.contexts[first.context_id]
    assert jar
    for key in jar:
        jar[key] = "rotated"
    seeds = list(browser.seeds)
    second = await driver._checkout(account)
    assert second.context_id == first.context_id
    first.used = True
    await driver._fresh_target(first, account)
    assert browser.seeds == seeds
    assert set(jar.values()) == {"rotated"}
    assert not second.closed and second.busy


async def test_concurrent_checkouts_seed_only_once(rig):
    driver, account, browser = rig
    first, second = await asyncio.gather(driver._checkout(account), driver._checkout(account))
    assert first.context_id == second.context_id
    assert len(browser.seeds) == len(account.cookies)


async def test_changed_fingerprint_seeds_new_context_without_touching_busy_old(rig):
    driver, account, browser = rig
    old = await driver._checkout(account)
    old_jar = browser.contexts[old.context_id]
    for key in old_jar:
        old_jar[key] = "rotated"
    account.credential_version += 1
    fresh = await driver._checkout(account)
    assert fresh.context_id != old.context_id and old.stale and not old.closed
    assert set(old_jar.values()) == {"rotated"}
    assert set(browser.contexts[fresh.context_id].values()) == {"original"}
    await driver._checkin(old)
    assert old.context_id not in browser.contexts
    assert driver._contexts[account.id] == fresh.context_id


@pytest.mark.parametrize("failure", ["Page.enable", "Network.setCookie"])
async def test_new_context_failure_rolls_back_and_next_attempt_seeds(rig, failure):
    driver, account, browser = rig
    browser.fail = failure
    with pytest.raises(CDPError):
        await driver._checkout(account)
    assert not browser.targets and not browser.contexts and not driver._contexts
    assert not driver._ctx_lock.locked()
    browser.fail = None
    tab = await driver._checkout(account)
    assert set(browser.contexts[tab.context_id].values()) == {"original"}


async def test_existing_context_open_failure_preserves_cookie_jar_and_other_tab(rig):
    driver, account, browser = rig
    old = await driver._checkout(account)
    jar = browser.contexts[old.context_id]
    for key in jar:
        jar[key] = "rotated"
    seeds = list(browser.seeds)
    browser.fail = "Runtime.enable"
    with pytest.raises(CDPError):
        await driver._checkout(account)
    assert browser.targets == {old.target_id: old.context_id}
    assert driver._contexts[account.id] == old.context_id
    assert set(jar.values()) == {"rotated"} and browser.seeds == seeds
    assert old.busy and not old.session.closed


async def test_cancelled_initial_seed_discards_new_context_without_leaking(rig):
    driver, account, browser = rig
    browser.block_seed = True
    task = asyncio.create_task(driver._checkout(account))
    await browser.entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    browser.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not browser.targets and not browser.contexts and not driver._contexts
    assert not driver._tabs.get(account.id) and not driver._ctx_lock.locked()
    browser.block_seed = False
    fresh = await driver._checkout(account)
    assert set(browser.contexts[fresh.context_id].values()) == {"original"}
