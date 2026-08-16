from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.research_workspace.models import ResearchReportArtifact
from traittutor.research_workspace.store import (
    ResearchRunLeaseUnavailable,
    ResearchWorkspaceStore,
)

T0 = "2026-08-10T00:00:00+00:00"
T1 = "2026-08-10T00:00:10+00:00"
T2 = "2026-08-10T00:00:20+00:00"


def _queued_run(store: ResearchWorkspaceStore):
    workspace = store.create_workspace(
        title="Fencing",
        subject_id=None,
        idempotency_key="workspace",
        created_at=T0,
    )
    brief = store.save_brief(
        workspace.workspace_id,
        question="Can a stale worker mutate state?",
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


def _empty_report(run, *, created_at: str) -> ResearchReportArtifact:
    return ResearchReportArtifact(
        report_id=f"report-{created_at[-8:-6]}",
        workspace_id=run.workspace_id,
        run_id=run.run_id,
        owner_id=run.owner_id,
        body="A durable report.",
        created_at=created_at,
    )


def test_expired_lease_recovery_advances_epoch_and_fences_old_token(tmp_path: Path) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    old_claim = store.claim_run(
        run.run_id,
        worker_id="worker-old",
        lease_seconds=10,
        now=T0,
    )
    with pytest.raises(ResearchRunLeaseUnavailable):
        store.claim_run(
            run.run_id,
            worker_id="worker-too-early",
            lease_seconds=10,
            now="2026-08-10T00:00:05+00:00",
        )

    recovered = store.claim_run(
        run.run_id,
        worker_id="worker-new",
        lease_seconds=60,
        now=T1,
    )
    assert recovered.fencing_epoch == old_claim.fencing_epoch + 1
    assert recovered.claim_token != old_claim.claim_token

    stale = store.commit_task_result(
        run.run_id,
        task_id="report",
        input_hash=old_claim.input_hash,
        fencing_epoch=old_claim.fencing_epoch,
        claim_token=old_claim.claim_token or "",
        sources=(),
        claims=(),
        report=_empty_report(old_claim, created_at=T1),
        final_status="completed",
        created_at=T1,
    )

    assert stale.outcome == "discarded_stale"
    current = store.get_run(run.run_id)
    assert current is not None
    assert current.status == "running"
    assert current.claim_token == recovered.claim_token
    assert store.get_report(run.run_id) is None


def test_expired_claim_cannot_be_renewed_or_resurrected(tmp_path: Path) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    claim = store.claim_run(
        run.run_id,
        worker_id="worker-old",
        lease_seconds=10,
        now=T0,
    )

    with pytest.raises(ResearchRunLeaseUnavailable):
        store.renew_run_lease(
            run.run_id,
            worker_id="worker-old",
            input_hash=claim.input_hash,
            fencing_epoch=claim.fencing_epoch,
            claim_token=claim.claim_token or "",
            lease_seconds=10,
            now=T1,
        )

    recovered = store.claim_run(
        run.run_id,
        worker_id="worker-new",
        lease_seconds=10,
        now=T1,
    )
    assert recovered.fencing_epoch == claim.fencing_epoch + 1
    assert recovered.claim_token != claim.claim_token


def test_cancelled_run_rejects_terminal_late_result_without_resurrection(
    tmp_path: Path,
) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    claim = store.claim_run(
        run.run_id,
        worker_id="worker",
        lease_seconds=60,
        now=T0,
    )
    cancelling = store.transition_run(
        run.run_id,
        "cancelling",
        expected_revision=claim.revision,
        idempotency_key="cancel-request",
        updated_at=T1,
    )
    cancelled = store.transition_run(
        run.run_id,
        "cancelled",
        expected_revision=cancelling.revision,
        idempotency_key="cancel-finish",
        updated_at=T2,
    )

    receipt = store.commit_task_result(
        run.run_id,
        task_id="report",
        input_hash=claim.input_hash,
        fencing_epoch=claim.fencing_epoch,
        claim_token=claim.claim_token or "",
        sources=(),
        claims=(),
        report=_empty_report(claim, created_at=T2),
        final_status="completed",
        created_at=T2,
    )

    assert receipt.outcome == "discarded_stale"
    current = store.get_run(run.run_id)
    assert current == cancelled
    assert current.status == "cancelled"
    assert store.list_sources(run.workspace_id) == ()
    assert store.list_claims(run.run_id) == ()
    assert store.get_report(run.run_id) is None


def test_accepted_result_is_idempotent_and_persists_receipt_with_terminal_state(
    tmp_path: Path,
) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    claim = store.claim_run(
        run.run_id,
        worker_id="worker",
        lease_seconds=60,
        now=T0,
    )
    report = _empty_report(claim, created_at=T1)

    first = store.commit_task_result(
        run.run_id,
        task_id="report",
        input_hash=claim.input_hash,
        fencing_epoch=claim.fencing_epoch,
        claim_token=claim.claim_token or "",
        sources=(),
        claims=(),
        report=report,
        final_status="completed",
        created_at=T1,
    )
    replay = store.commit_task_result(
        run.run_id,
        task_id="report",
        input_hash=claim.input_hash,
        fencing_epoch=claim.fencing_epoch,
        claim_token=claim.claim_token or "",
        sources=(),
        claims=(),
        report=report,
        final_status="completed",
        created_at=T2,
    )

    assert first.outcome == "accepted"
    assert replay == first
    assert len(store.list_receipts(run.run_id)) == 1
    assert store.get_run(run.run_id).status == "completed"  # type: ignore[union-attr]
    assert store.get_report(run.run_id) == report
