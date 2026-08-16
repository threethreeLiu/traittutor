"""Server-owned bounded routing policies for Gateway consumers.

The policy in this module deliberately covers only structured generation.  It
does not try to make a global promise about agentic tool loops, streams, or
provider health.  Its job is narrower: prevent one generation request from
combining the runner's route retry with the legacy provider client's retry.

The bounded loop itself lives in :mod:`traittutor.gateway.quota_rotation`;
this module keeps the generation-facing names and purpose guard so the
generation rollout flag, its telemetry contract, and call-site tests remain
unchanged.
"""

from __future__ import annotations

import logging

from traittutor.gateway.quota_rotation import (
    QuotaRotationPolicy,
    enumerate_fallback_routes,
)
from traittutor.gateway.route_health import RouteHealthStore
from traittutor.services.llm.config import LLMConfig
from traittutor.telemetry import ProductEventSink

logger = logging.getLogger(__name__)

# The route policy owns these limits when its opt-in flag is enabled.  They are
# intentionally small: structured generation can already fan out internally,
# so a click must not become an unbounded provider-spend multiplier.  Four
# routes keeps the active model plus three distinct fallbacks (e.g. MiniMax →
# DeepSeek → Zhipu → Kimi) so one provider's malformed JSON or slow responses
# cannot exhaust every attempt — the active catalog already lists several
# models with strong structured-output behaviour.
MAX_GENERATION_ROUTES = 4
MAX_ATTEMPTS_PER_ROUTE = 2
GENERATION_TOTAL_TIMEOUT_SECONDS = 240.0


def generation_route_configs(primary: LLMConfig) -> tuple[LLMConfig, ...]:
    """Return the active generation route followed by at most three fallbacks.

    Settings remain the default selection; the catalog is read only to recover
    one request from a separate configured route.  Invalid catalog records are
    skipped rather than becoming a browser-controlled routing surface.

    Routes are deduplicated by *model*, not by endpoint/binding: a provider's
    malformed structured output is a model behaviour, not a transport one, so
    the same model on a second binding would burn an attempt without adding a
    genuinely different fallback (e.g. MiniMax-M3 -> DeepSeek -> Zhipu -> Kimi).
    """
    candidates = enumerate_fallback_routes(primary, max_routes=8)
    seen: set[str] = set()
    unique: list[LLMConfig] = []
    for route in candidates:
        if route.model in seen:
            continue
        seen.add(route.model)
        unique.append(route)
        if len(unique) >= MAX_GENERATION_ROUTES:
            break
    return tuple(unique)


class GenerationRoutePolicy(QuotaRotationPolicy):
    """Bound structured-generation route and retry behaviour.

    Kept as a thin subclass of :class:`QuotaRotationPolicy` so the generation
    rollout flag and telemetry contract are unchanged.  Only ``generate:*``
    purposes are accepted, and route enumeration stays behind
    :func:`generation_route_configs` so call-site tests can replace it.
    """

    def __init__(
        self,
        *,
        purpose: str = "generate:structured",
        total_timeout_seconds: float = GENERATION_TOTAL_TIMEOUT_SECONDS,
        event_sink: ProductEventSink | None = None,
        route_health_store: RouteHealthStore | None = None,
    ) -> None:
        if not purpose.startswith("generate:"):
            raise ValueError("Generation route policy only accepts generate:* purposes")
        super().__init__(
            purpose=purpose,
            total_timeout_seconds=total_timeout_seconds,
            event_sink=event_sink,
            route_health_store=route_health_store,
            max_routes=MAX_GENERATION_ROUTES,
            max_attempts_per_route=MAX_ATTEMPTS_PER_ROUTE,
            enumerate_routes=lambda primary: generation_route_configs(primary),
        )


__all__ = [
    "GENERATION_TOTAL_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS_PER_ROUTE",
    "MAX_GENERATION_ROUTES",
    "GenerationRoutePolicy",
    "generation_route_configs",
]
