"""``failure_reason`` must not survive a transition out of failed/needs_review.

The store is append-only: every state change derives the next run revision
through ``model_copy(update=...)``.  A partial update that omits
``failure_reason`` silently inherits the stale ``"executor_failed"`` string
written by :meth:`record_task_failure`, which then renders next to a green
``completed`` badge in the UI.  These tests pin the reset at every exit from a
failed-ish state: retry (``failed -> queued``), successful commit, and settled
pause/cancel.
"""

from __future__ import annotations

from pathlib import Path

from traittutor.research_workspace.models import ResearchReportArtifact
from traittutor.research_workspace.store import ResearchWorkspaceStore

T0 = "2026-08-10T00:00:00+00:00"
T1 = "2026-08-10T00:00:10+00:00"
T2 = "2026-08-10T00:00:20+00:00"
T3 = "2026-08-10T00:00:30+00:00"
T4 = "2026-08-10T00:00:40+00:00"


def _queued_run(store: ResearchWorkspaceStore):
    workspace = store.create_workspace(
        title="Failure reason",
        subject_id=None,
        idempotency_key="workspace",
        created_at=T0,
    )
    brief = store.save_brief(
        workspace.workspace_id,
        question="Does failure_reason leak across transitions?",
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


def _claim_and_fail(store: ResearchWorkspaceStore, run):
    """Drive a queued run to a durable ``failed`` state with a receipt."""
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
    failed = store.get_run(run.run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_reason == "executor_failed"
    return failed


def test_retry_after_failure_clears_failure_reason(tmp_path: Path) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    failed = _claim_and_fail(store, run)

    retried = store.transition_run(
        run.run_id,
        "queued",
        expected_revision=failed.revision,
        idempotency_key="retry",
        updated_at=T2,
    )

    assert retried.status == "queued"
    assert retried.failure_reason is None
    current = store.get_run(run.run_id)
    assert current is not None
    assert current.failure_reason is None


def test_completed_after_retry_has_no_failure_reason(tmp_path: Path) -> None:
    """Regression: a run retried to success must render "completed" only.

    Before the fix, ``commit_task_result`` derived the completed revision from
    the failed run via a partial ``model_copy`` that omitted ``failure_reason``,
    so the UI showed ``status="completed"`` next to the red ``executor_failed``
    string inherited from the failed attempt.
    """
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    failed = _claim_and_fail(store, run)

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
        report=_empty_report(claim, created_at=T3),
        final_status="completed",
        created_at=T3,
    )

    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.failure_reason is None


def test_completed_directly_from_running_has_no_failure_reason(tmp_path: Path) -> None:
    """Even without a prior failure, a completed run must never carry a reason.

    Guards against future code that sets ``failure_reason`` on a running run
    before a successful commit.
    """
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
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
        report=_empty_report(claim, created_at=T1),
        final_status="completed",
        created_at=T1,
    )

    completed = store.get_run(run.run_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.failure_reason is None


def test_needs_review_after_failure_has_no_failure_reason(tmp_path: Path) -> None:
    """A retry that ends in ``needs_review`` must not keep ``executor_failed``.

    ``needs_review`` derives its own meaning from ``requires_review``; it must
    never be confused with a provider-executor failure.
    """
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    failed = _claim_and_fail(store, run)

    queued = store.transition_run(
        run.run_id,
        "queued",
        expected_revision=failed.revision,
        idempotency_key="retry",
        updated_at=T2,
    )
    assert queued.failure_reason is None
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
        report=_empty_report(claim, created_at=T3),
        final_status="needs_review",
        created_at=T3,
    )

    review = store.get_run(run.run_id)
    assert review is not None
    assert review.status == "needs_review"
    assert review.failure_reason is None


def test_paused_after_prior_failure_clears_failure_reason(tmp_path: Path) -> None:
    """``finalize_requested_lifecycle`` settling a pause must drop the reason.

    Defensive: although a run that failed cannot itself be paused, the append-
    only store means a settled ``paused`` revision derives from whichever
    revision ``_run`` resolved, so a leaked ``failure_reason`` would render on
    a non-failed badge.
    """
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    failed = _claim_and_fail(store, run)

    # Retry to queued, claim, then request a pause and let the worker settle it.
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
    pausing = store.transition_run(
        run.run_id,
        "pausing",
        expected_revision=claim.revision,
        idempotency_key="pause-request",
        updated_at=T3,
    )
    assert pausing.failure_reason is None

    settled = store.finalize_requested_lifecycle(run.run_id, updated_at=T4)

    assert settled.status == "paused"
    assert settled.failure_reason is None


def test_cancelled_after_prior_failure_clears_failure_reason(tmp_path: Path) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    failed = _claim_and_fail(store, run)

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
    cancelling = store.transition_run(
        run.run_id,
        "cancelling",
        expected_revision=claim.revision,
        idempotency_key="cancel-request",
        updated_at=T3,
    )
    assert cancelling.failure_reason is None

    settled = store.finalize_requested_lifecycle(run.run_id, updated_at=T4)

    assert settled.status == "cancelled"
    assert settled.failure_reason is None


def test_failed_state_still_carries_failure_reason(tmp_path: Path) -> None:
    """Sanity: the fix must not blank the reason while the run stays failed."""
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
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

    failed = store.get_run(run.run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.failure_reason == "executor_failed"


def test_transition_to_cancelled_from_failed_clears_failure_reason(
    tmp_path: Path,
) -> None:
    """``failed -> cancelled`` is a legal exit and must drop the reason."""
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    run = _queued_run(store)
    failed = _claim_and_fail(store, run)

    cancelled = store.transition_run(
        run.run_id,
        "cancelled",
        expected_revision=failed.revision,
        idempotency_key="cancel-from-failed",
        updated_at=T2,
    )

    assert cancelled.status == "cancelled"
    assert cancelled.failure_reason is None
    # Idempotency guard: repeat transitions must not resurrect the reason.
    assert store.get_run(run.run_id).failure_reason is None  # type: ignore[union-attr]
