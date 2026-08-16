from __future__ import annotations

from datetime import UTC, datetime, timedelta

from traittutor.learning_support import (
    calibration_record,
    due_reviews,
    normalize_slr_dimensions,
)


def test_subject_evidence_requires_three_observations() -> None:
    two = normalize_slr_dimensions(
        {
            "dimensions": {
                "goal_planning": {
                    "source": "subject_evidence",
                    "evidence_count": 2,
                    "emphasis": "strong",
                }
            }
        }
    )
    three = normalize_slr_dimensions(
        {
            "dimensions": {
                "goal_planning": {
                    "source": "subject_evidence",
                    "evidence_count": 3,
                    "emphasis": "strong",
                }
            }
        }
    )

    assert two["goal_planning"].source == "initial_profile"
    assert two["goal_planning"].emphasis == "standard"
    assert three["goal_planning"].source == "subject_evidence"
    assert three["goal_planning"].emphasis == "strong"


def test_calibration_uses_confidence_and_server_correctness() -> None:
    record = calibration_record("q1", 0.9, False, artifact_ref="quiz-1")

    assert record.quadrant == "confident_incorrect"
    assert record.recommended_strategy == "repair_with_contrast"


def test_due_reviews_are_bounded_and_sorted() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    pack = {
        "review_states": [
            {
                "review_id": f"r-{index}",
                "priority": 7 - index,
                "due_at": (now - timedelta(minutes=index + 1)).isoformat(),
            }
            for index in range(7)
        ]
    }

    result = due_reviews(pack, now=now, limit=99)

    assert len(result) == 5
    assert [item["priority"] for item in result] == [1, 2, 3, 4, 5]
