"""One audited model boundary for product-facing agent calls."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
import uuid
from typing import Any

from traittutor.services.llm.client import LLMClient
from traittutor.services.llm.config import LLMConfig, get_llm_config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GatewayRequest:
    prompt: str
    system_prompt: str
    purpose: str
    user_id: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)
    reasoning_effort: str | None = None
    response_format: dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayResponse:
    request_id: str
    content: str
    model: str
    purpose: str
    latency_ms: int


class TraitTutorGateway:
    """Gateway facade that keeps credentials and provider details server-side."""

    async def complete(self, request: GatewayRequest) -> GatewayResponse:
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        config = get_llm_config()
        if request.reasoning_effort:
            config = config.model_copy({"reasoning_effort": request.reasoning_effort})
        kwargs: dict[str, Any] = {}
        if request.response_format is not None:
            kwargs["response_format"] = request.response_format
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        content = await LLMClient(config).complete(
            request.prompt,
            system_prompt=request.system_prompt,
            history=request.history or None,
            **kwargs,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "gateway_complete request_id=%s purpose=%s model=%s latency_ms=%s user_id=%s",
            request_id,
            request.purpose,
            config.model,
            latency_ms,
            request.user_id or "anonymous",
        )
        try:
            from traittutor.multi_user.audit import log_usage
            log_usage("model", config.model, "gateway_complete", {"purpose": request.purpose, "latency_ms": latency_ms})
        except Exception:
            pass
        return GatewayResponse(
            request_id=request_id,
            content=content,
            model=config.model,
            purpose=request.purpose,
            latency_ms=latency_ms,
        )


_gateway: TraitTutorGateway | None = None


def get_gateway() -> TraitTutorGateway:
    global _gateway
    if _gateway is None:
        _gateway = TraitTutorGateway()
    return _gateway
