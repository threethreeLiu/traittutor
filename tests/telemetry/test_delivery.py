"""Delivery tests prove telemetry stays aggregate-only and fail-open."""

from __future__ import annotations

import json
from pathlib import Path

from traittutor.telemetry import (
    AsyncBoundedProductEventSink,
    NoOpProductEventSink,
    ProductEventEnvelope,
    TelemetryBatch,
    TelemetryDeliveryAlert,
    create_product_event_sink,
    get_configured_product_event_sink,
)
from traittutor.telemetry.delivery import (
    JsonlTelemetryBatchWriter,
    reset_configured_product_event_sink_for_tests,
)


def _event(
    *,
    request_id: str = "request-secret-1",
    duration_ms: int = 23,
    total_tokens: int | None = None,
    cost_picousd: int | None = None,
) -> ProductEventEnvelope:
    attributes = {
        "request_id": request_id,
        "purpose": "courseware_instruction",
        "attempt": 1,
        "duration_ms": duration_ms,
        "outcome": "succeeded",
        "timed_out": False,
        "degraded": False,
        "provider": "configured-provider",
        "route": "openai_compat",
        "model": "model-private-detail",
    }
    if total_tokens is not None:
        attributes["total_tokens"] = total_tokens
    if cost_picousd is not None:
        attributes["cost_picousd"] = cost_picousd
    return ProductEventEnvelope(
        plane="operational",
        event_name="gateway.complete",
        attributes=attributes,
        metric_labels={
            "provider": "configured-provider",
            "route": "openai_compat",
            "outcome": "succeeded",
            "timed_out": "false",
            "degraded": "false",
        },
    )


def test_jsonl_delivery_persists_aggregates_without_raw_attributes(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    sink = AsyncBoundedProductEventSink(
        writer=JsonlTelemetryBatchWriter(path),
        start_worker=False,
    )
    sink.emit(_event(duration_ms=23, total_tokens=13, cost_picousd=17))
    sink.emit(
        _event(request_id="request-secret-2", duration_ms=42, total_tokens=29, cost_picousd=31)
    )
    sink.flush()

    payload = json.loads(path.read_text(encoding="utf-8"))
    aggregate = payload["aggregates"][0]
    assert aggregate["count"] == 2
    assert aggregate["duration_ms_total"] == 65
    assert aggregate["duration_ms_max"] == 42
    assert aggregate["total_tokens"] == 42
    assert aggregate["total_cost_picousd"] == 48
    assert aggregate["metric_labels"]["provider"] == "configured-provider"
    serialized = json.dumps(payload)
    for forbidden in (
        "request-secret-1",
        "request-secret-2",
        "courseware_instruction",
        "model-private-detail",
        "purpose",
        "request_id",
        "attributes",
    ):
        assert forbidden not in serialized


def test_delivery_omits_cost_when_any_aggregate_member_is_unpriced(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    sink = AsyncBoundedProductEventSink(
        writer=JsonlTelemetryBatchWriter(path),
        start_worker=False,
    )
    sink.emit(_event(cost_picousd=17))
    sink.emit(_event(request_id="unpriced", cost_picousd=None))
    sink.flush()

    aggregate = json.loads(path.read_text(encoding="utf-8"))["aggregates"][0]
    assert "total_cost_picousd" not in aggregate


def test_bounded_queue_drops_without_blocking_product_path() -> None:
    class Writer:
        def write(self, batch: TelemetryBatch) -> None:
            del batch

    sink = AsyncBoundedProductEventSink(writer=Writer(), max_pending=1, start_worker=False)
    sink.emit(_event())
    sink.emit(_event(request_id="another-request"))

    assert sink.stats.accepted == 1
    assert sink.stats.dropped == 1
    sink.flush()
    assert sink.stats.written_batches == 1


def test_delivery_failure_retains_batch_and_alerts_at_threshold() -> None:
    class BrokenWriter:
        def __init__(self) -> None:
            self.calls = 0

        def write(self, batch: TelemetryBatch) -> None:
            del batch
            self.calls += 1
            raise OSError("private transport detail")

    class Alerts:
        def __init__(self) -> None:
            self.alerts: list[TelemetryDeliveryAlert] = []

        def alert(self, alert: TelemetryDeliveryAlert) -> None:
            self.alerts.append(alert)

    writer = BrokenWriter()
    alerts = Alerts()
    sink = AsyncBoundedProductEventSink(
        writer=writer,
        alert_hook=alerts,
        alert_failure_threshold=2,
        start_worker=False,
    )
    sink.emit(_event())
    sink.flush()
    sink.flush()
    sink.flush()

    assert writer.calls == 3
    assert sink.stats.consecutive_failures == 3
    assert sink.stats.written_batches == 0
    assert alerts.alerts == [
        TelemetryDeliveryAlert(reason="consecutive_delivery_failures", failure_count=2, threshold=2)
    ]


def test_environment_selection_is_explicit_and_invalid_config_rolls_back(tmp_path: Path) -> None:
    assert isinstance(create_product_event_sink(environ={}), NoOpProductEventSink)
    assert isinstance(
        create_product_event_sink(environ={"TRAITTUTOR_TELEMETRY_SINK": "jsonl"}),
        NoOpProductEventSink,
    )

    sink = create_product_event_sink(
        environ={
            "TRAITTUTOR_TELEMETRY_SINK": "jsonl",
            "TRAITTUTOR_TELEMETRY_OUTBOX_PATH": str(tmp_path / "outbox.jsonl"),
            "TRAITTUTOR_TELEMETRY_BATCH_SIZE": "2",
        }
    )
    assert isinstance(sink, AsyncBoundedProductEventSink)
    sink.close()


def test_process_sink_is_opt_in_at_composition_root(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    reset_configured_product_event_sink_for_tests()
    monkeypatch.setenv("TRAITTUTOR_TELEMETRY_SINK", "jsonl")
    monkeypatch.setenv("TRAITTUTOR_TELEMETRY_OUTBOX_PATH", str(tmp_path / "outbox.jsonl"))
    try:
        assert isinstance(get_configured_product_event_sink(), AsyncBoundedProductEventSink)
    finally:
        reset_configured_product_event_sink_for_tests()
