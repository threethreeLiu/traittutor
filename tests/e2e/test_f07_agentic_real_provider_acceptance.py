"""Opt-in five-scenario real-provider release gate for F-07.

These tests deliberately spend configured Provider calls. Normal regression
runs skip the module; ``scripts/run_agentic_provider_acceptance.py`` is the
only supported path that turns a passing run into a commit-bound report.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from traittutor.components import PageStore, validate_page_schema
from traittutor.generate import service
from traittutor.orchestration import OrchestratorRunStore
from traittutor.orchestration.agentic_planner import AgenticCoursewarePlanner

pytestmark = pytest.mark.skipif(
    os.environ.get("TRAITTUTOR_RUN_REAL_PROVIDER_E2E") != "1",
    reason="set TRAITTUTOR_RUN_REAL_PROVIDER_E2E=1 to spend real provider calls",
)


def _stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PageStore, OrchestratorRunStore]:
    page_store = PageStore(path=tmp_path / "pages.json")
    run_store = OrchestratorRunStore(tmp_path / "runs.json")
    monkeypatch.setattr(service, "_page_store", lambda: page_store)
    monkeypatch.setattr(service, "_orchestrator_run_store", lambda: run_store)
    monkeypatch.setenv("TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE", "agentic")
    return page_store, run_store


async def _generate(
    generation_id: str,
    *,
    title: str,
    external_augmentation_allowed: bool = False,
    external_search: Any = None,
    external_fetch: Any = None,
) -> dict[str, Any]:
    return await service._generate_courseware_with_orchestrator(
        generation_id=generation_id,
        title=title,
        chunks=(
            {
                "chunk_id": "chunk-1",
                "source_id": "source-1",
                "text": (
                    "A limit is the value a function approaches near an input. "
                    "The frozen material intentionally contains no historical context."
                ),
            },
        ),
        learner_strategy={"mode": "scaffolded"},
        slr_support={},
        language="en",
        learning_targets={"courseware_targets": ["limits"]},
        visual_seed={"title": "Limits", "visual_targets": []},
        external_augmentation_allowed=external_augmentation_allowed,
        external_search=external_search,
        external_fetch=external_fetch,
    )


def _assert_safe_page(page_store: PageStore, generation_id: str) -> None:
    page = page_store.get(f"{generation_id}:page")
    assert page is not None
    validate_page_schema(page)
    serialized = page.model_dump_json().lower()
    assert "answer_key" not in serialized
    assert "<script" not in serialized


@pytest.mark.asyncio
async def test_pure_material(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page_store, run_store = _stores(tmp_path, monkeypatch)
    result = await _generate("accept-pure-material", title="Teach limits from the frozen material")

    assert result["orchestration"]["mode"] == "agentic"
    persisted = run_store.get_by_key(result["trace"][0]["run_key"])
    assert persisted is not None
    checkpoints = [item.checkpoint for item in persisted.task_results]
    receipts = sum(len(item.gateway_receipts) for item in checkpoints if item is not None)
    assert receipts >= 2
    _assert_safe_page(page_store, "accept-pure-material")


@pytest.mark.asyncio
async def test_external_augmentation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page_store, _run_store = _stores(tmp_path, monkeypatch)
    calls = {"search": 0, "fetch": 0}

    async def search(_query: str):
        calls["search"] += 1
        return (
            {
                "source_id": "external-1",
                "title": "History of limits",
                "url": "https://example.com/history-of-limits",
            },
        )

    async def fetch(source_id: str):
        calls["fetch"] += 1
        assert source_id == "external-1"
        return {"text": "Cauchy helped formalize epsilon-style definitions of limits."}

    result = await _generate(
        "accept-external",
        title="Use the authorized external tools to add sourced historical context about limits",
        external_augmentation_allowed=True,
        external_search=search,
        external_fetch=fetch,
    )

    assert result["orchestration"]["mode"] == "agentic"
    assert calls["search"] >= 1 and calls["fetch"] >= 1
    _assert_safe_page(page_store, "accept-external")
    page = page_store.get("accept-external:page")
    assert page is not None
    assert "https://example.com/history-of-limits" in page.model_dump_json()


@pytest.mark.asyncio
async def test_tool_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page_store, _run_store = _stores(tmp_path, monkeypatch)
    calls = 0

    async def unavailable_search(_query: str):
        nonlocal calls
        calls += 1
        raise RuntimeError("acceptance-injected external tool outage")

    result = await _generate(
        "accept-tool-failure",
        title="Use the authorized external search tool, and degrade safely if it is unavailable",
        external_augmentation_allowed=True,
        external_search=unavailable_search,
        external_fetch=lambda _source_id: {},
    )

    assert result["orchestration"]["mode"] == "agentic"
    assert calls >= 1
    assert result["orchestration"]["status"] == "degraded"
    _assert_safe_page(page_store, "accept-tool-failure")


@pytest.mark.asyncio
async def test_planner_invalid_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page_store, _run_store = _stores(tmp_path, monkeypatch)
    original = AgenticCoursewarePlanner.plan
    provider_returned = False

    async def invalid_after_real_provider(
        self: AgenticCoursewarePlanner, *args: Any, **kwargs: Any
    ):
        nonlocal provider_returned
        graph = await original(self, *args, **kwargs)
        provider_returned = True
        first = graph.tasks[0].model_copy(update={"depends_on": ("missing-task",)})
        return graph.model_copy(update={"tasks": (first, *graph.tasks[1:])})

    monkeypatch.setattr(AgenticCoursewarePlanner, "plan", invalid_after_real_provider)
    result = await _generate(
        "accept-invalid-graph",
        title="Teach limits while exercising the invalid Planner graph fallback",
    )

    assert provider_returned is True
    assert result["orchestration"]["mode"] == "deterministic"
    _assert_safe_page(page_store, "accept-invalid-graph")


@pytest.mark.asyncio
async def test_planner_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page_store, _run_store = _stores(tmp_path, monkeypatch)
    original = AgenticCoursewarePlanner.plan
    provider_returned = False

    async def timeout_after_real_provider(
        self: AgenticCoursewarePlanner, *args: Any, **kwargs: Any
    ):
        nonlocal provider_returned
        await original(self, *args, **kwargs)
        provider_returned = True
        raise TimeoutError("acceptance-injected Planner deadline")

    monkeypatch.setattr(AgenticCoursewarePlanner, "plan", timeout_after_real_provider)
    result = await _generate(
        "accept-planner-timeout",
        title="Teach limits while exercising the Planner timeout fallback",
    )

    assert provider_returned is True
    assert result["orchestration"]["mode"] == "deterministic"
    _assert_safe_page(page_store, "accept-planner-timeout")
