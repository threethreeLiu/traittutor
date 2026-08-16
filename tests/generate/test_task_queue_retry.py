"""Regression: a user-driven retry must clear a sticky cancel flag.

``_TaskStore.put`` merges ``cancel_requested`` with ``MAX(existing,
excluded)`` so a stale in-flight persist cannot wipe a mid-run cancellation
request. Without an explicit clear on retry, a task that was cancelled and
then failed on its own stays poisoned: every retry is instantly
re-cancelled at the start-of-run boundary.

Also covers cancel(): when this manager instance owns the live runner, the
runner task must actually be cancelled, not just flagged in the database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from traittutor.generate.service import GenerationRequest, MaterialSource
from traittutor.generate.tasks import GenerationTask, GenerationTaskManager


def _manager(tmp_path: Path, monkeypatch) -> GenerationTaskManager:
    manager = GenerationTaskManager(storage_root=tmp_path)
    # Keep the DB-focused test free of a running event loop / real runs.
    monkeypatch.setattr(manager, "_schedule", lambda: None)
    return manager


def _running_task() -> GenerationTask:
    task = GenerationTask(
        generation_id="gen-retry-cancel",
        owner_id="user-1",
        request=GenerationRequest(
            "courseware", MaterialSource("paste", "Limits are foundational.", "Limits")
        ),
    )
    task.status = "running"
    return task


def test_retry_clears_sticky_cancel_flag(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _running_task()
    manager._tasks[task.generation_id] = task
    manager._persist(task)

    # Cancel while running, then let the run fail on its own: the failing
    # runner persists a failed record while the DB keeps the cancel flag
    # (that is the MAX() merge working as designed).
    manager._store.request_cancel(task.generation_id)
    assert manager._store.load(task.generation_id).cancel_requested is True

    task.status = "failed"
    task.error_code = "provider_error"
    manager._persist(task)
    assert manager._store.load(task.generation_id).cancel_requested is True  # sticky

    # The user retries: the fresh intent must clear the flag, otherwise
    # ``_run`` re-raises CancelledError at its start-of-run check.
    retried = manager.retry(task.generation_id)
    assert retried is not None
    assert retried.status == "queued"
    assert retried.cancel_requested is False
    stored = manager._store.load(task.generation_id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.cancel_requested is False


def test_retry_requires_retryable_state(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _running_task()
    task.status = "completed"
    task.result = None
    manager._tasks[task.generation_id] = task
    manager._persist(task)

    retried = manager.retry(task.generation_id)
    assert retried is not None
    assert retried.status == "completed"


@pytest.mark.asyncio
async def test_cancel_cancels_live_runner_on_this_instance(tmp_path: Path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = _running_task()
    manager._tasks[task.generation_id] = task
    manager._persist(task)

    async def endless() -> None:
        await asyncio.sleep(30)

    task.runner = asyncio.ensure_future(endless())
    try:
        updated = manager.cancel(task.generation_id)
        assert updated is not None
        # DB flag set (cross-instance channel) ...
        assert manager._store.load(task.generation_id).cancel_requested is True
        # ... and the locally owned runner actually stops.
        with pytest.raises(asyncio.CancelledError):
            await task.runner
    finally:
        if not task.runner.done():
            task.runner.cancel()
