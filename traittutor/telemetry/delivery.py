"""Bounded, aggregate-only delivery for operational product telemetry.

The event registry accepts a few correlation attributes for in-process
debugging.  This module deliberately does *not* deliver those envelopes.  It
reduces them to the registry-approved low-cardinality labels plus counters and
duration summaries before placing anything on disk or an adapter boundary.

Delivery is opt-in.  ``TRAITTUTOR_TELEMETRY_SINK=jsonl`` selects the local
durable JSONL adapter; every absent, invalid, or explicitly ``noop``
configuration remains a :class:`NoOpProductEventSink`.  The adapter is a safe
outbox.  An explicit credential-free HTTPS webhook may receive only a fixed
threshold breach contract; deployments can still inject an approved hook.
"""

from __future__ import annotations

import atexit
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread, current_thread
from typing import Literal, Mapping, Protocol

from .product_events import NoOpProductEventSink, ProductEventEnvelope, ProductEventSink

logger = logging.getLogger(__name__)

TELEMETRY_SINK_ENV = "TRAITTUTOR_TELEMETRY_SINK"
TELEMETRY_OUTBOX_PATH_ENV = "TRAITTUTOR_TELEMETRY_OUTBOX_PATH"
TELEMETRY_BATCH_SIZE_ENV = "TRAITTUTOR_TELEMETRY_BATCH_SIZE"
TELEMETRY_MAX_PENDING_ENV = "TRAITTUTOR_TELEMETRY_MAX_PENDING"
TELEMETRY_FLUSH_INTERVAL_MS_ENV = "TRAITTUTOR_TELEMETRY_FLUSH_INTERVAL_MS"
TELEMETRY_ALERT_FAILURE_THRESHOLD_ENV = "TRAITTUTOR_TELEMETRY_ALERT_FAILURE_THRESHOLD"

TelemetrySinkMode = Literal["noop", "jsonl"]


@dataclass(frozen=True)
class TelemetryDeliveryConfig:
    """Explicit, bounded deployment configuration for aggregate delivery."""

    mode: TelemetrySinkMode = "noop"
    outbox_path: Path | None = None
    batch_size: int = 64
    max_pending: int = 1_024
    flush_interval_ms: int = 1_000
    alert_failure_threshold: int = 3

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "TelemetryDeliveryConfig":
        """Read only explicit server configuration, rejecting unsafe values.

        A bad deployment setting must roll back to NoOp through
        :func:`create_product_event_sink`, rather than interrupting a learner
        request.  ``from_environ`` itself raises so startup diagnostics and
        tests can distinguish an invalid setting from intentional opt-out.
        """

        source = os.environ if environ is None else environ
        raw_mode = source.get(TELEMETRY_SINK_ENV, "noop").strip().lower()
        if raw_mode in {"", "0", "false", "off", "noop"}:
            return cls()
        if raw_mode != "jsonl":
            raise ValueError("TRAITTUTOR_TELEMETRY_SINK must be noop or jsonl")

        raw_path = source.get(TELEMETRY_OUTBOX_PATH_ENV, "").strip()
        if not raw_path:
            raise ValueError("TRAITTUTOR_TELEMETRY_OUTBOX_PATH is required for jsonl")
        path = Path(raw_path).expanduser()
        if not path.name or path.is_dir():
            raise ValueError("TRAITTUTOR_TELEMETRY_OUTBOX_PATH must name a file")

        return cls(
            mode="jsonl",
            outbox_path=path,
            batch_size=_bounded_int(source, TELEMETRY_BATCH_SIZE_ENV, 64, 1, 512),
            max_pending=_bounded_int(source, TELEMETRY_MAX_PENDING_ENV, 1_024, 1, 10_000),
            flush_interval_ms=_bounded_int(
                source, TELEMETRY_FLUSH_INTERVAL_MS_ENV, 1_000, 50, 60_000
            ),
            alert_failure_threshold=_bounded_int(
                source, TELEMETRY_ALERT_FAILURE_THRESHOLD_ENV, 3, 1, 100
            ),
        )


def _bounded_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class TelemetryAggregate:
    """Only persistable, low-cardinality operational telemetry fields."""

    event_name: str
    metric_labels: tuple[tuple[str, str], ...]
    count: int
    duration_ms_total: int
    duration_ms_max: int
    total_tokens: int
    total_cost_picousd: int | None

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "event_name": self.event_name,
            "metric_labels": dict(self.metric_labels),
            "count": self.count,
            "duration_ms_total": self.duration_ms_total,
            "duration_ms_max": self.duration_ms_max,
            "total_tokens": self.total_tokens,
        }
        if self.total_cost_picousd is not None:
            payload["total_cost_picousd"] = self.total_cost_picousd
        return payload


@dataclass(frozen=True)
class TelemetryBatch:
    """A durable batch; it has no raw envelope IDs or attributes."""

    generated_at: datetime
    aggregates: tuple[TelemetryAggregate, ...]
    schema_version: str = "v1"

    def as_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "aggregates": [aggregate.as_payload() for aggregate in self.aggregates],
        }


class TelemetryBatchWriter(Protocol):
    """Durable adapter boundary.  Implementations receive aggregates only."""

    def write(self, batch: TelemetryBatch) -> None:
        """Durably accept one aggregate-only batch."""


class JsonlTelemetryBatchWriter:
    """Append batches with flush+fsync so acknowledged batches survive a crash."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def write(self, batch: TelemetryBatch) -> None:
        payload = json.dumps(batch.as_payload(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(f"{payload}\n")
                handle.flush()
                os.fsync(handle.fileno())


@dataclass(frozen=True)
class TelemetryDeliveryAlert:
    """Sanitized alert contract for repeated delivery failures.

    ``failure_count`` is consecutive failed batch writes.  No exception text,
    outbox path, event attribute, user, or correlation identifier crosses this
    boundary.  The built-in hook intentionally only logs a bounded signal.
    """

    reason: Literal["consecutive_delivery_failures"]
    failure_count: int
    threshold: int


class TelemetryAlertHook(Protocol):
    """Optional deployment-owned alert adapter; must itself fail open."""

    def alert(self, alert: TelemetryDeliveryAlert) -> None:
        """Receive a sanitized threshold breach."""


class NoOpTelemetryAlertHook:
    """Default hook: no notification delivery without an explicit adapter."""

    def alert(self, alert: TelemetryDeliveryAlert) -> None:
        del alert


class _AggregateAccumulator:
    """Keep only metric labels and duration arithmetic in process memory."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], list[int]] = defaultdict(
            lambda: [0, 0, 0, 0, 0, 0]
        )

    def add(self, event: ProductEventEnvelope) -> None:
        key = (event.event_name, tuple(sorted(event.metric_labels.items())))
        value = self._values[key]
        value[0] += 1
        duration = event.attributes.get("duration_ms", 0)
        # The registered contract admits a scalar; only non-negative integer
        # milliseconds participate in duration summaries.
        duration_ms = duration if isinstance(duration, int) and duration >= 0 else 0
        value[1] += duration_ms
        value[2] = max(value[2], duration_ms)
        total_tokens = event.attributes.get("total_tokens", 0)
        if (
            isinstance(total_tokens, int)
            and not isinstance(total_tokens, bool)
            and total_tokens >= 0
        ):
            value[3] += total_tokens
        cost_picousd = event.attributes.get("cost_picousd")
        if (
            isinstance(cost_picousd, int)
            and not isinstance(cost_picousd, bool)
            and cost_picousd >= 0
        ):
            value[4] += cost_picousd
        else:
            value[5] += 1

    def batch(self) -> TelemetryBatch | None:
        if not self._values:
            return None
        aggregates = tuple(
            TelemetryAggregate(
                event_name=event_name,
                metric_labels=labels,
                count=values[0],
                duration_ms_total=values[1],
                duration_ms_max=values[2],
                total_tokens=values[3],
                total_cost_picousd=values[4] if values[5] == 0 else None,
            )
            for (event_name, labels), values in sorted(self._values.items())
        )
        return TelemetryBatch(generated_at=datetime.now(timezone.utc), aggregates=aggregates)

    def clear(self) -> None:
        self._values.clear()


@dataclass(frozen=True)
class TelemetryDeliveryStats:
    """Bounded health values for tests/startup diagnostics, never a metric label."""

    accepted: int
    dropped: int
    written_batches: int
    consecutive_failures: int


class AsyncBoundedProductEventSink:
    """Fail-open queue that delivers aggregate-only batches on a daemon thread."""

    def __init__(
        self,
        *,
        writer: TelemetryBatchWriter,
        max_pending: int = 1_024,
        batch_size: int = 64,
        flush_interval_ms: int = 1_000,
        alert_failure_threshold: int = 3,
        alert_hook: TelemetryAlertHook | None = None,
        start_worker: bool = True,
    ) -> None:
        if not 1 <= max_pending <= 10_000:
            raise ValueError("max_pending must be between 1 and 10000")
        if not 1 <= batch_size <= 512:
            raise ValueError("batch_size must be between 1 and 512")
        if not 50 <= flush_interval_ms <= 60_000:
            raise ValueError("flush_interval_ms must be between 50 and 60000")
        if not 1 <= alert_failure_threshold <= 100:
            raise ValueError("alert_failure_threshold must be between 1 and 100")
        self._writer = writer
        self._queue: Queue[ProductEventEnvelope] = Queue(maxsize=max_pending)
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_ms / 1_000
        self._alert_failure_threshold = alert_failure_threshold
        self._alert_hook = alert_hook if alert_hook is not None else NoOpTelemetryAlertHook()
        self._accumulator = _AggregateAccumulator()
        self._lock = Lock()
        self._flush_lock = Lock()
        self._stop = Event()
        self._closed = False
        self._accepted = 0
        self._dropped = 0
        self._written_batches = 0
        self._consecutive_failures = 0
        self._worker: Thread | None = None
        if start_worker:
            self._worker = Thread(target=self._run, name="traittutor-telemetry", daemon=True)
            self._worker.start()

    def emit(self, event: ProductEventEnvelope) -> None:
        """Enqueue without blocking or raising into a learner request."""

        if self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except Full:
            with self._lock:
                self._dropped += 1
            return
        with self._lock:
            self._accepted += 1

    @property
    def stats(self) -> TelemetryDeliveryStats:
        with self._lock:
            return TelemetryDeliveryStats(
                accepted=self._accepted,
                dropped=self._dropped,
                written_batches=self._written_batches,
                consecutive_failures=self._consecutive_failures,
            )

    def flush(self) -> None:
        """Best-effort synchronous drain for shutdown and deterministic tests."""

        with self._flush_lock:
            self._drain_queue()
            self._write_pending()

    def close(self) -> None:
        """Stop the daemon and make one bounded best-effort final flush."""

        self._closed = True
        self._stop.set()
        if self._worker is not None and self._worker is not current_thread():
            self._worker.join(timeout=max(1.0, self._flush_interval_seconds * 2))
        self.flush()

    def _run(self) -> None:
        processed = 0
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=self._flush_interval_seconds)
            except Empty:
                with self._flush_lock:
                    self._write_pending()
                processed = 0
                continue
            with self._flush_lock:
                self._add(event)
                processed += 1
                if processed >= self._batch_size:
                    self._write_pending()
                    processed = 0

    def _drain_queue(self) -> None:
        while True:
            try:
                self._add(self._queue.get_nowait())
            except Empty:
                return

    def _add(self, event: ProductEventEnvelope) -> None:
        self._accumulator.add(event)

    def _write_pending(self) -> None:
        batch = self._accumulator.batch()
        if batch is None:
            return
        try:
            self._writer.write(batch)
        except Exception as exc:  # noqa: BLE001 - telemetry must be fail-open
            del exc
            alert: TelemetryDeliveryAlert | None = None
            with self._lock:
                self._consecutive_failures += 1
                if self._consecutive_failures == self._alert_failure_threshold:
                    alert = TelemetryDeliveryAlert(
                        reason="consecutive_delivery_failures",
                        failure_count=self._consecutive_failures,
                        threshold=self._alert_failure_threshold,
                    )
            if alert is not None:
                try:
                    self._alert_hook.alert(alert)
                except Exception:  # noqa: BLE001 - hook cannot affect delivery/product paths
                    logger.warning("telemetry_alert_hook_failed")
            logger.warning("telemetry_batch_delivery_failed")
            return
        self._accumulator.clear()
        with self._lock:
            self._written_batches += 1
            self._consecutive_failures = 0


def create_product_event_sink(
    *,
    environ: Mapping[str, str] | None = None,
    alert_hook: TelemetryAlertHook | None = None,
) -> ProductEventSink:
    """Build the opt-in sink, rolling any bad config back to NoOp.

    Configuration errors are intentionally reduced to a bounded log event.
    Values such as configured paths are never printed because environment
    strings are deployment-controlled but not automatically safe to disclose.
    """

    try:
        config = TelemetryDeliveryConfig.from_environ(environ)
        if config.mode == "noop":
            return NoOpProductEventSink()
        assert config.outbox_path is not None
        if alert_hook is None:
            # Import lazily to avoid the alert module's contract import from
            # creating a delivery initialization cycle.
            from .alerts import create_configured_telemetry_alert_hook

            alert_hook = create_configured_telemetry_alert_hook(environ)
        return AsyncBoundedProductEventSink(
            writer=JsonlTelemetryBatchWriter(config.outbox_path),
            max_pending=config.max_pending,
            batch_size=config.batch_size,
            flush_interval_ms=config.flush_interval_ms,
            alert_failure_threshold=config.alert_failure_threshold,
            alert_hook=alert_hook,
        )
    except Exception as exc:  # noqa: BLE001 - invalid telemetry must not block startup
        logger.warning("telemetry_delivery_disabled error_type=%s", type(exc).__name__)
        return NoOpProductEventSink()


_configured_sink: ProductEventSink | None = None
_configured_sink_lock = Lock()


def get_configured_product_event_sink() -> ProductEventSink:
    """Return the process-lifetime explicitly configured sink (default NoOp)."""

    global _configured_sink
    with _configured_sink_lock:
        if _configured_sink is None:
            _configured_sink = create_product_event_sink()
            if isinstance(_configured_sink, AsyncBoundedProductEventSink):
                atexit.register(_configured_sink.close)
        return _configured_sink


def reset_configured_product_event_sink_for_tests() -> None:
    """Release the cached opt-in worker; test-only to make env rollback observable."""

    global _configured_sink
    with _configured_sink_lock:
        if isinstance(_configured_sink, AsyncBoundedProductEventSink):
            _configured_sink.close()
        _configured_sink = None
