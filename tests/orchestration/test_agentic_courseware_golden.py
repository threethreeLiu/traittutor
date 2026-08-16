from __future__ import annotations

import json
from typing import Any

import pytest

from traittutor.components import PageRegion, PageSchema, get_default_registry, validate_page_schema
from traittutor.gateway import GatewayReceipt, GatewayStreamEvent
from traittutor.orchestration.agentic_contracts import (
    CoursewareAgentRole,
    CoursewareRunPolicy,
    default_agent_roster,
)
from traittutor.orchestration.agentic_specialist import (
    AgenticSpecialistExecutor,
    CoursewareBudgetLedger,
)
from traittutor.orchestration.courseware_tools import CoursewareToolContext, CoursewareToolRegistry
from traittutor.orchestration.prompt_bundle import CoursewarePromptBundle
from traittutor.orchestration.task_graph import AgentTask

_GOLDEN_SCENARIOS: tuple[tuple[str, str, CoursewareAgentRole, str], ...] = (
    ("zh-instruction-concept", "zh-CN", "instruction", "concept_explanation"),
    ("en-instruction-concept", "en", "instruction", "concept_explanation"),
    ("zh-instruction-example", "zh-CN", "instruction", "worked_example"),
    ("en-instruction-example", "en", "instruction", "worked_example"),
    ("zh-instruction-audio", "zh-CN", "instruction", "audio_explanation"),
    ("en-instruction-audio", "en", "instruction", "audio_explanation"),
    ("zh-practice-diagnostic", "zh-CN", "practice", "diagnostic_check"),
    ("en-practice-diagnostic", "en", "practice", "diagnostic_check"),
    ("zh-practice-guided", "zh-CN", "practice", "guided_practice"),
    ("en-practice-guided", "en", "practice", "guided_practice"),
    ("zh-practice-retrieval", "zh-CN", "practice", "retrieval_card"),
    ("en-practice-transfer", "en", "practice", "transfer_challenge"),
    ("zh-srl-goal", "zh-CN", "srl", "goal_map"),
    ("en-srl-goal", "en", "srl", "goal_map"),
    ("zh-srl-progress", "zh-CN", "srl", "progress_checkpoint"),
    ("en-srl-reflection", "en", "srl", "reflection_prompt"),
    ("zh-srl-review", "zh-CN", "srl", "review_queue"),
    ("en-srl-review", "en", "srl", "review_queue"),
    ("zh-visual-map", "zh-CN", "visual", "visual_map"),
    ("en-visual-video", "en", "visual", "video_explanation"),
)


@pytest.mark.parametrize(
    ("scenario_id", "language", "role", "component_type"),
    _GOLDEN_SCENARIOS,
    ids=[item[0] for item in _GOLDEN_SCENARIOS],
)
@pytest.mark.asyncio
async def test_bilingual_agentic_golden_scenario_is_scoped_and_page_safe(
    monkeypatch: pytest.MonkeyPatch,
    scenario_id: str,
    language: str,
    role: CoursewareAgentRole,
    component_type: str,
) -> None:
    calls: list[Any] = []

    class GoldenGateway:
        async def stream(self, request: Any):
            calls.append(request)
            yield GatewayStreamEvent(
                type="text",
                text="FINAL\n"
                + json.dumps({"components": [{"component_type": component_type, "props": {}}]}),
            )
            yield GatewayStreamEvent(type="usage", usage={"output_tokens": 8})
            yield GatewayStreamEvent(
                type="final",
                receipt=GatewayReceipt(
                    request_id=f"request-{scenario_id}",
                    purpose=request.purpose,
                    model="golden",
                    provider="test",
                    route="test",
                    latency_ms=1,
                    timeout_seconds=5,
                    response_format_applied=False,
                    tools_applied=len(request.tools),
                    attachments_applied=0,
                ),
            )

    monkeypatch.setattr(
        "traittutor.orchestration.agentic_specialist.get_gateway", lambda: GoldenGateway()
    )
    registry = get_default_registry()
    roster = default_agent_roster()
    bundle = CoursewarePromptBundle(
        prompt_bundle_id=f"bundle-{scenario_id}",
        version="v2",
        context_snapshot_id=f"snapshot-{scenario_id}",
        context_snapshot_hash="b" * 64,
        grounding_refs=("chunk-1",),
        material_language=language,
        requested_component_types=(component_type,),
        teaching_goal="Use only the frozen material and the qualitative support state.",
        created_at="2026-08-14T00:00:00+00:00",
    )
    tools = CoursewareToolRegistry(
        roster=roster,
        context=CoursewareToolContext(
            chunks=(
                {
                    "chunk_id": "chunk-1",
                    "source_id": "source-1",
                    "text": "A source-grounded concept statement.",
                },
            ),
            support_state={
                "kc-1": {
                    "evidence_state": "developing",
                    "change_signal": "none",
                    "verified_observation_count": 3,
                    "probability": 0.99,
                    "persona": "must-not-cross-boundary",
                }
            },
            component_registry=registry,
        ),
    )
    task = AgentTask(
        task_id=f"task-{scenario_id}",
        task_type=role,
        agent=f"{role} specialist",
        depends_on=(),
        input_refs=bundle.task_input_refs(role),
        produces_component_types=(component_type,),
        budget_ms=5_000,
        timeout_ms=5_000,
        iteration_budget=1,
        tool_budget=1,
        repair_budget=1,
    )
    executor = AgenticSpecialistExecutor(
        roster=roster,
        tools=tools,
        policy=CoursewareRunPolicy(),
        budget=CoursewareBudgetLedger(CoursewareRunPolicy()),
    )

    result = await executor(task, bundle, registry)

    assert result.status == "succeeded"
    assert len(calls) == 1
    assert {tool.name for tool in calls[0].tools} == {tool["name"] for tool in tools.schemas(role)}
    assert not {
        "write_memory",
        "write_note",
        "execute_code",
        "ask_user",
        "cron",
        "update_bkt",
        "update_persona",
    }.intersection(tool.name for tool in calls[0].tools)
    assert result.checkpoint is not None
    assert result.checkpoint.logical_llm_calls == 1
    assert result.checkpoint.tool_calls == 0
    assert result.produced_component_instances
    page = PageSchema(
        page_schema_id=f"page-{scenario_id}",
        generation_run_id=f"run-{scenario_id}",
        version="v1",
        regions=[
            PageRegion(
                region_id=f"region-{scenario_id}",
                component=result.produced_component_instances[0],
            )
        ],
        created_at="2026-08-14T00:00:00+00:00",
    )
    validate_page_schema(page, registry=registry)

    support, _ = await tools.dispatch(
        role=role,
        task_id=task.task_id,
        tool_name="read_support_state" if role != "visual" else "read_component_contract",
        arguments={} if role != "visual" else {"component_type": component_type},
    )
    serialized = json.dumps(support, sort_keys=True)
    assert "probability" not in serialized
    assert "persona" not in serialized
