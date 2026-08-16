from __future__ import annotations

import pytest

from traittutor.components import ComponentRegistry
from traittutor.generate.courseware import CoursewareArtifact
from traittutor.orchestration import AgentTask, CoursewarePromptBundle, SRLSupportExecutor


def _bundle() -> CoursewarePromptBundle:
    return CoursewarePromptBundle(
        prompt_bundle_id="goal-map-bundle",
        version="v1",
        context_snapshot_id="goal-map-snapshot",
        context_snapshot_hash="h" * 64,
        material_language="en",
        requested_component_types=("goal_map",),
        teaching_goal="Learn the material concepts.",
        created_at="2026-08-14T00:00:00+00:00",
    )


def _task() -> AgentTask:
    return AgentTask(
        task_id="srl",
        task_type="srl",
        agent="SRL Support Agent",
        depends_on=("instruction",),
        input_refs=(),
        produces_component_types=("goal_map",),
        budget_ms=1_000,
        timeout_ms=1_000,
    )


@pytest.mark.asyncio
async def test_goal_map_falls_back_to_lesson_title_and_section_titles() -> None:
    artifact = CoursewareArtifact(
        lesson={
            "title": "Conservation of energy",
            "lesson_goal": " ",
            "sections": [
                {"section_title": "Energy transfer", "goal": ""},
                {"section_title": "Energy transfer", "goal": None},
                {"section_title": "System boundaries"},
            ],
            "final_takeaways": [],
        },
        content_analysis={"core_concepts": []},
        adaptation_plan={},
        trace=[],
    )

    async def artifact_provider() -> CoursewareArtifact:
        return artifact

    result = await SRLSupportExecutor(
        artifact_provider=artifact_provider,
        arrangement_context={
            "rationale": "Start with the map, then continue to the next component.",
            "components": [
                {
                    "component_type": "goal_map",
                    "label": "Goal map",
                    "reason": "This component should come first.",
                }
            ],
        },
    )(_task(), _bundle(), ComponentRegistry())

    assert result.produced_component_instances[0].props == {
        "title": "Conservation of energy",
        "milestones": ["Energy transfer", "System boundaries"],
    }
