# Assistant capability routing

This module owns owner-bound routing receipts for `/assist`. It selects
`chat`, `search`, `research`, `learn`, or `create`; it never writes Memory,
BKT, or learner evidence.

`POST /api/v1/assistant/route` scans input before opening the decision store.
`research`, `learn`, and `create` require confirmation. Confirmation re-submits
the capability-specific input that the destination owns:

- Research: question and workspace title.
- Learn: the visible `learning_goal`; one Pack and component plan are created,
  and the public receipt returns their IDs.
- Create: generation goal and one typed material reference.

These inputs are scanned again before any product write. The routing ledger
stores hashes and public resource IDs; the destination stores the actual
content. Replays return the same IDs and altered confirmed input fails closed.
Persistence uses the canonical owner-scoped SQLite store.
