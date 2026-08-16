from __future__ import annotations

import json
from pathlib import Path

from traittutor.orchestration import OrchestratorRunStore
from traittutor.orchestration.run_store import AgenticBudgetReservation


def test_empty_run_store_recovers_missing_run(tmp_path: Path) -> None:
    store = OrchestratorRunStore(tmp_path / "runs.json")
    assert store.get("missing") is None
    assert store.get_by_key("missing") is None


def test_missing_runs_key_loads_empty_not_keyerror(tmp_path: Path) -> None:
    # Regression for code-review finding #3 (run_store parity): a persisted run
    # store whose payload is missing the "runs" key must self-heal to empty
    # rather than raise an uncaught KeyError on every get/get_by_key.
    path = tmp_path / "runs.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    store = OrchestratorRunStore(path)
    assert store.get("anything") is None
    assert store.get_by_key("anything") is None


def test_agentic_budget_reservation_survives_before_checkpoint(tmp_path: Path) -> None:
    store = OrchestratorRunStore(tmp_path / "runs.json")
    reservation = AgenticBudgetReservation(
        reservation_id="generation:g1:llm:1",
        generation_run_id="g1",
        logical_llm_calls=1,
        started_at_unix=1_723_600_000.0,
    )
    store.reserve_agentic_budget(reservation)
    store.reserve_agentic_budget(reservation.model_copy(update={"output_tokens": 321}))

    recovered = OrchestratorRunStore(tmp_path / "runs.json").get_agentic_budget_usage("g1")

    assert recovered.logical_llm_calls == 1
    assert recovered.tool_calls == 0
    assert recovered.output_tokens == 321
    assert recovered.started_at_unix == 1_723_600_000.0


def test_generation_receipt_prefers_final_replanned_run(tmp_path: Path) -> None:
    """A bounded replan persists two receipts for one generation; the final
    (highest replan_count) run is the one external callers address, and the
    superseded pre-replan run is pruned on save."""
    from tests.orchestration.test_f07_acceptance import _bundle, _successful_executors
    from traittutor.orchestration import CoursewareOrchestrator

    store = OrchestratorRunStore(tmp_path / "runs.json")
    orch = CoursewareOrchestrator(run_store=store)
    graph_a = orch.plan(_bundle())
    first = orch.run(graph_a, _successful_executors(), generation_run_id="gen-1")
    assert store.get_by_generation_run_id("gen-1") == first
    # Bounded replan: the replanned graph executes under the same generation
    # with replan_count=1 (the orchestrator persists both receipts).
    graph_b = orch.plan(
        _bundle().model_copy(
            update={
                "prompt_bundle_id": "bundle-replan",
                "teaching_goal": "Replanned goal.",
                # Different requested component types produce a structurally
                # different task graph (and therefore a distinct run id).
                "requested_component_types": ("concept_explanation", "visual_map"),
            }
        )
    )
    orch._agentic_replan_counts[graph_b.graph_id] = 1
    replanned = orch.run(graph_b, _successful_executors(), generation_run_id="gen-1")

    assert replanned.replan_count == 1
    assert store.get_by_generation_run_id("gen-1") == replanned
    # The superseded pre-replan receipt was pruned on save.
    assert store.get(first.run_id) is None


def test_run_store_retention_caps_bound_growth(tmp_path: Path) -> None:
    from tests.orchestration.test_f07_acceptance import _bundle, _successful_executors
    from traittutor.orchestration import CoursewareOrchestrator

    store = OrchestratorRunStore(tmp_path / "runs.json")
    store._MAX_RUNS = 3
    orch = CoursewareOrchestrator(run_store=store)
    component_sets = (
        ("concept_explanation", "guided_practice", "visual_map"),
        ("concept_explanation", "visual_map"),
        ("concept_explanation", "guided_practice"),
        ("guided_practice", "visual_map"),
        ("concept_explanation",),
    )
    for index, component_types in enumerate(component_sets):
        graph = orch.plan(
            _bundle().model_copy(
                update={
                    "prompt_bundle_id": f"bundle-{index}",
                    "teaching_goal": f"Goal {index}.",
                    "requested_component_types": component_types,
                }
            )
        )
        store.save(orch.run(graph, _successful_executors(), generation_run_id=f"gen-{index}"))
    payload = store._adapter.snapshot()
    assert len(payload["runs"]) <= 3
