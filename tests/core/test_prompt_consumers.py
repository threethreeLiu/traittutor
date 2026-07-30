"""Prompt configuration failures must stop every LLM-facing consumer early."""

from __future__ import annotations

import pytest

from traittutor.core.context import UnifiedContext
from traittutor.core.stream_bus import StreamBus
from traittutor.services.prompt import PromptLoadError


class _BrokenPromptManager:
    def load_prompts(self, **_kwargs):
        raise PromptLoadError("prompt asset is malformed")


def test_agentic_chat_does_not_start_with_an_empty_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from traittutor.agents.chat.agentic_pipeline import AgenticChatPipeline

    monkeypatch.setattr(
        "traittutor.agents.chat.agentic_pipeline.get_prompt_manager",
        lambda: _BrokenPromptManager(),
    )

    with pytest.raises(PromptLoadError, match="prompt asset is malformed"):
        AgenticChatPipeline(language="en")


def test_question_pipeline_does_not_start_with_an_empty_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traittutor.agents.question.pipeline import QuestionPipeline

    monkeypatch.setattr(
        "traittutor.agents.question.pipeline.get_prompt_manager",
        lambda: _BrokenPromptManager(),
    )

    with pytest.raises(PromptLoadError, match="prompt asset is malformed"):
        QuestionPipeline(language="en")


def test_research_pipeline_does_not_start_with_an_empty_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traittutor.agents.research.pipeline import ResearchPipeline

    monkeypatch.setattr(
        "traittutor.agents.research.pipeline.get_prompt_manager",
        lambda: _BrokenPromptManager(),
    )

    with pytest.raises(PromptLoadError, match="prompt asset is malformed"):
        ResearchPipeline(language="en")


def test_explore_context_propagates_prompt_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traittutor.capabilities.explore_context import capability

    capability._PROMPT_CACHE.clear()

    def broken_loader(_path: str):
        raise PromptLoadError("prompt asset is malformed")

    monkeypatch.setattr(capability, "load_markdown_prompt", broken_loader)

    with pytest.raises(PromptLoadError, match="prompt asset is malformed"):
        capability._load_prompts("en")


@pytest.mark.asyncio
async def test_chat_pre_loop_propagates_prompt_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traittutor.agents.chat.agentic_pipeline import AgenticChatPipeline

    class BrokenCapability:
        name = "explore_context"

        async def pre_loop(self, *_args, **_kwargs):
            raise PromptLoadError("explore prompt asset is malformed")

    pipeline = AgenticChatPipeline(language="en")
    monkeypatch.setattr(pipeline, "_active_loop_capabilities", lambda _context: (BrokenCapability(),))

    with pytest.raises(PromptLoadError, match="explore prompt asset is malformed"):
        await pipeline._capability_pre_loop_briefings(UnifiedContext(), StreamBus())


@pytest.mark.asyncio
async def test_chat_pre_loop_keeps_operational_failure_best_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from traittutor.agents.chat.agentic_pipeline import AgenticChatPipeline

    class UnavailableCapability:
        name = "explore_context"

        async def pre_loop(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")

    pipeline = AgenticChatPipeline(language="en")
    monkeypatch.setattr(pipeline, "_active_loop_capabilities", lambda _context: (UnavailableCapability(),))

    assert await pipeline._capability_pre_loop_briefings(UnifiedContext(), StreamBus()) == ""
