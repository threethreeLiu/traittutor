# ADR-0005: Traceable L3 projection

**Date**: 2026-08-09  
**Status**: superseded by ADR-0008
**Deciders**: TraitTutor product owner, Codex

## Context

Legacy L3 memory is user-readable Markdown with surface-level references, but it cannot prove which evidence supports each claim or represent observation time, subject, confidence, and assertion status. Canonical memory already has source-aware records; introducing a third fact store would deepen divergence.

## Decision

L3 Markdown remains a display projection. A typed sidecar indexed by L3 entry/claim stores exact source entry IDs and refs, observation range, subject/domain, evidence type, confidence, assertion state, and projection version. Decisions and Prompt grounding resolve back to canonical memory or source evidence. Legacy claims are marked `legacy_unverified` until an explicit rebuild; provenance is never inferred silently from text similarity.

## Alternatives Considered

### Embed all metadata in Markdown

- **Pros**: One file.
- **Cons**: Fragile parsing and difficult atomic migration.
- **Why not**: Free text must not become the fact database.

### Keep only surface-level references

- **Pros**: No migration.
- **Cons**: Claims remain unverifiable and stale-source invalidation is impossible.
- **Why not**: It does not satisfy per-claim provenance.

## Consequences

### Positive

- Existing L3 UI survives while provenance becomes exact and rebuildable.
- Deleted or superseded evidence can invalidate dependent claims deterministically.

### Negative

- Markdown and sidecar need atomic update or deterministic reconstruction.

### Risks

- Model-supplied unknown IDs must be rejected against a server-provided allowlist.
