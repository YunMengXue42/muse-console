"""Navigation barriers exercised offline, without a browser or account data."""

import asyncio
import logging
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from muse_console.drivers.browser import dom
from muse_console.drivers.browser import driver as mod
from muse_console.drivers.browser.cdp import CDPError
from muse_console.drivers.browser.driver import BrowserDriver, _Tab
from muse_console.errors import UpstreamAuthError, UpstreamError, UpstreamTimeout
from muse_console.upstream import muse


class Session:
    def __init__(self, url, frames, navigation=None):
        self.url = url
        self.frames = list(frames)
        self.current = {}
        self.navigation = (
            {"frameId": "main", "loaderId": "new"} if navigation is None else navigation
        )
        self.calls = []
        self.pages = []
        self.closed = False
        self.navigated = False
        self.baseline = frame("about:blank", "blank")

    async def send(self, method, params=None, **kwargs):
        self.calls.append((method, params))
        if method == "Page.navigate":
            assert params == {"url": self.url}
            self.navigated = True
            if isinstance(self.navigation, BaseException):
                raise self.navigation
            return self.navigation
        assert method == "Page.getFrameTree"
        if not self.navigated:
            return {"frameTree": {"frame": self.baseline}}
        item = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        if isinstance(item, BaseException):
            raise item
        self.current = item
        return {"frameTree": {"frame": item}}

    async def evaluate(self, expression, **kwargs):
        if expression == dom.PAGE_STATE:
            page = {
                "ready": self.current.get("ready", True),
                "hasInput": True,
                "path": self.current.get("path", urlsplit(self.url).path),
                "head": self.current.get("head", ""),
            }
            self.pages.append((self.current.get("loaderId"), page))
            return page
        if expression == "location.href":
            return self.current.get("url", self.url)
        assert expression == dom.CHAT_STATE
        return {"generating": self.current.get("generating", False), "agentCount": 0}


def frame(url, loader="new", **kwargs):
    return {"id": "main", "loaderId": loader, "url": url, **kwargs}


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]

    async def sleep(delay):
        now[0] += delay

    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    return now


def setup_navigation(kind, frames, navigation=None):
    url = muse.NEW_THREAD_URL if kind == "new" else "https://muse.ai/thread/saved"
    session = Session(url, frames, navigation)
    driver = BrowserDriver(SimpleNamespace(page_ready_timeout=2))
    tab = _Tab("offline", "context", "target", session, thread_url=url)
    return driver, tab, driver._new_thread if kind == "new" else driver._open_saved


@pytest.mark.parametrize("kind", ["new", "saved"])
async def test_old_ready_same_path_must_wait_for_target_loader(kind, clock):
    driver, tab, navigate = setup_navigation(kind, [])
    url = tab.session.url
    tab.session.frames = [frame(url, "old"), frame(url), frame(url)]
    await navigate(tab)
    assert clock[0] == 0.5  # old document was ready at the exact same URL
    assert len([c for c in tab.session.calls if c[0] == "Page.navigate"]) == 1
    assert not tab.has_state if kind == "new" else tab.thread_url == url


@pytest.mark.parametrize("kind", ["new", "saved"])
@pytest.mark.parametrize("fault", ["old", "frame", "path", "not_ready", "changed", "busy"])
async def test_unacceptable_documents_time_out_without_renavigation(kind, fault, clock):
    _, tab, navigate = setup_navigation(kind, [])
    url = tab.session.url
    good = frame(url)
    bad = {
        "old": frame(url, "old"),
        "frame": frame(url, id="other"),
        "path": frame(url + "/extra", path=urlsplit(url).path + "/extra"),
        "not_ready": frame(url, ready=False),
        "changed": frame(url, "replacement"),
        "busy": frame(url, generating=True, ready=False),
    }[fault]
    # A commit between the DOM snapshot and final frame check must also fail.
    tab.session.frames = [good, bad] if fault == "changed" else [bad]
    with pytest.raises(UpstreamTimeout):
        await navigate(tab)
    assert len([c for c in tab.session.calls if c[0] == "Page.navigate"]) == 1


@pytest.mark.parametrize("kind", ["new", "saved"])
async def test_target_document_must_finish_dom_readiness(kind, clock):
    _, tab, navigate = setup_navigation(kind, [])
    url = tab.session.url
    tab.session.frames = [frame(url, ready=False), frame(url), frame(url)]
    await navigate(tab)
    assert clock[0] == 0.5


@pytest.mark.parametrize("kind", ["new", "saved"])
@pytest.mark.parametrize("error", [CDPError("context replaced"), TimeoutError()])
async def test_transient_poll_errors_are_retried_not_navigation(kind, error, clock):
    _, tab, navigate = setup_navigation(kind, [])
    tab.session.frames = [error, frame(tab.session.url), frame(tab.session.url)]
    await navigate(tab)
    assert clock[0] == 0.5
    assert len([c for c in tab.session.calls if c[0] == "Page.navigate"]) == 1


@pytest.mark.parametrize("kind", ["new", "saved"])
@pytest.mark.parametrize(
    "result",
    [
        {"errorText": "failed"},
        {"frameId": "main"},
        {"loaderId": "new"},
        {"frameId": "main", "loaderId": "new", "isDownload": True},
    ],
)
async def test_failed_or_unidentified_navigation_fails_closed(kind, result, clock):
    _, tab, navigate = setup_navigation(kind, [], result)
    with pytest.raises(UpstreamError):
        await navigate(tab)
    assert [c[0] for c in tab.session.calls] == (
        ["Page.getFrameTree", "Page.navigate"] if kind == "new" else ["Page.navigate"]
    )


async def test_new_login_redirect_keeps_auth_error(clock):
    _, tab, navigate = setup_navigation("new", [])
    tab.session.frames = [
        frame("https://muse.ai/login", "redirect", path="/login", head=dom.LOGIN_HINTS[0])
    ]
    with pytest.raises(UpstreamAuthError):
        await navigate(tab)


@pytest.mark.parametrize("kind", ["new", "saved"])
async def test_navigation_timeout_is_sanitized_and_never_retried(kind, monkeypatch):
    _, tab, navigate = setup_navigation(kind, [])
    calls = []

    async def timeout(method, params=None, **kwargs):
        calls.append(method)
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": frame(tab.session.url, "old")}}
        raise TimeoutError("PRIVATE_URL_COOKIE_PROMPT_SENTINEL")

    monkeypatch.setattr(tab.session, "send", timeout)
    with pytest.raises(UpstreamTimeout) as caught:
        await navigate(tab)
    expected = (
        "muse.ai new conversation navigation timed out"
        if kind == "new"
        else "saved conversation navigation timed out"
    )
    assert str(caught.value) == expected
    assert caught.value.__suppress_context__
    assert caught.value.__cause__ is None
    assert calls == (["Page.getFrameTree", "Page.navigate"] if kind == "new" else ["Page.navigate"])
    assert not tab.session.pages


async def test_saved_still_rejects_generating_conversation(clock):
    _, tab, navigate = setup_navigation("saved", [])
    tab.session.frames = [frame(tab.session.url, generating=True)]
    with pytest.raises(UpstreamTimeout):
        await navigate(tab)


async def test_timed_out_blank_navigation_commit_confirmed_once(clock, caplog):
    caplog.set_level(logging.INFO)
    _, tab, navigate = setup_navigation("new", [], TimeoutError("PRIVATE_SENTINEL"))
    url = tab.session.url
    tab.session.frames = [
        frame("about:blank", "blank"),
        frame(url, ready=False),
        frame(url),
        frame(url),
    ]
    await navigate(tab)
    assert clock[0] <= 5
    assert sum(c[0] == "Page.navigate" for c in tab.session.calls) == 1
    assert "code=navigation_commit_confirmed_after_timeout" in caplog.text
    assert "PRIVATE_SENTINEL" not in caplog.text
    assert url not in caplog.text


@pytest.mark.parametrize(
    "fault",
    [
        "old",
        "origin",
        "path",
        "query",
        "fragment",
        "unreachable",
        "frame",
        "changed",
        "not_ready",
        "busy",
    ],
)
async def test_timeout_confirmation_fails_closed(clock, fault, caplog):
    _, tab, navigate = setup_navigation("new", [], TimeoutError("PRIVATE_SENTINEL"))
    url = tab.session.url
    good = frame(url)
    bad = {
        "old": frame(url, "blank"),
        "origin": frame("https://other.invalid" + urlsplit(url).path),
        "path": frame(url + "/other"),
        "query": frame(url + "?PRIVATE_SENTINEL"),
        "fragment": frame(url + "#PRIVATE_SENTINEL"),
        "unreachable": frame(url, unreachableUrl=url),
        "frame": frame(url, id="other"),
        "changed": frame(url, "replacement"),
        "not_ready": frame(url, ready=False),
        "busy": TimeoutError("PRIVATE_SENTINEL"),
    }[fault]
    tab.session.frames = [good, bad] if fault == "changed" else [bad]
    with pytest.raises(UpstreamTimeout, match="new conversation navigation timed out") as error:
        await navigate(tab)
    assert clock[0] <= 5
    assert sum(c[0] == "Page.navigate" for c in tab.session.calls) == 1
    assert "PRIVATE_SENTINEL" not in caplog.text + str(error.value)


async def test_same_path_baseline_never_recovers(clock):
    _, tab, navigate = setup_navigation("new", [], TimeoutError())
    tab.session.baseline = frame(tab.session.url, "old")
    with pytest.raises(UpstreamTimeout):
        await navigate(tab)
    assert [c[0] for c in tab.session.calls] == ["Page.getFrameTree", "Page.navigate"]


@pytest.mark.parametrize("change", ["target", "session", "closed"])
async def test_confirmation_rejects_target_identity_change(clock, monkeypatch, change):
    _, tab, navigate = setup_navigation("new", [], TimeoutError())
    session = tab.session
    session.frames = [frame(session.url)]
    original = session.evaluate

    async def evaluate(*args, **kwargs):
        result = await original(*args, **kwargs)
        if change == "target":
            tab.target_id = "other"
        elif change == "session":
            tab.session = SimpleNamespace()
        else:
            session.closed = True
        return result

    monkeypatch.setattr(session, "evaluate", evaluate)
    with pytest.raises(UpstreamTimeout):
        await navigate(tab)
    assert sum(c[0] == "Page.navigate" for c in session.calls) == 1


@pytest.mark.parametrize("failure", [CDPError("protocol"), asyncio.CancelledError()])
async def test_confirmation_protocol_error_and_cancel_propagate(clock, failure):
    _, tab, navigate = setup_navigation("new", [failure], TimeoutError())
    with pytest.raises(type(failure)):
        await navigate(tab)
    assert sum(c[0] == "Page.navigate" for c in tab.session.calls) == 1


async def test_baseline_timeout_does_not_navigate(clock, monkeypatch):
    _, tab, navigate = setup_navigation("new", [])
    calls = []

    async def send(method, **kwargs):
        calls.append(method)
        raise TimeoutError("PRIVATE_SENTINEL")

    monkeypatch.setattr(tab.session, "send", send)
    with pytest.raises(UpstreamTimeout) as error:
        await navigate(tab)
    assert calls == ["Page.getFrameTree"]
    assert "PRIVATE_SENTINEL" not in str(error.value)


async def test_confirmation_rpc_time_is_part_of_five_second_budget(clock, monkeypatch):
    _, tab, navigate = setup_navigation("new", [], TimeoutError())
    session = tab.session
    original = session.send
    budgets = []

    async def send(method, params=None, **kwargs):
        if method == "Page.getFrameTree" and session.navigated:
            budgets.append(kwargs["timeout"])
            clock[0] += kwargs["timeout"]
            raise TimeoutError("PRIVATE_SENTINEL")
        return await original(method, params, **kwargs)

    monkeypatch.setattr(session, "send", send)
    with pytest.raises(UpstreamTimeout, match="new conversation navigation timed out"):
        await navigate(tab)
    assert clock[0] == 5
    assert budgets == [5]
    assert sum(c[0] == "Page.navigate" for c in session.calls) == 1


@pytest.mark.parametrize("failure", [CDPError("protocol"), asyncio.CancelledError()])
async def test_navigate_error_or_cancellation_does_not_enter_confirmation(clock, failure):
    _, tab, navigate = setup_navigation("new", [], failure)
    with pytest.raises(type(failure)):
        await navigate(tab)
    assert [c[0] for c in tab.session.calls] == ["Page.getFrameTree", "Page.navigate"]
