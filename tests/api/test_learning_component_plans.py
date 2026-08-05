from __future__ import annotations

from datetime import UTC, datetime

import pytest

FastAPI = pytest.importorskip("fastapi").FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from traittutor import learning_packs
from traittutor.api.routers import learning_packs as router_module
from traittutor.learning_components import (
    LearningComponent,
    LearningComponentPlan,
    SubjectSupportState,
)


def _plan(pack_id: str) -> LearningComponentPlan:
    now = datetime.now(UTC).isoformat()
    return LearningComponentPlan(
        plan_id="plan-api",
        pack_id=pack_id,
        goal="Learn slope",
        subject_ref={
            "subject_id": "mathematics", "label": "Mathematics",
            "path": ["mathematics"], "confidence": .9,
            "source": "material_analysis", "confirmed": False,
        },
        support_state_snapshot=SubjectSupportState(subject_id="mathematics"),
        components=[
            LearningComponent(
                component_id="cmp-api", component_type="diagnostic_check",
                executor="assessment", label_zh="起点诊断", label_en="Diagnostic",
                concept_refs=["slope"], support_dimensions=["monitoring_regulation"],
                bkt_stage="unobserved", modality="interactive", required=True,
                reason="No graded evidence yet.", completion_event="quiz_answer",
            )
        ],
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(learning_packs, "_path", lambda: tmp_path / "learning-packs.json")
    monkeypatch.setattr(
        router_module,
        "_build_component_plan",
        lambda pack, request: _plan(str(pack["pack_id"])),
    )

    async def record(*args, **kwargs):
        return True

    monkeypatch.setattr(router_module, "_record_component_learning_event", record)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1/learning-packs")
    with TestClient(app) as test_client:
        yield test_client


def test_plan_api_creates_reads_and_records_component_progress(client: TestClient):
    pack = client.post("/api/v1/learning-packs", json={"title": "Math", "goal": "Learn slope"}).json()
    learning_packs.update_pack(
        pack["pack_id"],
        {"artifact": {
            "kind": "quiz", "verified_generation_id": "generation-api",
            "items": [{"question_id": "question-api", "correct_answer": "4", "question_type": "short"}],
        }},
    )
    created = client.post(f"/api/v1/learning-packs/{pack['pack_id']}/plans", json={})
    assert created.status_code == 200
    assert created.json()["start_url"] == f"/space/learning/{pack['pack_id']}"
    assert created.json()["components"][0]["component_type"] == "diagnostic_check"

    fetched = client.get(f"/api/v1/learning-packs/{pack['pack_id']}/plans/plan-api")
    assert fetched.status_code == 200
    assert fetched.json()["support_state_snapshot"]["boundary"]

    interaction = client.post(
        f"/api/v1/learning-packs/{pack['pack_id']}/plans/plan-api/components/cmp-api/events",
        json={
            "event_id": "component-event-api",
            "action": "complete",
            "question_id": "question-api",
            "answer": "4",
            "output_ref": "generation-api",
            "concept_id": "slope",
            "replan": False,
        },
    )
    assert interaction.status_code == 200
    assert interaction.json()["component"]["status"] == "completed"
    assert interaction.json()["learner_state_updated"] is True

    updated_pack = client.get(f"/api/v1/learning-packs/{pack['pack_id']}").json()
    assert len(updated_pack["component_progress"]["plan-api"]["events"]) == 1
    assert updated_pack["artifacts"]["quiz"][0]["verified_generation_id"] == "generation-api"


def test_plan_api_rejects_unknown_pack_plan_and_component(client: TestClient):
    assert client.post("/api/v1/learning-packs/missing/plans", json={}).status_code == 404
    pack = client.post("/api/v1/learning-packs", json={"title": "Math"}).json()
    assert client.get(f"/api/v1/learning-packs/{pack['pack_id']}/plans/missing").status_code == 404


def test_assessment_event_rejects_client_supplied_observation(client: TestClient):
    pack = client.post("/api/v1/learning-packs", json={"title": "Math"}).json()
    created = client.post(f"/api/v1/learning-packs/{pack['pack_id']}/plans", json={})
    component_id = created.json()["components"][0]["component_id"]
    response = client.post(
        f"/api/v1/learning-packs/{pack['pack_id']}/plans/plan-api/components/{component_id}/events",
        json={"action": "complete", "observation": "correct"},
    )
    assert response.status_code == 422


def test_audio_component_rejects_untrusted_media_url(client: TestClient):
    pack = client.post("/api/v1/learning-packs", json={"title": "Math"}).json()
    plan = _plan(pack["pack_id"]).model_dump()
    plan["components"] = [{
        "component_id": "audio-api", "component_type": "audio_explanation",
        "executor": "audio", "label_zh": "音频", "label_en": "Audio",
        "concept_refs": ["slope"], "support_dimensions": [], "bkt_stage": "unobserved",
        "modality": "audio", "required": True, "reason": "Audio explanation",
        "completion_event": "audio_played",
    }]
    learning_packs.create_component_plan(pack["pack_id"], plan)

    response = client.post(
        f"/api/v1/learning-packs/{pack['pack_id']}/plans/plan-api/components/audio-api/events",
        json={
            "action": "complete", "output_ref": "generation-api",
            "media_url": "https://untrusted.example/audio.mp3", "replan": False,
        },
    )

    assert response.status_code == 422
