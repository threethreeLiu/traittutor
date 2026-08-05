# TraitTutor Claude Guide

> Living guide for the rebuild. Companion: `AGENTS.md` (current state + per-scope verification) and `docs/PRD.md` (scope, milestones, acceptance). When this file disagrees with code, the code wins; fix the doc in the same change.

## Mission

This repository is being rebuilt as TraitTutor from three read-only sources:

- TraitTutor supplies the app/runtime foundation.
- aio-prompt supplies generation prompts (now in `traittutor/generate/prompts/*.md` Markdown form).
- TraitTutor_flask_app supplies BFI-10/TIPI scoring and personalized courseware logic.

The final product should be TraitTutor throughout package names, CLI, SDK, API title, and visible UI. The HKUDS rebrand is already complete on `main`; do not reintroduce HKUDS strings in user-visible surfaces.

## MVP Boundary

Build only:

- Goal-first learning launch: a learner can start from a goal, source, or problem; Big Five setup is optional and must never block first value.
- Learning-component orchestration: users start one adaptive path; courseware, flashcards, quiz, image, and TTS are internal executors and historical outputs, not prerequisite mode choices.
- Big Five profile assessment (`traittutor_profile` router + `traittutor/assessment/big_five.py`, available as an optional support setting).
- Trait-aware courseware, flashcard, and quiz generation (`traittutor_generate` router + `traittutor/generate/{runner,tasks,service,courseware,flashcards,quiz,materials,visuals,grounding,catalog,evaluation,benchmark}`).
- Fusion with Space, Knowledge, Notebook, Question Bank, Chat, and Settings.
- Release demo loop: learning goal → reusable sources/material analysis → deterministic component plan → unified canvas → graded component `LearnerEvent` → BKT/knowledge/profile update → tail replan → Why Drawer explanation.
- The unified learning canvas is the primary learning workspace: it fills the available screen with path/current-component/why-this-step regions, and entering a learning path collapses the workspace sidebar. Chat remains the entry point for goals, sources, questions, and artifact follow-up rather than the place where the learning loop is rendered as a long answer.
- TraitTutor-owned UI states must have zh/en coverage through the shared locale switch, including loading, error, empty, dialog, button, ARIA, and Why Drawer copy. User-authored text, file names, source text, and generated learning content keep their source language.
- Auth, admin, and authenticated outputs gate (`traittutor/api/routers/{auth,admin,outputs}.py`) plus the `traittutor/gateway/` audit/router layer.

Do not build posttests, PPS, RIMMS, PSRLS/SRL questionnaires, experiment grouping, knowledge pretests, paper statistics, or Lark manuscript flows.

## Work Rules

- Do not modify sibling source repositories.
- Use feature branches and commit after verification (see `AGENTS.md` § "Repository Workflow" for the active branch list).
- Keep inherited architecture where it helps: FastAPI routers, capabilities, stream events, runtime settings, and frontend workspace routes.
- Do not keep `traittutor` as a compatibility import or command unless explicitly requested later.
- Treat personality as a bounded personalization cue, never a diagnosis or proof of learning gain. Every public profile response must carry `usage_boundary`.
- All prompt assets are Markdown (`*.md`); load them through `traittutor/services/prompt/markdown.py`. Do not add new YAML prompt loaders.
- All model calls go through `traittutor/gateway/` for prompt routing and audit.
- Keep old mastery-path style public entries hidden unless they are explicitly redesigned into the current learner-model flow. Do not hide or delete the core features: chat, Deep Research, guided solve, learning exploration, knowledge diagram, courseware, flashcards, quiz, and learner profile.
- A Learning Pack is anchored by a learning goal and may start without an uploaded document. Uploaded material is one traceable source among several, not a prerequisite for learning.
- Completed learning components are immutable evidence-backed steps. Replanning creates a new version and only replaces the unstarted tail.
- Courseware, flashcards, quiz, diagrams, and audio are component executors/history artifacts. They remain available in My Learning and through compatible routes, but are not homepage mode choices.

## Checks

- Python import and CLI after rename: `python -c "import traittutor; import traittutor_cli"` and `traittutor --help`.
- Backend tests for TIPI scoring, generate-suite schema behavior, prompt format round-trip (`tests/core/test_prompt_manager.py`, `tests/core/test_prompt_parity.py`), and security/admin boundaries (`tests/agent_runtime/test_graph.py`, `tests/api/test_unified_ws_turn_runtime.py`).
- Frontend tests for the TraitTutor workbench path (`npm run test:node`, `npm run lint`, the i18n surface regression, and Playwright smoke: goal/source → plan → full-screen canvas → component event → replan → artifact follow-up).
