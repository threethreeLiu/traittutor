from __future__ import annotations

from dataclasses import replace

import pytest

from traittutor.generate.catalog import PromptDefinition, load_prompt
from traittutor.gateway import GatewayResponse
from traittutor.generate.runner import StructuredOutputError, run_structured_prompt
from traittutor.services.llm.config import LLMConfig, set_scoped_llm_config


def test_catalog_renders_variables_and_fingerprints_asset():
    prompt = load_prompt(
        "flashcards/km-card-note.md",
        {"detected_primary_language": "zh", "text": "材料"},
    )

    assert prompt.signature
    assert prompt.json_schema is not None
    assert prompt.name == "km-card-note"


@pytest.mark.asyncio
async def test_runner_uses_high_reasoning_and_validates_json():
    prompt = PromptDefinition(
        name="test", path=None, system_prompt="system", user_prompt="user", json_schema={},
        temperature=0, max_output_tokens=100, reasoning_effort="low", signature="abc",
    )
    token = set_scoped_llm_config(LLMConfig(model="test-model", api_key="key", provider_name="test"))
    calls: list[dict] = []

    async def completion(*_args, **kwargs):
        calls.append(kwargs)
        return '{"items": []}'

    try:
        payload, metadata = await run_structured_prompt(
            prompt, validate=lambda value: value["items"], completion=completion
        )
    finally:
        from traittutor.services.llm.config import reset_scoped_llm_config
        reset_scoped_llm_config(token)

    assert payload == {"items": []}
    assert metadata.reasoning_effort == "high"
    assert calls[0]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_runner_honors_material_analysis_medium_reasoning():
    prompt = PromptDefinition(name="analysis", path=None, system_prompt="system", user_prompt="user", json_schema={}, temperature=None, max_output_tokens=None, reasoning_effort="high", signature="analysis")
    token = set_scoped_llm_config(LLMConfig(model="test-model", api_key="key", provider_name="test"))
    calls: list[dict] = []

    async def completion(*_args, **kwargs):
        calls.append(kwargs)
        return '{"subject": "mathematics"}'

    try:
        _, metadata = await run_structured_prompt(prompt, validate=lambda value: value["subject"], completion=completion, reasoning_effort="medium")
    finally:
        from traittutor.services.llm.config import reset_scoped_llm_config
        reset_scoped_llm_config(token)
    assert metadata.reasoning_effort == "medium"
    assert calls[0]["reasoning_effort"] == "medium"


@pytest.mark.asyncio
async def test_runner_rejects_schema_invalid_output():
    prompt = PromptDefinition(
        name="test", path=None, system_prompt="system", user_prompt="user", json_schema={},
        temperature=None, max_output_tokens=None, reasoning_effort="high", signature="abc",
    )
    token = set_scoped_llm_config(LLMConfig(model="test-model", api_key="key", provider_name="test"))
    try:
        with pytest.raises(StructuredOutputError):
            await run_structured_prompt(
                prompt, validate=lambda value: value["missing"], completion=lambda *_args, **_kwargs: _async_text("{}")
            )
    finally:
        from traittutor.services.llm.config import reset_scoped_llm_config
        reset_scoped_llm_config(token)


@pytest.mark.asyncio
async def test_runner_rotates_to_a_backup_model_after_a_quota_error(monkeypatch):
    from traittutor.generate import runner

    prompt = PromptDefinition(
        name="test", path=None, system_prompt="system", user_prompt="user", json_schema={},
        temperature=None, max_output_tokens=None, reasoning_effort="high", signature="abc",
    )
    primary = LLMConfig(model="primary", api_key="key", provider_name="primary")
    fallback = LLMConfig(model="fallback", api_key="key", provider_name="fallback")
    seen: list[str] = []

    class FakeGateway:
        async def complete(self, request):
            seen.append(request.llm_config.model)
            if request.llm_config.model == "primary":
                raise RuntimeError("rate_limit_error code 1308")
            return GatewayResponse("request", '{"items": []}', request.llm_config.model, "generate:test", 1)

    monkeypatch.setattr(runner, "_generation_route_configs", lambda _primary: [primary, fallback])
    monkeypatch.setattr(runner, "get_gateway", lambda: FakeGateway())
    monkeypatch.setattr(runner, "get_current_user", lambda: type("User", (), {"id": "test-user"})())
    token = set_scoped_llm_config(primary)
    try:
        payload, metadata = await run_structured_prompt(
            prompt, validate=lambda value: value["items"]
        )
    finally:
        from traittutor.services.llm.config import reset_scoped_llm_config
        reset_scoped_llm_config(token)

    assert payload == {"items": []}
    assert metadata.model == "fallback"
    assert seen == ["primary", "fallback"]


async def _async_text(value: str) -> str:
    return value
