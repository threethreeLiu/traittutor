"""Security boundary for learner-visible learning-artifact sources."""

from __future__ import annotations

import pytest

from traittutor.services.session.source_inventory import (
    SourceInventory,
    _resolve_learning_artifact,
    _serialize_learning_artifact,
    render_manifest,
)


def test_learning_artifact_sources_never_serialize_server_held_answers() -> None:
    pack = {
        "pack_id": "pack-1",
        "title": "Data structures",
        "material": {"title": "Course notes"},
    }
    flashcards = {
        "title": "Stack cards",
        "items": [
            {
                "front": "Which operation removes the top item?",
                "back": "FLASHCARD_BACK_SENTINEL",
                "answer": "FLASHCARD_ANSWER_SENTINEL",
            }
        ],
    }
    quiz = {
        "title": "Stack quiz",
        "items": [
            {
                "question": "Which rule describes a stack?",
                "options": [{"text": "FIFO"}, {"text": "LIFO"}],
                "correct_answer": "QUIZ_ANSWER_SENTINEL",
                "explanation": "QUIZ_EXPLANATION_SENTINEL",
                "rubric": "QUIZ_RUBRIC_SENTINEL",
                "hint": "QUIZ_HINT_SENTINEL",
            }
        ],
    }

    flashcard_text = _serialize_learning_artifact(pack, "flashcards", flashcards)
    quiz_text = _serialize_learning_artifact(pack, "quiz", quiz)
    combined = flashcard_text + "\n" + quiz_text

    assert "Which operation removes the top item?" in flashcard_text
    assert "Which rule describes a stack?" in quiz_text
    assert "FIFO" in quiz_text
    assert "LIFO" in quiz_text
    for sentinel in (
        "FLASHCARD_BACK_SENTINEL",
        "FLASHCARD_ANSWER_SENTINEL",
        "QUIZ_ANSWER_SENTINEL",
        "QUIZ_EXPLANATION_SENTINEL",
        "QUIZ_RUBRIC_SENTINEL",
        "QUIZ_HINT_SENTINEL",
    ):
        assert sentinel not in combined


def test_learning_artifact_source_index_contains_only_learner_visible_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack = {
        "pack_id": "pack-1",
        "title": "Data structures",
        "artifacts": {
            "quiz": [
                {
                    "title": "Stack quiz",
                    "items": [
                        {
                            "question": "Which rule describes a stack?",
                            "options": ["FIFO", "LIFO"],
                            "correct_answer": "SOURCE_INDEX_ANSWER_SENTINEL",
                            "explanation": "SOURCE_INDEX_EXPLANATION_SENTINEL",
                        }
                    ],
                }
            ]
        },
    }
    monkeypatch.setattr("traittutor.learning_packs.get_pack", lambda _pack_id: pack)
    entry = _resolve_learning_artifact(
        {"pack_id": "pack-1", "artifact_type": "quiz", "artifact_index": 0},
        fresh=True,
        first_seen_turn=1,
    )
    assert entry is not None
    inventory = SourceInventory()
    inventory.add(entry)

    manifest, source_index = render_manifest(inventory)
    combined = manifest + "\n" + "\n".join(source_index.values())
    assert "Which rule describes a stack?" in combined
    assert "SOURCE_INDEX_ANSWER_SENTINEL" not in combined
    assert "SOURCE_INDEX_EXPLANATION_SENTINEL" not in combined
