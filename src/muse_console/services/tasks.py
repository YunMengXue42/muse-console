"""Background task manager for long-running jobs (video generation, batch images, ...)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..errors import CapacityExceeded, MuseConsoleError

log = logging.getLogger(__name__)

Runner = Callable[[Callable[[int], None]], Awaitable[dict[str, Any]]]


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task(BaseModel):
    id: str = Field(default_factory=lambda: "task_" + uuid.uuid4().hex[:16])
    kind: str
    owner_key_id: str | None = None
    status: TaskStatus = TaskStatus.QUEUED
    progress: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    request: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    @property
    def finished(self) -> bool:
        return self.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class TaskManager:
    def __init__(
        self,
        path: Path,
        *,
        max_kept: int = 500,
        max_concurrent: int = 4,
        max_pending: int = 32,
        on_finish: Callable[[Task], Awaitable[None]] | None = None,
    ) -> None:
        if max_kept < 0 or max_concurrent < 1 or max_pending < 1:
            raise ValueError("invalid task history, concurrency or pending budget")
        self.path = path
        self.max_kept = max_kept
        self.max_pending = max_pending
        self._slots = asyncio.Semaphore(max_concurrent)
        # Runs in the task's own context, so it still sees the submitting request's state.
        self.on_finish = on_finish
        self._tasks: dict[str, Task] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._started: set[str] = set()

    async def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            items = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            log.warning("tasks file corrupted, starting empty")
            return
        for item in items:
            task = Task.model_validate(item)
            if not task.finished:
                task.status = TaskStatus.FAILED
                task.error = {"message": "interrupted by server restart", "code": "interrupted"}
            self._tasks[task.id] = task

    def _persist(self) -> None:
        history = sorted(
            (t for t in self._tasks.values() if t.finished), key=lambda t: t.created_at
        )
        kept = history[-self.max_kept :] if self.max_kept else []
        tasks = sorted(
            [t for t in self._tasks.values() if not t.finished] + kept,
            key=lambda t: t.created_at,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            if tmp.is_symlink():
                raise ValueError("task snapshot must not be a symlink")
            with tmp.open("w", encoding="utf-8") as stream:
                os.chmod(tmp, 0o600)
                stream.write(
                    json.dumps([t.model_dump(mode="json") for t in tasks], ensure_ascii=False)
                )
            tmp.replace(self.path)
        except Exception:  # noqa: BLE001
            log.exception("failed to persist tasks to %s", self.path)
            raise
        self._tasks = {t.id: t for t in tasks}

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list(self, limit: int = 100) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)[:limit]

    def get_owned(self, task_id: str, owner_key_id: str) -> Task | None:
        """hide foreign and ownerless legacy tasks from ordinary API keys."""
        task = self.get(task_id)
        if task is None or (owner_key_id != "admin" and task.owner_key_id != owner_key_id):
            return None
        return task

    def stats(self) -> dict[str, int]:
        return {
            "pending": len(self._running),
            "running": sum(task.status == TaskStatus.RUNNING for task in self._tasks.values()),
            "max_pending": self.max_pending,
        }

    def submit(
        self, kind: str, request: dict[str, Any], runner: Runner, *, owner_key_id: str | None = None
    ) -> Task:
        """reserve a bounded queue entry before creating a worker or persisting its input."""
        if len(self._running) >= self.max_pending:
            raise CapacityExceeded("task queue is full; retry after existing jobs finish")
        task = Task(kind=kind, request=request, owner_key_id=owner_key_id)
        self._tasks[task.id] = task
        try:
            self._persist()
        except Exception:
            self._tasks.pop(task.id, None)
            raise
        worker = asyncio.create_task(self._run(task, runner))
        self._running[task.id] = worker
        worker.add_done_callback(self._worker_done)
        return task

    @staticmethod
    def _worker_done(worker: asyncio.Task) -> None:
        if not worker.cancelled() and (exc := worker.exception()) is not None:
            log.error("task worker failed", exc_info=(type(exc), exc, exc.__traceback__))

    async def _run(self, task: Task, runner: Runner) -> None:
        def progress(p: int) -> None:
            task.progress = max(task.progress, min(99, int(p)))
            task.updated_at = time.time()

        self._started.add(task.id)
        try:
            if task.status == TaskStatus.CANCELLED:
                return
            async with self._slots:
                task.status = TaskStatus.RUNNING
                task.updated_at = time.time()
                result = await runner(progress)
                if task.status != TaskStatus.CANCELLED:
                    task.result = result
                    task.status = TaskStatus.SUCCEEDED
                    task.progress = 100
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
        except MuseConsoleError as exc:
            task.status = TaskStatus.FAILED
            task.error = {"message": exc.message, "code": exc.code}
        except Exception:  # noqa: BLE001
            log.exception("task %s crashed", task.id)
            task.status = TaskStatus.FAILED
            task.error = {"message": "internal task error", "code": "internal_error"}
        finally:
            task.updated_at = time.time()
            try:
                try:
                    self._persist()
                finally:
                    if self.on_finish:
                        try:
                            await self.on_finish(task)
                        except Exception:  # noqa: BLE001
                            log.exception("on_finish hook failed for task %s", task.id)
            finally:
                self._running.pop(task.id, None)
                self._started.discard(task.id)

    async def cancel(self, task_id: str) -> bool:
        """Wait for local cleanup; cancelling an upstream job is not guaranteed."""
        running = self._running.get(task_id)
        if running is None:
            return False
        task = self._tasks.get(task_id)
        if task is not None and not task.finished:
            task.status = TaskStatus.CANCELLED
            # A coroutine cancelled before its first step cannot execute its finally.
            # Let it start and finalize (including on_finish in the original context).
            if task_id in self._started:
                running.cancel()
        await asyncio.shield(running)
        return True

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self.cancel(task_id) for task_id in list(self._running)), return_exceptions=True
        )
