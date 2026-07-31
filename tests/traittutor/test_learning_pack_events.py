from __future__ import annotations

from types import SimpleNamespace

import pytest

from traittutor import learning_packs
from traittutor.api.routers.learning_packs import _record_pack_learning_events
from traittutor.personalization.service import PersonalizationService


@pytest.fixture
def learner_store(tmp_path, monkeypatch):
    from traittutor.personalization import service as service_module
    from traittutor.services import path_service

    active_user = SimpleNamespace(id="learner-a")
    monkeypatch.setattr(service_module.memory_paths, "memory_root", lambda: tmp_path / "memory")
    monkeypatch.setattr(service_module, "get_current_user", lambda: active_user)
    monkeypatch.setattr(path_service.get_path_service(), "get_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(learning_packs, "get_path_service", path_service.get_path_service)
    return PersonalizationService()


def _pack() -> dict:
    return {
        "pack_id": "pack-a",
        "title": "函数学习包",
        "material": {
            "title": "函数.pdf",
            "text": "一次函数与斜率",
            "metadata": {
                "learner_analysis": {
                    "subject": "mathematics",
                    "sub_subject": "linear functions",
                    "confidence": 0.9,
                }
            },
        },
        "artifacts": {
            "courseware": [],
            "flashcards": [
                {
                    "verified_generation_id": "gen-cards",
                    "items": [
                        {"node_id": "chunk-1", "node_name": "斜率", "front": "斜率是什么？"},
                        {"node_id": "chunk-2", "node_name": "截距", "front": "截距是什么？"},
                    ],
                }
            ],
            "quiz": [
                {
                    "verified_generation_id": "gen-quiz",
                    "items": [
                        {
                            "question_id": 1,
                            "node_id": "chunk-1",
                            "node_name": "斜率",
                            "question_type": "OPTIONS",
                            "correct_answer": "变化率",
                            "options": [
                                {"key": "A", "text": "变化率", "is_correct": True},
                                {"key": "B", "text": "面积", "is_correct": False},
                            ],
                        },
                        {
                            "question_id": 2,
                            "node_id": "chunk-2",
                            "node_name": "截距",
                            "question_type": "OPTIONS",
                            "correct_answer": "与坐标轴交点",
                            "options": [
                                {"key": "A", "text": "顶点", "is_correct": False},
                                {"key": "B", "text": "与坐标轴交点", "is_correct": True},
                            ],
                        },
                    ],
                }
            ],
        },
        "updated_at": "2026-07-31T00:00:00+00:00",
    }


@pytest.mark.asyncio
async def test_quiz_attempt_records_only_checked_answers_and_updates_bkt(learner_store):
    pack = _pack()

    await _record_pack_learning_events(
        pack,
        {
            "quiz_attempt": {
                "submitted_at": "2026-07-31T00:01:00+00:00",
                "answers": {"0": "变化率", "1": "顶点"},
                "checked": [0],
                "total": 2,
            }
        },
    )

    profile = learner_store.subject_profile("mathematics")
    assert profile.subject is not None
    assert [item.label for item in profile.concept_signals] == ["斜率"]
    assert profile.concept_signals[0].verified_observation_count == 1
    assert profile.concept_signals[0].mastery_probability > 0.2


@pytest.mark.asyncio
async def test_flashcard_review_records_uncertain_as_low_confidence_review(learner_store):
    pack = _pack()

    await _record_pack_learning_events(
        pack,
        {"flashcard_progress": {"chunk-2": "uncertain"}, "review_id": "review-a"},
    )

    profile = learner_store.subject_profile("mathematics")
    concept = profile.concept_signals[0]
    assert concept.label == "截距"
    assert concept.last_observation_source == "flashcard_review"
    assert concept.verified_observation_count == 1
    assert concept.mastery_probability < 0.5


@pytest.mark.asyncio
async def test_quiz_attempt_accepts_option_keys_and_ignores_dirty_checked_values(learner_store):
    pack = _pack()

    await _record_pack_learning_events(
        pack,
        {
            "quiz_attempt": {
                "submitted_at": "2026-07-31T00:02:00+00:00",
                "answers": {"0": "A", "1": "A"},
                "checked": [True, "0", "not-index"],
                "total": 2,
            }
        },
    )

    profile = learner_store.subject_profile("mathematics")
    assert [item.label for item in profile.concept_signals] == ["斜率"]
    assert profile.concept_signals[0].mastery_probability > 0.2
