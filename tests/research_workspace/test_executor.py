from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from pathlib import Path
from threading import Event, Thread
import time

from pydantic import ValidationError
import pytest

from traittutor.api.routers import research_workspace as research_router
from traittutor.gateway.service import (
    GatewayReceipt,
    GatewayRequest,
    GatewayResponse,
    GatewayStreamEvent,
    GatewayToolCall,
)
from traittutor.multi_user.models import CurrentUser, UserScope
from traittutor.research_workspace.executor import (
    GatewayResearchExecutor,
    ResearchClaimDraft,
    ResearchExecutionResult,
    ResearchExecutionTask,
    ResearchGatewayExecutionConfig,
    ResearchSourceDraft,
)
from traittutor.research_workspace.scheduler import ResearchRunScheduler
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.store import ResearchWorkspaceStore
from traittutor.research_workspace.worker import (
    ResearchProgressEvent,
    ResearchWorkspaceWorker,
)

T0 = "2026-08-10T00:00:00+00:00"


class FakeExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.tasks: list[ResearchExecutionTask] = []

    def execute(self, task: ResearchExecutionTask) -> ResearchExecutionResult:
        self.tasks.append(task)
        if self.fail:
            raise RuntimeError("provider error containing secret-do-not-persist")
        return ResearchExecutionResult(
            sources=(
                ResearchSourceDraft(
                    source_key="primary",
                    url="https://example.org/primary",
                    title="Primary source",
                    excerpt="Public evidence",
                ),
            ),
            claims=(
                ResearchClaimDraft(
                    claim_key="claim-1",
                    text="The cited source supports this statement.",
                    kind="grounded",
                    source_keys=("primary",),
                ),
            ),
            report_body="A report with one grounded claim.",
            report_claim_keys=("claim-1",),
        )


class BlockingExecutor(FakeExecutor):
    """A bounded test executor whose late output must remain fenced."""

    def __init__(self) -> None:
        super().__init__()
        self.started = Event()
        self.release = Event()

    def execute(self, task: ResearchExecutionTask) -> ResearchExecutionResult:
        self.started.set()
        assert self.release.wait(timeout=2), "test must release the executor"
        return super().execute(task)


class NeedsReviewExecutor(FakeExecutor):
    def execute(self, task: ResearchExecutionTask) -> ResearchExecutionResult:
        return super().execute(task).model_copy(update={"requires_review": True})


class ReceiptCheckingSink:
    def __init__(self, store: ResearchWorkspaceStore) -> None:
        self.store = store
        self.events: list[ResearchProgressEvent] = []

    def publish(self, event: ResearchProgressEvent) -> None:
        assert any(
            receipt.receipt_id == event.receipt_id
            for receipt in self.store.list_receipts(event.run_id)
        )
        self.events.append(event)


class FakeGateway:
    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[GatewayRequest] = []

    async def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.requests.append(request)
        return GatewayResponse(
            request_id="gateway-request",
            content=self.content,
            model="fake-model",
            purpose=request.purpose,
            latency_ms=1,
            receipt=_stream_receipt(),
        )

    async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayStreamEvent]:
        self.requests.append(request)
        yield GatewayStreamEvent(type="text", text=self.content)
        yield _stream_final()


class FailingGateway(FakeGateway):
    def __init__(self, error: Exception) -> None:
        super().__init__("unused")
        self.error = error

    async def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.requests.append(request)
        raise self.error

    async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayStreamEvent]:
        self.requests.append(request)
        raise self.error
        yield  # pragma: no cover - keeps this an async generator


class StreamingGateway(FakeGateway):
    """Capture the stream path without turning stream artifacts into results."""

    def __init__(
        self,
        events: tuple[GatewayStreamEvent, ...],
        *,
        error: BaseException | None = None,
    ) -> None:
        super().__init__("legacy completion must not run")
        self.events = events
        self.error = error
        self.stream_requests: list[GatewayRequest] = []

    async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayStreamEvent]:
        self.stream_requests.append(request)
        if self.error is not None:
            raise self.error
        for event in self.events:
            yield event


def _stream_receipt() -> GatewayReceipt:
    return GatewayReceipt(
        request_id="stream-request",
        purpose="research_workspace",
        model="fake-model",
        provider="fake-provider",
        route="fake-route",
        latency_ms=1,
        timeout_seconds=1,
        response_format_applied=True,
        tools_applied=0,
        attachments_applied=0,
    )


def _stream_final() -> GatewayStreamEvent:
    return GatewayStreamEvent(type="final", receipt=_stream_receipt())


def _grounded_stream_json() -> str:
    return json.dumps(
        {
            "claims": [
                {
                    "claim_key": "claim-1",
                    "text": "The approved source supports the claim.",
                    "kind": "grounded",
                    "source_keys": ["approved"],
                }
            ],
            "report_body": "A grounded report.",
            "report_claim_keys": ["claim-1"],
            "requires_review": False,
        }
    )


class StaticValidatedSources:
    def __init__(self, *sources: ResearchSourceDraft) -> None:
        self.sources = sources

    def sources_for(self, task: ResearchExecutionTask) -> tuple[ResearchSourceDraft, ...]:
        del task
        return self.sources


class AsyncValidatedSources(StaticValidatedSources):
    async def sources_for(self, task: ResearchExecutionTask) -> tuple[ResearchSourceDraft, ...]:
        return super().sources_for(task)


def _service_with_run(
    tmp_path: Path,
) -> tuple[ResearchWorkspaceService, ResearchWorkspaceStore, str]:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    service = ResearchWorkspaceService(store)
    workspace = service.create_workspace(
        title="Worker test",
        subject_id=None,
        idempotency_key="workspace",
    )
    brief = service.save_brief(
        workspace.workspace_id,
        question="What should the executor research?",
        expected_workspace_revision=workspace.revision,
        idempotency_key="brief",
    )
    run = service.start_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        idempotency_key="run",
    )
    return service, store, run.run_id


def test_worker_uses_injected_executor_and_persists_before_progress(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    executor = FakeExecutor()
    sink = ReceiptCheckingSink(store)
    worker = ResearchWorkspaceWorker(
        service,
        executor,
        progress_sink=sink,
        clock=lambda: T0,
    )

    receipt = worker.run_once(run_id, worker_id="worker")

    assert receipt is not None
    assert receipt.outcome == "accepted"
    assert len(executor.tasks) == 1
    assert executor.tasks[0].brief.question == "What should the executor research?"
    assert sink.events[0].receipt_id == receipt.receipt_id
    assert service.get_run(run_id).status == "completed"  # type: ignore[union-attr]
    assert store.list_sources(executor.tasks[0].workspace_id)
    assert store.list_claims(run_id)


def test_executor_failure_is_bounded_and_does_not_persist_raw_error(tmp_path: Path) -> None:
    service, _, run_id = _service_with_run(tmp_path)
    worker = ResearchWorkspaceWorker(service, FakeExecutor(fail=True), clock=lambda: T0)

    receipt = worker.run_once(run_id, worker_id="worker")

    assert receipt is not None
    assert receipt.outcome == "failed"
    assert receipt.detail == "executor_failed"
    assert "secret-do-not-persist" not in json.dumps(
        service._store._adapter.snapshot(), ensure_ascii=False
    )
    assert service.get_run(run_id).status == "failed"  # type: ignore[union-attr]


def test_execution_result_rejects_unknown_source_reference() -> None:
    with pytest.raises(ValidationError, match="unknown source key"):
        ResearchExecutionResult(
            claims=(
                ResearchClaimDraft(
                    claim_key="claim",
                    text="Unsupported claim",
                    kind="grounded",
                    source_keys=("missing",),
                ),
            ),
            report_body="Invalid report",
            report_claim_keys=("claim",),
        )


def test_worker_recovers_an_expired_lease_with_a_new_fencing_epoch(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    old_claim = store.claim_run(
        run_id,
        worker_id="old-worker",
        lease_seconds=10,
        now=T0,
    )
    recovery_time = "2026-08-10T00:00:10+00:00"
    executor = FakeExecutor()
    worker = ResearchWorkspaceWorker(
        service,
        executor,
        clock=lambda: recovery_time,
    )

    receipts = worker.recover_claimable(worker_id="recovery-worker")

    assert len(receipts) == 1
    assert receipts[0].outcome == "accepted"
    assert executor.tasks[0].fencing_epoch == old_claim.fencing_epoch + 1
    assert executor.tasks[0].claim_token != old_claim.claim_token
    assert store.get_run(run_id).status == "completed"  # type: ignore[union-attr]


def test_failed_attempt_can_retry_after_fencing_epoch_advances(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    failed_worker = ResearchWorkspaceWorker(
        service,
        FakeExecutor(fail=True),
        clock=lambda: T0,
    )
    failed_receipt = failed_worker.run_once(run_id, worker_id="failed-worker")
    failed_run = store.get_run(run_id)
    assert failed_receipt is not None and failed_receipt.outcome == "failed"
    assert failed_run is not None

    queued = service.transition_run(
        run_id,
        "queued",
        expected_revision=failed_run.revision,
        idempotency_key="retry-run",
    )
    successful_worker = ResearchWorkspaceWorker(
        service,
        FakeExecutor(),
        clock=lambda: "2026-08-10T00:01:00+00:00",
    )
    accepted = successful_worker.run_once(run_id, worker_id="retry-worker")

    assert accepted is not None and accepted.outcome == "accepted"
    assert accepted.fencing_epoch == queued.fencing_epoch
    assert accepted.fencing_epoch > failed_receipt.fencing_epoch
    assert store.get_run(run_id).status == "completed"  # type: ignore[union-attr]
    assert [receipt.outcome for receipt in store.list_receipts(run_id)] == [
        "failed",
        "accepted",
    ]


def test_retry_after_needs_review_accepts_once_in_its_new_fenced_attempt(
    tmp_path: Path,
) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    first = ResearchWorkspaceWorker(service, NeedsReviewExecutor(), clock=lambda: T0).run_once(
        run_id, worker_id="review-worker"
    )
    reviewable = service.get_run(run_id)
    assert first is not None and first.outcome == "accepted"
    assert reviewable is not None and reviewable.status == "needs_review"

    queued = service.transition_run(
        run_id,
        "queued",
        expected_revision=reviewable.revision,
        idempotency_key="retry-needs-review",
    )
    winner = ResearchWorkspaceWorker(
        service, FakeExecutor(), clock=lambda: "2026-08-10T00:01:00+00:00"
    ).run_once(run_id, worker_id="retry-worker")
    duplicate = ResearchWorkspaceWorker(
        service, FakeExecutor(), clock=lambda: "2026-08-10T00:01:01+00:00"
    ).run_once(run_id, worker_id="duplicate-scheduler")

    accepted_new_epoch = [
        receipt
        for receipt in store.list_receipts(run_id)
        if receipt.outcome == "accepted" and receipt.fencing_epoch == queued.fencing_epoch
    ]
    assert winner is not None and winner.outcome == "accepted"
    assert duplicate is None
    assert len(accepted_new_epoch) == 1
    assert store.get_run(run_id).status == "completed"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("requested_status", "terminal_status"),
    (("pausing", "paused"), ("cancelling", "cancelled")),
)
def test_worker_settles_pause_or_cancel_and_discards_late_output(
    tmp_path: Path,
    requested_status: str,
    terminal_status: str,
) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    executor = BlockingExecutor()
    worker = ResearchWorkspaceWorker(
        service,
        executor,
        clock=lambda: T0,
        heartbeat_seconds=0.01,
    )
    receipts = []
    thread = Thread(
        target=lambda: receipts.append(worker.run_once(run_id, worker_id="worker")),
        daemon=True,
    )
    thread.start()
    assert executor.started.wait(timeout=1)

    claimed = service.get_run(run_id)
    assert claimed is not None and claimed.status == "running"
    requested = service.transition_run(
        run_id,
        requested_status,  # type: ignore[arg-type]
        expected_revision=claimed.revision,
        idempotency_key=f"request-{requested_status}",
    )
    assert requested.status == requested_status
    executor.release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt is not None and receipt.outcome == "discarded_stale"
    current = service.get_run(run_id)
    assert current is not None and current.status == terminal_status
    assert current.fencing_epoch == claimed.fencing_epoch + 1
    assert store.list_sources(claimed.workspace_id) == ()
    assert store.list_claims(run_id) == ()
    assert store.get_report(run_id) is None


def test_scheduler_discards_cancelled_inflight_result_without_creating_artifacts(
    tmp_path: Path,
) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    executor = BlockingExecutor()
    scheduler = ResearchRunScheduler(lambda: executor, worker_id="scheduled-worker")
    receipts = []
    thread = Thread(
        target=lambda: receipts.append(scheduler.schedule(service, run_id)), daemon=True
    )
    thread.start()
    assert executor.started.wait(timeout=1)
    claimed = service.get_run(run_id)
    assert claimed is not None
    service.transition_run(
        run_id,
        "cancelling",
        expected_revision=claimed.revision,
        idempotency_key="cancel-scheduled-run",
    )
    executor.release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert receipts[0] is not None and receipts[0].outcome == "discarded_stale"
    assert store.get_run(run_id).status == "cancelled"  # type: ignore[union-attr]
    assert store.list_sources(claimed.workspace_id) == ()
    assert store.get_report(run_id) is None


def test_resume_fences_late_worker_and_accepts_only_the_resumed_attempt(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    old_executor = BlockingExecutor()
    old_worker = ResearchWorkspaceWorker(
        service,
        old_executor,
        clock=lambda: T0,
        heartbeat_seconds=0.01,
    )
    old_receipts = []
    thread = Thread(
        target=lambda: old_receipts.append(old_worker.run_once(run_id, worker_id="old-worker")),
        daemon=True,
    )
    thread.start()
    assert old_executor.started.wait(timeout=1)
    claimed = service.get_run(run_id)
    assert claimed is not None
    pausing = service.transition_run(
        run_id,
        "pausing",
        expected_revision=claimed.revision,
        idempotency_key="pause-running",
    )
    paused = service.finalize_requested_lifecycle(run_id)
    resumed = service.transition_run(
        run_id,
        "queued",
        expected_revision=paused.revision,
        idempotency_key="resume-paused",
    )
    assert pausing.status == "pausing"
    assert resumed.status == "queued"

    resumed_receipt = ResearchWorkspaceWorker(
        service, FakeExecutor(), clock=lambda: "2026-08-10T00:00:20+00:00"
    ).run_once(run_id, worker_id="resumed-worker")
    old_executor.release.set()
    thread.join(timeout=2)

    assert resumed_receipt is not None and resumed_receipt.outcome == "accepted"
    assert not thread.is_alive()
    assert old_receipts[0] is not None and old_receipts[0].outcome == "discarded_stale"
    assert store.get_run(run_id).status == "completed"  # type: ignore[union-attr]
    assert len(store.list_sources(claimed.workspace_id)) == 1
    assert len(store.list_claims(run_id)) == 1
    assert len([item for item in store.list_receipts(run_id) if item.outcome == "accepted"]) == 1


def test_worker_renews_live_claim_before_original_lease_expires(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    executor = BlockingExecutor()
    times = iter(
        (
            "2026-08-10T00:00:00+00:00",
            "2026-08-10T00:00:09+00:00",
            "2026-08-10T00:00:15+00:00",
        )
    )
    worker = ResearchWorkspaceWorker(
        service,
        executor,
        clock=lambda: next(times, "2026-08-10T00:00:15+00:00"),
        heartbeat_seconds=0.01,
    )
    receipts = []
    thread = Thread(
        target=lambda: receipts.append(
            worker.run_once(run_id, worker_id="renewing-worker", lease_seconds=10)
        ),
        daemon=True,
    )
    thread.start()
    assert executor.started.wait(timeout=1)

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        current = service.get_run(run_id)
        if current is not None and current.lease_revision >= 3:
            break
        time.sleep(0.01)
    current = service.get_run(run_id)
    assert current is not None
    assert current.lease_revision >= 3
    assert current.revision == 2  # heartbeat did not invalidate lifecycle CAS
    # The heartbeat can make a second valid renewal before this observer reads
    # the run.  Verify the safety property (the lease moved beyond its
    # original 00:00:10 expiry) rather than racing on an exact final tick.
    assert current.lease_expires_at >= "2026-08-10T00:00:19+00:00"

    executor.release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert receipts[0] is not None and receipts[0].outcome == "accepted"
    assert store.get_run(run_id).status == "completed"  # type: ignore[union-attr]


def test_gateway_executor_accepts_only_injected_validated_sources(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    gateway = FakeGateway(
        """{
          "claims": [{
            "claim_key": "claim-1",
            "text": "The approved source supports the claim.",
            "kind": "grounded",
            "source_keys": ["approved"]
          }],
          "report_body": "A grounded report.",
          "report_claim_keys": ["claim-1"],
          "requires_review": false
        }"""
    )
    source = ResearchSourceDraft(
        source_key="approved",
        url="https://evidence.example/approved",
        title="Approved evidence",
        excerpt="Validated excerpt",
    )
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(source),
        config=ResearchGatewayExecutionConfig(),
        user_id="owner",
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-worker",
    )

    assert receipt is not None and receipt.outcome == "accepted"
    assert service.get_run(run_id).status == "completed"  # type: ignore[union-attr]
    current = store.get_run(run_id)
    assert current is not None
    assert [str(item.url) for item in store.list_sources(current.workspace_id)] == [
        "https://evidence.example/approved"
    ]
    assert gateway.requests[0].purpose == "research_workspace"
    assert gateway.requests[0].user_id == "owner"
    assert "approved" in gateway.requests[0].messages[-1].content


def test_gateway_executor_typed_messages_are_opt_in_and_bounded(tmp_path: Path) -> None:
    service, _, run_id = _service_with_run(tmp_path)
    gateway = FakeGateway(
        """{
          "claims": [{
            "claim_key": "claim-1",
            "text": "The approved source supports the claim.",
            "kind": "grounded",
            "source_keys": ["approved"]
          }],
          "report_body": "A grounded report.",
          "report_claim_keys": ["claim-1"],
          "requires_review": false
        }"""
    )
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(timeout_seconds=45),
        user_id="owner",
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-worker",
    )

    assert receipt is not None and receipt.outcome == "accepted"
    request = gateway.requests[0]
    assert request.prompt == request.system_prompt == ""
    assert [message.role for message in request.messages] == ["system", "user"]
    assert request.messages[0].content and "validated source bundle" in request.messages[0].content
    assert request.messages[1].content and "approved" in request.messages[1].content
    assert request.timeout_seconds == 45
    assert set(request.metadata) == {"workspace_id", "run_id", "task_id", "fencing_epoch"}
    assert request.metadata["run_id"] == run_id
    assert "approved" not in str(request.metadata)


def test_default_research_scheduler_uses_typed_stream_gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gateway = FakeGateway("unused")
    monkeypatch.setattr(research_router, "get_gateway", lambda: gateway)
    user = CurrentUser(
        id="owner",
        username="owner",
        role="user",
        scope=UserScope(kind="user", user_id="owner", root=tmp_path),
    )

    executor = research_router.default_research_scheduler_factory(user)._executor_factory()

    assert isinstance(executor, GatewayResearchExecutor)
    assert executor._config is not None  # type: ignore[attr-defined]


def test_stream_gateway_buffers_only_text_then_validates_and_commits(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    payload = _grounded_stream_json()
    gateway = StreamingGateway(
        (
            GatewayStreamEvent(type="reasoning", text="private-chain-of-thought"),
            GatewayStreamEvent(type="text", text=payload[:35]),
            GatewayStreamEvent(type="usage", usage={"input_tokens": 33}),
            GatewayStreamEvent(type="text", text=payload[35:]),
            _stream_final(),
        )
    )
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(timeout_seconds=45),
        user_id="owner",
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-stream-worker",
    )

    assert receipt is not None and receipt.outcome == "accepted"
    assert gateway.requests == []
    assert len(gateway.stream_requests) == 1
    request = gateway.stream_requests[0]
    assert request.user_id == "owner"
    assert request.timeout_seconds == 45
    assert request.metadata["run_id"] == run_id
    assert [str(item.url) for item in store.list_sources(request.metadata["workspace_id"])] == [
        "https://evidence.example/approved"
    ]
    persisted = json.dumps(store._adapter.snapshot(), ensure_ascii=False)
    assert "private-chain-of-thought" not in persisted
    assert "input_tokens" not in persisted
    assert "stream-request" not in persisted


def test_stream_gateway_accepts_one_exact_json_markdown_fence(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    payload = f"```json\n{_grounded_stream_json()}\n```"
    gateway = StreamingGateway((GatewayStreamEvent(type="text", text=payload), _stream_final()))
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="fenced-json-worker",
    )

    assert receipt is not None and receipt.outcome == "accepted"
    assert service.get_run(run_id).status == "completed"  # type: ignore[union-attr]
    assert len(store.list_claims(run_id)) == 1


def test_stream_gateway_rejects_text_outside_json_markdown_fence(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    payload = f"Result:\n```json\n{_grounded_stream_json()}\n```"
    gateway = StreamingGateway((GatewayStreamEvent(type="text", text=payload), _stream_final()))
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="ambiguous-fenced-json-worker",
    )

    assert receipt is not None and receipt.outcome == "failed"
    assert store.list_claims(run_id) == ()
    assert store.get_report(run_id) is None


@pytest.mark.parametrize(
    "events",
    [
        (
            GatewayStreamEvent(
                type="tool_call",
                tool_call=GatewayToolCall("call-1", "search", {"private_arg": "never-persist"}),
            ),
        ),
        (GatewayStreamEvent(type="cancelled", receipt=_stream_receipt()),),
        (GatewayStreamEvent(type="text", text=_grounded_stream_json()),),
        (
            _stream_final(),
            GatewayStreamEvent(type="text", text="late output"),
        ),
    ],
    ids=["tool-call", "cancelled", "missing-final", "event-after-final"],
)
def test_stream_gateway_incomplete_or_unexpected_events_persist_only_failure(
    tmp_path: Path,
    events: tuple[GatewayStreamEvent, ...],
) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    gateway = StreamingGateway(events)
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="failing-stream-worker",
    )

    assert receipt is not None and receipt.outcome == "failed"
    assert receipt.detail == "executor_failed"
    assert service.get_run(run_id).status == "failed"  # type: ignore[union-attr]
    assert gateway.requests == []
    assert len(gateway.stream_requests) == 1
    assert store.list_sources(gateway.stream_requests[0].metadata["workspace_id"]) == ()
    assert store.list_claims(run_id) == ()
    assert store.get_report(run_id) is None
    assert "never-persist" not in json.dumps(store._adapter.snapshot(), ensure_ascii=False)


@pytest.mark.parametrize(
    "error",
    [RuntimeError("provider failed with private context"), asyncio.CancelledError()],
    ids=["provider-error", "asyncio-cancelled"],
)
def test_stream_gateway_error_or_cancel_is_bounded_failure_without_legacy_retry(
    tmp_path: Path,
    error: BaseException,
) -> None:
    service, _, run_id = _service_with_run(tmp_path)
    gateway = StreamingGateway((), error=error)
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="errored-stream-worker",
    )

    assert receipt is not None and receipt.outcome == "failed"
    assert receipt.detail == "executor_failed"
    assert service.get_run(run_id).status == "failed"  # type: ignore[union-attr]
    assert gateway.requests == []
    assert len(gateway.stream_requests) == 1


def test_stream_gateway_reuses_the_validated_source_url_gate(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    payload = _grounded_stream_json().replace(
        "A grounded report.", "See https://invented.example/unsupported."
    )
    gateway = StreamingGateway((GatewayStreamEvent(type="text", text=payload), _stream_final()))
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="source-gate-stream-worker",
    )

    assert receipt is not None and receipt.outcome == "failed"
    assert receipt.detail == "executor_failed"
    assert gateway.requests == []
    assert len(gateway.stream_requests) == 1
    assert store.list_sources(gateway.stream_requests[0].metadata["workspace_id"]) == ()
    assert store.list_claims(run_id) == ()
    assert store.get_report(run_id) is None


@pytest.mark.parametrize(
    "error", [TimeoutError("provider timed out"), RuntimeError("provider failed")]
)
def test_typed_gateway_failure_has_no_retired_protocol_fallback(
    tmp_path: Path,
    error: Exception,
) -> None:
    service, _, run_id = _service_with_run(tmp_path)
    gateway = FailingGateway(error)
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(timeout_seconds=1),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-worker",
    )

    assert receipt is not None
    assert receipt.outcome == "failed"
    assert receipt.detail == "executor_failed"
    assert service.get_run(run_id).status == "failed"  # type: ignore[union-attr]
    assert len(gateway.requests) == 1
    assert gateway.requests[0].messages


def test_gateway_executor_accepts_async_owner_revalidated_sources(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    gateway = FakeGateway(
        """{
          "claims": [{
            "claim_key": "claim-1",
            "text": "The async approved source supports the claim.",
            "kind": "grounded",
            "source_keys": ["owner-revalidated"]
          }],
          "report_body": "An async grounded report.",
          "report_claim_keys": ["claim-1"],
          "requires_review": false
        }"""
    )
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=AsyncValidatedSources(
            ResearchSourceDraft(
                source_key="owner-revalidated",
                url="https://evidence.example/owner-revalidated",
                title="Owner-revalidated evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(),
        user_id="owner",
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-worker",
    )

    assert receipt is not None and receipt.outcome == "accepted"
    current = store.get_run(run_id)
    assert current is not None and current.status == "completed"
    assert "owner-revalidated" in gateway.requests[0].messages[-1].content


def test_gateway_executor_without_sources_is_managed_needs_review(tmp_path: Path) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    gateway = FakeGateway("this must not be called")
    executor = GatewayResearchExecutor(
        gateway,
        config=ResearchGatewayExecutionConfig(),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-worker",
    )

    assert receipt is not None and receipt.outcome == "accepted"
    assert service.get_run(run_id).status == "needs_review"  # type: ignore[union-attr]
    assert gateway.requests == []
    current = store.get_run(run_id)
    assert current is not None
    assert store.list_sources(current.workspace_id) == ()
    assert store.list_claims(run_id) == ()
    assert "validated sources" in store.get_report(run_id).body  # type: ignore[union-attr]


def test_gateway_executor_without_execution_config_is_managed_needs_review(
    tmp_path: Path,
) -> None:
    service, store, run_id = _service_with_run(tmp_path)
    gateway = FakeGateway("this must not be called")
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=None,
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-worker",
    )

    assert receipt is not None and receipt.outcome == "accepted"
    assert service.get_run(run_id).status == "needs_review"  # type: ignore[union-attr]
    assert gateway.requests == []
    assert "not configured" in store.get_report(run_id).body  # type: ignore[union-attr]


def test_gateway_executor_rejects_an_unvalidated_output_url(tmp_path: Path) -> None:
    service, _, run_id = _service_with_run(tmp_path)
    gateway = FakeGateway(
        """{
          "claims": [{
            "claim_key": "claim-1",
            "text": "See https://invented.example/source",
            "kind": "grounded",
            "source_keys": ["approved"]
          }],
          "report_body": "Unsupported URL was presented as evidence.",
          "report_claim_keys": ["claim-1"],
          "requires_review": false
        }"""
    )
    executor = GatewayResearchExecutor(
        gateway,
        source_provider=StaticValidatedSources(
            ResearchSourceDraft(
                source_key="approved",
                url="https://evidence.example/approved",
                title="Approved evidence",
            )
        ),
        config=ResearchGatewayExecutionConfig(),
    )

    receipt = ResearchWorkspaceWorker(service, executor, clock=lambda: T0).run_once(
        run_id,
        worker_id="gateway-worker",
    )

    assert receipt is not None and receipt.outcome == "failed"
    assert service.get_run(run_id).status == "failed"  # type: ignore[union-attr]
