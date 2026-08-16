"""Opt-in, payload-minimal delivery for telemetry health alerts.

This is deliberately not a general webhook client.  It only delivers the
fixed ``TelemetryDeliveryAlert`` contract when the aggregate outbox itself is
repeatedly unavailable.  Product events, prompts, users, error messages and
outbox locations never cross this boundary.
"""

from __future__ import annotations

from email.message import EmailMessage
import json
import logging
import os
import re
import smtplib
import ssl
from typing import Mapping
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .delivery import NoOpTelemetryAlertHook, TelemetryAlertHook, TelemetryDeliveryAlert

logger = logging.getLogger(__name__)

TELEMETRY_ALERT_WEBHOOK_URL_ENV = "TRAITTUTOR_TELEMETRY_ALERT_WEBHOOK_URL"
TELEMETRY_ALERT_WEBHOOK_TIMEOUT_MS_ENV = "TRAITTUTOR_TELEMETRY_ALERT_WEBHOOK_TIMEOUT_MS"
TELEMETRY_ALERT_PAGERDUTY_EVENTS_URL_ENV = "TRAITTUTOR_TELEMETRY_ALERT_PAGERDUTY_EVENTS_URL"
TELEMETRY_ALERT_PAGERDUTY_ROUTING_KEY_ENV = "TRAITTUTOR_TELEMETRY_ALERT_PAGERDUTY_ROUTING_KEY"
TELEMETRY_ALERT_EMAIL_SMTP_HOST_ENV = "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_SMTP_HOST"
TELEMETRY_ALERT_EMAIL_SMTP_PORT_ENV = "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_SMTP_PORT"
TELEMETRY_ALERT_EMAIL_FROM_ENV = "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_FROM"
TELEMETRY_ALERT_EMAIL_TO_ENV = "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_TO"
TELEMETRY_ALERT_EMAIL_USERNAME_ENV = "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_USERNAME"
TELEMETRY_ALERT_EMAIL_PASSWORD_ENV = "TRAITTUTOR_TELEMETRY_ALERT_EMAIL_PASSWORD"

_PAGERDUTY_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
_ROUTING_KEY_RE = re.compile(r"[A-Za-z0-9_-]{16,128}")


class WebhookTelemetryAlertHook:
    """POST the fixed alert contract to an explicitly configured HTTPS URL."""

    def __init__(self, url: str, *, timeout_seconds: float = 2.0) -> None:
        self._url = _validated_https_url(url)
        if not 0.1 <= timeout_seconds <= 10.0:
            raise ValueError("Webhook alert timeout must be between 0.1 and 10 seconds")
        self._timeout_seconds = timeout_seconds

    def alert(self, alert: TelemetryDeliveryAlert) -> None:
        """Best-effort send of a schema-fixed, non-sensitive alert body.

        Callers already contain failures from this method.  Let network errors
        propagate to that fail-open boundary so tests can prove notification
        failure never changes product or outbox semantics.
        """
        payload = json.dumps(
            {
                "schema_version": "v1",
                "reason": alert.reason,
                "failure_count": alert.failure_count,
                "threshold": alert.threshold,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        outbound = Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "TraitTutorTelemetry/1"},
            method="POST",
        )
        with urlopen(outbound, timeout=self._timeout_seconds):  # noqa: S310 - URL is HTTPS validated
            pass


class PagerDutyTelemetryAlertHook:
    """Send the fixed alert contract to the PagerDuty Events v2 API.

    The routing key is deployment configuration, never part of a telemetry
    event, receipt, log payload, or exception.  Custom details are deliberately
    limited to the same fixed threshold contract used by the generic webhook.
    """

    def __init__(
        self,
        routing_key: str,
        *,
        events_url: str = _PAGERDUTY_EVENTS_URL,
        timeout_seconds: float = 2.0,
    ) -> None:
        if not _ROUTING_KEY_RE.fullmatch(routing_key):
            raise ValueError("PagerDuty routing key must be a bounded opaque value")
        self._routing_key = routing_key
        self._events_url = _validated_https_url(events_url)
        if not 0.1 <= timeout_seconds <= 10.0:
            raise ValueError("PagerDuty alert timeout must be between 0.1 and 10 seconds")
        self._timeout_seconds = timeout_seconds

    def alert(self, alert: TelemetryDeliveryAlert) -> None:
        """Best-effort, fixed-shape PagerDuty trigger without product payload."""
        payload = json.dumps(
            {
                "routing_key": self._routing_key,
                "event_action": "trigger",
                "payload": {
                    "summary": "TraitTutor telemetry delivery failure threshold reached",
                    "source": "traittutor-telemetry",
                    "severity": "error",
                    "custom_details": {
                        "schema_version": "v1",
                        "reason": alert.reason,
                        "failure_count": alert.failure_count,
                        "threshold": alert.threshold,
                    },
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        outbound = Request(
            self._events_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "TraitTutorTelemetry/1"},
            method="POST",
        )
        with urlopen(outbound, timeout=self._timeout_seconds):  # noqa: S310 - URL is HTTPS validated
            pass


class SmtpTelemetryAlertHook:
    """Send the fixed threshold alert through an explicitly configured STARTTLS relay."""

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipients: tuple[str, ...],
        *,
        username: str | None = None,
        password: str | None = None,
        timeout_seconds: float = 2.0,
    ) -> None:
        if not _valid_smtp_host(host):
            raise ValueError("SMTP host must be a bounded hostname or IP address")
        if not 1 <= port <= 65_535:
            raise ValueError("SMTP port must be between 1 and 65535")
        if not _valid_email(sender) or not recipients or len(recipients) > 10:
            raise ValueError("SMTP sender and 1-10 recipient addresses are required")
        if any(not _valid_email(item) for item in recipients):
            raise ValueError("SMTP recipient address is invalid")
        if bool(username) != bool(password):
            raise ValueError("SMTP username and password must be configured together")
        if not 0.1 <= timeout_seconds <= 10.0:
            raise ValueError("SMTP alert timeout must be between 0.1 and 10 seconds")
        self._host = host
        self._port = port
        self._sender = sender
        self._recipients = recipients
        self._username = username
        self._password = password
        self._timeout_seconds = timeout_seconds

    def alert(self, alert: TelemetryDeliveryAlert) -> None:
        """Best-effort STARTTLS delivery of a fixed, non-sensitive message."""
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = ", ".join(self._recipients)
        message["Subject"] = "TraitTutor telemetry delivery threshold reached"
        message.set_content(
            "Telemetry delivery requires attention.\n"
            f"schema_version: v1\nreason: {alert.reason}\n"
            f"failure_count: {alert.failure_count}\nthreshold: {alert.threshold}\n"
        )
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout_seconds) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            if self._username is not None and self._password is not None:
                client.login(self._username, self._password)
            client.send_message(message, from_addr=self._sender, to_addrs=list(self._recipients))


class _CompositeTelemetryAlertHook:
    """Deliver independently configured adapters without coupling their failure modes."""

    def __init__(self, hooks: tuple[TelemetryAlertHook, ...]) -> None:
        self._hooks = hooks

    def alert(self, alert: TelemetryDeliveryAlert) -> None:
        for hook in self._hooks:
            try:
                hook.alert(alert)
            except Exception:  # noqa: BLE001 - one notifier cannot block another
                logger.warning("telemetry_alert_adapter_failed adapter=%s", type(hook).__name__)


def create_configured_telemetry_alert_hook(
    environ: Mapping[str, str] | None = None,
) -> TelemetryAlertHook:
    """Build configured production alert adapters, reducing invalid input to NoOp.

    Each adapter is independent so an unavailable PagerDuty or SMTP setting
    cannot disable a separately valid generic webhook.  No adapter is created
    unless its complete server configuration is present and valid.
    """
    source = environ if environ is not None else os.environ
    hooks: list[TelemetryAlertHook] = []
    timeout_seconds = _configured_timeout_seconds(source)
    webhook = _configured_webhook(source, timeout_seconds)
    if webhook is not None:
        hooks.append(webhook)
    pagerduty = _configured_pagerduty(source, timeout_seconds)
    if pagerduty is not None:
        hooks.append(pagerduty)
    smtp = _configured_smtp(source, timeout_seconds)
    if smtp is not None:
        hooks.append(smtp)
    if not hooks:
        return NoOpTelemetryAlertHook()
    if len(hooks) == 1:
        return hooks[0]
    return _CompositeTelemetryAlertHook(tuple(hooks))


def _configured_timeout_seconds(environ: Mapping[str, str]) -> float:
    try:
        return _bounded_timeout_ms(environ) / 1_000
    except Exception as exc:  # noqa: BLE001 - invalid control must not block delivery
        logger.warning("telemetry_alert_timeout_disabled error_type=%s", type(exc).__name__)
        return 2.0


def _configured_webhook(
    environ: Mapping[str, str], timeout_seconds: float
) -> WebhookTelemetryAlertHook | None:
    raw_url = environ.get(TELEMETRY_ALERT_WEBHOOK_URL_ENV, "").strip()
    if not raw_url:
        return None
    try:
        return WebhookTelemetryAlertHook(raw_url, timeout_seconds=timeout_seconds)
    except Exception as exc:  # noqa: BLE001 - invalid configuration is an opt-out
        logger.warning("telemetry_alert_webhook_disabled error_type=%s", type(exc).__name__)
        return None


def _configured_pagerduty(
    environ: Mapping[str, str], timeout_seconds: float
) -> PagerDutyTelemetryAlertHook | None:
    routing_key = environ.get(TELEMETRY_ALERT_PAGERDUTY_ROUTING_KEY_ENV, "").strip()
    if not routing_key:
        return None
    try:
        return PagerDutyTelemetryAlertHook(
            routing_key,
            events_url=environ.get(
                TELEMETRY_ALERT_PAGERDUTY_EVENTS_URL_ENV, _PAGERDUTY_EVENTS_URL
            ).strip(),
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - invalid configuration is an opt-out
        logger.warning("telemetry_alert_pagerduty_disabled error_type=%s", type(exc).__name__)
        return None


def _configured_smtp(
    environ: Mapping[str, str], timeout_seconds: float
) -> SmtpTelemetryAlertHook | None:
    host = environ.get(TELEMETRY_ALERT_EMAIL_SMTP_HOST_ENV, "").strip()
    sender = environ.get(TELEMETRY_ALERT_EMAIL_FROM_ENV, "").strip()
    raw_recipients = environ.get(TELEMETRY_ALERT_EMAIL_TO_ENV, "").strip()
    if not host and not sender and not raw_recipients:
        return None
    try:
        port = _bounded_smtp_port(environ)
        recipients = tuple(item.strip() for item in raw_recipients.split(",") if item.strip())
        return SmtpTelemetryAlertHook(
            host,
            port,
            sender,
            recipients,
            username=environ.get(TELEMETRY_ALERT_EMAIL_USERNAME_ENV, "").strip() or None,
            password=environ.get(TELEMETRY_ALERT_EMAIL_PASSWORD_ENV, "") or None,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - invalid configuration is an opt-out
        logger.warning("telemetry_alert_smtp_disabled error_type=%s", type(exc).__name__)
        return None


def _bounded_timeout_ms(environ: Mapping[str, str]) -> int:
    raw = environ.get(TELEMETRY_ALERT_WEBHOOK_TIMEOUT_MS_ENV, "").strip()
    if not raw:
        return 2_000
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("Webhook alert timeout must be an integer") from exc
    if not 100 <= value <= 10_000:
        raise ValueError("Webhook alert timeout must be between 100 and 10000ms")
    return value


def _bounded_smtp_port(environ: Mapping[str, str]) -> int:
    raw = environ.get(TELEMETRY_ALERT_EMAIL_SMTP_PORT_ENV, "").strip()
    if not raw:
        return 587
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("SMTP port must be an integer") from exc
    if not 1 <= value <= 65_535:
        raise ValueError("SMTP port must be between 1 and 65535")
    return value


def _valid_smtp_host(value: str) -> bool:
    return bool(value) and len(value) <= 255 and not any(char.isspace() for char in value)


def _valid_email(value: str) -> bool:
    if not value or len(value) > 320 or any(char in value for char in "\r\n"):
        return False
    local, separator, domain = value.partition("@")
    return bool(separator and local and domain and "@" not in domain and "." in domain)


def _validated_https_url(value: str) -> str:
    """Reject credentials, fragments and non-HTTPS endpoints before I/O."""
    if not value or len(value) > 2_048:
        raise ValueError("Webhook alert URL must be a bounded HTTPS URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Webhook alert URL must be credential-free HTTPS")
    return value


__all__ = [
    "TELEMETRY_ALERT_WEBHOOK_TIMEOUT_MS_ENV",
    "TELEMETRY_ALERT_WEBHOOK_URL_ENV",
    "TELEMETRY_ALERT_PAGERDUTY_EVENTS_URL_ENV",
    "TELEMETRY_ALERT_PAGERDUTY_ROUTING_KEY_ENV",
    "TELEMETRY_ALERT_EMAIL_SMTP_HOST_ENV",
    "TELEMETRY_ALERT_EMAIL_SMTP_PORT_ENV",
    "TELEMETRY_ALERT_EMAIL_FROM_ENV",
    "TELEMETRY_ALERT_EMAIL_TO_ENV",
    "TELEMETRY_ALERT_EMAIL_USERNAME_ENV",
    "TELEMETRY_ALERT_EMAIL_PASSWORD_ENV",
    "PagerDutyTelemetryAlertHook",
    "SmtpTelemetryAlertHook",
    "WebhookTelemetryAlertHook",
    "create_configured_telemetry_alert_hook",
]
