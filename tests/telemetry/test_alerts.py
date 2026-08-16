"""Privacy and failure-boundary tests for opt-in telemetry webhook alerts."""

from __future__ import annotations

from typing import Any

from traittutor.telemetry import (
    AsyncBoundedProductEventSink,
    NoOpTelemetryAlertHook,
    PagerDutyTelemetryAlertHook,
    SmtpTelemetryAlertHook,
    TelemetryDeliveryAlert,
    WebhookTelemetryAlertHook,
    create_configured_telemetry_alert_hook,
    create_product_event_sink,
    record_product_event,
)


def _threshold_alert() -> TelemetryDeliveryAlert:
    return TelemetryDeliveryAlert(
        reason="consecutive_delivery_failures",
        failure_count=3,
        threshold=3,
    )


def test_https_webhook_sends_only_fixed_alert_contract(monkeypatch: Any) -> None:
    received: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Any, *, timeout: float) -> Response:
        received["url"] = request.full_url
        received["body"] = request.data.decode("utf-8")
        received["headers"] = dict(request.header_items())
        received["timeout"] = timeout
        return Response()

    monkeypatch.setattr("traittutor.telemetry.alerts.urlopen", fake_urlopen)
    WebhookTelemetryAlertHook("https://alerts.example.test/traittutor", timeout_seconds=1.5).alert(
        TelemetryDeliveryAlert(
            reason="consecutive_delivery_failures",
            failure_count=3,
            threshold=3,
        )
    )

    assert received["url"] == "https://alerts.example.test/traittutor"
    assert received["timeout"] == 1.5
    assert received["body"] == (
        '{"schema_version":"v1","reason":"consecutive_delivery_failures",'
        '"failure_count":3,"threshold":3}'
    )
    assert "Authorization" not in received["headers"]
    assert "Content-type" in received["headers"]


def test_pagerduty_sends_fixed_threshold_contract_only(monkeypatch: Any) -> None:
    received: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Any, *, timeout: float) -> Response:
        received["url"] = request.full_url
        received["body"] = request.data.decode("utf-8")
        received["timeout"] = timeout
        return Response()

    monkeypatch.setattr("traittutor.telemetry.alerts.urlopen", fake_urlopen)
    PagerDutyTelemetryAlertHook("a" * 32, timeout_seconds=1.5).alert(_threshold_alert())

    assert received["url"] == "https://events.pagerduty.com/v2/enqueue"
    assert received["timeout"] == 1.5
    assert received["body"] == (
        '{"routing_key":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","event_action":"trigger",'
        '"payload":{"summary":"TraitTutor telemetry delivery failure threshold reached",'
        '"source":"traittutor-telemetry","severity":"error","custom_details":'
        '{"schema_version":"v1","reason":"consecutive_delivery_failures",'
        '"failure_count":3,"threshold":3}}}'
    )
    assert "prompt" not in received["body"]
    assert "outbox" not in received["body"]


def test_smtp_uses_starttls_and_fixed_threshold_body(monkeypatch: Any) -> None:
    received: dict[str, Any] = {}

    class FakeSmtp:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            received.update(host=host, port=port, timeout=timeout)

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def ehlo(self) -> None:
            received["ehlo"] = received.get("ehlo", 0) + 1

        def starttls(self, *, context: object) -> None:
            received["starttls"] = context

        def login(self, username: str, password: str) -> None:
            received["login"] = (username, password)

        def send_message(self, message: Any, **kwargs: Any) -> None:
            received["message"] = message
            received["send_kwargs"] = kwargs

    monkeypatch.setattr("traittutor.telemetry.alerts.smtplib.SMTP", FakeSmtp)
    SmtpTelemetryAlertHook(
        "smtp.example.test",
        587,
        "alerts@example.test",
        ("ops@example.test",),
        username="service-account",
        password="server-only-password",
        timeout_seconds=1.5,
    ).alert(_threshold_alert())

    assert received["host"] == "smtp.example.test"
    assert received["port"] == 587
    assert received["timeout"] == 1.5
    assert received["ehlo"] == 2
    assert received["starttls"] is not None
    assert received["login"] == ("service-account", "server-only-password")
    assert received["send_kwargs"] == {
        "from_addr": "alerts@example.test",
        "to_addrs": ["ops@example.test"],
    }
    body = received["message"].get_content()
    assert body == (
        "Telemetry delivery requires attention.\n"
        "schema_version: v1\nreason: consecutive_delivery_failures\n"
        "failure_count: 3\nthreshold: 3\n"
    )
    assert "password" not in body


def test_invalid_or_missing_webhook_configuration_is_noop() -> None:
    assert isinstance(create_configured_telemetry_alert_hook({}), NoOpTelemetryAlertHook)
    assert isinstance(
        create_configured_telemetry_alert_hook(
            {"TRAITTUTOR_TELEMETRY_ALERT_WEBHOOK_URL": "http://unsafe.test"}
        ),
        NoOpTelemetryAlertHook,
    )


def test_partial_or_unsafe_pagerduty_and_smtp_configuration_is_noop() -> None:
    assert isinstance(
        create_configured_telemetry_alert_hook(
            {"TRAITTUTOR_TELEMETRY_ALERT_PAGERDUTY_ROUTING_KEY": "short"}
        ),
        NoOpTelemetryAlertHook,
    )


def test_complete_pagerduty_and_smtp_configuration_create_opt_in_adapters() -> None:
    assert isinstance(
        create_configured_telemetry_alert_hook(
            {"TRAITTUTOR_TELEMETRY_ALERT_PAGERDUTY_ROUTING_KEY": "a" * 32}
        ),
        PagerDutyTelemetryAlertHook,
    )
    assert isinstance(
        create_configured_telemetry_alert_hook(
            {
                "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_SMTP_HOST": "smtp.example.test",
                "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_FROM": "alerts@example.test",
                "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_TO": "ops@example.test",
            }
        ),
        SmtpTelemetryAlertHook,
    )
    assert isinstance(
        create_configured_telemetry_alert_hook(
            {
                "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_SMTP_HOST": "smtp.example.test",
                "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_FROM": "alerts@example.test",
            }
        ),
        NoOpTelemetryAlertHook,
    )


def test_configured_webhook_alert_is_fail_open_and_receives_only_threshold(
    monkeypatch: Any, tmp_path: Any
) -> None:
    received: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Any, *, timeout: float) -> Response:
        received["body"] = request.data.decode("utf-8")
        received["timeout"] = timeout
        return Response()

    def fail_write(_self: object, _batch: object) -> None:
        raise OSError("private outbox detail")

    monkeypatch.setattr("traittutor.telemetry.alerts.urlopen", fake_urlopen)
    monkeypatch.setattr("traittutor.telemetry.delivery.JsonlTelemetryBatchWriter.write", fail_write)
    sink = create_product_event_sink(
        environ={
            "TRAITTUTOR_TELEMETRY_SINK": "jsonl",
            "TRAITTUTOR_TELEMETRY_OUTBOX_PATH": str(tmp_path / "private-outbox.jsonl"),
            "TRAITTUTOR_TELEMETRY_ALERT_FAILURE_THRESHOLD": "1",
            "TRAITTUTOR_TELEMETRY_ALERT_WEBHOOK_URL": "https://alerts.example.test/traittutor",
        }
    )
    assert isinstance(sink, AsyncBoundedProductEventSink)
    try:
        record_product_event(
            sink,
            "gateway.route_attempt",
            {
                "purpose": "generate:test",
                "attempt": 1,
                "route_index": 1,
                "retry_index": 1,
                "fallback_used": False,
                "duration_ms": 1,
                "outcome": "error",
                "timed_out": False,
                "degraded": False,
                "model": "private-model",
                "provider": "private-provider",
                "route": "private-route",
            },
        )
        sink.flush()
    finally:
        sink.close()

    assert received["timeout"] == 2.0
    assert received["body"] == (
        '{"schema_version":"v1","reason":"consecutive_delivery_failures",'
        '"failure_count":1,"threshold":1}'
    )
    assert "private" not in received["body"]
    assert isinstance(
        create_configured_telemetry_alert_hook(
            {"TRAITTUTOR_TELEMETRY_ALERT_WEBHOOK_URL": "https://token@unsafe.test"}
        ),
        NoOpTelemetryAlertHook,
    )
