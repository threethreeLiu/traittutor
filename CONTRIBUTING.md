# Contributing to TraitTutor

Thanks for helping improve TraitTutor. This repository is focused on one product: a source-grounded learning workspace that turns learner signals and uploaded material into courseware, flashcards, quizzes, research support, and an evidence-backed learner model.

## Product boundaries

Please keep contributions inside the current TraitTutor scope:

- learner profile and explicit learning preferences;
- material analysis with source metadata;
- courseware, flashcards, quizzes, guided solving, learning exploration, knowledge diagrams, and Deep Research;
- Learning Packs, Knowledge, Notebook, Question Bank, Chat, and the learner model;
- Hermes / Reflection / Compass memory governance for explainable personalization.

Do not add or reintroduce unrelated foundation surfaces unless they are first redesigned as TraitTutor learning features and covered by tests.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cd web
npm install
```

Run the backend:

```bash
traittutor serve
```

Run the frontend:

```bash
cd web
npm run dev
```

## Before opening a pull request

Run the smallest relevant checks for your change. For core learning-loop work, use:

```bash
.venv/bin/python -m pytest \
  tests/traittutor/test_business_learning_loop.py \
  tests/traittutor/test_learning_pack_events.py \
  tests/traittutor/test_generate_suite.py \
  tests/traittutor/test_personalization.py \
  tests/api/test_personalization_router.py \
  tests/services/test_evolution_core.py -q
```

Frontend:

```bash
cd web
npm run test:node
npm run lint
```

Build check:

```bash
cd web
npm run build
```

`npm run build` may need network access when Google Fonts are fetched by Next.js.

## Coding guidelines

- Keep prompt assets in Markdown and load them through the shared prompt catalog.
- Route generation through the gateway / runner path so model calls are auditable.
- Store generated artifacts with material snapshot, prompt signature, model, citations, visual generation status, and degradation state.
- Update tests with every behavior change.
- Never use personality scores as diagnosis, ability labels, learning-style labels, or proof of learning gain.

## Pull request checklist

- The change preserves courseware, flashcards, quiz, guided solving, learning exploration, knowledge diagrams, Deep Research, and learner model flows.
- Any new user-facing text is bilingual or goes through the existing i18n path.
- New data that affects personalization is evidence-backed and deletable/rebuildable.
- Public docs and screenshots describe TraitTutor, not inherited foundation products.
