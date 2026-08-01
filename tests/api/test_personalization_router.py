from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

FastAPI = pytest.importorskip("fastapi").FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from traittutor.api.routers import personalization as router_module
from traittutor.personalization.models import LearnerEvent, LearningSignal, SubjectRef
from traittutor.personalization import service as service_module


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(service_module.memory_paths, "memory_root", lambda: tmp_path / "memory")
    monkeypatch.setattr(service_module, "get_current_user", lambda: SimpleNamespace(id="learner-api"))
    service_module._service = None
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1/memory")
    with TestClient(app) as test_client:
        yield test_client
    service_module._service = None


def _service():
    return service_module.get_personalization_service()


def _subject() -> SubjectRef:
    return SubjectRef(subject_id="math", label="数学", path=["数学", "函数"], confidence=0.9, source="material_analysis")


def _apply(signal: LearningSignal) -> None:
    asyncio.run(_service().apply_signal(signal))


def test_reflections_api_returns_preference_concept_and_summary(client: TestClient) -> None:
    subject = _subject()
    _apply(LearningSignal(
        signal_id="candidate-api", kind="strategy_feedback",
        payload={"value": "先类比再定义", "category": "explanation"},
        evidence_refs=["chat:1"], source="system", occurred_at="2026-07-29T00:00:00+00:00",
    ))
    _apply(LearningSignal(
        signal_id="confirmed-api", kind="explicit_preference",
        payload={"value": "目标先列清楚", "category": "goal"},
        evidence_refs=["memory:goal"], source="user", occurred_at="2026-07-29T00:01:00+00:00",
    ))
    _apply(LearningSignal(
        signal_id="rejected-api", kind="strategy_feedback",
        payload={"value": "每页都写长故事", "category": "explanation", "rejected": True},
        evidence_refs=["feedback:reject"], source="system", occurred_at="2026-07-29T00:02:00+00:00",
    ))
    asyncio.run(_service().record_event(LearnerEvent(
        event_id="concept-api", event_type="quiz_answer", subject=subject,
        concept_id="limits", concept_label="极限", observation="incorrect",
        evidence_refs=["question:1"], confidence=0.9, occurred_at="2026-07-29T00:03:00+00:00",
    ), trusted=True))

    response = client.get("/api/v1/memory/learner/reflections")

    assert response.status_code == 200
    body = response.json()
    by_id = {item["reflection_id"]: item for item in body["reflections"]}
    assert by_id["candidate-api"]["status"] == "candidate"
    assert by_id["candidate-api"]["applies_to_compass"] is False
    assert by_id["confirmed-api"]["status"] == "confirmed"
    assert by_id["confirmed-api"]["applies_to_compass"] is True
    assert by_id["rejected-api"]["status"] == "rejected"
    assert by_id["concept:limits"]["category"] == "concept"
    assert body["summary"]["candidate"] >= 1
    assert body["summary"]["confirmed"] >= 1
    assert body["summary"]["rejected"] >= 1


def test_reflection_decision_api_updates_context_preview_contract(client: TestClient) -> None:
    _apply(LearningSignal(
        signal_id="candidate-context", kind="strategy_feedback",
        payload={"value": "先用一个生活案例", "category": "explanation"},
        evidence_refs=["chat:2"], source="system", occurred_at="2026-07-29T00:00:00+00:00",
    ))

    before = client.post("/api/v1/memory/learner/context/preview", json={"purpose": "chat"}).json()
    assert "先用一个生活案例" not in before["memory_snapshot"]["explicit_preferences"]

    confirm = client.patch("/api/v1/memory/learner/reflections/candidate-context", json={"status": "confirmed"})
    assert confirm.status_code == 200
    assert confirm.json()["reflection"]["status"] == "confirmed"

    after = client.post("/api/v1/memory/learner/context/preview", json={"purpose": "chat"}).json()
    assert "先用一个生活案例" in after["memory_snapshot"]["explicit_preferences"]

    reject = client.patch("/api/v1/memory/learner/reflections/candidate-context", json={"status": "rejected"})
    assert reject.status_code == 200
    rejected_context = client.post("/api/v1/memory/learner/context/preview", json={"purpose": "courseware"}).json()
    assert "先用一个生活案例" in rejected_context["constraints"]
    assert "先用一个生活案例" not in rejected_context["memory_snapshot"]["explicit_preferences"]


def test_reflection_api_rejects_unknown_and_read_only_concept_decisions(client: TestClient) -> None:
    subject = _subject()
    asyncio.run(_service().record_event(LearnerEvent(
        event_id="concept-readonly", event_type="quiz_answer", subject=subject,
        concept_id="derivative", concept_label="导数", observation="incorrect",
        evidence_refs=["question:2"], confidence=0.9, occurred_at="2026-07-29T00:00:00+00:00",
    ), trusted=True))

    unknown = client.patch("/api/v1/memory/learner/reflections/not-found", json={"status": "confirmed"})
    concept = client.patch("/api/v1/memory/learner/reflections/concept%3Aderivative", json={"status": "confirmed"})

    assert unknown.status_code == 404
    assert concept.status_code == 404


def test_reflection_api_subject_filter_is_scoped(client: TestClient) -> None:
    subject = _subject()
    other = SubjectRef(subject_id="physics", label="物理", path=["科学", "物理"], confidence=0.9, source="material_analysis")
    for signal_id, ref, value in [
        ("math-pref", subject, "函数题先画图"),
        ("physics-pref", other, "物理题先画受力图"),
    ]:
        _apply(LearningSignal(
            signal_id=signal_id, kind="strategy_feedback", subject_refs=[ref],
            payload={"value": value, "category": "explanation"},
            evidence_refs=[f"feedback:{signal_id}"], source="system",
            occurred_at="2026-07-29T00:00:00+00:00",
        ))

    response = client.get("/api/v1/memory/learner/reflections?subject_id=math")

    assert response.status_code == 200
    ids = {item["reflection_id"] for item in response.json()["reflections"]}
    assert ids == {"math-pref"}
