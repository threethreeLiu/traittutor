"""Cross-process and policy-facing tests for durable Gateway route circuits."""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from traittutor.gateway.route_health import (
    FileRouteHealthStore,
    NoOpRouteHealthStore,
    create_configured_route_health_store,
)
from traittutor.gateway.routing import GenerationRoutePolicy
from traittutor.services.llm.config import LLMConfig
from traittutor.telemetry import InMemoryProductEventSink


def _config(model: str = "route-model") -> LLMConfig:
    return LLMConfig(
        model=model,
        api_key="server-only-test-key",
        base_url="https://private.gateway.test/v1",
        binding="openai",
        provider_name="test-provider",
    )


def _record_failure_in_process(path: str) -> None:
    """Spawn target: prove fcntl/atomic persistence is process shared."""
    FileRouteHealthStore(path, failure_threshold=3, cooldown_seconds=60).record_failure(_config())


def test_file_route_health_opens_circuit_and_expires(tmp_path: Path) -> None:
    store = FileRouteHealthStore(tmp_path / "health.json", failure_threshold=3, cooldown_seconds=60)
    config = _config()

    assert store.record_failure(config, now=10) is False
    assert store.record_failure(config, now=10) is False
    assert store.record_failure(config, now=10) is True
    assert store.allows(config, now=10) is False
    assert store.allows(config, now=70) is True
    store.record_success(config)
    assert store.allows(config, now=10) is True


def test_file_route_health_is_cross_process_atomic(tmp_path: Path) -> None:
    path = str(tmp_path / "health.json")
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_record_failure_in_process, args=(path,)) for _ in range(3)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    assert (
        FileRouteHealthStore(path, failure_threshold=3, cooldown_seconds=60).allows(_config())
        is False
    )


@pytest.mark.asyncio
async def test_policy_skips_open_route_and_records_circuit_outcome(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary, fallback = _config("primary"), _config("fallback")
    store = FileRouteHealthStore(tmp_path / "health.json", failure_threshold=1, cooldown_seconds=60)
    assert store.record_failure(primary) is True
    monkeypatch.setattr(
        "traittutor.gateway.routing.generation_route_configs",
        lambda _primary: (primary, fallback),
    )
    calls: list[str] = []

    async def invoke(route: LLMConfig, _remaining: float) -> str:
        calls.append(route.model)
        return "fallback answer"

    sink = InMemoryProductEventSink()
    result = await GenerationRoutePolicy(
        purpose="generate:circuit-test",
        total_timeout_seconds=10,
        event_sink=sink,
        route_health_store=store,
    ).run(primary, invoke=invoke, same_route_retryable=lambda _exc: True)

    assert calls == ["fallback"]
    assert result.config.model == "fallback"
    assert [event.attributes["outcome"] for event in sink.events] == ["circuit_open", "success"]
    assert sink.events[0].attributes["fallback_used"] is False
    assert sink.events[1].attributes["fallback_used"] is True


def test_missing_or_bad_config_rolls_back_to_noop() -> None:
    assert isinstance(create_configured_route_health_store({}), NoOpRouteHealthStore)
    assert isinstance(
        create_configured_route_health_store({"TRAITTUTOR_GATEWAY_ROUTE_HEALTH_PATH": "/"}),
        NoOpRouteHealthStore,
    )
