from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from traittutor import learning_packs
from traittutor.api.routers import learning_packs as learning_packs_router
from traittutor.api.routers.learning_packs import (
    CreateLearningPlanRequest,
    CreatePackWithPlanRequest,
)
from traittutor.learning.storage import LearningStore
from traittutor.services.path_service import PathService


@pytest.fixture
def learning_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> PathService:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)
    return service


def test_pack_is_not_persisted_when_initial_plan_build_fails(
    learning_workspace: PathService,
) -> None:
    def fail_plan(_pack: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("plan build failed")

    with pytest.raises(RuntimeError, match="plan build failed"):
        learning_packs.create_pack_with_component_plan(
            title="Atomic pack",
            plan_builder=fail_plan,
            idempotency_key="atomic-failure",
        )

    assert learning_packs.list_packs() == []


@pytest.mark.asyncio
async def test_pack_and_initial_plan_are_returned_from_one_create_request(
    learning_workspace: PathService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = {
        "plan_id": "plan-atomic",
        "status": "active",
        "components": [],
        "created_at": "2026-08-13T00:00:00+00:00",
        "updated_at": "2026-08-13T00:00:00+00:00",
    }
    monkeypatch.setattr(
        learning_packs_router,
        "_build_component_plan",
        lambda _pack, _request: SimpleNamespace(model_dump=lambda: plan),
    )

    result = await learning_packs_router.create_learning_pack_with_plan(
        CreatePackWithPlanRequest(
            title="Atomic pack",
            idempotency_key="atomic-success",
            material={"source_type": "paste", "title": "Source", "text": "Safe source"},
            plan=CreateLearningPlanRequest(instruction="Learn safely"),
        )
    )

    pack_id = result["pack"]["pack_id"]
    persisted = learning_packs.get_pack(pack_id)
    assert result["plan"]["plan_id"] == "plan-atomic"
    assert result["plan"]["start_url"] == f"/learning/{pack_id}"
    assert persisted is not None
    assert persisted["active_plan_id"] == "plan-atomic"
    assert [item["plan_id"] for item in persisted["component_plans"]] == ["plan-atomic"]
    assert {
        "_initial_create_idempotency_key",
        "_initial_create_request_fingerprint",
        "_initial_create_plan_id",
    }.isdisjoint(result["pack"])


@pytest.mark.asyncio
async def test_initial_create_binds_trusted_material_graph_for_canonical_evidence(
    learning_workspace: PathService,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    subject_ref = {
        "subject_id": "mathematics",
        "label": "Mathematics",
        "path": ["mathematics", "algebra"],
        "confidence": 0.92,
        "source": "material_analysis",
        "confirmed": False,
    }
    plan = {
        "plan_id": "plan-bound",
        "status": "active",
        "subject_ref": subject_ref,
        "components": [],
        "created_at": "2026-08-13T00:00:00+00:00",
        "updated_at": "2026-08-13T00:00:00+00:00",
    }
    monkeypatch.setattr(
        learning_packs_router,
        "_build_component_plan",
        lambda _pack, _request: SimpleNamespace(model_dump=lambda: plan),
    )
    monkeypatch.setattr(
        learning_packs_router,
        "load_material_analysis",
        lambda *_args, **_kwargs: {
            "analysis_id": "analysis-1",
            "source_id": "source-1",
            "subject": "mathematics",
            "confidence": 0.92,
            "concept_candidates": [
                {
                    "concept_id": "kc-algebra",
                    "label": "Algebra",
                    "module_id": "algebra",
                    "evidence_chunk_ids": ["chunk-1"],
                }
            ],
            "evidence": [{"chunk_id": "chunk-1", "excerpt": "Solve x + 1 = 2"}],
        },
    )
    store = LearningStore(root=tmp_path / "learning-progress")
    monkeypatch.setattr(
        learning_packs_router,
        "LearningStore",
        lambda *, owner_id: store,
    )
    request = CreatePackWithPlanRequest(
        title="Algebra",
        idempotency_key="trusted-graph",
        material={
            "source_type": "upload",
            "source_id": "source-1",
            "title": "Algebra notes",
            "metadata": {
                "learning_session_id": "session-1",
                "learner_analyses": [
                    {
                        "analysis_id": "analysis-1",
                        "session_id": "session-1",
                    }
                ],
            },
        },
        plan=CreateLearningPlanRequest(instruction="Learn algebra"),
    )

    result = await learning_packs_router.create_learning_pack_with_plan(request)
    replay = await learning_packs_router.create_learning_pack_with_plan(request)

    pack_id = result["pack"]["pack_id"]
    persisted = learning_packs.get_pack(pack_id)
    assert persisted is not None
    assert len(persisted["learning_path_bindings"]) == 1
    binding = persisted["learning_path_bindings"][0]
    assert binding["subject_id"] == "mathematics"
    assert binding["allowed_kc_ids"] == ["chunk-1"]
    progress = store.load(f"pack-{pack_id}")
    assert progress is not None
    assert progress.subject_id == "mathematics"
    assert replay["pack"]["pack_id"] == pack_id


def test_initial_pack_create_is_idempotent(
    learning_workspace: PathService,
) -> None:
    plan_builds = 0

    def build_plan(_pack: dict[str, object]) -> dict[str, object]:
        nonlocal plan_builds
        plan_builds += 1
        return {"plan_id": f"plan-{plan_builds}", "status": "active", "components": []}

    first_pack, first_plan = learning_packs.create_pack_with_component_plan(
        title="Retry-safe pack",
        plan_builder=build_plan,
        idempotency_key="stable-create-key",
        goal="Learn the same chapter",
    )
    assert learning_packs.create_component_plan(
        first_pack["pack_id"],
        {
            "plan_id": "plan-later",
            "status": "active",
            "supersedes_plan_id": first_plan["plan_id"],
            "components": [],
        },
    )
    replayed_pack, replayed_plan = learning_packs.create_pack_with_component_plan(
        title="Retry-safe pack",
        plan_builder=build_plan,
        idempotency_key="stable-create-key",
        goal="Learn the same chapter",
    )

    assert replayed_pack["pack_id"] == first_pack["pack_id"]
    assert replayed_plan["plan_id"] == first_plan["plan_id"]
    assert plan_builds == 1
    assert len(learning_packs.list_packs()) == 1


def test_initial_pack_idempotency_key_rejects_changed_create_input(
    learning_workspace: PathService,
) -> None:
    def build_plan(_pack: dict[str, object]) -> dict[str, object]:
        return {"plan_id": "plan-stable", "status": "active", "components": []}

    learning_packs.create_pack_with_component_plan(
        title="Original intent",
        plan_builder=build_plan,
        idempotency_key="reused-create-key",
        request_fingerprint_payload={"instruction": "Learn chapter one"},
    )

    with pytest.raises(
        learning_packs.InvalidComponentPlanChain,
        match="different initial Pack request",
    ):
        learning_packs.create_pack_with_component_plan(
            title="Changed intent",
            plan_builder=build_plan,
            idempotency_key="reused-create-key",
            request_fingerprint_payload={"instruction": "Learn chapter two"},
        )

    assert len(learning_packs.list_packs()) == 1
