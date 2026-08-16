# ADR-0004: Gateway as the model boundary

**Date**: 2026-08-09  
**Status**: accepted  
**Deciders**: TraitTutor product owner, Codex

## Context

The current Gateway covers audited non-streaming calls, while route enumeration and fallback remain in generation code and several agents call the LLM factory directly. Retry, timeout, attachment, streaming, usage, and degradation semantics therefore vary by call site.

## Decision

Gateway is the only product-facing model protocol and policy boundary. It owns
typed messages, attachments, complete/stream, route selection, retry/fallback
limits, timeout, usage, trace and degradation receipts. Domain runners retain
structured-output parsing and validation. Provider configuration and
credentials remain server-derived; direct factory and raw-client product paths
are outside the supported contract.

## Alternatives Considered

### Keep direct calls and add decorators

- **Pros**: Small changes.
- **Cons**: Policy and protocol drift remain.
- **Why not**: Audit without control does not satisfy Gateway unification.

## Consequences

### Positive

- Retry, fallback, latency, and spend have one receipt model.
- New Research and Persona work cannot create another model bypass.

### Negative

- Gateway protocol grows and must model stream cancellation and attachment ownership.
- Existing agent tracing hooks must emit the canonical receipt model.

### Risks

- Provider retries can multiply Gateway retries; cap both layers and record every attempt.
