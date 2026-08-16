"""Tests for the one-off ``repair_research_failure_reason`` backfill script.

The script clears stale ``failure_reason`` on non-failed research runs.  Tests
seed synthetic state into an isolated ``tmp_path`` workspace via the store API,
then run the script as a subprocess with ``--workspace-root`` so the global
identity registry and runtime home are never touched.  This mirrors the
``test_cleanup`` harness for the storage_cleanup script.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

from traittutor.research_workspace.models import ResearchReportArtifact
from traittutor.research_workspace.store import ResearchWorkspaceStore
from traittutor.services.path_service import PathService

T0 = "2026-08-10T00:00:00+00:00"
T1 = "2026-08-10T00:00:10+00:00"
T2 = "2026-08-10T00:00:20+00:00"

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "repair_research_failure_reason.py"


def _seed_store(tmp_path: Path) -> ResearchWorkspaceStore:
    # Resolve the same legacy path the script uses (PathService workspace dir),
    # so the seeded DB is exactly the one the subprocess script reads.
    service = PathService(workspace_root=tmp_path)
    legacy = service.get_workspace_dir() / "traittutor" / "research_workspaces.json"
    return ResearchWorkspaceStore("local-admin", path=legacy)


def _create_run(store: ResearchWorkspaceStore):
    workspace = store.create_workspace(
        title="Repair",
        subject_id=None,
        idempotency_key="workspace",
        created_at=T0,
    )
    brief = store.save_brief(
        workspace.workspace_id,
        question="Does the backfill clear stale reasons?",
        expected_workspace_revision=1,
        idempotency_key="brief",
        created_at=T0,
    )
    return store.create_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        input_hash=brief.content_hash,
        idempotency_key="run",
        created_at=T0,
    )


def _drive_to_failed(store: ResearchWorkspaceStore, run) -> None:
    claim = store.claim_run(
        run.run_id,
        worker_id="worker",
        lease_seconds=60,
        now=T0,
    )
    store.record_task_failure(
        run.run_id,
        task_id="report",
        input_hash=claim.input_hash,
        fencing_epoch=claim.fencing_epoch,
        claim_token=claim.claim_token or "",
        created_at=T1,
    )


def _stamp_stale_failure_reason(store: ResearchWorkspaceStore, run) -> int:
    """Force the live completed run to carry ``executor_failed``.

    The store fix already clears the reason on success; to exercise the backfill
    against genuinely stale data (as it exists in production from before the
    fix) we re-stamp the live revision with the stale reason, reproducing the
    pre-fix persisted state.  Returns the live revision before stamping.
    """

    completed = store.get_run(run.run_id)
    assert completed is not None and completed.status == "completed"
    with store._locked() as payload:  # noqa: SLF001 - synthetic stale data
        stale = completed.model_copy(update={"failure_reason": "executor_failed"})
        payload["runs"].append(stale.model_dump(mode="json"))
        store._adapter.replace_all(payload)  # noqa: SLF001
    return completed.revision


def _make_stale_completed_run(tmp_path: Path):
    store = _seed_store(tmp_path)
    run = _create_run(store)
    _drive_to_failed(store, run)
    failed = store.get_run(run.run_id)
    assert failed is not None
    store.transition_run(
        run.run_id,
        "queued",
        expected_revision=failed.revision,
        idempotency_key="retry",
        updated_at=T2,
    )
    claim = store.claim_run(
        run.run_id,
        worker_id="worker-2",
        lease_seconds=60,
        now=T2,
    )
    store.commit_task_result(
        run.run_id,
        task_id="report-2",
        input_hash=claim.input_hash,
        fencing_epoch=claim.fencing_epoch,
        claim_token=claim.claim_token or "",
        sources=(),
        claims=(),
        report=ResearchReportArtifact(
            report_id="report-2",
            workspace_id=run.workspace_id,
            run_id=run.run_id,
            owner_id=run.owner_id,
            body="A durable report.",
            created_at=T2,
        ),
        final_status="completed",
        created_at=T2,
    )
    live_revision = _stamp_stale_failure_reason(store, run)
    return store, run, live_revision


def _run_script(workspace_root: Path, output: Path, *, execute: bool = False) -> dict:
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--workspace-root",
        str(workspace_root),
        "--output",
        str(output),
    ]
    if execute:
        cmd.append("--execute")
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(output.read_text(encoding="utf-8"))


def _read_live_failure_reason(workspace_root: Path) -> str | None:
    """Read the live run's failure_reason straight from the DB.

    The script resolves the local-admin owner under ``workspace_root``; the
    DB path mirrors ``PathService.get_traittutor_database_path``.
    """

    db = workspace_root / "user" / "workspace" / "traittutor" / "traittutor.sqlite3"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    # The research_runs table stores one row per run_id; replace_all rewrites
    # the latest payload_json in place for the primary-key record_id.
    row = con.execute(
        "SELECT payload_json FROM research_runs ORDER BY CAST("
        "json_extract(payload_json, '$.revision') AS INTEGER"
        ") DESC LIMIT 1"
    ).fetchone()
    con.close()
    assert row is not None
    return json.loads(row["payload_json"]).get("failure_reason")


def test_dry_run_reports_stale_run_and_does_not_mutate(tmp_path: Path) -> None:
    store, run, live_revision = _make_stale_completed_run(tmp_path)
    before = store.get_run(run.run_id)
    assert before is not None and before.failure_reason == "executor_failed"

    report = _run_script(tmp_path, tmp_path / "report.json")

    assert report["dry_run"] is True
    assert report["owners_scanned"] == 1
    assert report["runs_scanned"] == 1
    assert report["runs_repaired"] == 1
    entry = report["owners"][0]["repaired"][0]
    assert entry["status"] == "completed"
    assert entry["cleared_reason"] == "executor_failed"
    assert entry["previous_revision"] == live_revision
    # Dry-run must not persist anything.
    after = store.get_run(run.run_id)
    assert after is not None
    assert after.failure_reason == "executor_failed"
    assert after.revision == live_revision


def test_execute_clears_stale_reason_and_is_idempotent(tmp_path: Path) -> None:
    store, run, _live_revision = _make_stale_completed_run(tmp_path)

    first = _run_script(tmp_path, tmp_path / "report1.json", execute=True)
    assert first["dry_run"] is False
    assert first["runs_repaired"] == 1
    cleared = store.get_run(run.run_id)
    assert cleared is not None
    assert cleared.status == "completed"
    assert cleared.failure_reason is None
    assert _read_live_failure_reason(tmp_path) is None

    # Re-running finds nothing: the reason is already gone.
    second = _run_script(tmp_path, tmp_path / "report2.json", execute=True)
    assert second["runs_repaired"] == 0
    assert second["owners"][0]["skipped_already_clean"] == 1


def test_genuinely_failed_run_is_left_alone(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    run = _create_run(store)
    _drive_to_failed(store, run)

    report = _run_script(tmp_path, tmp_path / "report.json", execute=True)
    assert report["runs_repaired"] == 0
    failed = store.get_run(run.run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_reason == "executor_failed"
    assert _read_live_failure_reason(tmp_path) == "executor_failed"


def test_clean_run_is_not_touched(tmp_path: Path) -> None:
    store = _seed_store(tmp_path)
    run = _create_run(store)
    claim = store.claim_run(
        run.run_id,
        worker_id="worker",
        lease_seconds=60,
        now=T0,
    )
    store.commit_task_result(
        run.run_id,
        task_id="report",
        input_hash=claim.input_hash,
        fencing_epoch=claim.fencing_epoch,
        claim_token=claim.claim_token or "",
        sources=(),
        claims=(),
        report=ResearchReportArtifact(
            report_id="report",
            workspace_id=run.workspace_id,
            run_id=run.run_id,
            owner_id=run.owner_id,
            body="A durable report.",
            created_at=T1,
        ),
        final_status="completed",
        created_at=T1,
    )

    report = _run_script(tmp_path, tmp_path / "report.json", execute=True)
    assert report["runs_repaired"] == 0
    assert report["owners"][0]["skipped_already_clean"] == 1
    completed = store.get_run(run.run_id)
    assert completed is not None and completed.failure_reason is None
