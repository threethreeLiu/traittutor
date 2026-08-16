"""Quota-driven route rotation for every Gateway LLM call path.

The generation policy in :mod:`traittutor.gateway.routing` is purpose-scoped
and owns the retry/fallback economics of structured generation.  This module
generalises the same bounded loop to the Gateway and legacy factory choke
points so that chat, agents, research and agent-runtime calls also recover
from a spent billing plan instead of surfacing the provider error verbatim.

The general policy keeps three generation guarantees:

* bounded attempts (no unbounded provider-spend multiplier),
* server-selected routes only (never browser-controlled), and
* per-request isolation (the user's active model setting never changes).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import logging
import time
from typing import Generic, TypeVar

from traittutor.gateway.route_health import (
    RouteHealthStore,
    create_configured_route_health_store,
)
from traittutor.services.llm.config import LLMConfig
from traittutor.services.llm.error_mapping import is_quota_exhaustion
from traittutor.services.model_selection import LLMSelection
from traittutor.services.model_selection.runtime import resolve_llm_config_for_selection
from traittutor.services.models.local_catalog import load_local_llm
from traittutor.telemetry import (
    ProductEventSink,
    get_configured_product_event_sink,
    record_product_event,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Small by design: every LLM surface can already fan out internally, so a
# single request must not become an unbounded provider-spend multiplier.
MAX_QUOTA_ROUTES = 2
MAX_QUOTA_ATTEMPTS_PER_ROUTE = 2
QUOTA_TOTAL_TIMEOUT_SECONDS = 180.0


class QuotaRotationExhaustedError(RuntimeError):
    """No bounded route produced a result within the deadline.

    ``last_error`` and ``last_config`` carry the most recent real provider
    failure so a caller can surface the original mapped error instead of a
    generic exhaustion message.
    """

    def __init__(
        self,
        message: str,
        *,
        last_error: Exception | None = None,
        last_config: LLMConfig | None = None,
    ) -> None:
        super().__init__(message)
        self.last_error = last_error
        self.last_config = last_config


@dataclass(frozen=True)
class QuotaRotationAttempt:
    """Payload-free record of one server-selected route attempt."""

    route_index: int
    retry_index: int
    fallback_used: bool
    outcome: str
    timed_out: bool
    model: str
    provider: str


@dataclass(frozen=True)
class QuotaRotationResult(Generic[T]):
    """A successful value and its selected config plus safe attempt facts."""

    value: T
    config: LLMConfig
    attempts: tuple[QuotaRotationAttempt, ...]


RouteInvoker = Callable[[LLMConfig, float], Awaitable[T]]
SameRouteRetryable = Callable[[Exception], bool]


def enumerate_fallback_routes(
    primary: LLMConfig, *, max_routes: int = MAX_QUOTA_ROUTES
) -> tuple[LLMConfig, ...]:
    """Return the active route followed by at most ``max_routes`` unique fallbacks.

    Settings remain the default selection; the catalog is read only to recover
    one request from a separate configured route.  Invalid catalog records are
    skipped rather than becoming a browser-controlled routing surface.
    """
    routes: list[LLMConfig] = [primary]
    try:
        catalog = load_local_llm() or {}
        active = str(catalog.get("active_profile_id") or "")
        profiles = list(catalog.get("profiles") or [])
        ordered = sorted(profiles, key=lambda profile: 0 if profile.get("id") == active else 1)
        for profile in ordered:
            profile_id = str(profile.get("id") or "")
            for model in profile.get("models") or []:
                model_id = str(model.get("id") or "")
                if not profile_id or not model_id:
                    continue
                try:
                    candidate = resolve_llm_config_for_selection(LLMSelection(profile_id, model_id))
                except Exception:  # noqa: BLE001 - malformed local catalog entry
                    logger.warning("Skipping unusable fallback route", exc_info=True)
                    continue
                if not candidate.api_key or any(
                    (item.model, item.effective_url, item.binding)
                    == (candidate.model, candidate.effective_url, candidate.binding)
                    for item in routes
                ):
                    continue
                routes.append(candidate)
                if len(routes) >= max_routes:
                    return tuple(routes)
    except Exception:  # noqa: BLE001 - routing must not break the request path
        logger.warning("Unable to enumerate fallback routes", exc_info=True)
    return tuple(routes[:max_routes])


def default_same_route_retryable(exc: Exception) -> bool:
    """Return whether the same route deserves one retry before rotating.

    Quota exhaustion and credential failures rotate immediately: neither will
    clear by re-invoking the same route.  Short-lived transport and upstream
    errors (timeouts, 5xx, 429) get one quick retry first.
    """
    if is_quota_exhaustion(exc):
        return False
    message = str(exc).lower()
    if any(marker in message for marker in ("authentication", "invalid api key")):
        return False
    return any(
        marker in message
        for marker in (
            "timeout",
            "timed out",
            "connection",
            "temporar",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
        )
    )


class QuotaRotationPolicy:
    """Bound route and retry behaviour for any Gateway LLM call path.

    ``invoke`` receives the current total-time budget.  It must issue exactly
    one provider request with no inner retry; this keeps provider retries out
    of the policy's explicitly bounded attempt envelope.
    """

    def __init__(
        self,
        *,
        purpose: str = "llm:completion",
        total_timeout_seconds: float = QUOTA_TOTAL_TIMEOUT_SECONDS,
        event_sink: ProductEventSink | None = None,
        route_health_store: RouteHealthStore | None = None,
        max_routes: int = MAX_QUOTA_ROUTES,
        max_attempts_per_route: int = MAX_QUOTA_ATTEMPTS_PER_ROUTE,
        enumerate_routes: Callable[[LLMConfig], Sequence[LLMConfig]] | None = None,
    ) -> None:
        if total_timeout_seconds <= 0:
            raise ValueError("Quota rotation timeout must be greater than zero")
        if max_routes < 1 or max_routes > 8:
            raise ValueError("Quota rotation route count must be between 1 and 8")
        if max_attempts_per_route < 1 or max_attempts_per_route > 5:
            raise ValueError("Quota rotation attempts per route must be between 1 and 5")
        self._purpose = purpose
        self._total_timeout_seconds = float(total_timeout_seconds)
        self._max_routes = max_routes
        self._max_attempts_per_route = max_attempts_per_route
        self._event_sink = (
            event_sink if event_sink is not None else get_configured_product_event_sink()
        )
        self._route_health_store = (
            route_health_store
            if route_health_store is not None
            else create_configured_route_health_store()
        )
        self._enumerate_routes = (
            enumerate_routes if enumerate_routes is not None else self._enumerate_catalog_routes
        )

    def _enumerate_catalog_routes(self, primary: LLMConfig) -> Sequence[LLMConfig]:
        return enumerate_fallback_routes(primary, max_routes=self._max_routes)

    async def run(
        self,
        primary: LLMConfig,
        *,
        invoke: RouteInvoker[T],
        same_route_retryable: SameRouteRetryable,
    ) -> QuotaRotationResult[T]:
        """Invoke bounded server-selected routes without an inner retry loop.

        A retryable failure gets one retry on the same route.  All other
        failures rotate to the next configured route.  Once exhausted, the
        outcome is terminal for this request: callers must not re-enter an
        older provider path, which could duplicate an accepted request.
        """
        deadline = time.monotonic() + self._total_timeout_seconds
        attempts: list[QuotaRotationAttempt] = []
        failures: list[str] = []
        last_error: Exception | None = None
        last_config: LLMConfig | None = None
        for route_index, route in enumerate(self._enumerate_routes(primary), start=1):
            for retry_index in range(1, self._max_attempts_per_route + 1):
                started = time.monotonic()
                if not self._route_health_store.allows(route):
                    attempt = QuotaRotationAttempt(
                        route_index=route_index,
                        retry_index=retry_index,
                        fallback_used=route_index > 1,
                        outcome="circuit_open",
                        timed_out=False,
                        model=route.model,
                        provider=route.provider_name,
                    )
                    attempts.append(attempt)
                    self._record_attempt(attempt, duration_ms=0, ordinal=len(attempts))
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    attempt = QuotaRotationAttempt(
                        route_index=route_index,
                        retry_index=retry_index,
                        fallback_used=route_index > 1,
                        outcome="timeout",
                        timed_out=True,
                        model=route.model,
                        provider=route.provider_name,
                    )
                    attempts.append(attempt)
                    self._record_attempt(attempt, duration_ms=0, ordinal=len(attempts))
                    raise QuotaRotationExhaustedError("quota rotation deadline exceeded")
                try:
                    value = await asyncio.wait_for(invoke(route, remaining), timeout=remaining)
                except Exception as exc:  # noqa: BLE001 - caller classifies provider output
                    last_error = exc
                    last_config = route
                    timed_out = (
                        isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
                    )
                    attempt = QuotaRotationAttempt(
                        route_index=route_index,
                        retry_index=retry_index,
                        fallback_used=route_index > 1,
                        outcome="timeout" if timed_out else "error",
                        timed_out=timed_out,
                        model=route.model,
                        provider=route.provider_name,
                    )
                    attempts.append(attempt)
                    self._record_attempt(
                        attempt,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        ordinal=len(attempts),
                    )
                    self._route_health_store.record_failure(route)
                    failures.append(f"{route.model}:{type(exc).__name__}")
                    if retry_index == 1 and same_route_retryable(exc):
                        continue
                    break
                attempt = QuotaRotationAttempt(
                    route_index=route_index,
                    retry_index=retry_index,
                    fallback_used=route_index > 1,
                    outcome="success",
                    timed_out=False,
                    model=route.model,
                    provider=route.provider_name,
                )
                attempts.append(attempt)
                self._route_health_store.record_success(route)
                self._record_attempt(
                    attempt,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    ordinal=len(attempts),
                )
                return QuotaRotationResult(value=value, config=route, attempts=tuple(attempts))
        logger.warning("quota_rotation_policy_exhausted attempts=%s", len(attempts))
        raise QuotaRotationExhaustedError(
            "all bounded quota rotation routes failed",
            last_error=last_error,
            last_config=last_config,
        ) from (RuntimeError(",".join(failures)) if failures else None)

    def _record_attempt(
        self,
        attempt: QuotaRotationAttempt,
        *,
        duration_ms: int,
        ordinal: int,
    ) -> None:
        """Emit only server-derived route facts; never expose prompt/config secrets."""
        record_product_event(
            self._event_sink,
            "gateway.route_attempt",
            {
                "purpose": self._purpose,
                "attempt": ordinal,
                "route_index": attempt.route_index,
                "retry_index": attempt.retry_index,
                "fallback_used": attempt.fallback_used,
                "duration_ms": max(0, duration_ms),
                "outcome": attempt.outcome,
                "timed_out": attempt.timed_out,
                "degraded": False,
                "model": attempt.model,
                "provider": attempt.provider,
                "route": attempt.provider,
            },
        )


__all__ = [
    "MAX_QUOTA_ATTEMPTS_PER_ROUTE",
    "MAX_QUOTA_ROUTES",
    "QUOTA_TOTAL_TIMEOUT_SECONDS",
    "QuotaRotationAttempt",
    "QuotaRotationExhaustedError",
    "QuotaRotationPolicy",
    "QuotaRotationResult",
    "SameRouteRetryable",
    "RouteInvoker",
    "default_same_route_retryable",
    "enumerate_fallback_routes",
]
