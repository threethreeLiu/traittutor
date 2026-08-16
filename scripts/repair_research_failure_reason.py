#!/usr/bin/env python3
"""One-off backfill: drop stale ``failure_reason`` on non-failed research runs.

A run's ``failure_reason`` is only meaningful while the run is actually
``failed`` (or, defensively, ``needs_review`` is debatable but the executor
never sets it there).  Before the lifecycle reset fix, successful / paused /
cancelled runs derived from a failed run inherited the ``executor_failed``
string via an append-only ``model_copy`` that omitted the field, so the UI
rendered a green "completed" badge next to a red ``executor_failed`` line.

This script walks every authenticated owner (via ``active_owner_contexts``,
never a raw directory scan), resolves the latest revision of each run, and
appends a corrective revision that clears ``failure_reason`` for any run whose
status is no longer failed-ish.  The append keeps the audit ledger intact and
mirrors exactly what the now-fixed lifecycle transitions do.

Default mode is dry-run; pass ``--execute`` to actually write corrective
revisions.  A JSON report is always written.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
from typing import Any, Iterator

# Allow running as a plain script without an editable install: the script lives
# in scripts/, so the project root is the parent directory.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import local_admin_user, user_context
from traittutor.operations.owners import active_owner_contexts
from traittutor.research_workspace.models import ResearchRun, ResearchRunStatus
from traittutor.research_workspace.store import ResearchWorkspaceStore
from traittutor.services.path_service import PathService

# A failure reason is meaningful only on a genuinely failed run.  ``needs_review``
# is intentionally excluded from clearing here: it is a successful-but-uncertain
# terminal state whose own semantics are unrelated to an executor failure, yet a
# run that was reviewed-and-retried will already have been cleared on its
# subsequent ``queued`` transition.  Anything in this set keeps its reason.
_FAILURE_STATES: frozenset[ResearchRunStatus] = frozenset({"failed"})


@dataclass
class OwnerReport:
    owner_id: str
    runs_scanned: int = 0
    runs_repaired: int = 0
    repaired: list[dict[str, Any]] = field(default_factory=list)
    skipped_already_clean: int = 0
    error: str | None = None


@dataclass
class RepairReport:
    dry_run: bool
    owners_scanned: int = 0
    runs_scanned: int = 0
    runs_repaired: int = 0
    owners: list[OwnerReport] = field(default_factory=list)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Clear stale failure_reason on non-failed research runs across all owners."),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually append corrective revisions.  Without this flag the script "
            "only reports what it would change."
        ),
    )
    parser.add_argument(
        "--owner-id",
        default=None,
        help=(
            "Restrict the repair to a single owner id.  By default every "
            "authenticated owner is processed."
        ),
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help=(
            "Isolate the repair to the local-admin owner under this workspace "
            "root directory (resolved via PathService, like the storage_cleanup "
            "script).  Intended for tests and single-scope operators; when "
            "omitted, every authenticated owner is processed against its own "
            "per-scope database."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/research_failure_reason_repair.json"),
        help="JSON file to write the repair report.",
    )
    return parser


@contextmanager
def _scoped_store(
    owner: CurrentUser, *, path_service: PathService | None = None
) -> Iterator[ResearchWorkspaceStore]:
    """Construct the owner-bound store inside its user context.

    ``user_context`` sets the current-user contextvar that ``get_path_service``
    resolves to the owner's per-scope database, exactly like the production
    research daemon does.  When ``path_service`` is supplied (tests / single-
    scope operators via ``--workspace-root``) the store is bound directly to it
    and no user context is entered.
    """

    if path_service is not None:
        legacy = path_service.get_workspace_dir() / "traittutor" / "research_workspaces.json"
        yield ResearchWorkspaceStore(owner.id, path=legacy)
        return
    with user_context(owner):
        yield ResearchWorkspaceStore(owner.id)


def _repair_owner(
    owner: CurrentUser,
    *,
    execute: bool,
    path_service: PathService | None = None,
) -> OwnerReport:
    """Repair one owner's runs.

    ``path_service`` isolates the store to a single workspace root (tests and
    single-scope operators).  When omitted the store is resolved through the
    owner's user context, exactly like the production research daemon.
    """

    report = OwnerReport(owner_id=owner.id)
    try:
        with _scoped_store(owner, path_service=path_service) as store:
            with store._locked() as payload:  # noqa: SLF001 - same-package backfill
                all_runs = store._models(payload, "runs", ResearchRun)  # noqa: SLF001
                # Index every run_id -> latest revision the same way the store
                # does on read, so we only ever append on top of the live state.
                latest_by_id: dict[str, ResearchRun] = {}
                for record in all_runs:
                    current = latest_by_id.get(record.run_id)
                    if current is None or (
                        record.revision,
                        record.lease_revision,
                    ) > (
                        current.revision,
                        current.lease_revision,
                    ):
                        latest_by_id[record.run_id] = record

                report.runs_scanned = len(latest_by_id)
                for run in latest_by_id.values():
                    if run.failure_reason is None:
                        report.skipped_already_clean += 1
                        continue
                    if run.status in _FAILURE_STATES:
                        # A genuinely failed run keeps its reason.
                        continue
                    repaired_record = _append_clearing_revision(payload, run)
                    report.runs_repaired += 1
                    report.repaired.append(repaired_record)
                if execute and report.runs_repaired:
                    store._adapter.replace_all(payload)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - report the owner and continue
        report.error = f"{type(exc).__name__}: {exc}"
    return report


def _append_clearing_revision(payload: dict[str, Any], run: ResearchRun) -> dict[str, Any]:
    """Append a new revision identical to ``run`` but with reason cleared.

    Mirrors the now-fixed lifecycle transitions: a partial copy that bumps the
    public revision and resets only ``failure_reason``.  No idempotency key is
    needed because the mutation is a pure projection of current state and the
    resolver selects the max revision deterministically.
    """

    from traittutor.research_workspace.store import _now  # noqa: SLF001

    cleared = run.model_copy(
        update={
            "revision": run.revision + 1,
            "failure_reason": None,
            "updated_at": _now(),
        }
    )
    payload["runs"].append(cleared.model_dump(mode="json"))
    return {
        "run_id": cleared.run_id,
        "workspace_id": cleared.workspace_id,
        "status": cleared.status,
        "previous_revision": run.revision,
        "new_revision": cleared.revision,
        "cleared_reason": run.failure_reason,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    path_service: PathService | None = None
    if args.workspace_root is not None:
        path_service = PathService(workspace_root=args.workspace_root)
        owners: tuple[CurrentUser, ...] = (local_admin_user(),)
    else:
        owners = active_owner_contexts()
    if args.owner_id is not None:
        wanted = args.owner_id.strip()
        owners = tuple(owner for owner in owners if owner.id == wanted)
        if not owners:
            print(f"no active owner matched --owner-id {wanted!r}")
            return 2

    report = RepairReport(dry_run=not args.execute)
    for owner in owners:
        owner_report = _repair_owner(owner, execute=args.execute, path_service=path_service)
        report.owners_scanned += 1
        report.runs_scanned += owner_report.runs_scanned
        report.runs_repaired += owner_report.runs_repaired
        report.owners.append(owner_report)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )

    verb = "would repair" if report.dry_run else "repaired"
    print(f"research failure_reason repair report: {args.output}")
    print(
        f"  dry_run={report.dry_run} owners_scanned={report.owners_scanned} "
        f"runs_scanned={report.runs_scanned} {verb}={report.runs_repaired}"
    )
    for owner_report in report.owners:
        if owner_report.error is not None:
            print(f"  owner {owner_report.owner_id}: ERROR {owner_report.error}")
        elif owner_report.runs_repaired:
            print(f"  owner {owner_report.owner_id}: {verb} {owner_report.runs_repaired} run(s)")
    if report.dry_run and report.runs_repaired:
        print("  (pass --execute to apply the clearing revisions)")
    return 0


def _json_default(obj: Any) -> Any:
    if isinstance(obj, OwnerReport | RepairReport):
        if isinstance(obj, OwnerReport):
            return {
                "owner_id": obj.owner_id,
                "runs_scanned": obj.runs_scanned,
                "runs_repaired": obj.runs_repaired,
                "repaired": obj.repaired,
                "skipped_already_clean": obj.skipped_already_clean,
                "error": obj.error,
            }
        return {
            "dry_run": obj.dry_run,
            "owners_scanned": obj.owners_scanned,
            "runs_scanned": obj.runs_scanned,
            "runs_repaired": obj.runs_repaired,
            "owners": [_json_default(o) for o in obj.owners],
        }
    raise TypeError(f"not JSON serializable: {type(obj)!r}")


if __name__ == "__main__":
    raise SystemExit(main())
