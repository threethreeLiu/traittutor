# Telemetry delivery design

## Data boundary

`ProductEventEnvelope` remains an in-process validated contract.  Delivery
never serializes it.  `AsyncBoundedProductEventSink` reduces each event to:

- registered `event_name`;
- approved low-cardinality `metric_labels`;
- `count`, `duration_ms_total`, `duration_ms_max`, and aggregate
  `total_tokens` when the approved event provides one.

Therefore correlation attributes (for example `request_id`, `run_id`, model,
or purpose) cannot become durable labels or payload values.  This is enforced
by construction rather than relying on a transport redaction list. Token
totals are operational counters only: delivery stores no provider usage detail,
price, prompt, completion, user, model, or route attribution.

## Optional cost accounting

`TRAITTUTOR_TELEMETRY_PRICING_PATH` names a bounded, deployment-owned JSON
file. `FileTokenPricing` accepts only a versioned exact-model mapping with
integer pico-USD prices per million input/output tokens. A cost is emitted only
when both provider counters and an exact model price are present. This avoids
shipping stale provider prices or silently charging unknown models. Delivery
reduces the value to aggregate `total_cost_picousd`; the model, price version,
raw usage, and request remain absent from durable telemetry.

## Failure and durability

The producer uses `Queue.put_nowait`; a full queue drops telemetry and never
blocks product work.  A daemon worker aggregates events and sends bounded
batches.  `JsonlTelemetryBatchWriter` flushes and fsyncs every acknowledged
batch.  On a write failure, the aggregate remains in memory for retry; the
business request has already completed and is never affected.

At `alert_failure_threshold` consecutive failures, the optional alert hook
receives only `{reason, failure_count, threshold}`.  The hook and writer are
both fail-open.  The built-in opt-in HTTPS webhook adapter adds a fixed
`schema_version` and accepts only credential-free HTTPS URLs; it never sees a
raw event, exception, path, user or prompt. The separately opt-in PagerDuty
adapter sends a fixed Events v2 trigger with only that contract in
`custom_details`; its routing key is deployment configuration and never
serialized by telemetry. The SMTP adapter only supports STARTTLS and renders a
fixed plain-text threshold message. The composite delivery wrapper isolates
adapters: an individual notification failure cannot prevent another configured
adapter or alter business/outbox semantics.

## Rollback

No environment setting means `NoOpProductEventSink`.  A malformed mode,
missing outbox path, or out-of-range limit also selects NoOp.  Removing the
opt-in variables and restarting is the rollback procedure.
