# Research Workspace design

## Purpose and boundary

This module implements ADR-0007. Product truth is a durable, owner-bound
workspace ledger containing `ResearchWorkspace`, immutable `ResearchBrief`
versions, `ResearchRun`, `ResearchTaskReceipt`, `ResearchSource`,
`ResearchNote`, `ResearchClaim` and `ResearchReportArtifact`. Existing
`agents.research.ResearchPipeline` may execute a claimed run but never owns its
lifecycle or persistence.

It does not introduce a direct provider client, alter chat history, or use
stream events as durable state.

The Gateway-backed adapter buffers only text in process and requires Gateway's
terminal `final` event before parsing and applying the normal validation and
fenced commit. Reasoning, usage and receipts are discarded; tool calls,
cancellation, provider errors and missing finals become ordinary executor
failures. The worker does not claim to interrupt its running thread and never
invokes another protocol after a stream attempt.

## Data flow

```text
authenticated owner -> service -> locked workspace store
                                  | persist run / receipt / source / report
                                  v
                            Gateway-backed worker
                                  | claim token + fencing epoch
                                  v
                           validate late result -> store
                                  |
                                  v
                         REST state / optional progress view
```

The worker receives only the frozen brief, allowed sources and a claim. Before
every write it must prove that owner, workspace, run, input hash, claim token
and fencing epoch still match a writable current run. A mismatch is recorded as
an audit receipt and the result is discarded.

## State machine

Runs transition only through:

```text
draft -> queued -> running -> completed | failed | needs_review
                    |          |
                    v          v
                pausing -> paused <- resume -> queued
                cancelling -> cancelled
```

`completed`, `failed` and `cancelled` are terminal for task-result writes. A
pause, cancel, retry or stale-lease recovery advances the fencing epoch, making
older workers unable to mutate the run.

## Evidence-safe courseware hand-off

`courseware.py` may prepare a courseware request only when the current run is
`completed`, the current report is `active`, and **every** report claim is an
active grounded claim with active source records. It freezes a revisioned
workspace/run/report identity, report-body digest and source id/revision pairs
into a server-only standard-generation field. The task derives its owner from
the authenticated queue, uses a deterministic replay id, and revalidates the
same field before a provider call and again before publication. Thus a source
invalidation while queued or executing causes a safe task failure rather than
a newly published artifact.

Generation composition copies only this typed identity reference into the
internal `ContextAssembler` read range and `CoursewarePromptBundle`; both
snapshot and bundle hashes include it. Source URLs/titles, claim text, report
body, prompts, credentials and provider telemetry remain outside these frozen
contracts. The generic generation API rejects provenance-shaped browser input,
so Research Workspace is the only public constructor.

## Persistence and concurrency

The store follows existing workspace JSON conventions: exclusive file lock,
read/validate/mutate under the lock, JSON flush + `fsync`, then atomic replace.
Mutations use revision CAS where users edit workspace/brief state. Run creation
deduplicates `(owner_id, workspace_id, idempotency_key, input_hash)`. Task
receipts use `(run_id, task_id, input_hash)`.

Lease fencing provides at-least-once work delivery, not exactly-once external
execution; executor side effects must therefore use the task receipt/event ID
as their own idempotency key.

## Evidence and privacy

`ResearchClaim` is accepted only if it is explicitly an inference or references
one or more stored sources with an HTTP(S) URL. Public DTOs are explicit
allowlists and never expose provider credentials, system prompts, raw prompts,
or hidden agent reasoning. Owner checks happen inside the store/service so a
router cannot accidentally broaden a query.

## Test obligations

- owner isolation reports another owner's object as absent;
- invalid transitions and stale CAS are rejected;
- duplicate run/task requests return the original durable record;
- expired lease recovery fences the prior token;
- cancel/pause prevent late result resurrection;
- every non-inference claim has a clickable recorded source.
