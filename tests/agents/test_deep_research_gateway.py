"""Gateway coverage for the browser-facing Deep Research provider loop."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from traittutor.agents.research.gateway_client import GatewayResearchClient
from traittutor.agents.research.pipeline import ResearchPipeline
from traittutor.core.agentic import run_labeled_step
from traittutor.core.context import UnifiedContext
from traittutor.core.stream_bus import StreamBus
from traittutor.gateway import GatewayReceipt, GatewayStreamEvent, GatewayToolCall
from traittutor.services.llm.config import LLMConfig


def _receipt() -> GatewayReceipt:
    return GatewayReceipt(
        request_id="research-private-receipt",
        purpose="research:agentic_loop",
        model="server-model",
        provider="server-provider",
        route="openai",
        latency_ms=1,
        timeout_seconds=180.0,
        response_format_applied=False,
        tools_applied=1,
        attachments_applied=0,
    )


def _client() -> GatewayResearchClient:
    return GatewayResearchClient(
        owner_id="server-owner",
        cancellation_event=asyncio.Event(),
        llm_config=LLMConfig(model="server-model", api_key="server-secret", binding="openai"),
        reasoning_effort=None,
    )


async def _frames(response: Any) -> list[Any]:
    return [frame async for frame in response]


@pytest.mark.asyncio
async def test_deep_research_gateway_replays_typed_tool_round_and_redacts_private_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    requests: list[Any] = []

    class Gateway:
        async def stream(self, request: Any):
            requests.append(request)
            if len(requests) == 1:
                yield GatewayStreamEvent(type="reasoning", text="private gateway reasoning")
                yield GatewayStreamEvent(
                    type="text", text="<think>private inline thought</think>``TOOL`` look up"
                )
                yield GatewayStreamEvent(
                    type="tool_call",
                    tool_call=GatewayToolCall(
                        id="research-call-1",
                        name="web_search",
                        arguments={"query": "private tool argument"},
                    ),
                )
                yield GatewayStreamEvent(type="usage", usage={"completion_tokens": 3})
                yield GatewayStreamEvent(
                    type="final", finish_reason="tool_calls", receipt=_receipt()
                )
                return

            replay = request.messages[-2:]
            assert replay[0].role == "assistant"
            assert replay[0].tool_calls[0].id == "research-call-1"
            assert replay[0].tool_calls[0].arguments == {"query": "private tool argument"}
            assert replay[1].role == "tool"
            assert replay[1].tool_call_id == "research-call-1"
            assert replay[1].content == "private server tool result"
            yield GatewayStreamEvent(type="text", text="``FINISH`` safe report")
            yield GatewayStreamEvent(type="final", finish_reason="stop", receipt=_receipt())

    monkeypatch.setattr("traittutor.core.agentic.gateway_client.get_gateway", lambda: Gateway())
    initial = [
        {"role": "system", "content": "private system prompt"},
        {"role": "user", "content": "private topic"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "private server search",
                "parameters": {"type": "object"},
            },
        }
    ]
    first = await client.chat.completions.create(
        model="server-model", messages=initial, tools=tools, stream=True, max_tokens=123
    )
    first_frames = await _frames(first)

    visible = "".join(
        frame.choices[0].delta.content or ""
        for frame in first_frames
        if frame.choices and frame.choices[0].delta is not None
    )
    assert visible == "``TOOL`` look up"
    assert "private gateway reasoning" not in visible
    assert "private inline thought" not in visible
    tool_delta = next(
        frame.choices[0].delta.tool_calls[0]
        for frame in first_frames
        if frame.choices[0].delta.tool_calls
    )
    replay_messages = initial + [
        {
            "role": "assistant",
            "content": "look up",
            "tool_calls": [
                {
                    "id": tool_delta.id,
                    "type": "function",
                    "function": {
                        "name": tool_delta.function.name,
                        "arguments": tool_delta.function.arguments,
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": tool_delta.id,
            "name": tool_delta.function.name,
            "content": "private server tool result",
        },
    ]
    second = await client.chat.completions.create(
        model="server-model", messages=replay_messages, tools=tools, stream=True, max_tokens=321
    )
    second_frames = await _frames(second)

    assert "".join(frame.choices[0].delta.content or "" for frame in second_frames) == (
        "``FINISH`` safe report"
    )
    assert len(requests) == 2
    assert requests[0].purpose == "research:agentic_loop"
    assert requests[0].user_id == "server-owner"
    assert requests[0].timeout_seconds == 180.0
    assert requests[0].attachments == ()
    assert requests[0].tools[0].name == "web_search"
    assert requests[0].max_tokens == 123


@pytest.mark.asyncio
async def test_deep_research_gateway_cancellation_or_missing_terminal_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()

    class CancelledGateway:
        async def stream(self, _request: Any):
            yield GatewayStreamEvent(type="cancelled", receipt=_receipt())

    monkeypatch.setattr(
        "traittutor.core.agentic.gateway_client.get_gateway", lambda: CancelledGateway()
    )
    response = await client.chat.completions.create(
        model="server-model", messages=[{"role": "user", "content": "cancel"}], stream=True
    )
    with pytest.raises(asyncio.CancelledError):
        await _frames(response)
    with pytest.raises(asyncio.CancelledError):
        await client.chat.completions.create(
            model="server-model",
            messages=[{"role": "user", "content": "cancel note"}],
            stream=False,
        )

    class IncompleteGateway:
        async def stream(self, _request: Any):
            yield GatewayStreamEvent(type="text", text="partial")

    monkeypatch.setattr(
        "traittutor.core.agentic.gateway_client.get_gateway", lambda: IncompleteGateway()
    )
    response = await client.chat.completions.create(
        model="server-model", messages=[{"role": "user", "content": "timeout"}], stream=True
    )
    with pytest.raises(RuntimeError, match="terminal receipt"):
        await _frames(response)


@pytest.mark.asyncio
async def test_deep_research_gateway_rejects_unoffered_or_duplicate_typed_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()

    class Gateway:
        async def stream(self, _request: Any):
            yield GatewayStreamEvent(
                type="tool_call",
                tool_call=GatewayToolCall(id="bad-tool", name="write_memory", arguments={}),
            )

    monkeypatch.setattr("traittutor.core.agentic.gateway_client.get_gateway", lambda: Gateway())
    response = await client.chat.completions.create(
        model="server-model",
        messages=[{"role": "user", "content": "topic"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "web_search", "parameters": {"type": "object"}},
            }
        ],
        stream=True,
    )
    with pytest.raises(RuntimeError, match="unsupported typed tool"):
        await _frames(response)

    class DuplicateGateway:
        async def stream(self, _request: Any):
            call = GatewayToolCall(id="duplicate-tool", name="web_search", arguments={})
            yield GatewayStreamEvent(type="tool_call", tool_call=call)
            yield GatewayStreamEvent(type="tool_call", tool_call=call)

    monkeypatch.setattr(
        "traittutor.core.agentic.gateway_client.get_gateway", lambda: DuplicateGateway()
    )
    response = await client.chat.completions.create(
        model="server-model",
        messages=[{"role": "user", "content": "topic"}],
        tools=[
            {
                "type": "function",
                "function": {"name": "web_search", "parameters": {"type": "object"}},
            }
        ],
        stream=True,
    )
    with pytest.raises(RuntimeError, match="unsupported typed tool"):
        await _frames(response)


@pytest.mark.asyncio
async def test_deep_research_gateway_projects_only_existing_safe_loop_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()

    class Gateway:
        async def stream(self, _request: Any):
            yield GatewayStreamEvent(type="reasoning", text="private reasoning")
            yield GatewayStreamEvent(
                type="text", text="<think>private inline reasoning</think>``TOOL`` searching"
            )
            yield GatewayStreamEvent(
                type="tool_call",
                tool_call=GatewayToolCall(
                    id="private-call",
                    name="web_search",
                    arguments={"query": "private argument"},
                ),
            )
            yield GatewayStreamEvent(type="final", finish_reason="tool_calls", receipt=_receipt())

    monkeypatch.setattr("traittutor.core.agentic.gateway_client.get_gateway", lambda: Gateway())
    stream = StreamBus()
    result = await run_labeled_step(
        client=client,
        model="server-model",
        messages=[{"role": "user", "content": "topic"}],
        completion_kwargs={},
        tool_schemas=[
            {
                "type": "function",
                "function": {"name": "web_search", "parameters": {"type": "object"}},
            }
        ],
        allowed_labels=("TOOL", "FINISH"),
        final_labels=frozenset({"FINISH"}),
        tool_label="TOOL",
        stream=stream,
        source="deep_research",
        stage="researching",
        iter_meta={},
        binding="openai",
    )

    assert result.label == "TOOL"
    assert result.tool_calls == [
        {
            "id": "private-call",
            "name": "web_search",
            "arguments": '{"query":"private argument"}',
        }
    ]
    browser_payload = "\n".join(
        event.content
        for event in stream._history
        if event.content  # noqa: SLF001
    )
    assert "searching" in browser_payload
    for private in (
        "private reasoning",
        "private inline reasoning",
        "private argument",
        "private-call",
        "research-private-receipt",
    ):
        assert private not in browser_payload


@pytest.mark.asyncio
async def test_deep_research_gateway_routes_citation_note_sidecar_through_typed_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    requests: list[Any] = []

    class Gateway:
        async def stream(self, request: Any):
            requests.append(request)
            yield GatewayStreamEvent(type="reasoning", text="private note reasoning")
            yield GatewayStreamEvent(
                type="text", text="<think>private inline note</think>``FINISH`` concise cited note"
            )
            yield GatewayStreamEvent(type="final", finish_reason="stop", receipt=_receipt())

    monkeypatch.setattr("traittutor.core.agentic.gateway_client.get_gateway", lambda: Gateway())
    response = await client.chat.completions.create(
        model="server-model",
        messages=[
            {"role": "system", "content": "private note prompt"},
            {"role": "user", "content": "private raw search output"},
        ],
        stream=False,
        max_completion_tokens=77,
    )

    assert response.choices[0].message.content == "``FINISH`` concise cited note"
    assert len(requests) == 1
    assert requests[0].purpose == "research:note"
    assert requests[0].user_id == "server-owner"
    assert requests[0].max_tokens == 77
    assert requests[0].attachments == ()


def test_deep_research_gateway_requires_server_scope_and_typed_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = object.__new__(ResearchPipeline)
    pipeline.binding = "ollama"
    pipeline.model = "local-model"
    pipeline.rephrase_enabled = False
    pipeline._block_tool_names = lambda: ["web_search"]
    pipeline._tool_in_registry = lambda _name: False

    with pytest.raises(RuntimeError, match="typed function-tool support"):
        pipeline._build_client_for_context(UnifiedContext())

    pipeline.binding = "openai"
    pipeline.model = "server-model"
    monkeypatch.setattr(
        "traittutor.agents.research.pipeline.gateway_supports_tools", lambda *_args: True
    )
    with pytest.raises(RuntimeError, match="server-owned turn"):
        pipeline._build_client_for_context(UnifiedContext())
