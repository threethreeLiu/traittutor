"""Focused contract tests for the typed, non-streaming Gateway slice."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import logging
from typing import Any

import pytest

from traittutor.agents.base_agent import BaseAgent
from traittutor.gateway import (
    GatewayAttachment,
    GatewayContentPart,
    GatewayMessage,
    GatewayReceipt,
    GatewayRequest,
    GatewayResponse,
    GatewayStreamEvent,
    GatewayTool,
    GatewayToolCall,
    TraitTutorGateway,
)
from traittutor.services.llm.config import LLMConfig
from traittutor.services.llm.provider_core.anthropic_provider import AnthropicProvider
from traittutor.services.llm.provider_core.base import LLMResponse, ToolCallRequest
from traittutor.services.llm.provider_core.openai_compat_provider import OpenAICompatProvider
from traittutor.services.llm.provider_core.openai_responses import convert_messages
from traittutor.telemetry import InMemoryProductEventSink


def _config(*, binding: str = "openai", model: str = "gateway-model") -> LLMConfig:
    return LLMConfig(
        model=model,
        api_key="gateway-secret-must-not-leak",
        base_url="https://private.gateway.example/v1",
        binding=binding,
        provider_name=binding,
    )


@pytest.mark.asyncio
async def test_typed_messages_and_attachments_are_normalized_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            received["config"] = config

        async def complete_with_usage(
            self, prompt: str, **kwargs: Any
        ) -> tuple[str, dict[str, int], list]:
            received["prompt"] = prompt
            received["kwargs"] = kwargs
            return "typed answer", {}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    gateway = TraitTutorGateway()

    response = await gateway.complete(
        GatewayRequest(
            prompt="fallback prompt",
            system_prompt="fallback system",
            purpose="typed-contract",
            messages=(
                GatewayMessage(role="system", content="typed system"),
                GatewayMessage(
                    role="user",
                    content=(GatewayContentPart(type="text", text="typed user"),),
                ),
            ),
            attachments=(
                GatewayAttachment(
                    type="image",
                    filename="diagram.png",
                    mime_type="image/png",
                    base64="aGVsbG8=",
                ),
            ),
            llm_config=_config(),
        )
    )

    assert response.content == "typed answer"
    messages = received["kwargs"]["history"]
    assert messages[0] == {"role": "system", "content": "typed system"}
    assert messages[1]["content"][0] == {"type": "text", "text": "typed user"}
    assert messages[1]["content"][1]["type"] == "image_url"
    assert response.receipt is not None
    assert response.receipt.attachments_applied == 1


@pytest.mark.asyncio
async def test_completion_preserves_provider_usage_only_as_aggregate_operational_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: Any
        ) -> tuple[str, dict[str, int], list]:
            assert prompt == "private prompt"
            assert kwargs["system_prompt"] == "private system"
            return "answer", {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    sink = InMemoryProductEventSink()
    response = await TraitTutorGateway(event_sink=sink).complete(
        GatewayRequest(
            prompt="private prompt",
            system_prompt="private system",
            purpose="usage-contract",
            llm_config=_config(),
        )
    )

    assert response.usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    assert response.receipt is not None
    receipt = json.dumps(asdict(response.receipt))
    assert "prompt_tokens" not in receipt
    assert "completion_tokens" not in receipt
    assert "total_tokens" not in receipt
    event = next(event for event in sink.events if "total_tokens" in event.attributes)
    assert event.attributes["total_tokens"] == 18
    assert "total_tokens" not in event.metric_labels
    assert "private prompt" not in event.model_dump_json()


@pytest.mark.asyncio
async def test_unsupported_optional_features_fall_back_without_provider_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: Any
        ) -> tuple[str, dict[str, int], list]:
            del prompt
            received.update(kwargs)
            return "plain fallback", {}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    monkeypatch.setattr("traittutor.gateway.service.supports_response_format", lambda *_: False)
    monkeypatch.setattr("traittutor.gateway.service.supports_tools", lambda *_: False)
    response = await TraitTutorGateway().complete(
        GatewayRequest(
            prompt="private prompt",
            system_prompt="private system",
            purpose="fallback",
            response_format={"type": "json_object"},
            tools=(
                GatewayTool(
                    name="private_tool",
                    description="private description",
                    parameters={"type": "object", "properties": {}},
                ),
            ),
            llm_config=_config(binding="anthropic"),
        )
    )

    assert response.content == "plain fallback"
    assert "response_format" not in received
    assert "tools" not in received
    assert response.receipt is not None
    assert response.receipt.response_format_applied is False
    assert response.receipt.tools_applied == 0


@pytest.mark.asyncio
async def test_gateway_total_timeout_is_telemetry_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: Any
        ) -> tuple[str, dict[str, int], list]:
            del prompt, kwargs
            await asyncio.sleep(0.1)
            return "too late", {}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", SlowClient)
    sink = InMemoryProductEventSink()
    with pytest.raises(TimeoutError):
        await TraitTutorGateway(event_sink=sink).complete(
            GatewayRequest(
                prompt="private timeout prompt",
                system_prompt="private timeout system",
                purpose="generate:timeout",
                # Exercise the single-route timeout path directly; the
                # quota-rotation wrapper has its own deadline semantics.
                allow_quota_rotation=False,
                timeout_seconds=0.001,
                llm_config=_config(),
            )
        )

    assert sink.events[0].attributes["outcome"] == "timeout"
    assert "private timeout prompt" not in sink.events[0].model_dump_json()


@pytest.mark.asyncio
async def test_receipt_is_redacted_of_request_and_configuration_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: Any
        ) -> tuple[str, dict[str, int], list]:
            del prompt, kwargs
            return "private answer", {}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    response = await TraitTutorGateway().complete(
        GatewayRequest(
            prompt="private prompt",
            system_prompt="private system",
            purpose="receipt",
            user_id="private owner",
            metadata={"authorization": "Bearer private metadata"},
            attachments=(GatewayAttachment(type="image", url="https://private.example/image.png"),),
            llm_config=_config(),
        )
    )

    assert response.receipt is not None
    serialized = json.dumps(asdict(response.receipt), sort_keys=True)
    for secret in (
        "private prompt",
        "private system",
        "private owner",
        "private metadata",
        "private answer",
        "gateway-secret-must-not-leak",
        "private.gateway.example",
        "private.example/image.png",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_assistant_tool_call_and_tool_result_replay_across_provider_forms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One typed conversation keeps call linkage for Chat, Responses and Claude."""
    received: dict[str, Any] = {}

    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: Any
        ) -> tuple[str, dict[str, int], list]:
            del prompt
            received.update(kwargs)
            return "next answer", {}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    tool_call = ToolCallRequest(
        id="call-private-1",
        name="lookup",
        arguments={"query": "private-tool-argument"},
    )
    # The caller receives this normalized output event, then uses it verbatim
    # as the assistant turn before appending its server-owned tool result.
    assistant = GatewayMessage(
        role="assistant",
        content=None,
        tool_calls=(
            # Keep this explicit to prove the public Gateway type, rather than
            # a provider implementation detail, owns the replay shape.
            GatewayToolCall(
                id=tool_call.id,
                name=tool_call.name,
                arguments=tool_call.arguments,
            ),
        ),
    )
    conversation = (
        GatewayMessage(role="user", content="find it"),
        assistant,
        GatewayMessage(
            role="tool",
            tool_call_id="call-private-1",
            name="lookup",
            content='{"result":"private-tool-result"}',
        ),
    )
    sink = InMemoryProductEventSink()
    response = await TraitTutorGateway(event_sink=sink).complete(
        GatewayRequest(
            prompt="unused fallback",
            system_prompt="unused system",
            purpose="tool-replay",
            messages=conversation,
            llm_config=_config(),
        )
    )

    provider_messages = received["history"]
    assert provider_messages[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-private-1",
                "type": "function",
                "function": {
                    "name": "lookup",
                    "arguments": '{"query":"private-tool-argument"}',
                },
            }
        ],
    }
    assert provider_messages[2]["tool_call_id"] == "call-private-1"

    # Chat Completions preserves the assistant call envelope and result ID.
    openai_messages = OpenAICompatProvider()._sanitize_messages(provider_messages)
    assert openai_messages[1]["content"] is None
    openai_call = openai_messages[1]["tool_calls"][0]
    assert openai_call["function"] == {
        "name": "lookup",
        "arguments": '{"query":"private-tool-argument"}',
    }
    assert openai_messages[2]["tool_call_id"] == openai_call["id"]

    # The native Anthropic Messages protocol produces a tool_use followed by
    # a user-side tool_result with exactly the linked call ID.
    _system, anthropic_messages = AnthropicProvider()._convert_messages(provider_messages)
    assert anthropic_messages[1]["content"] == [
        {
            "type": "tool_use",
            "id": "call-private-1",
            "name": "lookup",
            "input": {"query": "private-tool-argument"},
        }
    ]
    assert anthropic_messages[2]["content"][0]["tool_use_id"] == "call-private-1"

    # OpenAI Responses requires its distinct function_call/function_call_output
    # input items; the converter keeps both the call ID and canonical JSON.
    _instructions, response_items = convert_messages(provider_messages)
    assert response_items[1] == {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call-private-1",
        "name": "lookup",
        "arguments": '{"query":"private-tool-argument"}',
    }
    assert response_items[2] == {
        "type": "function_call_output",
        "call_id": "call-private-1",
        "output": '{"result":"private-tool-result"}',
    }

    assert response.receipt is not None
    serialized = "\n".join(
        [json.dumps(asdict(response.receipt), sort_keys=True)]
        + [event.model_dump_json() for event in sink.events]
    )
    assert "private-tool-argument" not in serialized
    assert "private-tool-result" not in serialized


def test_tool_result_requires_an_earlier_matching_assistant_call() -> None:
    orphan = GatewayRequest(
        prompt="prompt",
        system_prompt="system",
        purpose="tool-contract",
        messages=(GatewayMessage(role="tool", content="result", tool_call_id="unknown-call"),),
        llm_config=_config(),
    )
    with pytest.raises(ValueError, match="earlier assistant tool_call"):
        TraitTutorGateway._provider_messages(orphan)

    mismatched_name = GatewayRequest(
        prompt="prompt",
        system_prompt="system",
        purpose="tool-contract",
        messages=(
            GatewayMessage(
                role="assistant",
                content=None,
                tool_calls=(GatewayToolCall(id="call-1", name="lookup", arguments={}),),
            ),
            GatewayMessage(
                role="tool", content="result", tool_call_id="call-1", name="different_tool"
            ),
        ),
        llm_config=_config(),
    )
    with pytest.raises(ValueError, match="name must match"):
        TraitTutorGateway._provider_messages(mismatched_name)


def test_tool_call_json_validation() -> None:
    with pytest.raises(ValueError, match="valid JSON"):
        GatewayMessage.from_mapping(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "not-json"},
                    }
                ],
            }
        )
    with pytest.raises(ValueError, match="JSON-safe"):
        GatewayToolCall(id="call-1", name="lookup", arguments={"not_json": float("nan")})


@pytest.mark.asyncio
async def test_stream_preserves_reasoning_tool_calls_usage_and_final_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    class FakeProvider:
        async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
            received.update(kwargs)
            await kwargs["on_reasoning_delta"]("private reasoning")
            await kwargs["on_content_delta"]("visible answer")
            return LLMResponse(
                content="visible answer",
                tool_calls=[
                    ToolCallRequest(
                        id="call-1",
                        name="lookup",
                        arguments={"query": "private tool argument"},
                    )
                ],
                usage={"input_tokens": 4, "output_tokens": 8},
                finish_reason="tool_calls",
            )

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider", lambda config: FakeProvider()
    )
    sink = InMemoryProductEventSink()
    events = [
        event
        async for event in TraitTutorGateway(event_sink=sink).stream(
            GatewayRequest(
                prompt="private prompt",
                system_prompt="private system",
                purpose="stream-contract",
                tools=(GatewayTool(name="lookup"),),
                llm_config=_config(),
            )
        )
    ]

    assert [event.type for event in events] == [
        "reasoning",
        "text",
        "tool_call",
        "usage",
        "final",
    ]
    assert events[2].tool_call is not None
    assert events[2].tool_call.arguments == {"query": "private tool argument"}
    assert events[3].usage == {"input_tokens": 4, "output_tokens": 8}
    assert events[-1].receipt is not None
    assert events[-1].finish_reason == "tool_calls"
    assert events[-1].receipt.timeout_seconds == 300
    assert received["tools"] == [GatewayTool(name="lookup").to_provider()]
    event_payload = sink.events[0].model_dump_json()
    assert sink.events[0].attributes["total_tokens"] == 12
    assert "private prompt" not in event_payload
    assert "private tool argument" not in event_payload


@pytest.mark.asyncio
async def test_stream_emits_final_provider_content_when_no_delta_was_received(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FinalOnlyProvider:
        async def chat_stream_with_retry(self, **_kwargs: Any) -> LLMResponse:
            return LLMResponse(content="final-only answer", finish_reason="stop")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider",
        lambda _config: FinalOnlyProvider(),
    )

    events = [
        event
        async for event in TraitTutorGateway().stream(
            GatewayRequest(
                prompt="prompt",
                system_prompt="system",
                purpose="stream-final-content",
                llm_config=_config(),
            )
        )
    ]

    assert [event.type for event in events] == ["text", "final"]
    assert events[0].text == "final-only answer"


@pytest.mark.asyncio
async def test_completion_only_records_injected_server_pricing_as_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, _prompt: str, **_kwargs: Any
        ) -> tuple[str, dict[str, int], list]:
            return "answer", {"prompt_tokens": 10, "completion_tokens": 5}, []

    class Pricing:
        def cost_picousd(self, model: str, usage: dict[str, int]) -> int | None:
            assert model == "gateway-model"
            assert usage == {"prompt_tokens": 10, "completion_tokens": 5}
            return 40_000_000

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    sink = InMemoryProductEventSink()
    response = await TraitTutorGateway(event_sink=sink, token_pricing=Pricing()).complete(
        GatewayRequest(
            prompt="private prompt",
            system_prompt="private system",
            purpose="priced-contract",
            llm_config=_config(),
        )
    )

    assert response.receipt is not None
    assert "cost_picousd" not in json.dumps(asdict(response.receipt))
    event = next(event for event in sink.events if "cost_picousd" in event.attributes)
    assert event.attributes["cost_picousd"] == 40_000_000
    assert "cost_picousd" not in event.metric_labels


@pytest.mark.asyncio
async def test_stream_unsupported_optional_features_are_not_sent_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, Any] = {}

    class FakeProvider:
        async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
            received.update(kwargs)
            return LLMResponse(content="fallback")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider", lambda config: FakeProvider()
    )
    monkeypatch.setattr("traittutor.gateway.service.supports_response_format", lambda *_: False)
    monkeypatch.setattr("traittutor.gateway.service.supports_tools", lambda *_: False)
    events = [
        event
        async for event in TraitTutorGateway().stream(
            GatewayRequest(
                prompt="prompt",
                system_prompt="system",
                purpose="stream-fallback",
                response_format={"type": "json_object"},
                tools=(GatewayTool(name="private_tool"),),
                llm_config=_config(binding="anthropic"),
            )
        )
    ]

    assert received["tools"] is None
    assert "response_format" not in received
    assert events[-1].type == "final"
    assert events[-1].receipt is not None
    assert events[-1].receipt.response_format_applied is False
    assert events[-1].receipt.tools_applied == 0


@pytest.mark.asyncio
async def test_stream_cancellation_stops_provider_and_emits_redacted_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class FakeProvider:
        async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
            del kwargs
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("unreachable")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider", lambda config: FakeProvider()
    )
    cancellation_event = asyncio.Event()
    iterator = TraitTutorGateway().stream(
        GatewayRequest(
            prompt="private prompt",
            system_prompt="private system",
            purpose="stream-cancel",
            llm_config=_config(),
            cancellation_event=cancellation_event,
        )
    )
    next_event = asyncio.create_task(anext(iterator))
    await started.wait()
    cancellation_event.set()
    event = await next_event

    assert event.type == "cancelled"
    assert event.receipt is not None
    assert "private prompt" not in json.dumps(asdict(event.receipt), sort_keys=True)
    assert cancelled.is_set()
    await iterator.aclose()


@pytest.mark.asyncio
async def test_stream_timeout_is_bounded_and_telemetry_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowProvider:
        async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
            del kwargs
            await asyncio.sleep(0.1)
            return LLMResponse(content="too late")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider", lambda config: SlowProvider()
    )
    sink = InMemoryProductEventSink()
    with pytest.raises(TimeoutError):
        async for _event in TraitTutorGateway(event_sink=sink).stream(
            GatewayRequest(
                prompt="private timeout prompt",
                system_prompt="private system",
                purpose="stream-timeout",
                timeout_seconds=0.001,
                llm_config=_config(),
            )
        ):
            pass

    assert sink.events[0].attributes["outcome"] == "timeout"
    assert "private timeout prompt" not in sink.events[0].model_dump_json()


def test_stream_timeout_has_a_server_side_upper_bound() -> None:
    assert TraitTutorGateway._stream_timeout_seconds(999_999) == 300


class _GatewayProbeAgent(BaseAgent):
    async def process(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise NotImplementedError


def _bare_agent() -> _GatewayProbeAgent:
    """Avoid prompt loading; call_llm only needs this small stable state."""
    agent = object.__new__(_GatewayProbeAgent)
    agent.module_name = "probe"
    agent.agent_name = "probe_agent"
    agent.api_key = "agent-secret"
    agent.base_url = "https://agent.private.example/v1"
    agent.api_version = None
    agent.binding = "openai"
    agent._agent_params = {"temperature": 0.2, "max_tokens": 123}
    agent.agent_config = {"max_retries": 2}
    agent.llm_config = {}
    agent.model = "gateway-model"
    agent.logger = logging.getLogger("test.gateway-probe")
    agent._trace_callback = None
    agent.token_tracker = None
    return agent


@pytest.mark.asyncio
async def test_base_agent_uses_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _bare_agent()
    monkeypatch.setattr("traittutor.agents.base_agent.get_llm_config", lambda: _config())
    captured: dict[str, GatewayRequest] = {}

    class StubGateway:
        async def complete(self, request: GatewayRequest) -> GatewayResponse:
            captured["request"] = request
            return GatewayResponse(
                request_id="gateway-request",
                content="gateway answer",
                model="gateway-model",
                purpose=request.purpose,
                latency_ms=3,
                receipt=GatewayReceipt(
                    request_id="gateway-request",
                    purpose=request.purpose,
                    model="gateway-model",
                    provider="test",
                    route="test",
                    latency_ms=3,
                    timeout_seconds=2,
                    response_format_applied=True,
                    tools_applied=0,
                    attachments_applied=0,
                ),
            )

    monkeypatch.setattr("traittutor.agents.base_agent.get_gateway", lambda: StubGateway())
    tracked: list[dict[str, Any]] = []
    agent._track_tokens = lambda **kwargs: tracked.append(kwargs)  # type: ignore[method-assign]

    response = await agent.call_llm(
        "user",
        "system",
        messages=[{"role": "user", "content": "typed message"}],
        response_format={"type": "json_object"},
        timeout_seconds=2,
        verbose=False,
    )

    assert response == "gateway answer"
    request = captured["request"]
    assert request.messages[0].content == "typed message"
    assert request.response_format == {"type": "json_object"}
    assert request.timeout_seconds == 2
    assert tracked[0]["response"] == "gateway answer"


@pytest.mark.asyncio
async def test_base_agent_stream_uses_gateway_and_preserves_typed_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _bare_agent()
    monkeypatch.setattr("traittutor.agents.base_agent.get_llm_config", lambda: _config())
    captured: dict[str, GatewayRequest] = {}

    async def forbidden_legacy_stream(*_args: Any, **_kwargs: Any):
        pytest.fail("BaseAgent.stream_llm must not call the legacy LLM factory")
        yield "unreachable"  # pragma: no cover

    monkeypatch.setattr(
        "traittutor.agents.base_agent.llm_stream", forbidden_legacy_stream, raising=False
    )

    class StubGateway:
        async def stream(self, request: GatewayRequest):
            captured["request"] = request
            yield GatewayStreamEvent(type="reasoning", text="server-only reasoning")
            yield GatewayStreamEvent(type="text", text="gateway ")
            yield GatewayStreamEvent(type="usage", usage={"output_tokens": 2})
            yield GatewayStreamEvent(type="text", text="answer")
            yield GatewayStreamEvent(
                type="final",
                finish_reason="stop",
                receipt=GatewayReceipt(
                    request_id="gateway-stream-request",
                    purpose=request.purpose,
                    model="gateway-model",
                    provider="test",
                    route="test",
                    latency_ms=3,
                    timeout_seconds=12,
                    response_format_applied=True,
                    tools_applied=0,
                    attachments_applied=1,
                ),
            )

    monkeypatch.setattr("traittutor.agents.base_agent.get_gateway", lambda: StubGateway())
    tracked: list[dict[str, Any]] = []
    traces: list[dict[str, Any]] = []
    agent._track_tokens = lambda **kwargs: tracked.append(kwargs)  # type: ignore[method-assign]
    agent.set_trace_callback(lambda payload: traces.append(payload))

    chunks = [
        chunk
        async for chunk in agent.stream_llm(
            "user",
            "system",
            messages=[{"role": "user", "content": "typed message"}],
            attachments=(
                GatewayAttachment(
                    type="image",
                    filename="diagram.png",
                    mime_type="image/png",
                    base64="aGVsbG8=",
                ),
            ),
            response_format={"type": "json_object"},
            timeout_seconds=12,
            verbose=False,
        )
    ]

    assert chunks == ["gateway ", "answer"]
    request = captured["request"]
    assert request.purpose == "agent:probe:probe_agent:probe_agent"
    assert request.messages[0].content == "typed message"
    assert request.attachments[0].filename == "diagram.png"
    assert request.response_format == {"type": "json_object"}
    assert request.max_retries == 2
    assert request.timeout_seconds == 12
    assert tracked[0]["response"] == "gateway answer"
    assert [event["state"] for event in traces] == [
        "running",
        "streaming",
        "streaming",
        "complete",
    ]


@pytest.mark.asyncio
async def test_base_agent_stream_rejects_incomplete_gateway_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _bare_agent()
    monkeypatch.setattr("traittutor.agents.base_agent.get_llm_config", lambda: _config())

    class IncompleteGateway:
        async def stream(self, _request: GatewayRequest):
            yield GatewayStreamEvent(type="text", text="partial")

    monkeypatch.setattr("traittutor.agents.base_agent.get_gateway", lambda: IncompleteGateway())

    with pytest.raises(RuntimeError, match="without a terminal receipt"):
        async for _chunk in agent.stream_llm("user", "system", verbose=False):
            pass


@pytest.mark.asyncio
async def test_gateway_text_projection_preserves_reasoning_without_losing_terminal_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = TraitTutorGateway()
    request = GatewayRequest(prompt="x", system_prompt="y", purpose="agent:test")

    async def typed_stream(_request: GatewayRequest):
        yield GatewayStreamEvent(type="reasoning", text="private reasoning")
        yield GatewayStreamEvent(type="text", text="answer")
        yield GatewayStreamEvent(
            type="final",
            finish_reason="stop",
            receipt=GatewayReceipt(
                request_id="projection",
                purpose="agent:test",
                model="test",
                provider="test",
                route="test",
                latency_ms=1,
                timeout_seconds=1,
                response_format_applied=False,
                tools_applied=0,
                attachments_applied=0,
            ),
        )

    monkeypatch.setattr(gateway, "stream", typed_stream)

    chunks = [chunk async for chunk in gateway.stream_text(request, include_reasoning=True)]

    assert chunks == ["<think>", "private reasoning", "</think>", "answer"]
