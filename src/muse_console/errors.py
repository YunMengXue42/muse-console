"""Error hierarchy. Every error maps to an HTTP status and an OpenAI-style error body."""

from __future__ import annotations

from typing import Any


class MuseConsoleError(Exception):
    status_code: int = 500
    error_type: str = "server_error"
    code: str = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or self.__class__.__name__
        if code:
            self.code = code

    def to_body(self) -> dict[str, Any]:
        return {"error": {"message": self.message, "type": self.error_type, "code": self.code}}


# --- client side ---
class InvalidRequest(MuseConsoleError):
    status_code = 400
    error_type = "invalid_request_error"
    code = "invalid_request"


class Unauthorized(MuseConsoleError):
    status_code = 401
    error_type = "authentication_error"
    code = "invalid_api_key"


class NotFound(MuseConsoleError):
    status_code = 404
    error_type = "invalid_request_error"
    code = "not_found"


class ModelNotFound(NotFound):
    """OpenAI-compatible unknown-model error, so gateways can mark the channel wrong."""

    code = "model_not_found"


class FeatureNotImplemented(MuseConsoleError):
    """Reserved endpoint or driver capability that has not been built yet."""

    status_code = 501
    error_type = "not_implemented"
    code = "not_implemented"


class CapacityExceeded(MuseConsoleError):
    """reject work before execution when a local resource budget is exhausted."""

    status_code = 429
    error_type = "rate_limit_error"
    code = "capacity_exceeded"


# --- capacity ---
class NoAccountAvailable(MuseConsoleError):
    status_code = 503
    error_type = "server_error"
    code = "no_account_available"


# --- upstream (muse.ai) ---
class UpstreamError(MuseConsoleError):
    """Generic upstream failure. ``retryable`` tells the service layer whether to fail over."""

    status_code = 502
    error_type = "upstream_error"
    code = "upstream_error"
    retryable: bool = True


class UpstreamAuthError(UpstreamError):
    """The account's session is invalid; the account should be marked invalid."""

    code = "upstream_auth_failed"


class UpstreamQuotaError(UpstreamError):
    """The account ran out of quota; the account should cool down."""

    status_code = 429
    code = "upstream_quota_exhausted"


class UpstreamTimeout(UpstreamError):
    status_code = 504
    code = "upstream_timeout"


class UpstreamRefused(UpstreamError):
    """Upstream answered but did not produce what was asked (e.g. text instead of an image)."""

    code = "upstream_refused"
    retryable = False
