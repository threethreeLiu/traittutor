"""Research report to courseware hand-off tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated

from fastapi import Depends, FastAPI, Header
import httpx
import pytest
import pytest_asyncio

from traittutor.api.routers import research_workspace as research_router
from traittutor.generate.service import GenerationResult
from traittutor.generate.tasks import GenerationTaskManager
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.research_workspace import courseware
from traittutor.research_workspace.courseware import (
    ResearchCoursewareEvidenceError,
    prepare_research_courseware,
    validate_research_courseware_request,
)
from traittutor.research_workspace.executor import (
    ResearchClaimDraft,
    ResearchExecutionResult,
    ResearchSourceDraft,
)
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.store import ResearchWorkspaceStore


def _service(tmp_path: Path, owner_id: str) -> ResearchWorkspaceService:
    service = ResearchWorkspaceService(
        ResearchWorkspaceStore(owner_id, path=tmp_path / owner_id / "research.json")
    )
    workspace = service.create_workspace(
        title="Evidence workspace", subject_id="math", idempotency_key="workspace"
    )
    brief = service.save_brief(
        workspace.workspace_id,
        question="What evidence supports fraction learning?",
        expected_workspace_revision=workspace.revision,
        idempotency_key="brief",
    )
    run = service.start_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        idempotency_key="run",
    )
    claimed = service.claim_run(run.run_id, worker_id="test-worker", lease_seconds=60)
    service.commit_execution_result(
        claimed,
        task_id="report",
        result=ResearchExecutionResult(
            sources=(
                ResearchSourceDraft(
                    source_key="source",
                    url="https://example.test/fractions",
                    title="Fraction evidence",
                    excerpt="Evidence excerpt",
                ),
            ),
            claims=(
                ResearchClaimDraft(
                    claim_key="claim",
                    text="Worked examples improve fraction learning.",
                    kind="grounded",
                    source_keys=("source",),
                ),
            ),
            report_body="Use worked examples before independent fraction practice.",
            report_claim_keys=("claim",),
        ),
    )
    return service


def _user(owner_id: str) -> CurrentUser:
    return CurrentUser(
        id=owner_id,
        username=owner_id,
        role="user",
        scope=scope_for_user(owner_id, is_admin=False),
    )


def _patch_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        courseware,
        "ResearchWorkspaceStore",
        lambda owner_id: ResearchWorkspaceStore(
            owner_id, path=tmp_path / owner_id / "research.json"
        ),
    )


def test_preparation_freezes_active_grounded_evidence_and_rejects_later_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, "owner-a")
    workspace = service.list_workspaces()[0]
    run = service.list_runs(workspace.workspace_id)[0]
    prepared = prepare_research_courseware(
        service,
        workspace_id=workspace.workspace_id,
        run_id=run.run_id,
        idempotency_key="courseware-1",
        language="en",
    )

    assert prepared.generation_id.startswith("rgen_")
    assert prepared.request.material.source_id == run.run_id
    assert prepared.provenance.research_run_id == run.run_id
    assert prepared.provenance.source_refs[0].source_id in prepared.request.material.text
    assert prepared.request.research_provenance == prepared.provenance
    assert "research_courseware_provenance" not in (prepared.request.options or {})
    assert "research_courseware_provenance" not in (prepared.request.material.metadata or {})
    # URLs only remain in the actual owner-held material; the durable context
    # reference must be safe for snapshot/prompt hashes and result sidecars.
    assert "url" not in prepared.provenance.model_dump(mode="json")
    assert "title" not in prepared.provenance.model_dump(mode="json")

    _patch_store(monkeypatch, tmp_path)
    assert (
        validate_research_courseware_request(prepared.request, owner_id="owner-a")
        == prepared.provenance
    )

    source = service.list_sources(workspace.workspace_id)[0]
    service.invalidate_source(
        workspace.workspace_id,
        source.source_id,
        expected_revision=source.revision,
        idempotency_key="invalidate",
    )
    with pytest.raises(ResearchCoursewareEvidenceError, match="active evidence"):
        validate_research_courseware_request(prepared.request, owner_id="owner-a")


@pytest.mark.asyncio
async def test_generation_worker_revalidates_and_writes_only_public_provenance_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, "owner-a")
    workspace = service.list_workspaces()[0]
    run = service.list_runs(workspace.workspace_id)[0]
    prepared = prepare_research_courseware(
        service,
        workspace_id=workspace.workspace_id,
        run_id=run.run_id,
        idempotency_key="courseware-worker",
    )
    _patch_store(monkeypatch, tmp_path)

    def fake_generator(_request):
        return GenerationResult(
            generation_id="wrong-id",
            generation_type="courseware",
            status="needs_review",
            events=[],
            result={"kind": "courseware"},
            created_at="2026-01-01T00:00:00+00:00",
            prompt_asset="test",
            material={"source_id": run.run_id},
            learner_profile={},
        )

    manager = GenerationTaskManager(generator=fake_generator, storage_root=tmp_path / "tasks")
    token = set_current_user(_user("owner-a"))
    try:
        task = manager.create_idempotent(prepared.request, generation_id=prepared.generation_id)
        assert task.runner is not None
        await task.runner
        assert task.status == "needs_review"
        assert task.result is not None
        assert task.result.result["research_provenance"] == prepared.provenance.model_dump(
            mode="json"
        )
        assert task.result.material["research_provenance"] == prepared.provenance.model_dump(
            mode="json"
        )
        assert "url" not in task.result.result["research_provenance"]
        assert "url" not in task.result.material["research_provenance"]
        changed = prepare_research_courseware(
            service,
            workspace_id=workspace.workspace_id,
            run_id=run.run_id,
            idempotency_key="courseware-worker",
            language="en",
        )
        assert changed.generation_id == prepared.generation_id
        with pytest.raises(ValueError, match="different input"):
            manager.create_idempotent(changed.request, generation_id=changed.generation_id)
    finally:
        reset_current_user(token)


def test_revalidation_rejects_another_owner_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, "owner-a")
    workspace = service.list_workspaces()[0]
    run = service.list_runs(workspace.workspace_id)[0]
    prepared = prepare_research_courseware(
        service,
        workspace_id=workspace.workspace_id,
        run_id=run.run_id,
        idempotency_key="owner-isolation",
    )
    _patch_store(monkeypatch, tmp_path)

    with pytest.raises(ResearchCoursewareEvidenceError, match="not found"):
        validate_research_courseware_request(prepared.request, owner_id="owner-b")


@pytest.mark.asyncio
async def test_worker_refuses_publish_when_source_is_invalidated_while_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, "owner-a")
    workspace = service.list_workspaces()[0]
    run = service.list_runs(workspace.workspace_id)[0]
    prepared = prepare_research_courseware(
        service,
        workspace_id=workspace.workspace_id,
        run_id=run.run_id,
        idempotency_key="courseware-stale",
    )
    _patch_store(monkeypatch, tmp_path)
    called = False

    def fake_generator(_request):
        nonlocal called
        called = True
        raise AssertionError("invalidated evidence must fail before provider execution")

    manager = GenerationTaskManager(generator=fake_generator, storage_root=tmp_path / "tasks")
    monkeypatch.setattr(manager, "_schedule", lambda: None)
    token = set_current_user(_user("owner-a"))
    try:
        task = manager.create_idempotent(prepared.request, generation_id=prepared.generation_id)
        source = service.list_sources(workspace.workspace_id)[0]
        service.invalidate_source(
            workspace.workspace_id,
            source.source_id,
            expected_revision=source.revision,
            idempotency_key="invalidate-after-queue",
        )
        await manager._run(task)
        assert task.status == "failed"
        assert task.result is None
        assert called is False
    finally:
        reset_current_user(token)


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    def create_idempotent(self, request, *, generation_id: str):
        self.calls.append((request, generation_id))
        return SimpleNamespace(generation_id=generation_id, status="queued")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    services = {owner: _service(tmp_path, owner) for owner in ("owner-a", "owner-b")}
    queue = _Queue()
    monkeypatch.setattr(research_router, "research_service_factory", lambda user: services[user.id])
    monkeypatch.setattr(research_router, "generation_task_manager_factory", lambda: queue)

    async def install_user(x_test_user: Annotated[str, Header()]) -> AsyncIterator[None]:
        token = set_current_user(_user(x_test_user))
        try:
            yield
        finally:
            reset_current_user(token)

    value = FastAPI()
    value.state.queue = queue
    value.state.services = services
    value.include_router(
        research_router.router,
        prefix="/api/v1",
        dependencies=[Depends(install_user)],
    )
    return value


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://traittutor.test",
        headers={"X-Test-User": "owner-a"},
    ) as value:
        yield value


@pytest.mark.asyncio
async def test_route_queues_only_owner_completed_evidence_with_stable_replay_id(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    service = app.state.services["owner-a"]
    workspace = service.list_workspaces()[0]
    run = service.list_runs(workspace.workspace_id)[0]
    path = f"/api/v1/research/workspaces/{workspace.workspace_id}/runs/{run.run_id}/courseware"
    first = await client.post(path, json={"idempotency_key": "route-courseware", "language": "en"})
    second = await client.post(path, json={"idempotency_key": "route-courseware", "language": "en"})

    assert first.status_code == 202
    assert second.status_code == 202
    first_body = first.json()
    assert first_body["generation_id"] == second.json()["generation_id"]
    assert first_body["research_run_id"] == run.run_id
    assert first_body["source_ids"]
    assert first_body["provenance_delivery"] == "queue_snapshot_prompt_bundle_and_result_ref"
    assert len(app.state.queue.calls) == 2

    cross_owner = await client.post(
        path,
        json={"idempotency_key": "cross-owner"},
        headers={"X-Test-User": "owner-b"},
    )
    assert cross_owner.status_code == 404

    source = service.list_sources(workspace.workspace_id)[0]
    service.invalidate_source(
        workspace.workspace_id,
        source.source_id,
        expected_revision=source.revision,
        idempotency_key="invalidate-before-enqueue",
    )
    blocked = await client.post(path, json={"idempotency_key": "stale-evidence"})
    assert blocked.status_code == 422
