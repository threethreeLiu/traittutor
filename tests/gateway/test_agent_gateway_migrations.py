"""Focused regressions for Agent paths retired from the LLM factory."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.agents.notebook.analysis_agent import NotebookAnalysisAgent
from traittutor.capabilities.explore_context.explorer import ContextExplorer
from traittutor.gateway import (
    GatewayReceipt,
    GatewayRequest,
    GatewayStreamEvent,
    GatewayToolCall,
    gateway_config_with_overrides,
)
from traittutor.services.llm.client import LLMClient
from traittutor.services.llm.config import LLMConfig
from traittutor.services.llm.exceptions import LLMConfigError
from traittutor.services.llm.provider_core.base import LLMResponse
from traittutor.tools.brainstorm import brainstorm
from traittutor.tools.question.question_extractor import extract_questions_with_llm
from traittutor.tools.reason import reason


def _config() -> LLMConfig:
    return LLMConfig(
        model="gateway-model",
        api_key="secret",
        base_url="https://gateway.invalid/v1",
        binding="openai",
        provider_name="openai",
    )


def _receipt(purpose: str) -> GatewayReceipt:
    return GatewayReceipt(
        request_id="migration-test",
        purpose=purpose,
        model="gateway-model",
        provider="openai",
        route="openai",
        latency_ms=1,
        timeout_seconds=10,
        response_format_applied=False,
        tools_applied=0,
        attachments_applied=0,
    )


def test_gateway_override_infers_anthropic_from_model_without_binding() -> None:
    config = gateway_config_with_overrides(
        base=_config(),
        model="claude-3-5-sonnet",
        api_key="anthropic-secret",
        base_url="https://api.anthropic.com/v1",
    )

    assert config.binding == "anthropic"
    assert config.provider_name == "anthropic"
    assert config.provider_mode == "standard"


def test_gateway_override_infers_provider_without_active_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_catalog() -> LLMConfig:
        raise LLMConfigError("no active model")

    monkeypatch.setattr("traittutor.gateway.config.get_llm_config", missing_catalog)

    config = gateway_config_with_overrides(
        model="claude-3-5-sonnet",
        api_key="anthropic-secret",
        base_url="https://api.anthropic.com/v1",
    )

    assert config.binding == "anthropic"
    assert config.provider_name == "anthropic"


def test_gateway_override_infers_ollama_from_local_endpoint_without_binding() -> None:
    config = gateway_config_with_overrides(
        base=_config(),
        model="llama3.2",
        api_key="ollama",
        base_url="http://localhost:11434/v1",
    )

    assert config.binding == "ollama"
    assert config.provider_name == "ollama"
    assert config.provider_mode == "local"


def test_gateway_override_infers_openrouter_from_api_key_without_binding() -> None:
    config = gateway_config_with_overrides(
        base=_config(),
        api_key="sk-or-secret",
    )

    assert config.binding == "openrouter"
    assert config.provider_name == "openrouter"
    assert config.provider_mode == "gateway"
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.effective_url == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_notebook_analysis_stage_uses_gateway_text_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[GatewayRequest, bool]] = []

    class Gateway:
        async def stream_text(self, request: GatewayRequest, *, include_reasoning: bool):
            captured.append((request, include_reasoning))
            yield "notebook answer"

    monkeypatch.setattr("traittutor.agents.notebook.analysis_agent.get_gateway", lambda: Gateway())
    agent = object.__new__(NotebookAnalysisAgent)
    agent.llm_config = _config()

    chunks = [
        chunk
        async for chunk in agent._stream_gateway(
            prompt="question",
            system_prompt="system",
            temperature=0.2,
            max_tokens=900,
            purpose="agent:notebook-analysis:thinking",
        )
    ]

    assert chunks == ["notebook answer"]
    request, include_reasoning = captured[0]
    assert request.purpose == "agent:notebook-analysis:thinking"
    assert request.max_tokens == 900
    assert include_reasoning is True


@pytest.mark.asyncio
async def test_context_explorer_projects_gateway_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Gateway:
        async def stream(self, request: GatewayRequest):
            assert request.purpose == "agent:explore-context:loop"
            assert request.tools[0].name == "read_source"
            yield GatewayStreamEvent(type="reasoning", text="inspect")
            yield GatewayStreamEvent(type="text", text="grounded")
            yield GatewayStreamEvent(
                type="tool_call",
                tool_call=GatewayToolCall(
                    id="call-1", name="read_source", arguments={"source_id": "at-1"}
                ),
            )
            yield GatewayStreamEvent(
                type="final", finish_reason="tool_calls", receipt=_receipt(request.purpose)
            )

    class Stream:
        def __init__(self) -> None:
            self.thoughts: list[str] = []

        async def thinking(self, text: str, **_kwargs: Any) -> None:
            self.thoughts.append(text)

    monkeypatch.setattr(
        "traittutor.capabilities.explore_context.explorer.get_gateway", lambda: Gateway()
    )
    explorer = object.__new__(ContextExplorer)
    explorer.model = "gateway-model"
    explorer.reasoning_effort = None
    explorer.llm_config = _config()
    stream = Stream()

    result = await explorer._call_llm(
        [{"role": "user", "content": "inspect source"}],
        [
            {
                "type": "function",
                "function": {
                    "name": "read_source",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
        {},
        stream,
    )

    assert result.text == "grounded"
    assert result.tool_calls == [
        {"id": "call-1", "name": "read_source", "arguments": '{"source_id": "at-1"}'}
    ]
    assert stream.thoughts == ["inspect", "grounded"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "argument", "purpose"),
    [
        (reason, {"query": "prove it"}, "tool:reason"),
        (brainstorm, {"topic": "ideas"}, "tool:brainstorm"),
    ],
)
async def test_agent_tools_use_gateway_text_projection(
    monkeypatch: pytest.MonkeyPatch,
    call: Any,
    argument: dict[str, str],
    purpose: str,
) -> None:
    captured: list[GatewayRequest] = []

    class Gateway:
        async def stream_text(self, request: GatewayRequest, *, include_reasoning: bool):
            assert include_reasoning is True
            captured.append(request)
            yield "gateway tool answer"

    monkeypatch.setattr("traittutor.gateway.get_llm_config", lambda: _config(), raising=False)
    monkeypatch.setattr("traittutor.services.llm.config.get_llm_config", lambda: _config())
    monkeypatch.setattr("traittutor.gateway.get_gateway", lambda: Gateway())

    result = await call(**argument)

    assert result["answer"] == "gateway tool answer"
    assert captured[0].purpose == purpose


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "argument"),
    [
        (reason, {"query": "prove it"}),
        (brainstorm, {"topic": "ideas"}),
    ],
)
async def test_agent_tools_accept_complete_explicit_config_without_active_catalog(
    monkeypatch: pytest.MonkeyPatch,
    call: Any,
    argument: dict[str, str],
) -> None:
    captured: list[GatewayRequest] = []

    class Gateway:
        async def stream_text(self, request: GatewayRequest, *, include_reasoning: bool):
            captured.append(request)
            yield "explicit answer"

    def missing_catalog() -> LLMConfig:
        raise LLMConfigError("no active model")

    monkeypatch.setattr("traittutor.gateway.config.get_llm_config", missing_catalog)
    monkeypatch.setattr("traittutor.services.llm.config.get_llm_config", missing_catalog)
    monkeypatch.setattr("traittutor.gateway.get_gateway", lambda: Gateway())

    result = await call(
        **argument,
        model="explicit-model",
        api_key="explicit-key",
        base_url="https://explicit.invalid/v1",
    )

    assert result["answer"] == "explicit answer"
    assert captured[0].llm_config is not None
    assert captured[0].llm_config.model == "explicit-model"
    assert captured[0].llm_config.api_key == "explicit-key"


def test_question_extractor_uses_gateway_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: list[GatewayRequest] = []

    class Gateway:
        async def complete(self, request: GatewayRequest):
            captured.append(request)
            return SimpleNamespace(
                content='{"questions":[{"question_number":"1","question_text":"Q"}]}'
            )

    def missing_catalog() -> LLMConfig:
        raise LLMConfigError("no active model")

    monkeypatch.setattr(
        "traittutor.tools.question.question_extractor.get_llm_config",
        missing_catalog,
        raising=False,
    )
    monkeypatch.setattr("traittutor.gateway.config.get_llm_config", missing_catalog)
    monkeypatch.setattr(
        "traittutor.tools.question.question_extractor.get_gateway", lambda: Gateway()
    )
    monkeypatch.setattr(
        "traittutor.tools.question.question_extractor.get_agent_params",
        lambda _name: {"temperature": 0.1, "max_tokens": 500},
    )

    questions = extract_questions_with_llm(
        "1. Q",
        None,
        tmp_path,
        api_key="secret",
        base_url="https://gateway.invalid/v1",
        model="gateway-model",
        binding="openai",
    )

    assert questions[0]["question_number"] == "1"
    assert captured[0].purpose == "agent:question:mimic-source-extraction"


@pytest.mark.asyncio
async def test_llm_client_public_completion_enters_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[GatewayRequest] = []

    class Gateway:
        async def complete(self, request: GatewayRequest):
            captured.append(request)
            return SimpleNamespace(content="compat answer")

    monkeypatch.setattr("traittutor.gateway.get_gateway", lambda: Gateway())

    answer = await LLMClient(_config()).complete(
        "prompt", system_prompt="system", temperature=0.2, max_tokens=120
    )

    assert answer == "compat answer"
    assert captured[0].purpose == "compat:llm-client"
    assert captured[0].max_tokens == 120


@pytest.mark.asyncio
async def test_gateway_provider_transport_never_reintroduces_inner_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class Provider:
        async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
            captured.update(kwargs)
            return LLMResponse(content="transport answer", usage={"total_tokens": 3})

    monkeypatch.setattr(
        "traittutor.services.llm.client.get_runtime_provider", lambda _config: Provider()
    )

    content, usage, _images = await LLMClient(_config()).complete_with_usage(
        "prompt",
        history=[{"role": "user", "content": "prompt"}],
        max_retries=8,
        temperature=0.2,
    )

    assert content == "transport answer"
    assert usage == {"total_tokens": 3}
    assert captured["retry_delays"] == ()
