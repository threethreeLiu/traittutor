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

- Big Five profile assessment (`traittutor_profile` router + `traittutor/assessment/big_five.py`, surfaced through onboarding).
- Trait-aware courseware, flashcard, and quiz generation (`traittutor_generate` router + `traittutor/generate/{runner,tasks,service,courseware,flashcards,quiz,materials,visuals,grounding,catalog,evaluation,benchmark}`).
- Fusion with Space, Knowledge, Notebook, Question Bank, Chat, and Settings.
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

## Checks

- Python import and CLI after rename: `python -c "import traittutor; import traittutor_cli"` and `traittutor --help`.
- Backend tests for TIPI scoring, generate-suite schema behavior, prompt format round-trip (`tests/core/test_prompt_manager.py`, `tests/core/test_prompt_parity.py`), and security/admin boundaries (`tests/agent_runtime/test_graph.py`, `tests/api/test_unified_ws_turn_runtime.py`).
- Frontend tests for the TraitTutor workbench path (`npm run test:node`, `npm run lint`, Playwright smoke: profile → material → type → generate → save → chat).
