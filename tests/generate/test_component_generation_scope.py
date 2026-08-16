"""Independent learning-component generation stays scoped to one component."""

from __future__ import annotations

from typing import Any

import pytest

from traittutor.components import PageStore
from traittutor.generate import service
from traittutor.generate.courseware import CoursewareArtifact
from traittutor.orchestration import OrchestratorRunStore


@pytest.mark.asyncio
async def test_selected_goal_map_uses_llm_artifact_without_generating_other_components(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    calls = {"courseware": 0}

    async def courseware(**_kwargs: Any) -> Any:
        calls["courseware"] += 1
        return CoursewareArtifact(
            lesson={
                "title": "Derivatives",
                "lesson_goal": "Explain derivatives as local rates of change.",
                "sections": [
                    {
                        "section_title": "From change to rate",
                        "goal": "Connect secant slopes to tangent slopes.",
                        "core_content": "A derivative is a limit of average rates of change.",
                    }
                ],
                "final_takeaways": ["A derivative describes a local rate of change."],
            },
            content_analysis={
                "core_concepts": [
                    {"concept_id": "derivative", "label": "Derivative"},
                    {"concept_id": "limit", "label": "Limit of average rates"},
                ]
            },
            adaptation_plan={},
            trace=[],
        )

    monkeypatch.setenv("TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE", "deterministic")
    monkeypatch.setattr(service, "generate_courseware", courseware)
    monkeypatch.setattr(
        service,
        "_orchestrator_run_store",
        lambda: OrchestratorRunStore(tmp_path / "orchestrator-runs.json"),
    )
    page_store = PageStore(path=tmp_path / "pages.json")
    monkeypatch.setattr(service, "_page_store", lambda: page_store)

    await service._generate_courseware_with_orchestrator(
        generation_id="generation-goal-map-only",
        title="Derivatives",
        chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "Derivative source."}],
        learner_strategy={"mode": "scaffolded"},
        slr_support={},
        language="en",
        learning_targets={"courseware_targets": ["derivatives"]},
        visual_seed={
            "title": "Derivatives",
            "visual_targets": [],
            "component_id": "cmp-goal-map",
            "component_type": "goal_map",
        },
        # The broader adaptive recommendation must not leak into this explicit
        # one-component generation request.
        requested_component_types=(
            "concept_explanation",
            "guided_practice",
            "reflection_prompt",
        ),
    )

    page = page_store.get("generation-goal-map-only:page")
    assert page is not None
    assert calls == {"courseware": 1}
    assert [
        region.component.component_type for region in page.regions if region.component is not None
    ] == ["goal_map"]
    assert page.regions[0].component is not None
    assert page.regions[0].component.props["title"] == (
        "Explain derivatives as local rates of change."
    )
    assert page.regions[0].component.props["milestones"] == [
        "A derivative describes a local rate of change.",
        "Connect secant slopes to tangent slopes.",
        "Derivative",
        "Limit of average rates",
    ]


@pytest.mark.asyncio
async def test_unknown_explicit_component_never_falls_back_to_full_pack_generation() -> None:
    with pytest.raises(ValueError, match="Unknown learning component type"):
        await service._generate_courseware_with_orchestrator(
            generation_id="generation-unknown-component",
            title="Derivatives",
            chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "Derivative source."}],
            learner_strategy={},
            slr_support={},
            language="en",
            learning_targets={},
            visual_seed={
                "component_id": "cmp-unknown",
                "component_type": "unregistered_component",
                "visual_targets": [],
            },
            requested_component_types=("concept_explanation", "guided_practice"),
        )
