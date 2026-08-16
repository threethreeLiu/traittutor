"""Versioned qualitative product policy over private BKT state."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .knowledge_state import MIN_OBSERVATIONS_FOR_PROBABILITY, KnowledgeStateUnit

EVIDENCE_STAGE_POLICY_VERSION = "bkt-stage-policy-v1"
EvidenceState = Literal[
    "insufficient_evidence",
    "needs_support",
    "developing",
    "supported",
]
ChangeSignal = Literal["none", "needs_review", "repaired", "due_for_review"]


def qualitative_evidence_state(
    unit: KnowledgeStateUnit | None,
    *,
    has_open_or_relapsed_error: bool = False,
    has_due_review: bool = False,
    now: datetime | None = None,
) -> EvidenceState:
    """Map private posterior state to the learner-safe qualitative contract."""
    if has_open_or_relapsed_error:
        return "needs_support"
    if (
        unit is None
        or not unit.calibrated
        or unit.verified_observation_count < MIN_OBSERVATIONS_FOR_PROBABILITY
    ):
        return "insufficient_evidence"
    # Time changes only this derived read projection. The immutable event
    # ledger and rebuilt BKT posterior remain untouched and reproducible.
    from .decay import days_since, effective_mastery

    projected_probability = effective_mastery(
        prior=unit.initial_mastery_probability,
        posterior=unit.mastery_probability,
        days_since_last_practice=days_since(unit.updated_at, now=now),
    )
    if projected_probability < 0.4:
        return "needs_support"
    if projected_probability >= 0.75 and not has_due_review:
        return "supported"
    return "developing"


def qualitative_change_signal(
    *,
    has_open_or_relapsed_error: bool = False,
    repaired: bool = False,
    has_due_review: bool = False,
) -> ChangeSignal:
    if has_open_or_relapsed_error:
        return "needs_review"
    if repaired:
        return "repaired"
    if has_due_review:
        return "due_for_review"
    return "none"


__all__ = [
    "ChangeSignal",
    "EVIDENCE_STAGE_POLICY_VERSION",
    "EvidenceState",
    "qualitative_change_signal",
    "qualitative_evidence_state",
]
