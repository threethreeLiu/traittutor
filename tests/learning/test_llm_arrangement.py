from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
import pytest

from traittutor import learning_packs
from traittutor.api.routers import learning_packs as router
from traittutor.components import PageStore
from traittutor.generate import service as generation_service
from traittutor.generate.courseware import CoursewareArtifact
from traittutor.generate.runner import GenerationConfigurationError
from traittutor.learning_components import (
    LearningComponentSelector,
    MaterialAffordance,
    MaterialComponentAffordances,
    SubjectSupportState,
    build_arranged_learning_component_plan,
    load_learning_component_catalog,
    validate_arrangement_payload,
)
from traittutor.orchestration import OrchestratorRunStore
from traittutor.services.path_service import PathService


@pytest.fixture
def learning_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)


def _affordances() -> MaterialComponentAffordances:
    unavailable = MaterialAffordance(suitable=False, confidence=0.2)
    return MaterialComponentAffordances(
        visual=unavailable,
        audio=unavailable,
        worked_example=unavailable,
        practice=MaterialAffordance(suitable=True, confidence=0.8),
    )


def _pack_with_plan(*, arrangement: str = "pending") -> tuple[dict[str, Any], dict[str, Any]]:
    pack = learning_packs.create_pack(
        title="Fractions",
        goal="Understand fractions",
        material={"source_type": "paste", "title": "Fractions", "text": "1/2 is a fraction."},
    )
    plan = (
        LearningComponentSelector()
        .select(
            pack_id=pack["pack_id"],
            goal="Understand fractions",
            subject_ref={"subject_id": "mathematics", "label": "Mathematics"},
            analysis_id="analysis-1",
            concept_signals=[],
            support_state=SubjectSupportState(subject_id="mathematics"),
            affordances=_affordances(),
        )
        .model_copy(update={"arrangement": arrangement})
    )
    saved = learning_packs.create_component_plan(pack["pack_id"], plan.model_dump(mode="json"))
    assert saved is not None
    return learning_packs.get_pack(pack["pack_id"]), saved


def _decision() -> dict[str, Any]:
    return {
        "rationale": "Map the goal, explain the concept, then practice.",
        "components": [
            {
                "component_type": "goal_map",
                "reason": "Show the route first.",
                "support_dimensions": ["goal_planning"],
                "required": True,
            },
            {
                "component_type": "concept_explanation",
                "reason": "Establish the central idea.",
                "support_dimensions": [],
                "required": True,
            },
            {
                "component_type": "guided_practice",
                "reason": "Create trusted practice evidence.",
                "support_dimensions": ["monitoring_regulation"],
                "required": True,
            },
            {
                "component_type": "calibration_checkpoint",
                "reason": "Compare confidence with feedback.",
                "support_dimensions": ["monitoring_regulation"],
                "required": True,
            },
        ],
    }


def test_arrangement_requires_a_finite_required_path() -> None:
    decision = {
        "rationale": "Show only a map.",
        "components": [
            {
                "component_type": "goal_map",
                "reason": "Show the route first.",
                "support_dimensions": ["goal_planning"],
                "required": False,
            }
        ],
    }

    with pytest.raises(ValueError, match="at least one required component"):
        validate_arrangement_payload(decision, catalog=load_learning_component_catalog())


@pytest.mark.asyncio
async def test_arrange_success_builds_superseding_plan(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack, initial = _pack_with_plan()

    async def arrange(_pack: object, active: dict[str, Any]):
        return build_arranged_learning_component_plan(active, _decision())

    monkeypatch.setattr(router, "arrange_learning_component_plan", arrange)
    response = await router.arrange_learning_component_path(pack["pack_id"])

    assert response["version"] == initial["version"] + 1
    assert response["supersedes_plan_id"] == initial["plan_id"]
    assert response["arrangement"] == "llm"
    assert [item["component_type"] for item in response["components"]] == [
        "goal_map",
        "concept_explanation",
        "guided_practice",
        "calibration_checkpoint",
    ]
    assert response["components"][0]["executor"] == "lesson"
    assert response["components"][0]["label_zh"]
    assert response["components"][0]["bkt_stage"] == initial["components"][0]["bkt_stage"]
    assert response["components"][2]["dependencies"] == []
    assert response["components"][3]["dependencies"] == [response["components"][2]["component_id"]]


@pytest.mark.asyncio
async def test_arrange_rejects_unknown_component_type(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack, initial = _pack_with_plan()

    async def invalid(_pack: object, _active: object):
        payload = _decision()
        payload["components"][1]["component_type"] = "mystery_component"
        validate_arrangement_payload(payload, catalog=load_learning_component_catalog())

    monkeypatch.setattr(router, "arrange_learning_component_plan", invalid)
    response = await router.arrange_learning_component_path(pack["pack_id"])

    assert response["fallback"] is True
    assert response["arrangement"] == "deterministic_fallback"
    assert response["plan_id"] == initial["plan_id"]
    assert learning_packs.get_pack(pack["pack_id"])["active_plan_id"] == initial["plan_id"]


@pytest.mark.asyncio
async def test_arrange_rejects_dependencies_field(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack, initial = _pack_with_plan()

    async def invalid(_pack: object, _active: object):
        payload = _decision()
        payload["components"][1]["dependencies"] = ["anything"]
        validate_arrangement_payload(payload, catalog=load_learning_component_catalog())

    monkeypatch.setattr(router, "arrange_learning_component_plan", invalid)
    response = await router.arrange_learning_component_path(pack["pack_id"])

    assert response["fallback"] is True
    assert response["plan_id"] == initial["plan_id"]
    assert all(
        not item["dependencies"]
        for item in response["components"]
        if item["component_type"] != "calibration_checkpoint"
    )


@pytest.mark.asyncio
async def test_arrange_no_model_returns_409_configuration_required(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No configured model must surface as the typed 409, not a silent fallback.

    Retrying a configuration-less arrangement would only burn a doomed LLM
    call, so the endpoint reports ``model_configuration_required`` for the
    client's Model-settings branch instead of pretending a fallback helped.
    """
    pack, initial = _pack_with_plan()

    async def unavailable(_pack: object, _active: object):
        raise GenerationConfigurationError("No generation model")

    monkeypatch.setattr(router, "arrange_learning_component_plan", unavailable)
    with pytest.raises(HTTPException) as error:
        await router.arrange_learning_component_path(pack["pack_id"])
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "model_configuration_required"
    assert learning_packs.get_pack(pack["pack_id"])["active_plan_id"] == initial["plan_id"]


@pytest.mark.asyncio
async def test_arrange_after_start_preserves_started_prefix(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canvas retry may arrange after a component started: the superseding
    plan keeps the started prefix immutable and only re-arranges the tail."""
    pack, initial = _pack_with_plan()
    first = initial["components"][0]
    learning_packs.record_component_event(
        pack["pack_id"],
        initial["plan_id"],
        first["component_id"],
        {"event_id": "start-1", "action": "start"},
    )

    async def arrange(_pack: object, active: dict[str, Any]):
        return build_arranged_learning_component_plan(active, _decision())

    monkeypatch.setattr(router, "arrange_learning_component_plan", arrange)
    response = await router.arrange_learning_component_path(pack["pack_id"])

    assert response["arrangement"] == "llm"
    assert response["supersedes_plan_id"] == initial["plan_id"]
    types = [item["component_type"] for item in response["components"]]
    # The started goal_map is preserved as the immutable first component.
    assert types[0] == "goal_map"
    assert response["components"][0]["component_id"] == first["component_id"]
    # Arranged types already present in the preserved prefix are not repeated.
    assert types.count("goal_map") == 1
    assert types == [
        "goal_map",
        "concept_explanation",
        "guided_practice",
        "calibration_checkpoint",
    ]


def test_arranged_tail_drops_assessment_after_preserved_inflight_assessment() -> None:
    """Two graded assessments may never be adjacent: an arranged assessment
    landing directly after a preserved in-flight assessment (legacy prefix
    without its calibration) is dropped so the learner's current attempt stays
    the active evidence step."""

    def component(component_id: str, component_type: str, *, status: str) -> dict[str, Any]:
        return {
            "component_id": component_id,
            "component_type": component_type,
            "executor": "assessment"
            if component_type in {"guided_practice", "transfer_challenge"}
            else "deterministic",
            "label_zh": component_type,
            "label_en": component_type,
            "concept_refs": [],
            "support_dimensions": [],
            "bkt_stage": "developing",
            "modality": "interactive"
            if component_type in {"guided_practice", "transfer_challenge"}
            else "text",
            "dependencies": [],
            "required": True,
            "reason": "test",
            "evidence_refs": [],
            "completion_event": "quiz_answer"
            if component_type in {"guided_practice", "transfer_challenge"}
            else "courseware_outcome",
            "status": status,
        }

    baseline = {
        "plan_id": "plan-inflight",
        "pack_id": "pack-adjacent",
        "version": 2,
        "goal": "Understand fractions",
        "support_state_snapshot": {"subject_id": "mathematics"},
        "components": [
            component("gm", "goal_map", status="completed"),
            component("gp", "guided_practice", status="active"),
        ],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    decision = {
        "rationale": "Practice evidence first.",
        "components": [
            {
                "component_type": "goal_map",
                "reason": "Route first.",
                "support_dimensions": ["goal_planning"],
                "required": True,
            },
            {
                "component_type": "guided_practice",
                "reason": "Practice evidence.",
                "support_dimensions": ["monitoring_regulation"],
                "required": True,
            },
            {
                "component_type": "transfer_challenge",
                "reason": "Apply in a new context.",
                "support_dimensions": ["reflection_transfer"],
                "required": True,
            },
            {
                "component_type": "calibration_checkpoint",
                "reason": "Compare confidence.",
                "support_dimensions": ["monitoring_regulation"],
                "required": True,
            },
        ],
    }
    arranged = build_arranged_learning_component_plan(baseline, decision)
    types = [item.component_type for item in arranged.components]
    assert types == ["goal_map", "guided_practice", "calibration_checkpoint"], types
    # the arranged transfer_challenge was dropped instead of stacking next to
    # the preserved in-flight guided_practice
    assert "transfer_challenge" not in types
    # the tail calibration pairs with the preserved in-flight assessment
    assert arranged.components[2].dependencies == [arranged.components[1].component_id]


@pytest.mark.asyncio
async def test_arrange_idempotent(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack, plan = _pack_with_plan(arrangement="llm")
    calls = 0

    async def should_not_run(_pack: object, _active: object):
        nonlocal calls
        calls += 1
        raise AssertionError("Idempotent arrangement replay called the model")

    monkeypatch.setattr(router, "arrange_learning_component_plan", should_not_run)
    first = await router.arrange_learning_component_path(pack["pack_id"])
    second = await router.arrange_learning_component_path(pack["pack_id"])
    assert first["plan_id"] == second["plan_id"] == plan["plan_id"]
    assert first["idempotent_replay"] is True
    assert calls == 0


@pytest.mark.asyncio
async def test_goal_map_generation_does_not_publish_arrangement_explanations(
    learning_workspace: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pack, initial = _pack_with_plan()
    arranged = build_arranged_learning_component_plan(initial, _decision()).model_dump(mode="json")
    saved = learning_packs.create_component_plan(pack["pack_id"], arranged)
    assert saved is not None
    goal_map = saved["components"][0]
    captured: dict[str, Any] = {}

    async def courseware(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return CoursewareArtifact(
            lesson={
                "title": "Fractions",
                "lesson_goal": "Understand fractions",
                "sections": [
                    {
                        "section_title": "Equivalent fractions",
                        "goal": "Recognize equivalent fractions",
                    }
                ],
                "final_takeaways": ["Fractions represent equal parts of a whole"],
            },
            content_analysis={"core_concepts": [{"concept_id": "fraction", "label": "Fraction"}]},
            adaptation_plan={},
            trace=[],
        )

    run_store = OrchestratorRunStore(tmp_path / "orchestrator-runs.json")
    page_store = PageStore(path=tmp_path / "pages.json")
    monkeypatch.setattr(generation_service, "generate_courseware", courseware)
    monkeypatch.setattr(generation_service, "_orchestrator_run_store", lambda: run_store)
    monkeypatch.setattr(generation_service, "_page_store", lambda: page_store)
    context = generation_service._arrangement_context_for_component(
        goal_map["component_id"], language="en"
    )
    assert context is not None

    await generation_service._generate_courseware_with_orchestrator(
        generation_id="generation-arranged-goal-map",
        title="Fractions",
        chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "1/2 is a fraction."}],
        learner_strategy={},
        slr_support={},
        language="en",
        learning_targets={},
        visual_seed={
            "component_id": goal_map["component_id"],
            "component_type": "goal_map",
            "visual_targets": [],
        },
        requested_component_types=("goal_map",),
        arrangement_context=context,
    )

    assert captured["arrangement_context"] == context
    page = page_store.get("generation-arranged-goal-map:page")
    assert page is not None
    rendered = next(
        region.component
        for region in page.regions
        if region.component is not None and region.component.component_type == "goal_map"
    )
    assert rendered.props == {
        "title": "Understand fractions",
        "milestones": [
            "Fractions represent equal parts of a whole",
            "Recognize equivalent fractions",
            "Fraction",
        ],
    }
    serialized_props = str(rendered.props)
    assert context["rationale"] not in serialized_props
    assert all(
        item["label"] not in serialized_props and item["reason"] not in serialized_props
        for item in context["components"]
    )
