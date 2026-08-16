from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
import httpx
import pytest
import pytest_asyncio

from traittutor.api.routers import learning_governance as governance_router
from traittutor.learning.models import (
    ErrorRecord,
    ErrorRecordStatus,
    ErrorType,
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
    RepetitionState,
    RetryAttempt,
    ReviewTask,
)
from traittutor.learning.storage import LearningStore
from traittutor.learning_model.events import LearnerEventLedger
from traittutor.learning_model.misconception import MisconceptionStore
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user

_FORBIDDEN_KEYS = {
    "answer",
    "user_answer",
    "expected_answer",
    "rubric",
    "rubric_ref",
    "correct_rule",
    "prompt",
    "raw_prompt",
    "system_prompt",
    "ai_confirmation",
}


def _bundle(tmp_path, user_id: str) -> governance_router.GovernanceStoreBundle:
    root = tmp_path / user_id
    learning_store = LearningStore(root / "learning")
    state = RepetitionState(next_review_at=1.0)
    learning_store.save(
        LearningProgress(
            book_id=f"{user_id}-book",
            subject_id="math",
            modules=[
                LearningModule(
                    id="module-1",
                    name=f"{user_id} Algebra",
                    order=1,
                    knowledge_points=[
                        KnowledgePoint(
                            id="fractions",
                            name="Fractions",
                            type=KnowledgeType.PROCEDURE,
                            module_id="module-1",
                        )
                    ],
                )
            ],
            error_records=[
                ErrorRecord(
                    id=f"error-{user_id}",
                    question_id=f"question-{user_id}",
                    knowledge_point_id="fractions",
                    module_id="module-1",
                    error_type=ErrorType.APPLICATION_ERROR,
                    status=ErrorRecordStatus.REPAIRED,
                    retry_history=[
                        RetryAttempt(
                            timestamp=2.0,
                            is_correct=True,
                            attempt_number=1,
                            event_id=f"repair-{user_id}",
                        )
                    ],
                    source_event_ids=[f"event-{user_id}"],
                    created_at=1.0,
                    repaired_at=2.0,
                )
            ],
            review_queue=[
                ReviewTask(
                    id="review-fractions",
                    knowledge_point_id="fractions",
                    knowledge_type=KnowledgeType.CONCEPT,
                    due_at=state.next_review_at,
                    priority=1,
                    state=state,
                )
            ],
        )
    )
    misconception_store = MisconceptionStore(
        root / "learning_model" / "misconceptions.json",
        owner_id=user_id,
    )
    misconception_store.propose(
        hypothesis_id=f"misconception-{user_id}",
        user_id=user_id,
        subject_id="math",
        kc_ids=("fractions",),
        rubric_ref=f"private-rubric-{user_id}",
        pattern="reverses numerator and denominator",
        evidence_refs=(f"event-{user_id}",),
        created_at="2026-08-10T00:00:00Z",
    )
    return governance_router.GovernanceStoreBundle(
        learning_store=learning_store,
        event_ledger=LearnerEventLedger(root / "learning_model" / "events.json"),
        misconception_store=misconception_store,
    )


@pytest.fixture
def governance_app(tmp_path, monkeypatch) -> FastAPI:
    bundles = {user_id: _bundle(tmp_path, user_id) for user_id in ("user-a", "user-b")}

    def store_factory(user: CurrentUser) -> governance_router.GovernanceStoreBundle:
        return bundles[user.id]

    monkeypatch.setattr(governance_router, "governance_store_factory", store_factory)

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
    app.state.governance_bundles = bundles
    app.include_router(
        governance_router.router,
        prefix="/api/v1",
        dependencies=[Depends(install_test_user)],
    )
    return app


@pytest_asyncio.fixture
async def client(governance_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=governance_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://traittutor.test",
        headers={"X-Test-User": "user-a"},
    ) as api_client:
        yield api_client


def _assert_safe_keys(value: Any) -> None:
    if isinstance(value, dict):
        assert _FORBIDDEN_KEYS.isdisjoint(value)
        for child in value.values():
            _assert_safe_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_safe_keys(child)


class _ForbiddenGovernanceService:
    def snapshot(self, *, subject_id: str, kc_id: str | None):
        raise PermissionError("foreign owner")

    def subject_learning_state_snapshot(self, *, subject_id: str):
        raise PermissionError("foreign owner")


def test_snapshot_permission_failure_is_hidden_as_not_found() -> None:
    with pytest.raises(HTTPException) as captured:
        governance_router._snapshot(
            _ForbiddenGovernanceService(),
            subject_id="math",
            kc_id=None,
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == "Not found"


@pytest.mark.asyncio
async def test_learning_state_permission_failure_is_hidden_as_not_found() -> None:
    with pytest.raises(HTTPException) as captured:
        await governance_router.get_subject_learning_state(
            subject_id="math",
            service=_ForbiddenGovernanceService(),
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == "Not found"


@pytest.mark.asyncio
async def test_list_endpoints_derive_owner_and_ignore_user_selection(
    client: httpx.AsyncClient,
) -> None:
    errors = await client.get(
        "/api/v1/errors",
        params={"subject_id": "math", "user_id": "user-b"},
    )
    misconceptions = await client.get("/api/v1/misconceptions", params={"subject_id": "math"})
    repairs = await client.get("/api/v1/repairs", params={"subject_id": "math"})
    reviews = await client.get("/api/v1/reviews", params={"subject_id": "math"})

    assert errors.status_code == 200
    assert errors.json() == []
    assert [item["hypothesis_id"] for item in misconceptions.json()] == ["misconception-user-a"]
    assert repairs.json() == []
    assert reviews.json() == []


@pytest.mark.asyncio
async def test_learning_path_picker_lists_only_current_owner_and_never_subject_or_kcs(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/api/v1/learning/progress")

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert body["summaries"] == [
        {
            "book_id": "user-a-book",
            "name": "user-a Algebra",
            "modules_count": 1,
            "kp_count": 1,
            "current_stage": "diagnostic",
            "evidence_state_counts": {
                "insufficient_evidence": 1,
                "needs_support": 0,
                "developing": 0,
                "supported": 0,
            },
            "updated_at": pytest.approx(body["summaries"][0]["updated_at"]),
            "mastery_ready": True,
        }
    ]
    _assert_safe_keys(body)
    assert "subject_id" not in body["summaries"][0]
    assert "knowledge_points" not in body["summaries"][0]

    other_owner = await client.get(
        "/api/v1/learning/progress",
        headers={"X-Test-User": "user-b"},
    )
    assert other_owner.status_code == 200
    assert [item["book_id"] for item in other_owner.json()["summaries"]] == ["user-b-book"]


@pytest.mark.asyncio
async def test_canonical_learning_state_is_owner_derived_and_omits_internal_identity(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/api/v1/learning-state",
        params={"subject_id": "math", "user_id": "user-b"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["subject_id"] == "math"
    assert body["strong_event_count"] == 0
    assert body["knowledge"] == []
    assert "owner_id" not in body
    _assert_safe_keys(body)


@pytest.mark.asyncio
async def test_learning_path_picker_marks_unbound_paths_unavailable(
    client: httpx.AsyncClient,
    governance_app: FastAPI,
) -> None:
    governance_app.state.governance_bundles["user-a"].learning_store.save(
        LearningProgress(book_id="unbound-path")
    )

    response = await client.get("/api/v1/learning/progress")

    assert response.status_code == 200
    available = {item["book_id"]: item["mastery_ready"] for item in response.json()["summaries"]}
    assert available == {"unbound-path": False, "user-a-book": True}


@pytest.mark.asyncio
async def test_cross_owner_detail_is_indistinguishable_from_absence(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/api/v1/errors/error-user-b",
        params={"subject_id": "math"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Error not found"}


@pytest.mark.asyncio
async def test_event_void_is_server_targeted_idempotent_and_marks_only_target_kc_rebuild(
    client: httpx.AsyncClient,
    governance_app: FastAPI,
) -> None:
    """A browser cannot choose the owner/KC, and legacy review is not deleted."""
    bundle = governance_app.state.governance_bundles["user-a"]
    from traittutor.learning_model import LearnerEvent

    source = LearnerEvent(
        event_id="event-user-a",
        idempotency_key="submit:event-user-a",
        user_id="user-a",
        subject_id="math",
        kc_ids=("fractions",),
        surface_type="quiz",
        answer_correct=False,
        evidence_strength="strong",
        attribution_status="reliable",
        created_at="2026-08-10T00:00:00+00:00",
    )
    bundle.event_ledger.append(source)

    body = {"subject_id": "math", "reason_code": "item_invalid"}
    first = await client.post("/api/v1/learner-events/event-user-a/void", json=body)
    second = await client.post("/api/v1/learner-events/event-user-a/void", json=body)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    _assert_safe_keys(first.json())

    errors = await client.get("/api/v1/errors", params={"subject_id": "math"})
    reviews = await client.get("/api/v1/reviews", params={"subject_id": "math"})
    assert errors.json() == []
    assert reviews.json() == []
    # The source-linked row was not deleted: it remains an audit input in the
    # legacy store while public scheduling waits for a provenance-aware rebuild.
    assert bundle.learning_store.load("user-a-book").review_queue[0].id == "review-fractions"

    cross_owner = await client.post(
        "/api/v1/learner-events/event-user-a/void",
        json={"subject_id": "science", "reason_code": "item_invalid"},
        headers={"X-Test-User": "user-b"},
    )
    assert cross_owner.status_code == 404


@pytest.mark.asyncio
async def test_subject_is_required_and_blank_partition_maps_to_422(
    client: httpx.AsyncClient,
) -> None:
    missing = await client.get("/api/v1/errors")
    blank = await client.get("/api/v1/errors", params={"subject_id": "   "})

    assert missing.status_code == 422
    assert blank.status_code == 422
    assert blank.json() == {"detail": "subject_id must not be blank"}


@pytest.mark.asyncio
async def test_all_public_responses_obey_key_denylist(client: httpx.AsyncClient) -> None:
    paths = (
        "/api/v1/errors",
        "/api/v1/repairs",
        "/api/v1/misconceptions",
        "/api/v1/reviews",
    )
    for path in paths:
        response = await client.get(path, params={"subject_id": "math"})
        assert response.status_code == 200
        payload = response.json()
        _assert_safe_keys(payload)
        assert "private-rubric" not in response.text


def test_openapi_contract_has_no_client_selectable_user_id(governance_app: FastAPI) -> None:
    schema = governance_app.openapi()
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for operation in operations.values():
            parameters = operation.get("parameters", [])
            assert "user_id" not in {parameter["name"] for parameter in parameters}


def test_default_store_factory_uses_exact_authenticated_scope(tmp_path, monkeypatch) -> None:
    user = CurrentUser(
        id="scope-owner",
        username="scope-owner",
        role="user",
        scope=scope_for_user("scope-owner", is_admin=False),
    )
    observed_scopes = []

    class _PathService:
        def get_workspace_dir(self):
            return tmp_path / "workspace"

        def get_traittutor_database_path(self):
            return tmp_path / "workspace" / "traittutor" / "traittutor.sqlite3"

    def path_service_for_scope(scope):
        observed_scopes.append(scope)
        return _PathService()

    monkeypatch.setattr(
        governance_router,
        "get_path_service_for_scope",
        path_service_for_scope,
    )

    stores = governance_router.default_governance_store_factory(user)

    assert observed_scopes == [user.scope]
    assert stores.learning_store.root == tmp_path / "workspace" / "learning"
    assert stores.learning_store._adapter._store().db_path == (
        tmp_path / "workspace" / "traittutor" / "traittutor.sqlite3"
    )
    assert stores.misconception_store.owner_id == "scope-owner"
