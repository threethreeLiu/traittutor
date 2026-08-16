# TraitTutor Gateway

`TraitTutorGateway.complete()` and `.stream()` are the only model-completion
boundary used by TraitTutor product surfaces. Callers construct typed
`GatewayRequest`, `GatewayMessage`, `GatewayAttachment` and `GatewayTool`
values; provider dictionaries and credentials remain inside the Gateway.

For caller-owned tool loops, an assistant message carries typed
`GatewayToolCall` values and each following tool message references one earlier
call ID. Gateway validates argument JSON, call identity and result linkage
before provider I/O. Tool execution and replay remain caller-owned.

Streaming emits typed text, reasoning, tool-call, usage, final, error or
cancellation events. Browser consumers project only the fields their current
protocol permits. Prompts, attachment contents, user IDs, credentials, tool
arguments, tool results and provider reasoning never enter public receipts or
telemetry.

## Current consumers

- Base agents, Chat, Quiz Judge and session-title generation use Gateway.
- Notebook summaries and generated learning artifacts use Gateway.
- Agentic chat and Research use typed messages and typed tool schemas.
- Research Workspace requires a terminal event before validating and committing
  one durable result.
- Structured generation uses the bounded route policy and quota rotation.

The canonical contract permits no flag-off provider path, old factory retry,
DSML parser fallback or untyped browser-controlled Gateway request. Provider failure,
cancellation or an unsupported capability fails visibly or uses a documented
current-product degradation; it never invokes a retired implementation.
