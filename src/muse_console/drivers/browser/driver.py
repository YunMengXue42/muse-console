"""operate the muse.ai web client in owned Chromium contexts over CDP.

contexts are isolated by account and credential generation. the account pool
limits active requests; tabs are reserved separately to preserve warm threads.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import sys
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ...accounts.credentials import project_records, records_to_cdp
from ...accounts.model import Account
from ...config import Settings
from ...core import metrics
from ...core.prompt import followup_text
from ...core.safe_download import download_public_media
from ...errors import (
    CapacityExceeded,
    UpstreamAuthError,
    UpstreamError,
    UpstreamQuotaError,
    UpstreamRefused,
    UpstreamTimeout,
)
from ...upstream import muse
from ..base import (
    ChatRequest,
    DriverCapabilities,
    ImageRequest,
    InputImage,
    MediaResult,
    MuseDriver,
    SessionInfo,
    VideoRequest,
)
from . import dom
from .cdp import CDPError, CDPSession
from .chromium import ChromiumProcess, find_chromium
from .media_wait import Act, MediaWait
from .profile_safety import validate_owned_paths

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.15
STABLE_POLLS_DONE = 4
# Past this many assistant bubbles the page gets slow and a new thread is cheaper.
HOT_BUBBLE_LIMIT = 16


def is_thread_url(url: str) -> bool:
    """A real muse conversation, not the empty "new thread" page."""
    path = urlsplit(url).path.rstrip("/")
    return path.startswith("/thread/") and path != "/thread/new"


# A line this short may still be a markdown marker ("-", "1.", "##") that the page
# re-renders away once the next characters arrive, so it is held back until then.
_HOLD_LINE = 4


def stream_delta(emitted: str, text: str, final: bool = False) -> tuple[str, str]:
    """Next chunk to send for the page's current reply ``text``.

    Returns ``(delta, emitted)``. Sent text can never be retracted, so a page that
    re-renders earlier characters (markdown markers turning into list bullets) must
    not cause the whole reply to be sent again: only text past what was already
    emitted is sent, aligned on the longest common prefix.
    """
    text = text.replace("\u200b", "")
    if not final:
        cut = text.rfind("\n") + 1
        if len(text) - cut < _HOLD_LINE:
            text = text[:cut]
    if text.startswith(emitted):
        return text[len(emitted) :], text
    # The page rewrote text that was already sent. It cannot be retracted, so
    # continue after the sent length instead of sending the whole reply again.
    if len(text) <= len(emitted):
        return "", emitted
    return text[len(emitted) :], emitted + text[len(emitted) :]


@dataclass
class _Tab:
    account_id: str
    context_id: str
    target_id: str
    session: CDPSession
    hint: str | None = None
    turns: list[tuple[str, str]] = field(default_factory=list)
    image_count: int = 0
    thread_url: str = ""
    quota_note: str = ""
    """Last matched fixed quota hint; never retain page snippets for logging."""
    busy: bool = False
    used: bool = False
    """This target has entered submission; independent requests must replace it."""
    stale: bool = False
    """Cookies were renewed; close this tab once it is idle."""
    closed: bool = False
    idle_since: float = field(default_factory=time.monotonic)

    @property
    def has_state(self) -> bool:
        return bool(self.turns or self.thread_url)


class BrowserDriver(MuseDriver):
    name = "browser"
    capabilities = DriverCapabilities(
        chat=True, chat_images=True, image=True, image_edit=True, video=True, renew_session=True
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._chromium: ChromiumProcess | None = None
        self._browser: CDPSession | None = None
        # One browser context (cookie jar) per account, holding up to
        # account_max_concurrency tabs; each request checks one tab out.
        self._contexts: dict[str, str] = {}
        self._tabs: dict[str, list[_Tab]] = {}
        self._fingerprints: dict[str, str] = {}
        self._retired_contexts: set[str] = set()
        self._closing = False
        self._tab_cond = asyncio.Condition()
        self._tab_changed = asyncio.Event()
        self._ctx_lock = asyncio.Lock()
        self._browser_ws_url: str | None = None
        self._recovery_after = 0.0
        self._reaper: asyncio.Task | None = None

    # ------------------------------------------------------------ lifecycle
    async def startup(self) -> None:
        validate_owned_paths(self.settings.data_dir, self.settings.profile_dir)
        self._closing = False
        exe = find_chromium(self.settings.chromium_path)
        self._chromium = ChromiumProcess(
            exe,
            self.settings.cdp_port,
            self.settings.profile_dir,
            self.settings.headless,
            proxy=self.settings.browser_proxy,
            no_sandbox=self.settings.chromium_no_sandbox,
        )
        try:
            ws_url = await self._chromium.start()
            self._browser = await CDPSession.connect(ws_url)
            self._browser_ws_url = ws_url
        except BaseException:
            await self._chromium.stop()
            raise
        self._reaper = asyncio.create_task(self._reap_idle())
        log.info("browser driver ready")

    async def shutdown(self) -> None:
        """drain owned contexts before disconnecting from Chromium."""
        self._closing = True
        if self._reaper:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        async with self._ctx_lock:
            await self._finish_cleanup(self._shutdown_locked())
        await self._notify_tabs()

    async def _shutdown_locked(self) -> None:
        """close tabs and orphan contexts while preserving process ownership."""

        async def dispose_contexts() -> None:
            for tabs in list(self._tabs.values()):
                for tab in list(tabs):
                    await self._dispose_tab_locked(tab)
            for context_id in set(self._contexts.values()) | self._retired_contexts:
                await self._dispose_context(context_id)

        preserve_connection = False
        try:
            # wait_for also cancels/awaits the helper on Python 3.10.
            # Reserve the rest of the stop budget for Chromium's profile flush.
            await asyncio.wait_for(dispose_contexts(), 1)
        except asyncio.TimeoutError:
            log.warning("code=browser_context_shutdown_timeout")
        finally:
            try:
                if isinstance(self._chromium, ChromiumProcess):
                    # Only this already-owned connection may close the browser.
                    proc = self._chromium.proc
                    try:
                        await self._chromium.stop(self._browser)
                    finally:
                        if (
                            sys.platform == "win32"
                            and proc is not None
                            and proc.returncode == 0
                            and self._browser is not None
                            and not self._browser.closed
                        ):
                            # stop() retains ownership on incomplete shutdown;
                            # retain the existing connection for its next retry.
                            preserve_connection = True
                elif self._chromium:
                    # Preserve the no-argument stop contract of injected mocks.
                    await self._chromium.stop()
            finally:
                try:
                    if self._browser and not preserve_connection:
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(self._browser.close(), 1)
                finally:
                    self._contexts.clear()
                    self._retired_contexts.clear()
                    self._fingerprints.clear()
                    self._tab_changed.set()

    async def health(self) -> dict[str, Any]:
        ok = self._browser is not None and not self._browser.closed
        tabs = [t for ts in self._tabs.values() for t in ts]
        return {
            "driver": self.name,
            "ok": ok,
            "tabs": len(tabs),
            "busy_tabs": sum(t.busy for t in tabs),
        }

    async def _recover_locked(self) -> None:
        """Reconnect only at a new checkout, never retry a submitted operation.

        Use the exact endpoint obtained from our owned launch, not port discovery:
        a replacement process on the same port must not become trusted implicitly.
        Process restart is deliberately left to an explicit service restart.
        """
        if not self._browser or not self._browser.closed:
            return
        if not self._browser_ws_url or time.monotonic() < self._recovery_after:
            raise UpstreamError("browser disconnected; recovery temporarily unavailable")
        self._recovery_after = time.monotonic() + 5.0
        for tabs in list(self._tabs.values()):
            for tab in list(tabs):
                tab.stale = True
                await self._dispose_tab_locked(tab)
        self._contexts.clear()
        self._fingerprints.clear()
        self._retired_contexts.clear()
        await self._browser.close()
        try:
            self._browser = await CDPSession.connect(self._browser_ws_url)
        except Exception:
            raise UpstreamError(
                "browser reconnection failed; request was not submitted (code=cdp_reconnect_failed)"
            ) from None
        self._tab_changed.set()

    async def _prune_idle_locked(self) -> None:
        now = time.monotonic()
        ttl = max(0.0, getattr(self.settings, "browser_idle_ttl", 900.0))
        for tabs in list(self._tabs.values()):
            for tab in list(tabs):
                if not tab.busy and (tab.session.closed or now - tab.idle_since >= ttl):
                    await self._dispose_tab_locked(tab)
        for account_id in list(self._fingerprints):
            if not self._tabs.get(account_id) and account_id not in self._contexts:
                self._fingerprints.pop(account_id, None)
                self._tabs.pop(account_id, None)

    async def _reap_idle(self) -> None:
        try:
            while not self._closing:
                ttl = max(0.0, getattr(self.settings, "browser_idle_ttl", 900.0))
                await asyncio.sleep(max(0.1, min(30.0, ttl / 2)))
                async with self._ctx_lock:
                    await self._finish_cleanup(self._prune_idle_locked())
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------ tabs
    @staticmethod
    def _fingerprint(account: Account) -> str:
        payload = json.dumps(
            [
                account.cookies,
                account.cookie_expires,
                [r.model_dump(mode="json") for r in account.credential_records],
                account.credential_version,
            ],
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def _checkout(
        self, account: Account, prefer: Callable[[_Tab], bool] | None = None
    ) -> _Tab:
        """reserve a tab without reusing a context from an older credential generation."""
        limit = max(
            1, getattr(account, "max_concurrency", None) or self.settings.account_max_concurrency
        )
        while True:
            async with self._ctx_lock:
                if self._closing:
                    raise UpstreamError("browser driver is shutting down")
                await self._finish_cleanup(self._recover_locked())
                await self._finish_cleanup(self._prune_idle_locked())
                fingerprint = self._fingerprint(account)
                if self._fingerprints.get(account.id) != fingerprint:
                    await self._finish_cleanup(self._invalidate_locked(account.id))
                    self._fingerprints[account.id] = fingerprint
                for tab in list(self._tabs.get(account.id, [])):
                    if tab.closed or (not tab.busy and (tab.stale or tab.session.closed)):
                        await self._finish_cleanup(self._dispose_tab_locked(tab))
                tabs = self._tabs.setdefault(account.id, [])
                idle = [t for t in tabs if not t.busy and not t.stale]
                can_open = len(tabs) < limit
                match = next((t for t in idle if prefer is None or prefer(t)), None)
                if match or (idle and not can_open):
                    tab = match or next((t for t in idle if not t.has_state), idle[0])
                    tab.busy = True
                    return tab
                if can_open:
                    all_tabs = [t for ts in self._tabs.values() for t in ts if not t.closed]
                    budget = max(1, getattr(self.settings, "browser_max_tabs", 16))
                    if len(all_tabs) >= budget:
                        victims = [t for t in all_tabs if not t.busy]
                        if victims:
                            # Prefer local reuse over destroying another warm thread.
                            if idle:
                                tab = idle[0]
                                tab.busy = True
                                return tab
                            victim = min(victims, key=lambda t: t.idle_since)
                            await self._finish_cleanup(self._dispose_tab_locked(victim))
                        else:
                            if not tabs:
                                self._tabs.pop(account.id, None)
                                self._fingerprints.pop(account.id, None)
                            raise CapacityExceeded("browser tab budget exhausted")
                if can_open:
                    snapshot = account.model_copy(deep=True)
                    opening = asyncio.create_task(self._open_tab(snapshot))
                    try:
                        tab = await asyncio.shield(opening)
                    except asyncio.CancelledError:
                        await self._finish_cleanup(self._discard_opening(opening))
                        raise
                    except Exception:
                        if not self._tabs.get(account.id) and account.id not in self._contexts:
                            self._tabs.pop(account.id, None)
                            self._fingerprints.pop(account.id, None)
                        raise
                    tab.busy = True
                    self._tabs.setdefault(account.id, []).append(tab)
                    return tab
                self._tab_changed.clear()
            await self._tab_changed.wait()

    async def _discard_opening(self, opening: asyncio.Task) -> None:
        """consume a cancelled caller's pending resource allocation before unlocking."""
        try:
            tab = await opening
        except BaseException:
            return
        await self._dispose_tab_locked(tab)

    @staticmethod
    async def _finish_cleanup(operation) -> None:
        """keep cleanup under its lifecycle lock even after repeated cancellation."""
        cleanup = asyncio.create_task(operation)
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _checkin(self, tab: _Tab) -> None:
        # free before awaiting a lock so cancellation cannot strand a reservation.
        tab.idle_since = time.monotonic()
        tab.busy = False
        self._tab_changed.set()
        if tab.stale:
            await self._close_tab(tab)
        await asyncio.shield(self._notify_tabs())

    async def _notify_tabs(self) -> None:
        self._tab_changed.set()
        async with self._tab_cond:
            self._tab_cond.notify_all()

    async def invalidate_account(self, account_id: str) -> None:
        """retire credentials without interrupting tabs already serving a request."""
        async with self._ctx_lock:
            await self._finish_cleanup(self._invalidate_locked(account_id))

    async def _invalidate_locked(self, account_id: str) -> None:
        """detach the current context so future tabs get a clean cookie jar."""
        context_id = self._contexts.pop(account_id, None)
        self._fingerprints.pop(account_id, None)
        if context_id:
            self._retired_contexts.add(context_id)
        for tab in list(self._tabs.get(account_id, [])):
            tab.stale = True
            if not tab.busy:
                await self._dispose_tab_locked(tab)
        if (
            context_id
            and context_id in self._retired_contexts
            and not any(t.context_id == context_id for tabs in self._tabs.values() for t in tabs)
        ):
            await self._dispose_context(context_id)
        self._tab_changed.set()

    async def _context(self, account: Account) -> str:
        """get the context while the caller holds the lifecycle lock."""
        if ctx := self._contexts.get(account.id):
            return ctx
        assert self._browser
        res = await self._browser.send("Target.createBrowserContext", {"disposeOnDetach": True})
        self._contexts[account.id] = res["browserContextId"]
        return res["browserContextId"]

    async def _open_tab(self, account: Account) -> _Tab:
        """open a target transactionally; rollback partial CDP initialization."""
        if missing := muse.missing_session_cookies(account.cookies):
            raise UpstreamAuthError(f"account is missing session cookies: {', '.join(missing)}")
        assert self._browser and self._chromium
        # _ctx_lock serializes creation through initialization/rollback. A context
        # inherits browser-side cookie rotation after its first credential seed.
        new_context = account.id not in self._contexts
        context_id = await self._context(account)
        target_id = None
        session = None
        try:
            # separate windows avoid background-tab throttling of parallel requests.
            target = await self._browser.send(
                "Target.createTarget",
                {"url": "about:blank", "browserContextId": context_id, "newWindow": True},
            )
            target_id = target["targetId"]
            session = await CDPSession.connect(
                f"ws://127.0.0.1:{self._chromium.port}/devtools/page/{target_id}"
            )
            for domain in ("Page", "Runtime", "Network"):
                await session.send(f"{domain}.enable")
            await session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            await session.send("Page.setWebLifecycleState", {"state": "active"})
            if new_context:
                await self._set_cookies(session, account)
            return _Tab(account.id, context_id, target_id, session)
        except BaseException:
            if session:
                with contextlib.suppress(Exception):
                    await session.close()
            if target_id:
                with contextlib.suppress(Exception):
                    await self._browser.send("Target.closeTarget", {"targetId": target_id})
            if new_context:
                if self._contexts.get(account.id) == context_id:
                    self._contexts.pop(account.id, None)
                await self._dispose_context(context_id)
            raise

    async def _fresh_target(self, tab: _Tab, account: Account) -> None:
        """Replace a used target before submission without releasing its pool slot."""
        if not tab.used and not tab.has_state:
            return
        async with self._ctx_lock:
            await self._finish_cleanup(self._replace_target_locked(tab, account))

    async def _replace_target_locked(self, tab: _Tab, account: Account) -> None:
        if self._closing or tab.closed or tab.stale:
            raise UpstreamError("browser tab is no longer available")
        # Close first: replacement must not temporarily exceed the target budget.
        # Keep the reservation/context alive, including other hot or active tabs.
        assert self._browser
        await tab.session.close()
        await self._browser.send("Target.closeTarget", {"targetId": tab.target_id})
        # Credential invalidation may have detached this busy tab's context.
        # Never open a target using a newer account mapping for an old reservation.
        if self._contexts.get(account.id) != tab.context_id:
            raise UpstreamError("browser context changed before submission")
        fresh = await self._open_tab(account.model_copy(deep=True))
        tab.target_id, tab.session = fresh.target_id, fresh.session
        tab.used = False
        self._forget(tab)

    async def _close_tab(self, tab: _Tab) -> None:
        if not tab.closed:
            await self._finish_cleanup(self._dispose_tab(tab))

    async def _dispose_tab(self, tab: _Tab) -> None:
        async with self._ctx_lock:
            await self._dispose_tab_locked(tab)

    async def _dispose_tab_locked(self, tab: _Tab) -> None:
        """dispose by context identity, not by an account's newer context mapping."""
        if tab.closed:
            return
        tab.closed, tab.busy = True, False
        tabs = self._tabs.get(tab.account_id, [])
        if tab in tabs:
            tabs.remove(tab)
        try:
            await tab.session.close()
        finally:
            if self._browser and not self._browser.closed:
                with contextlib.suppress(Exception):
                    await self._browser.send("Target.closeTarget", {"targetId": tab.target_id})
            if not any(t.context_id == tab.context_id for t in tabs):
                if self._contexts.get(tab.account_id) == tab.context_id:
                    self._contexts.pop(tab.account_id, None)
                await self._dispose_context(tab.context_id)
            if not tabs:
                self._tabs.pop(tab.account_id, None)
                if tab.account_id not in self._contexts:
                    self._fingerprints.pop(tab.account_id, None)
            self._tab_changed.set()

    async def _dispose_context(self, context_id: str) -> None:
        self._retired_contexts.add(context_id)
        if self._browser and not self._browser.closed:
            try:
                await self._browser.send(
                    "Target.disposeBrowserContext", {"browserContextId": context_id}
                )
            except Exception:
                log.warning("browser context cleanup deferred until shutdown")
                return
        self._retired_contexts.discard(context_id)

    @staticmethod
    async def _set_cookies(session: CDPSession, account: Account) -> None:
        if account.credential_records:
            try:
                params_list = records_to_cdp(account.credential_records)
            except ValueError:
                raise UpstreamAuthError("session credentials have expired") from None
            for params in params_list:
                result = await session.send("Network.setCookie", params)
                if result.get("success") is False:
                    raise UpstreamAuthError("browser rejected session cookie scope")
            return
        for name, value in account.cookies.items():
            if not value:
                continue
            exp = account.cookie_expires.get(name) or 0
            params = {
                "name": name,
                "value": value,
                "domain": ".muse.ai",
                "path": "/",
                "secure": True,
            }
            if exp:
                if exp <= time.time():
                    raise UpstreamAuthError("session credentials have expired")
                params["expires"] = exp
            result = await session.send("Network.setCookie", params)
            if result.get("success") is False:
                raise UpstreamAuthError("browser rejected session cookie scope")

    @staticmethod
    def _forget(tab: _Tab) -> None:
        tab.hint = None
        tab.turns = []
        tab.image_count = 0
        tab.thread_url = ""

    def _load_hot(self, tab: _Tab, account: Account) -> None:
        """Restore the last conversation after a restart or a new browser tab."""
        if tab.turns or tab.thread_url:
            return
        hot = account.meta.get("hot_page")
        if not isinstance(hot, dict):
            return
        turns = []
        for item in hot.get("turns") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                turns.append((str(item[0]), str(item[1])))
        tab.turns = turns
        tab.hint = hot.get("hint") or None
        tab.thread_url = str(hot.get("url") or "")
        try:
            tab.image_count = int(hot.get("image_count") or 0)
        except (TypeError, ValueError):
            tab.image_count = 0

    def _save_hot(self, tab: _Tab, account: Account, req: ChatRequest, url: str) -> None:
        if url:
            tab.thread_url = url
        tab.hint = req.conversation_hint
        tab.turns = list(req.turns)
        tab.image_count = len(req.images)
        account.meta["hot_page"] = {
            "hint": req.conversation_hint or "",
            "turns": [list(turn) for turn in req.turns],
            "url": tab.thread_url,
            "image_count": tab.image_count,
        }

    async def _href(self, tab: _Tab) -> str:
        try:
            return (await tab.session.evaluate("location.href")) or ""
        except CDPError:
            return ""

    async def _on_thread(self, tab: _Tab) -> bool:
        """The open page is the saved conversation and can take another message."""
        href = await self._href(tab)
        if not is_thread_url(href):
            return False
        if tab.thread_url and urlsplit(href).path.rstrip("/") != urlsplit(
            tab.thread_url
        ).path.rstrip("/"):
            return False
        try:
            page = await tab.session.evaluate(dom.PAGE_STATE) or {}
            if not page.get("hasInput"):
                return False
            if "Connecting..." in (page.get("head") or ""):
                return False
            state = await self._state(tab)
        except CDPError:
            return False
        if state.get("generating"):
            return False
        if any(hint in state.get("tail", "") for hint in dom.STALL_HINTS):
            return False
        return (state.get("agentCount") or 0) < HOT_BUBBLE_LIMIT

    async def _new_thread(self, tab: _Tab) -> None:
        self._forget(tab)
        session, target_id = tab.session, tab.target_id
        try:
            before = (await session.send("Page.getFrameTree", timeout=5.0))["frameTree"]["frame"]
        except TimeoutError:
            raise UpstreamTimeout("muse.ai new conversation navigation timed out") from None

        async def confirm_commit() -> bool:
            # Only a fresh blank target is unambiguous enough to recover without
            # the navigate response. Never issue another navigation or reload.
            if (
                before.get("url") != "about:blank"
                or not before.get("id")
                or not before.get("loaderId")
            ):
                return False
            deadline = time.monotonic() + 5.0
            candidate = None
            expected = urlsplit(muse.NEW_THREAD_URL)

            def matches(frame: dict) -> bool:
                try:
                    url = urlsplit(frame.get("url", ""))
                    return (
                        frame.get("id") == before["id"]
                        and bool(frame.get("loaderId"))
                        and frame["loaderId"] != before["loaderId"]
                        and not frame.get("unreachableUrl")
                        and (url.scheme, url.netloc, url.path)
                        == (expected.scheme, expected.netloc, expected.path)
                        and not url.query
                        and not url.fragment
                    )
                except ValueError:
                    return False

            async def read_frame() -> dict:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                return (await session.send("Page.getFrameTree", timeout=remaining))["frameTree"][
                    "frame"
                ]

            while time.monotonic() < deadline:
                if tab.session is not session or tab.target_id != target_id or session.closed:
                    return False
                try:
                    frame = await read_frame()
                    if frame.get("id") != before["id"]:
                        return False
                    if candidate and frame.get("loaderId") != candidate:
                        return False
                    if frame.get("loaderId") != before["loaderId"] and not matches(frame):
                        return False
                    if matches(frame):
                        candidate = frame["loaderId"]
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return False
                        state = await session.evaluate(dom.PAGE_STATE, timeout=remaining) or {}
                        current = await read_frame()
                        if not matches(current) or current.get("loaderId") != candidate:
                            return False
                        if (
                            state.get("ready") is True
                            and state.get("path") == expected.path
                            and tab.session is session
                            and tab.target_id == target_id
                            and not session.closed
                        ):
                            log.info("code=navigation_commit_confirmed_after_timeout")
                            return True
                except TimeoutError:
                    pass
                # Protocol/connection errors and cancellation deliberately propagate.
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(min(0.25, remaining))
            return False

        try:
            navigation = await session.send("Page.navigate", {"url": muse.NEW_THREAD_URL})
        except TimeoutError:
            if await confirm_commit():
                return
            raise UpstreamTimeout("muse.ai new conversation navigation timed out") from None
        if (
            navigation.get("errorText")
            or navigation.get("isDownload")
            or not navigation.get("frameId")
            or not navigation.get("loaderId")
        ):
            raise UpstreamError("muse.ai new conversation navigation did not start")
        expected_frame = navigation.get("frameId")
        expected_loader = navigation["loaderId"]
        expected_path = urlsplit(muse.NEW_THREAD_URL).path
        deadline = time.monotonic() + self.settings.page_ready_timeout
        state: dict = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            # A busy page can leave Runtime.evaluate unanswered while it loads; poll again.
            with contextlib.suppress(CDPError, TimeoutError):
                # navigate acknowledges the request before the old document is replaced.
                frame = (await tab.session.send("Page.getFrameTree", timeout=5.0))["frameTree"][
                    "frame"
                ]
                state = await tab.session.evaluate(dom.PAGE_STATE, timeout=5.0) or {}
                if frame.get("id") != expected_frame or frame.get("loaderId") != expected_loader:
                    continue
                if state.get("ready") and state.get("path") == expected_path:
                    # Do not accept a DOM snapshot spanning another document commit.
                    current = (await tab.session.send("Page.getFrameTree", timeout=5.0))[
                        "frameTree"
                    ]["frame"]
                    if (
                        current.get("id") == expected_frame
                        and current.get("loaderId") == expected_loader
                        and urlsplit(current.get("url", "")).path == expected_path
                    ):
                        return
        head = (state.get("head") or "").lower()
        if any(h in head for h in dom.LOGIN_HINTS):
            raise UpstreamAuthError("muse.ai redirected to login; cookies are no longer valid")
        raise UpstreamTimeout("muse.ai page did not become ready")

    # ------------------------------------------------------------ input
    async def _attach(self, tab: _Tab, images: list[InputImage]) -> None:
        for idx, img in enumerate(images):
            ext = img.mime.split("/")[-1].replace("jpeg", "jpg")
            res = await tab.session.evaluate(
                dom.attach_file(base64.b64encode(img.data).decode(), img.mime, f"input_{idx}.{ext}")
            )
            if not (res or {}).get("ok"):
                raise UpstreamError("failed to attach image (code=attachment_failed)")
        if images:
            await asyncio.sleep(1.0)

    async def _send(self, tab: _Tab, prompt: str) -> None:
        # This is a pre-submission budget, independent of the unchanged first-token
        # timer. Never retry a click whose outcome may already have been accepted.
        deadline = time.monotonic() + self.settings.page_ready_timeout

        async def evaluate(script: str):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UpstreamTimeout("composer readiness timed out (code=composer_ready_timeout)")
            try:
                return await tab.session.evaluate(script, timeout=min(5.0, remaining))
            except TimeoutError:
                error = UpstreamTimeout(
                    "composer readiness timed out (code=composer_ready_timeout)"
                )
                error.retryable = False
                raise error from None
            except CDPError:
                error = UpstreamError("submission uncertain (code=composer_cdp_uncertain)")
                error.retryable = False
                raise error from None

        status = await evaluate(dom.composer_probe(reset=True))
        filled = False
        while time.monotonic() < deadline:
            if not isinstance(status, dict):
                raise UpstreamError("unsupported composer (code=composer_probe_unsupported)")
            # Initial hydration may not expose the schema yet. Wait within the
            # same bounded budget, but never fill or click an unsupported DOM.
            if (
                status.get("supported") is True
                and status.get("ready") is True
                and status.get("stable") is True
            ):
                if not filled:
                    res = await evaluate(dom.fill_input(prompt))
                    if (res or {}).get("clicked"):
                        error = UpstreamError(
                            "submission uncertain (code=composer_already_clicked)"
                        )
                        error.retryable = False
                        raise error
                    filled = (res or {}).get("ok") is True
                else:
                    result = await evaluate(dom.click_send(prompt))
                    if result == "clicked":
                        return
                    if result not in ("refill", "disabled", "unsupported", "pending-input"):
                        error = UpstreamError(
                            "submission uncertain (code=composer_submission_uncertain)"
                        )
                        error.retryable = False
                        raise error
                    if result in ("refill", "unsupported"):
                        filled = False
            else:
                filled = False
            # Poll a condition, not a fixed startup delay. Node/context changes
            # reset the stable sample and require refilling before any click.
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(POLL_INTERVAL, remaining))
            status = await evaluate(dom.composer_probe())
        raise UpstreamTimeout("composer readiness timed out (code=composer_ready_timeout)")

    async def _state(self, tab: _Tab) -> dict:
        state = await tab.session.evaluate(dom.CHAT_STATE) or {}
        tail = state.get("tail", "")
        low = tail.lower()
        if hit := next((h for h in dom.QUOTA_HINTS if h in low), None):
            # The page tail also holds the prompt and the thread-history sidebar, and
            # matching it cooled down accounts whose images muse.ai went on to deliver.
            # Only a finished agent reply that says so counts; anything else is logged.
            reply = state.get("lastText", "")
            if not state.get("generating") and hit in reply.lower():
                log.warning(
                    "code=quota_exhausted generating=false reply_chars=%d tail_chars=%d",
                    len(reply),
                    len(tail),
                )
                raise UpstreamQuotaError("muse.ai reports usage limit (code=quota_exhausted)")
            if hit != tab.quota_note:
                tab.quota_note = hit
                log.info(
                    "code=quota_hint_ignored generating=%s reply_chars=%d tail_chars=%d",
                    bool(state.get("generating")),
                    len(reply),
                    len(tail),
                )
        return state

    async def _prepare(self, tab: _Tab, prompt: str, images: list[InputImage]) -> dict:
        await self._new_thread(tab)
        base = await self._state(tab)
        await self._attach(tab, images)
        await self._send(tab, prompt)
        return base

    async def _deny_approvals(self, tab: _Tab) -> None:
        denied = await tab.session.evaluate(dom.DENY_APPROVALS) or []
        if denied:
            log.info("code=approvals_denied count=%d", len(denied))

    # ------------------------------------------------------------ chat
    async def _live_continuation(self, tab: _Tab) -> bool:
        """This tab already shows the stored conversation, even if its URL is still /thread/new."""
        last_user = next((text for role, text in reversed(tab.turns) if role == "user"), "")
        if not last_user:
            return False
        try:
            page = await tab.session.evaluate(dom.PAGE_STATE) or {}
            if not page.get("hasInput"):
                return False
            state = await self._state(tab)
            present = await tab.session.evaluate(
                f"!!(document.body && document.body.innerText.includes({dom.q(last_user[:80])}))"
            )
        except CDPError:
            return False
        if state.get("generating"):
            return False
        if any(hint in state.get("tail", "") for hint in dom.STALL_HINTS):
            return False
        if (state.get("agentCount") or 0) >= HOT_BUBBLE_LIMIT:
            return False
        return bool(present)

    async def _open_saved(self, tab: _Tab) -> None:
        try:
            navigation = await tab.session.send("Page.navigate", {"url": tab.thread_url})
        except TimeoutError:
            raise UpstreamTimeout("saved conversation navigation timed out") from None
        if (
            navigation.get("errorText")
            or navigation.get("isDownload")
            or not navigation.get("frameId")
            or not navigation.get("loaderId")
        ):
            raise UpstreamError("saved conversation navigation did not start")
        expected_frame = navigation.get("frameId")
        expected_loader = navigation["loaderId"]
        expected_path = urlsplit(tab.thread_url).path
        deadline = time.monotonic() + self.settings.page_ready_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            with contextlib.suppress(CDPError, TimeoutError):
                frame = (await tab.session.send("Page.getFrameTree", timeout=5.0))["frameTree"][
                    "frame"
                ]
                if frame.get("id") != expected_frame or frame.get("loaderId") != expected_loader:
                    continue
                page = await tab.session.evaluate(dom.PAGE_STATE, timeout=5.0) or {}
                if (
                    not page.get("ready")
                    or page.get("path") != expected_path
                    or not await self._on_thread(tab)
                ):
                    continue
                current = (await tab.session.send("Page.getFrameTree", timeout=5.0))["frameTree"][
                    "frame"
                ]
                if (
                    current.get("id") == expected_frame
                    and current.get("loaderId") == expected_loader
                    and urlsplit(current.get("url", "")).path == expected_path
                ):
                    return
        raise UpstreamTimeout("saved conversation did not become ready")

    async def _begin_chat(self, account: Account, req: ChatRequest):
        """Send ``req`` on the saved conversation when it continues that page."""
        hint = req.conversation_hint or ""
        t0 = time.monotonic()
        tab = await self._checkout(account, prefer=lambda t: t.has_state and (t.hint or "") == hint)
        t_tab = time.monotonic()
        prompt, images = req.prompt, req.images
        try:
            # The saved conversation (restored after a restart) may only go into a
            # tab while no other tab of the account already holds a conversation.
            if not any(t.has_state for t in self._tabs.get(account.id, []) if t is not tab):
                self._load_hot(tab, account)
            same_owner = (tab.hint or "") == (req.conversation_hint or "")
            follow = followup_text(tab.turns, req.turns) if tab.turns and same_owner else None
            if (
                follow
                and len(req.images) >= tab.image_count
                and (tab.thread_url or await self._live_continuation(tab))
            ):
                if await self._on_thread(tab) or await self._live_continuation(tab):
                    log.info("code=hot_page_reused saved_thread=%s", bool(tab.thread_url))
                elif tab.thread_url:
                    log.info("code=hot_page_reopening")
                    await self._open_saved(tab)
                prompt = follow
                images = req.images[tab.image_count :]
            else:
                log.info("opening a new thread")
                saved = account.meta.get("hot_page")
                if isinstance(saved, dict) and (saved.get("hint") or "") == hint:
                    account.meta.pop("hot_page", None)
                await self._fresh_target(tab, account)
                await self._new_thread(tab)
            t_page = time.monotonic()
            base = await self._state(tab)
            await self._attach(tab, images)
            tab.used = True  # Set before any possibly accepted/cancelled submission.
            await self._send(tab, prompt)
            log.info(
                "chat timing: tab %.2fs, page %.2fs, send %.2fs",
                t_tab - t0,
                t_page - t_tab,
                time.monotonic() - t_page,
            )
            return tab, base
        except CDPError:
            await self._close_tab(tab)
            await self._checkin(tab)
            raise UpstreamError("browser error (code=cdp_failed)") from None
        except BaseException:
            # Submission may already have happened; clearing local history does
            # not make a still-running upstream page safe to reuse.
            tab.stale = True
            self._forget(tab)
            await self._finish_cleanup(self._checkin(tab))
            raise

    async def chat_stream(self, account: Account, req: ChatRequest) -> AsyncIterator[str]:
        tab, base = await self._begin_chat(account, req)
        base_count, base_text = base.get("agentCount", 0), base.get("lastText", "")
        started = time.monotonic()
        emitted, last, stable, got_first = "", None, 0, False
        finished = False
        try:
            while True:
                if req.cancel and req.cancel.is_set():
                    raise asyncio.CancelledError
                elapsed = time.monotonic() - started
                if elapsed > req.timeout:
                    raise UpstreamTimeout("assistant reply timed out")
                await asyncio.sleep(POLL_INTERVAL)
                st = await self._state(tab)
                if st.get("approval"):
                    stable = 0
                    await self._deny_approvals(tab)
                    continue
                text = st.get("lastText", "")
                is_new = st.get("agentCount", 0) > base_count or (text and text != base_text)
                if not is_new or not text:
                    stable = 0
                    # Inspect this snapshot before enforcing the soft first-token
                    # limit: a first completed reply may arrive with Stop gone.
                    # Keep waiting without text only while visibly generating.
                    if (
                        not got_first
                        and elapsed > req.first_token_timeout
                        and not st.get("generating")
                    ):
                        raise UpstreamTimeout("no first token from assistant")
                    if elapsed > 15 and any(h in st.get("tail", "") for h in dom.STALL_HINTS):
                        raise UpstreamTimeout("upstream workspace is stuck connecting")
                    continue
                if not got_first:
                    log.info("chat timing: first text %.2fs after send", elapsed)
                got_first = True
                if text != last:
                    delta, emitted = stream_delta(emitted, text)
                    if delta:
                        yield delta
                    last, stable = text, 0
                else:
                    # A tool/thinking pause is not completion. Require consecutive
                    # explicitly idle snapshots, bounded by the request deadline.
                    stable = stable + 1 if st.get("generating") is False else 0
                    if stable >= STABLE_POLLS_DONE:
                        delta, emitted = stream_delta(emitted, text, final=True)
                        if delta:
                            yield delta
                        finished = True
                        return
        except CDPError:
            await self._close_tab(tab)
            raise UpstreamError("browser error (code=cdp_failed)") from None
        finally:
            saved = False
            try:
                if finished:
                    url = ""
                    for _ in range(10):
                        url = await self._href(tab)
                        if is_thread_url(url):
                            break
                        await asyncio.sleep(0.2)
                    self._save_hot(tab, account, req, url if is_thread_url(url) else "")
                    saved = True
                    if tab.thread_url:
                        log.info("code=hot_page_saved")
                    else:
                        log.info("reply finished but the thread url was not ready yet")
            finally:
                if not saved:
                    # Closing the page prevents reuse, not necessarily upstream work.
                    tab.stale = True
                    self._forget(tab)
                await self._finish_cleanup(self._checkin(tab))

    # ------------------------------------------------------------ media
    @staticmethod
    def _media_prompt(prompt: str, kind: str, size: str | None, duration: int | None = None) -> str:
        hints = []
        if size:
            hints.append(f"aspect ratio {size}")
        if duration:
            hints.append(f"{duration} seconds long")
        verb = "Generate an image" if kind == "image" else "Generate a video"
        suffix = f" ({', '.join(hints)})" if hints else ""
        return f"{verb}{suffix}: {prompt}"

    # muse.ai posts a completion sentence ("Here's your video…") a few seconds
    # before the media attachment's blob src is ready, so a reply that is "text
    # only" right now may still be finalising. Keep waiting this long for the
    # attachment to appear/load before treating the reply as a refusal.
    # Video grace is deliberately short: the canvas polls a completed video for
    # ~5 minutes, so a long grace burns the client's whole window before any
    # recovery can start (a 90s grace + a 90s follow-up cost 338s and still failed).
    _MEDIA_GRACE = {"video": 40.0, "image": 8.0}
    # After the "please attach the file" follow-up, the answer is usually quick.
    _ASK_GRACE = {"video": 25.0, "image": 8.0}
    # The timeout is soft while muse.ai is visibly still working (stop button
    # shown or attachment loading): giving up then threw away images that landed
    # a minute later, and the failover started the whole generation over.
    _OVERRUN_FACTOR = 2.5

    async def _wait_media(
        self,
        tab: _Tab,
        base: dict,
        kind: str,
        timeout: float,
        on_progress,
        cancel: asyncio.Event | None,
    ) -> dict:
        grace = self._MEDIA_GRACE.get(kind, 8.0)
        w = MediaWait.from_base(
            base,
            kind=kind,
            timeout=timeout,
            grace=grace,
            ask_grace=self._ASK_GRACE.get(kind, grace),
            overrun_factor=self._OVERRUN_FACTOR,
            started=time.monotonic(),
        )
        while not w.expired(time.monotonic()):
            if cancel and cancel.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(0.6)
            st = await self._state(tab)
            step = w.step(st, time.monotonic())
            if step.act is Act.DENY:
                await self._deny_approvals(tab)
                continue
            if step.act is Act.RETURN:
                return step.attachment
            if on_progress:
                on_progress(w.progress(time.monotonic()))
            if step.act is Act.ASK:
                # The agent often finishes with the file only in its workspace:
                # after a denied upload, after post-processing that it shows as
                # an image gallery (raw, QC crops and final mixed together), or
                # when it recognises a repeat request and points at the earlier
                # file. Asking once gets the final file attached in chat.
                metrics.media_outcome(kind, "attachment_requested")
                log.info(
                    "%s not attached (%s); code=attachment_requested reply_chars=%d",
                    kind,
                    "denied approval" if step.denied else "text-only reply",
                    step.reply_chars,
                )
                await self._send(
                    tab,
                    f"Please attach the final {kind} file here in the chat"
                    + (" instead of uploading it." if step.denied else ", as a single attachment."),
                )
            elif step.act is Act.PROBE:
                # Last resort: media delivered as a link in the text bubble.
                probe = await tab.session.evaluate(dom.LAST_BUBBLE_MEDIA) or {}
                url = self._pick_media_url(probe.get("links", []), kind)
                if url:
                    metrics.media_outcome(kind, "media_link_detected")
                    log.info("%s code=media_link_detected", kind)
                    return {"src": url, "kind": kind}
                log.info(
                    "%s: no media after %.0fs grace; code=media_missing links_count=%d html_chars=%d",
                    kind,
                    grace,
                    len(probe.get("links", [])),
                    len(probe.get("html", "")),
                )
                metrics.media_outcome(kind, "media_missing")
                raise UpstreamRefused("upstream replied with text only (code=media_missing)")
        metrics.media_outcome(kind, "timeout")
        if w.busy:
            log.warning(
                "%s still generating after %.0fs; giving up", kind, time.monotonic() - w.started
            )
        raise UpstreamTimeout(f"{kind} generation timed out")

    _VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v")
    _IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif")
    _MEDIA_HOSTS = (
        "videodelivery.net",
        "cloudflarestream.com",
        "cloudflarestorage.com",
        "amazonaws.com",
        "storage.googleapis.com",
        "blob.core.windows.net",
        "musecdn",
        "cdn.muse",
        "muse-cdn",
    )
    _MEDIA_PATHS = ("/media/", "/download", "/dl/", "/video", "/attachment", "/files/")

    @classmethod
    def _pick_media_url(cls, links: list[str], kind: str) -> str | None:
        """Best media URL from a reply bubble, or None if nothing looks like one."""
        exts = cls._VIDEO_EXT if kind == "video" else cls._IMAGE_EXT

        def score(u: str) -> int:
            low = u.lower()
            path = low.split("?", 1)[0].split("#", 1)[0]
            if u.startswith("blob:") or path.endswith(exts):
                return 3
            if any(h in low for h in cls._MEDIA_HOSTS):
                return 2
            if any(seg in low for seg in cls._MEDIA_PATHS):
                return 1
            return 0

        cands = [u for u in links if u and not u.startswith(("mailto:", "javascript:"))]
        cands.sort(key=score, reverse=True)
        return cands[0] if cands and score(cands[0]) > 0 else None

    @staticmethod
    def _blob_download_script(url: str, limit: int) -> str:
        # Blob URLs have no network redirects. Refuse noncanonical origins, opaque
        # origins, query/fragment tricks and arbitrary schemes in both runtimes.
        pattern = r"blob:https://muse\.ai/[A-Za-z0-9_-]+"
        if not re.fullmatch(pattern, url):
            raise ValueError("untrusted media blob")
        return (
            "(async () => { const source = " + dom.q(url) + ";"
            r"if (!/^blob:https:\/\/muse\.ai\/[A-Za-z0-9_-]+$/.test(source)) "
            "throw new Error('untrusted media blob');"
            "const fetch = (url, options) => {"
            "if (url !== source) throw new Error('untrusted media blob');"
            "return globalThis.fetch(url, {...options, credentials: 'omit', redirect: 'error'}); };"
            "return await " + dom.fetch_as_base64(url, limit) + "; })()"
        )

    async def _download(self, tab: _Tab, att: dict, kind: str) -> MediaResult:
        try:
            return await self._download_checked(tab, att, kind)
        except Exception:
            # URL, DNS, HTTP and renderer errors can contain signed URLs or page
            # data. Never expose their text or retry already accepted generation.
            error = UpstreamError("generated media download failed (code=media_download_failed)")
            error.retryable = False
            raise error from None

    async def _download_checked(self, tab: _Tab, att: dict, kind: str) -> MediaResult:
        limit = max(1, getattr(self.settings, "browser_media_max_bytes", 33554432))
        source = att["src"]
        if not isinstance(source, str) or kind not in ("image", "video"):
            raise ValueError("invalid media source")
        if not source.startswith("blob:"):
            parsed = urlsplit(source)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or "#" in source
                or any(ord(c) <= 32 or ord(c) == 127 for c in source)
                or "\\" in source
            ):
                raise ValueError("invalid media URL")
            data, mime = await download_public_media(source, timeout=20, max_bytes=limit, kind=kind)
            if len(data) > limit:
                raise ValueError("media exceeds byte limit")
            log.info("code=media_downloaded bytes=%d", len(data))
            return MediaResult(
                data=data,
                mime=mime,
                kind=kind,
                width=att.get("w") or None,
                height=att.get("h") or None,
            )
        res = await tab.session.evaluate(
            self._blob_download_script(source, limit), await_promise=True, timeout=300
        )
        if not (res or {}).get("ok"):
            error = (res or {}).get("err", "")
            code = (
                "media_byte_limit"
                if error == "Error: media exceeds byte limit"
                else "media_download_failed"
            )
            log.warning("code=%s", code)
            raise UpstreamError(f"failed to download generated media (code={code})")
        encoded = res.get("b64", "")
        if len(encoded) > 4 * ((limit + 2) // 3):
            raise UpstreamError("generated media exceeds byte limit")
        data = base64.b64decode(encoded, validate=True)
        if len(data) > limit:
            raise UpstreamError("generated media exceeds byte limit")
        mime = res.get("mime") or ("video/mp4" if kind == "video" else "image/png")
        log.info("code=media_downloaded bytes=%d", len(data))
        return MediaResult(
            data=data,
            mime=mime,
            kind=kind,
            width=att.get("w") or None,
            height=att.get("h") or None,
        )

    # Video replies that arrive as text only (no attachment) are an upstream
    # flake, not a parameter problem: the same prompt and parameters succeed on
    # a plain re-submission. Retry inside the same account, bounded by a budget
    # that still fits the client's ~5 minute poll window.
    _VIDEO_RETRY_MIN_LEFT = 100.0

    async def _generate(
        self,
        account: Account,
        prompt: str,
        images: list[InputImage],
        kind: str,
        timeout: float,
        on_progress,
        cancel,
    ) -> MediaResult:
        # Media always starts a fresh thread, so keep tabs holding a chat for chats.
        tab = await self._checkout(account, prefer=lambda t: not t.has_state)
        finished = False
        retries = getattr(self.settings, "video_text_only_retries", 1) if kind == "video" else 0
        budget = timeout
        if kind == "video":
            budget = min(timeout, getattr(self.settings, "video_deadline_cap", 600.0))
        deadline = time.monotonic() + budget
        try:
            await self._fresh_target(tab, account)
            tab.used = True  # _prepare includes submission; history stays empty for media.
            attempt = 0
            while True:
                attempt += 1
                base = await self._prepare(tab, prompt, images)
                remaining = deadline - time.monotonic()
                if remaining <= 1.0:
                    raise UpstreamTimeout(f"{kind} generation timed out")
                try:
                    att = await self._wait_media(tab, base, kind, remaining, on_progress, cancel)
                except UpstreamRefused:
                    left = deadline - time.monotonic()
                    if attempt <= retries and left >= self._VIDEO_RETRY_MIN_LEFT:
                        metrics.media_outcome(kind, "retry_text_only")
                        log.info(
                            "%s re-submitting after text-only reply; "
                            "code=retry_text_only attempt=%d left=%.0fs",
                            kind,
                            attempt + 1,
                            left,
                        )
                        continue
                    raise
                result = await self._download(tab, att, kind)
                metrics.media_outcome(
                    kind, "succeeded" if attempt == 1 else "succeeded_after_retry"
                )
                finished = True
                return result
        except CDPError:
            await self._close_tab(tab)
            raise UpstreamError("browser error (code=cdp_failed)") from None
        finally:
            if not finished:
                tab.stale = True
            await self._finish_cleanup(self._checkin(tab))

    async def generate_image(self, account: Account, req: ImageRequest) -> list[MediaResult]:
        prompt = self._media_prompt(req.prompt, "image", req.size)
        results = []
        for _ in range(max(1, req.n)):
            r = await self._generate(
                account,
                prompt,
                req.reference_images,
                "image",
                req.timeout,
                req.on_progress,
                req.cancel,
            )
            r.revised_prompt = req.prompt
            results.append(r)
        return results

    async def generate_video(self, account: Account, req: VideoRequest) -> MediaResult:
        prompt = self._media_prompt(req.prompt, "video", req.size, req.duration)
        images = [req.first_frame] if req.first_frame else []
        return await self._generate(
            account, prompt, images, "video", req.timeout, req.on_progress, req.cancel
        )

    # ------------------------------------------------------------ session
    async def renew_session(self, account: Account) -> SessionInfo:
        # the pool merges rotated credentials after this returns; checkout detects
        # that change instead of opening a context with the pre-renewal cookie jar.
        if account.credential_records:
            from .session_auth import SessionProbeError, probe_session

            try:
                project_records(account.credential_records)
            except ValueError:
                raise UpstreamAuthError("session credentials are expired or invalid") from None
            try:
                records, subject = await probe_session(self, account.credential_records)
            except SessionProbeError as exc:
                if exc.status == "awaiting_login":
                    raise UpstreamAuthError("session rejected by upstream") from None
                raise UpstreamError("session renewal temporarily unavailable") from None
            return SessionInfo(
                ok=True,
                credential_records=records,
                meta={"credential_subject": subject} if subject else {},
            )
        return await muse.renew_session(account.cookies)
