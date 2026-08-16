from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError
import pytest

from traittutor.research_workspace.models import ResearchClaim


def _now() -> str:
    return datetime.now(UTC).isoformat()


def test_grounded_research_claim_requires_source_ids() -> None:
    with pytest.raises(ValidationError, match="grounded claims require"):
        ResearchClaim(
            claim_id="clm_1",
            workspace_id="ws_1",
            run_id="run_1",
            owner_id="user_a",
            text="A claim presented as a sourced fact.",
            kind="grounded",
            created_at=_now(),
        )


def test_inference_cannot_be_presented_with_a_source_reference() -> None:
    with pytest.raises(ValidationError, match="must not masquerade"):
        ResearchClaim(
            claim_id="clm_1",
            workspace_id="ws_1",
            run_id="run_1",
            owner_id="user_a",
            text="A model inference.",
            kind="inference",
            source_ids=("src_1",),
            created_at=_now(),
        )
