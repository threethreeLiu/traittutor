from __future__ import annotations

import pytest

from traittutor.components import ComponentRegistry
from traittutor.generate.courseware import CoursewareArtifact, _lesson_schema
from traittutor.orchestration import (
    AgentTask,
    CoursewareExecutor,
    CoursewarePromptBundle,
    SRLSupportExecutor,
)
from traittutor.orchestration.executors import _public_figure


def test_public_figure_drops_malformed_input() -> None:
    assert _public_figure(None) is None
    assert _public_figure("not an object") is None
    assert _public_figure({"type": "unknown_type", "title": "T"}) is None
    assert _public_figure({"type": "flow", "title": " "}) is None
    # concept_map without any valid node
    assert (
        _public_figure(
            {
                "type": "concept_map",
                "title": "T",
                "nodes": [{"id": "a"}, {"label": "B"}],
                "edges": [{"from": "a", "to": "b"}],
            }
        )
        is None
    )


def test_public_figure_projects_concept_map_with_caps() -> None:
    figure = _public_figure(
        {
            "type": "concept_map",
            "title": "  Memory model  ",
            "nodes": [
                {"id": "n1", "label": "Short-term memory", "detail": "Holds a few items."},
                {"id": "n2", "label": "Long-term memory"},
                {"id": "bad", "label": ""},
                {"id": "n3", "label": "Working memory", "detail": "Manipulates items."},
                {"id": "n4", "label": "Encoding"},
                {"id": "n5", "label": "Retrieval"},
                {"id": "n6", "label": "Consolidation"},
                {"id": "n7", "label": "Forgetting"},
                {"id": "n8", "label": "Interference"},
                {"id": "n9", "label": "Overflow"},
            ],
            "edges": [
                {"from": "n1", "to": "n2", "label": "consolidates"},
                {"from": "n1", "to": "n3"},
                {"from": "n0", "to": "n2"},
            ],
        }
    )
    assert figure is not None
    assert figure["type"] == "concept_map"
    assert figure["title"] == "Memory model"
    # nodes capped at 8, malformed nodes dropped
    assert len(figure["nodes"]) == 8
    assert figure["nodes"][0] == {
        "id": "n1",
        "label": "Short-term memory",
        "detail": "Holds a few items.",
    }
    # edges referencing missing nodes are kept structurally; the renderer
    # ignores edges whose endpoints have no position
    assert len(figure["edges"]) == 3
    assert figure["edges"][0] == {"from": "n1", "to": "n2", "label": "consolidates"}


def test_public_figure_projects_flow_timeline_compare() -> None:
    flow = _public_figure(
        {
            "type": "flow",
            "title": "Pipeline",
            "steps": ["Chunk", "Analyze", "", "Render", "Publish", "x", "y", "z", "w"],
        }
    )
    assert flow is not None
    assert flow["steps"] == ["Chunk", "Analyze", "Render", "Publish", "x", "y", "z", "w"]

    timeline = _public_figure(
        {"type": "timeline", "title": "Timeline", "points": ["1800", "1900", "", "2000"]}
    )
    assert timeline is not None
    assert timeline["points"] == ["1800", "1900", "2000"]

    compare = _public_figure(
        {
            "type": "compare",
            "title": "Compare",
            "items": [
                {"label": "A", "detail": "First"},
                {"label": ""},
                {"label": "B"},
                {"label": "C", "detail": "Third"},
            ],
        }
    )
    assert compare is not None
    assert compare["items"] == [
        {"label": "A", "detail": "First"},
        {"label": "B"},
        {"label": "C", "detail": "Third"},
    ]
    # compare with fewer than two valid items is meaningless -> dropped
    assert _public_figure({"type": "compare", "title": "T", "items": [{"label": "Only"}]}) is None


def test_lesson_schema_allows_optional_figure_object() -> None:
    _lesson_schema(
        {
            "title": "T",
            "lesson_goal": "G",
            "sections": [
                {
                    "section_title": "S",
                    "goal": "G",
                    "core_content": "Body.",
                    "checkpoint": {
                        "question": "Q?",
                        "success_criteria": "SC",
                        "feedback_if_confused": "FB",
                    },
                    "reflection_prompt": "Reflect.",
                    "references": [],
                    "external_claims": [],
                    "figure": {"type": "flow", "title": "Fig", "steps": ["A"]},
                }
            ],
            "final_takeaways": [],
            "next_step_guidance": [],
        }
    )


def test_lesson_schema_rejects_non_object_figure() -> None:
    with pytest.raises(ValueError, match="figure must be an object"):
        _lesson_schema(
            {
                "title": "T",
                "lesson_goal": "G",
                "sections": [
                    {
                        "section_title": "S",
                        "goal": "G",
                        "core_content": "Body.",
                        "checkpoint": {
                            "question": "Q?",
                            "success_criteria": "SC",
                            "feedback_if_confused": "FB",
                        },
                        "reflection_prompt": "Reflect.",
                        "references": [],
                        "external_claims": [],
                        "figure": ["not", "an", "object"],
                    }
                ],
                "final_takeaways": [],
                "next_step_guidance": [],
            }
        )


def _bundle() -> CoursewarePromptBundle:
    return CoursewarePromptBundle(
        prompt_bundle_id="figure-bundle",
        version="v1",
        context_snapshot_id="figure-snapshot",
        context_snapshot_hash="h" * 64,
        material_language="en",
        requested_component_types=("concept_explanation",),
        teaching_goal="Learn the material concepts.",
        created_at="2026-08-14T00:00:00+00:00",
    )


def _task() -> AgentTask:
    return AgentTask(
        task_id="instruction",
        task_type="instruction",
        agent="Courseware Agent",
        depends_on=("material",),
        input_refs=(),
        produces_component_types=("concept_explanation",),
        budget_ms=1_000,
        timeout_ms=1_000,
    )


@pytest.mark.asyncio
async def test_courseware_executor_projects_figure_into_concept_explanation() -> None:
    artifact = CoursewareArtifact(
        lesson={
            "title": "Memory",
            "lesson_goal": "Understand memory.",
            "sections": [
                {
                    "section_title": "The modal model",
                    "goal": "Trace information flow.",
                    "core_content": "Sensory input flows through short-term memory.",
                    "checkpoint": {
                        "question": "Q?",
                        "success_criteria": "SC",
                        "feedback_if_confused": "FB",
                    },
                    "reflection_prompt": "Reflect.",
                    "references": [],
                    "external_claims": [],
                    "figure": {
                        "type": "flow",
                        "title": "Information flow",
                        "steps": ["Sensory register", "Short-term memory", "Long-term memory"],
                    },
                },
                {
                    "section_title": "Bad figure section",
                    "goal": "G",
                    "core_content": "Body.",
                    "checkpoint": {
                        "question": "Q?",
                        "success_criteria": "SC",
                        "feedback_if_confused": "FB",
                    },
                    "reflection_prompt": "Reflect.",
                    "references": [],
                    "external_claims": [],
                    # structurally broken figure must be dropped, not fatal
                    "figure": {"type": "compare", "title": "Broken", "items": [{"label": "A"}]},
                },
            ],
            "final_takeaways": [],
            "next_step_guidance": [],
        },
        content_analysis={"core_concepts": []},
        adaptation_plan={},
        trace=[],
    )

    async def body(*, chunks: object, learner_strategy: object, slr_support: object, language: str):
        del chunks, learner_strategy, slr_support, language
        return artifact

    executor = CoursewareExecutor(
        payload_provider=lambda task, bundle: {"chunks": []},
        body=body,  # type: ignore[arg-type]
    )
    result = await executor(_task(), _bundle(), ComponentRegistry())

    assert result.status == "succeeded"
    instances = result.produced_component_instances
    assert len(instances) == 2
    assert instances[0].props["title"] == "The modal model"
    assert instances[0].props["figure"] == {
        "type": "flow",
        "title": "Information flow",
        "steps": ["Sensory register", "Short-term memory", "Long-term memory"],
    }
    # broken figure dropped; the section still renders as plain explanation
    assert "figure" not in instances[1].props


@pytest.mark.asyncio
async def test_courseware_executor_projects_worked_example_steps() -> None:
    artifact = CoursewareArtifact(
        lesson={
            "title": "Energy transfer",
            "sections": [
                {
                    "section_title": "Trace the system",
                    "core_content": "Identify the boundary.\nTrack energy entering.\nTrack energy leaving.",
                    "references": [],
                    "external_claims": [],
                }
            ],
        },
        content_analysis={},
        adaptation_plan={},
        trace=[],
    )

    async def body(**_kwargs: object) -> CoursewareArtifact:
        return artifact

    task = AgentTask(
        task_id="instruction-worked",
        task_type="instruction",
        agent="Courseware Agent",
        depends_on=("material",),
        input_refs=(),
        produces_component_types=("worked_example",),
        budget_ms=1_000,
        timeout_ms=1_000,
    )
    result = await CoursewareExecutor(
        payload_provider=lambda _task, _bundle: {"chunks": []},
        body=body,
    )(task, _bundle(), ComponentRegistry())

    assert result.status == "succeeded"
    assert result.produced_component_instances[0].component_type == "worked_example"
    assert result.produced_component_instances[0].props["steps"] == [
        "Identify the boundary.",
        "Track energy entering.",
        "Track energy leaving.",
    ]


@pytest.mark.asyncio
async def test_courseware_executor_grounds_podcast_in_lesson_references() -> None:
    """The audio component carries the lesson's chunk-id references so the
    evaluation gate can verify citations instead of review-requiring every
    podcast run."""
    artifact = CoursewareArtifact(
        lesson={
            "title": "Equations",
            "lesson_goal": "Solve linear equations.",
            "sections": [
                {
                    "section_title": "Isolate the variable",
                    "goal": "Solve 2x + 3 = 7.",
                    "core_content": "Subtract 3 from both sides.",
                    "checkpoint": {
                        "question": "Solve 2x + 3 = 7.",
                        "success_criteria": "x = 2",
                        "feedback_if_confused": "Subtract 3 first.",
                    },
                    "reflection_prompt": "Explain each step.",
                    "references": ["chunk-1"],
                    "external_claims": [],
                }
            ],
            "final_takeaways": [],
            "next_step_guidance": [],
        },
        content_analysis={"core_concepts": []},
        adaptation_plan={},
        trace=[],
    )

    async def body(**_kwargs: object) -> CoursewareArtifact:
        return artifact

    async def podcast_body(*, lesson: object, language: str):
        del lesson, language
        return {"title": "Equations podcast", "script": "Today we solve equations."}

    task = AgentTask(
        task_id="instruction-audio",
        task_type="instruction",
        agent="Courseware Agent",
        depends_on=("material",),
        input_refs=(),
        produces_component_types=("audio_explanation",),
        budget_ms=1_000,
        timeout_ms=1_000,
    )
    result = await CoursewareExecutor(
        payload_provider=lambda _task, _bundle: {"chunks": []},
        body=body,
        podcast_body=podcast_body,
    )(task, _bundle(), ComponentRegistry())

    assert result.status == "succeeded"
    instances = result.produced_component_instances
    assert len(instances) == 1
    assert instances[0].component_type == "audio_explanation"
    assert instances[0].props["concept_refs"] == ["chunk-1"]


@pytest.mark.asyncio
async def test_srl_goal_map_carries_lesson_reference_records() -> None:
    """The goal map projection carries the lesson's chunk/claim reference
    records so the published page keeps its citation linkage (without them
    ``external_sources`` loses web-augmentation records even when the analysis
    requested augmentation)."""
    artifact = CoursewareArtifact(
        lesson={
            "title": "Evidence",
            "sections": [
                {
                    "section_title": "Evidence",
                    "core_content": "WHO reports the result.",
                    "references": ["external-web-1"],
                    "external_claims": [
                        {"claim": "WHO reports the result.", "source_chunk_id": "external-web-1"}
                    ],
                }
            ],
        },
        content_analysis={},
        adaptation_plan={},
        trace=[],
    )

    async def artifact_provider() -> CoursewareArtifact:
        return artifact

    chunks = [
        {
            "source_id": "web-source",
            "chunk_id": "external-web-1",
            "text": "WHO reports the result.",
            "source_url": "https://www.who.int/example",
        }
    ]
    task = AgentTask(
        task_id="srl",
        task_type="srl",
        agent="SRL Support Agent",
        depends_on=("instruction",),
        input_refs=(),
        produces_component_types=("goal_map",),
        budget_ms=1_000,
        timeout_ms=1_000,
    )
    executor = SRLSupportExecutor(
        artifact_provider=artifact_provider,
        payload_provider=lambda _task, _bundle: {"chunks": chunks},
    )
    result = await executor(task, _bundle(), ComponentRegistry())

    assert result.status == "succeeded"
    goal_map = result.produced_component_instances[0]
    assert goal_map.component_type == "goal_map"
    assert goal_map.props["concept_refs"] == [
        {"claim": "WHO reports the result.", "source_url": "https://www.who.int/example"}
    ]
