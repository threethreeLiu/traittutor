"""Closed courseware tool domain; it never falls back to the global registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import inspect
import re
from typing import Any, TypeAlias

from traittutor.components import ComponentRegistry
from traittutor.security.prompt_guard import enforce_prompt_guard

from .agentic_contracts import (
    AgentRosterManifest,
    CoursewareAgentRole,
    CoursewareToolName,
    CoursewareToolReceipt,
)

ExternalSearch: TypeAlias = Callable[
    [str], Sequence[Mapping[str, Any]] | Awaitable[Sequence[Mapping[str, Any]]]
]
ExternalFetch: TypeAlias = Callable[[str], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]

_TOOL_CATEGORY = {
    "read_grounding_chunk": "grounding",
    "search_frozen_material": "material_search",
    "read_support_state": "support",
    "read_component_contract": "contract",
    "search_external_sources": "external",
    "fetch_external_source": "external",
}


class CoursewareToolDenied(PermissionError):
    """A Specialist attempted to leave its explicit courseware capability set."""


@dataclass
class CoursewareToolContext:
    chunks: tuple[Mapping[str, Any], ...]
    support_state: Mapping[str, Any]
    component_registry: ComponentRegistry
    external_augmentation_allowed: bool = False
    external_search: ExternalSearch | None = None
    external_fetch: ExternalFetch | None = None
    external_sources: dict[str, Mapping[str, Any]] = field(default_factory=dict)


class CoursewareToolRegistry:
    """Role-scoped read tools with no dynamic registration or write surface."""

    def __init__(self, *, roster: AgentRosterManifest, context: CoursewareToolContext) -> None:
        self._roster = roster
        self._context = context

    def schemas(self, role: CoursewareAgentRole) -> tuple[dict[str, Any], ...]:
        entry = self._roster.require(role)
        schemas = {
            "read_grounding_chunk": {
                "name": "read_grounding_chunk",
                "description": "Read one exact chunk from the frozen course material.",
                "parameters": {
                    "type": "object",
                    "properties": {"chunk_id": {"type": "string"}},
                    "required": ["chunk_id"],
                    "additionalProperties": False,
                },
            },
            "search_frozen_material": {
                "name": "search_frozen_material",
                "description": "Search only the frozen material snapshot.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            "read_support_state": {
                "name": "read_support_state",
                "description": "Read qualitative support states; no BKT probability is available.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            "read_component_contract": {
                "name": "read_component_contract",
                "description": "Read allowed fields for one registered PageSchema component.",
                "parameters": {
                    "type": "object",
                    "properties": {"component_type": {"type": "string"}},
                    "required": ["component_type"],
                    "additionalProperties": False,
                },
            },
            "search_external_sources": {
                "name": "search_external_sources",
                "description": "Material Agent only: search when external augmentation was pre-authorized.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            "fetch_external_source": {
                "name": "fetch_external_source",
                "description": "Material Agent only: fetch a source returned by the scoped search.",
                "parameters": {
                    "type": "object",
                    "properties": {"source_id": {"type": "string"}},
                    "required": ["source_id"],
                    "additionalProperties": False,
                },
            },
        }
        return tuple(schemas[name] for name in entry.allowed_tools)

    async def dispatch(
        self,
        *,
        role: CoursewareAgentRole,
        task_id: str,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> tuple[dict[str, Any], CoursewareToolReceipt]:
        entry = self._roster.require(role)
        if tool_name not in entry.allowed_tools:
            raise CoursewareToolDenied(f"{role} is not allowed to call {tool_name}")
        name: CoursewareToolName = tool_name  # type: ignore[assignment]
        result = await self._execute(role=role, name=name, arguments=arguments)
        receipt_material = f"{task_id}\x1f{name}\x1f{len(result)}"
        receipt = CoursewareToolReceipt(
            receipt_id=f"tool-{hashlib.sha256(receipt_material.encode()).hexdigest()[:24]}",
            task_id=task_id,
            tool_category=_TOOL_CATEGORY[name],  # type: ignore[arg-type]
            succeeded=True,
        )
        return result, receipt

    async def _execute(
        self,
        *,
        role: CoursewareAgentRole,
        name: CoursewareToolName,
        arguments: Mapping[str, Any],
    ) -> dict[str, Any]:
        if name == "read_grounding_chunk":
            chunk_id = str(arguments.get("chunk_id") or "").strip()
            chunk = next(
                (item for item in self._context.chunks if str(item.get("chunk_id")) == chunk_id),
                None,
            )
            if chunk is None:
                raise KeyError("unknown frozen grounding chunk")
            text = str(chunk.get("text") or "")[:6000]
            enforce_prompt_guard(text)
            return {
                "chunk_id": chunk_id,
                "source_id": str(chunk.get("source_id") or ""),
                "text": text,
            }
        if name == "search_frozen_material":
            query = str(arguments.get("query") or "").strip()[:240]
            enforce_prompt_guard(query)
            tokens = {item.lower() for item in re.findall(r"[\w\u3400-\u9fff]+", query)}
            ranked: list[tuple[int, Mapping[str, Any]]] = []
            for chunk in self._context.chunks:
                text = str(chunk.get("text") or "")
                score = sum(text.lower().count(token) for token in tokens)
                if score:
                    ranked.append((score, chunk))
            matches: list[dict[str, str]] = []
            for _, chunk in sorted(
                ranked, key=lambda item: (-item[0], str(item[1].get("chunk_id")))
            )[:5]:
                excerpt = str(chunk.get("text") or "")[:800]
                # Same trust boundary as read_grounding_chunk: material text
                # is learner-uploaded and must not smuggle instructions into
                # the model through the search tool.
                enforce_prompt_guard(excerpt)
                matches.append(
                    {
                        "chunk_id": str(chunk.get("chunk_id") or ""),
                        "source_id": str(chunk.get("source_id") or ""),
                        "excerpt": excerpt,
                    }
                )
            return {
                "matches": matches,
            }
        if name == "read_support_state":
            allowed = {
                "evidence_state",
                "change_signal",
                "verified_observation_count",
                "model_version",
                "stage_policy_version",
            }
            return {
                "support": {
                    str(key): {
                        field: value for field, value in dict(item).items() if field in allowed
                    }
                    for key, item in self._context.support_state.items()
                    if isinstance(item, Mapping)
                }
            }
        if name == "read_component_contract":
            component_type = str(arguments.get("component_type") or "").strip()
            if component_type not in entry_component_types(self._roster, role):
                raise CoursewareToolDenied("role cannot read an output contract it cannot emit")
            spec = self._context.component_registry.require(component_type)
            return {
                "component_type": spec.component_type,
                "version": spec.version,
                "allowed_props": list(spec.allowed_props),
                "allowed_actions": list(spec.allowed_actions),
                "server_held_answers": spec.answer_policy == "server_held",
            }
        if role != "material" or not self._context.external_augmentation_allowed:
            raise CoursewareToolDenied("external augmentation is not authorized for this run")
        if name == "search_external_sources":
            if self._context.external_search is None:
                raise CoursewareToolDenied("external search adapter is unavailable")
            query = str(arguments.get("query") or "").strip()[:240]
            enforce_prompt_guard(query)
            search_value = self._context.external_search(query)
            sources = await search_value if inspect.isawaitable(search_value) else search_value
            public: list[dict[str, str]] = []
            for item in list(sources)[:4]:
                source_id = str(item.get("source_id") or "").strip()
                if not source_id:
                    continue
                self._context.external_sources[source_id] = dict(item)
                public.append({"source_id": source_id, "title": str(item.get("title") or "")[:180]})
            return {"sources": public}
        if self._context.external_fetch is None:
            raise CoursewareToolDenied("external fetch adapter is unavailable")
        source_id = str(arguments.get("source_id") or "").strip()
        if source_id not in self._context.external_sources:
            raise CoursewareToolDenied("source id was not returned by this run's search")
        fetch_value = self._context.external_fetch(source_id)
        fetched = await fetch_value if inspect.isawaitable(fetch_value) else fetch_value
        text = str(fetched.get("text") or "")[:6000]
        enforce_prompt_guard(text)
        source_url = str(self._context.external_sources[source_id].get("url") or "").strip()
        if source_url and not source_url.startswith(("http://", "https://")):
            raise CoursewareToolDenied("external source URL must be http(s)")
        return {
            "source_id": source_id,
            "trust": "external_untrusted_reference",
            "text": text,
            "source_url": source_url or None,
        }


def entry_component_types(roster: AgentRosterManifest, role: CoursewareAgentRole) -> frozenset[str]:
    return frozenset(roster.require(role).allowed_component_types)


__all__ = [
    "CoursewareToolContext",
    "CoursewareToolDenied",
    "CoursewareToolRegistry",
    "ExternalFetch",
    "ExternalSearch",
]
