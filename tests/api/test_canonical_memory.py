from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Header
import httpx
import pytest
import pytest_asyncio

from traittutor.api.routers import canonical_memory as memory_router
from traittutor.memory.index_store import MemoryIndexStore
from traittutor.memory.management import MemoryManagementService
from traittutor.memory.store import MemoryStore
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user


def _bundle(tmp_path, user_id: str) -> MemoryManagementService:
    root = tmp_path / user_id
    service = MemoryManagementService(
        user_id,
        store=MemoryStore(user_id, path=root / "memory.json"),
        index_store=MemoryIndexStore(user_id, path=root / "index.json"),
    )
    service.store.add_explicit(
        scope="global",
        key="language",
        value=f"language-{user_id}",
    )
    service.store.add_explicit(
        scope="subject",
        subject_id="math",
        key="goal",
        value=f"algebra-{user_id}",
    )
    service.store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="goal",
        value=f"calculus-{user_id}",
        provenance="inferred",
        confidence=0.8,
    )
    service.store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="example_style",
        value=f"visual-{user_id}",
        provenance="inferred",
        confidence=0.7,
    )
    service.store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="pace",
        value=f"steady-{user_id}",
        provenance="inferred",
        confidence=0.6,
    )
    service.store.add_explicit(
        scope="subject",
        subject_id="math",
        key="private_note",
        value=f"secret-value-{user_id}",
        evidence_refs=(f"turn-secret-{user_id}",),
        source_ref=f"https://example.test/{user_id}",
    )
    service.store.search(
        scope="subject",
        subject_id="math",
        snapshot_id=f"snapshot-{user_id}",
        purpose="management-test",
    )
    return service


@pytest.fixture
def memory_app(tmp_path, monkeypatch) -> FastAPI:
    services = {user_id: _bundle(tmp_path, user_id) for user_id in ("user-a", "user-b")}

    def service_factory(user: CurrentUser) -> MemoryManagementService:
        return services[user.id]

    monkeypatch.setattr(memory_router, "memory_service_factory", service_factory)

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
    app.include_router(
        memory_router.router,
        prefix="/api/v1",
        dependencies=[Depends(install_test_user)],
    )
    return app


@pytest_asyncio.fixture
async def client(memory_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=memory_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://traittutor.test",
        headers={"X-Test-User": "user-a"},
    ) as api_client:
        yield api_client


async def _ids(client: httpx.AsyncClient) -> dict[str, str]:
    items = (await client.get("/api/v1/memories/items")).json()
    candidates = (await client.get("/api/v1/memories/candidates")).json()
    return {
        "language": next(item["memory_id"] for item in items if item["key"] == "language"),
        "private": next(item["memory_id"] for item in items if item["key"] == "private_note"),
        "conflict": next(
            item["candidate_id"] for item in candidates if item["status"] == "conflict"
        ),
        "activate": next(
            item["candidate_id"] for item in candidates if item["key"] == "example_style"
        ),
        "reject": next(item["candidate_id"] for item in candidates if item["key"] == "pace"),
    }


@pytest.mark.asyncio
async def test_lists_derive_owner_and_public_dtos_omit_owner(
    client: httpx.AsyncClient,
) -> None:
    items = await client.get("/api/v1/memories/items", params={"user_id": "user-b"})
    candidates = await client.get("/api/v1/memories/candidates")
    conflicts = await client.get("/api/v1/memories/conflicts")

    assert items.status_code == candidates.status_code == conflicts.status_code == 200
    assert all(item["value"].endswith("user-a") for item in items.json())
    assert "user-b" not in items.text + candidates.text + conflicts.text
    assert "owner_id" not in items.text + candidates.text + conflicts.text


@pytest.mark.asyncio
async def test_candidate_lifecycle_and_explicit_conflict_supersede(
    client: httpx.AsyncClient,
) -> None:
    ids = await _ids(client)
    activated = await client.post(
        f"/api/v1/memories/candidates/{ids['activate']}/activate",
        json={"operation_id": "activate-1", "confirmed": True},
    )
    replay = await client.post(
        f"/api/v1/memories/candidates/{ids['activate']}/activate",
        json={"operation_id": "activate-1", "confirmed": True},
    )
    rejected = await client.post(
        f"/api/v1/memories/candidates/{ids['reject']}/reject",
        json={"operation_id": "reject-1"},
    )
    unconfirmed = await client.post(
        f"/api/v1/memories/conflicts/{ids['conflict']}/supersede",
        json={"operation_id": "supersede-1", "confirmed": False},
    )
    superseded = await client.post(
        f"/api/v1/memories/conflicts/{ids['conflict']}/supersede",
        json={"operation_id": "supersede-1", "confirmed": True},
    )

    assert activated.status_code == replay.status_code == 200
    assert replay.json()["memory_id"] == activated.json()["memory_id"]
    assert rejected.json()["status"] == "rejected"
    assert unconfirmed.status_code == 422
    assert superseded.status_code == 200
    assert superseded.json()["value"] == "calculus-user-a"


@pytest.mark.asyncio
async def test_cross_owner_object_ids_are_indistinguishable_from_missing(
    client: httpx.AsyncClient,
) -> None:
    other = httpx.Headers({"X-Test-User": "user-b"})
    other_items = (await client.get("/api/v1/memories/items", headers=other)).json()
    other_candidates = (await client.get("/api/v1/memories/candidates", headers=other)).json()
    other_memory_id = other_items[0]["memory_id"]
    other_candidate_id = other_candidates[0]["candidate_id"]
    other_grant = await client.post(
        "/api/v1/memories/grants",
        headers=other,
        json={
            "operation_id": "other-grant",
            "requesting_scope": "subject",
            "requesting_subject_id": "math",
            "target_scope": "global",
            "purpose": "other",
        },
    )
    other_grant_id = other_grant.json()["grant_id"]

    responses = (
        await client.get(f"/api/v1/memories/items/{other_memory_id}"),
        await client.get("/api/v1/memories/items/missing"),
        await client.request(
            "DELETE",
            f"/api/v1/memories/items/{other_memory_id}",
            json={"operation_id": "cross-delete"},
        ),
        await client.request(
            "DELETE",
            "/api/v1/memories/items/missing",
            json={"operation_id": "missing-delete"},
        ),
        await client.post(
            f"/api/v1/memories/candidates/{other_candidate_id}/activate",
            json={"operation_id": "cross-owner", "confirmed": True},
        ),
        await client.post(
            "/api/v1/memories/candidates/missing/activate",
            json={"operation_id": "missing", "confirmed": True},
        ),
        await client.delete(f"/api/v1/memories/grants/{other_grant_id}"),
        await client.delete("/api/v1/memories/grants/missing"),
    )
    assert all(response.status_code == 404 for response in responses)
    assert {response.json()["detail"] for response in responses} == {"Memory object not found"}


@pytest.mark.asyncio
async def test_delete_redacts_value_and_audit_contains_identifiers_only(
    client: httpx.AsyncClient,
) -> None:
    ids = await _ids(client)
    response = await client.request(
        "DELETE",
        f"/api/v1/memories/items/{ids['private']}",
        json={"operation_id": "delete-private"},
    )
    deleted = await client.get(
        "/api/v1/memories/items",
        params={"status": "deleted"},
    )
    audit = await client.get(
        "/api/v1/memories/access-records",
        params={"snapshot_id": "snapshot-user-a"},
    )

    assert response.status_code == deleted.status_code == audit.status_code == 200
    assert response.json()["item"]["value"] is None
    assert response.json()["item"]["evidence_refs"] == []
    assert response.json()["item"]["source_ref"] is None
    assert response.json()["item"]["redacted"] is True
    assert "secret-value-user-a" not in response.text + deleted.text + audit.text
    assert all("value" not in record for record in audit.json())


@pytest.mark.asyncio
async def test_grant_search_revoke_and_long_term_index_are_learner_safe(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(
        "/api/v1/memories/grants",
        json={
            "operation_id": "grant-1",
            "requesting_scope": "subject",
            "requesting_subject_id": "math",
            "target_scope": "global",
            "purpose": "api-test",
        },
    )
    grant = created.json()
    search_body = {
        "scope": "global",
        "requesting_scope": "subject",
        "requesting_subject_id": "math",
        "grant_id": grant["grant_id"],
        "snapshot_id": "grant-snapshot",
        "purpose": "api-test",
    }
    searched = await client.post("/api/v1/memories/search", json=search_body)
    rebuilt = await client.post(
        "/api/v1/memories/index/rebuild",
        json={"entry_id": "profile"},
    )
    status_response = await client.get("/api/v1/memories/index/status")
    revoked = await client.delete(f"/api/v1/memories/grants/{grant['grant_id']}")
    denied = await client.post("/api/v1/memories/search", json=search_body)

    assert created.status_code == 201
    assert "owner_id" not in grant
    assert searched.status_code == 200
    assert [item["key"] for item in searched.json()] == ["language"]
    assert rebuilt.status_code == status_response.status_code == 200
    assert rebuilt.json()["entries"][0]["claim_count"] > 0
    assert "markdown" not in rebuilt.text
    assert "language-user-a" not in rebuilt.text
    assert revoked.json()["status"] == "revoked"
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_request_contracts_reject_client_owner(client: httpx.AsyncClient) -> None:
    ids = await _ids(client)
    activation = await client.post(
        f"/api/v1/memories/candidates/{ids['activate']}/activate",
        json={"operation_id": "owner-injection", "confirmed": True, "owner_id": "user-b"},
    )
    grant = await client.post(
        "/api/v1/memories/grants",
        json={
            "operation_id": "owner-grant",
            "requesting_scope": "subject",
            "target_scope": "global",
            "purpose": "test",
            "owner_id": "user-b",
        },
    )

    assert activation.status_code == grant.status_code == 422
