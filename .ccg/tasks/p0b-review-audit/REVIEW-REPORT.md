# P0-B Review-Audit — Phase 3 Synthesis (ws-1b)

**Scope:** `ws-1b` diff `main..HEAD` (64 files, +6065/−307).  
**Method:** dual-model cross-validation — Codex (backend + 12 invariants + deferred
re-judgment) ∥ second-model frontend (Antigravity CLI unavailable → degraded to
`ecc:react-reviewer`, still an independent model vs Codex).  
**Date:** 2026-08-09.

## Verdict

| Dimension | Reviewer | Raw | After fixes |
|---|---|---|---|
| Frontend (WS-9B render + CSP) | react-reviewer | 5 W + 5 I | **PASS** — all actionable fixed (`48619a9`, `069f675`) |
| Backend (invariants, live wiring) | Codex | **FAIL 36/100** | flag-ON paths **not enable-ready** |

### The two questions this review answers
- **(a) Is `ws-1b` mergeable to `main` with flags OFF?** → **Yes.** All four
  reversible flags default OFF; the flag-OFF demo path is byte-unchanged and the
  defects below are dormant. Merge is demo-safe.
- **(b) Are the flags enable-ready?** → **No.** Codex found 11 Critical +
  6 Warning invariant-breaking defects on the flag-ON paths. They must be
  resolved before any flag is flipped ON.

## Backend findings — verified & triaged

Legend: ✅=Claude fixed (`e07a6c7`) · 🔧=Codex implementing (dispatch
`019fe5e0…`) · 📐=ADR-gated (design proposal only) · CONFIRMED=I read the cited
code this pass · PLAUSIBLE=Codex-cited, consistent, Codex verifies on implement.

| ID | file:line | invariant | verdict | triage |
|---|---|---|---|---|
| **C1** | `generate/service.py:842` — courseware calls `generate_courseware` directly; **no live caller** of `CoursewareOrchestrator`/`OrchestratorRunStore`/`build_executor_map`; executor map incomplete. WS-6B not connected. | #4 | CONFIRMED (grep: 0 refs outside orchestration/+tests) | 🔧 Codex **P1** |
| **C2** | `traittutor_generate.py:68` — PageSchema published for `needs_review`; retry reuses gen id → cached pre-retry page. | #8 #11 | CONFIRMED (read router) | ✅ Claude |
| **C3** | `traittutor_generate.py:284` — grades `needs_review`, no `LearnerEvent`, no `attempt_id`. | #1 #2 #4 #8 | CONFIRMED (read router) | ✅ Claude (gate; chain-routing → WS-10B) |
| **C4** | `event_chain.py:42` — identity from answer **content**; later identical answer suppressed forever; changed answer re-scores. | #4 | CONFIRMED (read `stable_answer_identity`) | 🔧 Codex P2 |
| **C5** | `learning/service.py:253` — `subject_id … or module_id or book_id`; `m1`-style ids merge subjects; strong-evidence allows empty subject. | #2 #6 | CONFIRMED (read call) | 🔧 Codex P2 |
| **C6** | `learning/service.py:337` — canonical projection updates counts/intervals only; `policy.py:68,76` gates from legacy `mastery_levels` → 0% display. | #3 | CONFIRMED (read projection) | 📐 ADR (Codex proposal) |
| **C7** | `learning_model/events.py:243` + `personalization/service.py:282` — derived callbacks process-local locks only; two workers can both apply same event. | #4 | PLAUSIBLE (consistent w/ `asyncio.Lock` + bg-task set seen) | 📐 ADR (Codex proposal) |
| **C8** | `generate/service.py:647` — `user_authorized=True` hardcoded; flag-on = implicit consent. | #7 #12 | CONFIRMED (read call) | 🔧 Codex P3 |
| **C9** | `orchestration/prompt_bundle.py:46` — idempotency hash omits `context_snapshot_hash` + version; stale/billable replay; cross-gen-id leak. | #4 #11 #12 | PLAUSIBLE | 🔧 Codex P4 |
| **C10** | `memory/store.py:578` — `scope=None` retrieval returns all partitions; access records assert `user_authorized=True` from snapshot-id existence. | #7 | PLAUSIBLE | 🔧 Codex P3 |
| **C11** | `orchestration/evaluator.py:33` — keyword external-claim detection; non-clickable `concept_refs` accepted. | #7 | PLAUSIBLE | 🔧 Codex P4 |
| **W1** | `page_store.py:70` — `save()` doesn't re-validate whitelist. | #8 | CONFIRMED (read save) | ✅ Claude |
| **W2** | `courseware_orchestrator.py:171` — planning ignores `requested_component_types`. | — | PLAUSIBLE | 🔧 Codex P4 |
| **W3** | `memory/store.py:277` — activation + status separate txns; `confirmed`/`evidence_count` trusted. | #7 | PLAUSIBLE | 🔧 Codex P3 |
| **W4** | `memory/store.py:85` — dup candidate/memory/access ids not rejected. | #4 | PLAUSIBLE (distinct from page_store's existing dup guard) | 🔧 Codex P3 |
| **W5** | `conversation/store.py:303` — episode branch/order/reference integrity unvalidated. | — | PLAUSIBLE | 🔧 Codex P5 |
| **W6** | `conversation/__init__.py:42` + `models.py:161` — blank-line-at-EOF (`git diff --check`). | — | CONFIRMED | ✅ Claude |

**Deferred re-judgment (prior `/code-review`):** D2/D4/D6/D7/D10 escalated
**fix-now** (folded into C2–C8 above); D8 (forward-cascade privacy-forget)
**keep-deferred** — document/rename, `cascade=False` exists.

## What Claude fixed (`e07a6c7` + `069f675`)
- **C2** `_task_result_with_page_schema` now requires `released=True`; only
  `completed` artifacts publish a frozen PageSchema; retry can't mask a
  regeneration. (#8/#11)
- **C3** `/tasks/{id}/quiz/grade` grades only `completed` (an unreleased quiz's
  explanation may begin with the answer). (#5/#8)
- **W1** `PageStore.save` re-validates the whitelist at the write boundary. (#8)
- **W6** trailing-blank-at-EOF stripped. `git diff --check` clean.
- ruff clean; 53 targeted tests pass (router / page-store / projection /
  joint-E2E / conversation / components / generate).

## Open / pending
- 🔧 **Codex dispatch** (`019fe5e0…`) implementing C1/C4/C5/C8/C9/C10/C11 + W2–W5.
  Claude will review + commit grouped by feature when it returns.
- 📐 **C6/C7 ADRs** — Codex to produce design proposals only (no code); both are
  convergence items requiring a decision before implementation
  (CODING-PLAN §5).
- **User decisions (still owned by user):** merge `ws-1b`→`main` (flags OFF,
  demo-safe); when to flip demo flags; start WS-7/8/11/12/13 convergence (each
  needs an ADR first).
