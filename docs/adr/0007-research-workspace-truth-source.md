# ADR-0007: Research Workspace as truth source

**Date**: 2026-08-09  
**Status**: accepted  
**Deciders**: TraitTutor product owner, Codex

## Context

The existing research pipeline can preview an outline and execute topic blocks, but its optional queue persistence is not the online source of truth. It lacks a product Workspace, frozen Brief versions, durable Run lifecycle, source/note ledger, pause/cancel/recovery claims, and reusable task receipts.

## Decision

ResearchWorkspace, frozen/versioned ResearchBrief, ResearchRun, TaskReceipt, Source, Note, and ReportArtifact form the durable domain model. The server owns the Run state machine and persists state before publishing progress events. Tasks are idempotent by `run_id + task_id + input_hash`; cancelled or superseded runs fence late results. The current ResearchPipeline and DynamicTopicQueue become replaceable executor adapters, not truth sources.

## Alternatives Considered

### Expand DynamicTopicQueue into the product model

- **Pros**: Reuses existing code.
- **Cons**: A scheduling queue does not naturally model multiple runs, brief versions, sources, notes, and invalidation.
- **Why not**: It conflates execution mechanics with product state.

### Keep research inside chat sessions

- **Pros**: No new domain store.
- **Cons**: No durable resume, source ledger, or idempotent background lifecycle.
- **Why not**: It cannot satisfy the PRD workspace contract.

## Consequences

### Positive

- Research can pause, resume, audit sources, and reuse completed task receipts.
- Existing pipeline logic remains usable behind an adapter.

### Negative

- New owner-isolated storage and transition authorization are required.

### Risks

- Persist state and receipts before SSE; use cancellation epochs so late provider results cannot revive a cancelled Run.
