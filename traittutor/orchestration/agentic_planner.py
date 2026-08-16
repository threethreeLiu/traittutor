"""Gateway-backed Planner for strict AgentTaskGraph v2 generation."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import TYPE_CHECKING, Any

from traittutor.gateway import GatewayMessage, GatewayRequest, get_gateway
from traittutor.generate.runner import CompletionFn
from traittutor.multi_user.context import get_current_user
from traittutor.utils.json_parser import parse_json_response

from .agentic_contracts import (
    AgentRosterManifest,
    AgentTaskGraphV2,
    CoursewareRunPolicy,
    default_agent_roster,
)
from .prompt_bundle import CoursewarePromptBundle

if TYPE_CHECKING:
    from .agentic_specialist import CoursewareBudgetLedger

PLANNER_CONTRACT_VERSION = "agent-task-graph-v2"


class AgenticPlannerContractError(ValueError):
    """The Planner output escaped roster, reference, output, or budget constraints."""


class AgenticCoursewarePlanner:
    def __init__(self, *, completion: CompletionFn | None = None) -> None:
        self._completion = completion

    async def plan(
        self,
        bundle: CoursewarePromptBundle,
        *,
        roster: AgentRosterManifest | None = None,
        policy: CoursewareRunPolicy | None = None,
        replan_reason_codes: tuple[str, ...] = (),
        budget: CoursewareBudgetLedger | None = None,
    ) -> AgentTaskGraphV2:
        roster = roster or default_agent_roster()
        policy = policy or CoursewareRunPolicy()
        prompt_payload = {
            "contract": PLANNER_CONTRACT_VERSION,
            "goal": bundle.teaching_goal,
            "material_language": bundle.material_language,
            "requested_component_types": bundle.requested_component_types,
            "roles": [
                {
                    "role": entry.role,
                    "allowed_tools": entry.allowed_tools,
                    "allowed_component_types": entry.allowed_component_types,
                    "input_refs": bundle.task_input_refs(entry.role),
                    "max_iterations": entry.max_iterations,
                    "tool_budget": entry.tool_budget,
                }
                for entry in roster.entries
            ],
            "policy": policy.model_dump(mode="json"),
            "replan_reason_codes": replan_reason_codes,
        }
        encoded = json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True)
        schema = AgentTaskGraphV2.model_json_schema()
        system_prompt = (
            "You are TraitTutor's bounded courseware Planner. Select a small acyclic graph "
            "only from the supplied roles, references, components, and budgets. Do not add UI "
            "composition or evaluation tasks; deterministic code owns both gates. Never plan "
            "state writes, user questions, code execution, memory, notes, or dynamic tools. "
            f"Return exactly one JSON object matching this schema: {schema}"
        )

        def validate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            graph = AgentTaskGraphV2.model_validate(payload)
            if len(graph.tasks) > policy.max_generation_tasks:
                raise AgenticPlannerContractError("Planner exceeded the generation-task budget")
            requested = frozenset(bundle.requested_component_types)
            producing = 0
            for task in graph.tasks:
                entry = roster.require(task.role)
                allowed_refs = frozenset(bundle.task_input_refs(task.role))
                if not set(task.input_refs).issubset(allowed_refs):
                    raise AgenticPlannerContractError(
                        "Planner emitted an unresolvable input reference"
                    )
                allowed_outputs = frozenset(entry.allowed_component_types)
                if not set(task.output_component_types).issubset(allowed_outputs):
                    raise AgenticPlannerContractError("Planner escaped the role component contract")
                if requested and not set(task.output_component_types).issubset(requested):
                    raise AgenticPlannerContractError(
                        "Planner emitted an unrequested component type"
                    )
                if task.iteration_budget > entry.max_iterations:
                    raise AgenticPlannerContractError("Planner exceeded a role iteration budget")
                if task.tool_budget > entry.tool_budget:
                    raise AgenticPlannerContractError("Planner exceeded a role tool budget")
                producing += int(bool(task.output_component_types))
            if producing == 0:
                raise AgenticPlannerContractError(
                    "Planner graph must contain a component-producing task"
                )
            return graph.model_dump(mode="json")

        reservation_id = await budget.reserve_llm() if budget is not None else None
        if self._completion is not None:
            raw = await self._completion(
                encoded,
                system_prompt=system_prompt,
                reasoning_effort="high",
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=3000,
            )
        else:
            response = await get_gateway().complete(
                GatewayRequest(
                    prompt=encoded,
                    system_prompt=system_prompt,
                    purpose="generate:courseware-agentic-planner-v2",
                    messages=(
                        GatewayMessage(role="system", content=system_prompt),
                        GatewayMessage(role="user", content=encoded),
                    ),
                    user_id=get_current_user().id,
                    reasoning_effort="high",
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=3000,
                    max_retries=0,
                    timeout_seconds=60,
                    metadata={
                        "contract_signature": hashlib.sha256(
                            f"{PLANNER_CONTRACT_VERSION}\x1f{encoded}".encode()
                        ).hexdigest()[:16]
                    },
                )
            )
            raw = response.content
            if budget is not None:
                await budget.record_output_tokens(
                    int(
                        response.usage.get("output_tokens")
                        or response.usage.get("completion_tokens")
                        or 0
                    ),
                    reservation_id=reservation_id,
                )
        payload = parse_json_response(raw, fallback={})
        return AgentTaskGraphV2.model_validate(validate(payload))


__all__ = [
    "AgenticCoursewarePlanner",
    "AgenticPlannerContractError",
    "PLANNER_CONTRACT_VERSION",
]
