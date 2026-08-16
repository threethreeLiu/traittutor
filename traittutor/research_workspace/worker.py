"""Recoverable Research Workspace worker with receipt-before-progress ordering."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from .executor import ResearchExecutionResult, ResearchExecutionTask, ResearchExecutor
from .models import ResearchTaskReceipt
from .service import ResearchWorkspaceService
from .store import ResearchRunLeaseUnavailable


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ResearchProgressEvent(BaseModel):
    """Operational view event emitted only after its durable receipt exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    task_id: str
    receipt_id: str
    outcome: Literal["accepted", "discarded_stale", "failed"]


class ResearchProgressSink(Protocol):
    def publish(self, event: ResearchProgressEvent) -> None: ...


class NoOpResearchProgressSink:
    def publish(self, event: ResearchProgressEvent) -> None:
        del event


class ResearchWorkspaceWorker:
    """Claim one durable run, execute through an injected adapter, then commit."""

    def __init__(
        self,
        service: ResearchWorkspaceService,
        executor: ResearchExecutor,
        *,
        progress_sink: ResearchProgressSink | None = None,
        clock: Callable[[], str] = _now,
        heartbeat_seconds: float | None = None,
    ) -> None:
        self._service = service
        self._executor = executor
        self._progress_sink = progress_sink or NoOpResearchProgressSink()
        self._clock = clock
        if heartbeat_seconds is not None and heartbeat_seconds <= 0:
            raise ValueError("heartbeat_seconds must be positive")
        self._heartbeat_seconds = heartbeat_seconds

    def _publish_after_receipt(self, receipt: ResearchTaskReceipt) -> None:
        try:
            self._progress_sink.publish(
                ResearchProgressEvent(
                    run_id=receipt.run_id,
                    task_id=receipt.task_id,
                    receipt_id=receipt.receipt_id,
                    outcome=receipt.outcome,
                )
            )
        except Exception:
            # Progress is operational telemetry, not product truth. A sink
            # failure must never roll back or rewrite the durable receipt.
            return

    def _heartbeat_interval(self, lease_seconds: int) -> float:
        """Keep claims alive before their lease can be recovered by another worker."""

        if self._heartbeat_seconds is not None:
            return self._heartbeat_seconds
        # A bounded provider call normally completes well inside this interval.
        # The cap avoids waiting minutes before noticing an operator's control
        # request when an executor is unexpectedly slow.
        return min(30.0, max(1.0, lease_seconds / 3))

    def _settle_requested_control(self, run_id: str) -> bool:
        """Observe control state before any late output can be committed."""

        current = self._service.get_run(run_id)
        if current is None:
            return True
        if current.status in {"pausing", "cancelling"}:
            self._service.finalize_requested_lifecycle(run_id, now=self._clock())
            return True
        return current.status != "running"

    def _execute_with_lifecycle(
        self,
        run,
        *,
        worker_id: str,
        task: ResearchExecutionTask,
        lease_seconds: int,
    ) -> ResearchExecutionResult:
        """Execute a bounded adapter while renewing only a current fenced claim.

        The adapter cannot be forcefully interrupted in-process.  A pause or
        cancellation therefore fences the claim immediately, settles the
        requested terminal state, and lets any later adapter output flow only
        into the durable ``discarded_stale`` receipt path.
        """

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-run") as pool:
            future: Future[ResearchExecutionResult] = pool.submit(self._executor.execute, task)
            heartbeat_interval = self._heartbeat_interval(lease_seconds)
            while True:
                try:
                    return future.result(timeout=heartbeat_interval)
                except TimeoutError:
                    if future.done():
                        # ``Future.result`` uses the same TimeoutError for a
                        # provider-raised timeout as for this heartbeat wait.
                        # Once it is done, re-read it outside the heartbeat
                        # branch so the outer bounded failure path persists a
                        # single executor_failed receipt instead of renewing
                        # a permanently failed claim forever.
                        return future.result()
                    if self._settle_requested_control(run.run_id):
                        # Do not renew a paused/cancelled/non-current claim.
                        # The still-running adapter will be joined by the
                        # executor context and its result remains fenced.
                        continue
                    try:
                        self._service.renew_run_lease(
                            run,
                            worker_id=worker_id,
                            lease_seconds=lease_seconds,
                            now=self._clock(),
                        )
                    except ResearchRunLeaseUnavailable:
                        # Expiry or a newer claim is not an executor failure.
                        # The fenced commit/failure path records it as stale.
                        continue

    def run_once(
        self,
        run_id: str,
        *,
        worker_id: str,
        task_id: str = "research_report",
        lease_seconds: int = 300,
    ) -> ResearchTaskReceipt | None:
        # A periodic owner-scoped worker can finish a control request left by a
        # process interruption without claiming or executing it again.
        current = self._service.get_run(run_id)
        if current is not None and current.status in {"pausing", "cancelling"}:
            self._service.finalize_requested_lifecycle(run_id, now=self._clock())
            return None
        claim_time = self._clock()
        try:
            run = self._service.claim_run(
                run_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                now=claim_time,
            )
        except ResearchRunLeaseUnavailable:
            return None
        try:
            brief = self._service.get_frozen_brief(run)
            task = ResearchExecutionTask(
                workspace_id=run.workspace_id,
                run_id=run.run_id,
                task_id=task_id,
                input_hash=run.input_hash,
                fencing_epoch=run.fencing_epoch,
                claim_token=run.claim_token or "",
                brief=brief,
                prior_report=self._service.continuation_context(brief),
            )
            result = self._execute_with_lifecycle(
                run,
                worker_id=worker_id,
                task=task,
                lease_seconds=lease_seconds,
            )
            # A fast executor can finish between heartbeat ticks.  Re-check
            # the durable lifecycle before constructing any accepted output.
            self._settle_requested_control(run.run_id)
            receipt = self._service.commit_execution_result(
                run,
                task_id=task_id,
                result=result,
                created_at=self._clock(),
            )
        except Exception:
            # Persist only a bounded failure code. Provider errors may contain
            # credentials or user text and are not product-facing artifacts.
            receipt = self._service.record_execution_failure(
                run,
                task_id=task_id,
                created_at=self._clock(),
            )
        self._publish_after_receipt(receipt)
        return receipt

    def recover_claimable(
        self,
        *,
        worker_id: str,
        limit: int = 10,
        task_id: str = "research_report",
        lease_seconds: int = 300,
    ) -> tuple[ResearchTaskReceipt, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = self._clock()
        receipts: list[ResearchTaskReceipt] = []
        for controlled in self._service.list_pending_control_runs():
            self._service.finalize_requested_lifecycle(controlled.run_id, now=now)
        for run in self._service.list_claimable_runs(now=now)[:limit]:
            receipt = self.run_once(
                run.run_id,
                worker_id=worker_id,
                task_id=task_id,
                lease_seconds=lease_seconds,
            )
            if receipt is not None:
                receipts.append(receipt)
        return tuple(receipts)


__all__ = [
    "NoOpResearchProgressSink",
    "ResearchProgressEvent",
    "ResearchProgressSink",
    "ResearchWorkspaceWorker",
]
