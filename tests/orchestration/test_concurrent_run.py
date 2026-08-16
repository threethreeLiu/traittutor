from __future__ import annotations

import asyncio
from time import monotonic

import pytest

from traittutor.orchestration import (
    AgentTask,
    AgentTaskResult,
    CoursewareOrchestrator,
)


@pytest.mark.asyncio
async def test_independent_tasks_execute_concurrently() -> None:
    created = "2026-08-09T00:00:00+00:00"
    orchestrator = CoursewareOrchestrator()
    bundle = __import__(
        "traittutor.orchestration", fromlist=["CoursewarePromptBundle"]
    ).CoursewarePromptBundle(
        prompt_bundle_id="b",
        version="v1",
        context_snapshot_id="s",
        context_snapshot_hash="h",
        material_language="en",
        requested_component_types=(),
        teaching_goal="teach",
        created_at=created,
    )
    graph = orchestrator.plan(bundle)
    # The planned practice and SRL branches form a genuine concurrent layer.
    starts: dict[str, float] = {}

    async def execute(task: AgentTask, *args: object) -> AgentTaskResult:
        del args
        starts[task.task_id] = monotonic()
        await asyncio.sleep(0.01)
        return AgentTaskResult(
            task_id=task.task_id, status="succeeded", produced_component_instances=()
        )

    executors = {task.task_type: execute for task in graph.tasks.values()}
    await orchestrator.arun(graph, executors, generation_run_id="g")
    assert abs(starts["practice"] - starts["srl"]) < 0.008
