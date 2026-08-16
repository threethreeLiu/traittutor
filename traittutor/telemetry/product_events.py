"""Versioned, privacy-bounded operational telemetry.

Operational telemetry, security audit, and consented product analytics are
separate data planes.  An event registered in one plane cannot silently be
promoted into another.  This first WS-13 slice registers only operational
health events; security and product-analytics events require their own typed
registrations and retention/authorization policies.

Payload keys are deny-by-default.  Prompt, answer, chat, persona, source body,
credentials, endpoints, and user text are deliberately absent from the
registry.  Correlation IDs are useful for debugging but high-cardinality, so
they remain attributes and can never become metric labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from threading import Lock
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, TypeAlias
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

TelemetryPlane: TypeAlias = Literal[
    "operational",
    "security_audit",
    "product_analytics",
]
TelemetryScalar: TypeAlias = str | int | float | bool


@dataclass(frozen=True)
class ProductEventSpec:
    """Server-owned whitelist for one event type."""

    plane: TelemetryPlane
    required_attributes: frozenset[str]
    optional_attributes: frozenset[str]
    metric_label_fields: frozenset[str]

    @property
    def allowed_attributes(self) -> frozenset[str]:
        return self.required_attributes | self.optional_attributes


_COMMON_REQUIRED = frozenset({"attempt", "duration_ms", "outcome", "timed_out", "degraded"})

# Keep this registry intentionally small.  Every addition is a privacy and
# cardinality decision, not an invitation to forward arbitrary metadata.
EVENT_REGISTRY: Mapping[str, ProductEventSpec] = MappingProxyType(
    {
        "gateway.complete": ProductEventSpec(
            plane="operational",
            required_attributes=_COMMON_REQUIRED | frozenset({"request_id", "purpose"}),
            optional_attributes=frozenset(
                {
                    "model",
                    "provider",
                    "route",
                    "fallback_used",
                    "total_tokens",
                    "cost_picousd",
                }
            ),
            # Provider and route are server-configured, bounded dimensions
            # needed for latency/error SLOs. Model/request/purpose remain
            # attributes because their cardinality can grow independently.
            metric_label_fields=frozenset(
                {"provider", "route", "outcome", "timed_out", "degraded"}
            ),
        ),
        "gateway.route_attempt": ProductEventSpec(
            plane="operational",
            required_attributes=_COMMON_REQUIRED
            | frozenset({"purpose", "route_index", "retry_index"}),
            optional_attributes=frozenset({"model", "provider", "route", "fallback_used"}),
            # Only configuration-owned/bounded fields may be labels. Purpose,
            # model and retry index remain attributes to cap cardinality.
            metric_label_fields=frozenset(
                {"provider", "route", "outcome", "timed_out", "degraded", "fallback_used"}
            ),
        ),
        "courseware_orchestrator.run": ProductEventSpec(
            plane="operational",
            required_attributes=_COMMON_REQUIRED
            | frozenset({"graph_id", "generation_run_id", "fallback_used"}),
            optional_attributes=frozenset({"run_id", "status"}),
            metric_label_fields=frozenset(
                {"outcome", "timed_out", "degraded", "fallback_used", "status"}
            ),
        ),
    }
)


class ProductEventEnvelope(BaseModel):
    """Frozen v1 event envelope validated against ``EVENT_REGISTRY``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=64)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    plane: TelemetryPlane
    event_name: str = Field(min_length=1, max_length=96)
    attributes: dict[str, TelemetryScalar] = Field(max_length=24)
    metric_labels: dict[str, str] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def _validate_registered_contract(self) -> "ProductEventEnvelope":
        spec = EVENT_REGISTRY.get(self.event_name)
        if spec is None:
            raise ValueError(f"unregistered product event: {self.event_name}")
        if self.plane != spec.plane:
            raise ValueError(f"event {self.event_name} belongs to {spec.plane}, not {self.plane}")

        keys = frozenset(self.attributes)
        missing = spec.required_attributes - keys
        unknown = keys - spec.allowed_attributes
        if missing:
            raise ValueError(f"missing required event attributes: {sorted(missing)}")
        if unknown:
            raise ValueError(f"unregistered event attributes: {sorted(unknown)}")

        label_keys = frozenset(self.metric_labels)
        if not label_keys <= spec.metric_label_fields:
            raise ValueError(
                f"high-cardinality or unregistered metric labels: "
                f"{sorted(label_keys - spec.metric_label_fields)}"
            )
        if not label_keys <= keys:
            raise ValueError(
                f"metric labels missing matching attributes: {sorted(label_keys - keys)}"
            )
        for key, value in self.metric_labels.items():
            if value != _label_value(self.attributes[key]):
                raise ValueError(f"metric label {key} does not match its attribute")
        for key, attribute_value in self.attributes.items():
            if isinstance(attribute_value, str) and len(attribute_value) > 192:
                raise ValueError(f"event attribute {key} exceeds bounded length")
        return self


class ProductEventSink(Protocol):
    """Non-blocking sink boundary for already validated envelopes."""

    def emit(self, event: ProductEventEnvelope) -> None:
        """Accept one event without mutating product state."""


class NoOpProductEventSink:
    """Default sink: telemetry is opt-in and has zero persistence by default."""

    def emit(self, event: ProductEventEnvelope) -> None:
        del event


class InMemoryProductEventSink:
    """Thread-safe test sink with no filesystem or cross-owner persistence."""

    def __init__(self) -> None:
        self._events: list[ProductEventEnvelope] = []
        self._lock = Lock()

    def emit(self, event: ProductEventEnvelope) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> tuple[ProductEventEnvelope, ...]:
        with self._lock:
            return tuple(self._events)


def _label_value(value: TelemetryScalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def record_product_event(
    sink: ProductEventSink,
    event_name: str,
    attributes: Mapping[str, TelemetryScalar],
) -> ProductEventEnvelope | None:
    """Validate and best-effort emit without affecting the business result.

    Both envelope validation and sink failures are contained here.  Callers
    must pass only server-derived values; arbitrary request metadata is never
    forwarded.
    """

    try:
        spec = EVENT_REGISTRY[event_name]
        copied = dict(attributes)
        labels = {
            key: _label_value(copied[key]) for key in spec.metric_label_fields if key in copied
        }
        event = ProductEventEnvelope(
            plane=spec.plane,
            event_name=event_name,
            attributes=copied,
            metric_labels=labels,
        )
        sink.emit(event)
        return event
    except Exception as exc:  # noqa: BLE001 - telemetry must never block product behavior
        # Do not log exception messages: a future sink could include private
        # transport details in them.  The bounded type is enough to diagnose
        # sink health without creating a second untyped telemetry channel.
        logger.warning(
            "product_event_emit_failed event_name=%s error_type=%s",
            event_name,
            type(exc).__name__,
        )
        return None
