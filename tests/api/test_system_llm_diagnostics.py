from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.api.routers import system
from traittutor.gateway import GatewayRequest
from traittutor.services.config import test_runner
from traittutor.services.llm.config import LLMConfig


def _config() -> LLMConfig:
    return LLMConfig(
        model="probe-model",
        api_key="probe-key",
        base_url="https://probe.invalid/v1",
        binding="openai",
        provider_name="openai",
    )


@pytest.mark.asyncio
async def test_system_llm_connection_probe_disables_route_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[GatewayRequest] = []

    class Gateway:
        async def complete(self, request: GatewayRequest) -> Any:
            captured.append(request)
            return SimpleNamespace(content="OK")

    monkeypatch.setattr(system, "get_llm_config", _config)
    monkeypatch.setattr(system, "get_gateway", lambda: Gateway())

    response = await system.test_llm_connection()

    assert response.success is True
    assert captured[0].allow_route_fallback is False


@pytest.mark.asyncio
async def test_catalog_probe_disables_route_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[GatewayRequest] = []

    class Gateway:
        async def complete(self, request: GatewayRequest) -> Any:
            captured.append(request)
            return SimpleNamespace(content="OK")

    resolved = SimpleNamespace(
        model="probe-model",
        api_key="probe-key",
        base_url="https://probe.invalid/v1",
        effective_url="https://probe.invalid/v1",
        binding="openai",
        provider_name="openai",
        provider_mode="standard",
        api_version=None,
        extra_headers={},
        reasoning_effort=None,
    )
    monkeypatch.setattr(test_runner, "resolve_llm_runtime_config", lambda **_kwargs: resolved)
    monkeypatch.setattr("traittutor.gateway.get_gateway", lambda: Gateway())
    monkeypatch.setattr("traittutor.services.llm.clear_llm_config_cache", lambda: None)
    monkeypatch.setattr(
        "traittutor.services.config.loader.get_agent_params",
        lambda _name: {"max_tokens": 64, "temperature": 0.0},
    )
    run = test_runner.TestRun(id="llm-probe", service="llm")

    await test_runner.ConfigTestRunner()._test_llm(run, {"profiles": []})

    assert captured[0].allow_route_fallback is False
