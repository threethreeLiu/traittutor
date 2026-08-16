# Design: assistant capability routing

The routing ledger is a decision boundary, not a second conversation or
orchestrator. It does not own turns, Memory, BKT, `LearnerEvent`, research
runs, learning packs, or generation tasks.

```text
safe request -> chat: completed
             -> search: completed | failed
             -> research: confirmation_required -> completed (Workspace/Brief/Run)
             -> learn: confirmation_required -> completed (Pack/Plan)
             -> create: confirmation_required -> completed (generation task)
unsafe request -> no record and no side effect
```

The confirmed input is re-scanned before destination writes. Research and
Learn preserve the submitted question/goal in their owner-bound destination;
Create preserves goal/material in the generation task. The private decision
stores only a digest plus public resource IDs. Owner-scoped idempotency makes
replays return the same destination and rejects altered input. Creating a Pack
or plan emits no answer event and cannot update BKT.
