"""Contract tests for provider-error mapping to unified LLM exceptions.

Focuses on the quota-exhaustion classification added for automatic model
rotation: HTTP 403 with billing markers maps to ``ProviderQuotaExceededError``
while a generic 403 stays ``LLMAPIError`` and a plain 429 stays transient.
"""

from __future__ import annotations

from traittutor.services.llm.error_mapping import is_quota_exhaustion, map_error
from traittutor.services.llm.exceptions import (
    LLMAPIError,
    LLMAuthenticationError,
    LLMRateLimitError,
    ProviderQuotaExceededError,
)


class _FakeStatusError(Exception):
    """Provider-shaped error carrying a status code like SDK errors do."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def test_403_with_quota_markers_maps_to_quota_exhaustion() -> None:
    exc = _FakeStatusError(
        "403 You've reached your usage limit for this billing cycle. "
        "Your quota will be refreshed in the next cycle.",
        403,
    )
    mapped = map_error(exc, provider="kimi")
    assert isinstance(mapped, ProviderQuotaExceededError)
    # ProviderQuotaExceededError remains a rate-limit-shaped error for retry.
    assert isinstance(mapped, LLMRateLimitError)


def test_403_without_quota_markers_stays_generic_api_error() -> None:
    exc = _FakeStatusError("403 Forbidden: endpoint requires elevated permissions", 403)
    mapped = map_error(exc, provider="test")
    assert isinstance(mapped, LLMAPIError)
    assert not isinstance(mapped, ProviderQuotaExceededError)
    assert mapped.status_code == 403


def test_401_still_maps_to_authentication_error() -> None:
    exc = _FakeStatusError("401 invalid api key", 401)
    assert isinstance(map_error(exc), LLMAuthenticationError)


def test_429_without_quota_markers_stays_transient_rate_limit() -> None:
    exc = _FakeStatusError("429 Too Many Requests: retry later", 429)
    mapped = map_error(exc, provider="test")
    assert isinstance(mapped, LLMRateLimitError)
    assert not isinstance(mapped, ProviderQuotaExceededError)


def test_is_quota_exhaustion_detects_mapped_and_raw_errors() -> None:
    assert is_quota_exhaustion(ProviderQuotaExceededError("quota", provider="kimi")) is True
    assert is_quota_exhaustion(LLMAPIError("reached your usage limit", status_code=403)) is True
    assert is_quota_exhaustion(LLMRateLimitError("rate limit exceeded")) is False
    assert is_quota_exhaustion(_FakeStatusError("billing cycle exhausted", 403)) is True
    assert is_quota_exhaustion(_FakeStatusError("temporarily unavailable", 503)) is False
