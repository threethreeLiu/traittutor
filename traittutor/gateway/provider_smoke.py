"""Small real-provider acceptance probe for the typed Gateway boundary."""

from __future__ import annotations

from dataclasses import dataclass

from .service import GatewayMessage, GatewayRequest, TraitTutorGateway


@dataclass(frozen=True, slots=True)
class GatewayProviderSmokeResult:
    complete_model: str
    complete_latency_ms: int
    complete_total_tokens: int | None
    stream_model: str
    stream_latency_ms: int
    stream_total_tokens: int | None


async def verify_gateway_provider(
    gateway: TraitTutorGateway | None = None,
) -> GatewayProviderSmokeResult:
    """Require non-empty complete/stream output and redacted final receipts."""

    active_gateway = gateway or TraitTutorGateway()
    request = GatewayRequest(
        prompt="Reply with OK.",
        system_prompt="Return a minimal availability response.",
        purpose="release:gateway-provider-smoke",
        messages=(
            GatewayMessage(role="system", content="Return a minimal availability response."),
            GatewayMessage(role="user", content="Reply with OK."),
        ),
        user_id="system-release-smoke",
        # Reasoning providers (e.g. DeepSeek V4 Flash, GLM, Kimi) emit a
        # `thinking` content block before any `text`; with max_tokens=8 the
        # whole budget is consumed by thinking and the response carries no
        # text, failing the smoke for a healthy provider. 512 leaves room to
        # think and still answer the trivial "Reply with OK." prompt.
        max_tokens=512,
        max_retries=0,
        timeout_seconds=60,
    )
    response = await active_gateway.complete(request)
    if not response.content.strip() or response.receipt is None:
        raise RuntimeError("Gateway complete smoke returned no verified completion")

    stream_text: list[str] = []
    stream_total: int | None = None
    stream_receipt = None
    async for event in active_gateway.stream(request):
        if event.type == "text" and event.text:
            stream_text.append(event.text)
        elif event.type == "usage" and event.usage:
            total = event.usage.get("total_tokens")
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                stream_total = total
        elif event.type == "final":
            stream_receipt = event.receipt
    if not "".join(stream_text).strip() or stream_receipt is None:
        raise RuntimeError("Gateway stream smoke returned no verified final response")

    complete_total = response.usage.get("total_tokens")
    if not isinstance(complete_total, int) or isinstance(complete_total, bool):
        complete_total = None
    return GatewayProviderSmokeResult(
        complete_model=response.receipt.model,
        complete_latency_ms=response.receipt.latency_ms,
        complete_total_tokens=complete_total,
        stream_model=stream_receipt.model,
        stream_latency_ms=stream_receipt.latency_ms,
        stream_total_tokens=stream_total,
    )


__all__ = ["GatewayProviderSmokeResult", "verify_gateway_provider"]
