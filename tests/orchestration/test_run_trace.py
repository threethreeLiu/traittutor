"""Learner-safe projections for persisted orchestration runs."""

from __future__ import annotations

from traittutor.components import text_degrade_page
from traittutor.orchestration.courseware_orchestrator import AgentTaskResult, OrchestratorRun
from traittutor.orchestration.evaluator import EvaluatorVerdict
from traittutor.orchestration.prompt_bundle import CoursewarePromptBundle
from traittutor.orchestration.run_trace import project_learner_safe_run_trace
from traittutor.orchestration.task_graph import AgentTask, AgentTaskGraph

CREATED_AT = "2026-08-11T08:00:00+00:00"


def _private_run() -> OrchestratorRun:
    bundle = CoursewarePromptBundle(
        prompt_bundle_id="bundle-safe-id",
        version="v1",
        context_snapshot_id="snapshot-safe-id",
        context_snapshot_hash="private-hash",
        grounding_refs=("chunk-safe-id", "SECRET PRIVATE PROMPT BODY"),
        material_language="zh-CN",
        requested_component_types=("concept_explanation",),
        teaching_goal="SECRET ANSWER AND RUBRIC",
        created_at=CREATED_AT,
    )
    material = AgentTask(
        task_id="material",
        task_type="material",
        agent="Material Agent",
        depends_on=(),
        input_refs=(
            "context_snapshot:snapshot-safe-id",
            "grounding:chunk-safe-id",
            "grounding:SECRET PRIVATE PROMPT BODY",
            "tool_args:SECRET_TOOL_ARGS",
        ),
        produces_component_types=(),
        budget_ms=100,
        timeout_ms=150,
        max_retries=1,
        failure_policy="degrade",
    )
    instruction = AgentTask(
        task_id="instruction",
        task_type="instruction",
        agent="Instruction Agent",
        depends_on=("material",),
        input_refs=("prompt_bundle:bundle-safe-id",),
        produces_component_types=("concept_explanation",),
        budget_ms=200,
        timeout_ms=250,
        failure_policy="abort",
    )
    graph = AgentTaskGraph(
        graph_id="graph-safe-id",
        prompt_bundle_id=bundle.prompt_bundle_id,
        prompt_bundle_hash="private-prompt-bundle-hash",
        version="v1",
        tasks={"material": material, "instruction": instruction},
        created_at=CREATED_AT,
    )
    return OrchestratorRun(
        run_id="run-safe-id",
        graph_id=graph.graph_id,
        generation_run_id="generation-safe-id",
        task_results=(
            AgentTaskResult(
                task_id="material",
                status="degraded",
                produced_component_instances=(),
                notes="SECRET_TOOL_ARGS and private provider reasoning",
            ),
            AgentTaskResult(
                task_id="instruction",
                status="failed",
                produced_component_instances=(),
                notes="executor timed out while using SECRET ANSWER",
            ),
        ),
        evaluator_findings=("component schema violation: SECRET RUBRIC",),
        succeeded=False,
        page=text_degrade_page(
            page_schema_id="run-safe-id:page",
            generation_run_id="generation-safe-id",
            reason="orchestration_failed",
            created_at=CREATED_AT,
        ),
        run_key="private-run-key",
        status="degraded",
        prompt_bundle=bundle,
        task_graph=graph,
        input_refs=tuple(ref for task in graph.tasks.values() for ref in task.input_refs),
        evaluator_verdict=EvaluatorVerdict(
            status="repair",
            findings=("component schema violation: SECRET RUBRIC",),
            offending_task_ids=("instruction",),
            repair_note="SECRET PRIVATE REPAIR PROMPT",
        ),
        duration_ms=245,
    )


def test_trace_projects_graph_status_dependencies_refs_budget_and_validation() -> None:
    trace = project_learner_safe_run_trace(_private_run())

    assert trace.generation_run_id == "generation-safe-id"
    assert trace.status == "degraded"
    assert trace.graph_status == "available"
    assert [node.task_id for node in trace.nodes] == ["material", "instruction"]
    assert trace.nodes[1].depends_on == ("material",)
    assert trace.nodes[1].status == "failed"
    assert trace.nodes[1].failure_code == "executor_timeout"
    assert trace.nodes[0].degradation_codes == ("task_degraded",)
    assert trace.nodes[0].input_refs[0].kind == "context_snapshot"
    assert trace.nodes[0].input_refs[0].ref_id == "snapshot-safe-id"
    assert trace.nodes[0].redacted_input_ref_count == 2
    assert trace.budget.total_planned_budget_ms == 300
    assert trace.budget.total_timeout_ms == 400
    assert trace.budget.total_retry_limit == 1
    assert trace.budget.elapsed_ms == 245
    assert trace.validation.status == "repair"
    assert trace.validation.finding_count == 1
    assert trace.validation.category_codes == ("component_schema",)
    assert trace.validation.offending_task_ids == ("instruction",)
    assert "task_failed" in trace.degradation_codes
    assert "validation_not_passed" in trace.degradation_codes


def test_trace_serialization_never_contains_private_run_content() -> None:
    payload = project_learner_safe_run_trace(_private_run()).model_dump(mode="json")
    serialized = str(payload)

    for secret in (
        "SECRET PRIVATE PROMPT BODY",
        "SECRET ANSWER AND RUBRIC",
        "SECRET_TOOL_ARGS",
        "SECRET RUBRIC",
        "SECRET PRIVATE REPAIR PROMPT",
        "private provider reasoning",
        "private-prompt-bundle-hash",
        "private-run-key",
    ):
        assert secret not in serialized

    forbidden_keys = {
        "prompt",
        "prompt_bundle",
        "teaching_goal",
        "notes",
        "evaluator_findings",
        "repair_note",
        "produced_component_instances",
        "page",
        "props",
        "answer",
        "rubric",
        "tool_args",
        "owner_id",
        "user_id",
    }

    def assert_safe_keys(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value)
            for child in value.values():
                assert_safe_keys(child)
        elif isinstance(value, list):
            for child in value:
                assert_safe_keys(child)

    assert_safe_keys(payload)
