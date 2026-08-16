"""Agentic chat capability."""

from __future__ import annotations

from typing import Any, cast

from traittutor.agents.chat.agentic_pipeline import CHAT_OPTIONAL_TOOLS, AgenticChatPipeline
from traittutor.agents.chat.learning_support import (
    build_learning_support_sources,
    context_snapshot_contract,
    learning_support_blocks,
    tutor_expression_contract,
)
from traittutor.agents.chat.policy import policy_preflight_contract, preflight
from traittutor.capabilities.protocol import PromptBlock
from traittutor.context_assembler.snapshot import AssistantContextSnapshot
from traittutor.core.capability_protocol import BaseCapability, CapabilityManifest
from traittutor.core.context import UnifiedContext
from traittutor.core.stream_bus import StreamBus
from traittutor.runtime.request_contracts import get_capability_request_schema

# Metadata key for extra prompt blocks injected by the capability before the
# pipeline builds its system prompt. The agentic pipeline merges these into
# ``_capability_system_blocks`` so they sit in the same prompt region as other
# capability playbooks.
_EXTRA_CAPABILITY_BLOCKS = "_extra_capability_blocks"


class ChatCapability(BaseCapability):
    manifest = CapabilityManifest(
        name="chat",
        description=(
            "Agentic chat: an exploring agent loop with tools, followed by "
            "a respond stage that streams the answer."
        ),
        stages=["exploring", "responding"],
        tools_used=CHAT_OPTIONAL_TOOLS,
        cli_aliases=["chat"],
        request_schema=get_capability_request_schema("chat"),
    )

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        # Policy evaluates the learner's request, never the attached source
        # text that _apply_learning_support appends afterwards.
        _apply_preflight(context)
        _apply_learning_support(context)
        pipeline = AgenticChatPipeline(language=context.language)
        await pipeline.run(context, stream)


def _apply_learning_support(context: UnifiedContext) -> None:
    """Inject the read-only Learning Canvas Q&A contracts into the context.

    When the turn was started with ``learning_support`` (the Learning
    Assistant surface), the retired ``agent_runtime`` LangGraph used to
    assemble source materials plus a read-only/tutor-expression prompt
    contract. After ADR-0009 convergence the migrated fragments are injected
    through the unified context and frozen byte-for-byte by contract tests;
    the unified pipeline still owns its distinct complete system prompt.
    """

    metadata = context.metadata
    if not metadata.get("learning_support"):
        return
    materials = cast(list[str], metadata.get("learning_support_materials") or [])
    # The full source text rides inside the user message wrapped in
    # <learning_sources>, so the system prompt stays byte-stable across
    # loop rounds.
    prompt, source_contract = build_learning_support_sources(
        context.user_message,
        materials,
        learning_support=True,
    )
    context.user_message = prompt
    tutor_contract = tutor_expression_contract(
        cast(dict[str, Any] | None, metadata.get("tutor_expression"))
    )
    blocks = learning_support_blocks(
        source_contract=source_contract,
        tutor_contract=tutor_contract,
    )
    existing = cast(list[PromptBlock] | None, metadata.get(_EXTRA_CAPABILITY_BLOCKS))
    metadata[_EXTRA_CAPABILITY_BLOCKS] = [*(existing or []), *blocks]


def _apply_preflight(context: UnifiedContext) -> None:
    """Apply deterministic policy decisions before the pipeline calls Gateway."""

    decisions = preflight(context.user_message)
    context.metadata["policy_preflight"] = [item.model_dump(mode="json") for item in decisions]
    existing = cast(list[PromptBlock] | None, context.metadata.get(_EXTRA_CAPABILITY_BLOCKS))
    blocks = list(existing or [])
    raw_snapshot = context.metadata.get("assistant_context_snapshot")
    if isinstance(raw_snapshot, AssistantContextSnapshot) and not any(
        block.name == "context_snapshot" for block in blocks
    ):
        snapshot_contract = context_snapshot_contract(raw_snapshot)
        if snapshot_contract:
            blocks.append(PromptBlock("context_snapshot", snapshot_contract))
    blocks.append(PromptBlock("policy_preflight", policy_preflight_contract(decisions)))
    context.metadata[_EXTRA_CAPABILITY_BLOCKS] = blocks
