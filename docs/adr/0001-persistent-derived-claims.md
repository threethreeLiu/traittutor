# ADR-0001: Persistent claims for derived work

**Date**: 2026-08-09  
**Status**: accepted  
**Deciders**: TraitTutor product owner, Codex

## Context

`LearnerEventLedger` durably records events and pending derived work, but its operation lock is process-local. Two workers can both observe an unfinished projection, execute it, and only afterwards converge on the same completion marker. Holding the ledger file lock during an external callback would block unrelated event appends and can deadlock callbacks that read the ledger.

## Decision

Derived work uses a durable lease claim with owner, opaque token, claim time, expiry, and optional heartbeat. Claim acquisition is a compare-and-set operation under the ledger file lock. Completion, failure, renewal, and late results must present the current token; expired tokens are fenced. Downstream stores remain idempotent by `event_id + operation` because a lease cannot provide distributed exactly-once semantics by itself.

## Alternatives Considered

### Hold the ledger lock through the callback

- **Pros**: Simple single-executor guarantee.
- **Cons**: Long I/O blocks the ledger and callback re-entry can deadlock.
- **Why not**: It violates the event store's availability and composability requirements.

### Rely only on downstream event-id deduplication

- **Pros**: No ledger schema change.
- **Cons**: Expensive callbacks can run twice and not every sink proves atomic deduplication.
- **Why not**: It leaves the known cross-process race open.

## Consequences

### Positive

- Competing workers cannot intentionally execute the same live claim.
- Crashed work is recoverable after lease expiry and stale results cannot overwrite a new claim.

### Negative

- Operations need lease duration and heartbeat policy. The personalization BKT
  projection renews at one third of its lease; a lost renewal cancels the live
  callback and leaves downstream event-id idempotency as the final fence.
- The persisted queue schema and migration tests become part of the ledger contract.

### Risks

- A short lease can cause re-entry; mitigate with operation-specific TTLs, heartbeat, and token fencing.
- Clock drift affects expiry; compare UTC timestamps conservatively and keep downstream writes idempotent.
