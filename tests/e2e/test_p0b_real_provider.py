"""Opt-in real-provider smoke for the P0-B courseware composition root.

The normal suite must stay deterministic and free of provider spend.  Set
``TRAITTUTOR_RUN_REAL_PROVIDER_E2E=1`` explicitly to verify the configured
Gateway route with the production Orchestrator budgets and isolated stores.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from traittutor.components import PageStore, validate_page_schema
from traittutor.generate import service
from traittutor.orchestration import OrchestratorRunStore

ANSWER_FIELDS = {"answer", "rubric", "solution", "back", "correct", "expected"}


def _answer_fields(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = ANSWER_FIELDS.intersection(value)
        for child in value.values():
            found.update(_answer_fields(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_answer_fields(child))
        return found
    return set()


@pytest.mark.asyncio
async def test_real_provider_orchestrator_publishes_non_degraded_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.environ.get("TRAITTUTOR_RUN_REAL_PROVIDER_E2E") != "1":
        pytest.skip("set TRAITTUTOR_RUN_REAL_PROVIDER_E2E=1 to spend a real provider call")

    page_store = PageStore(path=tmp_path / "pages.json")
    run_store = OrchestratorRunStore(tmp_path / "orchestrator-runs.json")
    monkeypatch.setattr(service, "_page_store", lambda: page_store)
    monkeypatch.setattr(service, "_orchestrator_run_store", lambda: run_store)

    generation_id = "real-provider-p0b-smoke"
    result = await service._generate_courseware_with_orchestrator(
        generation_id=generation_id,
        title="Limits (real provider smoke)",
        chunks=[
            {
                "chunk_id": "c1",
                "source_id": "s1",
                "text": (
                    "A limit describes the value a function approaches as the input "
                    "approaches a target. For example, lim x->2 of (3x+1) = 7."
                ),
            }
        ],
        learner_strategy={"mode": "scaffolded"},
        slr_support={},
        language="en",
        learning_targets={"courseware_targets": ["limits"]},
        # Keep this smoke focused on the configured text Gateway. Image-provider
        # availability is independent and must not make the courseware gate flaky.
        visual_seed={"title": "Limits", "visual_targets": []},
    )

    trace = result["trace"][0]
    assert trace["status"] == "succeeded", trace
    assert all(item["status"] == "succeeded" for item in trace["task_results"]), trace
    page = page_store.get(f"{generation_id}:page")
    assert page is not None
    validate_page_schema(page)
    assert not _answer_fields(page.model_dump(mode="json"))
    bodies = [
        str(region.component.props.get("body_markdown") or "")
        for region in page.regions
        if region.component is not None
    ]
    assert any(body and "orchestration_failed" not in body for body in bodies)
    assert run_store.get_by_key(trace["run_key"]) is not None
