"""browser ownership and credential lifecycle tests without a real browser."""

from __future__ import annotations

import asyncio
import itertools
import socket
from types import SimpleNamespace

import pytest

from muse_console.accounts.model import Account
from muse_console.config import Settings
from muse_console.drivers.browser.cdp import CDPError, CDPSession
from muse_console.drivers.browser.chromium import ChromiumProcess, find_chromium
from muse_console.drivers.browser.driver import BrowserDriver
from muse_console.upstream import muse


class FakeBrowser:
    closed = False

    def __init__(self):
        self.calls = []
        self.ids = itertools.count()

    async def send(self, method, params=None):
        self.calls.append((method, params))
        if method == "Target.createBrowserContext":
            return {"browserContextId": f"ctx-{next(self.ids)}"}
        if method == "Target.createTarget":
            return {"targetId": f"tab-{next(self.ids)}"}
        return {}

    async def close(self):
        self.closed = True


class FakeSession:
    closed = False

    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    async def send(self, method, params=None):
        self.calls.append((method, params))
        if method == self.failure:
            raise CDPError("scripted initialization failure")
        return {}

    async def close(self):
        self.closed = True


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    driver = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path, account_max_concurrency=2))
    driver._browser = FakeBrowser()
    driver._chromium = SimpleNamespace(port=19210)
    sessions = []

    async def connect(_url):
        session = FakeSession()
        sessions.append(session)
        return session

    monkeypatch.setattr(CDPSession, "connect", connect)
    account = Account(id="a", cookies={name: "old" for name in muse.SESSION_COOKIES})
    return driver, account, sessions


async def test_cookie_change_retires_busy_context_without_interrupting(lifecycle):
    driver, account, _ = lifecycle
    old = await driver._checkout(account)
    old_context = old.context_id
    account.cookies = {name: "new" for name in muse.SESSION_COOKIES}
    fresh = await driver._checkout(account)
    assert fresh.context_id != old_context
    assert old.stale and old.busy and not old.session.closed
    assert driver._contexts[account.id] == fresh.context_id
    await driver._checkin(old)
    assert old.closed and old.session.closed
    assert driver._contexts[account.id] == fresh.context_id
    disposed = [
        p["browserContextId"]
        for m, p in driver._browser.calls
        if m == "Target.disposeBrowserContext"
    ]
    assert old_context in disposed and fresh.context_id not in disposed


async def test_explicit_invalidation_closes_idle_and_drains_busy(lifecycle):
    driver, account, _ = lifecycle
    idle = await driver._checkout(account)
    busy = await driver._checkout(account)
    await driver._checkin(idle)
    await driver.invalidate_account(account.id)
    assert idle.closed and busy.stale and not busy.closed
    assert account.id not in driver._contexts
    await driver._checkin(busy)
    assert busy.closed
    assert not driver._retired_contexts


@pytest.mark.parametrize("failure", ["Page.enable", "Runtime.enable", "Network.setCookie"])
async def test_failed_initialization_reclaims_target_and_context(lifecycle, monkeypatch, failure):
    driver, account, _ = lifecycle
    session = FakeSession(failure)

    async def connect(_url):
        return session

    monkeypatch.setattr(CDPSession, "connect", connect)
    with pytest.raises(CDPError):
        await driver._checkout(account)
    assert session.closed
    assert not driver._contexts and not driver._tabs.get(account.id)
    methods = [m for m, _ in driver._browser.calls]
    assert "Target.closeTarget" in methods and "Target.disposeBrowserContext" in methods


async def test_cancelled_open_finishes_rollback_before_unlock(lifecycle, monkeypatch):
    driver, account, _ = lifecycle
    entered, proceed = asyncio.Event(), asyncio.Event()
    session = FakeSession()

    async def connect(_url):
        entered.set()
        await proceed.wait()
        return session

    monkeypatch.setattr(CDPSession, "connect", connect)
    pending = asyncio.create_task(driver._checkout(account))
    await entered.wait()
    pending.cancel()
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert session.closed
    assert not driver._contexts and not driver._tabs.get(account.id)
    assert not driver._ctx_lock.locked()


async def test_shutdown_disposes_orphan_contexts(lifecycle):
    driver, account, _ = lifecycle
    tab = await driver._checkout(account)
    driver._contexts["orphan"] = "ctx-orphan"

    async def stop():
        pass

    driver._chromium.stop = stop
    await driver.shutdown()
    assert tab.closed and driver._browser.closed
    assert not driver._contexts
    assert (
        "Target.disposeBrowserContext",
        {"browserContextId": "ctx-orphan"},
    ) in driver._browser.calls


async def test_failed_context_disposal_is_retried_on_shutdown(lifecycle, monkeypatch):
    driver, account, _ = lifecycle
    tab = await driver._checkout(account)
    original_send = driver._browser.send
    failed = False

    async def send(method, params=None):
        nonlocal failed
        if method == "Target.disposeBrowserContext" and not failed:
            failed = True
            raise CDPError("temporary transport failure")
        return await original_send(method, params)

    monkeypatch.setattr(driver._browser, "send", send)
    await driver._close_tab(tab)
    assert tab.context_id in driver._retired_contexts

    async def stop():
        pass

    driver._chromium.stop = stop
    await driver.shutdown()
    assert not driver._retired_contexts
    assert (
        "Target.disposeBrowserContext",
        {"browserContextId": tab.context_id},
    ) in driver._browser.calls


async def test_per_account_concurrency_overrides_global(lifecycle):
    driver, account, _ = lifecycle
    account = account.model_copy(update={"max_concurrency": 1})
    first = await driver._checkout(account)
    pending = asyncio.create_task(driver._checkout(account))
    await asyncio.sleep(0)
    assert not pending.done()
    await driver._checkin(first)
    assert await asyncio.wait_for(pending, 1) is first


async def test_occupied_port_never_attaches(tmp_path, monkeypatch):
    process = ChromiumProcess("not-a-browser", 0, tmp_path / "profile")

    async def forbidden(*args, **kwargs):
        pytest.fail("must not launch or inspect an occupied DevTools endpoint")

    monkeypatch.setattr(process, "browser_ws_url", forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        process.port = occupied.getsockname()[1]
        with pytest.raises(RuntimeError, match="already in use"):
            await process.start()
    assert not process.profile_dir.exists()


def test_find_windows_browser_in_program_files(monkeypatch, tmp_path):
    root = tmp_path / "Program Files"
    expected = root / "Google/Chrome/Application/chrome.exe"
    monkeypatch.setenv("PROGRAMFILES", str(root))
    monkeypatch.setattr(
        "muse_console.drivers.browser.chromium.os.path.isfile", lambda path: path == str(expected)
    )
    monkeypatch.setattr("muse_console.drivers.browser.chromium.shutil.which", lambda _: None)
    assert find_chromium() == str(expected)


async def test_profile_outside_data_dir_is_rejected_before_launch(tmp_path):
    driver = BrowserDriver(
        Settings(
            _env_file=None, data_dir=tmp_path / "data", browser_profile_dir=tmp_path / "outside"
        )
    )
    with pytest.raises(ValueError, match="inside"):
        await driver.startup()
    assert not (tmp_path / "outside").exists()


async def test_open_and_invalidation_are_serialized(lifecycle, monkeypatch):
    driver, account, _ = lifecycle
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def connect(_url):
        entered.set()
        await proceed.wait()
        return FakeSession()

    monkeypatch.setattr(CDPSession, "connect", connect)
    opening = asyncio.create_task(driver._checkout(account))
    await entered.wait()
    invalidation = asyncio.create_task(driver.invalidate_account(account.id))
    await asyncio.sleep(0)
    assert not invalidation.done()
    proceed.set()
    tab = await opening
    await invalidation
    assert tab.stale and not tab.closed
    await driver._checkin(tab)
    assert tab.closed and not driver._contexts


async def test_session_connect_failure_rolls_back_target(lifecycle, monkeypatch):
    driver, account, _ = lifecycle

    async def connect(_url):
        raise CDPError("connection failed")

    monkeypatch.setattr(CDPSession, "connect", connect)
    with pytest.raises(CDPError):
        await driver._checkout(account)
    assert not driver._contexts
    assert "Target.closeTarget" in [method for method, _ in driver._browser.calls]


async def test_repeated_cancel_keeps_opening_under_lifecycle_lock(lifecycle, monkeypatch):
    driver, account, _ = lifecycle
    entered, proceed = asyncio.Event(), asyncio.Event()
    session = FakeSession()

    async def connect(_url):
        entered.set()
        await proceed.wait()
        return session

    monkeypatch.setattr(CDPSession, "connect", connect)
    opening = asyncio.create_task(driver._checkout(account))
    await entered.wait()
    opening.cancel()
    await asyncio.sleep(0)
    opening.cancel()
    await asyncio.sleep(0)
    assert driver._ctx_lock.locked()
    proceed.set()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert session.closed and not driver._contexts


async def test_cdp_failed_send_does_not_leak_pending_future():
    class BrokenConnection:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Future()

        async def send(self, _message):
            raise RuntimeError("socket failed")

        async def close(self):
            pass

    session = CDPSession(BrokenConnection())
    try:
        with pytest.raises(RuntimeError, match="socket failed"):
            await session.send("Page.enable")
        assert not session._pending
    finally:
        await session.close()


async def test_owned_launch_has_no_wildcard_origin_and_stops_on_cancel(tmp_path, monkeypatch):
    class Process:
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return self.returncode

    process = ChromiumProcess("fake-chromium", 0, tmp_path / "profile")
    entered = asyncio.Event()
    launched = Process()
    args = []

    async def launch(*command, **kwargs):
        args.extend(command)
        return launched

    async def wait_endpoint():
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(process, "browser_ws_url", wait_endpoint)
    pending = asyncio.create_task(process.start())
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert "--remote-allow-origins=*" not in args
    assert "--remote-debugging-address=127.0.0.1" in args
    assert launched.returncode == 0 and process.proc is None


@pytest.mark.parametrize(("no_sandbox", "expected"), [(True, True), (False, False)])
async def test_no_sandbox_flag_follows_explicit_setting(
    tmp_path, monkeypatch, no_sandbox, expected
):
    args = []

    async def launch(*command, **kwargs):
        args.extend(command)

        class Process:
            returncode = 0

            def terminate(self):
                pass

            async def wait(self):
                return 0

        return Process()

    process = ChromiumProcess("fake-chromium", 0, tmp_path / "profile", no_sandbox=no_sandbox)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)

    async def no_wait(_seconds):
        return None

    async def no_endpoint():
        # A Windows launcher exiting 0 is still considered alive. This flag-only
        # test must never probe a real local endpoint while waiting for startup.
        return None

    monkeypatch.setattr(process, "browser_ws_url", no_endpoint)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    with pytest.raises(RuntimeError):
        await process.start()
    assert ("--no-sandbox" in args) is expected


@pytest.mark.parametrize("main_tree", [True, False])
async def test_unsafe_profile_rejected_before_mkdir_or_launch(tmp_path, monkeypatch, main_tree):
    from muse_console.drivers.browser import profile_safety

    root = tmp_path / "main/User Data"
    monkeypatch.setattr(profile_safety, "_known_profile_roots", lambda: (root,))
    profile = root / "Default" if main_tree else tmp_path / "redirect/profile"
    if not main_tree:
        import stat
        from pathlib import Path

        original = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == tmp_path / "redirect":
                return SimpleNamespace(
                    st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT
                )
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", lstat)
    process = ChromiumProcess("not-a-browser", 0, profile)

    async def forbidden(*args, **kwargs):
        pytest.fail("unsafe profile must not launch or inspect an endpoint")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(process, "browser_ws_url", forbidden)
    with pytest.raises(ValueError):
        await process.start()
    assert not profile.exists()


@pytest.mark.parametrize("lock", ["SingletonLock", "SingletonSocket", "SingletonCookie"])
async def test_profile_locks_are_never_removed(tmp_path, monkeypatch, lock):
    profile = tmp_path / "owned"
    profile.mkdir()
    marker = profile / lock
    marker.write_text("untouched", encoding="utf-8")
    process = ChromiumProcess("not-a-browser", 0, profile)

    async def forbidden(*args, **kwargs):
        pytest.fail("locked profiles must not launch or inspect an endpoint")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(process, "browser_ws_url", forbidden)
    with pytest.raises(RuntimeError, match="refusing to remove"):
        await process.start()
    assert marker.read_text(encoding="utf-8") == "untouched"


async def test_unowned_or_exited_process_never_probes_port(tmp_path, monkeypatch):
    import httpx

    def forbidden(*args, **kwargs):
        pytest.fail("must not inspect an unowned or exited process endpoint")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    process = ChromiumProcess("not-a-browser", 19210, tmp_path / "owned")
    assert await process.browser_ws_url() is None
    await process.stop()
    # A POSIX Chromium is the browser itself, so its exit is final.
    monkeypatch.setattr("muse_console.drivers.browser.chromium.sys.platform", "linux")
    process.proc = SimpleNamespace(returncode=0)
    assert await process.browser_ws_url() is None
    await process.stop()
    assert process.proc is None


async def test_windows_launcher_exit_keeps_the_browser_alive(tmp_path, monkeypatch):
    monkeypatch.setattr("muse_console.drivers.browser.chromium.sys.platform", "win32")
    process = ChromiumProcess("chrome.exe", 19210, tmp_path / "owned")
    process.proc = SimpleNamespace(returncode=0)
    # chrome.exe detaches the real browser and exits 0: not evidence of death.
    assert process.exited is False
    process.proc = SimpleNamespace(returncode=3)
    assert process.exited is True
    process.proc = SimpleNamespace(returncode=None)
    assert process.exited is False
    process.proc = None
    assert process.exited is True


async def test_timeout_kills_only_owned_process_handle(tmp_path):
    calls = []

    class OwnedProcess:
        pid = 12345
        returncode = None

        def terminate(self):
            calls.append(("terminate", self.pid))

        def kill(self):
            calls.append(("kill", self.pid))
            self.returncode = -9

        async def wait(self):
            if self.returncode is None:
                raise asyncio.TimeoutError
            return self.returncode

    process = ChromiumProcess("not-a-browser", 19210, tmp_path / "owned")
    process.proc = OwnedProcess()
    await process.stop()
    await process.stop()
    assert calls == [("terminate", 12345), ("kill", 12345)]
    assert process.proc is None


@pytest.mark.parametrize("contents", ["browser", "legacy", "invalid-marker", "marker-directory"])
async def test_nonempty_unknown_profile_never_claimed(tmp_path, monkeypatch, contents):
    from muse_console.drivers.browser.profile_safety import PROFILE_MARKER

    profile = tmp_path / "data/browser-profile"
    profile.mkdir(parents=True)
    if contents == "browser":
        (profile / "Local State").write_bytes(b"fake state")
        (profile / "Default").mkdir()
    elif contents == "legacy":
        (profile / "Preferences").write_bytes(b"legacy project profile")
    elif contents == "marker-directory":
        (profile / PROFILE_MARKER).mkdir()
    else:
        (profile / PROFILE_MARKER).write_bytes(b"not a valid marker")
    before = {p.name: p.read_bytes() if p.is_file() else None for p in profile.iterdir()}

    async def forbidden(*args, **kwargs):
        pytest.fail("must not launch or probe an unknown profile")

    process = ChromiumProcess("fake-browser", 0, profile)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    monkeypatch.setattr(process, "browser_ws_url", forbidden)
    with pytest.raises(ValueError, match="manually"):
        await process.start()
    assert {p.name: p.read_bytes() if p.is_file() else None for p in profile.iterdir()} == before
    assert process.proc is None


@pytest.mark.parametrize("preexisting", [False, True])
async def test_empty_profile_claim_and_owned_restarts(tmp_path, monkeypatch, preexisting):
    from muse_console.drivers.browser.profile_safety import PROFILE_MARKER, validate_owned_paths

    profile = tmp_path / "owned"
    if preexisting:
        profile.mkdir()
    calls = []

    class Owned:
        pid = 45678
        returncode = None

        def terminate(self):
            calls.append(("terminate", self.pid))
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def launch(*args, **kwargs):
        assert (profile / PROFILE_MARKER).is_file()
        calls.append(("launch", 45678))
        return Owned()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    process = ChromiumProcess("fake-browser", 0, profile)

    async def endpoint():
        return f"ws://127.0.0.1:{process.port}/devtools/browser/owned"

    monkeypatch.setattr(process, "browser_ws_url", endpoint)
    await process.start()
    marker_bytes = (profile / PROFILE_MARKER).read_bytes()
    assert marker_bytes
    await process.stop()
    # A service-owned browser can populate the directory and reuse its marker.
    (profile / "Local State").write_bytes(b"fake service state")
    (profile / "Default").mkdir()
    validate_owned_paths(profile, profile)
    await process.start()
    await process.stop()
    await process.stop()
    assert (profile / PROFILE_MARKER).read_bytes() == marker_bytes
    assert calls == [("launch", 45678), ("terminate", 45678)] * 2


async def test_marker_exclusive_creation_race_fails_closed(tmp_path, monkeypatch):
    from muse_console.drivers.browser import profile_safety

    profile = tmp_path / "owned"
    original = profile_safety.os.open

    def racing_open(path, flags, mode=0o777):
        if str(path).endswith(profile_safety.PROFILE_MARKER):
            assert flags & profile_safety.os.O_EXCL
            path.write_bytes(b"other writer")
        return original(path, flags, mode)

    async def forbidden(*args, **kwargs):
        pytest.fail("failed marker claim must not launch")

    monkeypatch.setattr(profile_safety.os, "open", racing_open)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    process = ChromiumProcess("fake-browser", 0, profile)
    with pytest.raises(ValueError, match="exclusively claim"):
        await process.start()
    assert (profile / profile_safety.PROFILE_MARKER).read_bytes() == b"other writer"
