# TraitTutor v1.4.0-beta Release Notes

**Release Date:** 2026.05.21

v1.4.0-beta is the largest release since the agent-native rewrite. It folds an
end-to-end **Auto Mode** on top of the existing capabilities, ships a
**three-layer memory subsystem (L1/L2/L3)** with a dedicated workbench, rebuilds
**Deep Research / Deep Solve / Question** on the same agentic engine as Chat,
re-architects the **chat capability + LlamaIndex RAG pipeline** around a
session-cumulative source inventory, unifies the **Capabilities infrastructure
and i18n**, merges the Animator menu into **Visualize**, and reorganises
**Settings, environment, and the local launcher**. Several new chat tools
(`ask_user`, `web_fetch`, `write_note`, `list_notebook`, `github_query`) plus a
delete-chat-turn flow, quiz follow-up chat, and a GeoGebra viewer round out the
release.

## Highlights

### Auto Mode — Agentic Capability Router
A new `auto` capability sits on top of the existing modes and chooses the right
one for each request, instead of forcing the user to pick a mode up front.

- **Three-stage agent loop** — `ANALYZING` (single LLM call, streamed as
  thinking) → `DELEGATING` (up to `max_iterations` of router calls that emit
  `delegate_to_<cap>` tool calls or atomic tool calls) → `SYNTHESIZING` (final
  inline answer, either passed through from the loop or assembled by a closing
  LLM call).
- **Routes to real capabilities** — `deep_solve`, `deep_question`,
  `deep_research`, `math_animator`, `visualize`, plus the chat-level atomic
  tools (`web_search`, `web_fetch`, `rag`, …) live behind the same router so
  the LLM can mix retrieval and full sub-capability runs in one turn.
- **Bounded retries and quotas** — independent retry budgets for router-LLM
  errors, per-delegation failures, and arg-validation feedback; a configurable
  `max_same_capability_calls` quota keeps the loop from spinning on one mode.
- **Clean conversation history** — sub-capability events flow through a
  `forward_events` shim that tags every content event with a `call_id`, so the
  conversation turn-runtime filter keeps only Auto's own final synthesis in
  saved history. Sub-runs are still streamed live to the UI.
- **`answer_now` fast-path** — when the user asks to "answer now" the pipeline
  skips analysis + delegation and produces an immediate inline reply.

### Three-Layer Memory Subsystem (Memory v2)
The previous flat memory page is replaced by a structured three-layer store
with an explicit consolidation pipeline and a dedicated workbench.

- **L1 / L2 / L3 layout** — L1 captures raw run traces, L2 holds normalised
  document records, L3 holds curated slots per surface (chat, notebook, book,
  TutorBot). Per-user paths flow through `PathService` so multi-user
  deployments stay isolated.
- **Consolidator pipeline** — modular `consolidator/` modules (chunker, guards,
  parse, references, runs, modes, line-doc, meta) turn run traces into
  versioned line-oriented documents with stable ids, references between
  layers, and a snapshot history.
- **Memory Workbench UI** — new `/memory` routes (`graph`, `l1`, `l2`, `l3`,
  `resolve`) ship as standalone pages with workbench, hub, graph viewer, run
  panel, and an archived-state banner. A reusable `MemorySection` component is
  embedded where the legacy memory panel used to live.
- **First-class chat tools** — `read_memory` and `write_memory` are exposed
  as agent tools (with i18n hints) so chat / Auto can recall and update memory
  inside a turn instead of needing a separate save step.
- **Settings integration** — Memory now has its own page under
  `/settings/memory` with run controls, mode toggles, and storage status.

### Deep Research, Deep Solve, and Question on the Agentic Engine
The three multi-agent pipelines have been rewritten as orchestrators on top of
the shared agentic-engine primitives, deleting hundreds of bespoke prompt
files and per-agent classes.

- **Deep Research → `agents/research/pipeline.py`** — four phases (`Rephrase`,
  `Decompose`, `Research blocks`, `Reporting`) implemented as labeled steps
  (`THINK` / `TOOL` / `APPEND` / `OUTLINE` / `SECTION` / `FINISH`). The dynamic
  topic queue and `CitationManager` are preserved; the new `APPEND` label lets
  research blocks add follow-up topics to the queue without leaving the loop.
  `ask_user` v2 drives up to three rephrase rounds with multi-question cards.
- **Deep Solve → `agents/solve/pipeline.py`** — `Pre-retrieve` (KB-only),
  `Plan`, `Solve` (per-step `THINK` / `TOOL` / `FINISH` / `REPLAN` loop with a
  back-edge from solve to plan), and a final `Synthesize` step. Each step's
  `FINISH` flows into the next step's prompt context so the answer reads as
  one continuous narrative.
- **Question / Quiz** — coordinator + pipeline replace the old `generator` /
  `idea_agent` / `models` modules; the old prompt directories have been
  removed entirely.
- **All three drop the legacy `agents/` and `prompts/` directories** for their
  respective modes, leaving one pipeline file and shared labeled-step prompts.

### Chat Capability & LlamaIndex RAG Refactor
The agentic chat pipeline has been rebuilt around a session-cumulative
"Attached Sources" manifest and a cleaner LlamaIndex pipeline.

- **Branch-isolated source inventory** — `services/session/source_inventory.py`
  materialises every source attached on the active branch's ancestor chain.
  Fresh sources from the current turn show a full preview; historical sources
  show a one-line row with id, name, kind, size, and the turn ordinal where
  they first appeared. The LLM calls `read_source(id)` to expand the full
  text on demand. Sibling branches never leak sources into each other.
- **LlamaIndex pipeline split-out** — dedicated `config.py`, `ingestion.py`,
  `retrievers.py`, and `document_loader.py` replace the previous monolithic
  pipeline module. Storage stays backward-compatible with v1.3 versioned
  indexes.
- **Lean agentic chat prompt** — `agentic_chat.yaml` (EN/ZH) was rewritten to
  match the new tool surface and the source-inventory contract; the old
  parallel-tool prompt scaffolding is gone.
- **Builtin tools registry** — `tools/builtin/__init__.py` is the single place
  where chat-mounted tools, hint prompts, and arg-augmentation wrappers are
  registered.

### Capabilities Infrastructure Unification
Every capability now goes through one shared envelope, one status-i18n loader,
and one cost-tracking surface.

- **`emit_capability_result` helper** — every capability emits its final
  result through one helper that fills the result envelope (label, summary,
  payload, render hints) and the trailing usage-tracker totals consistently.
- **`StatusI18n`** — capability status copy lives in
  `capabilities/prompts/{en,zh}/<name>.yaml` and is loaded via a shared
  `StatusI18n` accessor. Hard-coded English status strings have been removed
  from the pipelines.
- **`UsageTracker` cost surface** — token usage and cost are tracked through
  one tracker per capability run, exposed to the result envelope, and shown
  on the new `/settings/capabilities` admin page (live list, defaults,
  per-capability override toggles).
- **Deprecated `main.yaml` keys removed** — the legacy `main.yaml` capability
  copy has been deleted in favor of per-capability prompt files.

### Visualize: Animator Folded Into One Capability
The standalone Animator menu has been merged into Visualize so the user picks a
visualization once and the system chooses the renderer.

- **`render_type` discriminator** — `AnalysisAgent` picks one of six render
  types — `svg`, `chartjs`, `mermaid`, `html` (text-emitting, three-stage
  pipeline) or `manim_video` / `manim_image` (Manim subprocess pipeline). The
  result envelope carries `render_type` so the frontend delegates to the
  right viewer.
- **Single sidebar entry** — the old `Animator` menu entry is gone; users now
  go through `Visualize` for both static charts and Manim videos. The
  fullscreen viewer / config panel handle all render types.

### New Chat Tools
- **`ask_user`** — packages 1–3 structured questions into a single payload that
  pauses the same turn until the user answers. The frontend renders a card
  letting the user navigate questions and submit answers in one batch; the
  pipeline resumes the turn with the answers wired back as the tool result.
  Used by Deep Research's Rephrase phase and available to chat / Auto.
- **`web_fetch`** — URL fetch with readable-content extraction, strict scheme
  / private-IP / size guards (applied both pre-flight and post-redirect),
  and `…[truncated]` markers when output exceeds the cap.
- **`write_note`** — replaces the old `save_to_notebook` tool. Two modes:
  `append` creates a new record (default body is the rendered transcript,
  optional agent-authored body) and `edit` updates an existing record by
  `record_id`.
- **`list_notebook`** — read-only index / drill-down listing of the active
  user's notebooks and records. Only mounted when the user actually has
  notebooks, so empty runs are impossible by construction.
- **`github_query`** — read-only `gh` CLI wrapper covering `pr`, `issue`,
  `run`, `repo`, and a GET-only `api` fallback. No mutation verbs are
  reachable through the tool surface. Returns a clean "tool unavailable"
  outcome when `gh` is not installed.

### Chat Surface Features
- **Delete chat turn** (#443) — message items now carry a stable `id`, the
  session API exposes `deleteMessage`, the chat reducer adds a `DELETE_TURN`
  action, and a 409 vs 404 check rejects deletion of a still-running turn.
  Optimistic temp ids are resolved before deletion to avoid orphaned UI rows.
- **Quiz follow-up chat composer** — `FollowupChatComposer` and
  `QuizFollowupContext` let the user start a chat thread directly from a quiz
  question. The composer reuses the main `ChatComposer` (look, @space
  pickers, KB picker, attachments, LLM selector) but routes sends through a
  dedicated follow-up controller. Companion `quiz-judge.ts` helper supports
  judging follow-up answers inline.
- **Quiz UI polish** — quiz answer textarea is vertically resizable (#478);
  question content normalises single newlines to Markdown paragraphs (#441).
- **GeoGebra viewer** — `Geogebra.tsx`, `GeogebraOpenCTA.tsx`, and
  `GeogebraTabContext` add a GeoGebra applet renderer (loaded via the
  official GGB applet script) so geometry / algebra snippets can be opened
  inline alongside chat answers.

### Multi-User Data Isolation
Several regressions and gaps from the v1.3.x multi-user introduction were
fixed in a focused pass (#474, #465).

- **Auth decoupled from middleware** — multi-user identity resolution no
  longer relies on global middleware state, fixing rebase regressions that
  caused cross-user data bleed under specific routing orders.
- **Legacy session manager path capture** — the older session manager
  inherited the active user scope correctly, so its file paths land inside
  the per-user workspace instead of the shared default.
- **Frontend uses `apiFetch` everywhere** — every authenticated client call
  now goes through `apiFetch()` so the auth header is attached consistently.
- **SSL bypass sweep** — `DISABLE_SSL_VERIFY` now reaches the codex provider
  and four embedding adapters that were still missing it after v1.3.10.

### Environment Settings, Installer, and Local Launcher
The install + launch story has been rewritten to remove the `.env` parsing
maze and make `traittutor start` / `traittutor init` first-class.

- **`runtime_settings.py`** — system / auth / launch settings now live in
  one typed module with explicit defaults (`backend_port`, `frontend_port`,
  `cors_origins`, `disable_ssl_verify`, `chat_attachment_dir`, …) and JSON
  storage under `data/user/settings/`. The 280+ line legacy `env_store.py`
  and the two `.env.example` files have been deleted.
- **`runtime/launcher.py`** — single async launcher that owns the
  backend + frontend lifecycle, port discovery, readiness probes, and
  cleanup. Generates `web/.env.local` so the Next.js frontend always picks
  up the resolved backend port.
- **`traittutor/runtime/banner.py`** — localized startup banner shared
  between `traittutor start` and `traittutor init`; reads the language
  preference from interface settings so the banner matches the UI locale.
- **`init_wizard.py`** — interactive `traittutor init` wizard with provider
  menu, env-var auto-detect for API keys, live `GET {base_url}/models`
  fetch, curated fallback list, and an optional connectivity probe before
  save.
- **`model_catalog.py` trimmed** — the catalog file shrank by ~400 lines as
  per-provider boilerplate moved into `provider_registry` and adapter
  modules.

### Settings UI Reorganization
The single `/settings` page has been split into focused tabs.

- **New routes** — `/settings/appearance`, `/settings/capabilities`,
  `/settings/embedding`, `/settings/llm`, `/settings/mcp`,
  `/settings/memory`, `/settings/search`, `/settings/status`,
  `/settings/tools`, with a shared layout and items index.
- **Tools page** — lists every chat-mountable tool, surfaces availability
  (e.g. `gh` for `github_query`), and exposes per-tool toggles.
- **Capabilities page** — pairs the new `UsageTracker` cost surface with
  per-capability defaults and override toggles described above.

### Zulip Channel Integration
The TutorBot Zulip channel (added in v1.3.9) gets a follow-up sweep of fixes
and a self-subscribe feature (#480).

- **Auto-subscribe channels for @mentions** — Bot can subscribe itself to
  any channel where it gets @mentioned so it actually receives the message
  in topics. Subscribed-channel warnings are downgraded to info-level so
  startup logs stop misleadingly flagging the success path.
- **All mention flag types supported** — `mentioned`, `wildcard_mentioned`,
  `topic_wildcard_mentioned`, and `stream_wildcard_mentioned` all trigger
  the bot, fixing channel-`@`-mention silence.
- **Attachment send fixes** — re-sent attachments no longer treat the Zulip
  upload path as a local file, the upload helper no longer crashes on
  `'str' object has no attribute 'name'`, and missing routing metadata is
  rebuilt from `_recipient_map` so `Message must have recipients` errors
  are eliminated.
- **Progress message dedup** — internal `_tool_hint` progress events are
  filtered out of channel sends so the user no longer sees duplicate "tool
  starting…" lines.
- **Test coverage** — new unit tests for attachment upload + send recovery
  and channel-subscription behavior.

## Tests

- New tests for the Auto pipeline, delegation, schemas, and the
  `auto` capability surface — 1100+ lines of new coverage including
  end-to-end agent-loop behavior.
- Full test coverage for the new memory subsystem — chunker, consolidator,
  document, ids, line-doc, merge, meta settings, modes, ops, references,
  runs, store.
- Per-tool unit tests for `ask_user`, `github_query`, `list_notebook`,
  `web_fetch`, and `write_note`, plus ask-user UI state helpers.
- Refit chat / research / solve / question pipeline tests against the
  agentic-engine labels (`THINK` / `TOOL` / `APPEND` / `FINISH` / …).
- New session / source-inventory tests covering branch isolation and
  cumulative manifest behavior.
- Frontend tests cover the message-branches helper, version surface, and
  ask-user state machine.

## Upgrade Notes

- **Settings file relocation** — first launch will migrate any
  `.env`-based settings into the new JSON files under
  `data/user/settings/`. The legacy `env_store` shim is gone; if you
  scripted `.env` writes externally, point them at
  `runtime_settings.py` or the `/settings` API instead.
- **`traittutor start` is the recommended launcher** — `start_web.py` /
  `start_tour.py` continue to work but are now thin wrappers around the
  new `runtime/launcher.py`. Run `traittutor init` once to seed providers
  and credentials on a fresh machine.
- **Animator menu users** — point at **Visualize** instead. The
  capability now picks Manim automatically when the user asks for a
  video / animation; existing Manim-rendered records are unaffected.
- **Memory data migration** — the legacy single-blob memory format is
  read by the consolidator on first access and written back as L2 / L3
  records. No manual step is required; old snapshots remain on disk.
- **Capability authors** — emit results via
  `capabilities/_shared.emit_capability_result` and put status copy in
  `capabilities/prompts/{en,zh}/<name>.yaml`. Hard-coded English status
  strings will fail review.
- **Beta scope** — this release ships substantial new surfaces (Auto,
  Memory v2, settings split). Pin to `v1.4.0-beta` for production until
  the GA cut; bug reports against any of the new modules are welcome.

**Full Changelog**: https://github.com/<your-org>/TraitTutor/compare/v1.3.10...v1.4.0-beta
