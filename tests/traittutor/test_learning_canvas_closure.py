import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from traittutor import learning_packs
from traittutor.api.routers.learning_packs import (
    ComponentInteractionRequest,
    ReviewResultRequest,
    _assessment_attempt_views,
    _learner_due_reviews,
    _learner_pack,
    _record_component_learning_event,
    _record_pack_learning_events,
    _repair_retry_item,
    _verified_assessment_concept,
    _verified_assessment_observation,
    record_learning_component_event,
    record_learning_review_result,
    reveal_learning_review_answer,
)
from traittutor.learning_components import (
    LearningComponentSelector,
    MaterialAffordance,
    MaterialComponentAffordances,
    SubjectSupportState,
)
from traittutor.learning_model.events import LearnerEvent, LearnerEventLedger
from traittutor.services.path_service import PathService


def test_generated_interactive_output_ref_is_durable_before_submission(monkeypatch, tmp_path):
    """A refresh can restore an assessment/retrieval artifact before an answer."""
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn equations")
    plan = {
        "plan_id": "plan-1",
        "status": "active",
        "components": [
            {
                "component_id": "assessment-1",
                "component_type": "diagnostic_check",
                "executor": "assessment",
                "status": "active",
                "dependencies": [],
                "required": True,
            }
        ],
    }
    assert learning_packs.create_component_plan(pack["pack_id"], plan)

    recorded = learning_packs.record_component_event(
        pack["pack_id"],
        "plan-1",
        "assessment-1",
        {"event_id": "generated-quiz-1", "action": "feedback", "output_ref": "quiz-generation-1"},
    )

    assert recorded is not None
    restored = learning_packs.get_component_plan(pack["pack_id"], "plan-1")
    assert restored is not None
    assert restored["components"][0]["output_ref"] == "quiz-generation-1"
    assert restored["components"][0]["status"] == "active"


def test_attempt_projection_reveals_answer_only_for_exact_submitted_artifact() -> None:
    pack = {
        "component_plans": [
            {
                "plan_id": "plan-1",
                "components": [{"component_id": "assessment-1", "output_ref": "quiz-1"}],
            }
        ],
        "component_progress": {
            "plan-1": {
                "events": [
                    {
                        "event_id": "attempt-1",
                        "component_id": "assessment-1",
                        "question_id": "q-1",
                        "output_ref": "quiz-1",
                        "answer": "Learner answer",
                        "confidence": 0.65,
                        "observation": "incorrect",
                        "occurred_at": "2026-08-13T10:00:00+00:00",
                    }
                ]
            }
        },
        "artifacts": {
            "quiz": [
                {
                    "verified_generation_id": "quiz-1",
                    "items": [
                        {
                            "question_id": "q-1",
                            "correct_answer": "Reference answer",
                            "explanation": "Server explanation",
                        }
                    ],
                }
            ]
        },
    }

    attempts = _assessment_attempt_views(pack, "plan-1")
    assert len(attempts) == 1
    assert attempts[0].user_answer == "Learner answer"
    assert attempts[0].reference_answer == "Reference answer"
    assert attempts[0].explanation == "Server explanation"
    assert attempts[0].read_only is True

    # A stale/mismatched output still restores the historical learner verdict,
    # but never releases an answer key from another generated result.
    pack["component_progress"]["plan-1"]["events"][0]["output_ref"] = "quiz-other"
    unavailable = _assessment_attempt_views(pack, "plan-1")[0]
    assert unavailable.user_answer == "Learner answer"
    assert unavailable.correct is False
    assert unavailable.reference_answer is None
    assert unavailable.explanation is None
    assert unavailable.historical_explanation_available is False


def test_attempt_projection_restores_preserved_component_across_replan() -> None:
    pack = {
        "component_plans": [
            {
                "plan_id": "plan-1",
                "components": [{"component_id": "assessment-1", "output_ref": "quiz-1"}],
            },
            {
                "plan_id": "plan-2",
                "supersedes_plan_id": "plan-1",
                "components": [{"component_id": "assessment-1", "output_ref": "quiz-1"}],
            },
        ],
        "component_progress": {
            "plan-1": {
                "events": [
                    {
                        "event_id": "attempt-1",
                        "component_id": "assessment-1",
                        "question_id": "q-1",
                        "output_ref": "quiz-1",
                        "answer": "B",
                        "observation": "correct",
                        "occurred_at": "2026-08-13T10:00:00+00:00",
                    }
                ]
            },
            "plan-2": {"events": []},
        },
        "artifacts": {
            "quiz": [
                {
                    "verified_generation_id": "quiz-1",
                    "items": [
                        {
                            "question_id": "q-1",
                            "correct_answer": "B",
                            "explanation": "Because B follows from the source.",
                        }
                    ],
                }
            ]
        },
    }

    attempts = _assessment_attempt_views(pack, "plan-2")

    assert [(item.attempt_id, item.user_answer) for item in attempts] == [("attempt-1", "B")]
    assert attempts[0].reference_answer == "B"


def test_retrieval_self_rating_stays_out_of_bkt():
    """ "Known" on a flashcard is participation, never trusted mastery evidence."""
    request = ComponentInteractionRequest(
        action="complete", observation="known", output_ref="cards-1"
    )
    updated = asyncio.run(
        _record_component_learning_event(
            {"pack_id": "pack-1", "updated_at": "2026-01-01T00:00:00+00:00"},
            {
                "plan_id": "plan-1",
                "subject_ref": {
                    "subject_id": "math",
                    "label": "Math",
                    "confidence": 1,
                    "source": "user",
                },
            },
            {
                "component_id": "retrieval-1",
                "component_type": "retrieval_card",
                "concept_refs": ["equations"],
            },
            request,
            "event-1",
        )
    )

    assert updated is False


def test_diagnostic_judgement_stays_out_of_bkt():
    """A server-graded diagnostic is local feedback, not mastery evidence."""
    updated = asyncio.run(
        _record_component_learning_event(
            {"pack_id": "pack-1", "updated_at": "2026-01-01T00:00:00+00:00"},
            {
                "plan_id": "plan-1",
                "subject_ref": {
                    "subject_id": "math",
                    "label": "Math",
                    "confidence": 1,
                    "source": "user",
                },
            },
            {
                "component_id": "diagnostic-1",
                "component_type": "diagnostic_check",
                "concept_refs": ["equations"],
            },
            ComponentInteractionRequest(
                action="complete",
                observation="correct",
                confidence=0.9,
                question_id="q-1",
                answer="B",
                output_ref="quiz-diagnostic",
            ),
            "diagnostic-event-1",
        )
    )

    assert updated is False


def test_diagnostic_submission_creates_no_repair_calibration_or_plan(monkeypatch):
    pack = {
        "pack_id": "pack-1",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "artifacts": {
            "quiz": [
                {
                    "verified_generation_id": "quiz-diagnostic",
                    "items": [
                        {
                            "question_id": "q-1",
                            "node_id": "equations",
                            "node_name": "Equations",
                            "correct_answer": "B",
                            "explanation": "B is the source-grounded judgement.",
                        }
                    ],
                }
            ]
        },
    }
    component = {
        "component_id": "diagnostic-1",
        "component_type": "diagnostic_check",
        "executor": "assessment",
        "status": "active",
        "output_ref": "quiz-diagnostic",
        "concept_refs": ["equations"],
    }
    plan = {"plan_id": "plan-1", "subject_ref": None, "components": [component]}
    completed = {**component, "status": "completed"}
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_pack", lambda _: pack
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_component_plan",
        lambda *_: plan,
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.record_component_event",
        lambda *_args, **kwargs: (
            kwargs["before_mutation"](pack, plan, component) or (pack, completed)
        ),
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.record_calibration",
        lambda *_args: pytest.fail("diagnostic must not create calibration"),
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.create_repair",
        lambda *_args, **_kwargs: pytest.fail("diagnostic must not create repair"),
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs._build_component_plan",
        lambda *_args, **_kwargs: pytest.fail("diagnostic must not create another plan"),
    )

    response = asyncio.run(
        record_learning_component_event(
            "pack-1",
            "plan-1",
            "diagnostic-1",
            ComponentInteractionRequest(
                action="complete",
                answer="A",
                confidence=0.65,
                question_id="q-1",
                output_ref="quiz-diagnostic",
                replan=True,
            ),
        )
    )

    assert response["learner_state_updated"] is False
    assert response["calibration"] is None
    assert response["created_repair_id"] is None
    assert response["replanned_plan"] is None
    assert response["verified_observation"] == "incorrect"
    assert response["verified_feedback"] == "B is the source-grounded judgement."


def test_flashcard_self_rating_stays_out_of_bkt():
    """Standalone-tool progress is also self-report, not a graded result."""
    asyncio.run(
        _record_pack_learning_events(
            {"pack_id": "pack-1", "updated_at": "2026-01-01T00:00:00+00:00", "artifacts": {}},
            {"flashcard_progress": {"equations": "known"}, "review_id": "review-1"},
        )
    )


def test_short_answer_uses_server_artifact_not_client_claim():
    """A browser cannot mark an incorrect short answer as correct itself."""
    pack = {
        "artifacts": {
            "quiz": [
                {
                    "verified_generation_id": "quiz-1",
                    "items": [
                        {
                            "question_id": "q-1",
                            "question_type": "short",
                            "correct_answer": "x = 4",
                        }
                    ],
                }
            ],
        },
    }
    request = ComponentInteractionRequest(
        action="complete",
        observation="correct",
        output_ref="quiz-1",
        question_id="q-1",
        answer="x = 3",
    )

    assert _verified_assessment_observation(pack, request) == "incorrect"


def test_repair_review_preserves_choice_grading_contract(monkeypatch):
    pack = {
        "pack_id": "pack-1",
        "review_states": [{"review_id": "review-repair-r1", "source": "repair"}],
        "repairs": [
            {
                "repair_id": "r1",
                "retry_expected_answer": "B",
                "retry_question_type": "choice",
            }
        ],
    }
    captured = {}
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_pack", lambda _pack_id: pack
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.update_review_result",
        lambda _pack_id, _review_id, *, correct, event_id, before_schedule=None: (
            captured.setdefault(
                "review",
                {"due_at": "later", "correct": correct, "event_id": event_id},
            )
        ),
    )
    result = asyncio.run(
        record_learning_review_result(
            "pack-1",
            "review-repair-r1",
            ReviewResultRequest(event_id="review-event-1", answer="B"),
        )
    )
    assert result["correct"] is True
    assert captured["review"]["correct"] is True
    assert captured["review"]["event_id"] == "review-event-1"


def test_due_repair_review_exposes_prompt_without_answer_key():
    pack = {
        "review_states": [
            {
                "review_id": "review-repair-r1",
                "source": "repair",
                "due_at": "2020-01-01T00:00:00+00:00",
            }
        ],
        "repairs": [
            {
                "repair_id": "r1",
                "retry_prompt": "Choose again",
                "retry_expected_answer": "B",
                "retry_question_type": "choice",
                "retry_options": [{"key": "A", "text": "One"}, {"key": "B", "text": "Two"}],
            }
        ],
    }
    items = _learner_due_reviews(pack)
    assert items[0]["prompt"] == "Choose again"
    assert items[0]["options"][1]["key"] == "B"
    assert "retry_expected_answer" not in items[0]


def test_due_retrieval_review_exposes_prompt_without_answer_key():
    pack = {
        "review_states": [
            {
                "review_id": "review-retrieval-1",
                "source": "retrieval",
                "concept_id": "concept-1",
                "due_at": "2020-01-01T00:00:00+00:00",
            }
        ],
        "artifacts": {
            "flashcards": [
                {
                    "items": [
                        {
                            "node_id": "concept-1",
                            "front": "What is the rule?",
                            "back": "The server-held answer.",
                        }
                    ]
                }
            ]
        },
    }

    items = _learner_due_reviews(pack)

    assert items[0]["prompt"] == "What is the rule?"
    assert "answer" not in items[0]
    assert "back" not in items[0]


def test_learning_pack_projection_removes_flashcard_answers():
    public = _learner_pack(
        {
            "artifacts": {
                "flashcards": [
                    {
                        "items": [
                            {
                                "node_id": "concept-1",
                                "front": "What is the rule?",
                                "back": "The server-held answer.",
                            }
                        ]
                    }
                ],
                "quiz": [],
            },
            "review_states": [],
        }
    )

    assert public["artifacts"]["flashcards"][0]["items"] == [
        {"node_id": "concept-1", "front": "What is the rule?"}
    ]


def test_due_retrieval_review_reveals_only_the_requested_answer(monkeypatch):
    pack = {
        "review_states": [
            {
                "review_id": "review-retrieval-1",
                "source": "retrieval",
                "concept_id": "concept-1",
                "due_at": "2020-01-01T00:00:00+00:00",
                "priority": 1,
            }
        ],
        "artifacts": {
            "flashcards": [
                {
                    "items": [
                        {
                            "node_id": "concept-1",
                            "front": "What is the rule?",
                            "back": "The server-held answer.",
                        }
                    ]
                }
            ]
        },
    }
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_pack", lambda _pack_id: pack
    )

    response = asyncio.run(reveal_learning_review_answer("pack-1", "review-retrieval-1"))

    assert response == {
        "review_id": "review-retrieval-1",
        "answer": "The server-held answer.",
    }


def test_assessment_concept_comes_from_server_artifact_not_browser():
    pack = {
        "artifacts": {
            "quiz": [
                {
                    "verified_generation_id": "quiz-1",
                    "items": [
                        {
                            "question_id": "q-1",
                            "node_id": "linear-equations",
                            "node_name": "Linear equations",
                        }
                    ],
                }
            ]
        }
    }
    request = ComponentInteractionRequest(
        action="complete",
        output_ref="quiz-1",
        question_id="q-1",
        answer="anything",
        concept_id="browser-chosen",
        concept_label="Browser chosen",
    )
    assert _verified_assessment_concept(
        pack, {"component_id": "assessment", "concept_refs": ["fallback"]}, request
    ) == (
        "linear-equations",
        "Linear equations",
    )


def test_component_event_replay_is_marked_without_second_state_transition(monkeypatch, tmp_path):
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn equations")
    plan = {
        "plan_id": "plan",
        "status": "active",
        "components": [
            {
                "component_id": "step",
                "component_type": "retrieval_card",
                "status": "active",
                "dependencies": [],
                "required": True,
            }
        ],
    }
    assert learning_packs.create_component_plan(pack["pack_id"], plan)
    event = {"event_id": "retry-safe", "action": "feedback"}
    assert learning_packs.record_component_event(pack["pack_id"], "plan", "step", event)
    replay = {"event_id": "retry-safe", "action": "feedback"}
    assert learning_packs.record_component_event(pack["pack_id"], "plan", "step", replay)
    assert replay["_idempotent_replay"] is True
    with pytest.raises(learning_packs.InvalidComponentTransition, match="different request"):
        learning_packs.record_component_event(
            pack["pack_id"],
            "plan",
            "step",
            {"event_id": "retry-safe", "action": "feedback", "feedback": "changed"},
        )


def test_final_incorrect_submission_completes_without_creating_another_plan(monkeypatch, tmp_path):
    """A final incorrect answer completes workflow without claiming mastery."""
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn equations")
    assert learning_packs.update_pack(
        pack["pack_id"],
        {
            "artifact": {
                "kind": "quiz",
                "verified_generation_id": "revealed-quiz",
                "items": [
                    {"question_id": "q-1"},
                    {"question_id": "q-2"},
                ],
            }
        },
    )
    plan = {
        "plan_id": "plan-original",
        "status": "active",
        "components": [
            {
                "component_id": "assessment-1",
                "component_type": "guided_practice",
                "executor": "assessment",
                "status": "active",
                "dependencies": [],
                "required": True,
                "output_ref": "revealed-quiz",
            }
        ],
    }
    assert learning_packs.create_component_plan(pack["pack_id"], plan)
    for index, observation in enumerate(("correct", "incorrect"), start=1):
        assert learning_packs.record_component_event(
            pack["pack_id"],
            "plan-original",
            "assessment-1",
            {
                "event_id": f"attempt-{index}",
                "action": "complete" if index == 2 else "feedback",
                "observation": observation,
                "question_id": f"q-{index}",
                "answer": "answer",
                "output_ref": "revealed-quiz",
                "_server_graded": True,
            },
        )

    refreshed = learning_packs.get_pack(pack["pack_id"])
    assert refreshed is not None
    assert refreshed["active_plan_id"] == "plan-original"
    assert len(refreshed["component_plans"]) == 1
    assert refreshed["component_plans"][0]["components"][0]["status"] == "completed"


def test_component_event_validation_and_canonical_append_share_pack_lock(monkeypatch, tmp_path):
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn equations")
    plan = {
        "plan_id": "plan-atomic",
        "status": "active",
        "components": [
            {
                "component_id": "lesson-1",
                "component_type": "concept_explanation",
                "executor": "content",
                "status": "active",
                "dependencies": [],
                "required": True,
            }
        ],
    }
    assert learning_packs.create_component_plan(pack["pack_id"], plan)
    appended: list[str] = []

    def submit(event_id: str) -> str:
        try:
            learning_packs.record_component_event(
                pack["pack_id"],
                "plan-atomic",
                "lesson-1",
                {"event_id": event_id, "action": "complete"},
                before_mutation=lambda *_locked: appended.append(event_id),
            )
        except learning_packs.InvalidComponentTransition:
            return "rejected"
        return "recorded"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, ("event-a", "event-b")))

    assert sorted(results) == ["recorded", "rejected"]
    assert len(appended) == 1


def test_component_and_canonical_event_rollback_together(monkeypatch, tmp_path):
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn equations")
    plan = {
        "plan_id": "plan-atomic-rollback",
        "status": "active",
        "components": [
            {
                "component_id": "lesson-1",
                "component_type": "concept_explanation",
                "executor": "content",
                "status": "active",
                "dependencies": [],
                "required": True,
            }
        ],
    }
    assert learning_packs.create_component_plan(pack["pack_id"], plan)
    ledger_path = service.get_workspace_dir() / "learning_model" / "learner_events.json"
    ledger = LearnerEventLedger(ledger_path, path_service=service)

    def append_then_fail(*_locked: object) -> None:
        ledger.append(
            LearnerEvent(
                event_id="canonical-rollback",
                idempotency_key="canonical-rollback",
                user_id="local-admin",
                subject_id=None,
                surface_type="reading",
                answer_correct=None,
                evidence_strength="exposure",
                created_at="2026-08-14T00:00:00+00:00",
            )
        )
        raise RuntimeError("stop projection")

    with pytest.raises(RuntimeError, match="stop projection"):
        learning_packs.record_component_event(
            pack["pack_id"],
            "plan-atomic-rollback",
            "lesson-1",
            {"event_id": "component-rollback", "action": "complete"},
            before_mutation=append_then_fail,
        )

    refreshed = learning_packs.get_pack(pack["pack_id"])
    refreshed_component = refreshed["component_plans"][0]["components"][0]
    assert refreshed_component["status"] == "active"
    assert refreshed["component_progress"]["plan-atomic-rollback"]["events"] == []
    assert LearnerEventLedger(ledger_path, path_service=service).get("canonical-rollback") is None


@pytest.mark.parametrize("task_status", ["completed", "needs_review"])
def test_assessment_output_reference_can_persist_before_an_answer(monkeypatch, task_status):
    """The API accepts only the narrow non-grading persistence event."""
    pack = {
        "pack_id": "pack-1",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "artifacts": {
            "quiz": (
                [{"verified_generation_id": "generation-1", "items": []}]
                if task_status == "completed"
                else []
            )
        },
    }
    component = {
        "component_id": "assessment-1",
        "component_type": "diagnostic_check",
        "status": "active",
        "concept_refs": ["equations"],
    }
    plan = {"plan_id": "plan-1", "subject_ref": None, "components": [component]}
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_pack", lambda _: pack
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_component_plan", lambda *_: plan
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.record_component_event",
        lambda *_args, **kwargs: (
            kwargs["before_mutation"](pack, plan, component),
            captured.update(event=_args[-1]),
            (pack, component),
        )[-1],
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.validate_component_event",
        lambda *_args: (pack, component),
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.get_generation_task_manager",
        lambda: SimpleNamespace(get=lambda _: SimpleNamespace(status=task_status)),
    )

    response = asyncio.run(
        record_learning_component_event(
            "pack-1",
            "plan-1",
            "assessment-1",
            ComponentInteractionRequest(action="feedback", output_ref="generation-1", replan=False),
        )
    )

    assert response["learner_state_updated"] is False
    assert response["verified_observation"] is None
    assert captured["event"]["output_ref"] == "generation-1"
    assert captured["event"].get("observation") is None


def test_completed_assessment_output_requires_attached_quiz(monkeypatch):
    pack = {"pack_id": "pack-1", "artifacts": {"quiz": []}}
    component = {
        "component_id": "assessment-1",
        "component_type": "guided_practice",
        "status": "active",
    }
    plan = {"plan_id": "plan-1", "subject_ref": None, "components": [component]}
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_pack", lambda _: pack
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_component_plan", lambda *_: plan
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.get_generation_task_manager",
        lambda: SimpleNamespace(get=lambda _: SimpleNamespace(status="completed")),
    )

    with pytest.raises(HTTPException, match="Attach the completed Quiz artifact"):
        asyncio.run(
            record_learning_component_event(
                "pack-1",
                "plan-1",
                "assessment-1",
                ComponentInteractionRequest(
                    action="feedback", output_ref="generation-1", replan=False
                ),
            )
        )


def test_attaching_the_same_generated_artifact_is_idempotent(monkeypatch, tmp_path):
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr("traittutor.learning_packs.get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Algebra", goal="Learn equations")
    artifact = {
        "kind": "quiz",
        "verified_generation_id": "generation-1",
        "items": [],
    }

    assert learning_packs.update_pack(pack["pack_id"], {"artifact": artifact})
    updated = learning_packs.update_pack(pack["pack_id"], {"artifact": artifact})

    assert updated is not None
    assert len(updated["artifacts"]["quiz"]) == 1


def test_assessment_submission_requires_a_confidence_prediction(monkeypatch):
    pack = {"pack_id": "pack-1", "artifacts": {"quiz": []}}
    component = {
        "component_id": "assessment-1",
        "component_type": "guided_practice",
        "status": "active",
        "concept_refs": ["equations"],
    }
    plan = {"plan_id": "plan-1", "subject_ref": None, "components": [component]}
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_pack", lambda _: pack
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_component_plan", lambda *_: plan
    )

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            record_learning_component_event(
                "pack-1",
                "plan-1",
                "assessment-1",
                ComponentInteractionRequest(
                    action="complete",
                    question_id="q-1",
                    answer="x=3",
                    output_ref="quiz-1",
                ),
            )
        )

    assert getattr(raised.value, "status_code", None) == 422
    assert "confidence" in str(getattr(raised.value, "detail", "")).lower()


def test_new_pack_starts_with_llm_goal_map_and_keeps_dependencies_local():
    plan = LearningComponentSelector().select(
        pack_id="pack-1",
        goal="Learn equations",
        subject_ref=None,
        analysis_id=None,
        concept_signals=[],
        support_state=SubjectSupportState(),
        affordances=MaterialComponentAffordances(
            visual=MaterialAffordance(suitable=False, confidence=0.2),
            audio=MaterialAffordance(suitable=False, confidence=0.2),
            worked_example=MaterialAffordance(suitable=False, confidence=0.2),
            practice=MaterialAffordance(suitable=True, confidence=0.8),
        ),
    )
    types = [item.component_type for item in plan.components]
    assert types[:3] == ["goal_map", "concept_explanation", "diagnostic_check"]
    goal_map = plan.components[0]
    diagnostic = plan.components[2]
    assert goal_map.executor == "lesson"
    assert diagnostic.required is False
    assert diagnostic.dependencies == []
    practice = types.index("guided_practice")
    assert types[practice + 1] == "calibration_checkpoint"
    assert plan.components[practice].dependencies == []
    assert plan.components[practice + 1].dependencies == [plan.components[practice].component_id]
    assert all(
        not component.dependencies
        for index, component in enumerate(plan.components)
        if index != practice + 1
    )


def test_goal_only_plan_teaches_before_it_checks_understanding():
    plan = LearningComponentSelector().select(
        pack_id="pack-goal-only",
        goal="I want to learn Einstein's mass-energy equivalence",
        subject_ref=None,
        analysis_id=None,
        concept_signals=[],
        support_state=SubjectSupportState(),
        affordances=MaterialComponentAffordances(
            visual=MaterialAffordance(suitable=False, confidence=0.2),
            audio=MaterialAffordance(suitable=False, confidence=0.2),
            worked_example=MaterialAffordance(suitable=False, confidence=0.2),
            practice=MaterialAffordance(suitable=True, confidence=0.8),
        ),
        goal_only=True,
    )

    types = [item.component_type for item in plan.components]
    assert types[0] == "goal_map"
    assert types.index("concept_explanation") < types.index("diagnostic_check")
    assert types.index("concept_explanation") < types.index("guided_practice")
    assert types[types.index("guided_practice") + 1] == "calibration_checkpoint"


def test_plan_always_offers_one_optional_source_grounded_visual():
    plan = LearningComponentSelector().select(
        pack_id="pack-visual",
        goal="Understand a university course concept",
        subject_ref=None,
        analysis_id=None,
        concept_signals=[],
        support_state=SubjectSupportState(),
        affordances=MaterialComponentAffordances(
            visual=MaterialAffordance(suitable=False, confidence=0.1),
            audio=MaterialAffordance(suitable=False, confidence=0.1),
            worked_example=MaterialAffordance(suitable=False, confidence=0.1),
            practice=MaterialAffordance(suitable=False, confidence=0.1),
        ),
    )

    visuals = [item for item in plan.components if item.component_type == "visual_map"]
    assert len(visuals) == 1
    assert visuals[0].required is False
    assert "source-grounded" in visuals[0].reason


def test_plan_always_offers_one_optional_source_grounded_video():
    plan = LearningComponentSelector().select(
        pack_id="pack-video",
        goal="Understand a university course concept",
        subject_ref=None,
        analysis_id=None,
        concept_signals=[],
        support_state=SubjectSupportState(),
        affordances=MaterialComponentAffordances(
            visual=MaterialAffordance(suitable=False, confidence=0.1),
            audio=MaterialAffordance(suitable=False, confidence=0.1),
            worked_example=MaterialAffordance(suitable=False, confidence=0.1),
            practice=MaterialAffordance(suitable=False, confidence=0.1),
        ),
    )

    videos = [item for item in plan.components if item.component_type == "video_explanation"]
    assert len(videos) == 1
    assert videos[0].required is False
    assert videos[0].executor == "video"
    assert videos[0].modality == "video"
    assert "source-grounded" in videos[0].reason


def test_plan_always_offers_one_optional_source_grounded_podcast():
    plan = LearningComponentSelector().select(
        pack_id="pack-podcast",
        goal="Understand a university course concept",
        subject_ref=None,
        analysis_id=None,
        concept_signals=[],
        support_state=SubjectSupportState(),
        affordances=MaterialComponentAffordances(
            visual=MaterialAffordance(suitable=False, confidence=0.1),
            audio=MaterialAffordance(suitable=False, confidence=0.1),
            worked_example=MaterialAffordance(suitable=False, confidence=0.1),
            practice=MaterialAffordance(suitable=False, confidence=0.1),
        ),
    )

    podcasts = [item for item in plan.components if item.component_type == "audio_explanation"]
    assert len(podcasts) == 1
    assert podcasts[0].required is False
    assert podcasts[0].executor == "audio"
    assert podcasts[0].modality == "audio"
    assert "podcast" in podcasts[0].reason


def test_diagnostic_judgement_does_not_replace_evidence_practice():
    plan = LearningComponentSelector().select(
        pack_id="pack-1",
        goal="Learn equations",
        subject_ref=None,
        analysis_id=None,
        concept_signals=[{"concept_id": "equations", "mastery_probability": 0.5}],
        support_state=SubjectSupportState(),
        affordances=MaterialComponentAffordances(
            visual=MaterialAffordance(suitable=False, confidence=0.2),
            audio=MaterialAffordance(suitable=False, confidence=0.2),
            worked_example=MaterialAffordance(suitable=False, confidence=0.2),
            practice=MaterialAffordance(suitable=True, confidence=0.8),
        ),
    )
    types = [item.component_type for item in plan.components]
    assert types.count("diagnostic_check") == 1
    assert types.count("guided_practice") == 1
    assert types.count("calibration_checkpoint") == 1


def _calibration_endpoint_fixture(
    monkeypatch, *, verified_events: list[dict]
) -> tuple[dict, dict, dict]:
    """Shared fixture for the calibration-completion endpoint path."""
    component = {
        "component_id": "calibration-1",
        "component_type": "calibration_checkpoint",
        "executor": "deterministic",
        "label_zh": "校准复盘",
        "label_en": "Calibration checkpoint",
        "concept_refs": ["equations"],
        "support_dimensions": ["monitoring_regulation"],
        "bkt_stage": "developing",
        "modality": "text",
        "dependencies": [],
        "required": True,
        "reason": "Compare confidence with verified feedback.",
        "evidence_refs": [],
        "completion_event": "self_assessment",
        "status": "active",
    }
    plan = {
        "plan_id": "plan-1",
        "pack_id": "pack-1",
        "version": 1,
        "goal": "Learn equations",
        "subject_ref": None,
        "support_state_snapshot": {"source": "default"},
        "arrangement": "llm",
        "components": [component],
        "status": "active",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    pack = {
        "pack_id": "pack-1",
        "goal": {"text": "Learn equations"},
        "updated_at": "now",
        "calibrations": [],
        "component_progress": {"plan-1": {"events": [dict(item) for item in verified_events]}},
    }
    created = {}
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_pack", lambda _pack_id: pack
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.get_component_plan",
        lambda *_args: plan,
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.record_component_event",
        lambda *_args, **kwargs: (
            kwargs["before_mutation"](pack, plan, component) or (pack, component)
        ),
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.validate_component_event",
        lambda *_args: (pack, component),
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.save_progress_calibration",
        lambda _pack_id, record: (
            pack.setdefault("progress_calibrations", []).append(record) or record
        ),
    )
    monkeypatch.setattr(
        "traittutor.api.routers.learning_packs.learning_packs.create_component_plan",
        lambda _pack_id, payload: created.update(payload=payload) or payload,
    )
    return pack, plan, created


def test_calibration_complete_creates_progress_calibration_and_followup_plan(monkeypatch):
    """Completing a calibration aggregates verified evidence into a difficulty
    evaluation and a follow-up plan that inserts the named supports — without
    becoming mastery evidence."""
    pack, plan, created = _calibration_endpoint_fixture(
        monkeypatch,
        verified_events=[
            {"action": "complete", "observation": "correct", "concept_id": "equations"},
            {"action": "complete", "observation": "incorrect", "concept_id": "equations"},
            {"action": "complete", "observation": "correct", "concept_id": "equations"},
            {"action": "complete", "observation": "incorrect", "concept_id": "equations"},
        ],
    )
    del plan

    response = asyncio.run(
        record_learning_component_event(
            "pack-1",
            "plan-1",
            "calibration-1",
            ComponentInteractionRequest(action="complete", replan=True),
        )
    )

    assert response["learner_state_updated"] is False
    calibration = response["progress_calibration"]
    assert calibration is not None
    assert calibration["verified_observations"] == 4
    assert calibration["correct_count"] == 2
    assert calibration["difficulty"] == "needs_support"
    assert calibration["recommended_strategy"] == "worked_example_then_guided_retry"
    assert [item["kc_id"] for item in calibration["kc_summaries"]] == ["equations"]
    # the follow-up plan preserved the started prefix, inserted the missing
    # supports after the calibration, and kept the llm arrangement state
    replanned = response["replanned_plan"]
    assert replanned["plan_id"] == created["payload"]["plan_id"]
    assert created["payload"]["supersedes_plan_id"] == "plan-1"
    assert created["payload"]["arrangement"] == "llm"
    types = [item["component_type"] for item in created["payload"]["components"]]
    assert types == [
        "calibration_checkpoint",
        "worked_example",
        "guided_practice",
    ]
    assert pack["progress_calibrations"][-1]["difficulty"] == "needs_support"


def test_calibration_complete_with_insufficient_evidence_changes_no_plan(monkeypatch):
    """Below the minimum-observation gate the calibration reports insufficient
    evidence and the plan stays untouched (no fabricated difficulty)."""
    pack, _plan, created = _calibration_endpoint_fixture(monkeypatch, verified_events=[])

    response = asyncio.run(
        record_learning_component_event(
            "pack-1",
            "plan-1",
            "calibration-1",
            ComponentInteractionRequest(action="complete", replan=True),
        )
    )

    calibration = response["progress_calibration"]
    assert calibration is not None
    assert calibration["verified_observations"] == 0
    assert calibration["difficulty"] is None
    assert calibration["recommended_strategy"] is None
    assert "insufficient evidence" in calibration["difficulty_reason"]
    assert response["replanned_plan"] is None
    assert not created
    assert pack["progress_calibrations"][-1]["difficulty"] is None


def test_browser_confidence_cannot_create_canonical_evidence():
    request = ComponentInteractionRequest(
        action="complete",
        observation="correct",
        confidence=0.35,
        concept_id="equations",
        concept_label="Equations",
    )
    updated = asyncio.run(
        _record_component_learning_event(
            {"pack_id": "pack-1", "updated_at": "2026-01-01T00:00:00+00:00"},
            {
                "plan_id": "plan-1",
                "subject_ref": {
                    "subject_id": "math",
                    "label": "Math",
                    "confidence": 1,
                    "source": "user",
                },
            },
            {
                "component_id": "assessment-1",
                "component_type": "guided_practice",
                "concept_refs": ["equations"],
            },
            request,
            "event-1",
        )
    )

    assert updated is False


def test_learner_pack_hides_repair_key_and_uses_server_due_summary():
    pack = {
        "artifacts": {
            "quiz": [
                {
                    "items": [
                        {
                            "question_id": "q-1",
                            "correct_answer": "B",
                            "is_correct": True,
                            "explanation": "B is the answer",
                            "options": [
                                {"key": "A", "text": "A", "is_correct": False},
                                {"key": "B", "text": "B", "is_correct": True},
                            ],
                        }
                    ]
                }
            ]
        },
        "repairs": [
            {
                "repair_id": "repair-1",
                "retry_expected_answer": "secret",
                "user_answer": "wrong",
                "correct_rule": "private correction",
                "contrast": "private contrast",
                "retry_prompt": "private retry prompt",
                "retry_options": [{"key": "A", "text": "private option"}],
                "retry_event_receipts": {"old-event": {"answer_fingerprint": "secret"}},
            }
        ],
        "review_states": [
            {
                "review_id": "review-1",
                "due_at": "2020-01-01T00:00:00+00:00",
                "priority": 1,
            }
        ],
    }
    public = _learner_pack(pack)
    public_item = public["artifacts"]["quiz"][0]["items"][0]
    assert "correct_answer" not in public_item
    assert "is_correct" not in public_item
    assert "explanation" not in public_item
    assert all("is_correct" not in option for option in public_item["options"])
    assert "retry_expected_answer" not in public["repairs"][0]
    assert {
        "user_answer",
        "correct_rule",
        "contrast",
        "retry_prompt",
        "retry_options",
        "retry_event_receipts",
    }.isdisjoint(public["repairs"][0])
    assert public["due_review_count"] == 1
    assert public["next_review_at"] == "2020-01-01T00:00:00+00:00"
    assert pack["repairs"][0]["retry_expected_answer"] == "secret"
    assert pack["artifacts"]["quiz"][0]["items"][0]["correct_answer"] == "B"


def test_repair_retry_uses_a_distinct_server_owned_item():
    original = {
        "question_id": "q-1",
        "node_id": "equations",
        "question": "Solve x+1=3",
        "correct_answer": "2",
    }
    near = {
        "question_id": "q-2",
        "node_id": "equations",
        "question": "Solve x+2=5",
        "correct_answer": "3",
    }
    assert _repair_retry_item({"items": [original, near]}, original) == near
