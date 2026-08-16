# Gateway design

## Boundary

The Gateway is TraitTutor's canonical server-side provider boundary:

1. Callers submit typed messages, content parts, attachments, tools and response
   formats.
2. Gateway validates tool-call/result linkage and portable provider features
   before provider I/O.
3. A server-owned timeout and cancellation event bound each request.
4. Provider adapters return typed completion or stream events.
5. Callers project only approved text and structured results to product state or
   browser protocols.

Gateway never executes tools and never accepts owner, authorization or routing
authority from browser payloads. The authenticated runtime supplies those
values.

## Data handling

`GatewayReceipt` contains only derived operational fields. Request payloads,
credentials, attachment contents, tool arguments, tool results and hidden
reasoning are excluded from receipts, telemetry and learner-facing state.
Provider token counters remain server-operational data.

## Failure contract

Timeout, cancellation, malformed typed data, missing terminal receipts and
unsupported required capabilities fail closed. A failed canonical call is not
retried through a retired factory, raw client or text tool parser. Operational
route failover may choose another configured current provider, but it preserves
the same Gateway request and idempotency boundary.

## Consumer contract

All product consumers construct typed Gateway DTOs directly. A consumer-owned
tool loop retains assistant call IDs, executes approved tools server-side and
replays typed tool results. Research commits only validated source-bound claims;
learning generation freezes the canonical context snapshot before dispatch.
