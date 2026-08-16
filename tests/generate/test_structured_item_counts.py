from __future__ import annotations

from traittutor.generate.flashcards import (
    plan_flashcard_batches,
    validate_flashcard_payload,
)
from traittutor.generate.quiz import plan_quiz_batches, validate_quiz_payload
from traittutor.generate.service import _quiz_plans

CHUNKS = [
    {
        "source_id": "source-1",
        "chunk_id": "chunk-1",
        "text": "The source contains enough grounded material for repeated recall practice.",
    }
]


def _reference() -> dict[str, str]:
    return {
        "source_id": "source-1",
        "chunk_id": "chunk-1",
        "text_snippet": CHUNKS[0]["text"],
    }


def test_quiz_validation_does_not_cap_prompt_generated_item_count() -> None:
    payload = {
        "items": [
            {
                "node_id": "chunk-1",
                "node_name": f"Concept {index}",
                "question_id": index,
                "question": f"Explain grounded concept {index}.",
                "question_type": "SHORT_ANSWER",
                "difficulty": "medium",
                "options": [],
                "correct_answer": f"Answer {index}",
                "explanation": f"Explanation {index}",
                "references": [_reference()],
            }
            for index in range(1, 13)
        ]
    }

    assert len(validate_quiz_payload(payload, CHUNKS).items) == 12


def test_flashcard_validation_does_not_cap_prompt_generated_item_count() -> None:
    payload = {
        "items": [
            {
                "node_id": "chunk-1",
                "node_name": f"Concept {index}",
                "front": f"What is grounded concept {index}?",
                "back": f"Grounded answer {index}",
                "references": [_reference()],
            }
            for index in range(1, 13)
        ]
    }

    assert len(validate_flashcard_payload(payload, CHUNKS).items) == 12


def test_prompt_batch_planners_accept_positive_counts_without_fixed_maximum() -> None:
    quiz = plan_quiz_batches(CHUNKS, questions_per_batch=12)
    flashcards = plan_flashcard_batches(CHUNKS, cards_per_batch=12)

    assert quiz[0].question_count == 12
    assert flashcards[0].item_limit == 12


def test_quiz_generation_batch_count_is_bounded_by_chunks_not_question_count() -> None:
    """A public question_count must never amplify the number of LLM batches.

    Regression for the P1 cost/DoS guard: ``GenerateSuiteRequest.options`` is
    an untrusted client surface, and trusting ``question_count`` there would
    let one request fan out into an unbounded number of prompts and coroutines.
    Batch count is driven by the resolved source chunks alone.
    """
    bounded = _quiz_plans(CHUNKS, {"question_count": 25})

    assert [plan.question_count for plan in bounded] == [8]
    assert sum(plan.question_count for plan in bounded) == 8
    # The option is ignored entirely; a huge count must not create more batches.
    assert _quiz_plans(CHUNKS, {"question_count": 1_000_000}) == bounded


def test_quiz_plans_empty_chunks_produce_no_batches() -> None:
    assert _quiz_plans([], {}) == ()
