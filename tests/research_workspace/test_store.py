from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.research_workspace.store import (
    ResearchWorkspaceIdempotencyConflict,
    ResearchWorkspaceStore,
    ResearchWorkspaceStoreError,
    ResearchWorkspaceVersionConflict,
)

T0 = "2026-08-10T00:00:00+00:00"
T1 = "2026-08-10T00:01:00+00:00"


def _workspace_and_brief(store: ResearchWorkspaceStore) -> tuple[str, str, int, str]:
    workspace = store.create_workspace(
        title="Durable research",
        subject_id="subject-1",
        idempotency_key="workspace-create",
        created_at=T0,
    )
    brief = store.save_brief(
        workspace.workspace_id,
        question="What evidence supports the claim?",
        objectives=("Compare primary sources",),
        constraints=("Cite every external claim",),
        expected_workspace_revision=workspace.revision,
        idempotency_key="brief-v1",
        created_at=T0,
    )
    return workspace.workspace_id, brief.brief_id, brief.version, brief.content_hash


def test_workspace_and_brief_are_durable_idempotent_and_owner_isolated(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research.json"
    owner_a = ResearchWorkspaceStore("owner-a", path=path)
    workspace_id, brief_id, brief_version, _ = _workspace_and_brief(owner_a)

    replay_workspace = owner_a.create_workspace(
        title="Durable research",
        subject_id="subject-1",
        idempotency_key="workspace-create",
        created_at=T1,
    )
    replay_brief = owner_a.save_brief(
        workspace_id,
        question="What evidence supports the claim?",
        objectives=("Compare primary sources",),
        constraints=("Cite every external claim",),
        expected_workspace_revision=1,
        idempotency_key="brief-v1",
        created_at=T1,
    )

    assert replay_workspace.workspace_id == workspace_id
    assert replay_workspace.created_at == T0
    assert replay_brief.brief_id == brief_id
    assert replay_brief.version == brief_version
    assert ResearchWorkspaceStore("owner-a", path=path).get_brief(brief_id) == replay_brief

    owner_b = ResearchWorkspaceStore("owner-b", path=path)
    assert owner_b.get_workspace(workspace_id) is None
    assert owner_b.get_brief(brief_id) is None
    assert owner_b.list_workspaces() == ()
    with pytest.raises(KeyError):
        owner_b.save_brief(
            workspace_id,
            question="Cross-owner probe",
            expected_workspace_revision=2,
            idempotency_key="probe",
        )


def test_idempotency_key_change_and_stale_workspace_cas_fail_closed(tmp_path: Path) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    workspace = store.create_workspace(
        title="Original",
        subject_id=None,
        idempotency_key="create",
        created_at=T0,
    )

    with pytest.raises(ResearchWorkspaceIdempotencyConflict):
        store.create_workspace(
            title="Changed meaning",
            subject_id=None,
            idempotency_key="create",
        )

    updated = store.update_workspace(
        workspace.workspace_id,
        title="Updated",
        expected_revision=1,
        idempotency_key="update-1",
        updated_at=T1,
    )
    with pytest.raises(ResearchWorkspaceVersionConflict):
        store.update_workspace(
            workspace.workspace_id,
            title="Stale",
            expected_revision=1,
            idempotency_key="update-stale",
        )

    assert updated.revision == 2
    assert store.get_workspace(workspace.workspace_id) == updated


def test_brief_versions_are_immutable_and_run_creation_is_idempotent(tmp_path: Path) -> None:
    store = ResearchWorkspaceStore("owner", path=tmp_path / "research.json")
    workspace_id, brief_id, version, input_hash = _workspace_and_brief(store)
    workspace = store.get_workspace(workspace_id)
    assert workspace is not None
    second = store.save_brief(
        workspace_id,
        question="A refined research question",
        expected_workspace_revision=workspace.revision,
        idempotency_key="brief-v2",
        created_at=T1,
    )

    first = store.get_brief(brief_id, version=version)
    assert first is not None
    assert first.question == "What evidence supports the claim?"
    assert second.brief_id == brief_id
    assert second.version == 2

    run = store.create_run(
        workspace_id,
        brief_id=brief_id,
        brief_version=version,
        input_hash=input_hash,
        idempotency_key="run-start",
        created_at=T1,
    )
    replay = store.create_run(
        workspace_id,
        brief_id=brief_id,
        brief_version=version,
        input_hash=input_hash,
        idempotency_key="run-start",
    )

    assert replay == run
    assert run.status == "queued"
    assert len(store.list_runs(workspace_id)) == 1
    assert (
        ResearchWorkspaceStore("another-owner", path=tmp_path / "research.json").get_run(run.run_id)
        is None
    )


def test_note_requires_owned_known_sources_and_store_corruption_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "research.json"
    store = ResearchWorkspaceStore("owner", path=path)
    workspace_id, _, _, _ = _workspace_and_brief(store)

    with pytest.raises(ValueError, match="unknown source"):
        store.create_note(
            workspace_id,
            body="Unsupported note",
            source_ids=("missing",),
            idempotency_key="note",
        )

    (tmp_path / "traittutor.sqlite3").write_text("not-sqlite", encoding="utf-8")
    with pytest.raises(ResearchWorkspaceStoreError):
        store.list_workspaces()
