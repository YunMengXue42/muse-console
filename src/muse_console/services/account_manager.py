"""Credential import boundary: parsing errors never echo supplied credentials."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

from ..accounts import Account, AccountPool, AccountStatus
from ..accounts.credentials import CookieRecord, project_records
from ..accounts.store import session_digest
from ..upstream.muse import SESSION_COOKIES, SITE, missing_session_cookies


class AccountManager:
    def __init__(self, pool: AccountPool) -> None:
        self.pool = pool
        self._lock = asyncio.Lock()

    @staticmethod
    def _cookies(value: Any) -> tuple[dict[str, str], dict[str, int]]:
        expires: dict[str, int] = {}
        if isinstance(value, list):
            cookies = {}
            for item in value:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise ValueError("invalid browser cookie entry")
                name, content = item["name"], item.get("value")
                if "domain" in item:
                    domain = item["domain"]
                    host = urlsplit(SITE).hostname
                    if not isinstance(domain, str):
                        continue
                    domain = domain.lower().removeprefix(".")
                    if domain != host and not domain.endswith("." + host):
                        continue
                if name not in SESSION_COOKIES:
                    continue
                if not isinstance(content, str):
                    raise ValueError("invalid cookie value")
                cookies[name] = content
                expiry = item.get("expirationDate", item.get("expires"))
                if expiry is not None:
                    expires[name] = int(expiry)
        elif isinstance(value, dict):
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                raise ValueError("cookie names and values must be strings")
            cookies = {name: content for name, content in value.items() if name in SESSION_COOKIES}
        else:
            raise ValueError("invalid cookies format")
        if missing_session_cookies(cookies):
            raise ValueError("missing required session cookies")
        return cookies, expires

    @staticmethod
    def _entries(content: str) -> list[Any]:
        content = content.strip()
        if not content:
            return [None]
        if content.startswith(("[", "{")):
            try:
                raw = json.loads(content)
            except (ValueError, RecursionError):
                return [None]
            if isinstance(raw, list):
                if raw and all(isinstance(v, dict) and "name" in v for v in raw):
                    return [{"cookies": raw}]
                return raw or [None]
            return [raw]
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if any("\t" in line for line in lines):
            cookies, errors = [], []
            for line in lines:
                if line.startswith("#") and not line.startswith("#HttpOnly_"):
                    continue
                parts = line.split("\t")
                if len(parts) != 7:
                    errors.append(None)
                    continue
                try:
                    cookies.append(
                        {
                            "name": parts[5],
                            "value": parts[6],
                            "expires": int(parts[4]),
                            "domain": parts[0].removeprefix("#HttpOnly_"),
                        }
                    )
                except ValueError:
                    errors.append(None)
            return ([{"cookies": cookies}] if cookies else []) + errors or [None]
        entries: list[Any] = []
        for line in lines:
            if line.lower().startswith("cookie:"):
                line = line[7:].strip()
            cookies = {}
            valid = True
            for pair in line.split(";"):
                if not pair.strip():
                    continue
                name, sep, value = pair.strip().partition("=")
                if not sep or not name:
                    valid = False
                    break
                cookies[name] = value
            entries.append({"cookies": cookies} if valid else None)
        return entries

    def parse(self, content: str) -> list[Account | None]:
        result = []
        for entry in self._entries(content):
            try:
                if not isinstance(entry, dict):
                    raise ValueError("invalid account entry")
                cookies, expires = self._cookies(entry.get("cookies"))
                result.append(
                    Account(
                        label=entry.get("label", ""),
                        cookies=cookies,
                        cookie_expires=expires,
                        max_concurrency=entry.get("max_concurrency"),
                    )
                )
            except (ValueError, TypeError, OverflowError):
                result.append(None)
        return result

    async def import_accounts(self, content: str, *, preview: bool = False) -> dict:
        async with self._lock:
            seen = {session_digest(a) for a in self.pool.all()}
            rows = []
            summary = dict.fromkeys(("imported", "ready", "duplicate", "error"), 0)
            for index, account in enumerate(self.parse(content)):
                row: dict[str, Any] = {"index": index, "label": account.label if account else ""}
                if account is None:
                    row.update(
                        status="error",
                        message="invalid account or missing required session cookies",
                    )
                elif session_digest(account) in seen:
                    row.update(status="duplicate", message="session already present")
                else:
                    digest = session_digest(account)
                    if preview:
                        row["status"] = "ready"
                        seen.add(digest)
                    else:
                        try:
                            await self.pool.upsert(account)
                        except ValueError:
                            row.update(status="duplicate", message="session already present")
                        else:
                            row.update(status="imported", account_id=account.id)
                            seen.add(digest)
                summary[row["status"]] += 1
                rows.append(row)
            return {"data": rows, "summary": summary}

    async def preview(self, content: str) -> dict:
        return await self.import_accounts(content, preview=True)

    def _reject_duplicate(self, account: Account) -> None:
        digest = session_digest(account)
        if digest and any(
            a.id != account.id and session_digest(a) == digest for a in self.pool.all()
        ):
            raise ValueError("duplicate account session")

    async def import_account(
        self,
        cookies: dict,
        label: str = "",
        cookie_expires: dict | None = None,
        credential_records: list[CookieRecord] | None = None,
    ) -> Account:
        validated, expires = self._cookies(cookies)
        records = credential_records or []
        if records and project_records(records) != validated:
            raise ValueError("credential records do not match session")
        account = Account(
            label=label,
            cookies=validated,
            cookie_expires=cookie_expires if cookie_expires is not None else expires,
            credential_records=records,
        )
        async with self._lock:
            self._reject_duplicate(account)
            return await self.pool.upsert(account)

    async def create_legacy(self, body: Any) -> Account:
        values = body.model_dump() if hasattr(body, "model_dump") else dict(body)
        account = Account.model_validate(values)
        account.cookies = {k: v for k, v in account.cookies.items() if v}
        if not account.cookies:
            raise ValueError("cookies must not be empty")
        async with self._lock:
            self._reject_duplicate(account)
            return await self.pool.upsert(account)

    async def merge(self, source_id: str, target_id: str) -> Account:
        """Fold a duplicate entry into ``target_id``, keeping the fresher credentials.

        Identity is never guessed here: the caller names both accounts. Nothing is
        deleted until the merged record is persisted in the same commit.
        """
        if source_id == target_id:
            raise ValueError("cannot merge an account into itself")
        async with self._lock:
            source, target = self.pool.get(source_id), self.pool.get(target_id)
            if source is None or target is None:
                raise ValueError("account not found")
            fresher = source if source.credential_version > target.credential_version else target
            merged = target.model_copy(
                update={
                    "label": target.label or source.label,
                    "cookies": dict(fresher.cookies),
                    "credential_records": list(fresher.credential_records),
                    "cookie_expires": dict(fresher.cookie_expires),
                    "credential_version": max(source.credential_version, target.credential_version),
                    "credential_subject": (
                        target.credential_subject
                        or source.credential_subject
                        or fresher.credential_subject
                    ),
                    "max_concurrency": target.max_concurrency or source.max_concurrency,
                    "created_at": min(source.created_at, target.created_at),
                    "last_used_at": max(source.last_used_at, target.last_used_at),
                    "success_count": source.success_count + target.success_count,
                    "fail_count": source.fail_count + target.fail_count,
                    "status": (
                        AccountStatus.ACTIVE
                        if AccountStatus.ACTIVE in (source.status, target.status)
                        else target.status
                    ),
                    "meta": {**source.meta, **target.meta},
                }
            )
            return await self.pool.replace(source_id, merged)

    async def update_account(self, account_id: str, **changes: Any) -> Account:
        return await self.update(account_id, **changes)

    async def update(self, account_id: str, **changes: Any) -> Account:
        async with self._lock:
            while True:
                account = self.pool.get(account_id)
                if account is None:
                    raise ValueError("account not found")
                allowed = {"label", "enabled", "cookies", "cookie_expires", "max_concurrency"}
                if changes.keys() - allowed:
                    raise ValueError("unsupported account field")
                if "cookies" in changes:
                    cookies, expires = self._cookies(changes["cookies"])
                    changes.update(
                        cookies=cookies,
                        credential_records=[],
                        credential_subject=None,
                        cookie_expires=changes.get("cookie_expires", expires),
                        status=AccountStatus.ACTIVE,
                        cooldown_until=0,
                        last_error=None,
                        last_error_code=None,
                        last_error_at=None,
                    )
                updated = Account.model_validate({**account.model_dump(), **changes})
                self._reject_duplicate(updated)
                if await self.pool.upsert_if_current(updated, account):
                    return updated
