"""Contract tests for the general quota-driven route rotation policy.

Covers the shared bounded loop (quota rotates immediately, transient retries
once), circuit skipping, deadline enforcement, per-request selection, and the
Gateway non-streaming choke point under the opt-in flag.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from traittutor.gateway import GatewayReceipt, GatewayRequest, GatewayStreamEvent, TraitTutorGateway
from traittutor.gateway.quota_rotation import (
    QuotaRotationExhaustedError,
    QuotaRotationPolicy,
    default_same_route_retryable,
)
from traittutor.gateway.route_health import FileRouteHealthStore
from traittutor.services.llm.config import LLMConfig
from traittutor.services.llm.exceptions import (
    LLMAPIError,
    LLMRateLimitError,
    ProviderQuotaExceededError,
)
from traittutor.services.llm.provider_core.base import LLMResponse
from traittutor.telemetry import InMemoryProductEventSink

QUOTA_FLAG = "TRAITTUTOR_GATEWAY_QUOTA_ROTATION"


def _config(model: str) -> LLMConfig:
    return LLMConfig(
        model=model,
        api_key="server-only-test-key",
        base_url="https://gateway.test/v1",
        binding="openai",
        provider_name="test",
    )


def _routes(
    monkeypatch: pytest.MonkeyPatch,
    primary_model: str = "primary",
    fallback_model: str = "fallback",
) -> tuple[LLMConfig, LLMConfig]:
    primary, fallback = _config(primary_model), _config(fallback_model)
    monkeypatch.setattr(
        "traittutor.gateway.quota_rotation.enumerate_fallback_routes",
        lambda _primary, **kwargs: (primary, fallback),
    )
    return primary, fallback


def _quota_error() -> ProviderQuotaExceededError:
    # Mirrors the real Kimi message that triggered the original incident.
    return ProviderQuotaExceededError(
        "403 You've reached your usage limit for this billing cycle. "
        "Your quota will be refreshed in the next cycle.",
        provider="kimi",
    )


class _StubClient:
    """Provider-bound stub recording which route each attempt selected."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    async def complete_with_usage(
        self, prompt: str, **kwargs: object
    ) -> tuple[str, dict[str, int], list]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# Policy-level contract
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_failure_rotates_without_same_route_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, fallback = _routes(monkeypatch)
    calls: list[str] = []

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        calls.append(route.model)
        if route.model == "primary":
            raise _quota_error()
        return "fallback answer"

    result = await QuotaRotationPolicy(purpose="chat:test", total_timeout_seconds=10).run(
        primary, invoke=invoke, same_route_retryable=default_same_route_retryable
    )

    assert calls == ["primary", "fallback"]
    assert result.value == "fallback answer"
    assert result.config.model == "fallback"
    assert [(item.route_index, item.retry_index, item.outcome) for item in result.attempts] == [
        (1, 1, "error"),
        (2, 1, "success"),
    ]


@pytest.mark.asyncio
async def test_transient_failure_retries_same_route_once_then_rotates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, fallback = _routes(monkeypatch)
    calls: list[str] = []

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        calls.append(route.model)
        if route.model == "primary":
            raise TimeoutError("transient provider timeout")
        return "fallback answer"

    result = await QuotaRotationPolicy(purpose="chat:test", total_timeout_seconds=10).run(
        primary, invoke=invoke, same_route_retryable=default_same_route_retryable
    )

    assert calls == ["primary", "primary", "fallback"]
    assert [(item.route_index, item.retry_index, item.outcome) for item in result.attempts] == [
        (1, 1, "timeout"),
        (1, 2, "timeout"),
        (2, 1, "success"),
    ]


@pytest.mark.asyncio
async def test_open_circuit_skips_route(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    primary, fallback = _routes(monkeypatch)
    store = FileRouteHealthStore(tmp_path / "health.json", failure_threshold=1, cooldown_seconds=60)
    assert store.record_failure(primary) is True
    calls: list[str] = []

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        calls.append(route.model)
        return "fallback answer"

    result = await QuotaRotationPolicy(
        purpose="chat:test", total_timeout_seconds=10, route_health_store=store
    ).run(primary, invoke=invoke, same_route_retryable=default_same_route_retryable)

    assert calls == ["fallback"]
    assert result.config.model == "fallback"
    assert [item.outcome for item in result.attempts] == ["circuit_open", "success"]


@pytest.mark.asyncio
async def test_policy_enforces_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    primary, fallback = _routes(monkeypatch)
    calls = 0

    async def invoke(_route: LLMConfig, _remaining: float) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(QuotaRotationExhaustedError):
        await QuotaRotationPolicy(total_timeout_seconds=0.001).run(
            primary, invoke=invoke, same_route_retryable=default_same_route_retryable
        )
    assert calls == 1


@pytest.mark.asyncio
async def test_exhaustion_carries_last_error_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, fallback = _routes(monkeypatch)

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        if route.model == "primary":
            raise _quota_error()
        raise LLMRateLimitError("rate limited", provider="test")

    with pytest.raises(QuotaRotationExhaustedError) as excinfo:
        await QuotaRotationPolicy(total_timeout_seconds=10).run(
            primary, invoke=invoke, same_route_retryable=lambda _exc: False
        )
    assert isinstance(excinfo.value.last_error, LLMRateLimitError)
    assert excinfo.value.last_config is not None
    assert excinfo.value.last_config.model == "fallback"


@pytest.mark.asyncio
async def test_rotation_selects_alternate_config_without_mutating_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _fallback = _routes(monkeypatch)

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        if route.model == "primary":
            raise _quota_error()
        return "ok"

    result = await QuotaRotationPolicy(total_timeout_seconds=10).run(
        primary, invoke=invoke, same_route_retryable=default_same_route_retryable
    )
    # Per-request isolation: rotation returns a distinct fallback config rather
    # than mutating the request's selection (LLMConfig is frozen and untouched).
    assert primary.model == "primary"
    assert result.config.model == "fallback"
    assert result.config is not primary


def test_default_classifier_rotates_quota_and_credential_failures_immediately() -> None:
    assert default_same_route_retryable(_quota_error()) is False
    assert default_same_route_retryable(LLMAPIError("invalid api key", status_code=401)) is False
    # Short-lived transport/upstream errors get one retry on the same route.
    assert default_same_route_retryable(LLMRateLimitError("rate limit exceeded")) is True
    assert default_same_route_retryable(TimeoutError("connection timed out")) is True


@pytest.mark.asyncio
async def test_telemetry_emits_route_attempts_without_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _fallback = _routes(monkeypatch)

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        if route.model == "primary":
            raise _quota_error()
        return "ok"

    sink = InMemoryProductEventSink()
    await QuotaRotationPolicy(purpose="chat:test", total_timeout_seconds=10, event_sink=sink).run(
        primary, invoke=invoke, same_route_retryable=default_same_route_retryable
    )

    assert [event.event_name for event in sink.events] == [
        "gateway.route_attempt",
        "gateway.route_attempt",
    ]
    assert [event.attributes["fallback_used"] for event in sink.events] == [False, True]
    payload = sink.events[0].model_dump_json()
    assert "server-only-test-key" not in payload
    assert "gateway.test" not in payload
    assert all("model" not in event.metric_labels for event in sink.events)


# --------------------------------------------------------------------------
# Gateway non-streaming choke point
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_complete_rotates_to_fallback_on_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, _fallback = _routes(monkeypatch)
    used_models: list[str] = []

    class StubClient(_StubClient):
        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            used_models.append(self.config.model)
            if self.config.model == "primary":
                raise _quota_error()
            return ("fallback answer", {"total_tokens": 7}, [])

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    gateway = TraitTutorGateway(event_sink=InMemoryProductEventSink())
    request = GatewayRequest(
        prompt="hello",
        system_prompt="system",
        purpose="chat:test",
        llm_config=primary,
    )

    response = await gateway.complete(request)

    assert used_models == ["primary", "fallback"]
    assert response.content == "fallback answer"
    assert response.model == "fallback"
    assert response.receipt is not None
    assert response.receipt.model == "fallback"
    # The request's selection is untouched by the rotation.
    assert request.llm_config is primary


@pytest.mark.asyncio
async def test_gateway_complete_skips_rotation_for_explicit_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, _fallback = _routes(monkeypatch)
    used_models: list[str] = []

    class StubClient(_StubClient):
        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            used_models.append(self.config.model)
            raise _quota_error()

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    gateway = TraitTutorGateway(event_sink=InMemoryProductEventSink())

    with pytest.raises(ProviderQuotaExceededError):
        await gateway.complete(
            GatewayRequest(
                prompt="x",
                system_prompt="y",
                purpose="generate:quiz",
                # Callers running under their own bounded policy opt out
                # explicitly; the purpose prefix alone must not exempt them.
                allow_quota_rotation=False,
                llm_config=primary,
            )
        )
    # Generation keeps its own bounded policy; the general one must not run.
    assert used_models == ["primary"]


@pytest.mark.asyncio
async def test_gateway_complete_rotates_despite_generate_purpose_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``generate:`` purpose without the explicit opt-out still rotates.

    The prefix used to silently exempt any caller that claimed it —
    planner/specialist completions borrowed it while having no bounded
    policy of their own. The typed field is now the only exemption.
    """
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, _fallback = _routes(monkeypatch)
    used_models: list[str] = []

    class StubClient(_StubClient):
        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            used_models.append(self.config.model)
            if self.config.model == "primary":
                raise _quota_error()
            return "recovered", {"input_tokens": 1, "output_tokens": 1}, []

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    gateway = TraitTutorGateway(event_sink=InMemoryProductEventSink())

    response = await gateway.complete(
        GatewayRequest(
            prompt="x",
            system_prompt="y",
            purpose="generate:courseware-agentic-planner-v2",
            llm_config=primary,
        )
    )
    assert response.content == "recovered"
    assert used_models == ["primary", "fallback"]


@pytest.mark.asyncio
async def test_gateway_complete_respects_request_that_disables_route_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _fallback = _routes(monkeypatch)
    used_models: list[str] = []

    class StubClient(_StubClient):
        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            used_models.append(self.config.model)
            raise _quota_error()

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)

    with pytest.raises(ProviderQuotaExceededError):
        await TraitTutorGateway(event_sink=InMemoryProductEventSink()).complete(
            GatewayRequest(
                prompt="probe",
                system_prompt="system",
                purpose="system:model-catalog-probe",
                allow_route_fallback=False,
                llm_config=primary,
            )
        )

    assert used_models == ["primary"]


@pytest.mark.asyncio
async def test_gateway_complete_surfaces_last_error_when_all_routes_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, _fallback = _routes(monkeypatch)

    class StubClient(_StubClient):
        async def complete_with_usage(
            self, prompt: str, **kwargs: object
        ) -> tuple[str, dict[str, int], list]:
            raise _quota_error()

    monkeypatch.setattr("traittutor.gateway.service.LLMClient", StubClient)
    gateway = TraitTutorGateway(event_sink=InMemoryProductEventSink())

    # The real provider error is preserved, not a generic exhaustion type, so
    # existing callers keep their error handling after rotation also fails.
    with pytest.raises(ProviderQuotaExceededError):
        await gateway.complete(
            GatewayRequest(
                prompt="x",
                system_prompt="y",
                purpose="chat:test",
                llm_config=primary,
            )
        )


# --------------------------------------------------------------------------
# Gateway streaming choke point (quota error before any output rotates)
# --------------------------------------------------------------------------


def _stream_route_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
    primary: LLMConfig,
    fallback: LLMConfig,
) -> None:
    # The stream loop resolves the function from service.py's module globals,
    # so patch that binding (the policy tests patch the quota_rotation module).
    monkeypatch.setattr(
        "traittutor.gateway.service.enumerate_fallback_routes",
        lambda _primary, **kwargs: (primary, fallback),
    )


@pytest.mark.asyncio
async def test_gateway_stream_rotates_on_quota_before_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, fallback = _config("primary"), _config("fallback")
    _stream_route_monkeypatch(monkeypatch, primary, fallback)
    used_models: list[str] = []

    class FakeProvider:
        def __init__(self, model: str):
            self.model = model

        async def chat_stream_with_retry(self, **kwargs: object) -> LLMResponse:
            used_models.append(self.model)
            if self.model == "primary":
                raise _quota_error()
            await kwargs["on_content_delta"]("fallback answer")  # type: ignore[index]
            return LLMResponse(content="fallback answer")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider",
        lambda config: FakeProvider(config.model),
    )
    events = [
        event
        async for event in TraitTutorGateway().stream(
            GatewayRequest(prompt="x", system_prompt="y", purpose="chat:test", llm_config=primary)
        )
    ]

    assert used_models == ["primary", "fallback"]
    assert [event.type for event in events] == ["text", "final"]
    assert events[0].text == "fallback answer"
    assert events[-1].receipt is not None
    assert events[-1].receipt.model == "fallback"


@pytest.mark.asyncio
async def test_gateway_stream_retries_transient_once_then_rotates_before_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, fallback = _config("primary"), _config("fallback")
    _stream_route_monkeypatch(monkeypatch, primary, fallback)
    used_models: list[str] = []
    retry_delays: list[tuple[float, ...]] = []

    class FakeProvider:
        def __init__(self, model: str):
            self.model = model

        async def chat_stream_with_retry(self, **kwargs: object) -> LLMResponse:
            used_models.append(self.model)
            retry_delays.append(tuple(kwargs["retry_delays"]))  # type: ignore[arg-type]
            if self.model == "primary":
                raise TimeoutError("transient provider timeout")
            await kwargs["on_content_delta"]("fallback answer")  # type: ignore[index]
            return LLMResponse(content="fallback answer")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider",
        lambda config: FakeProvider(config.model),
    )

    events = [
        event
        async for event in TraitTutorGateway().stream(
            GatewayRequest(
                prompt="x",
                system_prompt="y",
                purpose="chat:test",
                max_retries=8,
                timeout_seconds=10,
                llm_config=primary,
            )
        )
    ]

    assert used_models == ["primary", "primary", "fallback"]
    assert retry_delays == [(), (), ()]
    assert [event.type for event in events] == ["text", "final"]
    assert events[-1].receipt is not None
    assert events[-1].receipt.model == "fallback"


@pytest.mark.asyncio
async def test_gateway_stream_reserves_deadline_for_fallback_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, fallback = _config("primary"), _config("fallback")
    _stream_route_monkeypatch(monkeypatch, primary, fallback)
    attempts: list[tuple[str, float | None, float]] = []

    async def fake_stream_one_route(self: TraitTutorGateway, request: GatewayRequest):
        del self
        attempts.append(
            (
                request.llm_config.model,  # type: ignore[union-attr]
                request.timeout_seconds,
                float(request.metadata["_gateway_first_stream_output_timeout_seconds"]),
            )
        )
        if request.llm_config is primary:
            raise TimeoutError("primary provider timeout")
        yield GatewayStreamEvent(
            type="final",
            finish_reason="stop",
            receipt=GatewayReceipt(
                request_id="fallback-budget",
                purpose=request.purpose,
                model=fallback.model,
                provider=fallback.provider_name,
                route=fallback.provider_name,
                latency_ms=1,
                timeout_seconds=request.timeout_seconds or 0,
                response_format_applied=False,
                tools_applied=0,
                attachments_applied=0,
            ),
        )

    monkeypatch.setattr(TraitTutorGateway, "_stream_one_route", fake_stream_one_route)

    events = [
        event
        async for event in TraitTutorGateway().stream(
            GatewayRequest(
                prompt="x",
                system_prompt="y",
                purpose="chat:test",
                timeout_seconds=10,
                llm_config=primary,
            )
        )
    ]

    assert [model for model, _timeout, _first_output in attempts] == [
        "primary",
        "primary",
        "fallback",
    ]
    assert attempts[0][1] == pytest.approx(10, rel=0.01)
    assert attempts[0][2] == pytest.approx(5, rel=0.01)
    assert attempts[1][2] == pytest.approx(5, rel=0.01)
    assert attempts[2][2] == pytest.approx(10, rel=0.01)
    assert events[-1].receipt is not None
    assert events[-1].receipt.model == "fallback"


@pytest.mark.asyncio
async def test_gateway_stream_skips_route_with_open_circuit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    primary, fallback = _config("primary"), _config("fallback")
    _stream_route_monkeypatch(monkeypatch, primary, fallback)
    store = FileRouteHealthStore(
        tmp_path / "stream-route-health.json",
        failure_threshold=1,
        cooldown_seconds=60,
    )
    store.record_failure(primary)
    monkeypatch.setattr(
        "traittutor.gateway.service.create_configured_route_health_store",
        lambda: store,
    )
    used_models: list[str] = []

    class FakeProvider:
        def __init__(self, model: str):
            self.model = model

        async def chat_stream_with_retry(self, **kwargs: object) -> LLMResponse:
            used_models.append(self.model)
            await kwargs["on_content_delta"]("fallback answer")  # type: ignore[index]
            return LLMResponse(content="fallback answer")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider",
        lambda config: FakeProvider(config.model),
    )
    sink = InMemoryProductEventSink()

    events = [
        event
        async for event in TraitTutorGateway(event_sink=sink).stream(
            GatewayRequest(prompt="x", system_prompt="y", purpose="chat:test", llm_config=primary)
        )
    ]

    assert used_models == ["fallback"]
    assert events[-1].receipt is not None
    assert events[-1].receipt.model == "fallback"
    attempts = [event for event in sink.events if event.event_name == "gateway.route_attempt"]
    assert [event.attributes["outcome"] for event in attempts] == ["circuit_open", "success"]


@pytest.mark.asyncio
async def test_gateway_stream_surfaces_quota_error_after_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, fallback = _config("primary"), _config("fallback")
    _stream_route_monkeypatch(monkeypatch, primary, fallback)
    used_models: list[str] = []

    class FakeProvider:
        def __init__(self, model: str):
            self.model = model

        async def chat_stream_with_retry(self, **kwargs: object) -> LLMResponse:
            used_models.append(self.model)
            if self.model == "primary":
                await kwargs["on_content_delta"]("partial")  # type: ignore[index]
                raise _quota_error()
            return LLMResponse(content="never")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider",
        lambda config: FakeProvider(config.model),
    )
    seen: list[str] = []
    with pytest.raises(ProviderQuotaExceededError):
        async for event in TraitTutorGateway().stream(
            GatewayRequest(prompt="x", system_prompt="y", purpose="chat:test", llm_config=primary)
        ):
            seen.append(event.type)
    # Content already reached the consumer, so it cannot be retracted by
    # rotating to the fallback: the real error surfaces verbatim.
    assert seen == ["text"]
    assert used_models == ["primary"]


@pytest.mark.asyncio
async def test_gateway_stream_never_replays_transient_failure_after_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, fallback = _config("primary"), _config("fallback")
    _stream_route_monkeypatch(monkeypatch, primary, fallback)
    used_models: list[str] = []

    class FakeProvider:
        def __init__(self, model: str):
            self.model = model

        async def chat_stream_with_retry(self, **kwargs: object) -> LLMResponse:
            used_models.append(self.model)
            if self.model == "primary":
                await kwargs["on_content_delta"]("partial")  # type: ignore[index]
                raise TimeoutError("provider stalled after first token")
            await kwargs["on_content_delta"]("duplicate fallback")  # type: ignore[index]
            return LLMResponse(content="duplicate fallback")

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider",
        lambda config: FakeProvider(config.model),
    )

    seen: list[str] = []
    with pytest.raises(TimeoutError, match="stalled after first token"):
        async for event in TraitTutorGateway().stream(
            GatewayRequest(prompt="x", system_prompt="y", purpose="chat:test", llm_config=primary)
        ):
            seen.append(event.text or event.type)

    assert seen == ["partial"]
    assert used_models == ["primary"]


@pytest.mark.asyncio
async def test_gateway_stream_skips_rotation_for_explicit_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(QUOTA_FLAG, "1")
    primary, fallback = _config("primary"), _config("fallback")
    _stream_route_monkeypatch(monkeypatch, primary, fallback)
    used_models: list[str] = []

    class FakeProvider:
        def __init__(self, model: str):
            self.model = model

        async def chat_stream_with_retry(self, **kwargs: object) -> LLMResponse:
            used_models.append(self.model)
            raise _quota_error()

    monkeypatch.setattr(
        "traittutor.gateway.service.get_runtime_provider",
        lambda config: FakeProvider(config.model),
    )
    with pytest.raises(ProviderQuotaExceededError):
        async for _event in TraitTutorGateway().stream(
            GatewayRequest(
                prompt="x",
                system_prompt="y",
                purpose="generate:quiz",
                # Typed opt-out only; the purpose prefix alone must not exempt.
                allow_quota_rotation=False,
                llm_config=primary,
            )
        ):
            pass
    assert used_models == ["primary"]
