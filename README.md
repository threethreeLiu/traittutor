# TraitTutor

<p align="center">
  <strong>A goal-first AI learning coach that turns questions and real materials into adaptive, evidence-aware study paths.</strong>
</p>

<p align="center">
  <a href="README.zh-CN.md">中文</a>
  ·
  <a href="#features">Features</a>
  ·
  <a href="#who-it-is-for">Use cases</a>
  ·
  <a href="#technical-design-and-innovation">Technical design</a>
  ·
  <a href="#quick-start">Quick start</a>
  ·
  <a href="#verification">Verification</a>
  ·
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-ready-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-blue">
</p>

TraitTutor turns a goal, a question, or real learning material into a continuous adaptive study path. Learners can start without uploading anything, then add a PDF, document, deck, spreadsheet, image, or text when it helps. TraitTutor combines source evidence, BKT-style concept signals, subject support actions, and material affordances to plan the next learning component—and keeps both the rationale and learning evidence visible as practice unfolds.

The product is intentionally narrow: profile signals personalize support, but they are never used as diagnosis, ability labels, or learning-style claims.

## Who it is for

TraitTutor is built for independent learners, tutors, and learning-product teams who need more than a one-off answer. It supports three equivalent starting points: a learning goal, a question, or source material. Typical uses include turning a textbook chapter into a practice path, preparing for an exam from a real PDF, and continuing a study plan after quiz or flashcard evidence reveals a weak concept.

## Features

- **Source-grounded material analysis**: subject, grade band, difficulty, language, concept candidates, page evidence, and augmentation decisions are stored as a reusable material snapshot.
- **Goal-first learning paths**: a goal, source, or problem creates one Learning Pack and a deterministic component plan instead of forcing the learner to choose a generator.
- **Full-screen learning canvas**: the path, current component, and “why this step” evidence are shown together; the workspace sidebar collapses when learning begins.
- **One material, many artifacts**: courseware, flashcards, and quizzes can share the same Learning Pack instead of requiring repeated uploads.
- **Learning-event feedback loop**: quiz answers and flashcard reviews write auditable learner events that update BKT-style concept progress.
- **Explainable learner model**: Reflection / Compass memory governance separates explicit preferences, inferred support needs, concept progress, evidence, and deletion/rebuild behavior.
- **Chat-native study workflows**: chat, Deep Research, guided solving, learning exploration, knowledge diagrams, and follow-up questions over generated artifacts.
- **Trait-aware generation boundary**: profile cues adapt wording and support actions without turning personality scores into labels or judgments.
- **Gateway-based model calls**: generation and chat use the configured model gateway for routing, retry, fallback, and auditability.

## How it works

```text
Goal / source / problem
        ↓
LearningPack + MaterialAnalysisSnapshot (when a source exists)
        ↓
BKT concept evidence + subject SLR support + material affordances
        ↓
LearningComponentPlan
        ↓
Full-screen Learning Canvas
        ↓
Lesson / assessment / retrieval / visual / audio executors
        ↓
LearnerEvent → BKT / knowledge graph / learner model
        ↓
Replan only the unstarted tail → next component
```

Courseware, flashcards, and quizzes are execution surfaces and historical artifacts, not homepage modes. Courseware completion is treated as participation evidence. Durable mastery changes come from explainable learning events such as quiz answers, flashcard reviews, and mastery attempts.

The learning canvas is the product destination; chat remains the entry point for goals, questions, source follow-up, and second questions over saved artifacts. TraitTutor-owned UI states are bilingual, while learner-authored text and source material keep their original language.

## Technical design and innovation

```text
Next.js learning workspace
        ↓ goal / question / source
FastAPI product API → material analysis → Learning Pack + component plan
        ↓                                  ↓
configured model gateway              durable learning events
        ↓                                  ↓
courseware / flashcards / quiz       BKT-style concept evidence + learner model
```

The core design choice is to keep the learning sequence deterministic and explainable even when a model generates content. Material evidence, concept signals, subject support actions, and explicit learner preferences determine the component plan; generated artifacts execute that plan. Graded quiz answers and flashcard reviews become auditable learner events, so only the unstarted tail of a path is replanned rather than rewriting completed learning evidence.

## Open-source and dependency boundary

TraitTutor's repository code is released under [Apache-2.0](LICENSE). Python dependencies are declared in `pyproject.toml`, frontend dependencies in `web/package.json`, and each dependency keeps its own license. The model gateway can use locally configured commercial or open model providers, but no provider keys, model weights, private user materials, or proprietary service outputs are included in this repository.

## Roadmap and delivery

The current release focuses on a runnable goal-to-learning-path loop, source analysis, practice evidence, and explainable next steps. Next priorities are generation-evaluation gates, durable TTS assets, and browser smoke coverage for the full material-to-follow-up journey.

## Quick start

### Requirements

- Python 3.11, 3.12, or 3.13
- Node.js 20+
- npm

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

traittutor serve
```

### Frontend

```bash
cd web
npm install
npm run dev
```

The app reads local runtime settings from ignored files such as `web/.env.local` and `config/models.local.yaml`. Use the example files as templates and never commit real model keys.

For a single-host Ubuntu deployment, follow [DEPLOYMENT.md](DEPLOYMENT.md). The repeatable flow is `bootstrap_production_server.sh` once, then `deploy_production.sh deploy` for each committed release; the same script also provides status, logs, and rollback commands.

## Configuration

Model configuration is intentionally local-first:

- copy `config/models.local.example.yaml` to `config/models.local.yaml`;
- configure your provider profiles and active model;
- keep real keys out of Git;
- route new generation paths through the existing gateway instead of calling providers directly.

## Verification

Focused backend checks:

```bash
.venv/bin/python -m pytest \
  tests/traittutor/test_business_learning_loop.py \
  tests/traittutor/test_learning_pack_events.py \
  tests/traittutor/test_generate_suite.py \
  tests/traittutor/test_personalization.py \
  tests/api/test_personalization_router.py \
  tests/services/test_evolution_core.py -q
```

Frontend checks:

```bash
cd web
npm run test:node
npm run lint
npm run build
```

`npm run build` may need network access if Next.js fetches remote fonts during the production build.

## Repository layout

```text
traittutor/                 FastAPI backend, generation, gateway, learner model
web/                        Next.js app
tests/                      Backend and business-loop regression tests
web/tests/                  Frontend node-based regression tests
config/                     Example runtime configuration
scripts/                    Local operational helpers
```

## Product safety boundary

TraitTutor uses profile and memory signals as adjustable teaching context only. It does not:

- diagnose personality, cognition, or ability;
- claim objective learning gains from profile data;
- treat browsing, saving, or courseware viewing as verified mastery;
- expose hidden prompts or private reasoning in user-facing explanations.

Why Drawer explanations should show the current goal, source evidence, weak concepts, explicit preferences, teaching actions, and degradation state—not private chain-of-thought, raw prompts, or personality judgments.

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Keep changes inside the TraitTutor learning-product boundary, add tests for behavior changes, and preserve bilingual user-facing copy where applicable.

## Security

Please do not open public issues containing credentials, private URLs, model keys, or user materials. See [SECURITY.md](SECURITY.md).

## License

TraitTutor is licensed under the [Apache License 2.0](LICENSE).
