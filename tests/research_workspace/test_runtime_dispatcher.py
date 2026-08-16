from __future__ import annotations

from pathlib import Path

from traittutor.multi_user.models import CurrentUser, UserScope
from traittutor.multi_user.paths import user_context
from traittutor.research_workspace.executor import (
    ResearchClaimDraft,
    ResearchExecutionResult,
    ResearchExecutionTask,
    ResearchSourceDraft,
)
from traittutor.research_workspace.runtime import dispatch_research_once
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.store import ResearchWorkspaceStore


class _Executor:
    def execute(self, task: ResearchExecutionTask) -> ResearchExecutionResult:
        return ResearchExecutionResult(
            sources=(
                ResearchSourceDraft(
                    source_key="source",
                    url="https://example.org/source",
                    title="Source",
                ),
            ),
            claims=(
                ResearchClaimDraft(
                    claim_key="claim",
                    text=f"Completed {task.run_id}",
                    kind="grounded",
                    source_keys=("source",),
                ),
            ),
            report_body="Owner-bound report",
            report_claim_keys=("claim",),
        )


def _owner(tmp_path: Path, owner_id: str) -> CurrentUser:
    root = tmp_path / owner_id
    return CurrentUser(
        id=owner_id,
        username=owner_id,
        role="user",
        scope=UserScope(kind="user", user_id=owner_id, root=root),
    )


def _queued_run(owner: CurrentUser) -> str:
    with user_context(owner):
        service = ResearchWorkspaceService(ResearchWorkspaceStore(owner.id))
        workspace = service.create_workspace(
            title="Research",
            subject_id=None,
            idempotency_key="workspace",
        )
        brief = service.save_brief(
            workspace.workspace_id,
            question="What should be researched?",
            expected_workspace_revision=workspace.revision,
            idempotency_key="brief",
        )
        return service.start_run(
            workspace.workspace_id,
            brief_id=brief.brief_id,
            brief_version=brief.version,
            idempotency_key="run",
        ).run_id


def test_dispatcher_recovers_queued_runs_for_each_owner_without_crossing_stores(
    tmp_path: Path,
) -> None:
    alice = _owner(tmp_path, "alice")
    bob = _owner(tmp_path, "bob")
    alice_run = _queued_run(alice)
    bob_run = _queued_run(bob)

    result = dispatch_research_once(
        owners=(alice, bob),
        executor_factory=lambda _owner: _Executor(),  # type: ignore[arg-type,return-value]
        worker_id="dispatcher-test",
    )

    assert [(item.owner_id, item.claimed, item.failed) for item in result] == [
        ("alice", 1, False),
        ("bob", 1, False),
    ]
    with user_context(alice):
        alice_service = ResearchWorkspaceService(ResearchWorkspaceStore("alice"))
        assert alice_service.get_run(alice_run).status == "completed"  # type: ignore[union-attr]
        assert alice_service.get_run(bob_run) is None
    with user_context(bob):
        bob_service = ResearchWorkspaceService(ResearchWorkspaceStore("bob"))
        assert bob_service.get_run(bob_run).status == "completed"  # type: ignore[union-attr]
        assert bob_service.get_run(alice_run) is None


def test_dispatcher_isolates_one_owner_failure_and_continues(tmp_path: Path) -> None:
    broken = _owner(tmp_path, "broken")
    healthy = _owner(tmp_path, "healthy")
    _queued_run(broken)
    healthy_run = _queued_run(healthy)

    def factory(owner: CurrentUser):
        if owner.id == "broken":
            raise RuntimeError("private provider detail")
        return _Executor()

    result = dispatch_research_once(
        owners=(broken, healthy),
        executor_factory=factory,  # type: ignore[arg-type]
        worker_id="dispatcher-test",
    )

    assert result[0].failed is True
    assert result[1].claimed == 1 and result[1].failed is False
    with user_context(healthy):
        service = ResearchWorkspaceService(ResearchWorkspaceStore("healthy"))
        assert service.get_run(healthy_run).status == "completed"  # type: ignore[union-attr]
