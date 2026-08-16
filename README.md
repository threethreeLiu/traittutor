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
  <a href="#research-foundation-and-team-contribution">Contribution</a>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-ready-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-blue">
</p>

TraitTutor turns a goal, a question, or real learning material into a continuous adaptive study path. Learners can start without uploading anything, then add a PDF, document, deck, spreadsheet, image, or text when it helps. TraitTutor combines source evidence, BKT-style concept signals, subject support actions, and material affordances to plan the next learning component—and keeps both the rationale and learning evidence visible as practice unfolds.

The personalization engine combines a personality profile with BKT-style concept signals that keep learning about the learner: profile signals steer which components, pacing, and feedback appear on the learning path — the component sequence on the canvas is a direct product of personalization — while only server-graded, reliably attributed answers ever update learning evidence. Profile signals themselves never become diagnosis, ability labels, or BKT evidence.

## Who it is for

TraitTutor is built for independent learners, tutors, and learning-product teams who need more than a one-off answer. It supports three equivalent starting points: a learning goal, a question, or source material. Typical uses include turning a textbook chapter into a practice path, preparing for an exam from a real PDF, and continuing a study plan after quiz or flashcard evidence reveals a weak concept.

## Features

- **Source-grounded material analysis**: subject, grade band, difficulty, language, concept candidates, page evidence, and augmentation decisions are stored as a reusable material snapshot.
- **Safe intelligent learning routing**: Learn intercepts suspicious prompt-injection attempts, then uses the Gateway to decide whether a one-off answer or a continuous learning path fits best; low-confidence cases remain learner-controlled.
- **Goal-first learning paths**: a goal, source, or problem creates one Learning Pack and a deterministic component plan instead of forcing the learner to choose a generator.
- **Full-screen learning canvas**: the path, current component, and “why this step” evidence are shown together; the workspace sidebar collapses when learning begins.
- **One material, many artifacts**: courseware, flashcards, and quizzes can share the same Learning Pack instead of requiring repeated uploads, while remaining available as standalone study tools.
- **Learning-event feedback loop**: server-graded quiz and short-answer results write auditable learner events that update BKT-style concept progress; flashcard self-ratings record participation only.
- **Explainable learner model**: Reflection / Compass memory governance separates explicit preferences, inferred support needs, concept progress, evidence, and deletion/rebuild behavior.
- **Chat-native study workflows**: chat, Deep Research, guided solving, learning exploration, knowledge diagrams, and follow-up questions over generated artifacts.
- **Trait-aware generation boundary**: profile cues adapt wording and support actions without turning personality scores into labels or judgments.
- **Human quality confirmation**: generated artifacts with evaluation concerns remain reviewable but are only attached to a Learning Pack after the learner confirms them.
- **Gateway-based model calls**: generation, intent classification, and chat use the configured model gateway for routing, retry, fallback, and auditability.

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

Learning paths are the default destination for sustained learning. Courseware, flashcards, and quizzes remain available as standalone study tools and can also attach to a Learning Pack. Courseware completion and flashcard self-ratings are participation evidence. Durable mastery changes come only from server-verifiable answers such as quiz and short-answer mastery attempts.

The learning canvas is the sustained-learning destination; Assistant handles one-off research, analysis, problem solving, and writing tasks. Learn only routes automatically when confidence is high; ambiguous input, classifier failure, and unavailable models show a clear learner choice. Suspicious privilege-escalation, prompt-exfiltration, or attachment-instruction requests never drive routing, pack creation, or learner-profile updates. TraitTutor-owned UI states are bilingual, while learner-authored text and source material keep their original language.

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

The core design choice is to keep the learning sequence deterministic and explainable even when a model generates content. Material evidence, concept signals, subject support actions, and explicit learner preferences determine the component plan; generated artifacts execute that plan. Server-graded answers become auditable learner events, so only the unstarted tail of a path is replanned rather than rewriting completed learning evidence.

## Research foundation and team contribution

TraitTutor's educational-personalization core grew from the team's unpublished research on bounded learner-support routing. To protect the ongoing study, this repository does not publish the manuscript title, participant records, instruments beyond those required by the product, statistical results, routing matrix, experimental conditions, or research prompts. This section discloses only the minimum design boundary needed to identify the team's implemented contribution. No learning-gain, long-term-effect, or causal claim is made from the unpublished study.

The team-developed competition contribution comprises:

- **Bounded profile-to-support routing:** a brief learner profile produces adjustable teaching-support cues. In the product this is the personalization engine behind component choice, pacing, and feedback; it remains a support signal, never a diagnosis, ability label, fixed learning style, or BKT evidence. The repository contains only the product policy needed to run and inspect this boundary; the study's full routing matrix and experimental variants remain private.
- **Separation of fixed content from variable support:** source-grounded objectives, concepts, terminology, and factual boundaries remain traceable; personalization changes support, feedback, and pacing, not source facts or grading rules.
- **Auditable generation and bounded repair:** structured generation retains source evidence and a reviewable run trace. Failed evaluation enters a limited repair or human-confirmation path; questionable output cannot silently become learning evidence.
- **Separation of system and learner evidence:** evaluator output, generation differences, and routing traces describe implementation only. Durable learning evidence requires a server-traceable verdict on a valid item with reliable knowledge-component attribution. Browsing, dwell time, personality scores, and self-reported mastery never update BKT.
- **Rewritten Core Learning workflow:** the team implemented the product-owned path from material upload to Learning Pack and component plan, published courseware, flashcards and quizzes, immutable LearnerEvents, server-owned grading, error repair and review, subject-isolated learner views, and recoverable progress. Completed learning evidence is never rewritten when the remaining path changes.
- **Rewritten ResearchWorkspace workflow:** the team implemented versioned research briefs, durable and resumable runs, explicit source policies, separately represented sources, claims and notes, source invalidation, conflict review, revisioned reports, and evidence-bound follow-up. Retrieved evidence and model inference remain visibly distinct, and research claims retain source links.
- **New Learning Tools workbench:** the team added the independent `/assist` workspace with explicit Chat, Mastery Practice, Solver, Learning Exploration, Knowledge Diagram, and Humanizer modes. Mode choice travels as typed metadata while the user's original message remains unchanged; capability prompts are server-owned. The workbench does not silently turn ordinary chat into a Learning Pack, research run, or learner-evidence update.
- **Product-owned persistence and governance:** the team implemented user-governed memory and owner-bound SQLite runtime storage across learning, research, profile, and conversation domains.

This repository also reuses selected general-purpose Agent, model-provider, RAG, parsing, and frontend foundations from [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor) under Apache-2.0. Those foundations are disclosed and are not claimed as independently created competition innovations. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for provenance and licenses.

## Core product screenshots

**The profile produces bounded teaching-support cues, not ability or psychological labels.**

![TraitTutor personality profile and SRL support boundary](docs/assets/readme/personality-support-profile.jpg)

**The core learning page combines a recoverable path, starting diagnostic, confidence input, server grading, and in-context assistance.**

![TraitTutor core learning path and starting diagnostic](docs/assets/readme/core-learning-path.jpg)

**The rewritten ResearchWorkspace keeps a versioned brief, recoverable run, evidence workspace, and report follow-up in one source-traceable workflow.**

![TraitTutor rewritten ResearchWorkspace](docs/assets/readme/research-workspace.jpg)

**The new Learning Tools workbench exposes six explicit modes without mixing ordinary conversation with the Core Learning or Research runtimes.**

![TraitTutor Learning Tools mode selector](docs/assets/readme/learning-tools-modes.png)

**Learning evidence stays subject-scoped and server-owned; missing evidence remains visibly unavailable.**

![TraitTutor subject-scoped learning evidence boundary](docs/assets/readme/learning-evidence-boundary.jpg)

**Inferred memory requires learner confirmation and remains correctable, deactivatable, deletable, and auditable.**

![TraitTutor long-term memory governance](docs/assets/readme/memory-governance.jpg)

These screenshots are captured from a local demo state and disclose UI behavior only. They contain no account information that identifies a real person and no participant record; the repository does not include the example learning document, research source corpus, underlying profile responses, learner answers, or runtime records.

## Open-source and dependency boundary

TraitTutor's repository code is released under [Apache-2.0](LICENSE). Python dependencies are declared in `pyproject.toml`; frontend dependencies are declared and locked in `web/package.json` and `web/package-lock.json`; third-party provenance and licenses are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The model gateway can use locally configured commercial or open model providers, but no provider keys, model weights, private user materials, paper experiment records, or proprietary service outputs are included in this repository.

## Roadmap and delivery

The current release focuses on a runnable goal-to-learning-path loop, source analysis, practice evidence, and explainable next steps. Generation evaluation now uses learner confirmation, and durable TTS assets can attach to learning components. Ongoing delivery work focuses on browser smoke coverage for safe routing, learning paths, practice, and reviewed artifacts.

### Public acceptance criteria

Before release, verify as the current user that suspicious prompts and attachment instructions cannot create a path or update a learner profile; low-confidence input always presents an explicit choice in every Learn session; a path restores generated questions and progress after refresh; only server-verified answers update mastery and replan the unstarted tail; a reviewed artifact can attach to a Learning Pack only after confirmation; and Assistant conversations, learning paths, and standalone tools remain separately accessible with non-mixed histories. Regression coverage must include real-server two-user isolation, these learning invariants, and the critical browser journey.

## Quick start

### One-command local development

```bash
./scripts/start_local_dev.sh
```

This starts the API at `http://127.0.0.1:8001` and the web app at
`http://127.0.0.1:3782`. Both reload automatically when their source files
change. On a first run, the script creates `.venv` and installs missing Python
or frontend dependencies. Press `Ctrl-C` to stop both servers.

### Container

```bash
python scripts/docker_compose.py up --build -d
python scripts/docker_compose.py logs -f
python scripts/docker_compose.py down
```

The wrapper always uses the canonical `compose.yaml` and maps the ports stored
in TraitTutor settings. Host bindings default to `127.0.0.1`.

### Requirements

- Python 3.11, 3.12, or 3.13
- Node.js 20+
- npm

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

python -m uvicorn traittutor.api.main:app --host 127.0.0.1 --port 8001
```

### Frontend

```bash
cd web
npm install
npm run dev
```

The app reads local runtime settings from ignored files such as `web/.env.local` and `config/models.local.yaml`. Use the example files as templates and never commit real model keys.

The supported `scripts/start_local_dev.sh`, container, and production deploy
profiles enable the four verified v2.7 seams (Context Snapshot, canonical
grading, PageSchema, and PageSchema CSP). Each remains independently
reversible at process startup by setting its `TRAITTUTOR_*` value to `0` and
restarting; PageSchema CSP is applied by the runtime Next proxy rather than
baked into the image. A bare library import still defaults off to avoid
accidental state writes.

## Configuration

Model configuration is intentionally local-first:

### LLM Model Setup

TraitTutor uses a code-defined model catalog stored in `config/models.local.yaml`
(gitignored — never commit real keys). Two templates are provided:
- `config/models.local.example.yaml` — uses `env(VAR)` references (committed)
- `config/models.local.template.yaml` — uses explicit placeholder values (gitignored)

**Setup steps:**

```bash
# 1. Copy the example template (recommended)
cp config/models.local.example.yaml config/models.local.yaml

# 2. Edit config/models.local.yaml and fill in your API keys
# 3. Set your active model by changing the `active` field
```

**File structure:**

```yaml
active: zhipu-glm                    # default model id (must match a model below)

models:
  - id: zhipu-glm                    # stable slug (used in code/config)
    name: Zhipu GLM 5.2              # human label in model picker
    binding: custom_anthropic         # provider key (see below)
    base_url: https://...             # provider endpoint
    api_key: env(ZHIPU_API_KEY)       # literal key OR env(VAR_NAME)
    model: glm-5.2                    # model id sent to the API
    context_window: 128000            # optional: max context tokens
```

**Binding types** (`binding` field):

| Binding | Description |
|---------|-------------|
| `custom` | OpenAI-compatible endpoint |
| `custom_anthropic` | Anthropic-API-compatible endpoint |
| `anthropic`, `openai`, `deepseek`, `zhipu`, `moonshot`, … | Built-in provider shortcuts (see `traittutor/services/provider_registry.py` PROVIDERS) |

**API key formats:**

```yaml
# Inline literal key (NOT recommended — risk of committing secrets)
api_key: "sk-xxxxxxxx"

# Environment variable (recommended)
api_key: env(MY_API_KEY)
```

Set environment variables in your shell or `.env` file:

```bash
export ZHIPU_API_KEY="sk-xxxxxxxx"
export DEEPSEEK_API_KEY="sk-xxxxxxxx"
```

**Provider examples:**

```yaml
# Zhipu GLM (custom_anthropic binding)
- id: zhipu-glm
  name: Zhipu GLM 5.2
  binding: custom_anthropic
  base_url: https://open.bigmodel.cn/api/anthropic
  api_key: env(ZHIPU_API_KEY)
  model: glm-5.2

# DeepSeek V4 (custom_anthropic binding)
- id: deepseek-v4
  name: DeepSeek V4
  binding: custom_anthropic
  base_url: https://api.deepseek.com/anthropic
  api_key: env(DEEPSEEK_API_KEY)
  model: deepseek-v4-pro

# OpenAI (built-in binding)
- id: openai-gpt4
  name: GPT-4o
  binding: openai
  api_key: env(OPENAI_API_KEY)
  model: gpt-4o
```

**Runtime path precedence:** The loader checks `$TRAITTUTOR_HOME/config/models.local.yaml`
first (production/symlink deployments), then falls back to repo-root `config/models.local.yaml`
(local development). When the file is absent or empty, the LLM catalog falls back to its
JSON default (empty) — define at least one model to enable chat.

**Auto-generation:** Use `traittutor models sync-cc-switch` to sync with CC Switch providers.

### Quota-Exhaustion Model Rotation (opt-in)

When a model's billing plan is exhausted, its provider returns an error like `403
You've reached your usage limit for this billing cycle` (e.g. Kimi). By default the
error surfaces verbatim to the caller. Setting this server-only flag makes every LLM
path automatically retry the current request on a backup model:

```bash
export TRAITTUTOR_GATEWAY_QUOTA_ROTATION=1   # default OFF — rollback = set to 0
```

Behavior when enabled:

- **Covers all LLM paths** — chat, agents, research, agent runtime (not just
  generation): rotation is inserted at the 4 shared gateway/factory choke points.
- **Error-driven, per-request only** — a quota error triggers rotation to the next
  configured route; the request's `active` model setting is never changed.
- **Bounded** — at most 2 routes × 2 attempts per route within a total deadline;
  a quota error rotates immediately, a transient error (timeout/5xx/rate limit)
  retries once on the same route first.
- **Streaming** — a stream rotates only if the quota error arrives *before* any
  text/reasoning/tool output; once output has been sent, the error surfaces verbatim
  (no silent retraction).
- **Circuit breaker** — after repeated failures a route is skipped for a cooldown
  (`TRAITTUTOR_GATEWAY_ROUTE_HEALTH_PATH`, shared across processes and paths).
- **No double rotation** — `generate:*` calls keep their own generation route policy;
  the general rotation excludes them.

Requires **≥2 models** in `config/models.local.yaml` (a primary plus at least one
backup). With one model or the flag off, behavior is unchanged.

### Canonical BKT Calibration

Canonical BKT calibration is deployment-owned. Generate an artifact only from
a sufficiently large immutable strong-evidence ledger, then install it at
`$TRAITTUTOR_HOME/config/bkt-parameters.json`:

```bash
.venv/bin/python scripts/calibrate_bkt.py \
  --ledger data/user/workspace/learning_model/learner_events.json \
  --output config/bkt-parameters.json \
  --version traittutor-bkt-YYYY-MM-v1
```

The command requires held-out improvement and defaults to at least 500 strong
observations, 50 user/subject/KC sequences, and 20 learners. Until those gates
are met, the UI continues to show evidence counts and uncertainty rather than
a posterior percentage.

For Compose, place the artifact under the persistent host `data/` directory
(for example `data/system/config/bkt-parameters.json`) and set
`TRAITTUTOR_BKT_PARAMETERS_PATH=/app/data/system/config/bkt-parameters.json`
in `.env`.

## Verification

```bash
# Backend
pytest
ruff check .
ruff format --check .
mypy traittutor

# Frontend
cd web
npm run lint
npm run build
npm run test:e2e
```

Run the checks that cover the affected scope. Real-model behavior, cost, deployment, and notification paths require separate acceptance. The frontend uses system font stacks, so `npm run build` does not depend on downloading remote fonts.

## Repository layout

```text
traittutor/                 FastAPI backend, generation, gateway, learner model
web/                        Next.js app
config/                     Example runtime configuration
scripts/                    Local operational helpers
docs/assets/readme/         Curated public product screenshots
```

## Product safety boundary

TraitTutor uses profile and memory signals as adjustable teaching context only. It does not:

- diagnose personality, cognition, or ability;
- claim objective learning gains from profile data;
- treat browsing, saving, or courseware viewing as verified mastery;
- expose hidden prompts or private reasoning in user-facing explanations.

Intent classification only controls product routing; it does not execute tools, read memory, or read attachment bodies. Learner material is treated as untrusted data. The safety layer records only minimal audit information and never exposes detection rules or hidden system prompts.

Why Drawer explanations should show the current goal, source evidence, weak concepts, explicit preferences, teaching actions, and degradation state—not private chain-of-thought, raw prompts, or personality judgments.

## Contributing

Describe the affected product entry point, data owner, and verification scope with every change. Changes to the shared composer, learning evidence, storage migrations, or public routes should include the relevant backend tests and browser regression. Never commit real API keys, learner materials, participant records, runtime databases, or unpublished manuscript files.

## License

TraitTutor is licensed under the [Apache License 2.0](LICENSE). Third-party provenance, upstream foundations, and applicable licenses are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
