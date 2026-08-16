"""
Error Mapping - Map provider-specific errors to unified exceptions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

# Import unified exceptions from exceptions.py
from .exceptions import (
    LLMAPIError,
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    ProviderContextWindowError,
    ProviderQuotaExceededError,
)

try:
    import openai

    _HAS_OPENAI = True
except ImportError:  # pragma: no cover
    openai = None  # type: ignore
    _HAS_OPENAI = False


logger = logging.getLogger(__name__)


ErrorClassifier = Callable[[Exception], bool]


@dataclass(frozen=True)
class MappingRule:
    classifier: ErrorClassifier
    factory: Callable[[Exception, str | None], LLMError]


def _instance_of(*types: type[BaseException]) -> ErrorClassifier:
    return lambda exc: isinstance(exc, types)


def _message_contains(*needles: str) -> ErrorClassifier:
    def _classifier(exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(needle in msg for needle in needles)

    return _classifier


_GLOBAL_RULES: list[MappingRule] = [
    MappingRule(
        classifier=_message_contains("rate limit", "429", "quota"),
        factory=lambda exc, provider: LLMRateLimitError(str(exc), provider=provider),
    ),
    MappingRule(
        classifier=_message_contains("context length", "maximum context"),
        factory=lambda exc, provider: ProviderContextWindowError(str(exc), provider=provider),
    ),
]

if _HAS_OPENAI and openai is not None:
    _GLOBAL_RULES[:0] = [
        MappingRule(
            classifier=_instance_of(openai.AuthenticationError),
            factory=lambda exc, provider: LLMAuthenticationError(str(exc), provider=provider),
        ),
        MappingRule(
            classifier=_instance_of(openai.RateLimitError),
            factory=lambda exc, provider: LLMRateLimitError(str(exc), provider=provider),
        ),
    ]

# Attempt to load Anthropic and Google rules if SDKs are present
try:
    import anthropic

    _GLOBAL_RULES.append(
        MappingRule(
            classifier=_instance_of(anthropic.RateLimitError),
            factory=lambda exc, provider: LLMRateLimitError(str(exc), provider=provider),
        )
    )
except ImportError:
    pass


# Billing/usage-limit exhaustion markers shared by several providers.  These
# are deliberately message-based because providers disagree on the HTTP code:
# OpenAI and Kimi surface plan exhaustion as 403, Anthropic as 529, and some
# OpenAI-compatible gateways as 429.  Lowercase, so the helper lowercases once.
_QUOTA_MARKERS = (
    "quota",
    "usage limit",
    "reached your usage",
    "billing cycle",
    "billing",
    "spend limit",
    "1308",
    "5 小时",
    "5小时",
)


def _message_mentions_quota(exc: Exception) -> bool:
    """Return whether an error message describes billing/usage exhaustion.

    ``plan`` alone is deliberately excluded: it appears in many unrelated
    provider messages and would make classification unsafe.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _QUOTA_MARKERS)


def is_quota_exhaustion(exc: Exception) -> bool:
    """Return whether the error is billing exhaustion, not a transient limit.

    A mapped ``ProviderQuotaExceededError`` always counts.  A raw provider
    error counts when its message names a quota marker; this keeps rotation
    robust even when a wrapper strips the status code.
    """
    if isinstance(exc, ProviderQuotaExceededError):
        return True
    return _message_mentions_quota(exc)


def map_error(exc: Exception, provider: str | None = None) -> LLMError:
    """Map provider-specific errors to unified internal exceptions."""
    # Heuristic check for status codes before rules
    status_code = getattr(exc, "status_code", None)
    if status_code == 401:
        return LLMAuthenticationError(str(exc), provider=provider)
    if status_code == 429:
        return LLMRateLimitError(str(exc), provider=provider)
    if status_code == 403 and _message_mentions_quota(exc):
        # A 403 can be an authorization failure or a spent billing plan.  Only
        # the latter maps to exhaustion; generic 403s stay LLMAPIError so the
        # caller can distinguish them from a retryable quota window.
        return ProviderQuotaExceededError(str(exc), provider=provider)

    for rule in _GLOBAL_RULES:
        if rule.classifier(exc):
            return rule.factory(exc, provider)

    return LLMAPIError(str(exc), status_code=status_code, provider=provider)
