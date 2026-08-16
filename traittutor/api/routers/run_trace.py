"""Authenticated, owner-bound, learner-safe GenerationRun trace API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import get_path_service_for_scope
from traittutor.orchestration import (
    GenerationRunTraceNotFound,
    LearnerSafeRunTrace,
    LearnerSafeRunTraceService,
    OrchestratorRunStore,
    OrchestratorRunStoreError,
)

router = APIRouter()

RunStoreFactory = Callable[[CurrentUser], OrchestratorRunStore]


def default_run_store_factory(user: CurrentUser) -> OrchestratorRunStore:
    """Resolve the durable store from the authenticated user's validated scope."""
    path_service = get_path_service_for_scope(user.scope)
    return OrchestratorRunStore(
        path_service.get_workspace_dir() / "traittutor" / "orchestrator-runs.json"
    )


run_store_factory: RunStoreFactory = default_run_store_factory


def get_run_trace_service() -> LearnerSafeRunTraceService:
    user = get_current_user()
    return LearnerSafeRunTraceService(run_store_factory(user))


RunTraceService = Annotated[LearnerSafeRunTraceService, Depends(get_run_trace_service)]


@router.get(
    "/generation-runs/{generation_run_id}/trace",
    response_model=LearnerSafeRunTrace,
)
async def generation_run_trace(
    generation_run_id: str,
    service: RunTraceService,
) -> LearnerSafeRunTrace:
    """Return a safe projection; inaccessible and missing IDs share one response."""
    try:
        return service.get(generation_run_id)
    except GenerationRunTraceNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generation run not found",
        ) from exc
    except OrchestratorRunStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generation run trace unavailable",
        ) from exc


__all__ = [
    "RunStoreFactory",
    "default_run_store_factory",
    "generation_run_trace",
    "get_run_trace_service",
    "router",
    "run_store_factory",
]
