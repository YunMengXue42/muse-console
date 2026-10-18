from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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

    cookies: dict[str, str] = Field(default_factory=dict)
    cookie_expires: dict[str, int] = Field(
        default_factory=dict, description="cookie name -> unix expiry seconds"
    )

    created_at: float = Field(default_factory=_now)
    last_used_at: float = 0.0
    cooldown_until: float = 0.0
    last_error: str | None = None
    success_count: int = 0
    fail_count: int = 0

    meta: dict[str, Any] = Field(
        default_factory=dict, description="Driver-specific data such as quota snapshots or vm ids"
    )

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
        data["last_error"] = "upstream request failed" if self.last_error else None
        return data
