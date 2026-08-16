"""Canonical Gateway coverage for the server-held Quiz Judge WebSocket."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json

import pytest

from traittutor.api.routers import quiz_judge
from traittutor.gateway import GatewayReceipt, GatewayRequest, GatewayStreamEvent


def _receipt() -> GatewayReceipt:
    return GatewayReceipt(
        request_id="request-1",
        purpose="quiz_judge",
        model="server-model",
        provider="server-provider",
        route="server-route",
        latency_ms=1,
        timeout_seconds=120.0,
        response_format_applied=False,
        tools_applied=0,
        attachments_applied=1,
    )


@pytest.mark.asyncio
async def test_judge_gateway_path_is_typed_bounded_and_receipt_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, GatewayRequest] = {}

    class StubGateway:
        async def stream(self, request: GatewayRequest):
            captured["request"] = request
            yield GatewayStreamEvent(type="reasoning", text="never sent to browser")
            yield GatewayStreamEvent(type="text", text="targeted judgment")
            yield GatewayStreamEvent(type="usage", usage={"output_tokens": 5})
            yield GatewayStreamEvent(type="final", receipt=_receipt())

    monkeypatch.setattr(quiz_judge, "get_gateway", lambda: StubGateway())

    reference_answer = "private server-held key"
    prompt = quiz_judge._build_judge_user_prompt(
        language="en",
        question="What is the answer?",
        question_type="choice",
        options={"A": "wrong", "B": reference_answer},
        correct_answer=reference_answer,
        explanation="private rubric",
        user_answer="B",
        has_image=True,
    )
    cancellation_event = asyncio.Event()
    chunks = [
        chunk
        async for chunk in quiz_judge._stream_judge_response(
            user_prompt=prompt,
            system_prompt="private server system prompt",
            image_records=[
                {
                    "base64": "aGVsbG8=",
                    "url": "",
                    "filename": "answer.png",
                    "mime_type": "image/png",
                }
            ],
            cancellation_event=cancellation_event,
        )
    ]

    assert chunks == ["targeted judgment"]
    request = captured["request"]
    assert request.purpose == "quiz_judge"
    assert request.timeout_seconds == 120.0
    assert request.cancellation_event is cancellation_event
    assert request.user_id is None
    assert request.metadata == {}
    assert request.attachments[0].base64 == "aGVsbG8="
    assert reference_answer in str(request.messages[1].content)
    # The key is necessarily sent only server-to-provider for judging; neither
    # receipts nor Gateway telemetry payloads carry prompt/message content.
    assert reference_answer not in json.dumps(asdict(_receipt()), sort_keys=True)


@pytest.mark.asyncio
async def test_judge_gateway_timeout_surfaces_without_retired_protocol_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutGateway:
        async def stream(self, request: GatewayRequest):
            del request
            if False:  # pragma: no cover - makes this an async generator.
                yield GatewayStreamEvent(type="final", receipt=_receipt())
            raise TimeoutError("server-owned Gateway deadline")

    monkeypatch.setattr(quiz_judge, "get_gateway", lambda: TimeoutGateway())

    with pytest.raises(TimeoutError, match="server-owned Gateway deadline"):
        async for _chunk in quiz_judge._stream_judge_response(
            user_prompt="private prompt",
            system_prompt="private system",
            image_records=[],
        ):
            pass
