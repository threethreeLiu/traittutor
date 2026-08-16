"""Deterministic planning and execution seam for F-07 courseware generation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import hashlib
import inspect
from threading import Lock
import time
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from traittutor.components import (
    ComponentInstance,
    ComponentRegistry,
    PageRegion,
    PageSchema,
    get_default_registry,
    safe_validate_or_degrade,
    text_degrade_page,
    validate_page_schema,
)
from traittutor.telemetry import (
    ProductEventSink,
    get_configured_product_event_sink,
    record_product_event,
)

from .agentic_contracts import (
    AgentNodeCheckpoint,
    AgentRosterManifest,
    AgentTaskGraphV2,
    CoursewareRunPolicy,
    MaterialContextOutput,
    default_agent_roster,
)
from .agentic_planner import PLANNER_CONTRACT_VERSION, AgenticCoursewarePlanner
from .evaluator import EvaluatorVerdict
from .prompt_bundle import CoursewarePromptBundle, content_hash
from .task_graph import AgentTask, AgentTaskGraph, AgentTaskPrompt, AgentTaskType

if TYPE_CHECKING:
    from .agentic_specialist import CoursewareBudgetLedger
    from .run_store import OrchestratorRunStore

AgentTaskResultStatus = Literal["succeeded", "degraded", "failed"]


class AgentTaskResult(BaseModel):
    """Immutable executor output retained for run audit and safe assembly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=64)
    status: AgentTaskResultStatus
    produced_component_instances: tuple[ComponentInstance, ...]
    notes: str = ""
    replan_requested: bool = False
    checkpoint: AgentNodeCheckpoint | None = None
    material_context_outputs: tuple[MaterialContextOutput, ...] = ()


AgentExecutor: TypeAlias = Callable[
    [AgentTask, CoursewarePromptBundle, ComponentRegistry],
    AgentTaskResult | Awaitable[AgentTaskResult],
]


class OrchestratorRun(BaseModel):
    """Frozen run receipt that keeps failures visible beside the safe page."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    graph_id: str
    generation_run_id: str
    task_results: tuple[AgentTaskResult, ...]
    evaluator_findings: tuple[str, ...]
    succeeded: bool
    page: PageSchema
    run_key: str = ""
    status: Literal["succeeded", "degraded", "failed"] = "failed"
    prompt_bundle: CoursewarePromptBundle | None = None
    task_graph: AgentTaskGraph | None = None
    input_refs: tuple[str, ...] = ()
    evaluator_verdict: EvaluatorVerdict | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    roster_manifest: AgentRosterManifest | None = None
    run_policy: CoursewareRunPolicy | None = None
    replan_count: int = Field(default=0, ge=0, le=1)


class _EvaluatorTask(AgentTask):
    """Give the evaluator exact outputs without exposing mutable global state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    produced_component_instances: tuple[ComponentInstance, ...]


class _AgenticSpecialistTask(AgentTask):
    """Agent task with typed outputs resolved from ancestor Material nodes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dependency_material_outputs: tuple[MaterialContextOutput, ...] = ()


_COMPONENT_TYPES: dict[AgentTaskType, tuple[str, ...]] = {
    "material": (),
    "instruction": ("concept_explanation", "worked_example", "audio_explanation"),
    "practice": (
        "diagnostic_check",
        "guided_practice",
        "calibration_checkpoint",
        "retrieval_card",
        "transfer_challenge",
    ),
    "srl": ("goal_map", "progress_checkpoint", "reflection_prompt", "review_queue"),
    "visual": ("visual_map", "video_explanation"),
    "ui_composer": (),
    "evaluator": (),
}

# Union of every component type any task can produce. Used by the adaptive
# fallback below: a requested set that matches no contract type at all collapses
# to "no restriction" so page generation never blocks on a bad/empty selection.
_ALL_CONTRACT_TYPES: frozenset[str] = frozenset(
    component_type
    for component_types in _COMPONENT_TYPES.values()
    for component_type in component_types
)


def _requested_filter(
    requested_component_types: tuple[str, ...],
) -> frozenset[str] | None:
    """Restrict component selection to the adaptive plan, with a static fallback.

    Returns ``None`` ("no restriction") when the bundle carried no requested
    types or the requested set matched no known contract type — in both cases
    the orchestrator falls back to the full static ``_COMPONENT_TYPES`` map,
    which keeps page generation unblockable when adaptive selection is absent
    or unusable (invariant #8: an unusable selector never prevents a page).
    """
    raw = frozenset(requested_component_types)
    if not raw or not (raw & _ALL_CONTRACT_TYPES):
        return None
    return raw


_TASK_SETTINGS: dict[AgentTaskType, tuple[int, int, Literal["retry", "degrade", "abort"]]] = {
    "material": (8_000, 12_000, "abort"),
    # The instruction executor is the one LLM-bearing spine in the current
    # production composition root.  It makes two sequential Gateway calls
    # (adaptation plan, then lesson) plus the material analysis when the
    # upload analysis is not reused.  Each cloud request may legitimately
    # approach the provider's 120-second request boundary, and slower
    # providers (e.g. MiniMax-M3 at ~50 tokens/s) can need several minutes
    # for a full lesson.  The 180-second budget that fit the original
    # single-call fast providers made every multi-call run time out on
    # slower models.  Keep a finite cancellation boundary, but give the
    # serial pipeline enough total headroom for provider variance and
    # structured-output validation; demo-mode output is bounded in
    # courseware.py so this budget stays affordable.
    "instruction": (240_000, 270_000, "abort"),
    "practice": (10_000, 15_000, "degrade"),
    "srl": (6_000, 10_000, "degrade"),
    "visual": (10_000, 15_000, "degrade"),
    "ui_composer": (5_000, 8_000, "abort"),
    "evaluator": (8_000, 12_000, "abort"),
}

_AGENT_LABELS: dict[AgentTaskType, str] = {
    "material": "Material Agent",
    "instruction": "Instruction Agent",
    "practice": "Practice Agent",
    "srl": "SRL Support Agent",
    "visual": "Visual Agent",
    "ui_composer": "UI Composer",
    "evaluator": "Evaluator",
}


def _dependency_material_outputs(
    task: AgentTask,
    task_index: Mapping[str, AgentTask],
    results_by_id: Mapping[str, AgentTaskResult],
) -> tuple[MaterialContextOutput, ...]:
    """Collect transitive material-context outputs of a task's dependencies."""
    collected: dict[str, MaterialContextOutput] = {}
    pending = list(task.depends_on)
    visited: set[str] = set()
    while pending:
        dependency_id = pending.pop()
        if dependency_id in visited:
            continue
        visited.add(dependency_id)
        dependency_result = results_by_id.get(dependency_id)
        if dependency_result is not None:
            for output in dependency_result.material_context_outputs:
                collected[output.source_id] = output
        dependency_task = task_index.get(dependency_id)
        if dependency_task is not None:
            pending.extend(dependency_task.depends_on)
    return tuple(collected[key] for key in sorted(collected))


class CoursewareOrchestrator:
    """Plan and execute courseware without embedding an LLM or state writer.

    Executors are injected at the boundary, which makes the orchestrator a
    deterministic coordinator rather than another learning-state authority.
    It never writes Memory, BKT, ErrorRecord, ReviewItem, or Persona (invariant
    #3). Completed run receipts are cached by their content-derived id so a
    same-process replay cannot rebuild or rebill components (invariant #4).
    """

    def __init__(
        self,
        *,
        registry: ComponentRegistry | None = None,
        run_store: OrchestratorRunStore | None = None,
        event_sink: ProductEventSink | None = None,
    ) -> None:
        self._registry = registry if registry is not None else get_default_registry()
        self._bundles: dict[tuple[str, str], CoursewarePromptBundle] = {}
        self._completed_runs: dict[str, OrchestratorRun] = {}
        self._run_locks: dict[str, Lock] = {}
        self._run_locks_guard = Lock()
        self._run_store = run_store
        self._agentic_configs: dict[str, tuple[AgentRosterManifest, CoursewareRunPolicy]] = {}
        self._agentic_replan_counts: dict[str, int] = {}
        self._event_sink = (
            event_sink if event_sink is not None else get_configured_product_event_sink()
        )

    def _registered_outputs(
        self,
        task_type: AgentTaskType,
        *,
        requested: frozenset[str] | None = None,
    ) -> tuple[str, ...]:
        return tuple(
            component_type
            for component_type in _COMPONENT_TYPES[task_type]
            if self._registry.is_registered(component_type)
            and (requested is None or component_type in requested)
        )

    def _task(
        self,
        task_type: AgentTaskType,
        *,
        depends_on: tuple[str, ...],
        bundle: CoursewarePromptBundle,
        requested: frozenset[str] | None = None,
    ) -> AgentTask:
        budget_ms, timeout_ms, failure_policy = _TASK_SETTINGS[task_type]
        outputs = self._registered_outputs(task_type, requested=requested)
        input_refs = bundle.task_input_refs(task_type)
        return AgentTask(
            task_id=task_type,
            task_type=task_type,
            agent=_AGENT_LABELS[task_type],
            depends_on=depends_on,
            input_refs=input_refs,
            produces_component_types=outputs,
            budget_ms=budget_ms,
            timeout_ms=timeout_ms,
            max_retries=0,
            failure_policy=failure_policy,
            prompt=AgentTaskPrompt(
                prompt_id=f"{bundle.prompt_bundle_id}:{task_type}",
                task_type=task_type,
                input_refs=input_refs,
                output_component_types=outputs,
            ),
        )

    def plan(self, bundle: CoursewarePromptBundle) -> AgentTaskGraph:
        """Build the fixed minimal DAG without calling an LLM.

        Registry-aware pruning avoids starting an Agent that cannot emit a
        whitelisted component. Material and instruction retain the grounding
        spine; UI composition and evaluation retain the release gate.
        """
        requested = _requested_filter(bundle.requested_component_types)
        tasks: dict[str, AgentTask] = {}
        tasks["material"] = self._task(
            "material", depends_on=(), bundle=bundle, requested=requested
        )
        tasks["instruction"] = self._task(
            "instruction", depends_on=("material",), bundle=bundle, requested=requested
        )

        branch_ids: list[str] = []
        for task_type in ("practice", "srl"):
            if self._registered_outputs(task_type, requested=requested):
                tasks[task_type] = self._task(
                    task_type,
                    depends_on=("instruction",),
                    bundle=bundle,
                    requested=requested,
                )
                branch_ids.append(task_type)

        if self._registered_outputs("visual", requested=requested):
            visual_dependencies = tuple(branch_ids) if branch_ids else ("instruction",)
            tasks["visual"] = self._task(
                "visual", depends_on=visual_dependencies, bundle=bundle, requested=requested
            )

        producer_ids = tuple(tasks)
        tasks["ui_composer"] = self._task(
            "ui_composer", depends_on=producer_ids, bundle=bundle, requested=requested
        )
        tasks["evaluator"] = self._task(
            "evaluator", depends_on=("ui_composer",), bundle=bundle, requested=requested
        )

        bundle_hash = content_hash(bundle)
        graph = AgentTaskGraph(
            graph_id=f"courseware_graph_{bundle_hash[:24]}",
            prompt_bundle_id=bundle.prompt_bundle_id,
            prompt_bundle_hash=bundle_hash,
            version="v1",
            tasks=tasks,
            created_at=bundle.created_at,
        )
        graph.validate_graph()
        self._bundles[(graph.graph_id, graph.prompt_bundle_id)] = bundle
        return graph

    def _convert_agentic_graph(
        self,
        planned: AgentTaskGraphV2,
        *,
        bundle: CoursewarePromptBundle,
        request_key: str,
    ) -> AgentTaskGraph:
        tasks: dict[str, AgentTask] = {}
        for planned_task in planned.tasks:
            outputs = tuple(
                component_type
                for component_type in planned_task.output_component_types
                if self._registry.is_registered(component_type)
            )
            if outputs != planned_task.output_component_types:
                raise ValueError("Planner emitted an unregistered component type")
            budget_ms, timeout_ms, failure_policy = _TASK_SETTINGS[planned_task.role]
            tasks[planned_task.task_id] = AgentTask(
                task_id=planned_task.task_id,
                task_type=planned_task.role,
                agent=_AGENT_LABELS[planned_task.role],
                depends_on=planned_task.depends_on,
                input_refs=planned_task.input_refs,
                produces_component_types=outputs,
                budget_ms=budget_ms,
                timeout_ms=timeout_ms,
                max_retries=0,
                iteration_budget=planned_task.iteration_budget,
                tool_budget=planned_task.tool_budget,
                repair_budget=planned_task.repair_budget,
                failure_policy=failure_policy,
                prompt=AgentTaskPrompt(
                    prompt_id=f"{bundle.prompt_bundle_id}:{planned_task.task_id}",
                    task_type=planned_task.role,
                    input_refs=planned_task.input_refs,
                    output_component_types=outputs,
                ),
            )
        producer_ids = tuple(tasks)
        tasks["ui_composer"] = self._task("ui_composer", depends_on=producer_ids, bundle=bundle)
        tasks["evaluator"] = self._task("evaluator", depends_on=("ui_composer",), bundle=bundle)
        graph = AgentTaskGraph(
            graph_id=f"courseware_graph_v2_{request_key[:24]}",
            prompt_bundle_id=bundle.prompt_bundle_id,
            prompt_bundle_hash=content_hash(bundle),
            version="v2",
            tasks=tasks,
            created_at=bundle.created_at,
        )
        graph.validate_graph()
        self._bundles[(graph.graph_id, graph.prompt_bundle_id)] = bundle
        return graph

    async def aplan(
        self,
        bundle: CoursewarePromptBundle,
        *,
        generation_run_id: str,
        planner: AgenticCoursewarePlanner | None = None,
        roster: AgentRosterManifest | None = None,
        policy: CoursewareRunPolicy | None = None,
        replan_iteration: int = 0,
        replan_reason_codes: tuple[str, ...] = (),
        budget: CoursewareBudgetLedger | None = None,
    ) -> AgentTaskGraph:
        """Claim, generate, validate, and persist one AgentTaskGraph v2."""
        from .run_store import (
            AgenticPlanReceipt,
            stable_agentic_request_key,
        )

        roster = roster or default_agent_roster()
        policy = policy or CoursewareRunPolicy()
        if replan_iteration < 0 or replan_iteration > policy.max_replans:
            raise ValueError("courseware replan budget exhausted")
        if replan_iteration == 0 and replan_reason_codes:
            raise ValueError("initial Planner call cannot carry replan reasons")
        if replan_iteration > 0 and not replan_reason_codes:
            raise ValueError("replan requires a bounded reason code")
        request_key = stable_agentic_request_key(
            bundle,
            planner_contract=PLANNER_CONTRACT_VERSION,
            roster=roster,
            policy=policy,
            replan_iteration=replan_iteration,
            replan_reason_codes=replan_reason_codes,
        )
        planner = planner or AgenticCoursewarePlanner()

        async def resolve() -> AgentTaskGraphV2:
            if self._run_store is not None:
                persisted = self._run_store.get_plan(request_key)
                if persisted is not None:
                    return persisted.graph
            output_tokens_before = budget.output_tokens if budget is not None else 0
            planned = await planner.plan(
                bundle,
                roster=roster,
                policy=policy,
                replan_reason_codes=replan_reason_codes,
                budget=budget,
            )
            if self._run_store is not None:
                self._run_store.save_plan(
                    AgenticPlanReceipt(
                        request_key=request_key,
                        generation_run_id=generation_run_id,
                        planner_contract=PLANNER_CONTRACT_VERSION,
                        graph=planned,
                        logical_llm_calls=1,
                        output_tokens=(
                            max(0, budget.output_tokens - output_tokens_before)
                            if budget is not None
                            else 0
                        ),
                        started_at_unix=(budget.started_at_unix if budget is not None else None),
                    )
                )
            return planned

        if self._run_store is None:
            planned = await resolve()
        else:
            claim = self._run_store.execution_lock(f"planner-{request_key}")
            await asyncio.to_thread(claim.__enter__)
            try:
                planned = await resolve()
            finally:
                await asyncio.to_thread(claim.__exit__, None, None, None)
        graph = self._convert_agentic_graph(planned, bundle=bundle, request_key=request_key)
        self._agentic_configs[graph.graph_id] = (roster, policy)
        self._agentic_replan_counts[graph.graph_id] = replan_iteration
        return graph

    @staticmethod
    def _failed_result(task: AgentTask, note: str) -> AgentTaskResult:
        return AgentTaskResult(
            task_id=task.task_id,
            status="failed",
            produced_component_instances=(),
            notes=note,
        )

    async def _execute(
        self,
        task: AgentTask,
        bundle: CoursewarePromptBundle,
        executors: Mapping[AgentTaskType, AgentExecutor],
    ) -> AgentTaskResult:
        executor = executors.get(task.task_type)
        if executor is None:
            return self._failed_result(task, f"missing executor for {task.task_type}")

        # ``AgentTask.max_retries`` is retained solely to read historical v1
        # receipts. Never execute it: otherwise a replay can reintroduce hidden
        # nested retries outside Gateway, Specialist, repair, and replan budgets.
        attempts = 1
        result: AgentTaskResult | None = None
        for attempt in range(attempts):
            try:
                if inspect.iscoroutinefunction(executor):
                    pending: Awaitable[Any] = cast(
                        Awaitable[Any], executor(task, bundle, self._registry)
                    )
                else:
                    pending = asyncio.to_thread(executor, task, bundle, self._registry)
                effective_ms = (
                    min(value for value in (task.budget_ms, task.timeout_ms) if value > 0)
                    if task.budget_ms > 0 and task.timeout_ms > 0
                    else max(task.budget_ms, task.timeout_ms)
                )
                resolved: Any = await asyncio.wait_for(pending, timeout=effective_ms / 1000)
                if inspect.isawaitable(resolved):
                    resolved = await asyncio.wait_for(resolved, timeout=effective_ms / 1000)
                result = (
                    resolved
                    if isinstance(resolved, AgentTaskResult)
                    else self._failed_result(task, "executor returned an invalid result")
                )
            except TimeoutError:
                result = self._failed_result(task, f"executor timed out on attempt {attempt + 1}")
            except Exception as exc:  # noqa: BLE001 - executor failures must degrade safely
                result = self._failed_result(
                    task,
                    f"executor raised {type(exc).__name__} on attempt {attempt + 1}",
                )
            assert result is not None
            if result.task_id != task.task_id:
                result = self._failed_result(
                    task,
                    f"executor returned mismatched task_id {result.task_id}",
                )
            if result.status != "failed":
                break
        if result is None:
            return self._failed_result(task, "executor returned no result")
        return result

    @staticmethod
    def _evaluator_task(
        task: AgentTask,
        produced_instances: tuple[ComponentInstance, ...],
    ) -> _EvaluatorTask:
        payload = task.model_dump()
        payload["input_refs"] = (
            *task.input_refs,
            *(f"component_instance:{instance.instance_id}" for instance in produced_instances),
        )
        return _EvaluatorTask(
            **payload,
            produced_component_instances=produced_instances,
        )

    def run(
        self,
        graph: AgentTaskGraph,
        executors: Mapping[AgentTaskType, AgentExecutor],
        *,
        generation_run_id: str,
    ) -> OrchestratorRun:
        """Execute once, validate once, and expose failure only through a safe page.

        A failed Agent never leaks a partially trusted page. The evaluator sees
        the exact immutable component tuple, and the final PageSchema must pass
        the registry whitelist or become a registered text-only fallback
        (invariant #8).
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(graph, executors, generation_run_id=generation_run_id))
        raise RuntimeError("run() cannot be called from an event loop; await arun()")

    async def arun(
        self,
        graph: AgentTaskGraph,
        executors: Mapping[AgentTaskType, AgentExecutor],
        *,
        generation_run_id: str,
    ) -> OrchestratorRun:
        """Run once and emit bounded operational health telemetry."""
        started = time.monotonic()
        try:
            run = await self._arun(graph, executors, generation_run_id=generation_run_id)
        except Exception as exc:
            timed_out = isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
            record_product_event(
                self._event_sink,
                "courseware_orchestrator.run",
                {
                    "graph_id": graph.graph_id,
                    "generation_run_id": generation_run_id,
                    "attempt": 1,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "outcome": "timeout" if timed_out else "failed",
                    "timed_out": timed_out,
                    "fallback_used": False,
                    "degraded": False,
                },
            )
            raise

        timed_out = any("executor timed out" in result.notes for result in run.task_results)
        fallback_used = run.page.supersedes_page_id == f"{run.run_id}:page"
        record_product_event(
            self._event_sink,
            "courseware_orchestrator.run",
            {
                "run_id": run.run_id,
                "graph_id": run.graph_id,
                "generation_run_id": run.generation_run_id,
                "attempt": 1,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "outcome": run.status,
                "status": run.status,
                "timed_out": timed_out,
                "fallback_used": fallback_used,
                "degraded": run.status == "degraded",
            },
        )
        return run

    async def _arun(
        self,
        graph: AgentTaskGraph,
        executors: Mapping[AgentTaskType, AgentExecutor],
        *,
        generation_run_id: str,
    ) -> OrchestratorRun:
        """Asynchronously execute independent DAG tasks in stable layers."""
        from .run_store import stable_run_key

        graph.validate_graph()
        run_key = stable_run_key(graph)
        run_digest = hashlib.sha256(run_key.encode()).hexdigest()
        run_id = f"courseware_run_{run_digest[:24]}"
        with self._run_locks_guard:
            run_lock = self._run_locks.setdefault(run_id, Lock())

        # Serialize only identical deterministic runs. Unrelated graphs keep
        # their own locks and may execute concurrently, while a replay cannot
        # miss the cache and duplicate executor/LLM side effects.
        # The local lock retains the historical identity replay behavior. The
        # store claim additionally serializes check-execute-save across workers.
        await asyncio.to_thread(run_lock.acquire)
        try:
            if self._run_store is not None:
                claim = self._run_store.execution_lock(run_key)
                await asyncio.to_thread(claim.__enter__)
                try:
                    persisted = self._run_store.get_by_key(run_key)
                    if persisted is not None:
                        self._completed_runs[run_id] = persisted
                        return persisted
                    run = await self._run_locked(
                        graph,
                        executors,
                        generation_run_id=generation_run_id,
                        run_id=run_id,
                        run_key=run_key,
                    )
                    self._run_store.save(run)
                    return run
                finally:
                    await asyncio.to_thread(claim.__exit__, None, None, None)
            return await self._run_locked(
                graph,
                executors,
                generation_run_id=generation_run_id,
                run_id=run_id,
                run_key=run_key,
            )
        finally:
            run_lock.release()

    async def _run_locked(
        self,
        graph: AgentTaskGraph,
        executors: Mapping[AgentTaskType, AgentExecutor],
        *,
        generation_run_id: str,
        run_id: str,
        run_key: str,
    ) -> OrchestratorRun:
        """Execute or replay one run while its content-derived lock is held."""
        cached = self._completed_runs.get(run_id)
        if cached is not None:
            if cached.generation_run_id != generation_run_id:
                raise ValueError(
                    "deterministic run_id already belongs to a different generation_run_id"
                )
            return cached

        bundle = self._bundles.get((graph.graph_id, graph.prompt_bundle_id))
        if bundle is None:
            raise ValueError("graph was not planned by this orchestrator")
        if content_hash(bundle) != graph.prompt_bundle_hash:
            raise ValueError("graph prompt_bundle_hash does not match the planned bundle")

        execution_started = time.monotonic()
        task_index = {task.task_id: task for task in graph.tasks.values()}
        results_by_id: dict[str, AgentTaskResult] = {}
        task_results: list[AgentTaskResult] = []
        produced_instances: list[ComponentInstance] = []
        aborted_task_ids: set[str] = set()

        order = graph.topological_order()
        agentic_config = self._agentic_configs.get(graph.graph_id)
        agentic_policy = agentic_config[1] if agentic_config is not None else None
        concurrency = (
            agentic_policy.max_concurrent_agents if agentic_policy is not None else len(order)
        )
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def execute_bounded(task: AgentTask) -> AgentTaskResult:
            async with semaphore:
                return await self._execute(task, bundle, executors)

        def persist_checkpoint(
            task: AgentTask,
            *,
            checkpoint: AgentNodeCheckpoint,
            result: AgentTaskResult | None = None,
        ) -> None:
            if graph.version != "v2" or self._run_store is None:
                return
            from .run_store import AgenticTaskCheckpointRecord

            checkpoint_id = hashlib.sha256(f"{run_key}\x1f{task.task_id}".encode()).hexdigest()
            self._run_store.save_task_checkpoint(
                AgenticTaskCheckpointRecord(
                    checkpoint_id=f"checkpoint-{checkpoint_id[:24]}",
                    run_key=run_key,
                    generation_run_id=generation_run_id,
                    checkpoint=checkpoint,
                    result=result,
                )
            )

        if graph.version == "v2" and self._run_store is not None:
            for task_id, record in self._run_store.get_task_checkpoints(run_key).items():
                if task_id not in task_index or record.result is None:
                    continue
                if record.checkpoint.status not in {"completed", "degraded"}:
                    continue
                results_by_id[task_id] = record.result
                if (
                    record.result.status == "failed"
                    and task_index[task_id].failure_policy == "abort"
                ):
                    aborted_task_ids.add(task_id)

        remaining = set(order)
        while remaining:
            layer = [
                task_id
                for task_id in order
                if task_id in remaining
                and all(
                    dependency in results_by_id for dependency in task_index[task_id].depends_on
                )
            ]
            execution_tasks: list[tuple[str, AgentTask]] = []
            for task_id in layer:
                task = task_index[task_id]
                if task_id in results_by_id:
                    continue
                if any(dependency_id in aborted_task_ids for dependency_id in task.depends_on):
                    result = self._failed_result(task, "aborted because a dependency aborted")
                    aborted_task_ids.add(task_id)
                    results_by_id[task_id] = result
                    persist_checkpoint(
                        task,
                        checkpoint=AgentNodeCheckpoint(
                            task_id=task_id,
                            status="degraded",
                            degradation_code="dependency_aborted",
                        ),
                        result=result,
                    )
                else:
                    execution_task: AgentTask = task
                    if task.task_type in {"ui_composer", "evaluator"}:
                        execution_task = self._evaluator_task(task, tuple(produced_instances))
                    elif graph.version == "v2":
                        execution_task = _AgenticSpecialistTask(
                            **task.model_dump(),
                            dependency_material_outputs=_dependency_material_outputs(
                                task, task_index, results_by_id
                            ),
                        )
                    persist_checkpoint(
                        task,
                        checkpoint=AgentNodeCheckpoint(task_id=task_id, status="running"),
                    )
                    execution_tasks.append((task_id, execution_task))

            gathered = await asyncio.gather(*(execute_bounded(task) for _, task in execution_tasks))
            for (task_id, _), result in zip(execution_tasks, gathered, strict=True):
                task = task_index[task_id]
                degraded_dependencies = [
                    dependency_id
                    for dependency_id in task.depends_on
                    if results_by_id[dependency_id].status in {"failed", "degraded"}
                ]
                if degraded_dependencies and result.status == "succeeded":
                    result = result.model_copy(
                        update={
                            "status": "degraded",
                            "notes": f"degraded dependencies: {', '.join(degraded_dependencies)}",
                        }
                    )
                if result.status == "failed" and task.failure_policy == "abort":
                    aborted_task_ids.add(task_id)
                results_by_id[task_id] = result
                final_checkpoint = result.checkpoint or AgentNodeCheckpoint(
                    task_id=task_id,
                    status="completed" if result.status == "succeeded" else "degraded",
                    degradation_code=None if result.status == "succeeded" else "task_failed",
                )
                persist_checkpoint(task, checkpoint=final_checkpoint, result=result)

            for task_id in layer:
                task = task_index[task_id]
                result = results_by_id[task_id]
                task_results.append(result)
                if task.task_type != "evaluator" and result.status != "failed":
                    produced_instances.extend(result.produced_component_instances)
            remaining.difference_update(layer)

        from .evaluator import CoursewareEvaluator

        owners = {
            instance.instance_id: result.task_id
            for result in task_results
            for instance in result.produced_component_instances
        }
        verdict = CoursewareEvaluator().evaluate(
            tuple(produced_instances),
            bundle=bundle,
            registry=self._registry,
            task_owners=owners,
        )
        # A retry-policy producer gets one directed repair pass carrying the
        # concrete evaluator note. Other policies degrade at the release gate.

        def has_v2_repair_budget(task_id: str) -> bool:
            task = task_index[task_id]
            checkpoint = results_by_id[task_id].checkpoint
            return task.repair_budget > 0 and (
                checkpoint is None or checkpoint.repairs < task.repair_budget
            )

        repairable = [
            task_id
            for task_id in verdict.offending_task_ids
            if task_id in task_index
            and (
                has_v2_repair_budget(task_id)
                if graph.version == "v2"
                else task_index[task_id].failure_policy == "retry"
            )
        ]
        if verdict.status == "repair" and repairable:
            # v2's Evaluator may trigger one directed repair for the whole run.
            selected_repairs = repairable[:1] if graph.version == "v2" else repairable
            for task_id in selected_repairs:
                original = task_index[task_id]
                repair_task = original.model_copy(
                    update={
                        "input_refs": (
                            *original.input_refs,
                            f"evaluator_note:{verdict.repair_note}",
                        )
                    }
                )
                repair_execution_task: AgentTask = repair_task
                if graph.version == "v2" and repair_task.task_type not in {
                    "ui_composer",
                    "evaluator",
                }:
                    repair_execution_task = _AgenticSpecialistTask(
                        **repair_task.model_dump(),
                        dependency_material_outputs=_dependency_material_outputs(
                            repair_task, task_index, results_by_id
                        ),
                    )
                repaired = await execute_bounded(repair_execution_task)
                checkpoint = repaired.checkpoint or AgentNodeCheckpoint(
                    task_id=task_id,
                    status="completed" if repaired.status == "succeeded" else "degraded",
                )
                prior_checkpoint = results_by_id[task_id].checkpoint
                if prior_checkpoint is not None:
                    checkpoint = checkpoint.model_copy(
                        update={
                            "logical_llm_calls": (
                                prior_checkpoint.logical_llm_calls + checkpoint.logical_llm_calls
                            ),
                            "tool_calls": prior_checkpoint.tool_calls + checkpoint.tool_calls,
                            "output_tokens": prior_checkpoint.output_tokens
                            + checkpoint.output_tokens,
                            "gateway_receipts": (
                                *prior_checkpoint.gateway_receipts,
                                *checkpoint.gateway_receipts,
                            ),
                            "tool_receipts": (
                                *prior_checkpoint.tool_receipts,
                                *checkpoint.tool_receipts,
                            ),
                        }
                    )
                repaired = repaired.model_copy(
                    update={
                        "checkpoint": checkpoint.model_copy(
                            update={
                                "repairs": (prior_checkpoint.repairs if prior_checkpoint else 0) + 1
                            }
                        )
                    }
                )
                results_by_id[task_id] = repaired
                persist_checkpoint(
                    original,
                    checkpoint=cast(AgentNodeCheckpoint, repaired.checkpoint),
                    result=repaired,
                )
                task_results = [
                    repaired if item.task_id == task_id else item for item in task_results
                ]
            produced_instances = [
                instance
                for result in task_results
                if task_index[result.task_id].task_type != "evaluator" and result.status != "failed"
                for instance in result.produced_component_instances
            ]
            owners = {
                instance.instance_id: result.task_id
                for result in task_results
                for instance in result.produced_component_instances
            }
            verdict = CoursewareEvaluator().evaluate(
                tuple(produced_instances),
                bundle=bundle,
                registry=self._registry,
                task_owners=owners,
            )

        findings: list[str] = []
        findings.extend(verdict.findings)
        evaluator_result = results_by_id.get("evaluator")
        evaluator_released = bool(
            evaluator_result is not None
            and (
                evaluator_result.status == "succeeded"
                or (
                    evaluator_result.status == "degraded"
                    and evaluator_result.notes.startswith("degraded dependencies:")
                )
            )
        )
        if not evaluator_released:
            evaluator_status = (
                evaluator_result.status if evaluator_result is not None else "missing"
            )
            findings.append(f"evaluator status: {evaluator_status}")
            if evaluator_result is not None and evaluator_result.notes:
                findings.append(f"evaluator note: {evaluator_result.notes}")

        page_schema_id = f"{run_id}:page"
        candidate_page: PageSchema | None = None
        if produced_instances:
            try:
                candidate_page = PageSchema(
                    page_schema_id=page_schema_id,
                    generation_run_id=generation_run_id,
                    version="v1",
                    regions=[
                        PageRegion(
                            region_id=f"region-{index:02d}",
                            component=instance,
                        )
                        for index, instance in enumerate(produced_instances, start=1)
                    ],
                    published=False,
                    created_at=bundle.created_at,
                )
            except ValidationError as exc:
                findings.append(f"page construction failed: {exc}")
        else:
            findings.append("no component instances produced")

        validation_failed = candidate_page is None
        if candidate_page is not None:
            safe_page = safe_validate_or_degrade(
                candidate_page,
                generation_run_id=generation_run_id,
                created_at=bundle.created_at,
                registry=self._registry,
            )
            # safe_validate_or_degrade performs the sole candidate validation;
            # identity is retained only when it passed that gate.
            validation_failed = safe_page is not candidate_page
            if validation_failed:
                findings.append("page validation failed; emitted text-only fallback")
        else:
            safe_page = text_degrade_page(
                page_schema_id=page_schema_id,
                generation_run_id=generation_run_id,
                reason="orchestration_failed",
                created_at=bundle.created_at,
            )

        non_evaluator_failed = any(
            result.status == "failed"
            for result in task_results
            if task_index[result.task_id].task_type != "evaluator"
        )
        every_task_succeeded = all(result.status == "succeeded" for result in task_results)
        succeeded = (
            every_task_succeeded
            and verdict.status == "passed"
            and not findings
            and not validation_failed
        )

        if non_evaluator_failed or verdict.status != "passed" or not evaluator_released:
            safe_page = text_degrade_page(
                page_schema_id=page_schema_id,
                generation_run_id=generation_run_id,
                reason="orchestration_failed",
                created_at=bundle.created_at,
            )

        validate_page_schema(safe_page, registry=self._registry)
        run = OrchestratorRun(
            run_id=run_id,
            graph_id=graph.graph_id,
            generation_run_id=generation_run_id,
            task_results=tuple(task_results),
            evaluator_findings=tuple(findings),
            succeeded=succeeded,
            page=safe_page,
            run_key=run_key,
            status="succeeded" if succeeded else ("degraded" if safe_page else "failed"),
            prompt_bundle=bundle,
            task_graph=graph,
            input_refs=tuple(
                sorted(ref for task in graph.tasks.values() for ref in task.input_refs)
            ),
            evaluator_verdict=verdict,
            duration_ms=max(0, int((time.monotonic() - execution_started) * 1000)),
            roster_manifest=agentic_config[0] if agentic_config is not None else None,
            run_policy=agentic_policy,
            replan_count=self._agentic_replan_counts.get(graph.graph_id, 0),
        )
        self._completed_runs[run_id] = run
        return run
