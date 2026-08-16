# Research Workspace

Owner-bound, durable product state for deep-research workspaces. It makes a
versioned research brief, run lifecycle, evidence, notes, reports and task
receipts the source of truth; the existing chat-oriented research pipeline is
only an executor adapter.

## Guarantees

- Every read and mutation is scoped to the authenticated owner.
- Brief versions are frozen once a run is created.
- Runs use idempotency keys, leases and fencing epochs so a late worker result
  cannot revive a paused, cancelled, replaced or completed run.
- Each externally grounded claim references a stored clickable source. Model
  inference is explicitly labelled and never represented as retrieved fact.
- State and receipts are persisted before progress is published.
- The canonical Gateway stream buffers text until a terminal `final` event,
  then applies JSON, source and URL validation before one fenced commit.
  Gateway reasoning, usage and receipts are never workspace state;
  cancellation, tool calls, provider errors and incomplete streams become a
  durable `executor_failed` receipt without invoking another protocol.
- A completed, fully grounded report can enter the normal courseware queue
  through `POST /research/workspaces/{workspace_id}/runs/{run_id}/courseware`.
  The queue request freezes revisioned `research_run_id`, report and source
  references, then revalidates them both before and after provider work;
  invalidated or review-required evidence fails closed.

## Non-goals

- This module does not call a model provider directly; worker adapters use the
  existing Gateway.
- `DynamicTopicQueue` and chat history are not product truth sources.
- This first slice uses REST state recovery; streaming is an optional view of
  persisted state rather than a second state channel.

## Layout

- `models.py` — public domain records and safe data contracts.
- `state_machine.py` — run transition table and terminal-state rules.
- `store.py` — file-locked, atomic owner-bound persistence.
- `service.py` — owner-safe product operations.
- `executor.py` / `worker.py` — Gateway-backed adapter and recovery boundary.
- `source_validation.py` — source and claim provenance gate.
- `courseware.py` — fail-closed Report → generation-queue evidence adapter.

## Research provenance delivery

The Research Workspace route is the only public entry that can construct a
research provenance field. The durable queue carries a frozen, typed reference
containing only workspace/run/report IDs and revisions, report-body digest, and
source IDs/revisions. Generation revalidates that evidence before and after
provider work, freezes the same reference into `ContextAssembler` and
`CoursewarePromptBundle`, and includes it in their replay hashes. Report prose,
claim text, source titles/URLs, prompts, credentials, and provider telemetry
never enter those reference contracts or the learner-facing queue receipt.

See [DESIGN.md](DESIGN.md) and [ADR-0007](../../docs/adr/0007-research-workspace-truth-source.md).
