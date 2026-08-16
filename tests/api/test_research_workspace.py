from __future__ import annotations

from collections.abc import AsyncIterator
import json
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header
import httpx
import pytest
import pytest_asyncio

from traittutor.api.routers import research_workspace as research_router
from traittutor.gateway.service import GatewayReceipt, GatewayRequest, GatewayStreamEvent
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser, KnowledgeResource
from traittutor.multi_user.paths import scope_for_user
from traittutor.research_workspace.executor import (
    GatewayResearchExecutor,
    ResearchClaimDraft,
    ResearchExecutionResult,
    ResearchGatewayExecutionConfig,
    ResearchSourceDraft,
)
from traittutor.research_workspace.scheduler import ResearchRunScheduler
from traittutor.research_workspace.service import ResearchWorkspaceService
from traittutor.research_workspace.source_provider import WebSearchValidatedSourceProvider
from traittutor.research_workspace.store import ResearchWorkspaceStore


class _NoOpScheduler:
    def schedule(self, service: ResearchWorkspaceService, run_id: str) -> None:
        del service, run_id


class _RouterFakeGateway:
    def __init__(self) -> None:
        self.requests: list[GatewayRequest] = []

    async def stream(self, request: GatewayRequest) -> AsyncIterator[GatewayStreamEvent]:
        self.requests.append(request)
        source_key = json.loads(request.messages[-1].content)["validated_sources"][0]["source_key"]
        yield GatewayStreamEvent(
            type="text",
            text=json.dumps(
                {
                    "claims": [
                        {
                            "claim_key": "router-claim",
                            "text": "The validated source supports this routed claim.",
                            "kind": "grounded",
                            "source_keys": [source_key],
                        }
                    ],
                    "report_body": "A Gateway-routed report.",
                    "report_claim_keys": ["router-claim"],
                    "requires_review": False,
                }
            ),
        )
        yield GatewayStreamEvent(
            type="final",
            receipt=GatewayReceipt(
                request_id="router-gateway-request",
                purpose=request.purpose,
                model="fake-model",
                provider="fake-provider",
                route="fake-route",
                latency_ms=1,
                timeout_seconds=request.timeout_seconds,
                response_format_applied=True,
                tools_applied=0,
                attachments_applied=0,
            ),
        )


class _RouterFakeSearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def __call__(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.queries.append(query)
        return {
            "answer": "This consolidated answer is deliberately ignored.",
            "search_results": [
                {
                    "url": "https://evidence.example/router",
                    "title": "Router source",
                    "snippet": "Validated before scheduling",
                }
            ],
        }


def _bundle(tmp_path, user_id: str) -> ResearchWorkspaceService:
    service = ResearchWorkspaceService(
        ResearchWorkspaceStore(user_id, path=tmp_path / user_id / "research.json")
    )
    workspace = service.create_workspace(
        title=f"Evidence workspace {user_id}",
        subject_id="math",
        idempotency_key="seed-workspace",
    )
    brief = service.save_brief(
        workspace.workspace_id,
        question=f"What is the evidence for {user_id}?",
        expected_workspace_revision=workspace.revision,
        idempotency_key="seed-brief",
    )
    run = service.start_run(
        workspace.workspace_id,
        brief_id=brief.brief_id,
        brief_version=brief.version,
        idempotency_key="seed-run",
    )
    claimed = service.claim_run(run.run_id, worker_id="seed-worker", lease_seconds=60)
    service.commit_execution_result(
        claimed,
        task_id="seed-report",
        result=ResearchExecutionResult(
            sources=(
                ResearchSourceDraft(
                    source_key="primary",
                    url=f"https://example.test/{user_id}",
                    title=f"Primary source {user_id}",
                    excerpt="Public evidence excerpt",
                ),
            ),
            claims=(
                ResearchClaimDraft(
                    claim_key="claim",
                    text=f"Grounded public claim {user_id}",
                    kind="grounded",
                    source_keys=("primary",),
                ),
            ),
            report_body=f"Public report {user_id}",
            report_claim_keys=("claim",),
        ),
    )
    source = service.list_sources(workspace.workspace_id)[0]
    service.create_note(
        workspace.workspace_id,
        body=f"Public note {user_id}",
        source_ids=(source.source_id,),
        idempotency_key="seed-note",
    )
    return service


@pytest.fixture
def research_app(tmp_path, monkeypatch) -> FastAPI:
    services = {user_id: _bundle(tmp_path, user_id) for user_id in ("user-a", "user-b")}

    def service_factory(user: CurrentUser) -> ResearchWorkspaceService:
        return services[user.id]

    monkeypatch.setattr(research_router, "research_service_factory", service_factory)
    monkeypatch.setattr(
        research_router,
        "research_scheduler_factory",
        lambda user: _NoOpScheduler(),
    )

    async def install_test_user(
        x_test_user: Annotated[str, Header()],
    ) -> AsyncIterator[None]:
        user = CurrentUser(
            id=x_test_user,
            username=x_test_user,
            role="user",
            scope=scope_for_user(x_test_user, is_admin=False),
        )
        token = set_current_user(user)
        try:
            yield
        finally:
            reset_current_user(token)

    app = FastAPI()
    app.state.research_services = services
    app.include_router(
        research_router.router,
        prefix="/api/v1",
        dependencies=[Depends(install_test_user)],
    )
    return app


@pytest_asyncio.fixture
async def client(research_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=research_app),
        base_url="http://traittutor.test",
        headers={"X-Test-User": "user-a"},
    ) as api_client:
        yield api_client


async def _create_workspace(client: httpx.AsyncClient, key: str) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/research/workspaces",
        json={"title": f"Workspace {key}", "subject_id": "math", "idempotency_key": key},
    )
    assert response.status_code == 201
    return response.json()


async def _create_brief(
    client: httpx.AsyncClient,
    workspace: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/briefs",
        json={
            "question": f"Question {key}?",
            "objectives": ["Compare sources"],
            "constraints": ["Cite claims"],
            "source_policy": "web",
            "expected_workspace_revision": workspace["revision"],
            "idempotency_key": key,
        },
    )
    assert response.status_code == 201
    return response.json()


async def _create_run(
    client: httpx.AsyncClient,
    workspace_id: str,
    brief: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/research/workspaces/{workspace_id}/runs",
        json={
            "brief_id": brief["brief_id"],
            "brief_version": brief["version"],
            "idempotency_key": key,
        },
    )
    assert response.status_code == 202
    return response.json()


@pytest.mark.asyncio
async def test_workspace_crud_derives_owner_and_requires_revision(
    client: httpx.AsyncClient,
) -> None:
    created = await _create_workspace(client, "workspace-create")
    replay = await client.post(
        "/api/v1/research/workspaces",
        json={
            "title": "Workspace workspace-create",
            "subject_id": "math",
            "idempotency_key": "workspace-create",
        },
    )
    updated = await client.patch(
        f"/api/v1/research/workspaces/{created['workspace_id']}",
        json={
            "expected_revision": created["revision"],
            "idempotency_key": "workspace-update",
            "title": "Updated workspace",
        },
    )
    stale = await client.patch(
        f"/api/v1/research/workspaces/{created['workspace_id']}",
        json={
            "expected_revision": created["revision"],
            "idempotency_key": "workspace-stale",
            "title": "Stale update",
        },
    )
    listed = await client.get(
        "/api/v1/research/workspaces",
        params={"owner_id": "user-b"},
    )
    injected = await client.post(
        "/api/v1/research/workspaces",
        json={
            "title": "Injected",
            "subject_id": "math",
            "idempotency_key": "injected",
            "owner_id": "user-b",
        },
    )

    assert replay.status_code == 201
    assert replay.json()["workspace_id"] == created["workspace_id"]
    assert updated.status_code == 200 and updated.json()["revision"] == 2
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert all("user-b" not in workspace["title"] for workspace in listed.json())
    assert "owner_id" not in listed.text + replay.text + updated.text
    assert injected.status_code == 422


@pytest.mark.asyncio
async def test_brief_create_list_read_and_immutable_update(
    client: httpx.AsyncClient,
) -> None:
    workspace = await _create_workspace(client, "brief-workspace")
    first = await _create_brief(client, workspace, "brief-v1")
    current_workspace = (
        await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}")
    ).json()
    second = await client.put(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/briefs/{first['brief_id']}",
        json={
            "question": "Refined question?",
            "objectives": [],
            "constraints": [],
            "source_policy": "web",
            "expected_workspace_revision": current_workspace["revision"],
            "idempotency_key": "brief-v2",
        },
    )
    listed = await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}/briefs")
    frozen_first = await client.get(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/briefs/{first['brief_id']}",
        params={"version": 1},
    )

    assert second.status_code == 200 and second.json()["version"] == 2
    assert [brief["version"] for brief in listed.json()] == [1, 2]
    assert listed.json()[1] == second.json()
    assert frozen_first.json()["question"] == first["question"]
    assert "owner_id" not in second.text
    assert "content_hash" not in second.text


@pytest.mark.asyncio
async def test_kb_brief_freezes_authorized_ref_and_run_cannot_override_it(
    client: httpx.AsyncClient,
    research_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = KnowledgeResource(
        id="user:kb:method-notes",
        name="method-notes",
        base_dir=Path("/private/research-user-a/knowledge_bases"),
        source="user",
    )

    def resolve(ref: str) -> KnowledgeResource | None:
        return resource if ref == resource.id else None

    monkeypatch.setattr(research_router, "resolve_for_rag", resolve)
    workspace = await _create_workspace(client, "kb-frozen-brief")
    brief_response = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/briefs",
        json={
            "question": "What does the assigned KB support?",
            "objectives": [],
            "constraints": [],
            "source_policy": "knowledge_base",
            "knowledge_base_ref": resource.id,
            "expected_workspace_revision": workspace["revision"],
            "idempotency_key": "kb-frozen-brief",
        },
    )
    brief = brief_response.json()
    run_override = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs",
        json={
            "brief_id": brief["brief_id"],
            "brief_version": brief["version"],
            "knowledge_base_ref": "user:kb:other-owner-kb",
            "idempotency_key": "cannot-override-frozen-brief",
        },
    )
    persisted = research_app.state.research_services["user-a"].get_brief(
        brief["brief_id"], version=brief["version"]
    )

    assert brief_response.status_code == 201
    assert brief["knowledge_base"] == {
        "resource_id": "user:kb:method-notes",
        "display_name": "method-notes",
        "source": "user",
    }
    assert "/private/" not in brief_response.text
    assert "authorized_owner_id" not in brief_response.text
    assert run_override.status_code == 422
    assert persisted is not None
    assert persisted.knowledge_base is not None
    assert persisted.knowledge_base.authorized_owner_id == "user-a"


@pytest.mark.asyncio
async def test_run_creation_returns_202_and_duplicate_is_idempotent(
    client: httpx.AsyncClient,
) -> None:
    workspace = await _create_workspace(client, "run-workspace")
    first_brief = await _create_brief(client, workspace, "run-brief-v1")
    first = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs",
        json={
            "brief_id": first_brief["brief_id"],
            "brief_version": first_brief["version"],
            "idempotency_key": "run-key",
        },
    )
    replay = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs",
        json={
            "brief_id": first_brief["brief_id"],
            "brief_version": first_brief["version"],
            "idempotency_key": "run-key",
        },
    )
    current_workspace = (
        await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}")
    ).json()
    second_brief = await client.put(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/briefs/{first_brief['brief_id']}",
        json={
            "question": "Different frozen input?",
            "expected_workspace_revision": current_workspace["revision"],
            "idempotency_key": "run-brief-v2",
        },
    )
    conflict = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs",
        json={
            "brief_id": first_brief["brief_id"],
            "brief_version": second_brief.json()["version"],
            "idempotency_key": "run-key",
        },
    )
    listed = await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs")

    assert first.status_code == replay.status_code == 202
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {"code": "idempotency_conflict"}
    assert len(listed.json()) == 1
    forbidden = {
        "owner_id",
        "input_hash",
        "idempotency_key",
        "lease_revision",
        "claim_token",
        "claimed_by",
    }
    assert forbidden.isdisjoint(first.json())


@pytest.mark.asyncio
async def test_run_pause_resume_cancel_and_invalid_lifecycle_mapping(
    client: httpx.AsyncClient,
) -> None:
    workspace = await _create_workspace(client, "lifecycle-workspace")
    brief = await _create_brief(client, workspace, "lifecycle-brief")
    run = await _create_run(client, workspace["workspace_id"], brief, "lifecycle-run")
    base = f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs/{run['run_id']}"
    paused = await client.post(
        f"{base}/pause",
        json={
            "expected_revision": run["revision"],
            "expected_status": "queued",
            "idempotency_key": "pause",
        },
    )
    pause_replay = await client.post(
        f"{base}/pause",
        json={
            "expected_revision": run["revision"],
            "expected_status": "queued",
            "idempotency_key": "pause",
        },
    )
    wrong_state = await client.post(
        f"{base}/resume",
        json={
            "expected_revision": paused.json()["revision"],
            "expected_status": "running",
            "idempotency_key": "wrong-resume",
        },
    )
    resumed = await client.post(
        f"{base}/resume",
        json={
            "expected_revision": paused.json()["revision"],
            "expected_status": "paused",
            "idempotency_key": "resume",
        },
    )
    cancelled = await client.post(
        f"{base}/cancel",
        json={
            "expected_revision": resumed.json()["revision"],
            "expected_status": "queued",
            "idempotency_key": "cancel",
        },
    )
    invalid = await client.post(
        f"{base}/pause",
        json={
            "expected_revision": cancelled.json()["revision"],
            "expected_status": "cancelled",
            "idempotency_key": "revive",
        },
    )

    assert paused.json()["status"] == "paused"
    assert pause_replay.json() == paused.json()
    assert wrong_state.status_code == 409
    assert wrong_state.json()["detail"]["code"] == "run_state_conflict"
    assert resumed.json()["status"] == "queued"
    assert cancelled.json()["status"] == "cancelled"
    assert invalid.status_code == 409
    assert invalid.json()["detail"] == {"code": "invalid_run_transition"}


@pytest.mark.asyncio
@pytest.mark.parametrize("recoverable_status", ["failed", "needs_review"])
async def test_retry_recoverable_run_is_cas_idempotent_and_schedules_once(
    client: httpx.AsyncClient,
    research_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    recoverable_status: Literal["failed", "needs_review"],
) -> None:
    class RecordingScheduler:
        def __init__(self) -> None:
            self.scheduled: list[str] = []

        def schedule(self, service: ResearchWorkspaceService, run_id: str) -> None:
            assert service.owner_id == "user-a"
            self.scheduled.append(run_id)

    scheduler = RecordingScheduler()
    monkeypatch.setattr(research_router, "research_scheduler_factory", lambda user: scheduler)
    workspace = await _create_workspace(client, "retry-workspace")
    brief = await _create_brief(client, workspace, "retry-brief")
    run = await _create_run(client, workspace["workspace_id"], brief, "retry-run")
    # The creation request has its own enqueue.  The assertion below covers
    # only the explicit retry and its idempotency replay.
    scheduler.scheduled.clear()
    service: ResearchWorkspaceService = research_app.state.research_services["user-a"]
    current = service.get_run(run["run_id"])
    assert current is not None
    if recoverable_status == "needs_review":
        current = service.transition_run(
            run["run_id"],
            "running",
            expected_revision=current.revision,
            idempotency_key="force-running-for-retry",
        )
    recoverable = service.transition_run(
        run["run_id"],
        recoverable_status,
        expected_revision=current.revision,
        idempotency_key=f"force-{recoverable_status}-for-retry",
    )
    base = f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs/{run['run_id']}"
    payload = {
        "expected_revision": recoverable.revision,
        "expected_status": recoverable_status,
        "idempotency_key": f"retry-{recoverable_status}-run",
    }

    accepted = await client.post(f"{base}/retry", json=payload)
    replay = await client.post(f"{base}/retry", json=payload)
    stale = await client.post(
        f"{base}/retry",
        json={**payload, "idempotency_key": "stale-retry"},
    )

    assert accepted.status_code == replay.status_code == 200
    assert accepted.json() == replay.json()
    assert accepted.json()["status"] == "queued"
    assert accepted.json()["fencing_epoch"] == recoverable.fencing_epoch + 1
    assert scheduler.scheduled == [run["run_id"]]
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"


@pytest.mark.asyncio
async def test_cross_owner_objects_are_indistinguishable_from_absent(
    client: httpx.AsyncClient,
) -> None:
    other_headers = {"X-Test-User": "user-b"}
    other_workspace = (
        await client.get("/api/v1/research/workspaces", headers=other_headers)
    ).json()[0]
    other_run = (
        await client.get(
            f"/api/v1/research/workspaces/{other_workspace['workspace_id']}/runs",
            headers=other_headers,
        )
    ).json()[0]
    responses = (
        await client.get(f"/api/v1/research/workspaces/{other_workspace['workspace_id']}"),
        await client.get("/api/v1/research/workspaces/missing"),
        await client.get(
            f"/api/v1/research/workspaces/{other_workspace['workspace_id']}/runs/{other_run['run_id']}"
        ),
        await client.get("/api/v1/research/workspaces/missing/runs/missing"),
        await client.get(
            f"/api/v1/research/workspaces/{other_workspace['workspace_id']}/runs/{other_run['run_id']}/report"
        ),
        await client.get("/api/v1/research/workspaces/missing/runs/missing/report"),
    )

    assert all(response.status_code == 404 for response in responses)
    assert {response.json()["detail"] for response in responses} == {"Research object not found"}


@pytest.mark.asyncio
async def test_sources_notes_and_report_are_public_safe_and_grounded(
    client: httpx.AsyncClient,
) -> None:
    workspace = (await client.get("/api/v1/research/workspaces")).json()[0]
    runs = await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs")
    completed = next(run for run in runs.json() if run["status"] == "completed")
    sources = await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}/sources")
    notes = await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}/notes")
    report = await client.get(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs/{completed['run_id']}/report"
    )
    created_note = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/notes",
        json={
            "body": "A second grounded note",
            "source_ids": [sources.json()[0]["source_id"]],
            "idempotency_key": "api-note",
        },
    )
    unknown_source = await client.post(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/notes",
        json={
            "body": "Unsupported note",
            "source_ids": ["missing-source"],
            "idempotency_key": "bad-note",
        },
    )

    assert sources.status_code == notes.status_code == report.status_code == 200
    assert created_note.status_code == 201
    assert str(sources.json()[0]["url"]).startswith("https://")
    assert report.json()["claims"][0]["source_ids"] == [sources.json()[0]["source_id"]]
    assert unknown_source.status_code == 422
    combined = sources.text + notes.text + report.text + created_note.text
    forbidden = (
        "owner_id",
        "claim_token",
        "idempotency_key",
        "provider_prompt",
        "system_prompt",
        "hidden_reasoning",
        "secret",
    )
    assert all(key not in combined for key in forbidden)


@pytest.mark.asyncio
async def test_source_invalidation_is_owner_bound_cas_idempotent_and_marks_evidence_for_review(
    client: httpx.AsyncClient,
) -> None:
    workspace = (await client.get("/api/v1/research/workspaces")).json()[0]
    workspace_id = workspace["workspace_id"]
    run = (await client.get(f"/api/v1/research/workspaces/{workspace_id}/runs")).json()[0]
    source = (await client.get(f"/api/v1/research/workspaces/{workspace_id}/sources")).json()[0]
    endpoint = f"/api/v1/research/workspaces/{workspace_id}/sources/{source['source_id']}"
    body = {
        "expected_revision": source["revision"],
        "expected_status": "active",
        "idempotency_key": "invalidate-source",
        "reason": "The source was withdrawn.",
    }

    invalidated = await client.request("DELETE", endpoint, json=body)
    replay = await client.request("DELETE", endpoint, json=body)
    report = await client.get(
        f"/api/v1/research/workspaces/{workspace_id}/runs/{run['run_id']}/report"
    )
    stale = await client.request(
        "DELETE",
        endpoint,
        json={**body, "idempotency_key": "stale-source"},
    )
    other_owner = await client.request(
        "DELETE",
        f"/api/v1/research/workspaces/{workspace_id}/sources/{source['source_id']}",
        headers={"x-test-user": "user-b"},
        json=body,
    )

    assert invalidated.status_code == replay.status_code == 200
    assert invalidated.json() == replay.json()
    assert invalidated.json()["status"] == "invalidated"
    assert invalidated.json()["invalidation_reason"] == "The source was withdrawn."
    assert report.status_code == 200
    assert report.json()["body"] == "Public report user-a"
    assert report.json()["evidence_status"] == "needs_review"
    assert report.json()["claims"][0]["evidence_status"] == "needs_review"
    assert report.json()["claims"][0]["review_required_source_ids"] == [source["source_id"]]
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert other_owner.status_code == 404


@pytest.mark.asyncio
async def test_create_run_schedules_owner_bound_gateway_execution(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _RouterFakeGateway()
    search = _RouterFakeSearch()

    def scheduler_factory(user: CurrentUser) -> ResearchRunScheduler:
        return ResearchRunScheduler(
            lambda: GatewayResearchExecutor(
                gateway,
                source_provider=WebSearchValidatedSourceProvider(search),
                config=ResearchGatewayExecutionConfig(),
                user_id=user.id,
            ),
            worker_id=f"router-worker-{user.id}",
        )

    monkeypatch.setattr(research_router, "research_scheduler_factory", scheduler_factory)
    workspace = await _create_workspace(client, "scheduled-workspace")
    brief = await _create_brief(client, workspace, "scheduled-brief")

    accepted = await _create_run(
        client,
        workspace["workspace_id"],
        brief,
        "scheduled-run",
    )
    persisted = await client.get(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs/{accepted['run_id']}"
    )
    sources = await client.get(f"/api/v1/research/workspaces/{workspace['workspace_id']}/sources")
    report = await client.get(
        f"/api/v1/research/workspaces/{workspace['workspace_id']}/runs/{accepted['run_id']}/report"
    )

    assert accepted["status"] == "queued"
    assert persisted.json()["status"] == "completed"
    assert report.status_code == 200
    assert report.json()["claims"][0]["source_ids"] == [sources.json()[0]["source_id"]]
    assert gateway.requests[0].purpose == "research_workspace"
    assert gateway.requests[0].user_id == "user-a"
    assert "https://evidence.example/router" in gateway.requests[0].messages[-1].content
    assert search.queries == ["Question scheduled-brief?"]
