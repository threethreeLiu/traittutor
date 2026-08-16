"""F-07 acceptance tests for the WS-6B production orchestrator.

These cover the acceptance criteria the per-module unit tests leave implicit:
  * one Run is fully recoverable (prompt bundle / task graph / input refs /
    results / evaluator verdict / page all re-queryable by id AND stable key);
  * replay is idempotent — the same stable run key never re-executes agents or
    re-bills, even across orchestrator instances (cross-process analog) [inv #4];
  * any non-evaluator task failure leaves the run NOT successful and emits only
    a safe (text-downgraded) page;
  * the release Evaluator rejects unregistered component types [#8] and flags
    inconsistent concept versions across components (quiz stem/explanation/answer
    must share one concept version).

They drive the public ``CoursewareOrchestrator`` + ``OrchestratorRunStore`` API
with injected stub executors and never invoke an LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.components import (
    ComponentInstance,
    ComponentRegistry,
    get_default_registry,
    validate_page_schema,
)
from traittutor.orchestration import (
    AgentTaskResult,
    AgentTaskType,
    CoursewareOrchestrator,
    CoursewarePromptBundle,
)
from traittutor.orchestration.evaluator import CoursewareEvaluator
from traittutor.orchestration.run_store import OrchestratorRunStore, stable_run_key

CREATED_AT = "2026-08-09T08:00:00+00:00"
_ALL_TYPES: tuple[AgentTaskType, ...] = (
    "material",
    "instruction",
    "practice",
    "srl",
    "visual",
    "ui_composer",
    "evaluator",
)


def _bundle() -> CoursewarePromptBundle:
    return CoursewarePromptBundle(
        prompt_bundle_id="bundle-accept",
        version="v1",
        context_snapshot_id="snapshot-accept",
        context_snapshot_hash="snapshot-hash-accept",
        material_language="zh-CN",
        requested_component_types=("concept_explanation", "guided_practice", "visual_map"),
        teaching_goal="Explain evidence quality and provide a short practice activity.",
        created_at=CREATED_AT,
    )


def _valid_instance(task: object, registry: ComponentRegistry) -> ComponentInstance | None:
    # ``task`` carries the AgentTask shape used by the executor contract.
    produces = getattr(task, "produces_component_types", ())
    task_id = getattr(task, "task_id", "task")
    if not produces:
        return None
    spec = registry.require(produces[0])
    props: dict[str, object] = {}
    if spec.allows_prop("title"):
        props["title"] = f"Output from {task_id}"
    if spec.allows_prop("body_markdown"):
        props["body_markdown"] = "Safe, source-grounded teaching content."
    return ComponentInstance(
        instance_id=f"instance-{task_id}",
        component_type=spec.component_type,
        version=spec.version,
        props=props,
        modality_hint=spec.modality,
    )


def _successful_executors(counter: dict[str, int] | None = None) -> dict[AgentTaskType, object]:
    """Stub executors that emit one valid component per producing task."""

    def execute(
        task: object, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        del bundle
        if counter is not None:
            key = getattr(task, "task_id", "?")
            counter[key] = counter.get(key, 0) + 1
        instance = _valid_instance(task, registry)
        return AgentTaskResult(
            task_id=getattr(task, "task_id", ""),
            status="succeeded",
            produced_component_instances=(instance,) if instance is not None else (),
        )

    return {task_type: execute for task_type in _ALL_TYPES}


def test_run_is_fully_recoverable_by_id_and_key(tmp_path: Path) -> None:
    store = OrchestratorRunStore(tmp_path / "runs.json")
    orch = CoursewareOrchestrator(run_store=store)
    graph = orch.plan(_bundle())
    run = orch.run(graph, _successful_executors(), generation_run_id="gen-1")

    recovered = store.get(run.run_id)
    assert recovered is not None
    assert recovered.run_id == run.run_id
    # F-07: Prompt bundle / task graph / input refs / results / verdict / page
    # are all re-queryable from the persisted receipt.
    assert recovered.prompt_bundle is not None
    assert recovered.task_graph is not None
    assert recovered.input_refs
    assert recovered.task_results
    assert recovered.evaluator_verdict is not None
    assert recovered.duration_ms is not None
    assert recovered.page is not None
    validate_page_schema(recovered.page)  # persisted page stays whitelist-valid

    by_key = store.get_by_key(stable_run_key(graph))
    assert by_key is not None
    assert by_key.run_id == run.run_id


def test_replay_does_not_re_execute_or_rebill(tmp_path: Path) -> None:
    store = OrchestratorRunStore(tmp_path / "runs.json")
    counter: dict[str, int] = {}
    executors = _successful_executors(counter)
    orch = CoursewareOrchestrator(run_store=store)
    graph = orch.plan(_bundle())

    first = orch.run(graph, executors, generation_run_id="gen-1")
    first_total = sum(counter.values())
    assert first_total > 0  # executors actually ran on the first execution

    # Replay on the same orchestrator + store: the persisted receipt is returned
    # and no executor is invoked again (invariant #4 — no re-create / re-bill).
    second = orch.run(graph, executors, generation_run_id="gen-1")
    assert sum(counter.values()) == first_total
    assert second.run_id == first.run_id

    # A FRESH orchestrator instance sharing the store (cross-process analog)
    # also recovers the same receipt without re-running any agent.
    fresh = CoursewareOrchestrator(run_store=store)
    fresh_graph = fresh.plan(_bundle())  # identical bundle -> identical run key
    third = fresh.run(fresh_graph, executors, generation_run_id="gen-1")
    assert sum(counter.values()) == first_total
    assert third.run_id == first.run_id


def test_any_task_failure_is_not_success_and_emits_safe_page(tmp_path: Path) -> None:
    store = OrchestratorRunStore(tmp_path / "runs.json")

    def execute(
        task: object, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        del bundle
        # ``material`` is always planned (grounding spine) and aborts on failure,
        # so every downstream task is aborted — an unambiguous non-success run.
        if getattr(task, "task_id", "") == "material":
            return AgentTaskResult(
                task_id="material",
                status="failed",
                produced_component_instances=(),
                notes="material executor error",
            )
        instance = _valid_instance(task, registry)
        return AgentTaskResult(
            task_id=getattr(task, "task_id", ""),
            status="succeeded",
            produced_component_instances=(instance,) if instance is not None else (),
        )

    executors = {task_type: execute for task_type in _ALL_TYPES}
    orch = CoursewareOrchestrator(run_store=store)
    graph = orch.plan(_bundle())
    run = orch.run(graph, executors, generation_run_id="gen-1")

    assert run.succeeded is False
    assert run.status != "succeeded"
    # The failure is retained for audit, never hidden behind a success flag.
    assert any(r.task_id == "material" and r.status == "failed" for r in run.task_results)
    assert run.evaluator_findings  # failure surfaces as findings
    validate_page_schema(run.page)  # only a valid safe/downgraded page is emitted


def test_evaluator_rejects_unregistered_component_type() -> None:
    registry = get_default_registry()
    bundle = _bundle()
    bad = ComponentInstance(
        instance_id="bad-1",
        component_type="definitely_not_registered_xyz",
        version="v1",
        props={},
    )
    verdict = CoursewareEvaluator().evaluate((bad,), bundle=bundle, registry=registry)
    assert verdict.status != "passed"
    assert any("unregistered" in finding.lower() for finding in verdict.findings)


def test_evaluator_flags_inconsistent_concept_versions() -> None:
    registry = get_default_registry()
    bundle = _bundle()
    usable = [
        ctype
        for ctype in (
            "concept_explanation",
            "worked_example",
            "guided_practice",
            "retrieval_card",
            "calibration_checkpoint",
        )
        if registry.is_registered(ctype) and registry.require(ctype).allows_prop("concept_refs")
    ]
    if len(usable) < 2:
        pytest.skip("no two registered component types expose a concept_refs prop")
    instances: list[ComponentInstance] = []
    for index, ctype in enumerate(usable[:2]):
        spec = registry.require(ctype)
        instances.append(
            ComponentInstance(
                instance_id=f"inst-{ctype}",
                component_type=spec.component_type,
                version=spec.version,
                props={"concept_refs": [f"fractions@v{index + 1}"]},
                modality_hint=spec.modality,
            )
        )
    verdict = CoursewareEvaluator().evaluate(tuple(instances), bundle=bundle, registry=registry)
    assert verdict.status != "passed"
    assert any("inconsistent versions" in finding for finding in verdict.findings)
