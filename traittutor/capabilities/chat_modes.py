"""Named chat-loop capabilities exposed by the Learning Tools composer.

These modes intentionally reuse the canonical chat agent loop and its tool
surface. Giving each user-visible mode a runtime capability name keeps session
history, telemetry, request validation, and availability checks aligned with
what the composer presents instead of recording every mode as generic chat.
"""

from __future__ import annotations

from functools import lru_cache
from typing import ClassVar

from traittutor.agents.chat.agentic_pipeline import CHAT_OPTIONAL_TOOLS
from traittutor.agents.chat.capability import ChatCapability
from traittutor.capabilities.protocol import PromptBlock
from traittutor.core.capability_protocol import CapabilityManifest
from traittutor.core.context import UnifiedContext
from traittutor.core.stream_bus import StreamBus
from traittutor.prompts import asset_path
from traittutor.runtime.request_contracts import get_capability_request_schema
from traittutor.services.prompt.markdown import PromptLoadError, load_markdown_prompt

_EXTRA_CAPABILITY_BLOCKS = "_extra_capability_blocks"


@lru_cache(maxsize=8)
def _load_mode_prompt(mode: str, language: str) -> str:
    lang = "zh" if language.lower().startswith("zh") else "en"
    prompt_path = asset_path("capabilities", "chat_modes", lang, f"{mode}.md")
    value = load_markdown_prompt(prompt_path).get("system")
    if not isinstance(value, str) or not value.strip():
        raise PromptLoadError(
            f"{prompt_path}: required 'system' prompt section is missing or empty"
        )
    return value


class _PromptedChatCapability(ChatCapability):
    """Add a server-owned mode contract without changing the user message."""

    prompt_name: ClassVar[str]

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        existing = context.metadata.get(_EXTRA_CAPABILITY_BLOCKS)
        blocks = list(existing) if isinstance(existing, list) else []
        blocks.append(
            PromptBlock(
                self.prompt_name,
                _load_mode_prompt(self.prompt_name, context.language),
            )
        )
        context.metadata[_EXTRA_CAPABILITY_BLOCKS] = blocks
        await super().run(context, stream)


class LearningExplorationCapability(_PromptedChatCapability):
    prompt_name = "learning_exploration"
    manifest = CapabilityManifest(
        name="learning_exploration",
        description="Explore sources, concepts, and useful next steps through the chat loop.",
        stages=["exploring", "responding"],
        tools_used=CHAT_OPTIONAL_TOOLS,
        cli_aliases=["explore"],
        request_schema=get_capability_request_schema("learning_exploration"),
    )


class KnowledgeDiagramCapability(_PromptedChatCapability):
    prompt_name = "knowledge_diagram"
    manifest = CapabilityManifest(
        name="knowledge_diagram",
        description="Build an accumulable concept diagram through the chat loop.",
        stages=["exploring", "responding"],
        tools_used=CHAT_OPTIONAL_TOOLS,
        cli_aliases=["diagram"],
        request_schema=get_capability_request_schema("knowledge_diagram"),
    )


class HumanizerCapability(_PromptedChatCapability):
    prompt_name = "humanizer"
    manifest = CapabilityManifest(
        name="humanizer",
        description="Rewrite text naturally while preserving its meaning through the chat loop.",
        stages=["exploring", "responding"],
        tools_used=CHAT_OPTIONAL_TOOLS,
        cli_aliases=["humanize"],
        request_schema=get_capability_request_schema("humanizer"),
    )


__all__ = [
    "HumanizerCapability",
    "KnowledgeDiagramCapability",
    "LearningExplorationCapability",
]
