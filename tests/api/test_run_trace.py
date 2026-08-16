"""Owner-bound HTTP contract for learner-safe generation Run traces."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header
import httpx
import pytest

from traittutor.api.routers import run_trace as run_trace_router
from traittutor.components import text_degrade_page
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.orchestration.courseware_orchestrator import AgentTaskResult, OrchestratorRun
from traittutor.orchestration.evaluator import EvaluatorVerdict
from traittutor.orchestration.prompt_bundle import CoursewarePromptBundle
from traittutor.orchestration.run_store import OrchestratorRunStore
from traittutor.orchestration.task_graph import AgentTask, AgentTaskGraph

CREATED_AT = "2026-08-11T08:00:00+00:00"


def _run(*, suffix: str, generation_run_id: str) -> OrchestratorRun:
    task = AgentTask(
        task_id="material",
        task_type="material",
        agent="Material Agent",
        depends_on=(),
        input_refs=("grounding:chunk-safe", "grounding:SECRET PRIVATE SOURCE"),
        produces_component_types=(),
        budget_ms=100,
        timeout_ms=150,
    )
    graph = AgentTaskGraph(
        graph_id=f"graph-{suffix}",
        prompt_bundle_id=f"bundle-{suffix}",
        prompt_bundle_hash=f"private-hash-{suffix}",
        version="v1",
        tasks={"material": task},
        created_at=CREATED_AT,
    )
    return OrchestratorRun(
        run_id=f"run-{suffix}",
        graph_id=graph.graph_id,
        generation_run_id=generation_run_id,
        task_results=(
            AgentTaskResult(
                task_id="material",
                status="failed",
                produced_component_instances=(),
                notes=f"SECRET TOOL PARAMS {suffix}",
            ),
        ),
        evaluator_findings=(f"SECRET RUBRIC {suffix}",),
        succeeded=False,
        page=text_degrade_page(
            page_schema_id=f"run-{suffix}:page",
            generation_run_id=generation_run_id,
            reason="orchestration_failed",
            created_at=CREATED_AT,
        ),
        run_key=f"private-key-{suffix}",
        status="degraded",
        prompt_bundle=CoursewarePromptBundle(
            prompt_bundle_id=f"bundle-{suffix}",
            version="v1",
            context_snapshot_id=f"snapshot-{suffix}",
            context_snapshot_hash=f"private-context-hash-{suffix}",
            material_language="zh-CN",
            requested_component_types=(),
            teaching_goal=f"SECRET ANSWER {suffix}",
            created_at=CREATED_AT,
        ),
        task_graph=graph,
        input_refs=task.input_refs,
        evaluator_verdict=EvaluatorVerdict(
            status="failed",
            findings=(f"SECRET RUBRIC {suffix}",),
            repair_note=f"SECRET REASONING {suffix}",
        ),
        duration_ms=25,
    )


@pytest.mark.asyncio
async def test_run_trace_is_owner_bound_and_learner_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stores = {
        owner: OrchestratorRunStore(tmp_path / owner / "orchestrator-runs.json")
        for owner in ("owner-a", "owner-b")
    }
    stores["owner-a"].save(_run(suffix="a", generation_run_id="shared-generation"))
    stores["owner-b"].save(_run(suffix="b", generation_run_id="shared-generation"))
    stores["owner-b"].save(_run(suffix="b-only", generation_run_id="private-generation"))
    stores["owner-a"].save(_run(suffix="duplicate-1", generation_run_id="duplicate"))
    stores["owner-a"].save(_run(suffix="duplicate-2", generation_run_id="duplicate"))
    monkeypatch.setattr(run_trace_router, "run_store_factory", lambda user: stores[user.id])

    async def install_user(
        x_test_user: Annotated[str, Header()],
    ) -> AsyncIterator[None]:
        user = CurrentUser(
            id=x_test_user,
            username=x_test_user,
            role="user",
            scope=scope_for_user(x_test_user, is_admin=False),
        )
        token = set_current_user(user)
        try:
            yield
        finally:
            reset_current_user(token)

    app = FastAPI()
    app.include_router(
        run_trace_router.router,
        prefix="/api/v1",
        dependencies=[Depends(install_user)],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        owner_a = await client.get(
            "/api/v1/generation-runs/shared-generation/trace",
            headers={"x-test-user": "owner-a"},
        )
        owner_b = await client.get(
            "/api/v1/generation-runs/shared-generation/trace",
            headers={"x-test-user": "owner-b"},
        )
        inaccessible = await client.get(
            "/api/v1/generation-runs/private-generation/trace",
            headers={"x-test-user": "owner-a"},
        )
        corrupt = await client.get(
            "/api/v1/generation-runs/duplicate/trace",
            headers={"x-test-user": "owner-a"},
        )

    assert owner_a.status_code == 200
    assert owner_b.status_code == 200
    assert owner_a.json()["run_id"] == "run-a"
    assert owner_b.json()["run_id"] == "run-b"
    assert inaccessible.status_code == 404
    assert inaccessible.json() == {"detail": "Generation run not found"}
    assert corrupt.status_code == 503
    assert corrupt.json() == {"detail": "Generation run trace unavailable"}
    serialized = owner_a.text
    assert "SECRET" not in serialized
    assert "prompt_bundle" not in serialized
    assert "notes" not in serialized
    assert "rubric" not in serialized.lower()
