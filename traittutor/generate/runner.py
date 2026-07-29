"""Structured LLM runner shared by TraitTutor generation modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from traittutor.services.llm import LLMConfigError, get_llm_config
from traittutor.gateway import GatewayRequest, get_gateway
from traittutor.multi_user.context import get_current_user
from traittutor.utils.json_parser import parse_json_response

from .catalog import PromptDefinition

CompletionFn = Callable[..., Awaitable[str]]


class GenerationConfigurationError(RuntimeError):
    """Raised when real generation is requested with no configured LLM."""


class StructuredOutputError(ValueError):
    """Raised when a model response cannot satisfy the expected JSON contract."""


@dataclass(frozen=True)
class LLMRunMetadata:
    model: str
    provider: str
    prompt_name: str
    prompt_signature: str
    reasoning_effort: str


async def run_structured_prompt(
    prompt: PromptDefinition,
    *,
    validate: Callable[[Mapping[str, Any]], None],
    completion: CompletionFn | None = None,
) -> tuple[dict[str, Any], LLMRunMetadata]:
    """Run one strict JSON prompt through the active TraitTutor LLM setting.

    ``reasoning_effort`` is deliberately raised to ``high`` for this product
    surface. The existing provider adapter strips unsupported fields, so this
    does not introduce provider-specific branching here.
    """
    try:
        config = get_llm_config()
    except LLMConfigError as exc:
        raise GenerationConfigurationError(str(exc)) from exc

    kwargs: dict[str, Any] = {"reasoning_effort": "high"}
    if prompt.temperature is not None:
        kwargs["temperature"] = prompt.temperature
    if prompt.max_output_tokens is not None:
        kwargs["max_tokens"] = prompt.max_output_tokens
    if prompt.json_schema is not None:
        kwargs["response_format"] = {"type": "json_object"}

    if completion is not None:
        raw = await completion(prompt.user_prompt, system_prompt=prompt.system_prompt, **kwargs)
    else:
        current_user = get_current_user()
        response = await get_gateway().complete(
            GatewayRequest(
                prompt=prompt.user_prompt,
                system_prompt=prompt.system_prompt,
                purpose=f"generate:{prompt.name}",
                user_id=current_user.id,
                reasoning_effort="high",
                response_format=kwargs.get("response_format"),
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
            )
        )
        raw = response.content
    parsed = parse_json_response(raw, fallback=None)
    if not isinstance(parsed, dict):
        raise StructuredOutputError("LLM returned invalid JSON object")
    try:
        validate(parsed)
    except (TypeError, ValueError, KeyError) as exc:
        raise StructuredOutputError(str(exc)) from exc
    return dict(parsed), LLMRunMetadata(
        model=config.model,
        provider=config.provider_name,
        prompt_name=prompt.name,
        prompt_signature=prompt.signature,
        reasoning_effort="high",
    )


__all__ = [
    "GenerationConfigurationError",
    "LLMRunMetadata",
    "StructuredOutputError",
    "run_structured_prompt",
]
