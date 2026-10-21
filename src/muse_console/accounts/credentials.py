"""scope-preserving credential transport; validation errors never include secret values."""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..upstream.muse import REQUIRED_SESSION_COOKIES, SESSION_COOKIES


class PartitionKey(BaseModel):
    """browser partition identity, independent of a local cookie store id."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    topLevelSite: str = Field(max_length=512)
    hasCrossSiteAncestor: bool = False

    @field_validator("topLevelSite")
    @classmethod
    def origin_only(cls, value: str) -> str:
        """reject opaque, insecure and credential-bearing partition origins."""
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise ValueError("invalid partition origin")
        return f"https://{parsed.hostname.lower()}"


class CookieRecord(BaseModel):
    """a Muse cookie with its original browser scope and lifetime."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)
    name: str = Field(max_length=64)
    value: str = Field(min_length=1, max_length=8192, repr=False)
    domain: str = Field(min_length=1, max_length=253)
    path: str = Field(default="/", min_length=1, max_length=1024)
    secure: bool = True
    httpOnly: bool = True
    sameSite: Literal["no_restriction", "lax", "strict", "unspecified"] = "unspecified"
    hostOnly: bool = False
    session: bool = True
    expires: float | None = None
    partitionKey: PartitionKey | None = None

    @field_validator("name")
    @classmethod
    def allowed_name(cls, value: str) -> str:
        if value not in SESSION_COOKIES:
            raise ValueError("unexpected cookie name")
        return value

    @field_validator("domain")
    @classmethod
    def allowed_domain(cls, value: str) -> str:
        """only Muse-owned domains belong in an account credential set."""
        normalized = value.lower()
        host = normalized.removeprefix(".")
        if host != "muse.ai" and not host.endswith(".muse.ai"):
            raise ValueError("unexpected cookie domain")
        if any(
            not part or not all(c.isascii() and (c.isalnum() or c == "-") for c in part)
            for part in host.split(".")
        ):
            raise ValueError("invalid cookie domain")
        return normalized

    @field_validator("value", "path")
    @classmethod
    def safe_text(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid cookie text")
        return value

    @model_validator(mode="after")
    def valid_scope(self) -> CookieRecord:
        """reject inconsistent attributes instead of guessing browser semantics."""
        if not self.path.startswith("/") or not self.secure:
            raise ValueError("invalid cookie scope")
        if ";" in self.value or "\r" in self.value or "\n" in self.value:
            raise ValueError("invalid cookie value")
        if self.hostOnly and self.domain.startswith("."):
            raise ValueError("inconsistent host-only scope")
        if self.session and self.expires is not None:
            raise ValueError("session cookie cannot have an expiry")
        if not self.session and (
            self.expires is None or not math.isfinite(self.expires) or self.expires <= 0
        ):
            raise ValueError("persistent cookie requires a finite expiry")
        return self

    def scope_key(self) -> tuple:
        partition = self.partitionKey
        return (
            self.name,
            self.domain,
            self.path,
            partition.topLevelSite if partition else "",
            partition.hasCrossSiteAncestor if partition else False,
        )


def project_records(records: list[CookieRecord], *, now: float | None = None) -> dict[str, str]:
    """return an unambiguous Muse session projection or reject missing/expired scopes."""
    now = time.time() if now is None else now
    values: dict[str, str] = {}
    scopes = set()
    partitions = set()
    for record in records:
        if record.expires is not None and record.expires <= now:
            raise ValueError("expired session cookie")
        key = record.scope_key()
        if key in scopes:
            raise ValueError("duplicate cookie scope")
        scopes.add(key)
        if record.partitionKey:
            partitions.add(record.partitionKey)
        if record.name in values:
            raise ValueError("ambiguous session cookies")
        values[record.name] = record.value
    if len(partitions) > 1:
        raise ValueError("mixed cookie partitions")
    if any(not values.get(name) for name in REQUIRED_SESSION_COOKIES):
        raise ValueError("required session cookies missing")
    return values


class CredentialEnvelope(BaseModel):
    """a single device/store/partition snapshot; browser secrets stay out of repr."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    source: Literal["extension"] = "extension"
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    capture_id: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    source_revision: int = Field(ge=1, le=2**53 - 1)
    captured_at: float = Field(gt=0, allow_inf_nan=False)
    store_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    partition_key: PartitionKey | None = None
    base_credential_version: int = Field(default=0, ge=0, le=2**53 - 1)
    cookies: list[CookieRecord] = Field(min_length=1, max_length=32, repr=False)

    @model_validator(mode="after")
    def one_source(self) -> CredentialEnvelope:
        """prevent accidentally combining cookies from different top-level partitions."""
        scopes = set()
        for record in self.cookies:
            if record.partitionKey and record.partitionKey != self.partition_key:
                raise ValueError("cookie partition does not match snapshot")
            if record.scope_key() in scopes:
                raise ValueError("duplicate cookie scope")
            scopes.add(record.scope_key())
        if self.captured_at > time.time() + 300:
            raise ValueError("capture time is in the future")
        return self

    def projection(self) -> dict[str, str]:
        return project_records(self.cookies)

    def fingerprint(self) -> str:
        """digest content and scope, not clocks or client-provided sequence numbers."""
        records = sorted(
            (r.model_dump(mode="json") for r in self.cookies),
            key=lambda r: json.dumps(r, sort_keys=True),
        )
        raw = json.dumps(
            {
                "store": self.store_id,
                "partition": self.partition_key.model_dump() if self.partition_key else None,
                "cookies": records,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode()).hexdigest()


def records_to_cdp(records: list[CookieRecord]) -> list[dict]:
    """encode original cookie scopes for an owned Chromium context without extending TTL."""
    result = []
    for record in records:
        if record.expires is not None and record.expires <= time.time():
            raise ValueError("expired session cookie")
        params = {
            "name": record.name,
            "value": record.value,
            "path": record.path,
            "secure": record.secure,
            "httpOnly": record.httpOnly,
        }
        if record.hostOnly:
            params["url"] = f"https://{record.domain}{record.path}"
        else:
            params["domain"] = record.domain
        if record.sameSite != "unspecified":
            params["sameSite"] = {"no_restriction": "None", "lax": "Lax", "strict": "Strict"}[
                record.sameSite
            ]
        if record.expires is not None:
            params["expires"] = record.expires
        if record.partitionKey is not None:
            params["partitionKey"] = record.partitionKey.model_dump()
        result.append(params)
    return result


def records_from_cdp(cookies: list[dict]) -> list[CookieRecord]:
    """filter a context snapshot to Muse session cookies with their reported attributes."""
    records = []
    for cookie in cookies:
        if cookie.get("name") not in SESSION_COOKIES:
            continue
        domain = cookie.get("domain", "")
        host = domain.lower().removeprefix(".")
        if host != "muse.ai" and not host.endswith(".muse.ai"):
            continue
        expiry = cookie.get("expires", -1)
        if expiry > 0 and expiry <= time.time():
            continue
        records.append(
            CookieRecord(
                name=cookie["name"],
                value=cookie.get("value", ""),
                domain=domain,
                path=cookie.get("path", "/"),
                secure=cookie.get("secure", True),
                httpOnly=cookie.get("httpOnly", True),
                sameSite={"None": "no_restriction", "Lax": "lax", "Strict": "strict"}.get(
                    cookie.get("sameSite"), "unspecified"
                ),
                hostOnly=not domain.startswith("."),
                session=expiry <= 0,
                expires=float(expiry) if expiry > 0 else None,
                partitionKey=cookie.get("partitionKey"),
            )
        )
    return records
