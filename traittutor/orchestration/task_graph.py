"""Frozen task graph contracts for deterministic courseware execution."""

from __future__ import annotations

from datetime import UTC, datetime
import heapq
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AgentTaskType = Literal[
    "material",
    "instruction",
    "practice",
    "srl",
    "visual",
    "ui_composer",
    "evaluator",
]
AgentTaskStatus = Literal["pending", "running", "succeeded", "failed", "degraded"]
FailurePolicy = Literal["retry", "degrade", "abort"]


def _require_utc_iso(value: str) -> str:
    """Reject ambiguous graph timestamps because run replay is auditable."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("created_at must include a UTC offset")
    return value


class AgentTaskGraphError(ValueError):
    """Raised when a graph cannot be executed without ambiguous dependencies."""


class AgentTaskPrompt(BaseModel):
    """Frozen least-context prompt contract for one specialist Agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str = Field(min_length=1, max_length=128)
    task_type: AgentTaskType
    input_refs: tuple[str, ...]
    output_component_types: tuple[str, ...]
    constraints: tuple[str, ...] = (
        "read_only_learning_state",
        "server_held_answers",
        "registered_components_only",
    )


class AgentTask(BaseModel):
    """One immutable, least-context execution contract in the courseware DAG."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=64)
    task_type: AgentTaskType
    agent: str = Field(min_length=1)
    depends_on: tuple[str, ...]
    input_refs: tuple[str, ...]
    produces_component_types: tuple[str, ...]
    budget_ms: int = Field(ge=0)
    timeout_ms: int = Field(ge=0)
    # Deprecated read-only compatibility field for schema-v1 receipts. The
    # deterministic rollback planner always writes zero and the executor never
    # consumes this value. New retry behavior belongs only to Gateway routing,
    # explicit Specialist iterations, directed repair, or bounded replanning.
    max_retries: int = Field(default=0, ge=0)
    iteration_budget: int = Field(default=1, ge=1, le=4)
    tool_budget: int = Field(default=0, ge=0, le=6)
    repair_budget: int = Field(default=0, ge=0, le=1)
    failure_policy: FailurePolicy = "degrade"
    prompt: AgentTaskPrompt | None = None


class AgentTaskGraph(BaseModel):
    """Versioned DAG whose stable order makes execution and replay inspectable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_id: str = Field(min_length=1, max_length=96)
    prompt_bundle_id: str
    prompt_bundle_hash: str
    version: str = Field(min_length=1, max_length=16)
    tasks: dict[str, AgentTask] = Field(default_factory=dict)
    created_at: str

    _validate_created_at = field_validator("created_at")(_require_utc_iso)

    def _validated_task_index(self) -> dict[str, AgentTask]:
        """Build the canonical id index before dependency traversal."""
        index: dict[str, AgentTask] = {}
        for task in self.tasks.values():
            if task.task_id in index:
                raise AgentTaskGraphError(f"duplicate task_id: {task.task_id}")
            index[task.task_id] = task

        for task in index.values():
            if task.task_id in task.depends_on:
                raise AgentTaskGraphError(f"task {task.task_id} cannot depend on itself")
            for dependency_id in task.depends_on:
                if dependency_id not in index:
                    raise AgentTaskGraphError(
                        f"task {task.task_id} depends on missing task_id: {dependency_id}"
                    )
        return index

    def topological_order(self) -> list[str]:
        """Return a stable Kahn order so equal DAGs execute identically."""
        index = self._validated_task_index()
        indegree = {task_id: len(set(task.depends_on)) for task_id, task in index.items()}
        dependents: dict[str, list[str]] = {task_id: [] for task_id in index}
        for task in index.values():
            for dependency_id in set(task.depends_on):
                dependents[dependency_id].append(task.task_id)

        ready = [task_id for task_id, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        order: list[str] = []
        while ready:
            task_id = heapq.heappop(ready)
            order.append(task_id)
            for dependent_id in sorted(dependents[task_id]):
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    heapq.heappush(ready, dependent_id)

        if len(order) != len(index):
            cyclic_ids = sorted(task_id for task_id, degree in indegree.items() if degree > 0)
            raise AgentTaskGraphError(f"cycle detected among tasks: {', '.join(cyclic_ids)}")
        return order

    def validate_graph(self) -> None:
        """Fail before execution when a dependency cannot be resolved once."""
        if self.version == "v2" and any(task.max_retries for task in self.tasks.values()):
            raise AgentTaskGraphError("v2 tasks cannot declare executor retries")
        self.topological_order()
