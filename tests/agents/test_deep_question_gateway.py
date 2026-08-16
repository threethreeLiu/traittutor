"""Gateway coverage for the browser-facing Deep Question model loop."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from traittutor.agents.question.pipeline import QuestionPipeline
from traittutor.core.context import UnifiedContext
from traittutor.gateway import GatewayReceipt, GatewayStreamEvent
from traittutor.services.llm.capabilities import supports_tools
from traittutor.services.llm.config import LLMConfig


def _pipeline() -> QuestionPipeline:
    pipeline = object.__new__(QuestionPipeline)
    pipeline.llm_config = LLMConfig(
        model="question-model",
        api_key="server-secret",
        binding="openai",
        provider_name="openai",
    )
    pipeline.reasoning_effort = None
    return pipeline


def _context() -> UnifiedContext:
    return UnifiedContext(
        session_id="question-session",
        metadata={
            "gateway_owner_id": "question-owner",
            "gateway_cancellation_event": asyncio.Event(),
        },
    )


@pytest.mark.asyncio
async def test_deep_question_builds_server_scoped_gateway_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []

    class Gateway:
        async def stream(self, request: Any):
            requests.append(request)
            yield GatewayStreamEvent(type="text", text="``FINISH`` question ready")
            yield GatewayStreamEvent(
                type="final",
                finish_reason="stop",
                receipt=GatewayReceipt(
                    request_id="question-request",
                    purpose=request.purpose,
                    model="question-model",
                    provider="openai",
                    route="openai",
                    latency_ms=1,
                    timeout_seconds=180.0,
                    response_format_applied=False,
                    tools_applied=0,
                    attachments_applied=0,
                ),
            )

    monkeypatch.setattr("traittutor.core.agentic.gateway_client.get_gateway", lambda: Gateway())
    context = _context()
    client = _pipeline()._build_client_for_context(context)

    response = await client.chat.completions.create(
        model="question-model",
        messages=[{"role": "user", "content": "make a question"}],
        stream=True,
        max_tokens=321,
    )
    frames = [frame async for frame in response]

    assert "".join(frame.choices[0].delta.content or "" for frame in frames) == (
        "``FINISH`` question ready"
    )
    assert requests[0].purpose == "question:agentic_loop"
    assert requests[0].user_id == "question-owner"
    assert requests[0].max_tokens == 321
    assert requests[0].cancellation_event is context.metadata["gateway_cancellation_event"]


@pytest.mark.asyncio
async def test_gateway_agentic_stream_flushes_visible_tail_before_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Gateway:
        async def stream(self, request: Any):
            yield GatewayStreamEvent(type="text", text="answer x < 5")
            yield GatewayStreamEvent(type="usage", usage={"total_tokens": 3})
            yield GatewayStreamEvent(
                type="final",
                finish_reason="stop",
                receipt=GatewayReceipt(
                    request_id="question-tail",
                    purpose=request.purpose,
                    model="question-model",
                    provider="openai",
                    route="openai",
                    latency_ms=1,
                    timeout_seconds=180.0,
                    response_format_applied=False,
                    tools_applied=0,
                    attachments_applied=0,
                ),
            )

    monkeypatch.setattr("traittutor.core.agentic.gateway_client.get_gateway", lambda: Gateway())
    response = (
        await _pipeline()
        ._build_client_for_context(_context())
        .chat.completions.create(
            model="question-model",
            messages=[{"role": "user", "content": "compare"}],
            stream=True,
        )
    )
    frames = [frame async for frame in response]

    assert "".join(frame.choices[0].delta.content or "" for frame in frames) == "answer x < 5"
    finish_index = next(
        index for index, frame in enumerate(frames) if frame.choices[0].finish_reason == "stop"
    )
    tail_index = max(index for index, frame in enumerate(frames) if frame.choices[0].delta.content)
    assert tail_index < finish_index


def test_deep_question_gateway_requires_server_owned_scope() -> None:
    with pytest.raises(RuntimeError, match="server-owned turn and cancellation scope"):
        _pipeline()._build_client_for_context(UnifiedContext(session_id="direct-call"))


@pytest.mark.parametrize(
    "binding",
    ["gemini", "zhipu", "stepfun", "xiaomi_mimo", "nvidia_nim", "qianfan"],
)
def test_registered_cloud_openai_compatible_providers_keep_typed_tools(binding: str) -> None:
    assert supports_tools(binding, "provider-model") is True


@pytest.mark.parametrize("binding", ["ollama", "vllm", "lm_studio", "llama_cpp"])
def test_local_openai_compatible_providers_remain_tool_disabled(binding: str) -> None:
    assert supports_tools(binding, "local-model") is False
