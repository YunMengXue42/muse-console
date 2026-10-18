"""Isolated, short-lived browser sessions controlled only by an administrator."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..accounts.model import Account
from ..config import Settings
from ..drivers.base import SessionInfo
from ..drivers.browser.cdp import CDPSession
from ..drivers.browser.driver import BrowserDriver
from ..errors import FeatureNotImplemented, InvalidRequest, Muse2APIError, NotFound, UpstreamError
from ..upstream import muse

WIDTH, HEIGHT = 1280, 900
KEYS = {
    "Enter": 13,
    "Tab": 9,
    "Backspace": 8,
    "Escape": 27,
    "ArrowUp": 38,
    "ArrowDown": 40,
    "ArrowLeft": 37,
    "ArrowRight": 39,
}


class LoginConflict(Muse2APIError):
    status_code = 409
    code = "login_session_conflict"
    error_type = "invalid_request_error"


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ClickInput(_Input):
    type: Literal["click"]
    x: int = Field(ge=0, lt=WIDTH)
    y: int = Field(ge=0, lt=HEIGHT)


class TextInput(_Input):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=2048)


class KeyInput(_Input):
    type: Literal["key"]
    key: Literal[
        "Enter", "Tab", "Backspace", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"
    ]


class ScrollInput(_Input):
    type: Literal["scroll"]
    delta_y: int = Field(ge=-900, le=900)


LoginInput = Annotated[ClickInput | TextInput | KeyInput | ScrollInput, Field(discriminator="type")]
_INPUT = TypeAdapter(LoginInput)


@dataclass(repr=False)
class _Session:
    id: str
    label: str
    expires_at: float
    deadline: float
    context_id: str
    page: CDPSession | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": "pending",
            "expires_at": self.expires_at,
            "width": WIDTH,
            "height": HEIGHT,
        }


class BrowserLoginManager:
    def __init__(
        self,
        settings: Settings,
        driver,
        account_manager=None,
        *,
        save_account: Callable[..., Awaitable[Account]] | None = None,
        validator: Callable[..., Awaitable[SessionInfo]] | None = None,
    ) -> None:
        self.settings = settings
        self.driver = driver
        self._save = save_account or (account_manager.import_account if account_manager else None)
        self._validate = validator or muse.renew_session
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None
        self._closed = False

    async def startup(self) -> None:
        if self._closed:
            raise LoginConflict("login manager is shut down")
        if self._reaper is None:
            self._reaper = asyncio.create_task(self._reap_loop(), name="browser-login-reaper")

    def _browser(self) -> CDPSession:
        if not isinstance(self.driver, BrowserDriver):
            raise FeatureNotImplemented("browser login requires the browser driver")
        browser = self.driver._browser
        if browser is None or browser.closed or self.driver._chromium is None:
            raise LoginConflict("browser driver is not ready")
        return browser

    def _site_url(self) -> str:
        url = self.settings.site_url
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname == "muse.ai"
                and not parsed.username
                and not parsed.password
                and parsed.port in (None, 443)
            )
        except ValueError:
            valid = False
        if not valid:
            raise InvalidRequest("browser login site_url must use the https://muse.ai origin")
        return url

    @staticmethod
    def _allowed_host(host: str) -> bool:
        host = host.lower()
        return host == "muse.ai" or host.endswith(".muse.ai")

    async def create(self, label: str = "") -> dict:
        if not isinstance(label, str) or len(label) > 100:
            raise InvalidRequest("label must contain at most 100 characters")
        self._browser()
        url = self._site_url()
        await self.reap()
        async with self._lock:
            if self._closed:
                raise LoginConflict("login manager is shut down")
            if len(self._sessions) >= self.settings.login_session_limit:
                raise LoginConflict("browser login session limit reached")
            if self._save is None:
                raise LoginConflict("account import is not configured")
            await self.startup()
            browser = self._browser()
            # Shield allocation so cancellation cannot lose a newly allocated context id.
            allocation = asyncio.create_task(
                browser.send(
                    "Target.createBrowserContext",
                    {
                        "disposeOnDetach": True,
                    },
                )
            )
            try:
                result = await asyncio.shield(allocation)
            except Exception:
                raise UpstreamError("login browser session could not be created") from None
            except asyncio.CancelledError:
                result = await allocation
                await self._dispose_context(browser, result["browserContextId"])
                raise
            session = _Session(
                secrets.token_urlsafe(32),
                label,
                time.time() + self.settings.login_session_ttl,
                time.monotonic() + self.settings.login_session_ttl,
                result["browserContextId"],
            )
            self._sessions[session.id] = session
            try:
                target = await browser.send(
                    "Target.createTarget",
                    {
                        "url": "about:blank",
                        "browserContextId": session.context_id,
                    },
                )
                port = self.driver._chromium.port
                session.page = await CDPSession.connect(
                    f"ws://127.0.0.1:{port}/devtools/page/{target['targetId']}"
                )
                await session.page.send("Page.enable")
                await session.page.send(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": WIDTH,
                        "height": HEIGHT,
                        "deviceScaleFactor": 1,
                        "mobile": False,
                    },
                )
                await session.page.send("Page.navigate", {"url": url})
            except asyncio.CancelledError:
                await self._remove(session)
                raise
            except Exception:
                await self._remove(session)
                raise UpstreamError("login browser session could not be created") from None
            return session.public()

    def _get(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise NotFound("login session not found or expired")
        return session

    async def _check(self, session: _Session) -> None:
        if self._sessions.get(session.id) is not session:
            raise NotFound("login session has already closed")
        if time.monotonic() >= session.deadline:
            await self._remove(session)
            raise NotFound("login session expired")
        if session.page is None or session.page.closed:
            with contextlib.suppress(Exception):
                await self._remove(session)
            raise UpstreamError("login browser page is unavailable")

    async def get(self, session_id: str) -> dict:
        session = self._get(session_id)
        async with session.lock:
            await self._check(session)
            return session.public()

    async def frame(self, session_id: str) -> dict:
        session = self._get(session_id)
        async with session.lock:
            await self._check(session)
            try:
                result = await session.page.send(
                    "Page.captureScreenshot",
                    {
                        "format": "jpeg",
                        "quality": 75,
                        "captureBeyondViewport": False,
                        "clip": {"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT, "scale": 1},
                    },
                )
            except Exception:
                raise UpstreamError("login browser page is unavailable") from None
            return {"image": result["data"], "width": WIDTH, "height": HEIGHT}

    async def input(self, session_id: str, event: LoginInput | dict) -> dict:
        try:
            event = _INPUT.validate_python(event)
        except ValidationError:
            raise InvalidRequest("invalid login input") from None
        session = self._get(session_id)
        async with session.lock:
            await self._check(session)
            page = session.page
            try:
                if isinstance(event, ClickInput):
                    for kind in ("mousePressed", "mouseReleased"):
                        await page.send(
                            "Input.dispatchMouseEvent",
                            {
                                "type": kind,
                                "x": event.x,
                                "y": event.y,
                                "button": "left",
                                "clickCount": 1,
                            },
                        )
                elif isinstance(event, TextInput):
                    if any(ord(c) < 32 or ord(c) == 127 for c in event.text):
                        raise InvalidRequest("text must not contain control characters")
                    await page.send("Input.insertText", {"text": event.text})
                elif isinstance(event, KeyInput):
                    for kind in ("keyDown", "keyUp"):
                        await page.send(
                            "Input.dispatchKeyEvent",
                            {
                                "type": kind,
                                "key": event.key,
                                "windowsVirtualKeyCode": KEYS[event.key],
                            },
                        )
                else:
                    await page.send(
                        "Input.dispatchMouseEvent",
                        {
                            "type": "mouseWheel",
                            "x": WIDTH // 2,
                            "y": HEIGHT // 2,
                            "deltaX": 0,
                            "deltaY": event.delta_y,
                        },
                    )
            except InvalidRequest:
                raise
            except Exception:
                raise UpstreamError("login browser page is unavailable") from None
            return session.public()

    async def complete(self, session_id: str) -> dict:
        session = self._get(session_id)
        async with session.lock:
            await self._check(session)
            try:
                result = await self._browser().send(
                    "Storage.getCookies",
                    {
                        "browserContextId": session.context_id,
                    },
                )
                cookies, expires = {}, {}
                for cookie in result.get("cookies", []):
                    domain = cookie.get("domain", "").removeprefix(".")
                    name = cookie.get("name")
                    if not self._allowed_host(domain) or name not in muse.SESSION_COOKIES:
                        continue
                    if name in cookies and cookies[name] != cookie.get("value"):
                        raise LoginConflict("ambiguous session cookies; finish signing in first")
                    expiry = cookie.get("expires", -1)
                    if expiry > 0 and expiry <= time.time():
                        continue
                    cookies[name] = cookie.get("value", "")
                    if expiry > 0:
                        expires[name] = int(expiry)
                if muse.missing_session_cookies(cookies):
                    raise InvalidRequest("login is not ready: required session cookies are missing")
                info = await self._validate(cookies, wake_vm=False)
                if not info.ok or info.meta.get("session_status") != "assigned":
                    raise InvalidRequest("login is not ready: upstream session is not assigned")
                cookies.update({k: v for k, v in info.cookies.items() if k in muse.SESSION_COOKIES})
                expires.update(
                    {k: v for k, v in info.cookie_expires.items() if k in muse.SESSION_COOKIES}
                )
                if muse.missing_session_cookies(cookies):
                    raise InvalidRequest("upstream session cookies are incomplete")
                await self._check(session)
                # Finish a started persistence call before releasing the session lock.
                saving = asyncio.create_task(
                    self._save(
                        cookies=cookies,
                        label=session.label,
                        cookie_expires=expires,
                    )
                )
                try:
                    account = await asyncio.shield(saving)
                except asyncio.CancelledError:
                    with contextlib.suppress(Exception):
                        await saving
                    raise
            except asyncio.CancelledError:
                await self._remove(session)
                raise
            except (InvalidRequest, LoginConflict, NotFound):
                raise
            except Exception:
                raise LoginConflict(
                    "login verification or account import failed; retry completion"
                ) from None
            await self._remove(session)
            return {"account": account.public()}

    async def cancel(self, session_id: str) -> dict:
        session = self._get(session_id)
        async with session.lock:
            await self._check(session)
            await self._remove(session)
            return {"cancelled": True}

    async def _dispose_context(self, browser: CDPSession, context_id: str) -> None:
        try:
            await browser.send("Target.disposeBrowserContext", {"browserContextId": context_id})
        except Exception:
            raise UpstreamError("login browser cleanup failed") from None

    async def _cleanup(self, session: _Session) -> None:
        async def cleanup():
            try:
                if session.page:
                    await session.page.close()
            finally:
                browser = self.driver._browser
                if browser and not browser.closed:
                    await self._dispose_context(browser, session.context_id)

        task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _remove(self, session: _Session) -> None:
        # Keep capacity reserved if CDP disposal fails, so repeated failures cannot leak unbounded contexts.
        await self._cleanup(session)
        self._sessions.pop(session.id, None)

    async def reap(self) -> None:
        for session in list(self._sessions.values()):
            if time.monotonic() >= session.deadline:
                async with session.lock:
                    if self._sessions.get(session.id) is session:
                        with contextlib.suppress(Exception):
                            await self._remove(session)

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            await self.reap()

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            if self._reaper:
                self._reaper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reaper
                self._reaper = None
            for session in list(self._sessions.values()):
                async with session.lock:
                    if self._sessions.get(session.id) is session:
                        await self._remove(session)

    async def shutdown(self) -> None:
        await self.close()


SessionManager = BrowserLoginManager
