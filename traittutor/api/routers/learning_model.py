"""Authenticated, learner-safe WS-16 learning-profile read API."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from traittutor.learning_model.read_models import (
    LearningModelOverview,
    LearningModelSubjectDetail,
)
from traittutor.learning_model.read_service import (
    CanonicalLearningModelSources,
    LearningModelReadService,
    LearningModelSources,
    LearningModelSubjectNotFound,
)
from traittutor.multi_user.context import get_current_user
from traittutor.multi_user.models import CurrentUser

router = APIRouter()

LearningModelSourcesFactory = Callable[[CurrentUser], LearningModelSources]
learning_model_sources_factory: LearningModelSourcesFactory = CanonicalLearningModelSources


def get_learning_model_read_service() -> LearningModelReadService:
    user = get_current_user()
    return LearningModelReadService(
        owner_id=user.id,
        sources=learning_model_sources_factory(user),
    )


ReadService = Annotated[LearningModelReadService, Depends(get_learning_model_read_service)]


@router.get("/learning-model/overview", response_model=LearningModelOverview)
async def learning_model_overview(service: ReadService) -> LearningModelOverview:
    return service.overview()


@router.get(
    "/learning-model/subjects/{subject_id}",
    response_model=LearningModelSubjectDetail,
)
async def learning_model_subject(
    subject_id: str,
    service: ReadService,
) -> LearningModelSubjectDetail:
    try:
        return service.subject_detail(subject_id)
    except LearningModelSubjectNotFound as exc:
        # Missing and inaccessible subjects intentionally share one response.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Learning subject not found",
        ) from exc


__all__ = [
    "LearningModelSourcesFactory",
    "get_learning_model_read_service",
    "learning_model_sources_factory",
    "router",
]
