"""Default-off Gateway coverage for the browser-facing agentic chat loop."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.agents.chat.agent_loop import AgentLoop
from traittutor.agents.chat.agentic_pipeline import AgenticChatPipeline
from traittutor.core.agentic.tool_dispatch import DispatchOutcome
from traittutor.core.agentic.usage import UsageTracker
from traittutor.core.context import UnifiedContext
from traittutor.core.stream_bus import StreamBus
from traittutor.gateway import GatewayReceipt, GatewayStreamEvent, GatewayToolCall
from traittutor.services.llm.config import LLMConfig


def _receipt() -> GatewayReceipt:
    return GatewayReceipt(
        request_id="gateway-agentic-1",
        purpose="chat:agentic_loop",
        model="server-model",
        provider="server-provider",
        route="openai",
        latency_ms=1,
        timeout_seconds=180.0,
        response_format_applied=False,
        tools_applied=1,
        attachments_applied=0,
    )


class _Pipeline:
    """Minimal AgentLoop host; tool execution remains explicitly server-side."""

    binding = "openai"
    model = "server-model"
    reasoning_effort = None
    _chat_temperature = 0.25
    loop_max_tokens = 321
    llm_config = LLMConfig(model="server-model", api_key="server-secret", binding="openai")

    def __init__(self, *, gateway_enabled: bool = True, pause: bool = False) -> None:
        self.gateway_enabled = gateway_enabled
        self.pause = pause
        self.usage = UsageTracker(model=self.model)
        self.dispatched: list[list[dict[str, Any]]] = []

    def effective_max_rounds(self, _context: UnifiedContext) -> int:
        return 3

    async def _guard_context_window(
        self, _messages: list[dict[str, Any]], _stream: StreamBus
    ) -> None:
        return None

    def _completion_kwargs(self, *, max_tokens: int) -> dict[str, Any]:
        del max_tokens
        return {}

    def _t(self, _key: str, default: str = "", **_kwargs: Any) -> str:
        return default

    async def _dispatch_tool_calls(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        **_kwargs: Any,
    ) -> DispatchOutcome:
        self.dispatched.append(tool_calls)
        call = tool_calls[0]
        return DispatchOutcome(
            tool_messages=[
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": "private server tool result",
                }
            ],
            pause=self.pause,
            pause_payload={"ask_user": {"question": "Choose one"}} if self.pause else None,
            pause_tool_call_id=call["id"] if self.pause else None,
        )

    async def _await_user_reply_and_resolve(
        self,
        *,
        dispatch: DispatchOutcome,
        **_kwargs: Any,
    ) -> bool:
        for message in dispatch.tool_messages:
            if message["tool_call_id"] == dispatch.pause_tool_call_id:
                message["content"] = "User answered: blue"
        return True

    async def _emit_terminator_final_response(
        self, _stream: StreamBus, _payload: dict[str, Any]
    ) -> None:
        return None

    async def _emit_protocol_fallback_final_response(self, _stream: StreamBus, _text: str) -> None:
        return None

    def _finish_exhausted_instruction(self) -> str:
        return "Finish without tools."


def _context() -> UnifiedContext:
    return UnifiedContext(
        session_id="server-session",
        metadata={
            "gateway_owner_id": "server-owner",
            "gateway_cancellation_event": asyncio.Event(),
        },
    )


def _loop(pipeline: _Pipeline) -> tuple[AgentLoop, StreamBus]:
    stream = StreamBus()
    return (
        AgentLoop(
            pipeline=pipeline,  # type: ignore[arg-type]
            context=_context(),
            stream=stream,
            enabled_tools=["lookup"],
            tool_schemas=[
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "server lookup",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        ),
        stream,
    )


@pytest.mark.asyncio
async def test_agentic_gateway_replays_tool_round_and_projects_no_private_gateway_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _Pipeline()
    loop, stream = _loop(pipeline)
    requests: list[Any] = []

    class Gateway:
        async def stream(self, request: Any):
            requests.append(request)
            if len(requests) == 1:
                yield GatewayStreamEvent(type="reasoning", text="private gateway reasoning")
                yield GatewayStreamEvent(
                    type="text", text="<think>private inline thought</think>Looking up. "
                )
                yield GatewayStreamEvent(
                    type="tool_call",
                    tool_call=GatewayToolCall(
                        id="call-private-1",
                        name="lookup",
                        arguments={"query": "private tool argument"},
                    ),
                )
                yield GatewayStreamEvent(type="usage", usage={"output_tokens": 5})
                yield GatewayStreamEvent(
                    type="final", finish_reason="tool_calls", receipt=_receipt()
                )
                return

            # The second provider call proves assistant call + server-owned
            # role=tool result replay is preserved inside the typed request.
            replay = request.messages[-2:]
            assert replay[0].role == "assistant"
            assert replay[0].tool_calls[0].id == "call-private-1"
            assert replay[0].tool_calls[0].arguments == {"query": "private tool argument"}
            assert replay[1].role == "tool"
            assert replay[1].tool_call_id == "call-private-1"
            assert replay[1].content == "private server tool result"
            yield GatewayStreamEvent(type="text", text="Final safe answer.")
            yield GatewayStreamEvent(type="final", finish_reason="stop", receipt=_receipt())

    monkeypatch.setattr("traittutor.agents.chat.agent_loop.get_gateway", lambda: Gateway())
    messages = [
        {"role": "system", "content": "private system prompt"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "private learner request"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
            ],
        },
    ]
    outcome = await loop._run_loop(
        messages=messages,
        state=SimpleNamespace(rounds=0, tool_steps=0, sources=[]),
        checkpoint_boundary=2,
    )

    assert outcome.completed is True
    assert outcome.final_text == "Final safe answer."
    assert pipeline.dispatched == [
        [
            {
                "id": "call-private-1",
                "name": "lookup",
                "arguments": '{"query":"private tool argument"}',
            }
        ]
    ]
    assert len(requests) == 2
    assert requests[0].purpose == "chat:agentic_loop"
    assert requests[0].user_id == "server-owner"
    assert requests[0].timeout_seconds == 180.0
    assert requests[0].attachments == ()
    assert requests[0].tools[0].name == "lookup"
    assert requests[0].messages[-1].content[1].type == "image_url"

    browser_payload = "\n".join(
        event.content
        for event in stream._history
        if event.content  # noqa: SLF001 - transport assertion
    )
    for private in (
        "private gateway reasoning",
        "private inline thought",
        "private tool argument",
        "private server tool result",
        "gateway-agentic-1",
    ):
        assert private not in browser_payload
    assert "Looking up." in browser_payload
    assert "Final safe answer." in browser_payload
    assert not [event for event in stream._history if event.type.value == "thinking"]  # noqa: SLF001


@pytest.mark.asyncio
async def test_agentic_gateway_ask_user_resume_replays_resolved_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _Pipeline(pause=True)
    loop, _stream = _loop(pipeline)
    requests: list[Any] = []

    class Gateway:
        async def stream(self, request: Any):
            requests.append(request)
            if len(requests) == 1:
                yield GatewayStreamEvent(
                    type="tool_call",
                    tool_call=GatewayToolCall(
                        id="ask-1", name="ask_user", arguments={"questions": []}
                    ),
                )
                yield GatewayStreamEvent(
                    type="final", finish_reason="tool_calls", receipt=_receipt()
                )
                return
            assert request.messages[-1].role == "tool"
            assert request.messages[-1].content == "User answered: blue"
            yield GatewayStreamEvent(type="text", text="Blue it is.")
            yield GatewayStreamEvent(type="final", finish_reason="stop", receipt=_receipt())

    monkeypatch.setattr("traittutor.agents.chat.agent_loop.get_gateway", lambda: Gateway())
    outcome = await loop._run_loop(
        messages=[{"role": "user", "content": "choose"}],
        state=SimpleNamespace(rounds=0, tool_steps=0, sources=[]),
        checkpoint_boundary=1,
    )

    assert outcome.completed is True
    assert outcome.final_text == "Blue it is."
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_agentic_gateway_cancelled_or_timeout_never_uses_legacy_or_recovery_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _Pipeline()
    loop, _stream = _loop(pipeline)

    class CancelledGateway:
        async def stream(self, _request: Any):
            yield GatewayStreamEvent(type="cancelled", receipt=_receipt())

    monkeypatch.setattr("traittutor.agents.chat.agent_loop.get_gateway", lambda: CancelledGateway())
    with pytest.raises(asyncio.CancelledError):
        await loop._run_loop(
            messages=[{"role": "user", "content": "cancel"}],
            state=SimpleNamespace(rounds=0, tool_steps=0, sources=[]),
            checkpoint_boundary=1,
        )

    class TimeoutGateway:
        async def stream(self, _request: Any):
            if False:  # pragma: no cover - preserves async-generator shape.
                yield GatewayStreamEvent(type="final", receipt=_receipt())
            raise TimeoutError("Gateway deadline")

    monkeypatch.setattr("traittutor.agents.chat.agent_loop.get_gateway", lambda: TimeoutGateway())
    with pytest.raises(TimeoutError, match="Gateway deadline"):
        await loop._run_loop(
            messages=[{"role": "user", "content": "timeout"}],
            state=SimpleNamespace(rounds=0, tool_steps=0, sources=[]),
            checkpoint_boundary=1,
        )


@pytest.mark.asyncio
async def test_agentic_gateway_rejects_non_tool_provider_before_any_legacy_or_dsml_path() -> None:
    pipeline = object.__new__(AgenticChatPipeline)
    pipeline.binding = "ollama"
    pipeline.model = "local-model"
    pipeline._prepare_deferred_tools = lambda _context: _async_none()
    pipeline._exec_allowed = lambda _context: _async_false()
    pipeline._compose_enabled_tools = lambda _context: ["lookup"]

    with pytest.raises(RuntimeError, match="typed function-tool support"):
        await pipeline.run(UnifiedContext(), StreamBus())


async def _async_none() -> None:
    return None


async def _async_false() -> bool:
    return False
