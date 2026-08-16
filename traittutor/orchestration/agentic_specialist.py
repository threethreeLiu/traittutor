"""Independent Gateway Specialist loop with TOOL | FINAL | REPLAN protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from traittutor.components import ComponentInstance, ComponentRegistry
from traittutor.gateway import GatewayMessage, GatewayRequest, GatewayTool, get_gateway
from traittutor.multi_user.context import get_current_user
from traittutor.utils.json_parser import parse_json_response

from .agentic_contracts import (
    AgentNodeCheckpoint,
    AgentRosterManifest,
    CoursewareRunPolicy,
    CoursewareToolReceipt,
    GatewayReceiptSummary,
    MaterialContextOutput,
)
from .courseware_orchestrator import AgentTaskResult
from .courseware_tools import CoursewareToolRegistry
from .prompt_bundle import CoursewarePromptBundle
from .task_graph import AgentTask


class SpecialistBudgetExceeded(TimeoutError):
    """A visible policy budget was exhausted; no hidden retry is permitted."""


@dataclass
class CoursewareBudgetLedger:
    policy: CoursewareRunPolicy
    started_at_unix: float = field(default_factory=time.time)
    logical_llm_calls: int = 0
    tool_calls: int = 0
    output_tokens: int = 0
    reservation_prefix: str = "courseware"
    persist_reservation: Callable[[str, int, int, int, float], None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def remaining_seconds(self) -> float:
        return self.policy.deadline_seconds - (time.time() - self.started_at_unix)

    async def hydrate(
        self,
        *,
        logical_llm_calls: int,
        tool_calls: int,
        output_tokens: int,
        started_at_unix: float | None,
    ) -> None:
        """Restore durable run-wide usage without double-counting this process."""
        async with self._lock:
            self.logical_llm_calls = max(self.logical_llm_calls, logical_llm_calls)
            self.tool_calls = max(self.tool_calls, tool_calls)
            self.output_tokens = max(self.output_tokens, output_tokens)
            if started_at_unix is not None and started_at_unix > 0:
                self.started_at_unix = min(self.started_at_unix, started_at_unix)

    async def reserve_llm(self) -> str:
        async with self._lock:
            if self.remaining_seconds() <= 0:
                raise SpecialistBudgetExceeded("courseware deadline exhausted")
            if self.logical_llm_calls >= self.policy.max_logical_llm_calls:
                raise SpecialistBudgetExceeded("logical LLM call budget exhausted")
            if self.output_tokens >= self.policy.max_output_tokens:
                raise SpecialistBudgetExceeded("courseware output-token budget exhausted")
            reservation_id = f"{self.reservation_prefix}:llm:{self.logical_llm_calls + 1}"
            if self.persist_reservation is not None:
                self.persist_reservation(reservation_id, 1, 0, 0, self.started_at_unix)
            self.logical_llm_calls += 1
            return reservation_id

    async def reserve_tools(self, count: int) -> None:
        async with self._lock:
            if self.tool_calls + count > self.policy.max_tool_calls:
                raise SpecialistBudgetExceeded("courseware tool-call budget exhausted")
            reservation_id = (
                f"{self.reservation_prefix}:tools:{self.tool_calls + 1}-{self.tool_calls + count}"
            )
            if self.persist_reservation is not None:
                self.persist_reservation(reservation_id, 0, count, 0, self.started_at_unix)
            self.tool_calls += count

    async def record_output_tokens(self, count: int, *, reservation_id: str | None = None) -> None:
        async with self._lock:
            if self.output_tokens + count > self.policy.max_output_tokens:
                raise SpecialistBudgetExceeded("courseware output-token budget exhausted")
            if self.persist_reservation is not None and reservation_id is not None:
                self.persist_reservation(reservation_id, 1, 0, count, self.started_at_unix)
            self.output_tokens += count


class ComponentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    component_type: str
    props: dict[str, Any]


class SpecialistFinal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    components: tuple[ComponentDraft, ...] = Field(max_length=8)


def _private_output_is_safe(value: Any) -> bool:
    prohibited_keys = {"answer", "answers", "answer_key", "rubric", "solution", "correct_answer"}
    if isinstance(value, Mapping):
        if prohibited_keys.intersection(str(key).lower() for key in value):
            return False
        return all(_private_output_is_safe(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_private_output_is_safe(item) for item in value)
    if isinstance(value, str):
        normalized = value.lower()
        return not any(
            marker in normalized
            for marker in ("javascript:", "<script", "<iframe", "onerror=", "onclick=")
        )
    return True


def _parse_label(text: str) -> tuple[Literal["TOOL", "FINAL", "REPLAN"], str]:
    label, separator, body = text.strip().partition("\n")
    normalized = label.strip().upper()
    if normalized not in {"TOOL", "FINAL", "REPLAN"}:
        raise ValueError("Specialist must start with TOOL, FINAL, or REPLAN")
    return normalized, body.strip() if separator else ""  # type: ignore[return-value]


@dataclass
class AgenticSpecialistExecutor:
    roster: AgentRosterManifest
    tools: CoursewareToolRegistry
    policy: CoursewareRunPolicy
    budget: CoursewareBudgetLedger

    async def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        role = task.task_type
        if role not in {"material", "instruction", "practice", "srl", "visual"}:
            raise ValueError("Agentic Specialist cannot execute deterministic gate tasks")
        self.roster.require(role)
        messages: list[GatewayMessage] = [
            GatewayMessage(
                role="system",
                content=(
                    f"You are the isolated {role} Specialist for TraitTutor courseware. "
                    "Every response starts with exactly TOOL, FINAL, or REPLAN. Use TOOL only "
                    "with native tool calls. FINAL must contain one JSON object with components. "
                    "REPLAN contains a short machine-safe reason. Emit only registered component "
                    "drafts and never answers, rubrics, HTML, executable URLs, state writes, user "
                    "questions, memory, notes, code execution, or hidden tools. "
                    "Dependency material outputs are already injection-scanned. For each external "
                    "claim, include a concept_refs record containing claim and source_url."
                ),
            ),
            GatewayMessage(
                role="user",
                content=json.dumps(
                    {
                        "goal": bundle.teaching_goal,
                        "language": bundle.material_language,
                        "input_refs": task.input_refs,
                        "allowed_component_types": task.produces_component_types,
                        "component_schema": SpecialistFinal.model_json_schema(),
                        "dependency_material_outputs": [
                            item.model_dump(mode="json")
                            for item in getattr(task, "dependency_material_outputs", ())
                        ],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        ]
        gateway_receipts: list[GatewayReceiptSummary] = []
        tool_receipts: list[CoursewareToolReceipt] = []
        local_tool_calls = 0
        local_output_tokens = 0
        material_context_outputs: dict[str, MaterialContextOutput] = {}
        for iteration in range(task.iteration_budget):
            reservation_id = await self.budget.reserve_llm()
            response_text: list[str] = []
            calls = []
            receipt = None
            usage: Mapping[str, int] = {}
            request = GatewayRequest(
                prompt=str(messages[-1].content or ""),
                system_prompt=str(messages[0].content or ""),
                purpose=f"generate:courseware-specialist:{role}",
                messages=tuple(messages),
                user_id=get_current_user().id,
                tools=tuple(GatewayTool.from_mapping(item) for item in self.tools.schemas(role)),
                reasoning_effort="high",
                # TOOL and REPLAN are line protocols; forcing JSON here makes
                # native provider tool calls impossible on some backends.
                response_format=None,
                max_tokens=min(4000, self.policy.max_output_tokens - self.budget.output_tokens),
                max_retries=0,
                timeout_seconds=max(1.0, self.budget.remaining_seconds()),
            )
            async for event in get_gateway().stream(request):
                if event.type == "text" and event.text:
                    response_text.append(event.text)
                elif event.type == "tool_call" and event.tool_call is not None:
                    calls.append(event.tool_call)
                elif event.type == "usage" and event.usage:
                    usage = event.usage
                elif event.type == "cancelled":
                    raise asyncio.CancelledError
                elif event.type == "final":
                    receipt = event.receipt
            output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
            await self.budget.record_output_tokens(
                output_tokens,
                reservation_id=reservation_id,
            )
            local_output_tokens += output_tokens
            if receipt is not None:
                gateway_receipts.append(
                    GatewayReceiptSummary(
                        request_id=receipt.request_id,
                        purpose=receipt.purpose,
                        model=receipt.model,
                        provider=receipt.provider,
                        latency_ms=receipt.latency_ms,
                    )
                )
            text = "".join(response_text).strip()
            label, body = _parse_label(text or ("TOOL" if calls else ""))
            if calls:
                if label != "TOOL" or body:
                    raise ValueError("Specialist tool turn must contain only the TOOL label")
                if local_tool_calls + len(calls) > task.tool_budget:
                    raise SpecialistBudgetExceeded("Specialist task tool budget exhausted")
                await self.budget.reserve_tools(len(calls))
                local_tool_calls += len(calls)
                messages.append(
                    GatewayMessage(role="assistant", content="TOOL", tool_calls=tuple(calls))
                )
                for call in calls:
                    result, tool_receipt = await self.tools.dispatch(
                        role=role,
                        task_id=task.task_id,
                        tool_name=call.name,
                        arguments=call.arguments,
                    )
                    tool_receipts.append(tool_receipt)
                    if role == "material" and call.name == "fetch_external_source":
                        material_output = MaterialContextOutput.model_validate(result)
                        material_context_outputs[material_output.source_id] = material_output
                    messages.append(
                        GatewayMessage(
                            role="tool",
                            content=json.dumps(result, ensure_ascii=False, sort_keys=True),
                            tool_call_id=call.id,
                        )
                    )
                continue
            if label == "REPLAN":
                return AgentTaskResult(
                    task_id=task.task_id,
                    status="failed",
                    produced_component_instances=(),
                    notes="specialist_requested_replan",
                    replan_requested=True,
                    checkpoint=AgentNodeCheckpoint(
                        task_id=task.task_id,
                        status="replanning",
                        logical_llm_calls=iteration + 1,
                        tool_calls=local_tool_calls,
                        output_tokens=local_output_tokens,
                        gateway_receipts=tuple(gateway_receipts),
                        tool_receipts=tuple(tool_receipts),
                    ),
                    material_context_outputs=tuple(material_context_outputs.values()),
                )
            if label != "FINAL":
                raise ValueError("Specialist ended without FINAL")
            payload = parse_json_response(body, fallback={})
            final = SpecialistFinal.model_validate(payload)
            components: list[ComponentInstance] = []
            for index, draft in enumerate(final.components):
                if draft.component_type not in task.produces_component_types:
                    raise ValueError("Specialist emitted a component outside the task contract")
                spec = registry.require(draft.component_type)
                if any(not spec.allows_prop(key) for key in draft.props):
                    raise ValueError("Specialist emitted a non-whitelisted component field")
                if not _private_output_is_safe(draft.props):
                    raise ValueError("Specialist output contains answer or executable content")
                digest = hashlib.sha256(
                    f"{bundle.prompt_bundle_id}\x1f{task.task_id}\x1f{index}".encode()
                ).hexdigest()[:24]
                components.append(
                    ComponentInstance(
                        instance_id=f"agentic-{digest}",
                        component_type=spec.component_type,
                        version=spec.version,
                        props=draft.props,
                        modality_hint=spec.modality,
                    )
                )
            return AgentTaskResult(
                task_id=task.task_id,
                status="succeeded",
                produced_component_instances=tuple(components),
                checkpoint=AgentNodeCheckpoint(
                    task_id=task.task_id,
                    status="completed",
                    logical_llm_calls=iteration + 1,
                    tool_calls=local_tool_calls,
                    output_tokens=local_output_tokens,
                    gateway_receipts=tuple(gateway_receipts),
                    tool_receipts=tuple(tool_receipts),
                ),
                material_context_outputs=tuple(material_context_outputs.values()),
            )
        return AgentTaskResult(
            task_id=task.task_id,
            status="degraded",
            produced_component_instances=(),
            notes="specialist_iteration_budget_exhausted",
            checkpoint=AgentNodeCheckpoint(
                task_id=task.task_id,
                status="degraded",
                logical_llm_calls=task.iteration_budget,
                tool_calls=local_tool_calls,
                output_tokens=local_output_tokens,
                gateway_receipts=tuple(gateway_receipts),
                tool_receipts=tuple(tool_receipts),
                degradation_code="iteration_budget_exhausted",
            ),
            material_context_outputs=tuple(material_context_outputs.values()),
        )


__all__ = [
    "AgenticSpecialistExecutor",
    "CoursewareBudgetLedger",
    "SpecialistBudgetExceeded",
]
