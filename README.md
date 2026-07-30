# TraitTutor

TraitTutor is a learner-centered study workspace. It turns a learner profile and source material into personalized courseware, flashcards, quizzes, and research support while keeping the reasoning evidence inspectable.

The current product scope is intentionally narrow:

- Big Five profile assessment as a personalization signal, not diagnosis or ability scoring.
- Material upload / paste analysis with subject, grade, language, difficulty, and source metadata.
- Trait-aware generation for courseware, flashcards, and quizzes.
- Learner model storage with evidence-backed memory, BKT-style concept progress, and a lightweight learning knowledge graph.
- Chat, notebooks, knowledge bases, saved learning packs, personas, settings, and learning exploration.
- Home composer learning modes: 解题, 生成 Quiz, 学习探索, 知识图解, 学习路径, 改写课件, 生成闪卡, and Humanizer.

## Core workflow

1. Complete the learner profile.
2. Upload or paste learning material.
3. TraitTutor analyzes the material and preserves file metadata.
4. Generate one of the learning artifacts:
   - courseware
   - flashcards
   - quiz
5. Save the artifact into the learning workspace.
6. Review, answer, or discuss it in chat.
7. Verified learning events update the learner model; raw uploads only update the knowledge graph, not mastery.

## Generation pipeline

The generation stack lives under `traittutor/generate/`.

Key pieces:

- `material_analysis.py` identifies source characteristics and queues graph extraction.
- `material_abstraction.py` reuses the existing material analysis to build subject-aware generation targets.
- `service.py` orchestrates profile strategy, material grounding, generation, validation, persistence, and artifact routing.
- `tasks.py` provides durable asynchronous generation tasks with resume, retry, and cancellation.
- `visuals.py` contains the generation-side image/visual asset seam.
- `assessment/slr_action_catalog.json` stores editable SLR/action support rules outside code.

## Learner model

The learner model lives under `traittutor/personalization/`.

It separates:

- learner profile and explicit preferences;
- material-derived knowledge graph concepts;
- verified learning events from quiz answers, flashcard reviews, and courseware outcomes;
- BKT-style concept progress;
- visible rationales used in later generation.

Chat history can contribute curated, auditable memory signals. It is not copied wholesale into BKT. New files can update the knowledge graph after material analysis, but mastery changes only after learning events.

## Home learning modes

The home composer keeps the broad learning entry points the product needs:

- 解题: step-by-step problem solving inside chat.
- 生成 Quiz: quiz generation from source material.
- 学习探索: source-grounded exploration and research.
- 知识图解: diagram-oriented explanation and concept mapping.
- 学习路径: practice, feedback, and review planning.
- 改写课件 and 生成闪卡: structured learning artifact generation.
- Humanizer: natural rewriting while preserving meaning.

These are TraitTutor product modes, not inherited capability identities. Courseware, flashcards, and quizzes remain backed by the structured generation flow.

## Local development

Install dependencies:

```bash
pip install -e ".[dev]"
cd web
npm install
```

Run the backend:

```bash
traittutor serve
```

Run the web app:

```bash
cd web
npm run dev
```

Or launch both through the local runtime:

```bash
traittutor start
```

## Useful checks

Backend generation and learner-model checks:

```bash
.venv/bin/pytest \
  tests/traittutor/test_material_resolver.py \
  tests/traittutor/test_material_abstraction.py \
  tests/traittutor/test_generate_suite.py \
  tests/traittutor/test_personalization.py \
  tests/traittutor/test_learning_knowledge_graph.py \
  tests/traittutor/test_graph_repository.py \
  tests/services/test_evolution_core.py -q
```

Frontend checks:

```bash
cd web
npm run test:node
```

## Product boundary

TraitTutor does not treat personality scores as a diagnosis, learning-style label, or objective ability measure. They are only used as adjustable personalization cues.
