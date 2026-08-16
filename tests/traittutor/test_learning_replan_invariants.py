"""Replan structural invariants for independent learning components.

Regression coverage for the supersede-path bug where a preserved in-flight
assessment lost its pending calibration, and for learner-requested similar
items that must remain inside the active component plan.
"""

from __future__ import annotations

from types import SimpleNamespace

from traittutor import learning_packs
from traittutor.learning_components import (
    LearningComponentSelector,
    MaterialAffordance,
    MaterialComponentAffordances,
    SubjectSupportState,
    build_learning_component_plan,
)
from traittutor.services.path_service import PathService

EVIDENCE_ASSESSMENTS = {"guided_practice", "transfer_challenge"}


def _affordances(*, practice: bool = True) -> MaterialComponentAffordances:
    def a(suitable: bool) -> MaterialAffordance:
        return MaterialAffordance(suitable=suitable, confidence=0.9 if suitable else 0.2)

    return MaterialComponentAffordances(
        visual=a(False),
        audio=a(False),
        worked_example=a(False),
        practice=a(practice),
    )


def _component(
    component_id: str,
    component_type: str,
    *,
    status: str,
    executor: str = "deterministic",
) -> dict:
    return {
        "component_id": component_id,
        "component_type": component_type,
        "executor": executor,
        "label_zh": component_type,
        "label_en": component_type,
        "concept_refs": [],
        "support_dimensions": [],
        "bkt_stage": "developing",
        "modality": "text",
        "dependencies": [],
        "required": True,
        "reason": "test",
        "evidence_refs": [],
        "completion_event": "quiz_answer" if executor == "assessment" else "courseware_outcome",
        "status": status,
    }


def _developing_signals() -> list[dict]:
    return [
        {
            "concept_id": "c1",
            "label": "c1",
            "support_level": "developing",
            "mastery_probability": 0.5,
            # calibrated with >=3 observations: required by the stage gate
            "bkt_calibrated": True,
            "verified_observation_count": 3,
        }
    ]


def _select(
    *,
    completed: list[dict] | None = None,
) -> list[str]:
    plan = LearningComponentSelector().select(
        pack_id="pack-1",
        goal="Learn X",
        subject_ref=None,
        analysis_id=None,
        concept_signals=_developing_signals(),
        support_state=SubjectSupportState(),
        affordances=_affordances(),
        supersedes_plan_id="plan-prev" if completed else None,
        completed_components=completed,
    )
    return [item.component_type for item in plan.components]


def _assert_no_adjacent_assessments(types: list[str]) -> None:
    for index, component_type in enumerate(types):
        if component_type in EVIDENCE_ASSESSMENTS:
            assert index + 1 < len(types)
            assert types[index + 1] == "calibration_checkpoint", (
                f"assessment {component_type} at {index} is not followed by calibration: {types}"
            )


def test_inflight_assessment_is_not_duplicated_by_replan() -> None:
    """A preserved in-flight assessment must not gain a second one next to it."""
    types = _select(
        completed=[
            _component("gm", "goal_map", status="completed"),
            _component("gp", "guided_practice", status="active", executor="assessment"),
        ]
    )
    assert types.count("guided_practice") == 1, types


def test_completed_assessment_without_calibration_blocks_new_assessment() -> None:
    """Legacy/reattempt prefixes ending in an uncalibrated assessment get no new one."""
    types = _select(
        completed=[_component("gp", "guided_practice", status="completed", executor="assessment")]
    )
    assert types.count("guided_practice") == 1, types
    assert types.count("goal_map") == 1, types


def test_normal_calibrated_prefix_keeps_next_practice_round() -> None:
    """A completed assessment WITH its calibration still allows another round."""
    types = _select(
        completed=[
            _component("gm", "goal_map", status="completed"),
            _component("gp", "guided_practice", status="completed", executor="assessment"),
            _component("cal", "calibration_checkpoint", status="completed"),
        ]
    )
    assert types.count("guided_practice") == 2, types
    _assert_no_adjacent_assessments(types)


def test_legacy_prefix_without_goal_map_keeps_goal_map_first() -> None:
    """A preserved prefix from an old single-component reattempt plan has no
    goal_map; the superseding plan must still lead with goal_map (invariant:
    goal_map is always the first component) instead of appending it second."""
    types = _select(
        completed=[_component("gp-r", "guided_practice", status="active", executor="assessment")]
    )
    assert types[0] == "goal_map", types
    # the preserved in-flight assessment stays as the active evidence step
    assert types[1] == "guided_practice", types
    # and no second assessment is appended next to the preserved one
    assert types.count("guided_practice") == 1, types


def _patch_plan_build(monkeypatch, previous: dict | None) -> None:
    """Route build_learning_component_plan through a fake personalization seam.

    ``build_learning_component_plan`` binds these names locally via function-
    level ``from ... import`` statements, so the source modules must be patched.
    """
    monkeypatch.setattr(
        "traittutor.learning_packs.get_component_plan",
        lambda _pack_id, _plan_id: previous,
    )
    fake_service = SimpleNamespace(
        classify_subject=lambda **_kwargs: None,
        build_context=lambda **_kwargs: SimpleNamespace(relevant_concept_signals=[]),
        subject_profile=lambda _subject_id: None,
    )
    monkeypatch.setattr(
        "traittutor.personalization.service.get_personalization_service",
        lambda: fake_service,
    )
    monkeypatch.setattr("traittutor.assessment.big_five.list_trait_profiles", lambda: [])


def _pack() -> dict:
    return {
        "pack_id": "pack-1",
        "title": "Learn X",
        "goal": {"text": "Learn X"},
        "material": {},
        "profile_id": "",
        "component_progress": {},
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


def test_build_plan_preserves_integrated_reattempt_baseline(monkeypatch) -> None:
    """Calibration replanning preserves the path and its explicit similar item."""
    reattempt = _component("gp-r", "guided_practice", status="completed", executor="assessment")
    reattempt["reattempt_of_component_id"] = "gp-1"
    _patch_plan_build(
        monkeypatch,
        previous={
            "plan_id": "plan-reattempt-abc",
            "version": 3,
            "reattempt_of_component_id": "gp-1",
            "components": [
                _component("gm", "goal_map", status="completed"),
                _component("gp-1", "guided_practice", status="completed", executor="assessment"),
                reattempt,
                _component("cal", "calibration_checkpoint", status="pending"),
            ],
        },
    )
    plan = build_learning_component_plan(_pack(), supersedes_plan_id="plan-reattempt-abc")
    types = [item.component_type for item in plan.components]
    assert types[:4] == [
        "goal_map",
        "guided_practice",
        "guided_practice",
        "calibration_checkpoint",
    ]
    assert plan.components[2].reattempt_of_component_id == "gp-1"
    assert plan.version == 4, plan.version


def test_completed_diagnostic_is_not_repeated_while_evidence_is_unattributed(
    monkeypatch,
) -> None:
    """One completed judgement is not repeated and never gates direct practice."""
    _patch_plan_build(
        monkeypatch,
        previous={
            "plan_id": "plan-diagnostic-complete",
            "version": 2,
            "components": [
                _component("gm", "goal_map", status="completed"),
                _component(
                    "diagnostic",
                    "diagnostic_check",
                    status="completed",
                    executor="assessment",
                ),
                _component("calibration", "calibration_checkpoint", status="completed"),
            ],
        },
    )

    plan = build_learning_component_plan(_pack(), supersedes_plan_id="plan-diagnostic-complete")
    appended = plan.components[3:]

    assert appended
    assert appended[0].component_type == "concept_explanation"
    assert all(item.component_type != "diagnostic_check" for item in appended)
    assert any(item.component_type == "guided_practice" for item in appended)
    assert "Start directly" in appended[0].reason


def test_build_plan_keeps_pending_calibration_of_preserved_assessment(
    monkeypatch,
) -> None:
    """Supersede keeps the pending calibration that follows an assessment."""
    _patch_plan_build(
        monkeypatch,
        previous={
            "plan_id": "plan-1",
            "version": 1,
            "components": [
                _component("gm", "goal_map", status="completed"),
                _component("gp", "guided_practice", status="active", executor="assessment"),
                _component("cal", "calibration_checkpoint", status="pending"),
                _component("vis", "visual_map", status="pending"),
            ],
        },
    )
    plan = build_learning_component_plan(_pack(), supersedes_plan_id="plan-1")
    types = [item.component_type for item in plan.components]
    assert types[2] == "calibration_checkpoint", types  # pending calibration preserved
    assert plan.version == 2, plan.version
    _assert_no_adjacent_assessments(types)


def test_round_completes_without_claiming_mastery_and_reopens_on_replan(
    monkeypatch, tmp_path
) -> None:
    """Finishing components closes a round, never the evidence-owned goal."""
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn equations")
    pack_id = pack["pack_id"]
    plan = {
        "plan_id": "plan-g",
        "status": "active",
        "goal": "Learn equations",
        "components": [
            {
                "component_id": "gm",
                "component_type": "goal_map",
                "executor": "deterministic",
                "label_zh": "目标地图",
                "label_en": "Goal map",
                "status": "pending",
                "dependencies": [],
                "required": True,
            },
            {
                "component_id": "gp",
                "component_type": "guided_practice",
                "executor": "assessment",
                "label_zh": "练习",
                "label_en": "Practice",
                "status": "pending",
                "dependencies": [],
                "required": True,
                "output_ref": "revealed-quiz",
            },
        ],
    }
    assert learning_packs.create_component_plan(pack_id, plan)
    assert learning_packs.record_component_event(
        pack_id, "plan-g", "gm", {"event_id": "e-gm", "action": "complete"}
    )
    assert learning_packs.record_component_event(
        pack_id,
        "plan-g",
        "gp",
        {
            "event_id": "e-gp",
            "action": "complete",
            "observation": "correct",
            "question_id": "q-1",
            "answer": "x = 4",
            "output_ref": "revealed-quiz",
            "_server_graded": True,
        },
    )

    refreshed = learning_packs.get_pack(pack_id)
    assert refreshed["component_plans"][0]["status"] == "completed"
    assert refreshed["goal"]["status"] == "active"
    assert refreshed["goal"]["round_status"] == "completed"
    assert "round_completed_at" in refreshed["goal"]

    # a superseding plan reopens the completed goal
    next_plan = {
        "plan_id": "plan-g2",
        "status": "active",
        "supersedes_plan_id": "plan-g",
        "goal": "Learn equations",
        "components": [
            {
                "component_id": "gp2",
                "component_type": "guided_practice",
                "executor": "assessment",
                "label_zh": "练习",
                "label_en": "Practice",
                "status": "pending",
                "dependencies": [],
                "required": True,
                "output_ref": "revealed-quiz",
            }
        ],
    }
    assert learning_packs.create_component_plan(pack_id, next_plan)
    reopened = learning_packs.get_pack(pack_id)
    assert reopened["goal"]["status"] == "active"
    assert "round_status" not in reopened["goal"]
    assert "round_completed_at" not in reopened["goal"]
