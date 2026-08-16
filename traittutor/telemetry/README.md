# Operational telemetry

`traittutor.telemetry` is an operational health plane, not security audit or
product analytics.  Its event registry is server-owned and deny-by-default.

## Default and rollout

Telemetry delivery is disabled by default.  Set both values below to opt in to
the durable local aggregate outbox:

```bash
TRAITTUTOR_TELEMETRY_SINK=jsonl
TRAITTUTOR_TELEMETRY_OUTBOX_PATH=/var/lib/traittutor/telemetry.jsonl
```

Optional bounded controls are `TRAITTUTOR_TELEMETRY_BATCH_SIZE` (1–512),
`TRAITTUTOR_TELEMETRY_MAX_PENDING` (1–10000),
`TRAITTUTOR_TELEMETRY_FLUSH_INTERVAL_MS` (50–60000), and
`TRAITTUTOR_TELEMETRY_ALERT_FAILURE_THRESHOLD` (1–100, default 3 consecutive
batch write failures).  Invalid or incomplete configuration rolls back to
NoOp and only creates a bounded server log record.

The JSONL adapter receives counters, duration totals/maxima, aggregate token
totals when an approved provider reports them, and registry-approved metric
labels only. It never receives raw event envelopes, prompts,
answers, user IDs, source text, credentials, endpoint URLs, or correlation
identifiers such as request/run IDs. Token totals are not priced or attributed
to a user, request, prompt, output, model, or route.

## Optional model pricing

Cost accounting is disabled by default. To opt in, point
`TRAITTUTOR_TELEMETRY_PRICING_PATH` at a deployment-owned JSON file with exact
model names and pico-USD prices per million input/output tokens:

```json
{
  "version": "2026-08-10",
  "models": {
    "server-selected-model": {
      "input_picousd_per_million_tokens": 2000000000000,
      "output_picousd_per_million_tokens": 4000000000000
    }
  }
}
```

Only an exact configured model with both input and output token counts produces
`total_cost_picousd` in the aggregate outbox. Unknown models, malformed files,
and incomplete usage have no cost value; no fallback price is guessed. The
file version is an operator audit aid and is not emitted as telemetry.

## Alerting

The default alert hook is a no-op.  After the configured number of consecutive
aggregate-batch delivery failures, an injected deployment-owned
`TelemetryAlertHook` receives a sanitized threshold alert.  Deployments can
also explicitly set a credential-free HTTPS endpoint:

```bash
TRAITTUTOR_TELEMETRY_ALERT_WEBHOOK_URL=https://alerts.example/traittutor
TRAITTUTOR_TELEMETRY_ALERT_WEBHOOK_TIMEOUT_MS=2000
```

The adapter sends only `{schema_version, reason, failure_count, threshold}`;
it cannot send envelopes, prompts, users, paths, exception text, or outbox
locations. Invalid/non-HTTPS configuration rolls back to the no-op hook.

PagerDuty Events v2 is separately opt-in. Its routing key stays in deployment
configuration and is never a telemetry field, receipt, log value, or browser
payload:

```bash
TRAITTUTOR_TELEMETRY_ALERT_PAGERDUTY_ROUTING_KEY=server-only-routing-key
# Optional; defaults to https://events.pagerduty.com/v2/enqueue
TRAITTUTOR_TELEMETRY_ALERT_PAGERDUTY_EVENTS_URL=https://events.pagerduty.com/v2/enqueue
```

SMTP email is also separately opt-in and always uses STARTTLS. Set all required
values; credentials are optional as a pair for relays that require auth:

```bash
TRAITTUTOR_TELEMETRY_ALERT_EMAIL_SMTP_HOST=smtp.example.com
TRAITTUTOR_TELEMETRY_ALERT_EMAIL_SMTP_PORT=587
TRAITTUTOR_TELEMETRY_ALERT_EMAIL_FROM=traittutor-alerts@example.com
TRAITTUTOR_TELEMETRY_ALERT_EMAIL_TO=ops@example.com,oncall@example.com
TRAITTUTOR_TELEMETRY_ALERT_EMAIL_USERNAME=optional-server-account
TRAITTUTOR_TELEMETRY_ALERT_EMAIL_PASSWORD=optional-server-secret
```

Each configured adapter receives only the fixed threshold contract. Invalid or
partial PagerDuty/SMTP settings disable that adapter without changing product
work or a separately valid alert adapter. Configuration enables adapters; a
real deployment must still verify its network, credentials, recipient policy,
and escalation routing.
