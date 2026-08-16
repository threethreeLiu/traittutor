from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from traittutor.gateway.provider_smoke import verify_gateway_provider
from traittutor.gateway.service import (
    GatewayReceipt,
    GatewayRequest,
    GatewayResponse,
    GatewayStreamEvent,
)


def _receipt(*, latency_ms: int = 3) -> GatewayReceipt:
    return GatewayReceipt(
        request_id="request",
        purpose="release:gateway-provider-smoke",
        model="test-model",
        provider="test-provider",
        route="test-route",
        latency_ms=latency_ms,
        timeout_seconds=60,
        response_format_applied=False,
        tools_applied=0,
        attachments_applied=0,
    )


class _Gateway:
    async def complete(self, request: GatewayRequest) -> GatewayResponse:
        return GatewayResponse(
            request_id="request",
            content="OK",
            model="test-model",
            purpose=request.purpose,
            latency_ms=3,
            receipt=_receipt(),
            usage={"total_tokens": 4},
        )

    async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayStreamEvent]:
        del request
        yield GatewayStreamEvent(type="text", text="OK")
        yield GatewayStreamEvent(type="usage", usage={"total_tokens": 5})
        yield GatewayStreamEvent(type="final", receipt=_receipt(latency_ms=4))


@pytest.mark.asyncio
async def test_provider_smoke_requires_both_typed_gateway_modes() -> None:
    result = await verify_gateway_provider(_Gateway())  # type: ignore[arg-type]

    assert result.complete_total_tokens == 4
    assert result.stream_total_tokens == 5
    assert result.stream_latency_ms == 4
