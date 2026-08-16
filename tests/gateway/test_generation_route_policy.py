"""Contract tests for the opt-in bounded structured-generation route policy."""

from __future__ import annotations

import asyncio

import pytest

from traittutor.gateway.quota_rotation import QuotaRotationExhaustedError
from traittutor.gateway.routing import GenerationRoutePolicy
from traittutor.services.llm.config import LLMConfig
from traittutor.telemetry import InMemoryProductEventSink


def _config(model: str) -> LLMConfig:
    return LLMConfig(
        model=model,
        api_key="server-only-test-key",
        base_url="https://gateway.test/v1",
        binding="openai",
        provider_name="test",
    )


def _routes(monkeypatch: pytest.MonkeyPatch) -> tuple[LLMConfig, LLMConfig]:
    primary, fallback = _config("primary"), _config("fallback")
    monkeypatch.setattr(
        "traittutor.gateway.routing.generation_route_configs",
        lambda _primary: (primary, fallback),
    )
    return primary, fallback


@pytest.mark.asyncio
async def test_policy_uses_one_single_attempt_gateway_call_on_first_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _fallback = _routes(monkeypatch)
    calls: list[tuple[str, float]] = []

    async def invoke(route: LLMConfig, remaining: float) -> str:
        calls.append((route.model, remaining))
        return "ok"

    sink = InMemoryProductEventSink()
    result = await GenerationRoutePolicy(
        purpose="generate:test-policy", total_timeout_seconds=10, event_sink=sink
    ).run(
        primary,
        invoke=invoke,
        same_route_retryable=lambda _exc: True,
    )

    assert calls and [model for model, _remaining in calls] == ["primary"]
    assert 0 < calls[0][1] <= 10
    assert result.config.model == "primary"
    assert [
        (item.route_index, item.retry_index, item.fallback_used, item.outcome)
        for item in result.attempts
    ] == [(1, 1, False, "success")]
    assert [event.event_name for event in sink.events] == ["gateway.route_attempt"]
    payload = sink.events[0].model_dump_json()
    assert "server-only-test-key" not in payload
    assert "gateway.test" not in payload


@pytest.mark.asyncio
async def test_policy_retries_once_then_rotates_to_one_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _fallback = _routes(monkeypatch)
    calls: list[str] = []

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        calls.append(route.model)
        if route.model == "primary":
            raise TimeoutError("transient provider timeout")
        return "fallback result"

    sink = InMemoryProductEventSink()
    result = await GenerationRoutePolicy(
        purpose="generate:test-policy", total_timeout_seconds=10, event_sink=sink
    ).run(
        primary,
        invoke=invoke,
        same_route_retryable=lambda exc: isinstance(exc, TimeoutError),
    )

    assert calls == ["primary", "primary", "fallback"]
    assert [
        (item.route_index, item.retry_index, item.fallback_used, item.outcome)
        for item in result.attempts
    ] == [
        (1, 1, False, "timeout"),
        (1, 2, False, "timeout"),
        (2, 1, True, "success"),
    ]
    assert [event.attributes["fallback_used"] for event in sink.events] == [False, False, True]
    assert all("model" not in event.metric_labels for event in sink.events)


@pytest.mark.asyncio
async def test_policy_rotates_quota_failure_without_same_route_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _fallback = _routes(monkeypatch)
    calls: list[str] = []

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        calls.append(route.model)
        if route.model == "primary":
            raise RuntimeError("quota exceeded")
        return "ok"

    result = await GenerationRoutePolicy(total_timeout_seconds=10).run(
        primary,
        invoke=invoke,
        same_route_retryable=lambda _exc: False,
    )

    assert calls == ["primary", "fallback"]
    assert result.attempts[1].fallback_used is True


@pytest.mark.asyncio
async def test_policy_enforces_total_deadline_without_legacy_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary, _fallback = _routes(monkeypatch)
    calls = 0

    async def invoke(_route: LLMConfig, _remaining: float) -> str:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(QuotaRotationExhaustedError):
        await GenerationRoutePolicy(total_timeout_seconds=0.001).run(
            primary,
            invoke=invoke,
            same_route_retryable=lambda _exc: True,
        )

    assert calls == 1
