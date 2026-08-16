# ADR-0006: Typed Tutor Persona

**Date**: 2026-08-09  
**Status**: accepted  
**Deciders**: TraitTutor product owner, Codex

## Context

Existing personas are largely free-text files injected into system prompts. That format cannot prove that Persona changes only expression and never grading, answers, BKT, teaching safety, or user diagnosis. Text, voice, accessibility, and proactive behavior also lack one versioned contract.

## Decision

`TutorPersonaProfile` is a frozen, versioned, typed whitelist for address, avatar, voice, speech rate, tone, directness, humor, encouragement, feedback format, proactivity, emoji, quiet hours, accessibility, and safety version. A deterministic compiler produces a bounded Persona Contract separated from Teaching and Learning Context. Free-text persona bodies are not injected into production prompts; unsafe or ambiguous legacy fields require explicit user-confirmed migration.

## Alternatives Considered

### Keep free text with richer frontmatter

- **Pros**: Maximum compatibility.
- **Cons**: The body can still override grading or safety instructions.
- **Why not**: Whitelist enforcement remains impossible.

### Allow only fixed presets

- **Pros**: Simple and safe.
- **Cons**: Removes explicit user choices and accessibility configuration.
- **Why not**: It fails the product's configurable tutor requirement.

## Consequences

### Positive

- Persona influence is auditable and consistent across modalities.
- Answers, rubric, KC, BKT, and safety remain outside the expressible schema.

### Negative

- Arbitrary legacy personas cannot migrate losslessly.

### Risks

- Differential tests must prove profile changes affect style fields only.
