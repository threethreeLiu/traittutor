# ADR-0009: Unified Assistant execution via CapabilityRegistry

**Date**: 2026-08-12
**Status**: accepted
**Deciders**: TraitTutor product owner, implementation agent

## Context

After retiring the legacy `/api/v1/chat` WebSocket (ADR-independent change,
commit `40ffd4d`), browser chat runs on the single `/api/v1/ws` unified
runtime. Inside that runtime, `TurnRuntimeManager` still branches on
`product_mode`:

- `product_mode in {"learn", "assist"}` → `agent_runtime.run_agent()` (a
  LangGraph that classifies, builds a context snapshot, runs a policy
  preflight, and calls the Gateway directly);
- otherwise → `ChatOrchestrator → CapabilityRegistry`.

The `else` branch is currently unreachable because `product_mode` is pinned
to `learn`/`assist` at `turn_runtime.py:810-812`, so every live Assistant
turn is executed by `agent_runtime`. This leaves two parallel orchestration
models, two deterministic classifiers (`assistant_routing.classify_capability`
and `agent_runtime._classify`), and an execution path that can both produce
the final answer and (in dead code) create a Learning Pack / Plan.

The `agent_runtime` Pack/Plan creation branch is unreachable today
(`AgentRunRequest.learning_confirmed` is never `True` at the call site), but
its mere presence conflicts with the `/home` contract that only accepts
learning materials and never launches a chat-driven Pack creation. Keeping
the branch around also invites a future caller to set
`learning_confirmed=True` and silently reintroduce the violation.

This ADR records the decision to converge Assistant execution to a single
chain. It is a **decision** record, not an implementation log: the
converged code lands in a later, separate PR after this ADR is accepted.

## Decision

Adopt **Plan A: converge Assistant execution into `ChatOrchestrator` and
`CapabilityRegistry`**. Specifically:

1. **Single classification owner.** `assistant_routing.classify_capability`
   (the durable, confirmation-gated HTTP `/api/v1/assistant/route` boundary)
   is the sole owner of capability classification for Assistant.
   `agent_runtime._classify` is removed; if any in-turn routing hint is still
   needed, it becomes a pure DTO derived from the already-recorded
   `CapabilityDecision`, never an independent keyword classifier.

2. **Single execution chain.** All Assistant dialog execution enters
   `ChatOrchestrator → CapabilityRegistry`. `TurnRuntimeManager` no longer
   branches on `product_mode` to choose between `agent_runtime` and
   `ChatOrchestrator`; the `product_mode in {"learn","assist"}` branch and
   its unreachable `else` are both removed.

3. **`agent_runtime` stops owning side effects.** After convergence,
   `agent_runtime` (or whatever survives of it) must not generate the final
   answer, must not create a Learning Pack / Plan, and must not re-run
   classification. Final answers come from the registered capability via the
   Gateway; Pack / Plan creation stays on the `/home` material-upload path.

4. **New homes for the valuable pieces.**
   - **`ContextAssembler` snapshot**: owned by a plain application service
     invoked by `TurnRuntimeManager` while constructing `UnifiedContext`,
     *before* the capability runs. It is no longer assembled inside the
     LangGraph responder.
   - **Policy preflight** (`agent_runtime.policy.preflight`): owned by the
     capability (or the orchestrator) as a deterministic gate that runs
     before the Gateway call, not as a LangGraph node.
   - **`product_action`**: the parallel turn-result channel is retired rather
     than relocated. The canonical `/api/v1/assistant/route` response owns
     confirmation-gated product actions; a dialog turn cannot create a Pack /
     Plan and therefore has no product action to emit through `StreamBus`.
   - **Gateway receipt / usage**: already owned by the Gateway; the
     capability simply uses the Gateway response's `request_id`/`receipt`.

5. **LangGraph removal, conditional.** After steps 1–4 land and no real
   stateful-graph need remains, delete `agent_runtime`'s graph, schema,
   policy, sandbox, and the LangGraph dependency. If LangGraph is retained
   for a genuine multi-state reason, add an architecture test proving its
   responsibility does not overlap `CapabilityRegistry`, and record that
   reason here.

## Alternatives Considered

### Alternative 1: Keep `agent_runtime` as an Assistant-only router (Plan B)

- **Pros**: Smaller change; preserves the LangGraph abstraction for future
  stateful flows.
- **Cons**: Keeps two orchestration layers (`agent_runtime` +
  `CapabilityRegistry`), two classifiers, and a `product_mode` branch.
  LangGraph for a single linear route→respond path has no justified state.
- **Why not**: A single linear path is not a stateful graph; two abstraction
  layers for one path is unjustified complexity and a recurring
  classification-drift hazard.

### Alternative 2: Status quo (do nothing)

- **Pros**: No work.
- **Cons**: Leaves the unreachable `else`, the dead Pack/Plan branch, and two
  classifiers in the tree. The dead code misleads readers and can be
  re-activated by a future caller, reintroducing the `/home` contract
  violation silently.
- **Why not**: The retirement of `/api/v1/chat` makes the duplication
  pointless; convergence is cheaper now than after more code accrues on the
  `agent_runtime` path.

## Consequences

### Positive

- One flow-event, pause/resume, cancellation, tool-authorization, and
  persistence chain for every Assistant turn.
- `CapabilityRegistry` manifest becomes the single capability catalog.
- No `product_mode`-based executor selection; classification has one owner.
- The `/home` contract (no chat-driven Pack creation) is enforced by absence
  of the creation path, not by an unreachable branch.

### Negative

- One-time migration cost: relocate snapshot assembly and policy preflight,
  and remove the unreachable `product_action` return channel without changing
  the observable turn protocol.
- Any caller that depended on `agent_runtime` internals (today: only tests)
  must move to the capability/orchestrator surface.

### Risks

- Re-introducing classification drift if the in-turn hint (step 1) is allowed
  to grow back into a second classifier. Mitigation: keep it a pure DTO
  derived from `CapabilityDecision`, with a test asserting no keyword
  classification in the turn path.
- Snapshot/policy relocation could subtly change prompt construction.
  Mitigation: freeze the migrated source wrapper, policy and snapshot-reference
  fragments byte-for-byte. Whole-system-prompt equality is intentionally not
  claimed because `AgenticChatPipeline` owns a different canonical system
  prompt and tool manifest from the retired direct-Gateway responder.
- Deleting `agent_runtime` could remove a hidden consumer together with its
  dead Pack/Plan branch and unused sandbox. Mitigation: confirm zero live
  consumers via static + dynamic scan, not just test deletion.

## Acceptance

Accepted on 2026-08-12 by the TraitTutor product owner. The F4 implementation
assembles an owner-authorized `ContextAssembler` snapshot in
`TurnRuntimeManager`, runs deterministic policy preflight in `ChatCapability`
before its pipeline, and removes the unreachable dialog `product_action`
channel. The exact migrated prompt fragments are protected by contract tests;
the complete old and new system prompts are not represented as equivalent.
