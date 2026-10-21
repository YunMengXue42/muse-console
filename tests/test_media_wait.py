"""Pure state-machine tests for media waiting (no browser, no sleeps)."""

from __future__ import annotations

from muse_console.drivers.browser.media_wait import Act, MediaWait


def _w(**kw) -> MediaWait:
    args = dict(kind="video", timeout=100, grace=40, ask_grace=25, overrun_factor=2.5, started=0.0)
    args.update(kw)
    return MediaWait.from_base({"attachments": [{"kind": "image"}], "agentCount": 1}, **args)


def _text(count=2, generating=False, atts=None):
    return {
        "agentCount": count,
        "lastText": "done",
        "generating": generating,
        "attachments": [{"kind": "image"}] + (atts or []),
    }


def test_ready_attachment_returns_newest_of_kind():
    w = _w()
    st = _text(atts=[{"kind": "video", "src": "blob:1"}, {"kind": "video", "src": "blob:2"}])
    step = w.step(st, 1)
    assert step.act is Act.RETURN and step.attachment["src"] == "blob:2"


def test_old_attachments_and_other_kinds_are_ignored():
    w = _w()
    st = _text(atts=[{"kind": "image", "src": "blob:img"}])
    assert w.step(st, 1).act is Act.WAIT


def test_grace_then_ask_then_short_grace_then_probe():
    w = _w()
    assert w.step(_text(), 10).act is Act.WAIT  # text done, grace starts
    assert w.step(_text(), 49).act is Act.WAIT  # 39s < 40s
    ask = w.step(_text(), 50)
    assert ask.act is Act.ASK and ask.reply_chars == 4 and not ask.denied
    # Same reply count after asking: not a new answer yet.
    assert w.step(_text(count=2), 51).act is Act.WAIT
    assert w.step(_text(count=3), 60).act is Act.WAIT  # follow-up answered
    assert w.step(_text(count=3), 84).act is Act.WAIT  # 24s < 25s ask grace
    assert w.step(_text(count=3), 85).act is Act.PROBE


def test_generating_or_loading_resets_grace():
    w = _w()
    w.step(_text(), 10)
    assert w.step(_text(generating=True), 30).act is Act.WAIT and w.busy
    assert w.step(_text(), 31).act is Act.WAIT  # grace restarts here
    assert w.step(_text(), 70).act is Act.WAIT
    assert w.step(_text(atts=[{"tid": "video-1"}]), 71).act is Act.WAIT and w.busy
    assert w.text_done_at is None


def test_approval_is_denied_and_reported_in_ask():
    w = _w(grace=0)
    assert w.step({"approval": True}, 1).act is Act.DENY
    w.step(_text(), 2)
    step = w.step(_text(), 2)
    assert step.act is Act.ASK and step.denied


def test_soft_deadline_extends_only_while_busy():
    w = _w()
    assert not w.expired(99) and w.expired(100)
    w.busy = True
    assert not w.expired(249) and w.expired(250)


def test_progress_is_capped():
    w = _w()
    assert w.progress(50) == 50 and w.progress(1000) == 95
