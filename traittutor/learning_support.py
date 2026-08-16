"""Canonical learning support, calibration, repair, and review contracts."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

SLRDimension = Literal[
    "goal_planning", "monitoring_regulation", "reflection_transfer", "motivation_emotion"
]


def load_slr_action_catalog() -> dict[str, Any]:
    path = Path(__file__).parent / "assessment" / "slr_action_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


class SLRDimensionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emphasis: Literal["light", "standard", "strong"] = "standard"
    source: Literal["initial_profile", "subject_evidence", "learner_choice"] = "initial_profile"
    evidence_count: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.5, ge=0, le=1)
    actions: list[str] = Field(default_factory=list, max_length=12)


class CalibrationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    artifact_ref: str
    confidence: float = Field(ge=0, le=1)
    correctness: bool
    quadrant: Literal[
        "confident_correct", "uncertain_correct", "confident_incorrect", "uncertain_incorrect"
    ]
    recommended_strategy: str


# Qualitative difficulty of the *next* learning step, derived from the current
# round's accumulated server-graded evidence. It is a teaching-support
# projection: it adjusts pacing/support, never mastery state, and it must never
# be presented as an ability label (invariants #2/#3/#8).
ProgressDifficulty = Literal["smooth", "can_continue", "needs_support", "blocked"]

PROGRESS_CALIBRATION_BOUNDARY = (
    "This difficulty evaluation adjusts next-step teaching support only. It is "
    "not a diagnosis of ability, does not label the learner, and never updates "
    "mastery state."
)

_PROGRESS_STRATEGY_BY_DIFFICULTY: dict[ProgressDifficulty, str] = {
    "smooth": "transfer_or_schedule_review",
    "can_continue": "self_explain_then_retrieve",
    "needs_support": "worked_example_then_guided_retry",
    "blocked": "repair_with_contrast",
}

# Mirror the BKT calibration gate: fewer verified observations carry no
# reliable difficulty signal, so the projection reports insufficient evidence
# instead of inventing a number (invariant #3).
MIN_VERIFIED_OBSERVATIONS_FOR_DIFFICULTY = 3


class KcCalibrationSummary(BaseModel):
    """Server-graded outcome counts for one knowledge-concept partition."""

    model_config = ConfigDict(extra="forbid")

    subject_id: str = ""
    kc_id: str
    correct: int = Field(ge=0)
    incorrect: int = Field(ge=0)


class ProgressCalibration(BaseModel):
    """One round-scoped aggregation of accumulated server-graded evidence.

    Created when the learner completes a calibration checkpoint. Inputs are
    only verified answers (and prior per-question calibration quadrants for
    the metacognitive-bias note); self-reports never enter and nothing here
    writes BKT.
    """

    model_config = ConfigDict(extra="forbid")

    plan_id: str
    created_at: str
    verified_observations: int = Field(ge=0)
    correct_count: int = Field(ge=0)
    kc_summaries: list[KcCalibrationSummary] = Field(default_factory=list, max_length=24)
    difficulty: ProgressDifficulty | None = None
    difficulty_reason: str = ""
    recommended_strategy: str | None = None
    boundary: str = PROGRESS_CALIBRATION_BOUNDARY


def build_progress_calibration(
    *,
    plan: Mapping[str, Any],
    events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    calibrations: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
    created_at: str | None = None,
) -> ProgressCalibration:
    """Deterministically aggregate one round's verified evidence by subject+KC.

    Only server-graded ``correct``/``incorrect`` component events count. The
    difficulty tiers are a support projection (smooth → schedule review/transfer,
    blocked → repair with contrast), never a mastery claim, and below the
    minimum-observation gate the result stays ``insufficient_evidence``.
    """
    subject_id = ""
    raw_subject = plan.get("subject_ref")
    if isinstance(raw_subject, Mapping):
        subject_id = str(raw_subject.get("subject_id") or "").strip()
    per_kc: dict[str, dict[str, int]] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("observation") or "") not in {"correct", "incorrect"}:
            continue
        kc_id = (
            str(event.get("concept_id") or "").strip()
            or str(event.get("component_id") or "").strip()
        )
        if not kc_id:
            # No KC attribution: the answer cannot be meaningfully aggregated.
            continue
        bucket = per_kc.setdefault(kc_id, {"correct": 0, "incorrect": 0})
        if str(event.get("observation")) == "correct":
            bucket["correct"] += 1
        else:
            bucket["incorrect"] += 1
    kc_summaries = [
        KcCalibrationSummary(
            subject_id=subject_id,
            kc_id=kc_id,
            correct=int(bucket["correct"]),
            incorrect=int(bucket["incorrect"]),
        )
        for kc_id, bucket in sorted(per_kc.items())
    ]
    correct_count = sum(item.correct for item in kc_summaries)
    incorrect_count = sum(item.incorrect for item in kc_summaries)
    verified_observations = correct_count + incorrect_count
    base = ProgressCalibration(
        plan_id=str(plan.get("plan_id") or ""),
        created_at=created_at or datetime.now(UTC).isoformat(),
        verified_observations=verified_observations,
        correct_count=correct_count,
        kc_summaries=kc_summaries,
    )
    if verified_observations < MIN_VERIFIED_OBSERVATIONS_FOR_DIFFICULTY:
        return base.model_copy(
            update={
                "difficulty": None,
                "difficulty_reason": (
                    f"{verified_observations} verified answer(s) across "
                    f"{len(kc_summaries)} concept(s); at least "
                    f"{MIN_VERIFIED_OBSERVATIONS_FOR_DIFFICULTY} are required "
                    "for a difficulty judgement (insufficient evidence)."
                ),
                "recommended_strategy": None,
            }
        )
    accuracy = correct_count / verified_observations
    if accuracy >= 0.85:
        difficulty: ProgressDifficulty = "smooth"
    elif accuracy >= 0.65:
        difficulty = "can_continue"
    elif accuracy >= 0.5:
        difficulty = "needs_support"
    else:
        difficulty = "blocked"
    # Scope the metacognitive-bias note to THIS plan's assessment artifacts:
    # per-question calibration records are pack-wide, and older rounds' bias
    # must not leak into the current round's evaluation. When the plan carries
    # no output refs (legacy/imported), fall back to the whole collection.
    owned_artifact_refs = {
        str(component.get("output_ref") or "").strip()
        for component in plan.get("components") or []
        if str(component.get("output_ref") or "").strip()
    }
    confident_incorrect = sum(
        1
        for record in calibrations
        if isinstance(record, Mapping)
        and record.get("quadrant") == "confident_incorrect"
        and (
            not owned_artifact_refs or str(record.get("artifact_ref") or "") in owned_artifact_refs
        )
    )
    bias_note = (
        " The learner has repeatedly overestimated confidence on verified attempts."
        if confident_incorrect >= 2
        else ""
    )
    weakest = max(kc_summaries, key=lambda item: (item.incorrect, -item.correct))
    return base.model_copy(
        update={
            "difficulty": difficulty,
            "difficulty_reason": (
                f"{verified_observations} verified answers across {len(kc_summaries)} "
                f"concept(s): {correct_count} correct ({round(accuracy * 100)}%). "
                f"Weakest concept {weakest.kc_id or 'unknown'} "
                f"({weakest.incorrect} incorrect).{bias_note}"
            ),
            "recommended_strategy": _PROGRESS_STRATEGY_BY_DIFFICULTY[difficulty],
        }
    )


class RepairRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    repair_id: str
    action_id: str
    question_id: str
    concept_id: str
    user_answer: str
    correct_rule: str
    error_type: str = "deviation"
    contrast: str = ""
    status: Literal["identified", "explained", "retrying", "repaired", "scheduled"] = "identified"
    retry_count: int = 0
    next_review_at: str | None = None
    created_at: str


class ReviewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    pack_id: str
    concept_id: str
    knowledge_type: Literal["memory", "concept", "procedure", "design"] = "concept"
    source: Literal["retrieval", "quiz", "repair"]
    due_at: str
    priority: int = Field(default=3, ge=1, le=9)
    interval_index: int = Field(default=0, ge=0)
    consecutive_correct: int = Field(default=0, ge=0)
    consecutive_wrong: int = Field(default=0, ge=0)
    last_result: bool | None = None


def calibration_record(
    question_id: str, confidence: float, correctness: bool, *, artifact_ref: str
) -> CalibrationRecord:
    confident = confidence >= 0.75
    quadrant: Literal[
        "confident_correct",
        "uncertain_correct",
        "confident_incorrect",
        "uncertain_incorrect",
    ] = (
        "confident_correct"
        if confident and correctness
        else "uncertain_correct"
        if correctness
        else "confident_incorrect"
        if confident
        else "uncertain_incorrect"
    )
    strategy = {
        "confident_correct": "transfer_or_schedule_review",
        "uncertain_correct": "self_explain_then_retrieve",
        "confident_incorrect": "repair_with_contrast",
        "uncertain_incorrect": "worked_example_then_guided_retry",
    }[quadrant]
    return CalibrationRecord(
        question_id=question_id,
        artifact_ref=artifact_ref,
        confidence=confidence,
        correctness=correctness,
        quadrant=quadrant,
        recommended_strategy=strategy,
    )


def normalize_slr_dimensions(slr_support: Mapping[str, Any] | None) -> dict[str, SLRDimensionState]:
    catalog = load_slr_action_catalog()
    raw = dict((slr_support or {}).get("dimensions") or {})
    result: dict[str, SLRDimensionState] = {}
    for key, definition in dict(catalog["dimensions"]).items():
        current = dict(raw.get(key) or {})
        evidence_count = max(0, int(current.get("evidence_count") or 0))
        requested_source = str(current.get("source") or "initial_profile")
        source = (
            requested_source
            if requested_source == "learner_choice"
            else (
                "subject_evidence"
                if requested_source == "subject_evidence" and evidence_count >= 3
                else "initial_profile"
            )
        )
        emphasis = str(current.get("emphasis") or "standard")
        if requested_source == "subject_evidence" and evidence_count < 3:
            emphasis = "standard"
        if emphasis not in {"light", "standard", "strong"}:
            emphasis = "standard"
        result[key] = SLRDimensionState(
            emphasis=emphasis,  # type: ignore[arg-type]
            source=source,  # type: ignore[arg-type]
            evidence_count=evidence_count,
            confidence=(
                1.0
                if source == "learner_choice"
                else min(0.95, 0.5 + evidence_count * 0.1)
                if source == "subject_evidence"
                else 0.5
            ),
            actions=list(definition.get("actions") or []),
        )
    return result


def due_reviews(
    pack: Mapping[str, Any], *, now: datetime | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    instant = now or datetime.now(UTC)
    instant = instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant.astimezone(UTC)
    reviews = [dict(item) for item in pack.get("review_states") or [] if isinstance(item, Mapping)]
    due = []
    for item in reviews:
        try:
            due_at = datetime.fromisoformat(str(item.get("due_at") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if due_at.tzinfo is None:
            continue
        due_at = due_at.astimezone(UTC)
        if due_at <= instant:
            due.append(item)
    due.sort(key=lambda item: (int(item.get("priority") or 9), str(item.get("due_at") or "")))
    return due[: max(0, min(limit, 5))]


__all__ = [
    "CalibrationRecord",
    "KcCalibrationSummary",
    "MIN_VERIFIED_OBSERVATIONS_FOR_DIFFICULTY",
    "PROGRESS_CALIBRATION_BOUNDARY",
    "ProgressCalibration",
    "ProgressDifficulty",
    "RepairRecord",
    "ReviewState",
    "SLRDimension",
    "SLRDimensionState",
    "build_progress_calibration",
    "calibration_record",
    "due_reviews",
    "load_slr_action_catalog",
    "normalize_slr_dimensions",
]
