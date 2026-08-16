from __future__ import annotations

from pydantic import ValidationError
import pytest

from traittutor.learning.models import ErrorRecordStatus, ErrorType
from traittutor.learning_governance.models import (
    ErrorSummary,
    GovernanceAttributionStatus,
)


def _error_payload() -> dict[str, object]:
    return {
        "error_id": "error-1",
        "question_id": "question-1",
        "subject_id": "math",
        "kc_id": "fractions",
        "error_type": ErrorType.APPLICATION_ERROR,
        "status": ErrorRecordStatus.OPEN,
        "attribution_status": GovernanceAttributionStatus.VERIFIED,
        "created_at": 1.0,
    }


@pytest.mark.parametrize(
    "secret_field",
    [
        "answer",
        "user_answer",
        "expected_answer",
        "rubric",
        "correct_rule",
        "prompt",
        "raw_prompt",
        "ai_confirmation",
    ],
)
def test_public_error_contract_rejects_secret_fields(secret_field: str) -> None:
    payload = _error_payload()
    payload[secret_field] = "must not escape"

    with pytest.raises(ValidationError):
        ErrorSummary.model_validate(payload)


def test_public_error_serialization_contains_only_learner_safe_fields() -> None:
    serialized = ErrorSummary.model_validate(_error_payload()).model_dump(mode="json")

    forbidden = {
        "answer",
        "user_answer",
        "expected_answer",
        "rubric",
        "correct_rule",
        "prompt",
        "raw_prompt",
        "ai_confirmation",
    }
    assert forbidden.isdisjoint(serialized)
    assert serialized["subject_id"] == "math"
