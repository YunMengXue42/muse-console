from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from muse2api.accounts.model import Account
from muse2api.api.routes.browser_login import router
from muse2api.config import Settings
from muse2api.drivers.base import SessionInfo
from muse2api.drivers.browser.cdp import CDPSession
from muse2api.drivers.browser.driver import BrowserDriver
from muse2api.drivers.mock import MockDriver
from muse2api.errors import (
    FeatureNotImplemented,
    InvalidRequest,
    Muse2APIError,
    NotFound,
    UpstreamError,
)
from muse2api.services.browser_login import LoginConflict, SessionManager
from muse2api.upstream.muse import SESSION_COOKIES


class FakeCDP:
    def __init__(self):
        self.closed = False
        self.calls = []
        self.contexts = set()
        self.counter = 0
        self.cookies = []

    async def send(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == "Target.createBrowserContext":
            self.counter += 1
            context = f"context-{self.counter}"
            self.contexts.add(context)
            return {"browserContextId": context}
        if method == "Target.createTarget":
            assert params["browserContextId"] in self.contexts
            return {"targetId": f"target-{self.counter}"}
        if method == "Target.disposeBrowserContext":
            self.contexts.discard(params["browserContextId"])
        if method == "Storage.getCookies":
            assert params["browserContextId"] in self.contexts
            return {"cookies": self.cookies}
        if method == "Page.captureScreenshot":
            return {"data": "anBlZw=="}
        return {}

    async def close(self):
        self.closed = True


class Importer:
    def __init__(self):
        self.accounts = {}
        self.calls = 0

    async def import_account(self, cookies, label="", cookie_expires=None):
        self.calls += 1
        identity = cookies["hatch_sess"]
        if identity not in self.accounts:
            self.accounts[identity] = Account(
                cookies=cookies, label=label, cookie_expires=cookie_expires or {}
            )
        return self.accounts[identity]


@pytest.fixture
async def setup(monkeypatch):
    settings = Settings(_env_file=None, admin_key="admin", api_key="api")
    driver = BrowserDriver(settings)
    driver._browser = FakeCDP()
    driver._chromium = SimpleNamespace(port=19210)
    pages = []

    async def connect(url):
        assert url.startswith("ws://127.0.0.1:19210/devtools/page/target-")
        page = FakeCDP()
        pages.append(page)
        return page

    monkeypatch.setattr(CDPSession, "connect", connect)
    importer = Importer()

    async def validate(cookies, *, wake_vm):
        assert wake_vm is False
        return SessionInfo(ok=True, cookies=cookies, meta={"session_status": "assigned"})

    manager = SessionManager(settings, driver, importer, validator=validate)
    driver._browser.cookies = [
        {"name": name, "value": name + "-value", "domain": ".muse.ai", "expires": -1}
        for name in SESSION_COOKIES
    ]
    yield manager, driver._browser, pages, importer
    await manager.close()


async def test_isolation_limit_ttl_and_frame(setup):
    manager, browser, pages, _ = setup
    sessions = [await manager.create(str(i)) for i in range(3)]
    assert len(browser.contexts) == 3
    assert len({s["id"] for s in sessions}) == 3
    assert set(sessions[0]) == {"id", "status", "expires_at", "width", "height"}
    assert manager.driver._contexts == {}
    with pytest.raises(LoginConflict):
        await manager.create()
    assert (await manager.frame(sessions[0]["id"]))["image"] == "anBlZw=="
    assert (
        "Emulation.setDeviceMetricsOverride",
        {
            "width": 1280,
            "height": 900,
            "deviceScaleFactor": 1,
            "mobile": False,
        },
    ) in pages[0].calls
    manager._sessions[sessions[0]["id"]].deadline = 0
    with pytest.raises(NotFound):
        await manager.get(sessions[0]["id"])
    assert len(browser.contexts) == 2
    assert pages[0].closed
    manager._sessions[sessions[1]["id"]].deadline = 0
    before = len(browser.calls)
    await manager.reap()
    assert all(m == "Target.disposeBrowserContext" for m, _ in browser.calls[before:])
    await manager.close()
    assert not browser.contexts
    assert not browser.closed


@pytest.mark.parametrize(
    "event",
    [
        {"type": "click", "x": -1, "y": 0},
        {"type": "click", "x": 1280, "y": 0},
        {"type": "click", "x": True, "y": 0},
        {"type": "scroll", "delta_y": 901},
        {"type": "key", "key": "F12"},
        {"type": "text", "text": "x" * 2049},
        {"type": "text", "text": "\n"},
        {"type": "evaluate", "expression": "document.cookie"},
        {"type": "click", "x": 0, "y": 0, "url": "https://evil.test"},
    ],
)
async def test_input_rejects_before_cdp(setup, event):
    manager, _, pages, _ = setup
    session = await manager.create()
    before = len(pages[0].calls)
    with pytest.raises(InvalidRequest):
        await manager.input(session["id"], event)
    assert len(pages[0].calls) == before


async def test_allowed_input(setup):
    manager, _, pages, _ = setup
    session = await manager.create()
    for event in [
        {"type": "click", "x": 1279, "y": 899},
        {"type": "text", "text": "手动认证"},
        {"type": "key", "key": "Enter"},
        {"type": "scroll", "delta_y": -900},
    ]:
        await manager.input(session["id"], event)
    assert pages[0].calls[-1][0] == "Input.dispatchMouseEvent"
    assert not any(m.startswith("Runtime.") for m, _ in pages[0].calls)


async def test_complete_validation_failure_retains_and_success_closes(setup):
    manager, browser, pages, importer = setup
    session = await manager.create("manual")
    session_id = session["id"]

    async def rejected(cookies, *, wake_vm):
        return SessionInfo(ok=False, cookies=cookies, meta={"session_status": "assigned"})

    original = manager._validate
    manager._validate = rejected
    with pytest.raises(InvalidRequest):
        await manager.complete(session_id)
    assert await manager.get(session_id) == session
    assert importer.calls == 0
    manager._validate = original
    browser.cookies.extend(
        [
            {"name": "hatch_sess", "value": "evil", "domain": "evilmuse.ai"},
            {"name": "hatch_sess", "value": "evil", "domain": "muse.ai.evil.test"},
            {"name": "extra", "value": "secret", "domain": "muse.ai"},
        ]
    )
    result = await manager.complete(session_id)
    assert result["account"]["label"] == "manual"
    assert all(value == "***" for value in result["account"].get("cookies", {}).values())
    assert not browser.contexts and pages[0].closed
    second = await manager.create()
    assert (await manager.complete(second["id"]))["account"]["id"] == result["account"]["id"]
    assert len(importer.accounts) == 1


async def test_missing_and_ambiguous_cookies(setup):
    manager, browser, _, importer = setup
    session = await manager.create()
    browser.cookies[0]["domain"] = "notmuse.ai"
    with pytest.raises(InvalidRequest):
        await manager.complete(session["id"])
    browser.cookies[0]["domain"] = "auth.muse.ai"
    browser.cookies.append({"name": "hatch_sess", "value": "other", "domain": ".muse.ai"})
    with pytest.raises(LoginConflict):
        await manager.complete(session["id"])
    assert importer.calls == 0


async def test_concurrent_complete_cancel(setup):
    manager, browser, _, importer = setup
    session = await manager.create()
    entered, resume = asyncio.Event(), asyncio.Event()
    original = manager._validate

    async def blocking(*args, **kwargs):
        entered.set()
        await resume.wait()
        return await original(*args, **kwargs)

    manager._validate = blocking
    first = asyncio.create_task(manager.complete(session["id"]))
    await entered.wait()
    second = asyncio.create_task(manager.complete(session["id"]))
    cancel = asyncio.create_task(manager.cancel(session["id"]))
    resume.set()
    results = await asyncio.gather(first, second, cancel, return_exceptions=True)
    assert isinstance(results[0], dict)
    assert all(isinstance(r, NotFound) for r in results[1:])
    assert importer.calls == 1
    assert not browser.contexts


async def test_cancel_complete_task_cleans_context(setup):
    manager, browser, _, _ = setup
    session = await manager.create()
    entered = asyncio.Event()

    async def blocking(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    manager._validate = blocking
    task = asyncio.create_task(manager.complete(session["id"]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not browser.contexts


async def test_mock_is_not_login():
    settings = Settings(_env_file=None)
    manager = SessionManager(settings, MockDriver(settings), Importer())
    with pytest.raises(FeatureNotImplemented):
        await manager.create()


async def test_all_routes_admin_and_contract(setup):
    manager, _, _, _ = setup
    app = FastAPI()
    app.state.services = SimpleNamespace(settings=manager.settings, browser_login=manager)
    app.include_router(router)

    @app.exception_handler(Muse2APIError)
    async def handler(request: Request, exc: Muse2APIError):
        return JSONResponse(exc.to_body(), status_code=exc.status_code)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, path, body in [
            ("POST", "", {"label": "manual"}),
            ("GET", "/unknown", None),
            ("GET", "/unknown/frame", None),
            ("POST", "/unknown/input", {"type": "text", "text": "x"}),
            ("POST", "/unknown/complete", None),
            ("DELETE", "/unknown", None),
        ]:
            for headers in ({}, {"Authorization": "Bearer api"}):
                r = await client.request(
                    method, "/admin/login-sessions" + path, json=body, headers=headers
                )
                assert r.status_code == 401
        headers = {"Authorization": "Bearer admin"}
        r = await client.post(
            "/admin/login-sessions", headers=headers, json={"url": "https://evil.test"}
        )
        assert r.status_code == 422
        r = await client.post("/admin/login-sessions", headers=headers, json={"label": "manual"})
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        sid = r.json()["id"]
        root = "/admin/login-sessions/" + sid
        assert (await client.get(root, headers=headers)).json() == r.json()
        assert (await client.get(root + "/frame", headers=headers)).status_code == 200
        assert (
            await client.post(root + "/input", headers=headers, json={"type": "key", "key": "F12"})
        ).status_code == 422
        assert (await client.post(root + "/complete", headers=headers)).status_code == 200
        assert (await client.get(root, headers=headers)).status_code == 404


async def test_create_failure_disposes_context(setup, monkeypatch):
    manager, browser, _, _ = setup

    async def fail(url):
        raise RuntimeError("page connection failed")

    monkeypatch.setattr(CDPSession, "connect", fail)
    with pytest.raises(UpstreamError):
        await manager.create()
    assert not browser.contexts
    assert not manager._sessions


async def test_shutdown_excludes_new_sessions(setup):
    manager, browser, _, _ = setup
    await manager.create()
    await manager.shutdown()
    assert not browser.contexts
    with pytest.raises(LoginConflict):
        await manager.create()


async def test_configured_capacity_and_ttl(setup):
    manager, _, _, _ = setup
    manager.settings.login_session_ttl = 30
    manager.settings.login_session_limit = 1
    import time

    before = time.time()
    session = await manager.create()
    assert before + 30 <= session["expires_at"] <= time.time() + 30
    with pytest.raises(LoginConflict):
        await manager.create()


@pytest.mark.parametrize("operation", ["frame", "input"])
async def test_disconnected_page_sanitizes_errors(setup, operation):
    manager, _, pages, importer = setup
    session = await manager.create()

    async def fail(*args, **kwargs):
        raise RuntimeError("ws://secret.test/?key=private")

    pages[0].send = fail
    with pytest.raises(UpstreamError) as error:
        if operation == "frame":
            await manager.frame(session["id"])
        else:
            await manager.input(session["id"], {"type": "text", "text": "manual"})
    assert "private" not in str(error.value)
    assert "ws:" not in str(error.value)
    assert importer.calls == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test",
        "http://muse.ai",
        "https://auth.muse.ai",
        "https://muse.ai.evil.test",
        "https://muse.ai:444",
        "https://muse.ai:invalid",
        "https://user:password@muse.ai",
    ],
)
async def test_site_url_fixed_origin(setup, url):
    manager, browser, _, _ = setup
    manager.settings.site_url = url
    with pytest.raises(InvalidRequest):
        await manager.create()
    assert not browser.contexts


async def test_closed_page_is_upstream_error(setup):
    manager, _, pages, _ = setup
    session = await manager.create()
    pages[0].closed = True
    with pytest.raises(UpstreamError):
        await manager.frame(session["id"])
