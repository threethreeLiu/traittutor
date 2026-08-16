from __future__ import annotations

import pytest

from traittutor.learning_governance.service import LearningGovernanceService

from .test_repository import _repository


def test_snapshot_returns_one_explicit_partition(tmp_path) -> None:
    service = LearningGovernanceService(_repository(tmp_path))

    snapshot = service.snapshot(subject_id=" math ", kc_id=" fractions ", now=10.0)

    assert snapshot.subject_id == "math"
    assert snapshot.kc_id == "fractions"
    assert {item.error_id for item in snapshot.errors} == {"verified", "pending"}
    assert {item.subject_id for item in snapshot.errors} == {"math"}
    assert snapshot.reviews == ()


@pytest.mark.parametrize("subject_id", ["", "   "])
def test_snapshot_rejects_blank_subject(subject_id: str, tmp_path) -> None:
    service = LearningGovernanceService(_repository(tmp_path))

    with pytest.raises(ValueError, match="subject_id"):
        service.snapshot(subject_id=subject_id)
