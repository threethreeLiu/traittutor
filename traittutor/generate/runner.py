"""Structured LLM runner shared by TraitTutor generation modules."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping

from traittutor.services.llm import LLMConfigError, get_llm_config
from traittutor.services.llm.config import LLMConfig
from traittutor.services.model_selection import LLMSelection
from traittutor.services.model_selection.runtime import resolve_llm_config_for_selection
from traittutor.services.models.local_catalog import load_local_llm
from traittutor.gateway import GatewayRequest, get_gateway
from traittutor.multi_user.context import get_current_user
from traittutor.utils.json_parser import parse_json_response

from .catalog import PromptDefinition

CompletionFn = Callable[..., Awaitable[str]]
logger = logging.getLogger(__name__)


class GenerationConfigurationError(RuntimeError):
    """Raised when real generation is requested with no configured LLM."""


class StructuredOutputError(ValueError):
    """Raised when a model response cannot satisfy the expected JSON contract."""


class GenerationModelExhaustedError(RuntimeError):
    """No configured generation route could produce a valid response."""

    user_message = "All configured generation models are temporarily unavailable. Please try again later."


@dataclass(frozen=True)
class LLMRunMetadata:
    model: str
    provider: str
    prompt_name: str
    prompt_signature: str
    reasoning_effort: str


def _generation_route_configs(primary: LLMConfig) -> list[LLMConfig]:
    """Return active route first, then every other configured route once.

    This deliberately reads the local model catalog rather than persisting a
    rotated choice: Settings remains the user's default, while a single
    generation can recover from a quota, provider, or schema failure.
    """
    routes = [primary]
    try:
        catalog = load_local_llm() or {}
        active = str(catalog.get("active_profile_id") or "")
        profiles = list(catalog.get("profiles") or [])
        ordered = sorted(profiles, key=lambda profile: 0 if profile.get("id") == active else 1)
        for profile in ordered:
            profile_id = str(profile.get("id") or "")
            for model in profile.get("models") or []:
                model_id = str(model.get("id") or "")
                if not profile_id or not model_id:
                    continue
                try:
                    candidate = resolve_llm_config_for_selection(LLMSelection(profile_id, model_id))
                except Exception:
                    logger.warning("Skipping unusable generation route %s/%s", profile_id, model_id, exc_info=True)
                    continue
                if not candidate.api_key or any(
                    (item.model, item.effective_url, item.binding) == (candidate.model, candidate.effective_url, candidate.binding)
                    for item in routes
                ):
                    continue
                routes.append(candidate)
    except Exception:
        logger.warning("Unable to enumerate generation fallback routes", exc_info=True)
    return routes


def _retryable_same_route(exc: Exception) -> bool:
    message = str(exc).lower()
    # A long quota window should rotate immediately.  Short-lived transport and
    # upstream errors get one quick retry before a different route is used.
    if any(marker in message for marker in ("quota", "5 小时", "5小时", "1308", "authentication", "invalid api key")):
        return False
    return any(marker in message for marker in ("timeout", "timed out", "connection", "temporar", "rate limit", "429", "500", "502", "503", "504"))


async def run_structured_prompt(
    prompt: PromptDefinition,
    *,
    validate: Callable[[Mapping[str, Any]], None],
    completion: CompletionFn | None = None,
    reasoning_effort: str | None = None,
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

    selected_reasoning = reasoning_effort or "high"
    kwargs: dict[str, Any] = {"reasoning_effort": selected_reasoning}
    if prompt.temperature is not None:
        kwargs["temperature"] = prompt.temperature
    if prompt.max_output_tokens is not None:
        kwargs["max_tokens"] = prompt.max_output_tokens
    if prompt.json_schema is not None:
        kwargs["response_format"] = {"type": "json_object"}
    # Some OpenAI-compatible providers accept the response_format hint but do
    # not enforce it. Include the schema in the instruction as well, so strict
    # generation remains portable instead of relying on a provider-only flag.
    system_prompt = prompt.system_prompt
    if prompt.json_schema:
        system_prompt = (
            f"{system_prompt}\n\nReturn exactly one JSON object matching this schema: "
            f"{prompt.json_schema}"
        )

    if completion is not None:
        raw = await completion(prompt.user_prompt, system_prompt=system_prompt, **kwargs)
        return _validated_payload(raw, validate, config, prompt, selected_reasoning)

    current_user = get_current_user()
    failures: list[str] = []
    for route_index, route in enumerate(_generation_route_configs(config), start=1):
        # The underlying provider client has its own bounded retry. This retry
        # is a generation-level safety net for a transient final failure.
        for attempt in range(2):
            try:
                response = await get_gateway().complete(
                    GatewayRequest(
                        prompt=prompt.user_prompt,
                        system_prompt=system_prompt,
                        purpose=f"generate:{prompt.name}",
                        user_id=current_user.id,
                        reasoning_effort=selected_reasoning,
                        response_format=kwargs.get("response_format"),
                        temperature=kwargs.get("temperature"),
                        max_tokens=kwargs.get("max_tokens"),
                        llm_config=route,
                    )
                )
                return _validated_payload(response.content, validate, route, prompt, selected_reasoning)
            except Exception as exc:
                failures.append(f"{route.model}: {type(exc).__name__}")
                should_retry = attempt == 0 and (
                    isinstance(exc, StructuredOutputError) or _retryable_same_route(exc)
                )
                if should_retry:
                    await asyncio.sleep(0.35)
                    continue
                logger.warning(
                    "Generation route failed; rotating route=%s model=%s attempt=%s error=%s detail=%s",
                    route_index, route.model, attempt + 1, type(exc).__name__, str(exc)[:500],
                )
                break
    logger.error("All generation routes failed for prompt=%s failures=%s", prompt.name, failures)
    raise GenerationModelExhaustedError(GenerationModelExhaustedError.user_message)


def _validated_payload(
    raw: str,
    validate: Callable[[Mapping[str, Any]], None],
    config: LLMConfig,
    prompt: PromptDefinition,
    reasoning_effort: str = "high",
) -> tuple[dict[str, Any], LLMRunMetadata]:
    parsed = parse_json_response(raw, fallback=None)
    if not isinstance(parsed, dict):
        raise StructuredOutputError("LLM returned invalid JSON object")
    try:
        validate(parsed)
    except (TypeError, ValueError, KeyError) as exc:
        raise StructuredOutputError(str(exc)) from exc
    return dict(parsed), LLMRunMetadata(
        model=config.model, provider=config.provider_name, prompt_name=prompt.name,
        prompt_signature=prompt.signature, reasoning_effort=reasoning_effort,
    )


__all__ = [
    "GenerationConfigurationError",
    "GenerationModelExhaustedError",
    "LLMRunMetadata",
    "StructuredOutputError",
    "run_structured_prompt",
]
