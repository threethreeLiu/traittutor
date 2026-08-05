"""Runtime tests for built-in capabilities under the unified framework."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from traittutor.agents.chat.capability import ChatCapability
from traittutor.agents.research.capability import DeepResearchCapability
from traittutor.core.context import Attachment, UnifiedContext
from traittutor.core.stream import StreamEvent, StreamEventType
from traittutor.core.stream_bus import StreamBus
from traittutor.runtime.bootstrap.builtin_capabilities import BUILTIN_CAPABILITY_CLASSES


async def _collect_events(run_coro) -> list[StreamEvent]:
    bus = StreamBus()
    events: list[StreamEvent] = []

    async def _consume() -> None:
        async for event in bus.subscribe():
            events.append(event)

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0)
    await run_coro(bus)
    await asyncio.sleep(0)
    await bus.close()
    await consumer
    return events


def test_builtin_capability_registry_covers_documented_capabilities() -> None:
    assert set(BUILTIN_CAPABILITY_CLASSES) == {
        "chat",
        "deep_research",
    }


@pytest.mark.asyncio
async def test_chat_capability_streams_content_and_geogebra_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakePipeline:
        def __init__(self, language: str = "en") -> None:
            captured["pipeline_init"] = {"language": language}

        async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
            captured["process"] = {
                "message": f"{context.user_message}\nGGB commands",
                "enabled_tools": list(context.enabled_tools or []),
            }
            await stream.tool_call(
                "geogebra_analysis",
                {"image_name": "img.png"},
                source="chat",
                stage="acting",
            )
            await stream.sources(
                [
                    {"type": "rag", "kb_name": "demo-kb", "content": "grounding"},
                    {"type": "web", "url": "https://example.com", "title": "Example"},
                ],
                source="chat",
                stage="responding",
            )
            await stream.content("assistant output", source="chat", stage="responding")

    monkeypatch.setattr("traittutor.agents.chat.capability.AgenticChatPipeline", FakePipeline)

    context = UnifiedContext(
        user_message="analyze triangle",
        enabled_tools=["rag", "web_search", "geogebra_analysis"],
        knowledge_bases=["demo-kb"],
        language="en",
        attachments=[Attachment(type="image", base64="ZmFrZQ==", filename="img.png")],
    )

    capability = ChatCapability()
    events = await _collect_events(lambda bus: capability.run(context, bus))

    assert any(event.type == StreamEventType.TOOL_CALL for event in events)
    assert any(event.type == StreamEventType.SOURCES for event in events)
    assert any(
        event.type == StreamEventType.CONTENT and "assistant output" in event.content
        for event in events
    )
    assert "GGB commands" in captured["process"]["message"]


@pytest.mark.asyncio
async def test_deep_research_capability_delegates_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capability shim validates the request config, normalises
    KB-without-KB, builds a runtime config, and hands the heavy lifting
    to :class:`ResearchPipeline`. We mock the pipeline at its import site
    in the capability module so we can assert what it was called with
    without spinning up real LLM I/O.
    """
    import traittutor.agents.research.capability as deep_research_mod
    import traittutor.agents.research.request_config  # noqa: F401

    captured: dict[str, Any] = {}

    class FakeResearchPipeline:
        def __init__(self, **kwargs: Any) -> None:
            captured["pipeline_init"] = kwargs

        async def run(self, **kwargs: Any) -> dict[str, Any]:
            captured["pipeline_run"] = kwargs
            return {
                "response": f"Report about {kwargs['topic']}",
                "metadata": {"mode": "agentic_research", "block_count": 2},
            }

    def fake_load_config_with_main(_: str) -> dict[str, Any]:
        return {
            "capabilities": {
                "research": {
                    "researching": {
                        "note_agent_mode": "auto",
                        "tool_timeout": 60,
                        "tool_max_retries": 2,
                        "paper_search_years_limit": 3,
                    },
                }
            },
        }

    monkeypatch.setattr(deep_research_mod, "ResearchPipeline", FakeResearchPipeline)
    monkeypatch.setattr(deep_research_mod, "load_config_with_main", fake_load_config_with_main)

    context = UnifiedContext(
        user_message="agent-native tutoring",
        enabled_tools=["rag", "web_search", "paper_search"],
        knowledge_bases=["research-kb"],
        attachments=[Attachment(type="image", base64="ZmFrZQ==", filename="brief.png")],
        config_overrides={
            "mode": "report",
            "depth": "standard",
            # Provide a confirmed outline so the capability skips the
            # outline-preview short-circuit and drives the full
            # research + reporting flow on the pipeline.
            "confirmed_outline": [
                {"title": "Background", "overview": "Why this topic matters"},
                {"title": "Approaches", "overview": "How to do it"},
            ],
        },
        language="en",
    )
    capability = DeepResearchCapability()
    await _collect_events(lambda bus: capability.run(context, bus))

    init_kwargs = captured["pipeline_init"]
    runtime_cfg = init_kwargs["runtime_config"]
    assert init_kwargs["kb_name"] == "research-kb"
    assert init_kwargs["language"] == "en"
    # ``enabled_tools`` is the user's composer toggles forwarded
    # unchanged. The pipeline's per-block ``compose_enabled_tools`` call
    # is what decides what the block loop actually exposes.
    assert init_kwargs["enabled_tools"] == ["rag", "web_search", "paper_search"]
    # Runtime config carries the structured policy sub-dicts the
    # pipeline reads at init time. We only assert the keys the runtime
    # config builder is contractually responsible for producing.
    assert "planning" in runtime_cfg
    assert "researching" in runtime_cfg
    assert "reporting" in runtime_cfg
    # Source-derived enable_* flags were removed; the block loop now
    # composes tools the same way chat does (user toggles + auto-mounts).
    assert "enable_rag" not in runtime_cfg["researching"]
    assert "enable_web_search" not in runtime_cfg["researching"]
    assert "enable_paper_search" not in runtime_cfg["researching"]
    assert "enable_run_code" not in runtime_cfg["researching"]

    run_kwargs = captured["pipeline_run"]
    assert run_kwargs["topic"] == "agent-native tutoring"
    assert run_kwargs["confirmed_outline"] is not None
    assert [item.title for item in run_kwargs["confirmed_outline"]] == [
        "Background",
        "Approaches",
    ]
    # Attachments are forwarded verbatim so the rephrase / decompose
    # prompts can see image evidence.
    assert run_kwargs["attachments"][0].filename == "brief.png"
