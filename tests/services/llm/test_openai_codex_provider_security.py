"""Transport-security regressions for the OpenAI Codex provider."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.services.llm.provider_core import openai_codex_provider as codex_module
from traittutor.services.llm.provider_core.openai_codex_provider import OpenAICodexProvider


@pytest.mark.asyncio
async def test_certificate_failure_never_retries_with_verification_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def load_token() -> Any:
        return SimpleNamespace(account_id="account-1", access="secret-token")

    async def failing_request(
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        verify: bool,
        on_content_delta: Any = None,
    ) -> tuple[str, list[Any], str]:
        del url, body, on_content_delta
        calls.append({"verify": verify, "authorization": headers["Authorization"]})
        raise RuntimeError("CERTIFICATE_VERIFY_FAILED")

    provider = OpenAICodexProvider(default_model="openai-codex/test-model")
    monkeypatch.setattr(provider, "_load_token", load_token)
    monkeypatch.setattr(codex_module, "_request_codex", failing_request)
    monkeypatch.setattr(codex_module, "disable_ssl_verify_enabled", lambda: False)

    result = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert result.finish_reason == "error"
    assert calls == [{"verify": True, "authorization": "Bearer secret-token"}]


@pytest.mark.asyncio
async def test_explicit_nonproduction_ssl_override_remains_single_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def load_token() -> Any:
        return SimpleNamespace(account_id=None, access="secret-token")

    async def successful_request(
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        verify: bool,
        on_content_delta: Any = None,
    ) -> tuple[str, list[Any], str]:
        del url, headers, body, on_content_delta
        calls.append(verify)
        return "ok", [], "stop"

    provider = OpenAICodexProvider(default_model="openai-codex/test-model")
    monkeypatch.setattr(provider, "_load_token", load_token)
    monkeypatch.setattr(codex_module, "_request_codex", successful_request)
    monkeypatch.setattr(codex_module, "disable_ssl_verify_enabled", lambda: True)

    result = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert result.finish_reason == "stop"
    assert result.content == "ok"
    assert calls == [False]
