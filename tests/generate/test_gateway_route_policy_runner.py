"""Runner-level contract for the opt-in bounded Gateway route policy."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from traittutor.gateway import GatewayReceipt, GatewayResponse
from traittutor.generate.catalog import PromptDefinition
from traittutor.generate.runner import run_structured_prompt
from traittutor.services.llm.config import LLMConfig


def _config() -> LLMConfig:
    return LLMConfig(
        model="policy-model",
        api_key="server-only-test-key",
        base_url="https://private.gateway.test/v1",
        binding="openai",
        provider_name="test-provider",
    )


def _receipt(purpose: str) -> GatewayReceipt:
    return GatewayReceipt(
        request_id="safe-id",
        purpose=purpose,
        model="policy-model",
        provider="test-provider",
        route="test-route",
        latency_ms=1,
        timeout_seconds=1,
        response_format_applied=True,
        tools_applied=0,
        attachments_applied=0,
    )


@pytest.mark.asyncio
async def test_enabled_runner_policy_disables_nested_provider_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    requests = []

    class Gateway:
        async def complete(self, request: object) -> GatewayResponse:
            requests.append(request)
            return GatewayResponse(
                request_id="safe-id",
                content='{"answer":"ok"}',
                model=config.model,
                purpose="generate:policy-test",
                latency_ms=1,
                receipt=_receipt("generate:policy-test"),
            )

    monkeypatch.setattr("traittutor.generate.runner.get_llm_config", lambda: config)
    monkeypatch.setattr(
        "traittutor.gateway.routing.generation_route_configs", lambda _primary: (config,)
    )
    monkeypatch.setattr("traittutor.generate.runner.get_gateway", lambda: Gateway())
    prompt = PromptDefinition(
        name="policy-test",
        path=None,
        system_prompt="server system",
        user_prompt="server prompt",
        json_schema={"type": "object"},
        temperature=0.2,
        max_output_tokens=100,
        reasoning_effort="high",
        signature="safe-signature",
    )

    payload, metadata = await run_structured_prompt(
        prompt,
        validate=lambda value: value["answer"] == "ok" or (_ for _ in ()).throw(ValueError()),
    )

    assert payload == {"answer": "ok"}
    assert metadata.model == config.model
    assert len(requests) == 1
    request = requests[0]
    assert request.purpose == "generate:policy-test"
    assert request.max_retries == 0
    assert 0 < request.timeout_seconds <= 240


@pytest.mark.asyncio
async def test_structured_retry_receives_the_previous_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    requests = []

    class Gateway:
        async def complete(self, request: object) -> GatewayResponse:
            requests.append(request)
            content = '{"wrong":"shape"}' if len(requests) == 1 else '{"answer":"ok"}'
            return GatewayResponse(
                request_id=f"safe-id-{len(requests)}",
                content=content,
                model=config.model,
                purpose="generate:policy-test",
                latency_ms=1,
                receipt=_receipt("generate:policy-test"),
            )

    monkeypatch.setattr("traittutor.generate.runner.get_llm_config", lambda: config)
    monkeypatch.setattr(
        "traittutor.gateway.routing.generation_route_configs", lambda _primary: (config,)
    )
    monkeypatch.setattr("traittutor.generate.runner.get_gateway", lambda: Gateway())
    prompt = PromptDefinition(
        name="policy-test",
        path=None,
        system_prompt="server system",
        user_prompt="server prompt",
        json_schema={"type": "object"},
        temperature=0.2,
        max_output_tokens=100,
        reasoning_effort="high",
        signature="safe-signature",
    )

    def validate(value: Mapping[str, object]) -> None:
        if value.get("answer") != "ok":
            raise ValueError("missing answer field")

    payload, _metadata = await run_structured_prompt(prompt, validate=validate)

    assert payload == {"answer": "ok"}
    assert len(requests) == 2
    retry_prompt = requests[1].system_prompt
    assert "CORRECTION FOR THIS RETRY" in retry_prompt
    assert "missing answer field" in retry_prompt
