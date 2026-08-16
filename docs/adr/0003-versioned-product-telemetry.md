# ADR-0003: Versioned product telemetry

**Date**: 2026-08-09  
**Status**: accepted  
**Deciders**: TraitTutor product owner, Codex

## Context

Current logs expose scattered latency and error messages but do not define stable event schemas, denominators, privacy classes, or cross-process aggregation. Provider latency variation, orchestration degradation, memory use, and recovery therefore cannot be measured consistently. Security audit records are not a substitute for product or operational telemetry.

## Decision

Telemetry uses a versioned `ProductEventEnvelope` plus an event registry with payload allowlists. Operational telemetry, security audit, and consented product analytics are separate data classes with different identity, retention, and access rules. The first sink is injectable and safe-by-default; telemetry failure never blocks the product path. Events never contain prompts, answers, raw chat, persona scores, credentials, or source bodies, and metric dimensions must remain low-cardinality.

## Alternatives Considered

### Parse metrics from existing logs

- **Pros**: No product code changes.
- **Cons**: Schemas and denominators drift and privacy fields cannot be enforced.
- **Why not**: It cannot produce auditable PRD metrics or stable alerts.

### Add third-party analytics SDKs in each module

- **Pros**: Fast dashboards.
- **Cons**: Distributed governance, vendor coupling, and high leakage risk.
- **Why not**: Collection policy must stay server-owned and centrally testable.

## Consequences

### Positive

- Gateway and Orchestrator latency, fallback, timeout, and degradation become comparable.
- Future research, memory, persona, and learning metrics share one governed contract.

### Negative

- Event schemas and metric definitions require version ownership.
- A production aggregation/alert sink remains a separate deployment decision.

### Risks

- Telemetry backpressure must not stall requests; use bounded out-of-band delivery and a drop counter.
- High-cardinality identifiers must stay trace fields, not metric labels.
