"""Component-mode courseware evaluation: support components are not lessons."""

from __future__ import annotations

from typing import Any

from traittutor.generate.evaluation import evaluate_generation


def _material() -> list[dict[str, str]]:
    return [{"chunk_id": "chunk-1", "text": "植物把光能转化为化学能。光合作用是能量转化的过程。"}]


def _goal_map_result() -> dict[str, Any]:
    return {
        "kind": "courseware",
        "title": "心理测试起源",
        "sections": [],
        "markdown": "",
        "component": {
            "component_type": "goal_map",
            "props": {
                "title": "心理测试起源的学习目标",
                "milestones": [
                    "Goal map — 明确本章范围与完成判据。",
                    "Concept explanation — 先给出核心概念讲解。",
                    "Guided practice — 小步引导题产出作答证据。",
                    "Calibration checkpoint — 复盘把握度判断。",
                ],
            },
        },
        "save_target": "notebook",
    }


def test_goal_map_component_mode_passes_evaluation() -> None:
    """A single support component has no course sections or citations and must
    not be failed as if it were an empty lesson."""
    evaluation = evaluate_generation(
        "courseware",
        _goal_map_result(),
        material=_material(),
    )

    assert evaluation.verdict in ("pass", "revise")
    assert evaluation.scores["grounding_and_citations"].score == 100
    assert evaluation.scores["structure"].score >= 50
    assert evaluation.scores["personality_safety"].score == 100
    assert evaluation.overall_score >= 80


def test_full_courseware_still_requires_citations() -> None:
    """A full lesson without any reference still fails the grounding gate."""
    result = {
        "kind": "courseware",
        "title": "光合作用",
        "sections": [
            {
                "section_title": "能量转化",
                "core_content": "植物把光能转化为化学能。",
            },
            {
                "section_title": "反应物与产物",
                "core_content": "光合作用需要水和二氧化碳。",
            },
        ],
        "markdown": "## 能量转化\n植物把光能转化为化学能。",
        "save_target": "notebook",
    }
    evaluation = evaluate_generation("courseware", result, material=_material())

    assert evaluation.scores["grounding_and_citations"].score == 0
    assert evaluation.verdict == "fail"


def test_chunk_id_citations_are_verified_against_material() -> None:
    """Real lessons cite supplied chunk ids, not quoted text; the grounding
    gate must treat an exact chunk-id match as a verified citation."""
    result = {
        "kind": "courseware",
        "title": "光合作用",
        "sections": [
            {
                "title": "能量转化",
                "section_title": "能量转化",
                "core_content": "植物把光能转化为化学能。",
                "references": ["chunk-1"],
            },
            {
                "title": "反应物与产物",
                "section_title": "反应物与产物",
                "core_content": "光合作用需要水和二氧化碳。",
                "references": ["chunk-1"],
            },
        ],
        "markdown": "## 能量转化\n植物把光能转化为化学能。",
        "save_target": "notebook",
    }
    evaluation = evaluate_generation("courseware", result, material=_material())

    assert evaluation.scores["grounding_and_citations"].score == 100
    assert evaluation.verdict in ("pass", "revise")


def test_chunk_id_citation_fails_when_id_not_in_material() -> None:
    """A chunk-id citation that references no supplied chunk stays unverified
    and must still fail the grounding gate."""
    result = {
        "kind": "courseware",
        "title": "光合作用",
        "sections": [
            {
                "title": "能量转化",
                "section_title": "能量转化",
                "core_content": "植物把光能转化为化学能。",
                "references": ["chunk-999"],
            },
        ],
        "markdown": "## 能量转化\n植物把光能转化为化学能。",
        "save_target": "notebook",
    }
    evaluation = evaluate_generation("courseware", result, material=_material())

    assert evaluation.scores["grounding_and_citations"].score < 50
    assert evaluation.verdict == "fail"


def test_reflection_prompt_component_mode_passes_evaluation() -> None:
    """Other support components (reflection prompt) also skip citation gates."""
    result = {
        "kind": "courseware",
        "title": "反思提示",
        "sections": [],
        "markdown": "",
        "component": {
            "component_type": "reflection_prompt",
            "props": {
                "title": "总结能量转化",
                "prompt": "用自己的话解释光能如何转化为化学能，并尝试迁移到新例子。",
            },
        },
        "save_target": "notebook",
    }
    evaluation = evaluate_generation("courseware", result, material=_material())

    assert evaluation.verdict in ("pass", "revise")
    assert evaluation.scores["grounding_and_citations"].score == 100


def test_worked_example_component_mode_grounds_via_concept_refs() -> None:
    """A single worked_example run has no concept_explanation section; its
    projected unit must carry the component's chunk-id references so the
    grounding gate can verify them instead of failing every generation."""
    result = {
        "kind": "courseware",
        "title": "光合作用",
        "sections": [],
        "markdown": "",
        "component": {
            "component_type": "worked_example",
            "props": {
                "title": "能量转化示例",
                "body_markdown": "植物把光能转化为化学能，写成 6CO₂ + 6H₂O → C₆H₁₂O₆ + 6O₂。",
                "concept_refs": ["chunk-1"],
            },
        },
        "save_target": "notebook",
    }
    evaluation = evaluate_generation("courseware", result, material=_material())

    assert evaluation.scores["grounding_and_citations"].score == 100
    assert evaluation.verdict in ("pass", "revise")


def test_audio_component_mode_grounds_via_concept_refs() -> None:
    """A single audio (podcast) run carries the lesson's chunk-id references."""
    result = {
        "kind": "courseware",
        "title": "光合作用播客",
        "sections": [],
        "markdown": "",
        "component": {
            "component_type": "audio_explanation",
            "props": {
                "title": "光合作用",
                "body_markdown": "主持人：今天我们讲光合作用……",
                "concept_refs": ["chunk-1"],
            },
        },
        "save_target": "notebook",
    }
    evaluation = evaluate_generation("courseware", result, material=_material())

    assert evaluation.scores["grounding_and_citations"].score == 100
    assert evaluation.verdict in ("pass", "revise")


def test_visual_component_mode_grounds_via_concept_refs() -> None:
    """A single visual_map/video_explanation run grounds through chunk ids."""
    for component_type in ("visual_map", "video_explanation"):
        result = {
            "kind": "courseware",
            "title": "光合作用图",
            "sections": [],
            "markdown": "",
            "component": {
                "component_type": component_type,
                "props": {
                    "title": "光合作用示意",
                    "media_url": "/api/outputs/learning-visual.png",
                    "a11y_label": "光合作用示意图",
                    "concept_refs": ["chunk-1"],
                },
            },
            "save_target": "notebook",
        }
        evaluation = evaluate_generation("courseware", result, material=_material())

        assert evaluation.scores["grounding_and_citations"].score == 100, component_type
        assert evaluation.verdict in ("pass", "revise"), component_type


def test_projected_component_without_references_still_fails_grounding() -> None:
    """A non-support single component with no references must NOT be exempt:
    it states material facts and stays review-required until grounded."""
    result = {
        "kind": "courseware",
        "title": "光合作用示例",
        "sections": [],
        "markdown": "",
        "component": {
            "component_type": "worked_example",
            "props": {
                "title": "能量转化示例",
                "body_markdown": "植物把光能转化为化学能。",
            },
        },
        "save_target": "notebook",
    }
    evaluation = evaluate_generation("courseware", result, material=_material())

    assert evaluation.scores["grounding_and_citations"].score == 0
    assert evaluation.verdict == "fail"
