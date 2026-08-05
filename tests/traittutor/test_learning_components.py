from __future__ import annotations

import pytest

from traittutor.learning_components import (
    LearningComponentSelector,
    MaterialAffordance,
    MaterialComponentAffordances,
    SubjectSupportState,
    build_subject_support_state,
    infer_material_affordances,
)


def _affordances(*, visual: bool = False, audio: bool = False, worked: bool = False) -> MaterialComponentAffordances:
    def item(value: bool) -> MaterialAffordance:
        return MaterialAffordance(suitable=value, confidence=.8 if value else .2)
    return MaterialComponentAffordances(
        visual=item(visual), audio=item(audio), worked_example=item(worked), practice=item(True),
    )


def _plan(*, signals=None, support=None, affordances=None, completed=None, supersedes=None):
    return LearningComponentSelector().select(
        pack_id="pack-math",
        goal="Understand linear functions",
        subject_ref={"subject_id": "mathematics", "label": "Mathematics"},
        analysis_id="analysis-math",
        concept_signals=list(signals or []),
        support_state=support or SubjectSupportState(subject_id="mathematics"),
        affordances=affordances or _affordances(),
        completed_components=completed,
        supersedes_plan_id=supersedes,
    )


def test_unobserved_subject_starts_with_diagnostic_not_a_fixed_artifact_menu():
    plan = _plan()
    component_types = [item.component_type for item in plan.components]
    assert component_types[:3] == ["goal_map", "diagnostic_check", "concept_explanation"]
    assert "retrieval_card" in component_types
    assert {item.executor for item in plan.components} <= {
        "deterministic", "lesson", "assessment", "retrieval", "image", "audio",
    }


def test_wrong_answer_changes_the_component_mix_for_only_the_current_subject():
    plan = _plan(signals=[{
        "concept_id": "slope", "support_level": "needs_support",
        "mastery_probability": .18, "evidence_refs": ["question:q1"],
    }])
    component_types = [item.component_type for item in plan.components]
    assert component_types[:3] == ["goal_map", "concept_explanation", "worked_example"]
    assert all(item.bkt_stage == "needs_support" for item in plan.components)
    assert plan.subject_ref["subject_id"] == "mathematics"
    assert all("physics" not in item.concept_refs for item in plan.components)


def test_supported_concept_moves_to_transfer_instead_of_repeating_diagnosis():
    plan = _plan(signals=[{
        "concept_id": "slope", "support_level": "supported",
        "mastery_probability": .86, "verified_observation_count": 5,
    }])
    component_types = [item.component_type for item in plan.components]
    assert component_types[1] == "transfer_challenge"
    assert "diagnostic_check" not in component_types


def test_material_affordances_gate_visual_and_audio_components():
    analysis = {
        "subject": "english_foreign_language",
        "page_evidence": [{"chunk_id": "p2", "page": 2}],
    }
    affordances = infer_material_affordances(
        analysis,
        title="English pronunciation and sentence structure",
        text="Use this diagram to compare pronunciation patterns.",
    )
    plan = _plan(affordances=affordances)
    component_types = [item.component_type for item in plan.components]
    assert "visual_map" in component_types
    assert "audio_explanation" in component_types
    visual = next(item for item in plan.components if item.component_type == "visual_map")
    assert visual.executor == "image"
    assert visual.modality == "visual"


def test_subject_evidence_can_strengthen_support_actions_without_learning_style_labels():
    support = build_subject_support_state(
        {
            "dimensions": {
                "monitoring_regulation": {"emphasis": "light", "evidence_count": 0, "actions": ["checkpoint"]},
                "reflection_transfer": {"emphasis": "standard", "evidence_count": 0, "actions": ["reflection"]},
            },
        },
        subject_id="mathematics",
        strategy_evidence=[
            {"support_dimensions": ["monitoring_regulation"], "positive_weight": 1, "negative_weight": 0},
            {"support_dimensions": ["monitoring_regulation"], "positive_weight": 1, "negative_weight": 0},
            {"support_dimensions": ["monitoring_regulation"], "positive_weight": 1, "negative_weight": 0},
        ],
    )
    plan = _plan(support=support)
    assert support.source == "subject_evidence"
    assert support.dimensions["monitoring_regulation"]["emphasis"] == "strong"
    assert "progress_checkpoint" in [item.component_type for item in plan.components]
    serialized = plan.model_dump_json().lower()
    assert '"learning_style"' not in serialized
    assert '"ability_score"' not in serialized
    assert "does not diagnose" in serialized


@pytest.mark.parametrize(
    ("dimension", "expected_component"),
    [
        ("goal_planning", "progress_checkpoint"),
        ("monitoring_regulation", "worked_example"),
        ("reflection_transfer", "reflection_prompt"),
        ("motivation_emotion", "progress_checkpoint"),
    ],
)
def test_each_slr_dimension_changes_the_learning_component_mix(dimension, expected_component):
    support = SubjectSupportState(
        subject_id="mathematics",
        source="subject_evidence",
        dimensions={dimension: {"emphasis": "strong", "evidence_count": 3}},
    )
    baseline_types = [item.component_type for item in _plan().components]
    supported_types = [item.component_type for item in _plan(support=support).components]
    assert expected_component not in baseline_types
    assert expected_component in supported_types


def test_replan_preserves_completed_component_output_and_only_replaces_tail():
    first = _plan()
    completed = first.components[0].model_copy(update={"status": "completed", "output_ref": "generation-1"})
    replanned = _plan(
        signals=[{"concept_id": "slope", "support_level": "needs_support", "mastery_probability": .2}],
        completed=[completed.model_dump()],
        supersedes=first.plan_id,
    )
    assert replanned.supersedes_plan_id == first.plan_id
    assert replanned.components[0].component_id == completed.component_id
    assert replanned.components[0].output_ref == "generation-1"
    assert replanned.components[1].component_id != first.components[1].component_id
