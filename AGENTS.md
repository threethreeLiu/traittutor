# TraitTutor Agent Guide

## Product Goal

TraitTutor is a full product built in this repository from three read-only source projects:

- `/Users/lrm/Documents/code/TraitTutor`: engineering foundation for the web app, API server, CLI, capabilities, tools, knowledge, memory, notebooks, and streaming runtime.
- `/Users/lrm/Documents/code/aio-prompt`: source-grounded YAML prompts for study-guide, flashcard, quiz, and AI tutor generation.
- `/Users/lrm/Documents/code/TraitTutor_flask_app`: source for BFI-10/TIPI scoring and personalized courseware generation logic.

The product identity is TraitTutor. Do not preserve `traittutor` as a public package, CLI command, SDK class, API title, or visible brand.

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

## Generate Suite Rules

- First response must be fast: emit progress/stream events immediately after request acceptance.
- Do not render partial strict JSON as final flashcards or quiz questions.
- Validate flashcard and quiz batches before showing them as accepted results.
- Persist final structured outputs with prompt signature, model, material source, profile summary, trace metadata, and created timestamp.
- If no model is configured, return a user-facing configuration message instead of crashing.

## Verification

Minimum checks by scope:

- Rename: `python -c "import traittutor; import traittutor_cli"` and `traittutor --help`.
- Big Five: unit tests for all ten TIPI items, reverse scoring, missing-answer validation, and profile summary boundaries.
- Generate suite: mock model tests for courseware, flashcards, quiz, first progress event, schema validation, and batch failure.
- Frontend: `npm run test:node`, `npm run lint`, and a Playwright smoke path for profile -> material -> type -> generate -> save -> chat.

Use `rg` for source searches. Avoid broad refactors unrelated to the active branch.
