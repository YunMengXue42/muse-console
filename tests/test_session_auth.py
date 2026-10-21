from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from muse_console.accounts.credentials import records_to_cdp
from muse_console.accounts.model import Account
from muse_console.config import Settings
from muse_console.drivers.browser.driver import BrowserDriver
from muse_console.drivers.browser.session_auth import SessionProbeError, probe_session
from muse_console.errors import UpstreamAuthError, UpstreamError
from test_credentials import records


class Browser:
    closed = False

    def __init__(self):
        self.calls = []
        self.cookies = records_to_cdp(records())

    async def send(self, method, params=None, **kwargs):
        self.calls.append((method, params))
        if method == "Target.createBrowserContext":
            return {"browserContextId": "owned-ctx"}
        if method == "Target.createTarget":
            return {"targetId": "owned-tab"}
        if method == "Storage.getCookies":
            return {"cookies": self.cookies}
        return {}


class Page:
    def __init__(self, response=None):
        self.closed = False
        self.response = response or {"http": 200, "assigned": True}

    async def send(self, *args, **kwargs):
        return {}

    async def evaluate(self, script, **kwargs):
        if script.startswith("({origin"):
            return {"origin": "https://muse.ai", "ready": "complete"}
        return self.response

    async def close(self):
        self.closed = True


def driver():
    return SimpleNamespace(
        _browser=Browser(),
        _chromium=SimpleNamespace(port=19210, proc=SimpleNamespace(returncode=None)),
    )


async def test_probe_only_reads_owned_context_and_disposes():
    runtime, page = driver(), Page()

    async def connect(url):
        assert url == "ws://127.0.0.1:19210/devtools/page/owned-tab"
        return page

    refreshed, subject = await probe_session(runtime, records(), connect=connect)
    assert refreshed == records() and subject is None and page.closed
    for method, params in runtime._browser.calls:
        if method.startswith("Storage."):
            assert params["browserContextId"] == "owned-ctx"
    assert runtime._browser.calls[-1] == (
        "Target.disposeBrowserContext",
        {"browserContextId": "owned-ctx"},
    )


async def test_auth_rejection_and_cancellation_always_reclaim():
    runtime, page = driver(), Page({"http": 401})

    async def connect(url):
        return page

    with pytest.raises(SessionProbeError) as exc:
        await probe_session(runtime, records(), connect=connect)
    assert exc.value.status == "awaiting_login"
    assert page.closed
    assert runtime._browser.calls[-1][0] == "Target.disposeBrowserContext"

    entered = asyncio.Event()
    runtime = driver()

    async def blocked(url):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(probe_session(runtime, records(), connect=blocked))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._browser.calls[-1][0] == "Target.disposeBrowserContext"


async def test_probe_reports_upstream_workspace_subject():
    runtime = driver()
    page = Page({"http": 200, "assigned": True, "vm_id": "322DF828-738C-4523-9A49-7E766A9ADFEB"})

    async def connect(url):
        return page

    _, subject = await probe_session(runtime, records(), connect=connect)
    assert subject == "muse-vm:322df828-738c-4523-9a49-7e766a9adfeb"
    assert page.closed


async def test_probe_ignores_unusable_workspace_ids():
    async def subject_for(vm_id):
        page = Page({"http": 200, "assigned": True, "vm_id": vm_id})

        async def connect(url):
            return page

        _, subject = await probe_session(driver(), records(), connect=connect)
        return subject

    for vm_id in (None, "", "not-a-uuid", 42):
        assert await subject_for(vm_id) is None


async def test_probe_accepts_a_detached_windows_launcher():
    """chrome.exe hands off and exits 0, which must not read as a dead browser."""
    runtime, page = driver(), Page()
    runtime._chromium = SimpleNamespace(port=19210, exited=False, proc=None)

    async def connect(url):
        return page

    refreshed, subject = await probe_session(runtime, records(), connect=connect)
    assert refreshed == records() and subject is None and page.closed


async def test_probe_refuses_unowned_runtime():
    runtime = driver()
    runtime._chromium.proc = None
    with pytest.raises(SessionProbeError):
        await probe_session(runtime, records())
    assert not runtime._browser.calls


@pytest.mark.parametrize(
    "status,error", [("awaiting_login", UpstreamAuthError), ("retry_wait", UpstreamError)]
)
async def test_browser_renewal_maps_probe_errors(monkeypatch, tmp_path, status, error):
    async def fail(*args):
        raise SessionProbeError(status, "private-code")

    monkeypatch.setattr("muse_console.drivers.browser.session_auth.probe_session", fail)
    runtime = BrowserDriver(Settings(_env_file=None, data_dir=tmp_path))
    with pytest.raises(error):
        await runtime.renew_session(Account(credential_records=records()))


async def test_repeated_cancellation_cannot_cancel_context_disposal():
    runtime, page = driver(), Page()
    entered, release = asyncio.Event(), asyncio.Event()
    disposed = False
    original = runtime._browser.send

    async def send(method, params=None, **kwargs):
        nonlocal disposed
        if method == "Target.disposeBrowserContext":
            entered.set()
            await release.wait()
            disposed = True
        return await original(method, params, **kwargs)

    async def connect(url):
        return page

    runtime._browser.send = send
    task = asyncio.create_task(probe_session(runtime, records(), connect=connect))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert disposed
    assert not getattr(runtime, "_credential_probe_unhealthy", False)


async def test_repeated_cancellation_during_allocation_disposes_returned_context():
    runtime = driver()
    entered, release = asyncio.Event(), asyncio.Event()
    original = runtime._browser.send

    async def send(method, params=None, **kwargs):
        if method == "Target.createBrowserContext":
            entered.set()
            await release.wait()
        return await original(method, params, **kwargs)

    runtime._browser.send = send
    task = asyncio.create_task(probe_session(runtime, records()))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime._browser.calls[-1][0] == "Target.disposeBrowserContext"
