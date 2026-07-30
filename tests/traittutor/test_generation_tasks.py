from __future__ import annotations

import asyncio
from threading import Event

import pytest

from traittutor.generate.service import GenerationRequest, GenerationResult, MaterialSource
from traittutor.generate.tasks import GenerationTask, GenerationTaskManager


def _request() -> GenerationRequest:
    return GenerationRequest("flashcards", MaterialSource("paste", "材料", "标题"))


@pytest.mark.asyncio
async def test_task_emits_accepted_before_background_generation(tmp_path):
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    def generator(_request: GenerationRequest) -> GenerationResult:
        return GenerationResult("internal", "flashcards", "completed", [{"type": "batch_validated", "data": {"count": 1}}], {"items": []}, "now", "prompt", {}, {})

    token = set_current_user(local_admin_user())
    try:
        manager = GenerationTaskManager(generator, storage_root=tmp_path)
        task = manager.create(_request())
        assert task.events[0]["type"] == "accepted"

        events = [event async for event in manager.events_after(task.generation_id)]
        assert [event["type"] for event in events][:4] == ["accepted", "material_resolved", "profile_strategy_ready", "generation_started"]
        assert events[-1]["type"] == "completed"
        assert task.result is not None
        assert task.result.generation_id == task.generation_id
    finally:
        reset_current_user(token)


def _result() -> GenerationResult:
    return GenerationResult("internal", "flashcards", "completed", [], {"items": []}, "now", "prompt", {}, {})


@pytest.mark.asyncio
async def test_task_queue_limits_concurrent_generation_per_user(tmp_path):
    """A second request stays queued until the first worker releases its slot."""
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    first_started = Event()
    release_first = Event()
    calls: list[str] = []

    def generator(request: GenerationRequest) -> GenerationResult:
        calls.append(request.material.title)
        if request.material.title == "first":
            first_started.set()
            assert release_first.wait(timeout=2)
        return _result()

    token = set_current_user(local_admin_user())
    try:
        manager = GenerationTaskManager(generator, max_concurrent_per_user=1, storage_root=tmp_path)
        first = manager.create(GenerationRequest("flashcards", MaterialSource("paste", "材料", "first")))
        second = manager.create(GenerationRequest("flashcards", MaterialSource("paste", "材料", "second")))
        await asyncio.to_thread(first_started.wait, 1)
        assert manager.get(first.generation_id).status == "running"  # type: ignore[union-attr]
        assert manager.get(second.generation_id).status == "queued"  # type: ignore[union-attr]
        assert calls == ["first"]

        release_first.set()
        second_events = [event async for event in manager.events_after(second.generation_id)]
        assert second_events[-1]["type"] == "completed"
        assert calls == ["first", "second"]
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_running_task_is_recovered_as_retryable_interruption(tmp_path):
    """A record left running by a stopped process remains queryable and retryable."""
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    token = set_current_user(local_admin_user())
    try:
        first_manager = GenerationTaskManager(storage_root=tmp_path)
        task = GenerationTask("recover-me", "local-admin", _request(), status="running")
        task.emit("generation_started", "Generating structured learning content")
        first_manager._persist(task)

        restarted_manager = GenerationTaskManager(storage_root=tmp_path)
        await restarted_manager.start()
        recovered = restarted_manager.get("recover-me")
        assert recovered is not None
        assert recovered.status == "interrupted"
        assert recovered.retryable is True
        assert recovered.error_code == "generation_interrupted"
        assert recovered.events[-1]["type"] == "interrupted"
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_completed_task_can_be_queried_from_durable_record(tmp_path):
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    token = set_current_user(local_admin_user())
    try:
        manager = GenerationTaskManager(lambda _request: _result(), storage_root=tmp_path)
        task = manager.create(_request())
        events = [event async for event in manager.events_after(task.generation_id)]
        assert events[-1]["type"] == "completed"

        restarted_manager = GenerationTaskManager(storage_root=tmp_path)
        recovered = restarted_manager.get(task.generation_id)
        assert recovered is not None
        assert recovered.status == "completed"
        assert recovered.result is not None
        assert recovered.result.generation_id == task.generation_id
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_sse_replay_does_not_lose_wakeup_emitted_after_event_scan(tmp_path):
    """An emit in the scan-to-wait window must wake the next SSE iteration."""
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    token = set_current_user(local_admin_user())
    try:
        manager = GenerationTaskManager(storage_root=tmp_path)
        task = GenerationTask("sse-race", "local-admin", _request(), status="running")
        task.emit("accepted", "Generation request accepted")

        class EmitAfterScanEvents(list[dict[str, object]]):
            emitted = False

            def __iter__(self):
                snapshot = self[:]

                def _iterate():
                    yield from snapshot
                    if not self.emitted:
                        self.emitted = True
                        task.emit("generation_started", "Generating structured learning content")

                return _iterate()

        task.events = EmitAfterScanEvents(task.events)
        manager._tasks[task.generation_id] = task
        manager._persist(task)
        # Keep this unit test focused on the event-loop wake protocol rather
        # than replacing the instrumented in-memory event list from SQLite.
        manager.get = lambda generation_id: task if generation_id == task.generation_id else None  # type: ignore[method-assign]
        stream = manager.events_after(task.generation_id)
        assert (await anext(stream))["type"] == "accepted"
        # The second event is emitted after the first scan. With a clear-after-
        # scan implementation this call blocks forever; the clear-before-scan
        # protocol retains the wake signal.
        assert (await asyncio.wait_for(anext(stream), timeout=0.2))["type"] == "generation_started"
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_shared_sqlite_claim_allows_only_one_instance_to_run_same_task(tmp_path):
    """Two API instances share one claim record; only one may execute it."""
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    started, release = Event(), Event()
    calls: list[str] = []

    def generator(_request: GenerationRequest) -> GenerationResult:
        calls.append("run")
        started.set()
        assert release.wait(timeout=2)
        return _result()

    token = set_current_user(local_admin_user())
    try:
        first = GenerationTaskManager(generator, storage_root=tmp_path, max_concurrent=1)
        second = GenerationTaskManager(generator, storage_root=tmp_path, max_concurrent=1)
        task = GenerationTask("shared-claim", "local-admin", _request(), status="queued")
        task.emit("accepted", "Generation request accepted")
        first._persist(task)
        first._schedule()
        second._schedule()
        assert await asyncio.to_thread(started.wait, 1)
        assert calls == ["run"]
        release.set()
        events = [event async for event in first.events_after(task.generation_id)]
        assert events[-1]["type"] == "completed"
        assert calls == ["run"]
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_sse_last_event_id_takes_precedence_over_legacy_query(monkeypatch):
    from traittutor.api.routers import traittutor_generate as router_module

    seen: list[int] = []

    class FakeManager:
        def get(self, _generation_id):
            return object()

        async def events_after(self, _generation_id, after_sequence=0):
            seen.append(after_sequence)
            yield {"sequence": after_sequence + 1, "type": "completed", "message": "done", "data": {}}

    monkeypatch.setattr(router_module, "get_generation_task_manager", lambda: FakeManager())
    response = await router_module.stream_generation_events("task", after_seq=1, last_event_id="7")
    body = "".join([chunk async for chunk in response.body_iterator])
    assert seen == [7]
    assert "id: 8" in body


@pytest.mark.asyncio
async def test_remote_instance_cancel_is_persisted_and_worker_finishes_cancelled(tmp_path):
    """Cancellation reaches the instance that owns the provider call."""
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    started, release = Event(), Event()

    def generator(_request: GenerationRequest) -> GenerationResult:
        started.set()
        assert release.wait(timeout=2)
        return _result()

    token = set_current_user(local_admin_user())
    try:
        worker = GenerationTaskManager(generator, storage_root=tmp_path)
        remote = GenerationTaskManager(generator, storage_root=tmp_path)
        task = worker.create(_request())
        assert await asyncio.to_thread(started.wait, 1)
        cancellation = remote.cancel(task.generation_id)
        assert cancellation is not None
        assert cancellation.status == "running"
        assert cancellation.cancel_requested is True
        release.set()
        events = [event async for event in worker.events_after(task.generation_id)]
        assert events[-1]["type"] == "cancelled"
        assert worker.get(task.generation_id).status == "cancelled"  # type: ignore[union-attr]
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_legacy_generate_endpoint_submits_durable_task(monkeypatch):
    from types import SimpleNamespace
    from traittutor.api.routers import traittutor_generate as router_module

    submitted: list[GenerationRequest] = []

    class FakeManager:
        def create(self, request):
            submitted.append(request)
            return SimpleNamespace(generation_id="durable-id", status="queued")

    monkeypatch.setattr(router_module, "get_generation_task_manager", lambda: FakeManager())
    response = await router_module.generate_suite(
        router_module.GenerateSuiteRequest(generation_type="flashcards", material={"source_type": "paste", "text": "材料"})
    )
    assert submitted and submitted[0].generation_type == "flashcards"
    assert response["generation_id"] == "durable-id"


@pytest.mark.asyncio
async def test_rotating_model_routes_share_one_queue_slot(tmp_path):
    """Different primaries cannot oversubscribe a common fallback model."""
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user

    started, release = Event(), Event()
    calls: list[str] = []

    def generator(request: GenerationRequest) -> GenerationResult:
        calls.append(str((request.options or {}).get("model")))
        if len(calls) == 1:
            started.set()
            assert release.wait(timeout=2)
        return _result()

    token = set_current_user(local_admin_user())
    try:
        manager = GenerationTaskManager(generator, storage_root=tmp_path, max_concurrent=2, max_concurrent_per_model=1)
        first = manager.create(GenerationRequest("flashcards", MaterialSource("paste", "材料", "first"), options={"model": "primary-a"}))
        second = manager.create(GenerationRequest("flashcards", MaterialSource("paste", "材料", "second"), options={"model": "primary-b"}))
        assert await asyncio.to_thread(started.wait, 1)
        assert manager.get(second.generation_id).status == "queued"  # type: ignore[union-attr]
        release.set()
        events = [event async for event in manager.events_after(second.generation_id)]
        assert events[-1]["type"] == "completed"
        assert calls == ["primary-a", "primary-b"]
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_prompt_configuration_failure_is_not_retryable(tmp_path):
    from traittutor.multi_user.context import reset_current_user, set_current_user
    from traittutor.multi_user.paths import local_admin_user
    from traittutor.services.prompt import PromptLoadError

    def generator(_request: GenerationRequest) -> GenerationResult:
        raise PromptLoadError("missing prompt")

    token = set_current_user(local_admin_user())
    try:
        manager = GenerationTaskManager(generator, storage_root=tmp_path)
        task = manager.create(_request())
        events = [event async for event in manager.events_after(task.generation_id)]
        assert events[-1]["type"] == "failed"
        stored = manager.get(task.generation_id)
        assert stored.error_code == "prompt_configuration_invalid"  # type: ignore[union-attr]
        assert stored.retryable is False  # type: ignore[union-attr]
    finally:
        reset_current_user(token)
