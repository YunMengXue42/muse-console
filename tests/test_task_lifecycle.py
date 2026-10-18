"""Offline, deterministic coverage of task retention, cancellation and persistence."""

from __future__ import annotations

import asyncio
import contextvars
import json
from pathlib import Path

import pytest

from muse2api.errors import Muse2APIError
from muse2api.services.tasks import Task, TaskManager, TaskStatus


def persisted(manager):
    return {item["id"]: item for item in json.loads(manager.path.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("max_kept", [0, 1, 2])
def test_retention_only_prunes_finished_history(tmp_path, max_kept):
    manager = TaskManager(tmp_path / "tasks.json", max_kept=max_kept)
    tasks = [
        Task(kind="test", status=status, created_at=index)
        for index, status in enumerate(
            [
                TaskStatus.QUEUED,
                TaskStatus.RUNNING,
                TaskStatus.SUCCEEDED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ]
        )
    ]
    manager._tasks = {task.id: task for task in tasks}
    manager._persist()
    expected = tasks[:2] + (tasks[-max_kept:] if max_kept else [])
    assert set(persisted(manager)) == {task.id for task in expected}
    assert {task.id for task in manager.list()} == set(persisted(manager))


async def test_cancel_before_first_step_keeps_hook_context(tmp_path):
    context = contextvars.ContextVar("request", default="other")
    finished = []
    called = False

    async def hook(task):
        finished.append((task.status, context.get()))

    async def runner(progress):
        nonlocal called
        called = True
        return {}

    manager = TaskManager(tmp_path / "tasks.json", on_finish=hook)
    token = context.set("submitter")
    task = manager.submit("test", {}, runner)
    context.reset(token)
    assert await manager.cancel(task.id)
    assert not called
    assert task.status == TaskStatus.CANCELLED
    assert not manager._running
    assert not manager._started
    assert persisted(manager)[task.id]["status"] == "cancelled"
    assert finished == [(TaskStatus.CANCELLED, "submitter")]
    assert not await manager.cancel(task.id)
    assert not await manager.cancel("missing")


async def test_cancel_waits_for_cleanup_and_hook(tmp_path):
    entered, cleaning, release, hooked = (asyncio.Event() for _ in range(4))

    async def runner(progress):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    async def hook(task):
        await asyncio.sleep(0)
        hooked.set()

    manager = TaskManager(tmp_path / "tasks.json", on_finish=hook)
    task = manager.submit("test", {}, runner)
    await asyncio.wait_for(entered.wait(), 2)
    cancelling = asyncio.create_task(manager.cancel(task.id))
    await asyncio.wait_for(cleaning.wait(), 2)
    assert not cancelling.done()
    release.set()
    assert await asyncio.wait_for(cancelling, 2)
    assert hooked.is_set()
    assert not manager._running
    assert persisted(manager)[task.id]["status"] == "cancelled"


async def test_concurrency_is_bounded_and_waiters_are_cancellable(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", max_kept=1, max_concurrent=2)
    ready, release = asyncio.Event(), asyncio.Event()
    active = peak = calls = 0

    async def runner(progress):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        if active == 2:
            ready.set()
        try:
            await release.wait()
            return {"ok": True}
        finally:
            active -= 1

    tasks = [manager.submit("test", {}, runner) for _ in range(8)]
    workers = list(manager._running.values())
    await asyncio.wait_for(ready.wait(), 2)
    assert all(task.status == TaskStatus.QUEUED for task in tasks[2:])
    assert len(manager.list()) == 8
    assert len(persisted(manager)) == 8
    assert await manager.cancel(tasks[2].id)
    assert tasks[2].status == TaskStatus.CANCELLED
    release.set()
    await asyncio.wait_for(asyncio.gather(*workers), 2)
    assert peak == 2
    assert calls == 7
    assert not manager._running
    assert len(persisted(manager)) == 1


@pytest.mark.parametrize("started", [False, True])
async def test_shutdown_finalizes_all_tasks(tmp_path, started):
    manager = TaskManager(tmp_path / "tasks.json", max_concurrent=1)
    entered = asyncio.Event()
    finished = []

    async def hook(task):
        finished.append(task.id)

    async def runner(progress):
        entered.set()
        await asyncio.Event().wait()

    manager.on_finish = hook
    tasks = [manager.submit("test", {}, runner) for _ in range(5)]
    if started:
        await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(manager.shutdown(), 2)
    assert not manager._running
    assert all(task.status == TaskStatus.CANCELLED for task in tasks)
    assert set(finished) == {task.id for task in tasks}
    assert all(item["status"] == "cancelled" for item in persisted(manager).values())


@pytest.mark.parametrize("outcome", ["success", "domain", "internal"])
async def test_outcomes_and_finish_hook(tmp_path, outcome):
    finished = []

    async def hook(task):
        finished.append(task.status)

    async def runner(progress):
        progress(150)
        assert task.progress == 99
        if outcome == "domain":
            raise Muse2APIError("offline failure")
        if outcome == "internal":
            raise RuntimeError("offline crash")
        return {"value": 1}

    manager = TaskManager(tmp_path / "tasks.json", on_finish=hook)
    task = manager.submit("test", {}, runner)
    await manager._running[task.id]
    expected = TaskStatus.SUCCEEDED if outcome == "success" else TaskStatus.FAILED
    assert task.status == expected
    assert finished == [expected]
    assert persisted(manager)[task.id]["status"] == expected.value
    if outcome == "success":
        assert task.result == {"value": 1}
        assert task.progress == 100
    else:
        assert task.error["message"].startswith("offline")


async def test_persist_failure_is_visible_and_still_finishes_hook(tmp_path, monkeypatch, caplog):
    finished = []

    async def hook(task):
        finished.append(task.id)

    async def runner(progress):
        return {}

    manager = TaskManager(tmp_path / "tasks.json", on_finish=hook)
    task = manager.submit("test", {}, runner)
    worker = manager._running[task.id]

    def fail_replace(self, target):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        await worker
    assert finished == [task.id]
    assert not manager._running
    assert "failed to persist tasks" in caplog.text
    with pytest.raises(OSError, match="disk unavailable"):
        manager.submit("test", {}, runner)
    assert not manager._running
    assert manager.list() == [task]


async def test_cancel_waits_for_hook_even_when_history_was_pruned(tmp_path):
    entered, release = asyncio.Event(), asyncio.Event()

    async def hook(task):
        entered.set()
        await release.wait()

    async def runner(progress):
        return {}

    manager = TaskManager(tmp_path / "tasks.json", max_kept=0, on_finish=hook)
    task = manager.submit("test", {}, runner)
    await asyncio.wait_for(entered.wait(), 2)
    assert manager.get(task.id) is None
    cancelling = asyncio.create_task(manager.cancel(task.id))
    await asyncio.sleep(0)
    assert not cancelling.done()
    release.set()
    assert await asyncio.wait_for(cancelling, 2)
    assert task.status == TaskStatus.SUCCEEDED
    assert not manager._running


@pytest.mark.parametrize("kwargs", [{"max_kept": -1}, {"max_concurrent": 0}])
def test_invalid_limits(tmp_path, kwargs):
    with pytest.raises(ValueError):
        TaskManager(tmp_path / "tasks.json", **kwargs)
