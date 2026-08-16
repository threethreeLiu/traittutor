"""Application service for the read-only Layer 1 governance slice."""

from __future__ import annotations

import time

from traittutor.learning_governance.models import (
    LearningGovernanceSnapshot,
    SubjectLearningStateSnapshot,
)
from traittutor.learning_governance.repository import LearningGovernanceRepository
from traittutor.learning_model.parameters import BKTParamSet


class LearningGovernanceService:
    def __init__(self, repository: LearningGovernanceRepository) -> None:
        self._repository = repository

    def snapshot(
        self,
        *,
        subject_id: str,
        kc_id: str | None = None,
        now: float | None = None,
    ) -> LearningGovernanceSnapshot:
        subject = self._validated_partition(subject_id, "subject_id")
        kc = self._validated_partition(kc_id, "kc_id") if kc_id is not None else None
        observed_at = time.time() if now is None else now
        return LearningGovernanceSnapshot(
            subject_id=subject,
            kc_id=kc,
            errors=tuple(self._repository.list_errors(subject_id=subject, kc_id=kc)),
            misconceptions=tuple(
                self._repository.list_misconceptions(subject_id=subject, kc_id=kc)
            ),
            repairs=tuple(self._repository.list_repairs(subject_id=subject, kc_id=kc)),
            reviews=tuple(
                self._repository.list_reviews(subject_id=subject, kc_id=kc, now=observed_at)
            ),
        )

    def subject_learning_state_snapshot(
        self,
        *,
        subject_id: str,
        params: BKTParamSet | None = None,
    ) -> SubjectLearningStateSnapshot:
        """Read a deterministic canonical subject-state snapshot without writes."""
        return self._repository.subject_learning_state_snapshot(
            subject_id=self._validated_partition(subject_id, "subject_id"),
            params=params,
        )

    @staticmethod
    def _validated_partition(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be blank")
        if len(normalized) > 96:
            raise ValueError(f"{field_name} must not exceed 96 characters")
        return normalized


__all__ = ["LearningGovernanceService"]
