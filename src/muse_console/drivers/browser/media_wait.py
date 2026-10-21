"""Pure decision logic for waiting on a generated image/video attachment.

The browser driver owns all I/O (polling the page, denying approvals, sending
the follow-up, probing links). This state machine only decides what to do next
from one page snapshot and a clock reading, so every timing branch can be tested
without a browser or real sleeps.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Act(Enum):
    WAIT = "wait"  # poll again
    DENY = "deny"  # an upload approval is pending: deny it
    RETURN = "return"  # attachment ready: ``Step.attachment``
    ASK = "ask"  # text-only so far: ask once for the file in chat
    PROBE = "probe"  # last resort: look for a media link in the reply


@dataclass
class Step:
    act: Act
    attachment: dict | None = None
    denied: bool = False
    reply_chars: int = 0


@dataclass
class MediaWait:
    kind: str
    timeout: float
    grace: float
    ask_grace: float
    overrun_factor: float
    started: float
    base_atts: int = 0
    base_count: int = 0
    denied: bool = False
    asked: bool = False
    busy: bool = False
    text_done_at: float | None = field(default=None)

    @classmethod
    def from_base(cls, base: dict, **kw) -> MediaWait:
        return cls(
            base_atts=len(base.get("attachments", [])),
            base_count=base.get("agentCount", 0),
            **kw,
        )

    def expired(self, now: float) -> bool:
        """The soft deadline: keep going up to ``overrun_factor`` while visibly busy."""
        elapsed = now - self.started
        return elapsed >= self.timeout and not (
            self.busy and elapsed < self.timeout * self.overrun_factor
        )

    def progress(self, now: float) -> int:
        return min(95, int((now - self.started) / self.timeout * 100))

    def step(self, st: dict, now: float) -> Step:
        if st.get("approval"):
            self.denied, self.text_done_at = True, None
            return Step(Act.DENY)
        kind = self.kind
        new_atts = st.get("attachments", [])[self.base_atts :]
        fresh = [a for a in new_atts if a.get("src") and a.get("kind") == kind]
        if fresh:
            return Step(Act.RETURN, attachment=fresh[-1])
        loading = any(kind in (a.get("tid") or "") for a in new_atts)
        self.busy = bool(st.get("generating")) or loading
        # An attachment node exists but its (blob) src is not loaded yet.
        if loading:
            self.text_done_at = None
            return Step(Act.WAIT)
        text = st.get("lastText", "")
        if not (st.get("agentCount", 0) > self.base_count and text and not st.get("generating")):
            self.text_done_at = None
            return Step(Act.WAIT)
        # The reply finished without media: either a refusal, or the attachment lags.
        if self.text_done_at is None:
            self.text_done_at = now
            return Step(Act.WAIT)
        if now - self.text_done_at < (self.ask_grace if self.asked else self.grace):
            return Step(Act.WAIT)
        if not self.asked:
            self.asked, self.text_done_at = True, None
            self.base_count = st.get("agentCount", 0)
            return Step(Act.ASK, denied=self.denied, reply_chars=len(text))
        return Step(Act.PROBE)
