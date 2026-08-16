"""Structured LLM runner shared by TraitTutor generation modules."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable, Mapping

from traittutor.gateway import GatewayMessage, GatewayRequest, get_gateway
from traittutor.gateway.quota_rotation import QuotaRotationExhaustedError
from traittutor.gateway.routing import GenerationRoutePolicy
from traittutor.multi_user.context import get_current_user
from traittutor.services.llm import LLMConfigError, get_llm_config
from traittutor.services.llm.config import LLMConfig
from traittutor.utils.json_parser import parse_json_response

from .catalog import PromptDefinition

CompletionFn = Callable[..., Awaitable[str]]
StructuredValidator = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
logger = logging.getLogger(__name__)


class GenerationConfigurationError(RuntimeError):
    """Raised when real generation is requested with no configured LLM."""


class StructuredOutputError(ValueError):
    """Raised when a model response cannot satisfy the expected JSON contract."""


class GenerationModelExhaustedError(RuntimeError):
    """No configured generation route could produce a valid response."""

    user_message = (
        "All configured generation models are temporarily unavailable. Please try again later."
    )


class GenerationStructuredOutputExhaustedError(RuntimeError):
    """Configured routes responded, but none satisfied the output contract."""

    user_message = (
        "The models responded, but the generated content did not pass validation. Please try again."
    )


@dataclass(frozen=True)
class LLMRunMetadata:
    model: str
    provider: str
    prompt_name: str
    prompt_signature: str
    reasoning_effort: str


def _retryable_same_route(exc: Exception) -> bool:
    message = str(exc).lower()
    # A long quota window should rotate immediately.  Short-lived transport and
    # upstream errors get one quick retry before a different route is used.
    if any(
        marker in message
        for marker in ("quota", "5 小时", "5小时", "1308", "authentication", "invalid api key")
    ):
        return False
    return any(
        marker in message
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "temporar",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
        )
    )


def _system_prompt_with_validation_feedback(
    system_prompt: str, validation_error: StructuredOutputError | None
) -> str:
    """Give a bounded retry the concrete contract violation to repair.

    The retry previously repeated the exact same request, so providers commonly
    returned the same malformed shape twice. Keep the feedback short and avoid
    including full model output or source material in the additional message.
    """

    if validation_error is None:
        return system_prompt
    detail = " ".join(str(validation_error).split())[:1200]
    return (
        f"{system_prompt}\n\n"
        "CORRECTION FOR THIS RETRY\n"
        "The previous JSON response was rejected by the server validator. "
        f"Fix this exact problem: {detail}\n"
        "Return a complete replacement JSON object. Do not explain the correction."
    )


async def run_structured_prompt(
    prompt: PromptDefinition,
    *,
    validate: StructuredValidator,
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

    return await _run_with_gateway_route_policy(
        config=config,
        prompt=prompt,
        validate=validate,
        selected_reasoning=selected_reasoning,
        kwargs=kwargs,
        system_prompt=system_prompt,
    )


async def _run_with_gateway_route_policy(
    *,
    config: LLMConfig,
    prompt: PromptDefinition,
    validate: StructuredValidator,
    selected_reasoning: str,
    kwargs: Mapping[str, Any],
    system_prompt: str,
) -> tuple[dict[str, Any], LLMRunMetadata]:
    """Use the opt-in bounded policy without nesting factory retries.

    Validation is intentionally inside the policy invocation: malformed JSON
    is a route outcome and may use the one bounded retry, rather than escaping
    after the provider call and silently defeating structured-output recovery.
    """
    current_user = get_current_user()
    validation_error: StructuredOutputError | None = None

    async def invoke(route: LLMConfig, remaining: float) -> tuple[dict[str, Any], LLMRunMetadata]:
        nonlocal validation_error
        response = await get_gateway().complete(
            GatewayRequest(
                prompt=prompt.user_prompt,
                system_prompt=_system_prompt_with_validation_feedback(
                    system_prompt, validation_error
                ),
                purpose=f"generate:{prompt.name}",
                messages=(
                    GatewayMessage(
                        role="system",
                        content=_system_prompt_with_validation_feedback(
                            system_prompt, validation_error
                        ),
                    ),
                    GatewayMessage(role="user", content=prompt.user_prompt),
                ),
                user_id=current_user.id,
                reasoning_effort=selected_reasoning,
                response_format=kwargs.get("response_format"),
                temperature=kwargs.get("temperature"),
                max_tokens=kwargs.get("max_tokens"),
                # The policy already bounds attempt count; the legacy factory
                # must issue one provider try for each of those attempts.
                max_retries=0,
                # This call runs inside ``GenerationRoutePolicy``'s bounded
                # rotation — opting out here prevents the gateway's general
                # quota rotation from double-rotating the same attempt.
                allow_quota_rotation=False,
                timeout_seconds=remaining,
                llm_config=route,
            )
        )
        try:
            return _validated_payload(response.content, validate, route, prompt, selected_reasoning)
        except StructuredOutputError as exc:
            validation_error = exc
            # Keep the concrete contract violation in the audit trail. The
            # route policy only propagates error type names, so without this
            # log a batch failure reads as an opaque ``StructuredOutputError``
            # and the offending validation rule is unrecoverable.
            logger.warning(
                "structured output validation failed model=%s prompt=%s detail=%s",
                route.model,
                prompt.name,
                str(exc),
            )
            raise

    try:
        result = await GenerationRoutePolicy(purpose=f"generate:{prompt.name}").run(
            config,
            invoke=invoke,
            same_route_retryable=lambda exc: (
                isinstance(exc, StructuredOutputError) or _retryable_same_route(exc)
            ),
        )
    except QuotaRotationExhaustedError as exc:
        if isinstance(exc.last_error, StructuredOutputError):
            raise GenerationStructuredOutputExhaustedError(
                GenerationStructuredOutputExhaustedError.user_message
            ) from exc.last_error
        raise GenerationModelExhaustedError(GenerationModelExhaustedError.user_message) from exc
    return result.value


def _validated_payload(
    raw: str,
    validate: StructuredValidator,
    config: LLMConfig,
    prompt: PromptDefinition,
    reasoning_effort: str = "high",
) -> tuple[dict[str, Any], LLMRunMetadata]:
    parsed = parse_json_response(raw, fallback=None)
    if not isinstance(parsed, dict):
        raise StructuredOutputError("LLM returned invalid JSON object")
    try:
        validated = validate(parsed)
    except (TypeError, ValueError, KeyError) as exc:
        raise StructuredOutputError(str(exc)) from exc
    payload = dict(validated) if isinstance(validated, Mapping) else dict(parsed)
    return payload, LLMRunMetadata(
        model=config.model,
        provider=config.provider_name,
        prompt_name=prompt.name,
        prompt_signature=prompt.signature,
        reasoning_effort=reasoning_effort,
    )


__all__ = [
    "GenerationConfigurationError",
    "GenerationModelExhaustedError",
    "GenerationStructuredOutputExhaustedError",
    "LLMRunMetadata",
    "StructuredOutputError",
    "run_structured_prompt",
]
