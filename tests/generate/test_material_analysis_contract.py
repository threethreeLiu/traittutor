from __future__ import annotations

import pytest

from traittutor.generate.material_analysis import (
    MaterialAnalysis,
    _validate_material_analysis_record,
)


def _analysis() -> MaterialAnalysis:
    return MaterialAnalysis(
        analysis_id="analysis-1",
        session_id="session-1",
        owner_id="learner-1",
        source_id="source-1",
        subject="mathematics",
        sub_subject="algebra",
        chinese_grade="junior_1",
        international_grade="grade_7",
        difficulty="standard",
        confidence=0.8,
        evidence=[
            {
                "chunk_id": "chunk-1",
                "page": 1,
                "excerpt": "Linear equations",
                "source_id": "source-1",
            }
        ],
        augmentation_needed=False,
        augmentation_reason="",
        created_at="2026-08-12T00:00:00+00:00",
        trace={"mode": "llm"},
        concept_candidates=[],
        component_affordances={},
    )


def test_material_analysis_serializes_one_canonical_shape() -> None:
    payload = _analysis().to_dict()

    assert "grade_band" not in payload
    assert "page_evidence" not in payload
    assert "augmentation_decision" not in payload
    _validate_material_analysis_record(payload)


@pytest.mark.parametrize("retired_key", ["grade_band", "page_evidence", "augmentation_decision"])
def test_material_analysis_rejects_retired_duplicate_fields(retired_key: str) -> None:
    payload = _analysis().to_dict()
    payload[retired_key] = {}

    with pytest.raises(ValueError, match="retired fields"):
        _validate_material_analysis_record(payload)
