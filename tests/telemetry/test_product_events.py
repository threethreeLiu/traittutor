"""Privacy and failure-isolation tests for WS-13 product telemetry."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from traittutor.telemetry import (
    InMemoryProductEventSink,
    ProductEventEnvelope,
    record_product_event,
)


def _gateway_attributes() -> dict[str, str | int | bool]:
    return {
        "request_id": "request-1",
        "purpose": "courseware_instruction",
        "attempt": 1,
        "duration_ms": 50,
        "outcome": "succeeded",
        "timed_out": False,
        "degraded": False,
    }


def test_registry_rejects_unknown_payload_and_high_cardinality_label() -> None:
    with pytest.raises(ValidationError, match="unregistered event attributes"):
        ProductEventEnvelope(
            plane="operational",
            event_name="gateway.complete",
            attributes={**_gateway_attributes(), "prompt": "private user text"},
        )

    with pytest.raises(ValidationError, match="high-cardinality"):
        ProductEventEnvelope(
            plane="operational",
            event_name="gateway.complete",
            attributes=_gateway_attributes(),
            metric_labels={"request_id": "request-1"},
        )


def test_registered_plane_cannot_be_silently_changed() -> None:
    with pytest.raises(ValidationError, match="belongs to operational"):
        ProductEventEnvelope(
            plane="product_analytics",
            event_name="gateway.complete",
            attributes=_gateway_attributes(),
        )


def test_sink_failure_is_contained() -> None:
    class BrokenSink:
        def emit(self, event: ProductEventEnvelope) -> None:
            del event
            raise OSError("telemetry unavailable")

    assert record_product_event(BrokenSink(), "gateway.complete", _gateway_attributes()) is None


def test_in_memory_sink_receives_validated_bounded_event() -> None:
    sink = InMemoryProductEventSink()

    event = record_product_event(sink, "gateway.complete", _gateway_attributes())

    assert event is not None
    assert sink.events == (event,)
    assert event.metric_labels == {
        "degraded": "false",
        "outcome": "succeeded",
        "timed_out": "false",
    }
    assert "request_id" not in event.metric_labels
