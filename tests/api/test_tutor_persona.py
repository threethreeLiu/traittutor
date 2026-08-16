from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Header
import httpx
import pytest
import pytest_asyncio

from traittutor.api.routers import tutor_persona
from traittutor.learning.models import KnowledgeType
from traittutor.learning_governance.models import (
    GovernanceAttributionStatus,
    ReviewStatus,
    ReviewSummary,
)
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.tutor_persona.reminder_outbox import TutorReminderOutbox
from traittutor.tutor_persona.store import TutorPersonaStore


@pytest.fixture
def persona_app(tmp_path, monkeypatch) -> FastAPI:
    stores: dict[str, TutorPersonaStore] = {}
    outboxes: dict[str, TutorReminderOutbox] = {}

    def store_factory(user: CurrentUser) -> TutorPersonaStore:
        return stores.setdefault(
            user.id, TutorPersonaStore(user.id, path=tmp_path / "personas.json")
        )

    monkeypatch.setattr(tutor_persona, "tutor_persona_store_factory", store_factory)

    def outbox_factory(user: CurrentUser) -> TutorReminderOutbox:
        return outboxes.setdefault(
            user.id,
            TutorReminderOutbox(user.id, path=tmp_path / "reminders.json"),
        )

    monkeypatch.setattr(tutor_persona, "tutor_reminder_outbox_factory", outbox_factory)

    async def install_test_user(x_test_user: Annotated[str, Header()]) -> AsyncIterator[None]:
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
    app.state.reminder_outboxes = outboxes
    app.include_router(
        tutor_persona.router,
        prefix="/api/v1/tutor-personas",
        dependencies=[Depends(install_test_user)],
    )
    return app


@pytest_asyncio.fixture
async def client(persona_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=persona_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://traittutor.test",
        headers={"X-Test-User": "user-a"},
    ) as api_client:
        yield api_client


@pytest.mark.asyncio
async def test_tutor_persona_owner_is_derived_and_response_is_style_only(
    client: httpx.AsyncClient,
) -> None:
    initial = await client.get("/api/v1/tutor-personas")
    assert initial.status_code == 200
    body = initial.json()
    assert "owner_id" not in body
    assert {"answer", "rubric", "bkt", "system_prompt"}.isdisjoint(body)

    response = await client.put(
        "/api/v1/tutor-personas",
        json={
            "expected_version": body["version"],
            "idempotency_key": "persona-update-1",
            "settings": {"name": "Guide", "tone": "calm", "user_id": "user-b"},
        },
    )
    assert response.status_code == 422

    update = await client.put(
        "/api/v1/tutor-personas",
        json={
            "expected_version": body["version"],
            "idempotency_key": "persona-update-2",
            "settings": {"name": "Guide", "tone": "calm"},
        },
    )
    assert update.status_code == 200
    assert update.json()["settings"]["tone"] == "calm"


@pytest.mark.asyncio
async def test_tutor_persona_cas_and_preview_do_not_persist_candidate(
    client: httpx.AsyncClient,
) -> None:
    current = (await client.get("/api/v1/tutor-personas")).json()
    preview = await client.post(
        "/api/v1/tutor-personas/preview",
        json={"settings": {"name": "Preview", "tone": "energetic"}},
    )
    assert preview.status_code == 200
    assert preview.json()["identity"]["display_name"] == "Preview"
    assert preview.json()["expression"]["tone"] == "energetic"
    assert (await client.get("/api/v1/tutor-personas")).json()["version"] == current["version"]

    conflict = await client.put(
        "/api/v1/tutor-personas",
        json={
            "expected_version": 999,
            "idempotency_key": "stale-update",
            "settings": {"name": "Guide"},
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "version_conflict"


@pytest.mark.asyncio
async def test_reminder_eligibility_is_owner_derived_and_delivery_free(
    client: httpx.AsyncClient,
) -> None:
    initial = (await client.get("/api/v1/tutor-personas")).json()
    before = await client.get("/api/v1/tutor-personas/reminder-eligibility")
    assert before.status_code == 200
    assert before.json()["reason"] == "proactivity_off"

    update = await client.put(
        "/api/v1/tutor-personas",
        json={
            "expected_version": initial["version"],
            "idempotency_key": "reminder-consent",
            "settings": {
                "proactivity": "reminders_only",
                "reminder_consent": True,
                "quiet_hours": {"enabled": False, "timezone": "UTC"},
            },
        },
    )
    assert update.status_code == 200
    decision = await client.get("/api/v1/tutor-personas/reminder-eligibility")
    assert decision.status_code == 200
    assert decision.json()["allowed"] is True
    assert set(decision.json()) == {"allowed", "reason", "evaluated_at"}


@pytest.mark.asyncio
async def test_reminder_inbox_is_owner_safe_and_supports_read_and_cancel(
    client: httpx.AsyncClient,
    persona_app: FastAPI,
) -> None:
    # Force dependency construction, then seed the private owner-bound outbox.
    assert (await client.get("/api/v1/tutor-personas/reminders")).status_code == 200
    outbox = persona_app.state.reminder_outboxes["user-a"]
    review = ReviewSummary(
        review_id="path:review",
        learning_path_id="path",
        subject_id="math",
        kc_id="fractions",
        knowledge_type=KnowledgeType.CONCEPT,
        due_at=1.0,
        priority=1,
        status=ReviewStatus.DUE,
        attribution_status=GovernanceAttributionStatus.VERIFIED,
        interval_index=1,
    )
    reminder = outbox.schedule_review(review, profile_version=1)
    stored_profile = tutor_persona.tutor_persona_store_factory(
        CurrentUser(
            id="user-a",
            username="user-a",
            role="user",
            scope=scope_for_user("user-a", is_admin=False),
        )
    ).get_or_create_default()
    consented = stored_profile.model_copy(
        update={"proactivity": "reminders_only", "reminder_consent": True}
    )
    outbox.reconcile_and_deliver(
        profile=consented,
        active_reference_ids={review.review_id},
    )

    listed = await client.get("/api/v1/tutor-personas/reminders?status=delivered")
    assert listed.status_code == 200
    body = listed.json()[0]
    assert "owner_id" not in body and "profile_version" not in body
    assert body["reference_id"] == "path:review"
    read = await client.post(f"/api/v1/tutor-personas/reminders/{reminder.reminder_id}/read")
    assert read.status_code == 200 and read.json()["status"] == "read"
    cancelled = await client.delete(f"/api/v1/tutor-personas/reminders/{reminder.reminder_id}")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "read"


def test_tutor_persona_openapi_has_no_client_selectable_owner(persona_app: FastAPI) -> None:
    schema = persona_app.openapi()
    assert "user_id" not in str(schema["paths"])
    assert "owner_id" not in str(schema["paths"])
