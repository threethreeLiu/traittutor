# TraitTutor Agent Guide

> Living guide. Companion to `CLAUDE.md` (mission + checks) and `docs/PRD.md` (scope + acceptance). When this file disagrees with code, the code wins; please fix the doc in the same change.

## Product Goal

TraitTutor is a full product built in this repository from three read-only source projects:

- `/Users/lrm/Documents/code/TraitTutor`: engineering foundation for the web app, API server, CLI, capabilities, tools, knowledge, memory, notebooks, and streaming runtime.
- `/Users/lrm/Documents/code/aio-prompt`: source-grounded prompts (now Markdown) for study-guide, flashcard, quiz, and AI tutor generation.
- `/Users/lrm/Documents/code/TraitTutor_flask_app`: source for BFI-10/TIPI scoring and personalized courseware generation logic.

The product identity is TraitTutor. Do not preserve `traittutor` as a public package, CLI command, SDK class, API title, or visible brand.

## Current State (rebuilt so far)

What is already merged on `main` and what is the next feature slice — keep this list honest.

- **Rebrand** (`chore(brand)`, `docs: rewire HKUDS links`): HKUDS attribution removed from READMEs, badges, source. API title is `TraitTutor API`. Brand mark shipped via `web/components/brand/TraitTutorIcon*` with transparent asset fix.
- **Package rename** (`refactor/package-rename-traittutor`): `traittutor/` + `traittutor_cli/`, CLI entry `traittutor`, SDK facade `TraitTutorApp`, server target `traittutor.api.main:app`. No `traittutor` compatibility import.
- **Onboarding + accounts** (`feat: add consumer accounts and modal onboarding`, `feat: move Big Five assessment into onboarding`, `feat: complete consumer login and registration flow`): invite-only registration, bootstrap admin, modal onboarding funnel.
- **Big Five profile** (`feature/big-five-profile`, `feature/big-five-onboarding`): `traittutor_profile` router, `traittutor/assessment/big_five.py` (TIPI constants + scoring), `GET /api/traittutor_profile/questions`, profile list/create/delete.
- **Learning profile signal UI** (`feat: redesign learning profile signal interface`, `feat: add initial SLR learning profile graphs`): trait signal cards + profile maps render inside the workbench.
- **Config-driven model selector** (`feat: redesign config-driven chat model selector`, `feat(models): code-defined LLM models synced from CC Switch`): runtime config + code-defined catalog, no hard-coded provider list.
- **Generate suite v1** (`feature/generate-suite`, `feature/generate-suite-streaming`, `feature/material-resolution`, `feature/courseware-prompt-fusion`, `feature/flashcard-quiz-fusion`, `feature/generate-workbench-integration`): `traittutor_generate` router + `traittutor/generate/{runner,tasks,service,materials,material_analysis,courseware,flashcards,quiz,visuals,catalog,grounding,evaluation,benchmark}` with sync + 202-async paths and `/tasks/{id}/events` stream.
- **Prompt catalog & runner** (`feature/prompt-catalog-and-runner`, `feature/toc-agent-product`): `traittutor/services/prompt/markdown.py` parser, unified `generate/runner.py` chokepoint, prompt catalog exposed to chat.
- **Security remediation** (`security(auth)`, `security(api)`, `feat(gateway)`): invite-only registration, session revocation, authenticated artifact downloads, `admin` control-plane, `outputs` authenticated gateway, structured prompt routing with usage audit through `traittutor/gateway/`.
- **i18n polish** (`fix: translate personas workspace page`, `fix/chinese-persona-i18n`, `fix/personas-page-chinese`): zh/en parity for personas workspace.
- **Frontend workbench** (`feature/frontend-traittutor-workbench`, `feature/learning-space-modules`, `feature/traittutor-brand-icon`, `fix/transparent-brand-favicon`, `fix/brand-mark-import`, `fix/remove-sidebar-release-chrome`): `(utility)/profile` + `(utility)/space` workbench path, `SpaceDashboard` → `StudyToolWorkbench`, transparent brand mark, sidebar cleanup.

Next expected slices (not yet on `main`, see `docs/PRD.md` §10 for milestones):

- Generate suite fusion into Notebook / Knowledge / Question Bank / Space surfaces (`feature/generate-workbench-integration` finishing line).
- Generation eval benchmark (offline/online gating).
- Hardening pass on the GA checklist (`docs/PRD.md` §12).

## MVP Scope

Implement only:

- Big Five profile assessment using the BFI-10/TIPI items and O/C/E/A/N scoring.
- Trait-aware generation for courseware, flashcards, and quiz.
- Integration with the existing product surfaces: Space, Knowledge, Notebook, Question Bank, Chat, and Settings.

Do not implement in the MVP:

- posttests
- PPS
- RIMMS
- PSRLS/SRL questionnaires
- experiment grouping
- knowledge pretest
- paper statistics or Lark manuscript workflows

Personality scores are personalization cues only. Do not describe them as diagnosis, learning style classification, objective ability, or proof of learning gain.

## Repository Workflow

- Keep `/Users/lrm/Documents/code/TraitTutor`, `/Users/lrm/Documents/code/aio-prompt`, and `/Users/lrm/Documents/code/TraitTutor_flask_app` read-only.
- Work in `/Users/lrm/Documents/code/TraitTutor_all_in_one`.
- Use one branch per feature:
  - `init/traittutor-foundation`
  - `docs/source-projects-and-agent-guides`
  - `refactor/package-rename-traittutor`
  - `feature/big-five-profile`
  - `feature/generate-suite`
  - `feature/frontend-traittutor-workbench`
- After each feature branch, run focused verification, review the diff, and commit with a scoped message.
- Keep generated caches, runtime data, build output, virtual environments, `node_modules`, and `.next` out of commits.

## Implementation Rules

- Prefer existing runtime seams before adding new architecture: FastAPI routers, capability registry, stream events, runtime settings, notebooks, question bank, and frontend app routes.
- Rename the inherited foundation rather than adding a `traittutor` wrapper beside `traittutor`.
- Use `traittutor/` and `traittutor_cli/` as first-party Python packages.
- Use `traittutor` as the CLI entry point.
- Use `TraitTutorApp` as the SDK facade.
- Use `traittutor.api.main:app` as the server target.
- Keep source-project references in `docs/source-projects/`; runtime UI and product docs should say TraitTutor.

## Prompt Format

All prompt assets are Markdown files (`*.md`) — no YAML prompt files remain. One canonical format everywhere (`traittutor/**/prompts/`, `traittutor/tools/prompting/hints/`):

- YAML frontmatter holds metadata and short values (single-line strings, numbers, lists, nested dicts of short values such as `labels:`/`status:`).
- Multi-line strings live in the body under `## <key>` sections; nested keys use dotted paths (`## loop.system`).
- Load assets through the shared parser `traittutor/services/prompt/markdown.py` (`load_markdown_prompt`), which rebuilds the same nested dict the old YAML produced. Do not add new `yaml.safe_load` prompt loaders.
- `scripts/migrate_prompts_to_md.py --rerender` re-canonicalizes any edited prompt asset.
- Generation prompts live in `traittutor/generate/prompts/{courseware,flashcards,quiz}/*.md` and are routed through the unified `generate/runner.py` chokepoint; do not bypass the runner.
- Prompt selection and audit go through `traittutor/gateway/` so every model call is observable; new entry points must register there.

## Generate Suite Rules

- First response must be fast: emit progress/stream events immediately after request acceptance.
- Do not render partial strict JSON as final flashcards or quiz questions.
- Validate flashcard and quiz batches before showing them as accepted results.
- Persist final structured outputs with prompt signature, model, material source, profile summary, trace metadata, and created timestamp.
- If no model is configured, return a user-facing configuration message instead of crashing.
- Honor the per-trait `usage_boundary` (no diagnosis, no learning-style labels, no ability claims) inside prompts that consume the profile.

## Security & Gateway

- Auth is invite-only (`traittutor/api/routers/auth.py`); session revocation is supported.
- The `admin` control-plane (`traittutor/api/routers/admin.py`) and the authenticated `outputs` gateway (`traittutor/api/routers/outputs.py`) sit behind the same session check; tests pin those boundaries (see `tests/agent_runtime/test_graph.py` and the security test set).
- Every generation call passes through `traittutor/gateway/` for model + prompt routing and audit; do not call providers directly from new code paths.

## Verification

Minimum checks by scope (run before committing the slice they belong to):

- Rename: `python -c "import traittutor; import traittutor_cli"` and `traittutor --help`.
- Big Five: unit tests for all ten TIPI items, reverse scoring, missing-answer validation, profile summary boundaries, and `usage_boundary` propagation (`tests/agent_runtime`, `tests/traittutor/test_material_resolver.py`).
- Generate suite: mock model tests for courseware, flashcards, quiz, first progress event, schema validation, batch failure, prompt fusion (`tests/traittutor/test_generate_suite.py`, `test_flashcard_prompt_fusion.py`, `test_quiz_prompt_fusion.py`, `test_generation_tasks.py`, `test_prompt_runner.py`).
- Prompt format: `tests/core/test_prompt_manager.py`, `tests/core/test_prompt_parity.py` (every prompt must round-trip through `markdown.py`).
- Security/admin: `tests/agent_runtime/test_graph.py`, `tests/api/test_unified_ws_turn_runtime.py`, `tests/capabilities/test_status_i18n_consistency.py`, `tests/services/test_media_gen.py` — keep these green when touching auth, gateway, or i18n.
- Frontend: `npm run test:node`, `npm run lint`, and a Playwright smoke path for profile → material → type → generate → save → chat.

Use `rg` for source searches. Avoid broad refactors unrelated to the active branch.
