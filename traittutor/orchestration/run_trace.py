"""Learner-safe read projection for persisted GenerationRun receipts."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .courseware_orchestrator import AgentTaskResult, OrchestratorRun
from .run_store import AgenticBudgetUsage, OrchestratorRunStore
from .task_graph import AgentTask, AgentTaskStatus, AgentTaskType

TraceAvailability = Literal["available", "unavailable"]
ValidationStatus = Literal["passed", "repair", "degraded", "failed", "unavailable"]
FailureCode = Literal[
    "dependency_failed",
    "executor_timeout",
    "executor_unavailable",
    "task_failed",
]

_SAFE_REF_KINDS = frozenset(
    {
        "agent_roster",
        "bkt_model",
        "component_catalog",
        "context_snapshot",
        "grounding",
        "interaction",
        "kc_mapping",
        "learner_profile_snapshot",
        "output_contract",
        "persona_contract",
        "prompt_bundle",
        "quality_contract",
        "subject_state_snapshot",
    }
)
_SAFE_REF_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_PRIVATE_REF_MARKERS = (
    "answer",
    "chain_of_thought",
    "prompt",
    "reasoning",
    "rubric",
    "tool",
)


class LearnerSafeInputRef(BaseModel):
    """An allowlisted opaque identity, never an input body or tool argument."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    ref_id: str


class LearnerSafeTaskTrace(BaseModel):
    """Public status for one DAG node with closed failure semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    task_type: AgentTaskType
    status: AgentTaskStatus
    depends_on: tuple[str, ...]
    input_refs: tuple[LearnerSafeInputRef, ...]
    redacted_input_ref_count: int
    failure_code: FailureCode | None = None
    degradation_codes: tuple[Literal["task_degraded"], ...] = ()
    tool_categories: tuple[str, ...] = ()


class LearnerSafeRunBudget(BaseModel):
    """Aggregated planned limits plus persisted wall-clock execution time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_planned_budget_ms: int | None
    total_timeout_ms: int | None
    total_retry_limit: int | None
    elapsed_ms: int | None
    timing_status: TraceAvailability
    logical_llm_calls: int = 0
    tool_calls: int = 0
    output_tokens: int = 0
    repairs: int = 0
    replans: int = 0
    logical_llm_call_limit: int | None = None
    tool_call_limit: int | None = None
    output_token_limit: int | None = None


class LearnerSafeValidationTrace(BaseModel):
    """Closed validation result that never returns evaluator prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ValidationStatus
    finding_count: int
    category_codes: tuple[str, ...]
    offending_task_ids: tuple[str, ...]


class LearnerSafeRunTrace(BaseModel):
    """Task-oriented HTTP contract for Why/Research/Learning consumers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    generation_run_id: str
    graph_id: str
    status: Literal["succeeded", "degraded", "failed"]
    graph_status: TraceAvailability
    graph_version: str | None
    created_at: str | None
    page_schema_id: str
    nodes: tuple[LearnerSafeTaskTrace, ...]
    budget: LearnerSafeRunBudget
    validation: LearnerSafeValidationTrace
    degradation_codes: tuple[str, ...]


class GenerationRunTraceNotFound(LookupError):
    """No generation receipt exists in the authorized owner's store."""


def _safe_refs(refs: tuple[str, ...]) -> tuple[tuple[LearnerSafeInputRef, ...], int]:
    public: list[LearnerSafeInputRef] = []
    redacted = 0
    for ref in refs:
        kind, separator, ref_id = ref.partition(":")
        normalized_id = ref_id.lower()
        if (
            not separator
            or kind not in _SAFE_REF_KINDS
            or _SAFE_REF_ID.fullmatch(ref_id) is None
            or any(marker in normalized_id for marker in _PRIVATE_REF_MARKERS)
        ):
            redacted += 1
            continue
        public.append(LearnerSafeInputRef(kind=kind, ref_id=ref_id))
    return tuple(public), redacted


def _failure_code(result: AgentTaskResult | None) -> FailureCode | None:
    if result is None or result.status != "failed":
        return None
    note = result.notes.lower()
    if "timed out" in note or "timeout" in note:
        return "executor_timeout"
    if "dependency" in note and "abort" in note:
        return "dependency_failed"
    if "missing executor" in note or "unavailable" in note:
        return "executor_unavailable"
    return "task_failed"


def _task_trace(task: AgentTask, result: AgentTaskResult | None) -> LearnerSafeTaskTrace:
    refs, redacted = _safe_refs(task.input_refs)
    status: AgentTaskStatus = result.status if result is not None else "pending"
    return LearnerSafeTaskTrace(
        task_id=task.task_id,
        task_type=task.task_type,
        status=status,
        depends_on=task.depends_on,
        input_refs=refs,
        redacted_input_ref_count=redacted,
        failure_code=_failure_code(result),
        degradation_codes=("task_degraded",) if status == "degraded" else (),
        tool_categories=tuple(
            sorted(
                {
                    receipt.tool_category
                    for receipt in (
                        result.checkpoint.tool_receipts
                        if result is not None and result.checkpoint is not None
                        else ()
                    )
                }
            )
        ),
    )


def _validation_category(finding: str) -> str:
    normalized = finding.lower()
    if "schema" in normalized or "unregistered" in normalized:
        return "component_schema"
    if "source_url" in normalized or "external claim" in normalized:
        return "source_attribution"
    if "version" in normalized:
        return "concept_version"
    if "language" in normalized:
        return "language_constraint"
    if "evaluator" in normalized:
        return "evaluator_unavailable"
    return "validation_failed"


def _validation_trace(run: OrchestratorRun) -> LearnerSafeValidationTrace:
    verdict = run.evaluator_verdict
    if verdict is None:
        return LearnerSafeValidationTrace(
            status="unavailable",
            finding_count=len(run.evaluator_findings),
            category_codes=tuple(
                sorted({_validation_category(item) for item in run.evaluator_findings})
            ),
            offending_task_ids=(),
        )
    graph_task_ids = set(run.task_graph.tasks) if run.task_graph is not None else set()
    return LearnerSafeValidationTrace(
        status=verdict.status,
        finding_count=len(verdict.findings),
        category_codes=tuple(sorted({_validation_category(item) for item in verdict.findings})),
        offending_task_ids=tuple(
            sorted(task_id for task_id in verdict.offending_task_ids if task_id in graph_task_ids)
        ),
    )


def project_learner_safe_run_trace(
    run: OrchestratorRun,
    *,
    budget_usage: AgenticBudgetUsage | None = None,
) -> LearnerSafeRunTrace:
    """Project a persisted receipt without serializing any internal run model."""
    graph = run.task_graph
    results = {result.task_id: result for result in run.task_results}
    if graph is None:
        nodes: tuple[LearnerSafeTaskTrace, ...] = ()
        graph_status: TraceAvailability = "unavailable"
        graph_version = None
        created_at = None
        planned_budget = None
        planned_timeout = None
        retry_limit = None
    else:
        nodes = tuple(
            _task_trace(graph.tasks[task_id], results.get(task_id))
            for task_id in graph.topological_order()
        )
        graph_status = "available"
        graph_version = graph.version
        created_at = graph.created_at
        planned_budget = sum(task.budget_ms for task in graph.tasks.values())
        planned_timeout = sum(task.timeout_ms for task in graph.tasks.values())
        retry_limit = (
            None
            if graph.version == "v2"
            else sum(task.max_retries for task in graph.tasks.values())
        )

    checkpoints = [
        result.checkpoint for result in run.task_results if result.checkpoint is not None
    ]
    logical_llm_calls = (
        budget_usage.logical_llm_calls
        if budget_usage is not None
        else sum(item.logical_llm_calls for item in checkpoints)
    )
    tool_calls = (
        budget_usage.tool_calls
        if budget_usage is not None
        else sum(item.tool_calls for item in checkpoints)
    )
    output_tokens = (
        budget_usage.output_tokens
        if budget_usage is not None
        else sum(item.output_tokens for item in checkpoints)
    )

    validation = _validation_trace(run)
    degradation_codes: set[str] = set()
    if run.status == "degraded":
        degradation_codes.add("run_degraded")
    if any(result.status == "degraded" for result in run.task_results):
        degradation_codes.add("task_degraded")
    if any(result.status == "failed" for result in run.task_results):
        degradation_codes.add("task_failed")
    if validation.status not in {"passed", "unavailable"}:
        degradation_codes.add("validation_not_passed")

    return LearnerSafeRunTrace(
        run_id=run.run_id,
        generation_run_id=run.generation_run_id,
        graph_id=run.graph_id,
        status=run.status,
        graph_status=graph_status,
        graph_version=graph_version,
        created_at=created_at,
        page_schema_id=run.page.page_schema_id,
        nodes=nodes,
        budget=LearnerSafeRunBudget(
            total_planned_budget_ms=planned_budget,
            total_timeout_ms=planned_timeout,
            total_retry_limit=retry_limit,
            elapsed_ms=run.duration_ms,
            timing_status="available" if run.duration_ms is not None else "unavailable",
            logical_llm_calls=logical_llm_calls,
            tool_calls=tool_calls,
            output_tokens=output_tokens,
            repairs=sum(item.repairs for item in checkpoints),
            replans=run.replan_count,
            logical_llm_call_limit=(
                run.run_policy.max_logical_llm_calls if run.run_policy is not None else None
            ),
            tool_call_limit=(run.run_policy.max_tool_calls if run.run_policy is not None else None),
            output_token_limit=(
                run.run_policy.max_output_tokens if run.run_policy is not None else None
            ),
        ),
        validation=validation,
        degradation_codes=tuple(sorted(degradation_codes)),
    )


class LearnerSafeRunTraceService:
    """Read traces only from the already-authorized owner's durable store."""

    def __init__(self, store: OrchestratorRunStore) -> None:
        self._store = store

    def get(self, generation_run_id: str) -> LearnerSafeRunTrace:
        run = self._store.get_by_generation_run_id(generation_run_id)
        if run is None:
            raise GenerationRunTraceNotFound(generation_run_id)
        return project_learner_safe_run_trace(
            run,
            budget_usage=self._store.get_agentic_budget_usage(generation_run_id),
        )


__all__ = [
    "GenerationRunTraceNotFound",
    "LearnerSafeInputRef",
    "LearnerSafeRunBudget",
    "LearnerSafeRunTrace",
    "LearnerSafeRunTraceService",
    "LearnerSafeTaskTrace",
    "LearnerSafeValidationTrace",
    "project_learner_safe_run_trace",
]
