"""F-07 acceptance tests use injected stubs and never invoke an LLM."""

from __future__ import annotations

import asyncio
from threading import Barrier, Lock, Thread
from time import sleep

import pytest

from traittutor.components import ComponentInstance, ComponentRegistry, validate_page_schema
from traittutor.orchestration import (
    AgentExecutor,
    AgentTask,
    AgentTaskGraph,
    AgentTaskGraphError,
    AgentTaskResult,
    AgentTaskType,
    CoursewareOrchestrator,
    CoursewarePromptBundle,
    content_hash,
)
from traittutor.telemetry import InMemoryProductEventSink

CREATED_AT = "2026-08-09T08:00:00+00:00"


def _bundle() -> CoursewarePromptBundle:
    return CoursewarePromptBundle(
        prompt_bundle_id="bundle-1",
        version="v1",
        context_snapshot_id="snapshot-1",
        context_snapshot_hash="snapshot-hash-1",
        material_language="zh-CN",
        requested_component_types=("concept_explanation", "guided_practice", "visual_map"),
        teaching_goal="Explain evidence quality and provide a short practice activity.",
        created_at=CREATED_AT,
    )


def _task(task_id: str, *, depends_on: tuple[str, ...] = ()) -> AgentTask:
    return AgentTask(
        task_id=task_id,
        task_type="material",
        agent="Stub Agent",
        depends_on=depends_on,
        input_refs=(),
        produces_component_types=(),
        budget_ms=1,
        timeout_ms=1,
    )


def _graph(tasks: dict[str, AgentTask]) -> AgentTaskGraph:
    return AgentTaskGraph(
        graph_id="graph-1",
        prompt_bundle_id="bundle-1",
        prompt_bundle_hash="hash-1",
        version="v1",
        tasks=tasks,
        created_at=CREATED_AT,
    )


def _valid_instance(task: AgentTask, registry: ComponentRegistry) -> ComponentInstance | None:
    if not task.produces_component_types:
        return None
    spec = registry.require(task.produces_component_types[0])
    props: dict[str, object] = {}
    if spec.allows_prop("title"):
        props["title"] = f"Output from {task.task_id}"
    if spec.allows_prop("body_markdown"):
        props["body_markdown"] = "Safe, source-grounded teaching content."
    return ComponentInstance(
        instance_id=f"instance-{task.task_id}",
        component_type=spec.component_type,
        version=spec.version,
        props=props,
        modality_hint=spec.modality,
    )


def _successful_executors(
    seen_evaluator_inputs: list[tuple[ComponentInstance, ...]] | None = None,
) -> dict[AgentTaskType, AgentExecutor]:
    def execute(
        task: AgentTask,
        bundle: CoursewarePromptBundle,
        registry: ComponentRegistry,
    ) -> AgentTaskResult:
        del bundle
        if task.task_type == "evaluator" and seen_evaluator_inputs is not None:
            seen_evaluator_inputs.append(getattr(task, "produced_component_instances", ()))
        instance = _valid_instance(task, registry)
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded",
            produced_component_instances=(instance,) if instance is not None else (),
        )

    return {
        task_type: execute
        for task_type in (
            "material",
            "instruction",
            "practice",
            "srl",
            "visual",
            "ui_composer",
            "evaluator",
        )
    }


def test_plan_builds_dag_with_evaluator_last() -> None:
    graph = CoursewareOrchestrator().plan(_bundle())

    order = graph.topological_order()
    assert order[-1] == "evaluator"
    assert order.index("ui_composer") < order.index("evaluator")
    assert graph.tasks["evaluator"].depends_on == ("ui_composer",)


def test_plan_compiles_least_context_agent_task_prompts() -> None:
    bundle = _bundle().model_copy(
        update={
            "learner_profile_snapshot_id": "profile-1",
            "subject_state_snapshot_id": "subject-state-1",
            "grounding_refs": ("chunk-1",),
            "interaction_refs": ("episode:e1",),
            "persona_contract_ref": "persona-1:hash-1",
        }
    )
    graph = CoursewareOrchestrator().plan(bundle)

    instruction = graph.tasks["instruction"]
    ui_composer = graph.tasks["ui_composer"]
    assert instruction.prompt is not None
    assert instruction.prompt.input_refs == instruction.input_refs
    assert "context_snapshot:snapshot-1" in instruction.input_refs
    assert "grounding:chunk-1" in instruction.input_refs
    assert "persona_contract:persona-1:hash-1" in instruction.input_refs

    assert ui_composer.prompt is not None
    assert all("context_snapshot:" not in ref for ref in ui_composer.input_refs)
    assert all("learner_profile" not in ref for ref in ui_composer.input_refs)
    assert all("persona_contract" not in ref for ref in ui_composer.input_refs)
    assert ui_composer.input_refs == (
        "prompt_bundle:bundle-1",
        "component_catalog:component-catalog-v1",
        "output_contract:page-schema-v1",
    )


def test_plan_gives_real_instruction_generation_provider_latency_headroom() -> None:
    """The live instruction executor performs three sequential Gateway calls.

    Its enforced deadline is the smaller of ``budget_ms`` and ``timeout_ms``;
    the aggregate budget must exceed a single provider's 120-second request
    boundary so normal cross-call variance does not become a degraded page.
    """
    instruction = CoursewareOrchestrator().plan(_bundle()).tasks["instruction"]

    assert instruction.budget_ms >= 180_000
    assert instruction.timeout_ms >= instruction.budget_ms


def test_prompt_bundle_hash_changes_with_snapshot_content_and_bundle_version() -> None:
    bundle = _bundle()

    changed_snapshot = bundle.model_copy(update={"context_snapshot_hash": "snapshot-hash-2"})
    changed_version = bundle.model_copy(update={"version": "v2"})
    same_inputs_new_receipt = bundle.model_copy(
        update={
            "prompt_bundle_id": "bundle-2",
            "created_at": "2026-08-09T09:00:00+00:00",
        }
    )

    assert content_hash(changed_snapshot) != content_hash(bundle)
    assert content_hash(changed_version) != content_hash(bundle)
    assert content_hash(same_inputs_new_receipt) == content_hash(bundle)


def test_plan_validates_no_cycle() -> None:
    graph = CoursewareOrchestrator().plan(_bundle())

    assert graph.validate_graph() is None


def test_topological_order_detects_cycle() -> None:
    graph = _graph(
        {
            "a": _task("a", depends_on=("b",)),
            "b": _task("b", depends_on=("a",)),
        }
    )

    with pytest.raises(AgentTaskGraphError, match="cycle"):
        graph.validate_graph()


def test_validate_rejects_missing_dependency() -> None:
    graph = _graph({"a": _task("a", depends_on=("missing",))})

    with pytest.raises(AgentTaskGraphError, match="missing task_id"):
        graph.validate_graph()


def test_run_succeeds_when_all_executors_produce_valid_instances() -> None:
    registry = ComponentRegistry()
    orchestrator = CoursewareOrchestrator(registry=registry)
    graph = orchestrator.plan(_bundle())
    evaluator_inputs: list[tuple[ComponentInstance, ...]] = []

    run = orchestrator.run(
        graph,
        _successful_executors(evaluator_inputs),
        generation_run_id="generation-1",
    )

    assert run.succeeded is True
    assert evaluator_inputs and evaluator_inputs[0]
    assert len(evaluator_inputs[0]) == len(run.page.regions)
    assert validate_page_schema(run.page, registry=registry) is run.page


def test_run_emits_bounded_operational_telemetry() -> None:
    registry = ComponentRegistry()
    sink = InMemoryProductEventSink()
    orchestrator = CoursewareOrchestrator(registry=registry, event_sink=sink)
    graph = orchestrator.plan(_bundle())

    run = orchestrator.run(
        graph,
        _successful_executors(),
        generation_run_id="generation-telemetry",
    )

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_name == "courseware_orchestrator.run"
    assert event.attributes["run_id"] == run.run_id
    assert event.attributes["outcome"] == "succeeded"
    assert event.attributes["fallback_used"] is False
    assert event.attributes["degraded"] is False
    assert "run_id" not in event.metric_labels


def test_run_degrades_when_an_executor_fails() -> None:
    registry = ComponentRegistry()
    sink = InMemoryProductEventSink()
    orchestrator = CoursewareOrchestrator(registry=registry, event_sink=sink)
    graph = orchestrator.plan(_bundle())
    executors = _successful_executors()

    def fail_instruction(
        task: AgentTask,
        bundle: CoursewarePromptBundle,
        task_registry: ComponentRegistry,
    ) -> AgentTaskResult:
        del bundle, task_registry
        return AgentTaskResult(
            task_id=task.task_id,
            status="failed",
            produced_component_instances=(),
            notes="stubbed failure",
        )

    executors["instruction"] = fail_instruction
    run = orchestrator.run(graph, executors, generation_run_id="generation-2")

    assert run.succeeded is False
    assert run.page.supersedes_page_id == f"{run.run_id}:page"
    assert sink.events[0].attributes["degraded"] is True
    assert sink.events[0].attributes["fallback_used"] is True
    assert all(
        region.component is None or registry.is_registered(region.component.component_type)
        for region in run.page.regions
    )
    validate_page_schema(run.page, registry=registry)


def test_legacy_v1_max_retries_is_readable_but_never_executed() -> None:
    orchestrator = CoursewareOrchestrator()
    calls = 0
    task = _task("legacy-retry").model_copy(update={"failure_policy": "retry", "max_retries": 3})

    def fail_once(
        _task: AgentTask,
        _bundle: CoursewarePromptBundle,
        _registry: ComponentRegistry,
    ) -> AgentTaskResult:
        nonlocal calls
        calls += 1
        raise RuntimeError("legacy retry must stay inert")

    result = asyncio.run(orchestrator._execute(task, _bundle(), {"material": fail_once}))

    assert calls == 1
    assert result.status == "failed"
    assert "attempt 1" in result.notes


def test_optional_agent_degradation_keeps_valid_core_page() -> None:
    registry = ComponentRegistry()
    orchestrator = CoursewareOrchestrator(registry=registry)
    graph = orchestrator.plan(_bundle())
    executors = _successful_executors()

    def unavailable_visual(
        task: AgentTask,
        bundle: CoursewarePromptBundle,
        task_registry: ComponentRegistry,
    ) -> AgentTaskResult:
        del bundle, task_registry
        return AgentTaskResult(
            task_id=task.task_id,
            status="degraded",
            produced_component_instances=(),
            notes="optional visual unavailable",
        )

    executors["visual"] = unavailable_visual
    run = orchestrator.run(graph, executors, generation_run_id="generation-optional-degrade")

    assert run.succeeded is False
    assert run.status == "degraded"
    assert run.page.supersedes_page_id is None
    assert any(
        region.component is not None and region.component.component_type == "concept_explanation"
        for region in run.page.regions
    )


def test_run_is_idempotent_by_bundle_hash() -> None:
    orchestrator = CoursewareOrchestrator()
    graph = orchestrator.plan(_bundle())
    call_count = 0
    successful = _successful_executors()

    def counted(task_type: AgentTaskType) -> AgentExecutor:
        executor = successful[task_type]

        def execute(
            task: AgentTask,
            bundle: CoursewarePromptBundle,
            registry: ComponentRegistry,
        ) -> AgentTaskResult:
            nonlocal call_count
            call_count += 1
            return executor(task, bundle, registry)

        return execute

    executors = {task_type: counted(task_type) for task_type in successful}
    first = orchestrator.run(graph, executors, generation_run_id="generation-3")
    calls_after_first_run = call_count
    second = orchestrator.run(graph, executors, generation_run_id="generation-3")

    assert first.run_id == second.run_id
    assert content_hash(_bundle()) == graph.prompt_bundle_hash
    assert call_count == calls_after_first_run


def test_run_is_atomic_under_concurrent_replay() -> None:
    orchestrator = CoursewareOrchestrator()
    graph = orchestrator.plan(_bundle())
    successful = _successful_executors()
    start_barrier = Barrier(2)
    count_lock = Lock()
    call_count = 0
    runs: list[object] = []

    def counted(task_type: AgentTaskType) -> AgentExecutor:
        executor = successful[task_type]

        def execute(
            task: AgentTask,
            bundle: CoursewarePromptBundle,
            registry: ComponentRegistry,
        ) -> AgentTaskResult:
            nonlocal call_count
            with count_lock:
                call_count += 1
            # Release the GIL so an unlocked replay reliably reaches the same
            # cache miss while the first graph is still executing.
            sleep(0.005)
            return executor(task, bundle, registry)

        return execute

    executors = {task_type: counted(task_type) for task_type in successful}

    def run_replay() -> None:
        start_barrier.wait()
        runs.append(orchestrator.run(graph, executors, generation_run_id="generation-concurrent"))

    threads = [Thread(target=run_replay) for _ in range(start_barrier.parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(runs) == start_barrier.parties
    assert runs[0] is runs[1]
    assert call_count == len(graph.tasks)
