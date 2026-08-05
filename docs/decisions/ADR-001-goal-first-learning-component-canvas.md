# ADR-001: Goal-first learning component canvas

- **Status:** Accepted
- **Date:** 2026-08-05
- **Scope:** Learning Pack creation, component orchestration, frontend learning workspace

## Context

TraitTutor contains several useful executors—courseware, flashcards, quiz, diagrams, audio, guided solving, and exploration—but exposing them as a generator menu makes the product look like a collection of tools. It also sends a learning request back into chat as a long answer instead of giving the learner a place to act, receive feedback, and continue.

The product needs to distinguish three concerns:

1. how a learner starts (`goal`, `source`, or `problem`);
2. how the system decides the next learning action (BKT evidence, subject SLR support, material affordances, and governed preferences);
3. how an action is rendered and executed (lesson, assessment, retrieval, visual, or audio output).

## Decision

Use a goal-first Learning Pack and a deterministic `LearningComponentPlan` as the product spine:

```text
goal / source / problem
        ↓
LearningPack + MaterialAnalysisSnapshot when a source exists
        ↓
BKT + subject SLR + material affordances + explicit context
        ↓
LearningComponentPlan
        ↓
full-screen Learning Canvas
        ↓
component executor → LearnerEvent → BKT/profile → unstarted-tail replan
```

The homepage and Chat create or resume a Pack and show a short plan preview. The learner then enters `/space/learning/{packId}`. The canvas is the primary learning surface:

- desktop: learning path, current component, and “why this step” evidence in a full-screen layout;
- mobile: step navigation, current component, and a bottom Why Drawer;
- entering the canvas collapses the workspace sidebar so the learning task has the available screen;
- completed components are immutable; only the unstarted tail is replaced by a new plan version.

Courseware, flashcards, quiz, diagrams, and audio remain available as component executors and historical artifacts. They are not homepage mode choices and do not need a second upload when they share a Pack/material snapshot.

Hermes/Reflection/Compass provide the governed, minimal `PersonalizationContext` consumed by the selector. BKT remains the source of concept-state updates; SLR selects temporary support actions; neither layer is allowed to become an ability, personality, or learning-style label.

## Alternatives considered

### Generator-first homepage

Rejected. It asks the learner to understand internal artifact types before stating a goal and hides the adaptive path that differentiates TraitTutor.

### Chat-only learning loop

Rejected. Chat remains valuable for starting, asking follow-up questions, and referencing saved artifacts, but it is not a reliable surface for step completion, progress, evidence, or replanning.

### LLM-generated free-form learning path

Rejected as the source of truth. Models may generate component content through the existing Gateway, but the sequence, dependencies, event mapping, and evidence boundaries must be deterministic and auditable.

## Consequences

Positive:

- The first-use story is understandable without knowing the names of internal generators.
- BKT/SLR/Compass decisions become visible as a sequence of actionable learning components.
- Existing courseware, flashcard, quiz, visual, audio, and artifact routes remain reusable and backward compatible.
- A component event can update the learner model and replan only future work without rewriting completed evidence.

Costs and follow-up:

- The backend must persist plan versions, component status, interaction events, and executor output references.
- The frontend needs a full-screen canvas, responsive step navigation, and a consistent Why Drawer.
- Browser smoke must cover goal/source → plan → component interaction → BKT/replan → artifact follow-up.
- Every new product-owned UI state must be added to both zh and en locale catalogs.

## Implementation references

- `traittutor/learning_components.py`
- `traittutor/api/routers/learning_packs.py`
- `traittutor/services/evolution/core.py`
- `web/components/learning/LearningCanvas.tsx`
- `docs/PRD.md`
- `docs/HERMES_EVOLUTION_DESIGN.md`
