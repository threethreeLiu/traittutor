"""Progress calibration: deterministic evidence aggregation and follow-up plans.

The calibration checkpoint no longer collects a single-question strategy in
the browser. Completing it aggregates the round's accumulated server-graded
evidence into a qualitative difficulty evaluation (smooth / can_continue /
needs_support / blocked, or insufficient evidence below the observation gate)
and a next-step strategy; the follow-up plan only inserts the missing supports
and never rewrites completed components or the LLM arrangement state.
"""

from __future__ import annotations

from typing import Any

from traittutor.learning_components import (
    LearningComponentSelector,
    MaterialAffordance,
    MaterialComponentAffordances,
    SubjectSupportState,
    build_calibrated_followup_plan,
)
from traittutor.learning_support import build_progress_calibration


def _plan(*, arrangement: str = "llm") -> dict[str, Any]:
    selector_plan = (
        LearningComponentSelector()
        .select(
            pack_id="pack-progress",
            goal="Learn equations",
            subject_ref={"subject_id": "math", "label": "Mathematics"},
            analysis_id=None,
            concept_signals=[],
            support_state=SubjectSupportState(),
            affordances=MaterialComponentAffordances(
                visual=MaterialAffordance(suitable=False, confidence=0.2),
                audio=MaterialAffordance(suitable=False, confidence=0.2),
                worked_example=MaterialAffordance(suitable=False, confidence=0.2),
                practice=MaterialAffordance(suitable=True, confidence=0.8),
            ),
        )
        .model_dump(mode="json")
    )
    selector_plan["arrangement"] = arrangement
    # Simulate a started prefix: the first component and the in-flight practice
    # plus its calibration are preserved by the follow-up builder.
    statuses = {"goal_map": "completed"}
    for component in selector_plan["components"]:
        component["status"] = statuses.get(component["component_type"], "pending")
    return selector_plan


def _verified(correct: int, incorrect: int, *, kc: str = "equations") -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for _ in range(correct):
        events.append({"observation": "correct", "concept_id": kc})
    for _ in range(incorrect):
        events.append({"observation": "incorrect", "concept_id": kc})
    return events


def test_insufficient_evidence_stays_unguessable() -> None:
    calibration = build_progress_calibration(plan=_plan(), events=_verified(1, 1), calibrations=[])
    assert calibration.verified_observations == 2
    assert calibration.difficulty is None
    assert calibration.recommended_strategy is None
    assert "insufficient evidence" in calibration.difficulty_reason


def test_difficulty_tiers_and_strategies() -> None:
    cases = [
        (4, 0, "smooth", "transfer_or_schedule_review"),
        (3, 1, "can_continue", "self_explain_then_retrieve"),
        (2, 2, "needs_support", "worked_example_then_guided_retry"),
        (1, 3, "blocked", "repair_with_contrast"),
    ]
    for correct, incorrect, difficulty, strategy in cases:
        calibration = build_progress_calibration(
            plan=_plan(), events=_verified(correct, incorrect), calibrations=[]
        )
        assert calibration.difficulty == difficulty, (correct, incorrect)
        assert calibration.recommended_strategy == strategy, (correct, incorrect)
        assert calibration.kc_summaries[0].subject_id == "math"


def test_ignores_non_graded_events_and_groups_by_kc() -> None:
    events = [
        {"observation": "correct", "concept_id": "a"},
        {"observation": "incorrect", "concept_id": "a"},
        {"observation": "known", "concept_id": "a"},
        {"observation": "correct", "concept_id": "b"},
        {"observation": "correct", "concept_id": "b"},
        {"action": "start"},
    ]
    calibration = build_progress_calibration(plan=_plan(), events=events, calibrations=[])
    # the self-reported "known" rating and the start action never count
    assert calibration.verified_observations == 4
    by_kc = {item.kc_id: (item.correct, item.incorrect) for item in calibration.kc_summaries}
    assert by_kc == {"a": (1, 1), "b": (2, 0)}
    # 3/4 correct = 0.75 -> can_continue
    assert calibration.difficulty == "can_continue"


def test_overconfidence_bias_is_noted_in_basis() -> None:
    calibration = build_progress_calibration(
        plan=_plan(),
        events=_verified(1, 3),
        calibrations=[
            {"quadrant": "confident_incorrect"},
            {"quadrant": "confident_incorrect"},
        ],
    )
    assert "overestimated" in calibration.difficulty_reason
    assert calibration.difficulty == "blocked"


def test_followup_plan_inserts_missing_support_and_preserves_arrangement() -> None:
    plan = _plan(arrangement="llm")
    original_types = [item["component_type"] for item in plan["components"]]
    progress = {
        "plan_id": plan["plan_id"],
        "recommended_strategy": "worked_example_then_guided_retry",
    }
    followup = build_calibrated_followup_plan(plan, progress)
    assert followup is not None
    assert followup.arrangement == "llm"
    assert followup.supersedes_plan_id == plan["plan_id"]
    assert followup.version == plan["version"] + 1
    types = [item.component_type for item in followup.components]
    # goal_map stays first; inserted supports follow the started prefix and no
    # existing type is duplicated
    assert types[0] == "goal_map"
    for component_type in original_types:
        assert types.count(component_type) == 1, types
    assert "worked_example" in types
    assert "guided_practice" in types


def test_followup_plan_none_for_smooth_or_missing_strategy() -> None:
    plan = _plan(arrangement="llm")
    for strategy in ("transfer_or_schedule_review", None, ""):
        followup = build_calibrated_followup_plan(
            plan, {"plan_id": plan["plan_id"], "recommended_strategy": strategy}
        )
        assert followup is None, strategy


def test_followup_plan_inserts_nothing_when_support_already_present() -> None:
    # A plan whose selector already included a worked example needs no
    # insertion for repair-with-contrast — no duplication, no new plan.
    selector_plan = (
        LearningComponentSelector()
        .select(
            pack_id="pack-progress",
            goal="Learn equations",
            subject_ref={"subject_id": "math", "label": "Mathematics"},
            analysis_id=None,
            concept_signals=[],
            support_state=SubjectSupportState(),
            affordances=MaterialComponentAffordances(
                visual=MaterialAffordance(suitable=False, confidence=0.2),
                audio=MaterialAffordance(suitable=False, confidence=0.2),
                worked_example=MaterialAffordance(suitable=True, confidence=0.8),
                practice=MaterialAffordance(suitable=False, confidence=0.2),
            ),
        )
        .model_dump(mode="json")
    )
    selector_plan["arrangement"] = "llm"
    selector_plan["components"][0]["status"] = "completed"
    assert any(item["component_type"] == "worked_example" for item in selector_plan["components"])
    followup = build_calibrated_followup_plan(
        selector_plan,
        {"plan_id": selector_plan["plan_id"], "recommended_strategy": "repair_with_contrast"},
    )
    assert followup is None
