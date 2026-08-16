"""Durable, cross-process circuit facts for bounded Gateway route policies.

The store contains no prompts, URLs, API keys, user identifiers or provider
responses.  It is deliberately an opt-in process-shared safety valve: a bad
or absent configuration leaves route selection unchanged rather than creating
an implicit persistent state file in an unknown deployment location.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock
import time
from typing import Mapping, Protocol

from traittutor.services.llm.config import LLMConfig
from traittutor.unified_storage import SectionedRecordStore

logger = logging.getLogger(__name__)

ROUTE_HEALTH_PATH_ENV = "TRAITTUTOR_GATEWAY_ROUTE_HEALTH_PATH"
ROUTE_CIRCUIT_FAILURE_THRESHOLD_ENV = "TRAITTUTOR_GATEWAY_ROUTE_CIRCUIT_FAILURE_THRESHOLD"
ROUTE_CIRCUIT_COOLDOWN_SECONDS_ENV = "TRAITTUTOR_GATEWAY_ROUTE_CIRCUIT_COOLDOWN_SECONDS"

_SCHEMA_VERSION = 1


class RouteHealthStore(Protocol):
    """Server-only route-health state used by a bounded generation policy."""

    def allows(self, config: LLMConfig, *, now: float | None = None) -> bool:
        """Return whether the configured route is outside its open circuit."""

    def record_success(self, config: LLMConfig) -> None:
        """Clear a route's consecutive failures after one successful attempt."""

    def record_failure(self, config: LLMConfig, *, now: float | None = None) -> bool:
        """Record failure and return whether this failure opens the circuit."""


class NoOpRouteHealthStore:
    """Rollback/default state: never reject a route and never persist facts."""

    def allows(self, config: LLMConfig, *, now: float | None = None) -> bool:
        del config, now
        return True

    def record_success(self, config: LLMConfig) -> None:
        del config

    def record_failure(self, config: LLMConfig, *, now: float | None = None) -> bool:
        del config, now
        return False


class FileRouteHealthStore:
    """Lock-protected route health with a bounded consecutive-failure circuit."""

    def __init__(
        self,
        path: Path | str,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        if failure_threshold < 1 or failure_threshold > 100:
            raise ValueError("Route circuit failure threshold must be between 1 and 100")
        if cooldown_seconds <= 0 or cooldown_seconds > 3_600:
            raise ValueError("Route circuit cooldown must be between 0 and 3600 seconds")
        self.path = Path(path)
        if not self.path.name or self.path.is_dir():
            raise ValueError("Route health path must name a file")
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = float(cooldown_seconds)
        self._lock = Lock()
        self._store = SectionedRecordStore(
            "gateway_route_health", "system", schema_version=_SCHEMA_VERSION, legacy_path=self.path
        )

    def _adapter(self) -> SectionedRecordStore:
        return self._store

    def allows(self, config: LLMConfig, *, now: float | None = None) -> bool:
        instant = time.time() if now is None else now
        with self._lock, self._store.locked():
            routes = self._load_unlocked()
            entry = routes.get(_route_key(config), {})
            opened_until = _nonnegative_number(entry.get("opened_until"))
            return opened_until <= instant

    def record_success(self, config: LLMConfig) -> None:
        with self._lock, self._store.locked():
            routes = self._load_unlocked()
            key = _route_key(config)
            if key not in routes:
                return
            del routes[key]
            self._save_unlocked(routes)

    def record_failure(self, config: LLMConfig, *, now: float | None = None) -> bool:
        instant = time.time() if now is None else now
        with self._lock, self._store.locked():
            routes = self._load_unlocked()
            key = _route_key(config)
            prior = routes.get(key, {})
            failures = min(100, int(_nonnegative_number(prior.get("failures"))) + 1)
            opened_until = _nonnegative_number(prior.get("opened_until"))
            opened = failures >= self._failure_threshold
            routes[key] = {
                "failures": failures,
                "opened_until": instant + self._cooldown_seconds if opened else opened_until,
            }
            self._save_unlocked(routes)
            return opened

    def _load_unlocked(self) -> dict[str, dict[str, float | int]]:
        try:
            routes = {}
            for value in self._adapter().snapshot()["routes"]:
                key = value.get("route_key")
                if not isinstance(key, str):
                    continue
                routes[key] = {
                    "failures": int(_nonnegative_number(value.get("failures"))),
                    "opened_until": _nonnegative_number(value.get("opened_until")),
                }
            return routes
        except (OSError, ValueError, TypeError) as exc:
            # A corrupt non-critical health file must not take down generation.
            logger.warning("gateway_route_health_unreadable error_type=%s", type(exc).__name__)
            return {}

    def _save_unlocked(self, routes: Mapping[str, Mapping[str, float | int]]) -> None:
        self._adapter().replace_all(
            {
                "schema_version": _SCHEMA_VERSION,
                "routes": [
                    {"route_key": key, **dict(value)} for key, value in sorted(routes.items())
                ],
            }
        )


def create_configured_route_health_store(
    environ: Mapping[str, str] | None = None,
) -> RouteHealthStore:
    """Build an explicit durable store or safely roll configuration back to NoOp."""
    source = os.environ if environ is None else environ
    raw_path = source.get(ROUTE_HEALTH_PATH_ENV, "").strip()
    if not raw_path:
        return NoOpRouteHealthStore()
    try:
        return FileRouteHealthStore(
            raw_path,
            failure_threshold=_bounded_int(
                source, ROUTE_CIRCUIT_FAILURE_THRESHOLD_ENV, default=3, minimum=1, maximum=100
            ),
            cooldown_seconds=_bounded_float(
                source,
                ROUTE_CIRCUIT_COOLDOWN_SECONDS_ENV,
                default=60.0,
                minimum=1.0,
                maximum=3_600.0,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - health must never block rollback
        logger.warning("gateway_route_health_disabled error_type=%s", type(exc).__name__)
        return NoOpRouteHealthStore()


def _route_key(config: LLMConfig) -> str:
    """Use only non-secret configured identifiers; endpoints remain excluded."""
    return f"{config.provider_name}:{config.binding}:{config.model}"


def _nonnegative_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return 0.0
    return float(value)


def _bounded_int(
    environ: Mapping[str, str], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} outside bounds")
    return value


def _bounded_float(
    environ: Mapping[str, str], name: str, *, default: float, minimum: float, maximum: float
) -> float:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} outside bounds")
    return value


__all__ = [
    "FileRouteHealthStore",
    "NoOpRouteHealthStore",
    "ROUTE_CIRCUIT_COOLDOWN_SECONDS_ENV",
    "ROUTE_CIRCUIT_FAILURE_THRESHOLD_ENV",
    "ROUTE_HEALTH_PATH_ENV",
    "RouteHealthStore",
    "create_configured_route_health_store",
]
