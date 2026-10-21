"""Account pool: scheduling, per-account concurrency limits, health state and failover.

Usage::

    async with pool.lease(exclude={...}, hint=conversation_key) as lease:
        ... use lease.account ...
        lease.fail(exc)   # optional; success is recorded automatically otherwise
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Protocol

from ..errors import NoAccountAvailable, UpstreamAuthError, UpstreamQuotaError, UpstreamTimeout
from .model import Account, AccountStatus
from .store import AccountStore, session_digest

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- strategies
class Strategy(Protocol):
    def choose(self, candidates: list[Account], hint: str | None) -> Account: ...


class RoundRobinStrategy:
    def __init__(self) -> None:
        self._last: str | None = None

    def choose(self, candidates: list[Account], hint: str | None) -> Account:
        ordered = sorted(candidates, key=lambda a: a.id)
        account = next((a for a in ordered if self._last is None or a.id > self._last), ordered[0])
        self._last = account.id
        return account


class LRUStrategy:
    def choose(self, candidates: list[Account], hint: str | None) -> Account:
        return min(candidates, key=lambda a: a.last_used_at)


class AffinityStrategy:
    """Sticks a hint (e.g. conversation id / user id) to the account that served it last,
    so a driver can reuse warm browser tabs or upstream threads. Falls back to LRU."""

    def __init__(self, max_entries: int = 4096) -> None:
        self._map: dict[str, str] = {}
        self._max = max_entries
        self._fallback = LRUStrategy()

    def choose(self, candidates: list[Account], hint: str | None) -> Account:
        if hint and (acc_id := self._map.get(hint)):
            for acc in candidates:
                if acc.id == acc_id:
                    return acc
        acc = self._fallback.choose(candidates, hint)
        if hint:
            if len(self._map) >= self._max:
                self._map.pop(next(iter(self._map)))
            self._map[hint] = acc.id
        return acc


STRATEGIES: dict[str, type] = {
    "round_robin": RoundRobinStrategy,
    "lru": LRUStrategy,
    "affinity": AffinityStrategy,
}


# ---------------------------------------------------------------- lease
class Lease:
    def __init__(self, account: Account) -> None:
        self.account = account
        self.error: BaseException | None = None

    def fail(self, exc: BaseException) -> None:
        self.error = exc


# ---------------------------------------------------------------- pool
class AccountPool:
    def __init__(
        self,
        store: AccountStore,
        *,
        strategy: str = "lru",
        max_concurrency: int = 1,
        cooldown: float = 120.0,
        acquire_timeout: float = 60.0,
    ) -> None:
        self.store = store
        self.strategy_name = strategy
        self.strategy: Strategy = STRATEGIES[strategy]()
        self.max_concurrency = max(1, max_concurrency)
        self.cooldown = cooldown
        self.acquire_timeout = acquire_timeout
        self._accounts: dict[str, Account] = {}
        self._inflight: dict[str, int] = defaultdict(int)
        self._cond = asyncio.Condition()
        self._waiting = 0
        self._revisions: dict[str, int] = defaultdict(int)
        self._leases: dict[int, tuple[str, int, dict]] = {}
        self._credential_locks: dict[str, asyncio.Lock] = {}

    def credential_lock(self, account_id: str) -> asyncio.Lock:
        """serialize credential ingestion and renewal for a stable account id."""
        return self._credential_locks.setdefault(account_id, asyncio.Lock())

    @staticmethod
    def _credentials_changed(current: Account, updated: Account) -> bool:
        return (
            current.cookies != updated.cookies
            or current.cookie_expires != updated.cookie_expires
            or current.credential_records != updated.credential_records
        )

    @classmethod
    def _versioned(cls, account: Account, current: Account | None) -> Account:
        """persist a monotonic revision even for legacy administrative writes."""
        changed = current is None or cls._credentials_changed(current, account)
        minimum = (current.credential_version if current else 0) + int(changed)
        return account.model_copy(
            update={"credential_version": max(account.credential_version, minimum)}
        )

    # ---- lifecycle / CRUD ----
    async def load(self) -> None:
        accounts = await self.store.load()
        self._accounts = {a.id: a for a in accounts}
        loader = getattr(self.store, "load_policy", None)
        if loader is not None:
            policy = await loader()
            if policy and policy.get("strategy") in STRATEGIES:
                self.strategy_name = policy["strategy"]
                self.strategy = STRATEGIES[self.strategy_name]()
        log.info("loaded %d account(s)", len(self._accounts))

    async def persist(self) -> None:
        await self.store.save(list(self._accounts.values()))

    async def _commit(self, updated: dict[str, Account]) -> None:
        async def save() -> None:
            await self.store.save(list(updated.values()))
            self._accounts = updated
            self._cond.notify_all()

        task = asyncio.create_task(save())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def all(self) -> list[Account]:
        return list(self._accounts.values())

    def get(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)

    async def replace(self, removed_id: str, account: Account) -> Account:
        """Store one merged account and drop one duplicate in a single commit."""
        async with self._cond:
            if self._accounts.get(removed_id) is None or self._accounts.get(account.id) is None:
                raise ValueError("account not found")
            if removed_id == account.id:
                raise ValueError("cannot merge an account into itself")
            self._revisions[account.id] += 1
            updated = {**self._accounts, account.id: account}
            del updated[removed_id]
            await self._commit(updated)
        return account

    async def upsert(self, account: Account) -> Account:
        async with self._cond:
            from ..upstream.muse import missing_session_cookies

            if not missing_session_cookies(account.cookies):
                digest = session_digest(account)
                if any(a.id != account.id and session_digest(a) == digest for a in self.all()):
                    raise ValueError("duplicate account session")
            current = self._accounts.get(account.id)
            account = self._versioned(account, current)
            if current is None or self._credentials_changed(current, account):
                self._revisions[account.id] += 1
            updated = {**self._accounts, account.id: account}
            await self._commit(updated)
        return account

    async def upsert_if_current(self, account: Account, expected: Account) -> bool:
        async with self._cond:
            if self._accounts.get(account.id) is not expected:
                return False
            from ..upstream.muse import missing_session_cookies

            if not missing_session_cookies(account.cookies):
                digest = session_digest(account)
                if any(a.id != account.id and session_digest(a) == digest for a in self.all()):
                    raise ValueError("duplicate account session")
            account = self._versioned(account, expected)
            if self._credentials_changed(expected, account):
                self._revisions[account.id] += 1
            await self._commit({**self._accounts, account.id: account})
            return True

    async def remove(self, account_id: str) -> bool:
        async with self._cond:
            if account_id not in self._accounts:
                return False
            self._revisions[account_id] += 1
            updated = {k: v for k, v in self._accounts.items() if k != account_id}
            await self._commit(updated)
            return True

    async def set_strategy(self, strategy: str) -> None:
        replacement = STRATEGIES[strategy]()
        async with self._cond:

            async def commit() -> None:
                saver = getattr(self.store, "save_policy", None)
                if saver is not None:
                    await saver({"strategy": strategy})
                self.strategy, self.strategy_name = replacement, strategy
                self._cond.notify_all()

            task = asyncio.create_task(commit())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    def _limit(self, account: Account) -> int:
        return account.max_concurrency or self.max_concurrency

    def _effective_status(self, account: Account) -> str:
        if not account.enabled:
            return "disabled"
        if account.status == AccountStatus.INVALID:
            return "invalid"
        if not account.is_available():
            return "cooling"
        if self._inflight[account.id] >= self._limit(account):
            return "busy"
        return "available"

    def all_public(self) -> list[dict]:
        return [
            {
                **a.public(),
                "inflight": self._inflight[a.id],
                "effective_status": self._effective_status(a),
                "effective_max_concurrency": self._limit(a),
            }
            for a in self.all()
        ]

    def stats(self) -> dict[str, int]:
        counts = {key: 0 for key in ("available", "invalid", "disabled", "cooling", "busy")}
        for account in self.all():
            counts[self._effective_status(account)] += 1
        return {
            "total": len(self._accounts),
            **counts,
            "inflight": sum(self._inflight.values()),
            "waiting": self._waiting,
        }

    # ---- scheduling ----
    def _candidates(self, exclude: Iterable[str]) -> tuple[list[Account], bool, float | None]:
        """Returns (free candidates, whether a usable account is merely busy, and the
        earliest time a cooling account comes back, if any)."""
        now = time.time()
        excluded = set(exclude)
        free, busy, back_at = [], False, None
        for acc in self._accounts.values():
            if acc.id in excluded or not acc.enabled or acc.status == AccountStatus.INVALID:
                continue
            if not acc.is_available(now):  # cooling down
                back_at = (
                    acc.cooldown_until if back_at is None else min(back_at, acc.cooldown_until)
                )
                continue
            if self._inflight[acc.id] >= self._limit(acc):
                busy = True
                continue
            free.append(acc)
        return free, busy, back_at

    def has_alternative(self, exclude: Iterable[str]) -> bool:
        """Whether an enabled, valid account exists outside ``exclude`` (busy or cooling counts)."""
        excluded = set(exclude)
        return any(
            a.id not in excluded and a.enabled and a.status != AccountStatus.INVALID
            for a in self._accounts.values()
        )

    async def acquire(self, *, exclude: Iterable[str] = (), hint: str | None = None) -> Account:
        exclude = tuple(exclude)
        deadline = time.monotonic() + self.acquire_timeout
        async with self._cond:
            while True:
                free, busy, back_at = self._candidates(exclude)
                if free:
                    acc = self.strategy.choose(free, hint)
                    self._inflight[acc.id] += 1
                    acc.last_used_at = time.time()
                    snapshot = acc.model_copy(deep=True)
                    self._leases[id(snapshot)] = (
                        acc.id,
                        self._revisions[acc.id],
                        deepcopy(acc.meta),
                    )
                    return snapshot
                remaining = deadline - time.monotonic()
                back_in = None if back_at is None else back_at - time.time()
                if not busy and back_in is None:
                    raise NoAccountAvailable("no usable account in pool")
                if remaining <= 0 or (not busy and back_in > remaining):
                    # Nothing frees up before the deadline; say why instead of waiting in vain.
                    reason = "busy" if busy else "cooling down"
                    raise NoAccountAvailable(f"all accounts are {reason}, try again later")
                # Wake on release, or when the soonest cooldown ends (nothing notifies that).
                wait = remaining if back_in is None else min(remaining, max(back_in, 0.05))
                self._waiting += 1
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    continue
                finally:
                    self._waiting -= 1

    async def release(self, account: Account, error: BaseException | None = None) -> None:
        async with self._cond:
            lease = self._leases.pop(id(account), None)
            if lease is None:
                return
            self._inflight[account.id] = max(0, self._inflight[account.id] - 1)
            current = self._accounts.get(account.id)
            if (
                current is not None
                and lease[1] == self._revisions[account.id]
                and current.cookies == account.cookies
                and not isinstance(error, (asyncio.CancelledError, GeneratorExit))
            ):
                updated = current.model_copy(deep=True)
                # Drivers own hot-page state, not quota or renewal metadata.
                for key in ("hot_page",):
                    if account.meta.get(key) != lease[2].get(key):
                        if key in account.meta:
                            updated.meta[key] = deepcopy(account.meta[key])
                        else:
                            updated.meta.pop(key, None)
                self._apply_result(updated, error)
                await self._commit({**self._accounts, account.id: updated})
            else:
                self._cond.notify_all()

    def _apply_result(self, acc: Account, error: BaseException | None) -> None:
        if error is None:
            acc.success_count += 1
            acc.last_error = None
            if acc.status == AccountStatus.COOLING and acc.is_available():
                acc.status = AccountStatus.ACTIVE
            return
        acc.fail_count += 1
        acc.last_error = type(error).__name__
        if acc.status == AccountStatus.INVALID:
            return
        if isinstance(error, UpstreamAuthError):
            acc.status = AccountStatus.INVALID
            log.warning("account %s marked invalid", acc.id)
        elif isinstance(error, UpstreamQuotaError):
            acc.status = AccountStatus.COOLING
            acc.cooldown_until = time.time() + max(self.cooldown, 3600)
            log.warning("account %s out of quota, cooling down", acc.id)
        elif isinstance(error, UpstreamTimeout):
            # muse.ai being slow says nothing about the account; cooling it down
            # would only fail the other requests running on it in parallel.
            pass
        elif getattr(error, "retryable", False):
            acc.status = AccountStatus.COOLING
            acc.cooldown_until = time.time() + self.cooldown

    @asynccontextmanager
    async def lease(
        self, *, exclude: Iterable[str] = (), hint: str | None = None
    ) -> AsyncIterator[Lease]:
        acc = await self.acquire(exclude=exclude, hint=hint)
        lease = Lease(acc)
        try:
            yield lease
        except (asyncio.CancelledError, GeneratorExit) as exc:
            lease.error = exc
            raise
        except BaseException as exc:
            lease.error = lease.error or exc
            raise
        finally:
            cleanup = asyncio.create_task(self.release(acc, lease.error))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise
