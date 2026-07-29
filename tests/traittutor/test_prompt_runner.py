from __future__ import annotations

from dataclasses import replace

import pytest

from traittutor.generate.catalog import PromptDefinition, load_prompt
from traittutor.generate.runner import StructuredOutputError, run_structured_prompt
from traittutor.services.llm.config import LLMConfig, set_scoped_llm_config


def test_catalog_renders_variables_and_fingerprints_asset():
    prompt = load_prompt(
        "flashcards/km-card-note.yml",
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


async def _async_text(value: str) -> str:
    return value
