"""Background scheduling seam for owner-bound Research Workspace runs."""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

from .executor import ResearchExecutionResult, ResearchExecutionTask, ResearchExecutor
from .models import ResearchTaskReceipt
from .service import ResearchWorkspaceService
from .worker import ResearchWorkspaceWorker

ResearchExecutorFactory = Callable[[], ResearchExecutor]


class _UnavailableResearchExecutor:
    """Turn executor construction errors into the worker's bounded failure path."""

    def execute(self, task: ResearchExecutionTask) -> ResearchExecutionResult:
        del task
        raise RuntimeError("research_executor_unavailable")


class ResearchRunScheduler:
    """Claim and execute a persisted run after the HTTP response is prepared.

    Store locking and claim fencing provide cross-process deduplication; this
    scheduler intentionally keeps no in-memory product state.
    """

    def __init__(
        self,
        executor_factory: ResearchExecutorFactory,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._executor_factory = executor_factory
        self._worker_id = worker_id or f"api-research-{uuid4().hex[:12]}"

    def schedule(
        self,
        service: ResearchWorkspaceService,
        run_id: str,
    ) -> ResearchTaskReceipt | None:
        try:
            executor = self._executor_factory()
        except Exception:
            executor = _UnavailableResearchExecutor()
        try:
            return ResearchWorkspaceWorker(service, executor).run_once(
                run_id,
                worker_id=self._worker_id,
            )
        except Exception:
            # The durable worker owns managed product state. A background-task
            # transport error must not turn a successful 202 into a server crash.
            return None


__all__ = ["ResearchExecutorFactory", "ResearchRunScheduler"]
