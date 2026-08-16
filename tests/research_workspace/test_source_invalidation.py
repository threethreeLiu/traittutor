from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.research_workspace.executor import (
    ResearchClaimDraft,
    ResearchExecutionResult,
    ResearchSourceDraft,
)
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.store import (
    ResearchWorkspaceStore,
    ResearchWorkspaceVersionConflict,
)


def _completed_service(
    path: Path, owner_id: str = "owner"
) -> tuple[ResearchWorkspaceService, str, str]:
    service = ResearchWorkspaceService(ResearchWorkspaceStore(owner_id, path=path))
    workspace = service.create_workspace(
        title="Evidence review",
        subject_id="research-methods",
        idempotency_key="create-workspace",
    )
    brief = service.save_brief(
        workspace.workspace_id,
        question="Which source supports the claim?",
        expected_workspace_revision=workspace.revision,
        idempotency_key="save-brief",
    )
    run = service.start_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        idempotency_key="start-run",
    )
    claimed = service.claim_run(run.run_id, worker_id="test-worker", lease_seconds=60)
    service.commit_execution_result(
        claimed,
        task_id="report",
        result=ResearchExecutionResult(
            sources=(
                ResearchSourceDraft(
                    source_key="primary",
                    url="https://evidence.example/primary",
                    title="Primary evidence",
                    excerpt="Durable evidence",
                ),
                ResearchSourceDraft(
                    source_key="secondary",
                    url="https://evidence.example/secondary",
                    title="Independent evidence",
                ),
            ),
            claims=(
                ResearchClaimDraft(
                    claim_key="grounded",
                    text="The primary evidence supports the result.",
                    kind="grounded",
                    source_keys=("primary",),
                ),
                ResearchClaimDraft(
                    claim_key="inference",
                    text="The learner may want a follow-up.",
                    kind="inference",
                ),
            ),
            report_body="The durable report body is audit evidence.",
            report_claim_keys=("grounded", "inference"),
        ),
    )
    source = next(
        item
        for item in service.list_sources(workspace.workspace_id)
        if item.title == "Primary evidence"
    )
    return service, workspace.workspace_id, source.source_id


def test_source_invalidation_is_atomic_auditable_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "research.json"
    service, workspace_id, source_id = _completed_service(path)
    before_report = service.get_report(service.list_runs(workspace_id)[0].run_id)
    assert before_report is not None

    invalidated = service.invalidate_source(
        workspace_id,
        source_id,
        expected_revision=1,
        idempotency_key="invalidate-primary",
        reason="Publisher withdrew the result.",
    )
    replay = service.invalidate_source(
        workspace_id,
        source_id,
        expected_revision=1,
        idempotency_key="invalidate-primary",
        reason="Publisher withdrew the result.",
    )

    assert invalidated == replay
    assert invalidated.status == "invalidated"
    assert invalidated.revision == 2
    run = service.list_runs(workspace_id)[0]
    claims = service.list_claims(run.run_id)
    grounded = next(claim for claim in claims if claim.kind == "grounded")
    inference = next(claim for claim in claims if claim.kind == "inference")
    report = service.get_report(run.run_id)
    assert report is not None
    assert grounded.evidence_status == "needs_review"
    assert grounded.review_required_source_ids == (source_id,)
    assert inference.evidence_status == "active"
    assert report.evidence_status == "needs_review"
    assert report.review_required_source_ids == (source_id,)
    assert report.body == before_report.body

    raw = service._store._adapter.snapshot()
    assert len([item for item in raw["sources"] if item["source_id"] == source_id]) == 2
    assert "learner_events" not in raw
    assert "bkt" not in raw

    with pytest.raises(ResearchWorkspaceVersionConflict):
        service.invalidate_source(
            workspace_id,
            source_id,
            expected_revision=1,
            idempotency_key="stale-invalidation",
        )


def test_source_invalidation_fails_closed_for_other_owner_and_inactive_workspace(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research.json"
    service, workspace_id, source_id = _completed_service(path)
    other = ResearchWorkspaceService(ResearchWorkspaceStore("other-owner", path=path))

    with pytest.raises(KeyError):
        other.invalidate_source(
            workspace_id,
            source_id,
            expected_revision=1,
            idempotency_key="cross-owner",
        )

    workspace = service.get_workspace(workspace_id)
    assert workspace is not None
    service.update_workspace(
        workspace_id,
        expected_revision=workspace.revision,
        idempotency_key="archive-workspace",
        status="archived",
    )
    with pytest.raises(ValueError, match="non-active"):
        service.invalidate_source(
            workspace_id,
            source_id,
            expected_revision=1,
            idempotency_key="archived-invalidation",
        )
