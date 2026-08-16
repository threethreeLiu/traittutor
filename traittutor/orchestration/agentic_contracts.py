"""Strict v2 contracts for bounded courseware-only agent autonomy."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CoursewareAgentRole = Literal["material", "instruction", "practice", "srl", "visual"]
CoursewareToolName = Literal[
    "read_grounding_chunk",
    "search_frozen_material",
    "read_support_state",
    "read_component_contract",
    "search_external_sources",
    "fetch_external_source",
]
NodeCheckpointStatus = Literal[
    "planned",
    "running",
    "evaluating",
    "replanning",
    "completed",
    "degraded",
]


class AgentRosterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: CoursewareAgentRole
    allowed_tools: tuple[CoursewareToolName, ...]
    allowed_input_kinds: tuple[str, ...]
    allowed_component_types: tuple[str, ...]
    max_iterations: int = Field(default=3, ge=1, le=4)
    tool_budget: int = Field(default=3, ge=0, le=6)


class AgentRosterManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["courseware-agent-roster-v2"] = "courseware-agent-roster-v2"
    entries: tuple[AgentRosterEntry, ...]

    @model_validator(mode="after")
    def unique_roles(self) -> AgentRosterManifest:
        roles = [entry.role for entry in self.entries]
        if len(roles) != len(set(roles)):
            raise ValueError("agent roster roles must be unique")
        return self

    def require(self, role: CoursewareAgentRole) -> AgentRosterEntry:
        entry = next((item for item in self.entries if item.role == role), None)
        if entry is None:
            raise ValueError(f"courseware role is not registered: {role}")
        return entry


class PlannedAgentTask(BaseModel):
    """Planner-selected node; provider retries are deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,47}$")
    role: CoursewareAgentRole
    depends_on: tuple[str, ...] = ()
    input_refs: tuple[str, ...]
    output_component_types: tuple[str, ...]
    iteration_budget: int = Field(ge=1, le=4)
    tool_budget: int = Field(ge=0, le=6)
    repair_budget: Literal[0, 1] = 1


class AgentTaskGraphV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v2"] = "v2"
    planner_contract_version: Literal["agent-task-graph-v2"] = "agent-task-graph-v2"
    tasks: tuple[PlannedAgentTask, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_dag(self) -> AgentTaskGraphV2:
        # Runtime-reserved task ids: the orchestrator always installs its own
        # ui_composer/evaluator nodes after the planned tasks, so a planner
        # emitting them would be silently overwritten (lost work). Reject
        # loudly so the run falls back deterministically instead.
        reserved = {"ui_composer", "evaluator"}
        for task in self.tasks:
            if task.task_id in reserved:
                raise ValueError(f"planned task id {task.task_id!r} is reserved by the runtime")
        index = {task.task_id: task for task in self.tasks}
        if len(index) != len(self.tasks):
            raise ValueError("planned task ids must be unique")
        visited: set[str] = set()
        active: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in active:
                raise ValueError("planned task graph contains a cycle")
            if task_id in visited:
                return
            active.add(task_id)
            for dependency in index[task_id].depends_on:
                if dependency not in index:
                    raise ValueError("planned task depends on an unknown task")
                visit(dependency)
            active.remove(task_id)
            visited.add(task_id)

        for task_id in index:
            visit(task_id)
        return self


class CoursewareRunPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["courseware-run-policy-v1"] = "courseware-run-policy-v1"
    max_generation_tasks: Literal[6] = 6
    max_concurrent_agents: Literal[3] = 3
    max_logical_llm_calls: Literal[12] = 12
    max_tool_calls: Literal[12] = 12
    max_output_tokens: Literal[24000] = 24000
    max_replans: Literal[1] = 1
    max_repairs_per_task: Literal[1] = 1
    deadline_seconds: Literal[300] = 300


class GatewayReceiptSummary(BaseModel):
    """Redacted receipt fields safe for private run accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    purpose: str
    model: str
    provider: str
    latency_ms: int = Field(ge=0)


class CoursewareToolReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str
    task_id: str
    tool_category: Literal["grounding", "material_search", "support", "contract", "external"]
    succeeded: bool


class MaterialContextOutput(BaseModel):
    """Private, injection-scanned Material output for dependent Specialists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=160)
    trust: Literal["external_untrusted_reference"]
    text: str = Field(min_length=1, max_length=6000)
    source_url: str | None = Field(default=None, max_length=2048)


class AgentNodeCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    status: NodeCheckpointStatus
    logical_llm_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    repairs: int = Field(default=0, ge=0, le=1)
    gateway_receipts: tuple[GatewayReceiptSummary, ...] = ()
    tool_receipts: tuple[CoursewareToolReceipt, ...] = ()
    degradation_code: str | None = Field(default=None, max_length=96)


def default_agent_roster() -> AgentRosterManifest:
    common_material: tuple[CoursewareToolName, ...] = (
        "read_grounding_chunk",
        "search_frozen_material",
    )
    return AgentRosterManifest(
        entries=(
            AgentRosterEntry(
                role="material",
                allowed_tools=(
                    *common_material,
                    "read_component_contract",
                    "search_external_sources",
                    "fetch_external_source",
                ),
                allowed_input_kinds=("prompt_bundle", "grounding"),
                allowed_component_types=(),
            ),
            AgentRosterEntry(
                role="instruction",
                allowed_tools=(
                    *common_material,
                    "read_support_state",
                    "read_component_contract",
                ),
                allowed_input_kinds=("prompt_bundle", "grounding", "support", "component"),
                allowed_component_types=(
                    "concept_explanation",
                    "worked_example",
                    "audio_explanation",
                ),
            ),
            AgentRosterEntry(
                role="practice",
                allowed_tools=(
                    *common_material,
                    "read_support_state",
                    "read_component_contract",
                ),
                allowed_input_kinds=("prompt_bundle", "grounding", "support", "component"),
                allowed_component_types=(
                    "diagnostic_check",
                    "guided_practice",
                    "calibration_checkpoint",
                    "retrieval_card",
                    "transfer_challenge",
                ),
            ),
            AgentRosterEntry(
                role="srl",
                allowed_tools=("read_support_state", "read_component_contract"),
                allowed_input_kinds=("prompt_bundle", "support", "component"),
                allowed_component_types=(
                    "goal_map",
                    "progress_checkpoint",
                    "reflection_prompt",
                    "review_queue",
                ),
            ),
            AgentRosterEntry(
                role="visual",
                allowed_tools=(*common_material, "read_component_contract"),
                allowed_input_kinds=("prompt_bundle", "grounding", "component"),
                allowed_component_types=("visual_map", "video_explanation"),
            ),
        )
    )


__all__ = [
    "AgentNodeCheckpoint",
    "AgentRosterEntry",
    "AgentRosterManifest",
    "AgentTaskGraphV2",
    "CoursewareAgentRole",
    "CoursewareRunPolicy",
    "CoursewareToolName",
    "CoursewareToolReceipt",
    "GatewayReceiptSummary",
    "MaterialContextOutput",
    "NodeCheckpointStatus",
    "PlannedAgentTask",
    "default_agent_roster",
]
