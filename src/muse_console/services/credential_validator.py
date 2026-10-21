"""Candidate validation adapter; no HTTP fallback or inferred identity."""

from __future__ import annotations

from dataclasses import dataclass

from ..accounts.credentials import CookieRecord
from ..drivers.browser.driver import BrowserDriver
from ..drivers.browser.session_auth import SessionProbeError, probe_session

CredentialValidationError = SessionProbeError


@dataclass(repr=False)
class VerifiedCredential:
    records: list[CookieRecord]
    subject: str | None = None


class BrowserCredentialValidator:
    def __init__(self, driver, *, connect=None, timeout: float = 20) -> None:
        self.driver, self.connect, self.timeout = driver, connect, timeout

    async def __call__(self, envelope) -> VerifiedCredential:
        if not isinstance(self.driver, BrowserDriver):
            raise CredentialValidationError("retry_wait", "browser_unavailable")
        records, subject = await probe_session(
            self.driver, envelope.cookies, connect=self.connect, timeout=self.timeout
        )
        return VerifiedCredential(records, subject)
