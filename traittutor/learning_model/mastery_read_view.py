"""Identity-bound canonical mastery projection for learner-facing reads.

The immutable learner-event ledger remains the source of truth. This adapter
rebuilds the versioned per-KC state and then binds every lookup to one explicit
``user_id + subject_id`` partition. It deliberately exposes the public
``display_mastery`` projection rather than the internal BKT posterior, so an
uncalibrated state can never leak a pseudo-precise percentage.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .bkt import BKTParamSet, rebuild_knowledge_states
from .events import LearnerEventLedger
from .knowledge_state import KnowledgeStateKey, KnowledgeStateStore, display_mastery
from .stage_policy import (
    EVIDENCE_STAGE_POLICY_VERSION,
    ChangeSignal,
    EvidenceState,
)


class MasteryReadResult(BaseModel):
    """Public, version-aware mastery state for one isolated KC cell."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_state: EvidenceState
    change_signal: ChangeSignal = "none"
    verified_observation_count: int
    model_version: str | None = None
    stage_policy_version: str
    updated_at: str | None = None


class MasteryDecisionState(BaseModel):
    """Private policy input; it is not used as an API response model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mastery_probability: float | None
    verified_observation_count: int
    param_version: str | None
    calibrated: bool
    updated_at: str | None


class MasteryReadView:
    """Read canonical mastery inside one explicit user/subject partition.

    Construction from a ledger is the production seam. ``from_state_store``
    is a narrow adapter for callers that already rebuilt the canonical read
    model and for isolated contract tests. Neither path mutates the state store.
    """

    def __init__(
        self,
        state_store: KnowledgeStateStore,
        *,
        user_id: str,
        subject_id: str,
    ) -> None:
        self._user_id = user_id.strip()
        self._subject_id = subject_id.strip()
        # Validate the partition at construction, before any serving read.
        KnowledgeStateKey(
            user_id=self._user_id,
            subject_id=self._subject_id,
            kc_id="__partition_validation__",
        )
        self._state_store = state_store

    @classmethod
    def from_ledger(
        cls,
        ledger: LearnerEventLedger,
        *,
        user_id: str,
        subject_id: str,
        params: BKTParamSet | None = None,
    ) -> MasteryReadView:
        """Rebuild from immutable facts, then bind reads to one identity."""
        return cls(
            rebuild_knowledge_states(ledger.effective_events(), params=params),
            user_id=user_id,
            subject_id=subject_id,
        )

    @classmethod
    def from_state_store(
        cls,
        state_store: KnowledgeStateStore,
        *,
        user_id: str,
        subject_id: str,
    ) -> MasteryReadView:
        """Bind an already-rebuilt canonical state store to one identity."""
        return cls(state_store, user_id=user_id, subject_id=subject_id)

    def read(self, kc_id: str) -> MasteryReadResult:
        """Return an honest public projection; a missing cell is unknown."""
        key = KnowledgeStateKey(
            user_id=self._user_id,
            subject_id=self._subject_id,
            kc_id=kc_id,
        )
        unit = self._state_store.get(key)
        if unit is None:
            return MasteryReadResult(
                evidence_state="insufficient_evidence",
                verified_observation_count=0,
                stage_policy_version=EVIDENCE_STAGE_POLICY_VERSION,
            )
        public = display_mastery(unit)
        return MasteryReadResult(
            evidence_state=public["evidence_state"],
            change_signal=public["change_signal"],
            verified_observation_count=public["verified_observation_count"],
            model_version=public["model_version"],
            stage_policy_version=public["stage_policy_version"],
            updated_at=unit.updated_at,
        )

    def read_internal(self, kc_id: str) -> MasteryDecisionState:
        """Return calibrated policy facts without widening the public DTO."""
        key = KnowledgeStateKey(
            user_id=self._user_id,
            subject_id=self._subject_id,
            kc_id=kc_id,
        )
        unit = self._state_store.get(key)
        if unit is None:
            return MasteryDecisionState(
                mastery_probability=None,
                verified_observation_count=0,
                param_version=None,
                calibrated=False,
                updated_at=None,
            )
        public = display_mastery(unit)
        return MasteryDecisionState(
            mastery_probability=public["mastery_probability"],
            verified_observation_count=unit.verified_observation_count,
            param_version=unit.param_version,
            calibrated=unit.calibrated,
            updated_at=unit.updated_at,
        )


__all__ = [
    "MasteryReadResult",
    "MasteryDecisionState",
    "MasteryReadView",
]
