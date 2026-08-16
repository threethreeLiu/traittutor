# ADR-0002: Canonical mastery read view

**Date**: 2026-08-09  
**Status**: accepted  
**Deciders**: TraitTutor product owner, Codex

## Context

Canonical answer events update per-user, per-subject, per-KC knowledge state without writing the legacy `mastery_levels` map. Legacy policy/display code still reads that map and can therefore show 0% even when verified canonical evidence exists. Copying posterior values back into the legacy map would recreate dual-write and isolation problems.

## Decision

`LearnerEventLedger` remains the fact source and a canonical `MasteryReadView`
serves versioned state by `user_id + subject_id + kc_id`. All display, summary
and decision paths use this view. Public output hides uncalibrated posterior and
exposes only verified observation count, interval and `unknown` state. The view
never writes `LearningProgress.mastery_levels`.

## Alternatives Considered

### Mirror posterior into the legacy map

- **Pros**: Existing UI works without read-path changes.
- **Cons**: Creates dual-write, weak isolation, and false precision.
- **Why not**: It contradicts the canonical single-write migration.

## Consequences

### Positive

- Learner-facing views never display a stale heuristic value.
- Every serving read carries isolation and model-version semantics.

### Negative

- Callers must supply user and subject identity instead of only book/KC labels.
- Uncalibrated canonical state cannot unlock a quantitative objective. This is
  deliberately conservative until a calibrated parameter set is deployed.

### Risks

- Missing projections must return `unknown`, never silently fall back to stale legacy values.
- Internal mastery decisions must not expose an uncalibrated posterior publicly.
