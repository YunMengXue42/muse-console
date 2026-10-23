"""Owned shutdown regressions: no browser, network, profile or real process."""

import asyncio
from types import SimpleNamespace

import pytest

from muse_console.config import Settings
from muse_console.drivers.browser import chromium
from muse_console.drivers.browser.driver import BrowserDriver


@pytest.fixture(autouse=True)
def posix_process_semantics(monkeypatch):
    from muse_console.drivers.browser import driver as module

    monkeypatch.setattr(chromium, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))


class Process:
    def __init__(self, events, *, returncode=None, waits=()):
        self.events = events
        self.returncode = returncode
        self.waits = iter(waits)

    async def wait(self):
        self.events.append("wait")
        result = next(self.waits, 0)
        if result == "timeout":
            raise asyncio.TimeoutError
        self.returncode = result
        return result

    def terminate(self):
        self.events.append("terminate")

    def kill(self):
        self.events.append("kill")


class Browser:
    def __init__(self, events, *, failure=None, closed=False):
        self.events = events
        self.failure = failure
        self.closed = closed

    async def send(self, method):
        self.events.append(method)
        if self.failure:
            raise self.failure

    async def close(self):
        self.events.append("disconnect")
        self.closed = True


def owned(tmp_path, events, **kwargs):
    manager = chromium.ChromiumProcess("fake", 19210, tmp_path / "unused")
    manager.proc = Process(events, **kwargs)
    return manager


async def test_graceful_close_precedes_process_wait_and_disconnect(tmp_path):
    events = []
    manager = owned(tmp_path, events)
    browser = Browser(events)
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    driver._chromium, driver._browser = manager, browser
    await driver.shutdown()
    assert events == ["Browser.close", "wait", "disconnect"]
    assert manager.proc is None
    assert not driver._ctx_lock.locked()


@pytest.mark.parametrize("failure", [RuntimeError("private CDP detail"), asyncio.TimeoutError()])
async def test_close_error_still_allows_normal_exit(tmp_path, failure, caplog):
    events = []
    manager = owned(tmp_path, events)
    await manager.stop(Browser(events, failure=failure))
    assert events == ["Browser.close", "wait"]
    assert manager.proc is None
    assert "private CDP detail" not in caplog.text


async def test_graceful_then_terminate_then_kill_timeouts_are_bounded(tmp_path, monkeypatch):
    events = []
    manager = owned(tmp_path, events, waits=("timeout", "timeout", 0))
    budgets = []
    original = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        budgets.append(timeout)
        return await original(awaitable, timeout)

    monkeypatch.setattr(chromium.asyncio, "wait_for", wait_for)
    await manager.stop(Browser(events))
    assert events == ["Browser.close", "wait", "terminate", "wait", "kill", "wait"]
    assert budgets == [1, 3, 2, 1]
    assert sum(budgets) == 7
    assert manager.proc is None


async def test_hung_close_command_is_cancelled_before_fallback(tmp_path, monkeypatch):
    events = []
    manager = owned(tmp_path, events, waits=("timeout", 0))
    browser = Browser(events)
    cancelled = asyncio.Event()

    async def hung(method):
        events.append(method)
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    browser.send = hung
    original = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        return await original(awaitable, 0.01 if timeout == 1 else timeout)

    monkeypatch.setattr(chromium.asyncio, "wait_for", wait_for)
    await manager.stop(browser)
    assert cancelled.is_set()
    assert events == ["Browser.close", "wait", "terminate", "wait"]


@pytest.mark.parametrize("connection", ["absent", "closed"])
async def test_no_connection_terminates_only_the_owned_process(tmp_path, connection):
    events = []
    manager = owned(tmp_path, events)
    await manager.stop(None if connection == "absent" else Browser(events, closed=True))
    assert events == ["terminate", "wait"]


async def test_unowned_connection_is_never_sent_browser_close(tmp_path):
    events = []
    manager = chromium.ChromiumProcess("fake", 19210, tmp_path / "unused")
    await manager.stop(Browser(events))
    assert events == []
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    driver._browser = Browser(events)
    await driver.shutdown()
    assert events == ["disconnect"]


@pytest.mark.parametrize(
    "platform,code,close", [("linux", 0, False), ("win32", 0, True), ("win32", 1, False)]
)
async def test_launcher_exit_is_not_browser_exit(tmp_path, monkeypatch, platform, code, close):
    events = []
    manager = owned(tmp_path, events, returncode=code)
    browser = Browser(events)
    monkeypatch.setattr(chromium, "sys", SimpleNamespace(platform=platform))

    async def send(method):
        events.append(method)
        browser.closed = True

    browser.send = send
    await manager.stop(browser)
    assert events == (["Browser.close"] if close else [])
    assert manager.proc is None


async def test_windows_launcher_waits_only_for_existing_connection(tmp_path, monkeypatch):
    events = []
    manager = owned(tmp_path, events, returncode=0)
    browser = Browser(events)
    monkeypatch.setattr(chromium, "sys", SimpleNamespace(platform="win32"))
    original = asyncio.wait_for
    budgets = []

    async def wait_for(awaitable, timeout):
        budgets.append(timeout)
        return await original(awaitable, 0.01)

    monkeypatch.setattr(chromium.asyncio, "wait_for", wait_for)
    proc = manager.proc
    with pytest.raises(
        RuntimeError, match="^browser shutdown incomplete \\(code=browser_close_timeout\\)$"
    ):
        await manager.stop(browser)
    assert manager.proc is proc
    assert not browser.closed
    assert events == ["Browser.close"]
    assert budgets == [1, 3]


async def test_repeated_cancellation_finishes_owned_cleanup_before_propagating(tmp_path):
    events = []
    manager = owned(tmp_path, events)
    entered, release = asyncio.Event(), asyncio.Event()
    browser = Browser(events)

    async def send(method):
        events.append(method)
        entered.set()
        await release.wait()

    browser.send = send
    task = asyncio.create_task(manager.stop(browser))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.proc is None
    assert events == ["Browser.close", "wait"]


async def test_context_failure_still_closes_browser_and_disconnects(tmp_path):
    events = []
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    driver._chromium = owned(tmp_path, events)
    driver._browser = Browser(events)
    driver._contexts["account"] = "context"

    async def fail(_):
        events.append("context")
        raise RuntimeError("cleanup failed")

    driver._dispose_context = fail
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await driver.shutdown()
    assert events == ["context", "Browser.close", "wait", "disconnect"]
    assert not driver._contexts and not driver._ctx_lock.locked()


async def test_context_timeout_preserves_time_for_browser_close(tmp_path):
    events = []
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    driver._chromium = owned(tmp_path, events)
    driver._browser = Browser(events)
    driver._contexts["account"] = "context"

    async def timeout(_):
        raise asyncio.TimeoutError

    driver._dispose_context = timeout
    await driver.shutdown()
    assert events == ["Browser.close", "wait", "disconnect"]


async def test_unreaped_killed_process_retains_ownership(tmp_path):
    events = []
    manager = owned(tmp_path, events, waits=("timeout", "timeout"))
    proc = manager.proc
    with pytest.raises(asyncio.TimeoutError):
        await manager.stop()
    assert manager.proc is proc
    assert events == ["terminate", "wait", "kill", "wait"]


async def test_windows_launcher_exits_during_graceful_wait(tmp_path, monkeypatch):
    events = []
    manager = owned(tmp_path, events)
    browser = Browser(events)
    monkeypatch.setattr(chromium, "sys", SimpleNamespace(platform="win32"))
    original = asyncio.wait_for
    budgets = []

    async def wait_for(awaitable, timeout):
        budgets.append(timeout)
        return await original(awaitable, 0.01)

    monkeypatch.setattr(chromium.asyncio, "wait_for", wait_for)
    proc = manager.proc
    with pytest.raises(
        RuntimeError, match="^browser shutdown incomplete \\(code=browser_close_timeout\\)$"
    ):
        await manager.stop(browser)
    assert manager.proc is proc
    assert not browser.closed
    assert events == ["Browser.close", "wait"]
    assert budgets == [1, 3]


async def test_driver_cancellation_keeps_close_and_disconnect_under_lock(tmp_path):
    events = []
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    driver._chromium = owned(tmp_path, events)
    driver._browser = Browser(events)
    entered, release = asyncio.Event(), asyncio.Event()

    async def send(method):
        events.append(method)
        entered.set()
        await release.wait()

    driver._browser.send = send
    task = asyncio.create_task(driver.shutdown())
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert driver._ctx_lock.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["Browser.close", "wait", "disconnect"]
    assert not driver._ctx_lock.locked()
    assert driver._chromium.proc is None


async def test_context_cleanup_hang_is_bounded(tmp_path, monkeypatch):
    events = []
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    driver._chromium = owned(tmp_path, events)
    driver._browser = Browser(events)
    driver._contexts["account"] = "context"
    cancelled = asyncio.Event()

    async def hang(_):
        events.append("context")
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    driver._dispose_context = hang
    from muse_console.drivers.browser import driver as module

    original = asyncio.wait_for
    budgets = []

    async def wait_for(awaitable, timeout):
        budgets.append(timeout)
        return await original(awaitable, 0.01)

    # Simulate 3.10: using the newer context manager would fail immediately.
    monkeypatch.delattr(module.asyncio, "timeout", raising=False)
    monkeypatch.setattr(module.asyncio, "wait_for", wait_for)
    await driver.shutdown()
    assert budgets[0] == 1
    assert cancelled.is_set()
    assert events == ["context", "Browser.close", "wait", "disconnect"]


@pytest.mark.parametrize("launcher_exited", [True, False])
async def test_driver_retains_windows_ownership_when_cdp_stays_alive(
    tmp_path, monkeypatch, launcher_exited, caplog
):
    from muse_console.drivers.browser import driver as module

    events = []
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    manager = owned(tmp_path, events, returncode=0 if launcher_exited else None)
    proc = manager.proc
    browser = Browser(events, failure=RuntimeError("PRIVATE_CDP_SENTINEL"))
    driver._chromium, driver._browser = manager, browser
    monkeypatch.setattr(chromium, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="win32"))
    original = asyncio.wait_for

    async def wait_for(awaitable, timeout):
        return await original(awaitable, 0.01)

    monkeypatch.setattr(chromium.asyncio, "wait_for", wait_for)
    with pytest.raises(RuntimeError) as error:
        await driver.shutdown()
    assert str(error.value) == "browser shutdown incomplete (code=browser_close_timeout)"
    assert manager.proc is proc
    assert driver._browser is browser and not browser.closed
    assert "disconnect" not in events
    assert "terminate" not in events and "kill" not in events
    assert not driver._ctx_lock.locked()
    assert "PRIVATE_CDP_SENTINEL" not in caplog.text

    # Retry uses only the retained connection, then releases ownership on close.
    async def close(method):
        events.append(method)
        browser.closed = True

    browser.send = close
    await driver.shutdown()
    assert manager.proc is None
    assert events[-2:] == ["Browser.close", "disconnect"]
