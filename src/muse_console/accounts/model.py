from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..errors import (
    UpstreamAuthError,
    UpstreamError,
    UpstreamQuotaError,
    UpstreamRefused,
    UpstreamTimeout,
)
from .credentials import CookieRecord

# Only these local codes and descriptions may cross the account public boundary.
ERROR_REASONS = {
    "composer_ready_timeout": "上次请求的输入框未能及时就绪，请稍后重试",
    "upstream_timeout": "上次请求等待上游响应超时，请稍后重试",
    "upstream_auth_failed": "上次上游认证失败，请检查或更新账号凭据",
    "upstream_quota_exhausted": "上次请求的上游额度不足，请等待额度恢复",
    "media_missing": "上次请求仅收到文本，未获得所需图片或视频",
    "upstream_refused": "上次上游未返回所需结果，请调整请求后重试",
    "upstream_error": "上次请求发生上游服务错误，请稍后重试",
    "request_failed": "上次请求失败，请检查请求或稍后重试",
}
LEGACY_ERROR_CODES = {
    "UpstreamAuthError": "upstream_auth_failed",
    "UpstreamQuotaError": "upstream_quota_exhausted",
    "UpstreamTimeout": "upstream_timeout",
    "UpstreamRefused": "upstream_refused",
    "UpstreamError": "upstream_error",
}


def _error_code(error: BaseException) -> str:
    # Match the entire known internal message, never arbitrary code attributes,
    # class names, substrings or upstream text. Type checks keep auth precedence.
    if isinstance(error, UpstreamAuthError):
        return "upstream_auth_failed"
    if isinstance(error, UpstreamQuotaError):
        return "upstream_quota_exhausted"
    if isinstance(error, UpstreamTimeout):
        if error.message == "composer readiness timed out (code=composer_ready_timeout)":
            return "composer_ready_timeout"
        return "upstream_timeout"
    if isinstance(error, UpstreamRefused):
        if error.message == "upstream replied with text only (code=media_missing)":
            return "media_missing"
        return "upstream_refused"
    if isinstance(error, UpstreamError):
        return "upstream_error"
    return "request_failed"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    COOLING = "cooling"
    """Temporarily unavailable (quota exhausted or transient errors); returns after cooldown."""
    INVALID = "invalid"
    """Session rejected by upstream; needs fresh cookies."""


def _now() -> float:
    return time.time()


class Account(BaseModel):
    id: str = Field(default_factory=lambda: "acc_" + uuid.uuid4().hex[:10])
    label: str = ""
    enabled: bool = True
    max_concurrency: int | None = Field(default=None, ge=1, le=16)
    status: AccountStatus = AccountStatus.ACTIVE

    cookies: dict[str, str] = Field(default_factory=dict, repr=False)
    credential_records: list[CookieRecord] = Field(default_factory=list, repr=False)
    credential_version: int = Field(default=0, ge=0)
    credential_subject: str | None = Field(default=None, repr=False)
    cookie_expires: dict[str, int] = Field(
        default_factory=dict, description="cookie name -> unix expiry seconds"
    )

    created_at: float = Field(default_factory=_now)
    last_used_at: float = 0.0
    cooldown_until: float = 0.0
    last_error: str | None = None
    last_error_code: str | None = None
    last_error_at: float | None = None
    success_count: int = 0
    fail_count: int = 0

    meta: dict[str, Any] = Field(
        default_factory=dict, description="Driver-specific data such as quota snapshots or vm ids"
    )

    @field_validator("last_error_code", mode="before")
    @classmethod
    def _safe_error_code(cls, value: Any) -> str | None:
        return value if isinstance(value, str) and value in ERROR_REASONS else None

    def clear_error(self) -> None:
        self.last_error = self.last_error_code = self.last_error_at = None

    def record_error(self, error: BaseException) -> None:
        code = _error_code(error)
        self.last_error_code = code
        self.last_error = ERROR_REASONS[code]
        self.last_error_at = _now()

    def is_available(self, now: float | None = None) -> bool:
        if not self.enabled or self.status == AccountStatus.INVALID:
            return False
        if self.status == AccountStatus.COOLING:
            return (_now() if now is None else now) >= self.cooldown_until
        return True

    def public(self) -> dict[str, Any]:
        """Public allowlist: upstream metadata and error text may contain credentials."""
        data = self.model_dump(
            mode="json",
            include={
                "id",
                "label",
                "enabled",
                "credential_version",
                "status",
                "created_at",
                "last_used_at",
                "cooldown_until",
                "success_count",
                "fail_count",
                "max_concurrency",
            },
        )
        from ..upstream.muse import SESSION_COOKIES

        data["cookies"] = {name: "***" for name in SESSION_COOKIES if name in self.cookies}
        quota = self.meta.get("quota")
        safe_quota = {}
        if isinstance(quota, dict):
            for key in ("checked_at", "remaining", "total", "used", "limit", "credits", "reset_at"):
                value = quota.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    safe_quota[key] = value
            if quota.get("plan") in ("free", "pro", "team", "enterprise", "business", "premium"):
                safe_quota["plan"] = quota["plan"]
        data["meta"] = {"quota": safe_quota} if safe_quota else {}
        code = self._safe_error_code(self.last_error_code)
        if code is None and self.last_error:
            code = LEGACY_ERROR_CODES.get(self.last_error, "request_failed")
        data["last_error"] = ERROR_REASONS[code] if code else None
        data["last_error_code"] = code
        # Legacy records have no event timestamp; never invent one on read.
        data["last_error_at"] = self.last_error_at if code else None
        return data
