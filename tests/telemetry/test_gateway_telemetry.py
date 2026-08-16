"""Gateway emits only server-derived operational telemetry."""

from __future__ import annotations

import pytest

from traittutor.gateway.service import GatewayMessage, GatewayRequest, TraitTutorGateway
from traittutor.services.llm.config import LLMConfig
from traittutor.telemetry import InMemoryProductEventSink, ProductEventEnvelope


def _config() -> LLMConfig:
    return LLMConfig(
        model="server-model",
        api_key="must-never-be-recorded",
        base_url="https://private.example/v1",
        binding="openai_compat",
        provider_name="configured_provider",
    )


@pytest.mark.asyncio
async def test_gateway_success_emits_no_request_content_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            del prompt, kwargs
            return "private model answer", {}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    sink = InMemoryProductEventSink()
    gateway = TraitTutorGateway(event_sink=sink)

    response = await gateway.complete(
        GatewayRequest(
            prompt="private prompt",
            system_prompt="private system prompt",
            purpose="courseware_instruction",
            messages=(GatewayMessage(role="user", content="private chat"),),
            user_id="owner-1",
            metadata={"persona": "private persona", "source_body": "private source"},
            llm_config=_config(),
        )
    )

    assert response.content == "private model answer"
    event = next(event for event in sink.events if event.event_name == "gateway.complete")
    assert event.event_name == "gateway.complete"
    assert event.attributes["provider"] == "configured_provider"
    assert event.attributes["route"] == "openai_compat"
    assert event.metric_labels["provider"] == "configured_provider"
    assert event.metric_labels["route"] == "openai_compat"
    assert "model" not in event.metric_labels
    assert "request_id" not in event.metric_labels
    serialized = event.model_dump_json()
    for private_value in (
        "private prompt",
        "private system prompt",
        "private chat",
        "private persona",
        "private source",
        "private model answer",
        "must-never-be-recorded",
        "https://private.example/v1",
        "owner-1",
    ):
        assert private_value not in serialized


@pytest.mark.asyncio
async def test_gateway_timeout_is_recorded_and_original_error_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            del prompt, kwargs
            raise TimeoutError("provider timeout with private detail")

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", TimeoutClient)
    sink = InMemoryProductEventSink()

    with pytest.raises(TimeoutError, match="private detail"):
        await TraitTutorGateway(event_sink=sink).complete(
            GatewayRequest(
                prompt="private prompt",
                system_prompt="private system",
                purpose="courseware_instruction",
                llm_config=_config(),
            )
        )

    event = sink.events[0]
    assert event.attributes["outcome"] == "timeout"
    assert event.attributes["timed_out"] is True
    assert "private detail" not in event.model_dump_json()


@pytest.mark.asyncio
async def test_broken_sink_does_not_change_gateway_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubClient:
        def __init__(self, config: LLMConfig) -> None:
            del config

        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            del prompt, kwargs
            return "ok", {}, []

    class BrokenSink:
        def emit(self, event: ProductEventEnvelope) -> None:
            del event
            raise RuntimeError("sink down")

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)

    response = await TraitTutorGateway(event_sink=BrokenSink()).complete(
        GatewayRequest(
            prompt="prompt",
            system_prompt="system",
            purpose="test",
            llm_config=_config(),
        )
    )

    assert response.content == "ok"
