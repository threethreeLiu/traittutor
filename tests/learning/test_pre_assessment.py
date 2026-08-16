from __future__ import annotations

from pathlib import Path

import pytest

from traittutor import learning_packs
from traittutor.api.routers import learning_packs as router
from traittutor.learning_components import validate_pre_assessment_payload
from traittutor.services.path_service import PathService


@pytest.fixture
def learning_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)


def _probe(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "concept_id": "kc-1",
        "concept_label": "Fractions",
        "question": "Which value is one half?",
        "options": ["1/3", "1/2", "2/3"],
        "correct_index": 1,
        "rationale": "One divided by two is one half.",
    }
    value.update(overrides)
    return value


def test_pre_assessment_judge_parse() -> None:
    needed = validate_pre_assessment_payload(
        {"needed": True, "reason": "Evidence is limited.", "probes": [_probe()]},
        concept_ids={"kc-1"},
    )
    assert needed["needed"] is True
    assert needed["probes"][0]["correct_index"] == 1
    assert validate_pre_assessment_payload(
        {"needed": False, "reason": "Evidence is sufficient.", "probes": []},
        concept_ids={"kc-1"},
    ) == {"needed": False, "reason": "Evidence is sufficient.", "probes": []}

    invalid_payloads = [
        {"needed": True, "reason": "Probe.", "probes": [_probe(correct_index=9)]},
        {"needed": True, "reason": "Probe.", "probes": [_probe(question="")]},
        {"needed": False, "reason": "No probe.", "probes": [_probe()]},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            validate_pre_assessment_payload(payload, concept_ids={"kc-1"})


@pytest.mark.asyncio
async def test_pre_assessment_response_hides_answer_key(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = learning_packs.create_pack(title="Fractions", goal="Understand fractions")

    async def generate(_pack: object) -> dict[str, object]:
        return {"needed": True, "reason": "Probe first.", "probes": [_probe()]}

    monkeypatch.setattr(router, "judge_and_generate_pre_assessment", generate)
    response = await router.create_learning_pre_assessment(pack["pack_id"])

    serialized = str(response)
    assert response["needed"] is True
    assert "correct_index" not in serialized
    assert "rationale" not in serialized
    private = learning_packs.get_pack(pack["pack_id"])["pre_assessment"]
    assert private["probes"][0]["correct_index"] == 1
    assert private["probes"][0]["rationale"]


@pytest.mark.asyncio
async def test_pre_assessment_submit_keeps_bkt_untouched(learning_workspace: None) -> None:
    pack = learning_packs.create_pack(title="Fractions", goal="Understand fractions")
    created_at = "2026-08-14T00:00:00+00:00"
    saved = learning_packs.save_pre_assessment(
        pack["pack_id"],
        {
            "assessment_id": "pre-1",
            "status": "pending",
            "created_at": created_at,
            "updated_at": created_at,
            "probes": [{**_probe(), "question_id": "q1"}],
            "responses": [],
        },
    )
    assert saved is not None
    before = learning_packs.get_pack(pack["pack_id"])

    response = await router.submit_learning_pre_assessment(
        pack["pack_id"],
        "pre-1",
        router.SubmitPreAssessmentRequest(
            answers=[
                router.PreAssessmentAnswer(question_id="q1", selected_index=1, confidence=None)
            ],
            event_id="pre-event-1",
        ),
    )
    replay = await router.submit_learning_pre_assessment(
        pack["pack_id"],
        "pre-1",
        router.SubmitPreAssessmentRequest(
            answers=[
                router.PreAssessmentAnswer(question_id="q1", selected_index=1, confidence=None)
            ],
            event_id="pre-event-1",
        ),
    )

    after = learning_packs.get_pack(pack["pack_id"])
    assert response["results"] == [
        {
            "question_id": "q1",
            "correct": True,
            "confidence": None,
            "rationale": "One divided by two is one half.",
        }
    ]
    assert replay["idempotent_replay"] is True
    assert after["pre_assessment"]["responses"][0]["correct"] is True
    for field in ("learning_evidence", "calibrations", "repairs", "review_states"):
        assert after[field] == before[field] == []
    assert after["component_plans"] == before["component_plans"] == []


@pytest.mark.asyncio
async def test_pre_assessment_can_be_skipped_without_results(learning_workspace: None) -> None:
    pack = learning_packs.create_pack(title="Fractions")
    learning_packs.save_pre_assessment(
        pack["pack_id"],
        {
            "assessment_id": "pre-skip",
            "status": "pending",
            "created_at": "2026-08-14T00:00:00+00:00",
            "updated_at": "2026-08-14T00:00:00+00:00",
            "probes": [{**_probe(), "question_id": "q1"}],
            "responses": [],
        },
    )

    result = await router.skip_learning_pre_assessment(pack["pack_id"], "pre-skip")
    private = learning_packs.get_pack(pack["pack_id"])["pre_assessment"]
    assert result["status"] == "skipped"
    assert private["status"] == "skipped"
    assert private["responses"] == []


@pytest.mark.asyncio
async def test_pre_assessment_submit_without_confidence_field(learning_workspace: None) -> None:
    """The confidence picker was removed; a payload with no confidence key is valid."""
    pack = learning_packs.create_pack(title="Fractions")
    learning_packs.save_pre_assessment(
        pack["pack_id"],
        {
            "assessment_id": "pre-noconf",
            "status": "pending",
            "created_at": "2026-08-14T00:00:00+00:00",
            "updated_at": "2026-08-14T00:00:00+00:00",
            "probes": [{**_probe(), "question_id": "q1"}],
            "responses": [],
        },
    )

    raw = {"answers": [{"question_id": "q1", "selected_index": 1}]}
    request = router.SubmitPreAssessmentRequest.model_validate(raw)
    assert request.answers[0].confidence is None

    response = await router.submit_learning_pre_assessment(pack["pack_id"], "pre-noconf", request)
    assert response["results"][0]["correct"] is True
    assert response["results"][0]["confidence"] is None


@pytest.mark.asyncio
async def test_pre_assessment_response_has_no_confidence_scale(learning_workspace: None) -> None:
    """The public questions no longer advertise a confidence picker."""
    pack = learning_packs.create_pack(title="Fractions")
    learning_packs.save_pre_assessment(
        pack["pack_id"],
        {
            "assessment_id": "pre-noscale",
            "status": "pending",
            "created_at": "2026-08-14T00:00:00+00:00",
            "updated_at": "2026-08-14T00:00:00+00:00",
            "probes": [{**_probe(), "question_id": "q1"}],
            "responses": [],
        },
    )

    response = await router.create_learning_pre_assessment(pack["pack_id"])
    assert "confidence_scale" not in response["questions"][0]
