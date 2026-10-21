"""First-token snapshot ordering, without a browser, network or account data."""

import asyncio
from types import SimpleNamespace

import pytest

from muse_console.accounts.model import Account
from muse_console.drivers.base import ChatRequest
from muse_console.drivers.browser import driver as mod
from muse_console.errors import UpstreamTimeout


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]

    async def sleep(delay):
        now[0] += delay

    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mod.asyncio, "sleep", sleep)
    return now


def harness(monkeypatch, clock, snapshots, base=None):
    driver = mod.BrowserDriver(SimpleNamespace())
    tab = SimpleNamespace(stale=False, thread_url="")
    calls = {"begin": 0, "poll": 0, "deny": 0, "save": 0, "checkin": 0}
    snapshots = iter(snapshots)

    async def begin(*_):
        calls["begin"] += 1
        return tab, base or {"agentCount": 0, "lastText": ""}

    async def state(_):
        calls["poll"] += 1
        when, snapshot = next(snapshots)
        clock[0] = max(clock[0], when)
        return snapshot

    async def deny(_):
        calls["deny"] += 1

    async def href(_):
        # No real URL is visited. This only skips the URL-settle polling loop.
        return "https://muse.ai/thread/offline"

    def save(*_):
        calls["save"] += 1

    async def checkin(_):
        calls["checkin"] += 1

    monkeypatch.setattr(driver, "_begin_chat", begin)
    monkeypatch.setattr(driver, "_state", state)
    monkeypatch.setattr(driver, "_deny_approvals", deny)
    monkeypatch.setattr(driver, "_href", href)
    monkeypatch.setattr(driver, "_save_hot", save)
    monkeypatch.setattr(driver, "_forget", lambda _: None)
    monkeypatch.setattr(driver, "_checkin", checkin)
    return driver, tab, calls


def snapshot(text="", *, count=0, generating=False, approval=False):
    return {
        "agentCount": count,
        "lastText": text,
        "generating": generating,
        "approval": approval,
    }


def request(**kwargs):
    req = ChatRequest("offline prompt", "offline", **kwargs)
    assert req.first_token_timeout == 45
    return req


async def test_first_completed_text_after_soft_deadline_is_accepted(monkeypatch, clock):
    # Advance via the previous snapshot so the next loop's elapsed exceeds 45s.
    done = snapshot("completed reply", count=1)
    driver, tab, calls = harness(
        monkeypatch,
        clock,
        [(46, snapshot())] + [(46, done)] * (1 + mod.STABLE_POLLS_DONE),
    )
    chunks = [chunk async for chunk in driver.chat_stream(Account(id="offline"), request())]
    assert "".join(chunks) == "completed reply"
    assert calls == {
        "begin": 1,
        "poll": 2 + mod.STABLE_POLLS_DONE,
        "deny": 0,
        "save": 1,
        "checkin": 1,
    }
    assert not tab.stale


@pytest.mark.parametrize("old_text", ["", "unchanged baseline"])
async def test_no_valid_new_text_still_times_out(monkeypatch, clock, old_text):
    old = snapshot(old_text, count=1)
    driver, tab, calls = harness(monkeypatch, clock, [(46, old), (46, old)], base=old)
    with pytest.raises(UpstreamTimeout, match="no first token from assistant"):
        _ = [chunk async for chunk in driver.chat_stream(Account(id="offline"), request())]
    assert calls["begin"] == 1
    assert calls["save"] == 0
    assert calls["checkin"] == 1
    assert tab.stale


async def test_generating_can_continue_past_soft_deadline(monkeypatch, clock):
    done = snapshot("later reply", count=1)
    driver, _, calls = harness(
        monkeypatch,
        clock,
        [(46, snapshot()), (47, snapshot(generating=True))]
        + [(47, done)] * (1 + mod.STABLE_POLLS_DONE),
    )
    chunks = [chunk async for chunk in driver.chat_stream(Account(id="offline"), request())]
    assert "".join(chunks) == "later reply"
    assert calls["save"] == 1
    assert calls["begin"] == 1


async def test_total_deadline_still_precedes_next_snapshot(monkeypatch, clock):
    driver, tab, calls = harness(
        monkeypatch,
        clock,
        [(51, snapshot(generating=True)), (51, snapshot("too late", count=1))],
    )
    with pytest.raises(UpstreamTimeout, match="assistant reply timed out"):
        _ = [
            chunk async for chunk in driver.chat_stream(Account(id="offline"), request(timeout=50))
        ]
    assert calls["poll"] == 1
    assert calls["save"] == 0
    assert calls["checkin"] == 1
    assert tab.stale


async def test_cancel_still_precedes_poll(monkeypatch, clock):
    driver, tab, calls = harness(monkeypatch, clock, [])
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        _ = [
            chunk
            async for chunk in driver.chat_stream(Account(id="offline"), request(cancel=cancel))
        ]
    assert calls["poll"] == 0
    assert calls["save"] == 0
    assert calls["checkin"] == 1
    assert tab.stale


async def test_approval_is_handled_before_accepting_snapshot_text(monkeypatch, clock):
    done = snapshot("approved-path reply", count=1)
    driver, _, calls = harness(
        monkeypatch,
        clock,
        [(46, snapshot()), (47, snapshot("not accepted yet", count=1, approval=True))]
        + [(47, done)] * (1 + mod.STABLE_POLLS_DONE),
    )
    chunks = [chunk async for chunk in driver.chat_stream(Account(id="offline"), request())]
    assert "".join(chunks) == "approved-path reply"
    assert calls["deny"] == 1
    assert calls["begin"] == 1
    assert calls["save"] == 1


@pytest.mark.parametrize("generating", [True, False])
async def test_empty_snapshot_resets_consecutive_idle_polls(monkeypatch, clock, generating):
    partial = snapshot("partial reply", count=1)
    complete = snapshot("partial reply with complete output", count=1)
    snapshots = (
        [(46, snapshot()), (46, partial)]
        + [(46, partial)] * (mod.STABLE_POLLS_DONE - 1)
        + [(47, snapshot(count=1, generating=generating)), (47, partial)]
        + [(48, complete)] * (1 + mod.STABLE_POLLS_DONE)
    )
    driver, tab, calls = harness(monkeypatch, clock, snapshots)
    chunks = [chunk async for chunk in driver.chat_stream(Account(id="offline"), request())]
    assert "".join(chunks) == "partial reply with complete output"
    assert calls["poll"] == len(snapshots)
    assert calls["save"] == 1
    assert calls["checkin"] == 1
    assert not tab.stale


async def test_active_generation_pause_does_not_complete(monkeypatch, clock):
    active = snapshot("partial reply", count=1, generating=True)
    driver, _, calls = harness(
        monkeypatch,
        clock,
        [(46, snapshot())]
        + [(46, active)] * (2 + mod.STABLE_POLLS_DONE)
        + [(47, snapshot("partial reply", count=1))] * mod.STABLE_POLLS_DONE,
    )
    chunks = [chunk async for chunk in driver.chat_stream(Account(id="offline"), request())]
    assert "".join(chunks) == "partial reply"
    assert calls["poll"] == 3 + 2 * mod.STABLE_POLLS_DONE
    assert calls["save"] == 1
