from __future__ import annotations

import pytest

from traittutor import learning_packs
from traittutor.learning.grading import grade_answer
from traittutor.services.path_service import PathService


@pytest.mark.parametrize(
    ("user_answer", "expected_answer", "question_type", "correct"),
    [
        ("正确", "正确", "TF", True),
        ("true", "正确", "TF", True),
        ("错误", "正确", "TF", False),
        ("错", "false", "TF", True),
        (" B ", "B", "OPTIONS", True),
        ("选 项 B", "选项B", "DELAY_OPTIONS", True),
        ("叶绿体", "叶绿体", "SHORT_ANSWER", True),
        ("光能", "光能", "FILL_BLANK", True),
        ("anything", "anything", "UNSUPPORTED", False),
    ],
)
def test_generated_question_types_use_deterministic_grading(
    user_answer: str,
    expected_answer: str,
    question_type: str,
    correct: bool,
) -> None:
    assert grade_answer(user_answer, expected_answer, question_type) is correct


def test_tf_repair_can_be_verified_and_scheduled(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Photosynthesis", goal="Understand photosynthesis")
    learning_packs.update_pack(
        pack["pack_id"],
        {
            "artifact": {
                "kind": "quiz",
                "verified_generation_id": "quiz-1",
                "items": [
                    {
                        "question_id": "q-1",
                        "node_id": "photosynthesis",
                        "question": "Where does photosynthesis occur?",
                        "question_type": "SHORT_ANSWER",
                        "correct_answer": "叶绿体",
                        "explanation": "叶绿体。它是光合作用的主要场所。",
                    },
                    {
                        "question_id": "q-2",
                        "node_id": "photosynthesis",
                        "question": "二氧化碳和水会在光合作用中转化为有机物。",
                        "question_type": "TF",
                        "correct_answer": "正确",
                        "explanation": "正确。该陈述符合材料。",
                        "options": [
                            {"text": "正确", "is_correct": True},
                            {"text": "错误", "is_correct": False},
                        ],
                    },
                ],
            }
        },
    )
    repair = learning_packs.create_repair(
        pack["pack_id"],
        action_id="assessment",
        question_id="q-1",
        artifact_ref="quiz-1",
        concept_id="photosynthesis",
        user_answer="线粒体",
        correct_rule="叶绿体是光合作用的主要场所。",
    )

    assert repair is not None
    assert repair["retry_question_type"] == "TF"
    assert repair["retry_expected_answer"] == "正确"
    verified = learning_packs.record_repair_retry(
        pack["pack_id"],
        repair["repair_id"],
        answer="正确",
        event_id="tf-retry-1",
    )

    assert verified is not None
    assert verified["last_retry_correct"] is True
    assert verified["status"] == "scheduled"
    refreshed = learning_packs.get_pack(pack["pack_id"])
    assert refreshed is not None
    assert refreshed["review_states"][0]["source"] == "repair"


def test_repair_defers_after_two_failures_and_reopens_after_other_concept_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn two concepts")
    plan = {
        "plan_id": "plan-recovery",
        "status": "active",
        "components": [
            {
                "component_id": "assessment-1",
                "component_type": "diagnostic_check",
                "executor": "assessment",
                "status": "active",
                "dependencies": [],
                "required": True,
            },
            {
                "component_id": "assessment-2",
                "component_type": "diagnostic_check",
                "executor": "assessment",
                "status": "active",
                "dependencies": [],
                "required": True,
            },
        ],
    }
    assert learning_packs.create_component_plan(pack["pack_id"], plan)
    repair = learning_packs.create_repair(
        pack["pack_id"],
        action_id="assessment-1",
        question_id="q-1",
        artifact_ref="quiz-1",
        concept_id="kc-1",
        user_answer="wrong",
        correct_rule="Use the verified rule.",
        retry_prompt="Apply it.",
        retry_expected_answer="right",
    )
    assert repair is not None

    first = learning_packs.record_repair_retry(
        pack["pack_id"], repair["repair_id"], answer="wrong", event_id="retry-1"
    )
    assert first is not None and first["status"] == "retrying"
    second = learning_packs.record_repair_retry(
        pack["pack_id"], repair["repair_id"], answer="still wrong", event_id="retry-2"
    )
    assert second is not None and second["status"] == "deferred"
    assert second["suggested_next_component_id"] == "assessment-2"
    with pytest.raises(learning_packs.InvalidComponentTransition, match="temporarily deferred"):
        learning_packs.record_repair_retry(
            pack["pack_id"], repair["repair_id"], answer="right", event_id="retry-3"
        )

    learning_packs.record_component_event(
        pack["pack_id"],
        "plan-recovery",
        "assessment-2",
        {
            "event_id": "other-kc-attempt",
            "action": "feedback",
            "observation": "incorrect",
            "concept_id": "kc-2",
        },
    )
    reopened = learning_packs.get_pack(pack["pack_id"])["repairs"][0]
    assert reopened["status"] == "retrying"

    repaired = learning_packs.record_repair_retry(
        pack["pack_id"], repair["repair_id"], answer="right", event_id="retry-3"
    )
    assert repaired is not None and repaired["status"] == "scheduled"
    # Idempotent replay cannot add another review task.
    learning_packs.record_repair_retry(
        pack["pack_id"], repair["repair_id"], answer="right", event_id="retry-3"
    )
    refreshed = learning_packs.get_pack(pack["pack_id"])
    assert len(refreshed["review_states"]) == 1


def test_repair_retry_replay_remains_idempotent_after_detail_projection_is_trimmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Practice safely")
    repair = learning_packs.create_repair(
        pack["pack_id"],
        action_id="assessment-1",
        question_id="q-1",
        artifact_ref="quiz-1",
        concept_id="kc-1",
        user_answer="wrong",
        correct_rule="Use the verified rule.",
        retry_prompt="Apply it.",
        retry_expected_answer="right",
    )
    assert repair is not None

    for index in range(35):
        with learning_packs._locked_packs() as packs:
            stored = next(item for item in packs if item["pack_id"] == pack["pack_id"])
            stored_repair = stored["repairs"][0]
            stored_repair["status"] = "retrying"
            learning_packs._save(packs)
        result = learning_packs.record_repair_retry(
            pack["pack_id"],
            repair["repair_id"],
            answer="wrong",
            event_id=f"retry-{index}",
        )
        assert result is not None

    before = learning_packs.get_pack(pack["pack_id"])["repairs"][0]
    assert len(before["retry_attempts"]) == 32
    assert before["retry_count"] == 35

    replay = learning_packs.record_repair_retry(
        pack["pack_id"],
        repair["repair_id"],
        answer="wrong",
        event_id="retry-0",
    )

    assert replay is not None
    assert replay["retry_count"] == 35
    assert len(replay["retry_attempts"]) == 32
