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
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from ...accounts.model import Account
from ...config import Settings
from ...core.prompt import followup_text
from ...errors import (
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

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.15
STABLE_POLLS_DONE = 4
STABLE_POLLS_FORCE = 12
# Past this many assistant bubbles the page gets slow and a new thread is cheaper.
HOT_BUBBLE_LIMIT = 16


def is_thread_url(url: str) -> bool:
    """A real muse conversation, not the empty "new thread" page."""
    path = urlsplit(url).path.rstrip("/")
    return path.startswith("/thread/") and path != "/thread/new"


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
    """Last ignored quota-hint snippet, so a page polled every 0.6s logs it once."""
    busy: bool = False
    stale: bool = False
    """Cookies were renewed; close this tab once it is idle."""
    closed: bool = False

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

    # ------------------------------------------------------------ lifecycle
    async def startup(self) -> None:
        profile = self.settings.profile_dir.resolve()
        if not profile.is_relative_to(self.settings.data_dir.resolve()):
            raise ValueError("browser profile must be inside the configured data_dir")
        self._closing = False
        exe = find_chromium(self.settings.chromium_path)
        self._chromium = ChromiumProcess(
            exe,
            self.settings.cdp_port,
            self.settings.profile_dir,
            self.settings.headless,
            proxy=self.settings.browser_proxy,
        )
        try:
            ws_url = await self._chromium.start()
            self._browser = await CDPSession.connect(ws_url)
        except BaseException:
            await self._chromium.stop()
            raise
        log.info("browser driver ready")

    async def shutdown(self) -> None:
        """drain owned contexts before disconnecting from Chromium."""
        self._closing = True
        async with self._ctx_lock:
            await self._finish_cleanup(self._shutdown_locked())
        await self._notify_tabs()

    async def _shutdown_locked(self) -> None:
        """close tabs and orphan contexts while preserving process ownership."""
        try:
            for tabs in list(self._tabs.values()):
                for tab in list(tabs):
                    await self._dispose_tab_locked(tab)
            for context_id in set(self._contexts.values()) | self._retired_contexts:
                await self._dispose_context(context_id)
            self._contexts.clear()
            self._retired_contexts.clear()
            self._fingerprints.clear()
        finally:
            try:
                if self._browser:
                    await self._browser.close()
            finally:
                if self._chromium:
                    await self._chromium.stop()
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

    # ------------------------------------------------------------ tabs
    @staticmethod
    def _fingerprint(account: Account) -> str:
        payload = json.dumps([account.cookies, account.cookie_expires], sort_keys=True)
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
                    snapshot = account.model_copy(deep=True)
                    opening = asyncio.create_task(self._open_tab(snapshot))
                    try:
                        tab = await asyncio.shield(opening)
                    except asyncio.CancelledError:
                        await self._finish_cleanup(self._discard_opening(opening))
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
            await self._set_cookies(session, account)
            return _Tab(account.id, context_id, target_id, session)
        except BaseException:
            if session:
                with contextlib.suppress(Exception):
                    await session.close()
            if target_id:
                with contextlib.suppress(Exception):
                    await self._browser.send("Target.closeTarget", {"targetId": target_id})
            if not any(t.context_id == context_id for t in self._tabs.get(account.id, [])):
                if self._contexts.get(account.id) == context_id:
                    self._contexts.pop(account.id, None)
                await self._dispose_context(context_id)
            raise

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
        floor = time.time() + 3600
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
                # Never hand Chromium a past expiry; it would drop the cookie silently.
                "expires": exp if exp > floor else time.time() + 7 * 86400,
            }
            await session.send("Network.setCookie", params)

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
        await tab.session.send("Page.navigate", {"url": muse.NEW_THREAD_URL})
        deadline = time.monotonic() + self.settings.page_ready_timeout
        state: dict = {}
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            # A busy page can leave Runtime.evaluate unanswered while it loads; poll again.
            with contextlib.suppress(CDPError, TimeoutError):
                state = await tab.session.evaluate(dom.PAGE_STATE, timeout=5.0) or {}
                if state.get("ready"):
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
                raise UpstreamError(f"failed to attach image: {(res or {}).get('err')}")
        if images:
            await asyncio.sleep(1.0)

    async def _send(self, tab: _Tab, prompt: str) -> None:
        res = await tab.session.evaluate(dom.fill_input(prompt))
        if not (res or {}).get("ok"):
            raise UpstreamError(f"cannot fill chat input: {(res or {}).get('err')}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if await tab.session.evaluate(dom.CLICK_SEND) == "clicked":
                return
            await asyncio.sleep(0.1)
        for kind in ("keyDown", "keyUp"):
            await tab.session.send(
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    **({"text": "\r"} if kind == "keyDown" else {}),
                },
            )
        await asyncio.sleep(0.5)
        if not await tab.session.evaluate(dom.INPUT_EMPTY):
            raise UpstreamError("send button not found and Enter did not submit")

    async def _state(self, tab: _Tab) -> dict:
        state = await tab.session.evaluate(dom.CHAT_STATE) or {}
        tail = state.get("tail", "")
        low = tail.lower()
        if hit := next((h for h in dom.QUOTA_HINTS if h in low), None):
            i = low.find(hit)
            snippet = " ".join(tail[max(0, i - 120) : i + 120].split())
            # The page tail also holds the prompt and the thread-history sidebar, and
            # matching it cooled down accounts whose images muse.ai went on to deliver.
            # Only a finished agent reply that says so counts; anything else is logged.
            reply = state.get("lastText", "")
            if not state.get("generating") and hit in reply.lower():
                log.warning(
                    "quota hint %r in reply on %s: %s", hit, tab.thread_url or "new thread", snippet
                )
                raise UpstreamQuotaError(
                    f"muse.ai reports the account is out of quota: …{snippet}…"
                )
            if snippet != tab.quota_note:
                tab.quota_note = snippet
                log.info(
                    "ignoring quota hint %r outside the reply on %s: %s",
                    hit,
                    tab.thread_url or "new thread",
                    snippet,
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
        for what in denied:
            log.info("denied muse.ai %s", what.lower())

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
        await tab.session.send("Page.navigate", {"url": tab.thread_url})
        deadline = time.monotonic() + self.settings.page_ready_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if await self._on_thread(tab):
                return
        raise UpstreamTimeout("saved conversation did not become ready")

    async def _begin_chat(self, account: Account, req: ChatRequest):
        """Send ``req`` on the saved conversation when it continues that page."""
        hint = req.conversation_hint or ""
        tab = await self._checkout(account, prefer=lambda t: t.has_state and (t.hint or "") == hint)
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
                    log.info("reusing hot page %s", tab.thread_url or "open page")
                elif tab.thread_url:
                    log.info("reopening %s", tab.thread_url)
                    await self._open_saved(tab)
                prompt = follow
                images = req.images[tab.image_count :]
            else:
                log.info("opening a new thread")
                saved = account.meta.get("hot_page")
                if isinstance(saved, dict) and (saved.get("hint") or "") == hint:
                    account.meta.pop("hot_page", None)
                await self._new_thread(tab)
            base = await self._state(tab)
            await self._attach(tab, images)
            await self._send(tab, prompt)
            return tab, base
        except CDPError as exc:
            await self._close_tab(tab)
            await self._checkin(tab)
            raise UpstreamError(f"browser error: {exc}") from exc
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
                if not got_first and elapsed > req.first_token_timeout:
                    raise UpstreamTimeout("no first token from assistant")
                await asyncio.sleep(POLL_INTERVAL)
                st = await self._state(tab)
                if st.get("approval"):
                    await self._deny_approvals(tab)
                    continue
                text = st.get("lastText", "")
                is_new = st.get("agentCount", 0) > base_count or (text and text != base_text)
                if not is_new or not text:
                    if elapsed > 15 and any(h in st.get("tail", "") for h in dom.STALL_HINTS):
                        raise UpstreamTimeout("upstream workspace is stuck connecting")
                    continue
                got_first = True
                if text != last:
                    delta = text[len(emitted) :] if text.startswith(emitted) else text
                    if delta:
                        emitted = text
                        yield delta
                    last, stable = text, 0
                else:
                    stable += 1
                    if (
                        not st.get("generating") and stable >= STABLE_POLLS_DONE
                    ) or stable >= STABLE_POLLS_FORCE:
                        finished = True
                        return
        except CDPError as exc:
            await self._close_tab(tab)
            raise UpstreamError(f"browser error: {exc}") from exc
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
                        log.info("saved hot page %s", tab.thread_url)
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
    _MEDIA_GRACE = {"video": 90.0, "image": 8.0}
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
        base_atts = len(base.get("attachments", []))
        base_count = base.get("agentCount", 0)
        grace = self._MEDIA_GRACE.get(kind, 8.0)
        started = time.monotonic()
        text_done_at: float | None = None
        denied = asked_to_attach = busy = False
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= timeout and not (busy and elapsed < timeout * self._OVERRUN_FACTOR):
                break
            if cancel and cancel.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(0.6)
            st = await self._state(tab)
            if st.get("approval"):
                await self._deny_approvals(tab)
                denied, text_done_at = True, None
                continue
            new_atts = st.get("attachments", [])[base_atts:]
            fresh = [a for a in new_atts if a.get("src") and a.get("kind") == kind]
            if fresh:
                return fresh[-1]
            busy = bool(st.get("generating")) or any(kind in (a.get("tid") or "") for a in new_atts)
            elapsed = time.monotonic() - started
            if on_progress:
                on_progress(min(95, int(elapsed / timeout * 100)))
            # An attachment node for this kind is on the page but its (blob) src
            # has not loaded yet -> media is still finalising, keep waiting.
            if any(kind in (a.get("tid") or "") for a in new_atts):
                text_done_at = None
                continue
            # No attachment yet. The reply may be genuinely text-only (a refusal),
            # or the attachment may simply lag the completion sentence.
            text = st.get("lastText", "")
            if st.get("agentCount", 0) > base_count and text and not st.get("generating"):
                if text_done_at is None:
                    text_done_at = time.monotonic()
                elif time.monotonic() - text_done_at >= grace:
                    if not asked_to_attach:
                        # The agent often finishes with the file only in its workspace:
                        # after a denied upload, after post-processing that it shows as
                        # an image gallery (raw, QC crops and final mixed together), or
                        # when it recognises a repeat request and points at the earlier
                        # file. Asking once gets the final file attached in chat.
                        log.info(
                            "%s not attached (%s); asking for it in chat: %.120s",
                            kind,
                            "denied approval" if denied else "text-only reply",
                            text,
                        )
                        await self._send(
                            tab,
                            f"Please attach the final {kind} file here in "
                            "the chat"
                            + (
                                " instead of uploading it."
                                if denied
                                else ", as a single attachment."
                            ),
                        )
                        asked_to_attach, text_done_at = True, None
                        base_count = st.get("agentCount", 0)
                        continue
                    # Last resort: media delivered as a link in the text bubble.
                    probe = await tab.session.evaluate(dom.LAST_BUBBLE_MEDIA) or {}
                    url = self._pick_media_url(probe.get("links", []), kind)
                    if url:
                        log.info("%s delivered as link in text bubble: %s", kind, url)
                        return {"src": url, "kind": kind}
                    log.info(
                        "%s: no media after %.0fs grace; links=%s html=%.300s",
                        kind,
                        grace,
                        probe.get("links", []),
                        probe.get("html", ""),
                    )
                    raise UpstreamRefused(f"upstream replied with text only: {text[:200]}")
            else:
                text_done_at = None
        if busy:
            log.warning("%s still generating after %.0fs; giving up", kind, elapsed)
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

    async def _download(self, tab: _Tab, att: dict, kind: str) -> MediaResult:
        res = await tab.session.evaluate(
            dom.fetch_as_base64(att["src"]), await_promise=True, timeout=300
        )
        if not (res or {}).get("ok"):
            raise UpstreamError(f"failed to download generated {kind}: {(res or {}).get('err')}")
        mime = res.get("mime") or ("video/mp4" if kind == "video" else "image/png")
        return MediaResult(
            data=base64.b64decode(res["b64"]),
            mime=mime,
            kind=kind,
            width=att.get("w") or None,
            height=att.get("h") or None,
        )

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
        try:
            base = await self._prepare(tab, prompt, images)
            att = await self._wait_media(tab, base, kind, timeout, on_progress, cancel)
            result = await self._download(tab, att, kind)
            finished = True
            return result
        except CDPError as exc:
            await self._close_tab(tab)
            raise UpstreamError(f"browser error: {exc}") from exc
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
        return await muse.renew_session(account.cookies)
