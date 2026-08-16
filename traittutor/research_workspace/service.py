"""Owner-safe product operations for durable Research Workspaces."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from .executor import ResearchExecutionResult, ResearchPriorClaim, ResearchPriorReportContext
from .models import (
    ResearchBrief,
    ResearchClaim,
    ResearchContinuationRef,
    ResearchKnowledgeBaseBinding,
    ResearchNote,
    ResearchReportArtifact,
    ResearchRun,
    ResearchRunStatus,
    ResearchSource,
    ResearchTaskReceipt,
    ResearchWorkspace,
    ResearchWorkspaceStatus,
)
from .store import ResearchWorkspaceStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ResearchWorkspaceService:
    """Composition seam for an authenticated router and injected worker."""

    def __init__(self, store: ResearchWorkspaceStore) -> None:
        self._store = store

    @property
    def owner_id(self) -> str:
        return self._store.owner_id

    def create_workspace(
        self,
        *,
        title: str,
        subject_id: str | None,
        idempotency_key: str,
    ) -> ResearchWorkspace:
        return self._store.create_workspace(
            title=title,
            subject_id=subject_id,
            idempotency_key=idempotency_key,
        )

    def get_workspace(self, workspace_id: str) -> ResearchWorkspace | None:
        return self._store.get_workspace(workspace_id)

    def list_workspaces(self) -> tuple[ResearchWorkspace, ...]:
        return self._store.list_workspaces()

    def update_workspace(
        self,
        workspace_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        title: str | None = None,
        subject_id: str | None = None,
        status: ResearchWorkspaceStatus | None = None,
    ) -> ResearchWorkspace:
        return self._store.update_workspace(
            workspace_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            title=title,
            subject_id=subject_id,
            status=status,
        )

    def save_brief(
        self,
        workspace_id: str,
        *,
        question: str,
        objectives: tuple[str, ...] = (),
        constraints: tuple[str, ...] = (),
        source_policy: str = "web",
        knowledge_base: ResearchKnowledgeBaseBinding | None = None,
        continuation: ResearchContinuationRef | None = None,
        expected_workspace_revision: int,
        idempotency_key: str,
    ) -> ResearchBrief:
        return self._store.save_brief(
            workspace_id,
            question=question,
            objectives=objectives,
            constraints=constraints,
            source_policy=source_policy,
            knowledge_base=knowledge_base,
            continuation=continuation,
            expected_workspace_revision=expected_workspace_revision,
            idempotency_key=idempotency_key,
        )

    def get_brief(self, brief_id: str, *, version: int | None = None) -> ResearchBrief | None:
        return self._store.get_brief(brief_id, version=version)

    def list_briefs(self, workspace_id: str) -> tuple[ResearchBrief, ...]:
        return self._store.list_briefs(workspace_id)

    def start_run(
        self,
        workspace_id: str,
        *,
        brief_id: str,
        brief_version: int,
        idempotency_key: str,
    ) -> ResearchRun:
        brief = self._store.get_brief(brief_id, version=brief_version)
        if brief is None or brief.workspace_id != workspace_id:
            raise KeyError(workspace_id)
        return self._store.create_run(
            workspace_id,
            brief_id=brief_id,
            brief_version=brief_version,
            input_hash=brief.content_hash,
            idempotency_key=idempotency_key,
        )

    def get_run(self, run_id: str) -> ResearchRun | None:
        return self._store.get_run(run_id)

    def list_runs(self, workspace_id: str | None = None) -> tuple[ResearchRun, ...]:
        return self._store.list_runs(workspace_id)

    def create_note(
        self,
        workspace_id: str,
        *,
        body: str,
        source_ids: tuple[str, ...] = (),
        idempotency_key: str,
    ) -> ResearchNote:
        return self._store.create_note(
            workspace_id,
            body=body,
            source_ids=source_ids,
            idempotency_key=idempotency_key,
        )

    def list_notes(self, workspace_id: str) -> tuple[ResearchNote, ...]:
        return self._store.list_notes(workspace_id)

    def list_sources(self, workspace_id: str) -> tuple[ResearchSource, ...]:
        return self._store.list_sources(workspace_id)

    def invalidate_source(
        self,
        workspace_id: str,
        source_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        reason: str | None = None,
    ) -> ResearchSource:
        """Mark evidence unusable without deleting the source or report body."""

        return self._store.invalidate_source(
            workspace_id,
            source_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def list_claims(self, run_id: str) -> tuple[ResearchClaim, ...]:
        return self._store.list_claims(run_id)

    def get_report(self, run_id: str) -> ResearchReportArtifact | None:
        return self._store.get_report(run_id)

    def continuation_context(self, brief: ResearchBrief) -> ResearchPriorReportContext | None:
        """Resolve and revalidate the report frozen into a follow-up brief."""

        reference = brief.continuation
        if reference is None:
            return None
        report = self.get_report(reference.parent_run_id)
        if (
            report is None
            or report.report_id != reference.report_id
            or report.revision != reference.report_revision
            or report.workspace_id != brief.workspace_id
            or report.evidence_status != "active"
        ):
            raise ValueError("follow-up report is missing, changed, or no longer active")
        claims = tuple(
            claim
            for claim in self.list_claims(reference.parent_run_id)
            if claim.claim_id in report.claim_ids and claim.evidence_status == "active"
        )
        if len(claims) != len(report.claim_ids):
            raise ValueError("follow-up report claims are no longer active")
        return ResearchPriorReportContext(
            report_id=report.report_id,
            report_revision=report.revision,
            body=report.body,
            claims=tuple(
                ResearchPriorClaim(
                    text=claim.text,
                    kind=claim.kind,
                    source_ids=claim.source_ids,
                )
                for claim in claims
            ),
        )

    def list_receipts(self, run_id: str) -> tuple[ResearchTaskReceipt, ...]:
        return self._store.list_receipts(run_id)

    def list_claimable_runs(self, *, now: str | None = None) -> tuple[ResearchRun, ...]:
        return self._store.list_claimable_runs(now=now)

    def claim_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> ResearchRun:
        return self._store.claim_run(
            run_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
        )

    def renew_run_lease(
        self,
        run: ResearchRun,
        *,
        worker_id: str,
        lease_seconds: int,
        now: str | None = None,
    ) -> ResearchRun:
        """Renew a still-current worker claim without advancing user CAS state."""

        if run.owner_id != self.owner_id or run.claim_token is None:
            raise KeyError(run.run_id)
        return self._store.renew_run_lease(
            run.run_id,
            worker_id=worker_id,
            input_hash=run.input_hash,
            fencing_epoch=run.fencing_epoch,
            claim_token=run.claim_token,
            lease_seconds=lease_seconds,
            now=now,
        )

    def finalize_requested_lifecycle(
        self,
        run_id: str,
        *,
        now: str | None = None,
    ) -> ResearchRun:
        """Complete a durable pause/cancel request without accepting output."""

        return self._store.finalize_requested_lifecycle(run_id, updated_at=now)

    def list_pending_control_runs(self) -> tuple[ResearchRun, ...]:
        return self._store.list_pending_control_runs()

    def transition_run(
        self,
        run_id: str,
        target: ResearchRunStatus,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchRun:
        return self._store.transition_run(
            run_id,
            target,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )

    def get_frozen_brief(self, run: ResearchRun) -> ResearchBrief:
        if run.owner_id != self.owner_id:
            raise KeyError(run.run_id)
        brief = self._store.get_brief(run.brief_id, version=run.brief_version)
        if brief is None or brief.workspace_id != run.workspace_id:
            raise KeyError(run.brief_id)
        return brief

    def commit_execution_result(
        self,
        run: ResearchRun,
        *,
        task_id: str,
        result: ResearchExecutionResult,
        created_at: str | None = None,
    ) -> ResearchTaskReceipt:
        """Assign durable IDs and atomically commit a fenced executor result."""

        if run.owner_id != self.owner_id or run.claim_token is None:
            raise KeyError(run.run_id)
        now = created_at or _now()
        source_ids = {draft.source_key: f"rs_{uuid4().hex[:20]}" for draft in result.sources}
        sources = tuple(
            ResearchSource(
                source_id=source_ids[draft.source_key],
                workspace_id=run.workspace_id,
                owner_id=self.owner_id,
                url=draft.url,
                title=draft.title,
                excerpt=draft.excerpt,
                retrieved_at=now,
            )
            for draft in result.sources
        )
        claim_ids = {draft.claim_key: f"rc_{uuid4().hex[:20]}" for draft in result.claims}
        claims = tuple(
            ResearchClaim(
                claim_id=claim_ids[draft.claim_key],
                workspace_id=run.workspace_id,
                run_id=run.run_id,
                owner_id=self.owner_id,
                text=draft.text,
                kind=draft.kind,
                source_ids=tuple(source_ids[key] for key in draft.source_keys),
                created_at=now,
            )
            for draft in result.claims
        )
        report = ResearchReportArtifact(
            report_id=f"rpt_{uuid4().hex[:20]}",
            workspace_id=run.workspace_id,
            run_id=run.run_id,
            owner_id=self.owner_id,
            body=result.report_body,
            claim_ids=tuple(claim_ids[key] for key in result.report_claim_keys),
            created_at=now,
        )
        return self._store.commit_task_result(
            run.run_id,
            task_id=task_id,
            input_hash=run.input_hash,
            fencing_epoch=run.fencing_epoch,
            claim_token=run.claim_token,
            sources=sources,
            claims=claims,
            report=report,
            final_status="needs_review" if result.requires_review else "completed",
            created_at=now,
        )

    def record_execution_failure(
        self,
        run: ResearchRun,
        *,
        task_id: str,
        created_at: str | None = None,
    ) -> ResearchTaskReceipt:
        if run.owner_id != self.owner_id or run.claim_token is None:
            raise KeyError(run.run_id)
        return self._store.record_task_failure(
            run.run_id,
            task_id=task_id,
            input_hash=run.input_hash,
            fencing_epoch=run.fencing_epoch,
            claim_token=run.claim_token,
            created_at=created_at,
        )


__all__ = ["ResearchWorkspaceService"]
