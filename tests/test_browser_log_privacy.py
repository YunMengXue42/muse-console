"""Browser diagnostics must not retain page content or signed URLs."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from muse_console.drivers.browser import dom
from muse_console.drivers.browser.cdp import CDPError, CDPSession
from muse_console.drivers.browser.driver import BrowserDriver, _Tab
from muse_console.errors import UpstreamError, UpstreamQuotaError, UpstreamRefused

SECRET = "PRIVATE_USER_CONTENT"
SIGNED_URL = "https://example.invalid/media/result.png?signature=PRIVATE_SIGNATURE"


class Session:
    closed = False

    def __init__(self, state=None, links=None):
        self.state = state or {}
        self.links = links or []

    async def evaluate(self, expression, **kwargs):
        if expression == dom.CHAT_STATE:
            return dict(self.state)
        if expression == dom.DENY_APPROVALS:
            return [SECRET]
        if expression == dom.LAST_BUBBLE_MEDIA:
            return {"links": self.links, "html": SECRET + SIGNED_URL}
        return {}

    async def fetch_base64(self, *args):
        return {"ok": False, "err": SECRET + SIGNED_URL}


def assert_private(caplog, error=None):
    output = caplog.text + (str(error) if error else "")
    assert SECRET not in output
    assert SIGNED_URL not in output
    assert "PRIVATE_SIGNATURE" not in output


async def test_quota_and_approvals_log_only_status_and_lengths(caplog):
    caplog.set_level(logging.INFO)
    driver = BrowserDriver(SimpleNamespace())
    state = {"tail": SECRET + " usage limit " + SIGNED_URL, "lastText": SECRET, "generating": True}
    tab = _Tab("a", "ctx", "target", Session(state), thread_url=SIGNED_URL)
    await driver._state(tab)
    await driver._deny_approvals(tab)
    state.update(lastText=SECRET + " usage limit", generating=False)
    with pytest.raises(UpstreamQuotaError) as caught:
        await driver._state(tab)
    assert_private(caplog, caught.value)
    assert "reply_chars=" in caplog.text
    assert "code=quota_exhausted" in caplog.text
    assert "code=approvals_denied count=1" in caplog.text


@pytest.mark.parametrize("link_found", [True, False])
async def test_media_diagnostics_do_not_log_reply_html_or_urls(caplog, monkeypatch, link_found):
    caplog.set_level(logging.INFO)
    driver = BrowserDriver(SimpleNamespace())
    driver._MEDIA_GRACE = {"image": 0}
    tab = _Tab(
        "a",
        "ctx",
        "target",
        Session(
            {"agentCount": 1, "lastText": SECRET, "generating": False},
            [SIGNED_URL] if link_found else ["https://example.invalid/" + SECRET],
        ),
    )
    sent = []

    async def send(*args):
        sent.append(1)
        tab.session.state["agentCount"] += 1

    real_sleep = asyncio.sleep
    monkeypatch.setattr(
        "muse_console.drivers.browser.driver.asyncio.sleep", lambda _: real_sleep(0)
    )
    monkeypatch.setattr(driver, "_send", send)
    if link_found:
        assert (await driver._wait_media(tab, {}, "image", 10, None, None))["src"] == SIGNED_URL
    else:
        with pytest.raises(UpstreamRefused) as caught:
            await driver._wait_media(tab, {}, "image", 10, None, None)
        assert_private(caplog, caught.value)
        assert "html_chars=" in caplog.text
    assert len(sent) == 1
    assert_private(caplog)


async def test_download_error_does_not_propagate_page_error(caplog):
    driver = BrowserDriver(SimpleNamespace())
    tab = _Tab("a", "ctx", "target", Session())
    with pytest.raises(UpstreamError) as caught:
        await driver._download(tab, {"src": SIGNED_URL}, "image")
    assert "code=media_download_failed" in str(caught.value)
    assert_private(caplog, caught.value)


async def test_js_exception_description_is_not_propagated(caplog):
    class Evaluator:
        async def send(self, *args, **kwargs):
            return {"exceptionDetails": {"exception": {"description": SECRET + SIGNED_URL}}}

    with pytest.raises(CDPError) as caught:
        await CDPSession.evaluate(Evaluator(), "offline")
    assert "code=cdp_evaluation_failed" in str(caught.value)
    assert_private(caplog, caught.value)
