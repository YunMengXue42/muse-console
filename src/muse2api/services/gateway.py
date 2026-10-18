"""Gateway: runs driver calls on pooled accounts with automatic failover."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager
from typing import TypeVar

from ..accounts.model import Account
from ..accounts.pool import AccountPool, Lease
from ..drivers.base import ChatRequest, ImageRequest, MediaResult, MuseDriver, VideoRequest
from ..errors import UpstreamError
from .request_log import note_account

log = logging.getLogger(__name__)
T = TypeVar("T")

_ANONYMOUS = Account(id="anonymous", label="driver without accounts")


class Gateway:
    def __init__(self, pool: AccountPool, driver: MuseDriver, *, max_failover: int = 2) -> None:
        self.pool = pool
        self.driver = driver
        self.max_failover = max_failover
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._conversation_refs: dict[str, int] = {}
        self._conversation_guard = asyncio.Lock()

    @asynccontextmanager
    async def _lease(self, exclude: list[str], hint: str | None) -> AsyncIterator[Lease]:
        if not self.driver.requires_account and not self.pool.all():
            note_account(_ANONYMOUS.id)
            yield Lease(_ANONYMOUS)
            return
        async with self.pool.lease(exclude=exclude, hint=hint) as lease:
            note_account(lease.account.id)
            yield lease

    def _should_retry(self, exc: UpstreamError, attempt: int, tried: list[str]) -> bool:
        # With no other account to switch to, a retry would only replace the real
        # error with "no usable account in pool".
        return exc.retryable and attempt < self.max_failover and self.pool.has_alternative(tried)

    @asynccontextmanager
    async def _conversation_lock(self, hint: str | None) -> AsyncIterator[None]:
        """Serialize non-empty conversation streams without retaining idle locks."""
        if not hint:
            yield
            return
        async with self._conversation_guard:
            lock = self._conversation_locks.setdefault(hint, asyncio.Lock())
            self._conversation_refs[hint] = self._conversation_refs.get(hint, 0) + 1
        acquired = False
        try:
            await lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            async with self._conversation_guard:
                refs = self._conversation_refs[hint] - 1
                if refs == 0:
                    self._conversation_refs.pop(hint, None)
                    self._conversation_locks.pop(hint, None)
                else:
                    self._conversation_refs[hint] = refs

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        async with self._conversation_lock(req.conversation_hint):
            tried: list[str] = []
            for attempt in range(self.max_failover + 1):
                async with self._lease(tried, req.conversation_hint) as lease:
                    emitted = False
                    try:
                        async with aclosing(
                            self.driver.chat_stream(lease.account, req)
                        ) as upstream:
                            async for delta in upstream:
                                if delta:
                                    emitted = True
                                yield delta
                        return
                    except UpstreamError as exc:
                        lease.fail(exc)
                        tried.append(lease.account.id)
                        # Once text reached the client we cannot transparently switch accounts.
                        if emitted or not self._should_retry(exc, attempt, tried):
                            raise
                        log.warning(
                            "chat failed on %s (%s), failing over", lease.account.id, exc.code
                        )

    async def _run(
        self,
        fn: Callable[[Account], Awaitable[T]],
        hint: str | None = None,
        *,
        retry: bool = True,
    ) -> T:
        # Media failures can mean a submission succeeded but its result is unknown,
        # or only part of a multi-image request completed. Never replay the call
        # without an explicit driver contract confirming that nothing was submitted.
        async with self._lease([], hint) as lease:
            try:
                return await fn(lease.account)
            except UpstreamError as exc:
                lease.fail(exc)
                raise

    async def generate_image(self, req: ImageRequest) -> list[MediaResult]:
        return await self._run(lambda acc: self.driver.generate_image(acc, req))

    async def generate_video(self, req: VideoRequest) -> MediaResult:
        return await self._run(lambda acc: self.driver.generate_video(acc, req))
