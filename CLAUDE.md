# TraitTutor Claude Guide

## Mission

This repository is being rebuilt as TraitTutor from three read-only sources:

- TraitTutor supplies the app/runtime foundation.
- aio-prompt supplies generation prompts.
- TraitTutor_flask_app supplies BFI-10/TIPI scoring and personalized courseware logic.

The final product should be TraitTutor throughout package names, CLI, SDK, API title, and visible UI.

## MVP Boundary

Build only:

- Big Five profile assessment.
- Trait-aware courseware, flashcard, and quiz generation.
- Fusion with Space, Knowledge, Notebook, Question Bank, Chat, and Settings.

Do not build posttests, PPS, RIMMS, PSRLS/SRL questionnaires, experiment grouping, knowledge pretests, paper statistics, or Lark manuscript flows.

## Work Rules

- Do not modify sibling source repositories.
- Use feature branches and commit after verification.
- Keep inherited architecture where it helps: FastAPI routers, capabilities, stream events, runtime settings, and frontend workspace routes.
- Do not keep `traittutor` as a compatibility import or command unless explicitly requested later.
- Treat personality as a bounded personalization cue, never a diagnosis or proof of learning gain.

## Checks

- Python import and CLI after rename: `python -c "import traittutor; import traittutor_cli"` and `traittutor --help`.
- Backend tests for TIPI scoring and generate-suite schema behavior.
- Frontend tests for the TraitTutor workbench path.
