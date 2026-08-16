from __future__ import annotations

import pytest

from traittutor.components.projection import (
    GenerationResultProjectionRetiredError,
    project_generation_result,
)


def test_generation_result_projection_is_rejected() -> None:
    with pytest.raises(
        GenerationResultProjectionRetiredError,
        match="load the published PageSchema",
    ):
        project_generation_result(
            {"kind": "courseware", "markdown": "Old result"},
            generation_run_id="run-1",
            created_at="2026-08-09T08:00:00+00:00",
        )
