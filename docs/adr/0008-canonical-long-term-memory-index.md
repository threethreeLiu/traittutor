# ADR-0008: Canonical long-term memory index

**Date**: 2026-08-11
**Status**: accepted
**Deciders**: TraitTutor product owner, Codex

## Context

The DeepTutor-derived L1/L2/L3 workbench exposed storage implementation as product language and maintained text-only long-term documents beside canonical `UserMemoryItem` records. The parallel write and migration paths could disagree about activation, provenance, deletion, and ownership.

## Decision

Product surfaces use “long-term memory” and “long-term memory index”, never L1/L2/L3. Canonical memory is the only source of recallable user memory. Historical DeepTutor Markdown is imported deterministically as inactive inferred candidates with zero confidence, then its exact source directory is archived. A user must explicitly confirm a candidate before recall. The derived index is rebuilt only from active canonical records and is invalidated on deactivation or deletion.

The old memory workbench, its public API and text-to-projection migration are retired. Internal layer fields may remain temporarily in persisted schema for backward-compatible deserialization, but are not public product concepts.

## Consequences

- Legacy text cannot silently become a fact or personalization signal.
- There is one activation, conflict, provenance, ownership and deletion lifecycle.
- Migration is one-way and replay-safe; archived source remains recoverable for audit.
- Deployment migration must report imported, already-existing and archived counts.
