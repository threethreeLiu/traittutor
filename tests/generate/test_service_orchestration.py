"""Live courseware composition root uses the durable orchestrator path."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.components import PageStore
from traittutor.generate import service
from traittutor.orchestration import OrchestratorRunStore


@pytest.fixture(autouse=True)
def _deterministic_legacy_scenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests assert the rollback executor counts, not the agentic path."""
    monkeypatch.setenv("TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE", "deterministic")


@pytest.mark.asyncio
async def test_orchestrated_helper_replays_from_durable_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    calls = {"courseware": 0, "visual": 0}

    async def courseware(**_kwargs: Any) -> Any:
        calls["courseware"] += 1
        return SimpleNamespace(
            lesson={
                "title": "Limits",
                "sections": [
                    {"section_title": "Intro", "core_content": "A limit describes behavior."}
                ],
            },
            trace=[],
        )

    async def visual(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["visual"] += 1
        return {
            "status": "completed",
            "asset": {"url": "/media/limit.png", "alt": "Limit diagram"},
        }

    store = OrchestratorRunStore(tmp_path / "orchestrator-runs.json")
    monkeypatch.setattr(service, "generate_courseware", courseware)
    monkeypatch.setattr(service, "generate_learning_visual", visual)
    monkeypatch.setattr(service, "_orchestrator_run_store", lambda: store)
    # Isolate the WS-9B publish side-effect from the shared workspace PageStore.
    monkeypatch.setattr(service, "_page_store", lambda: PageStore(path=tmp_path / "pages.json"))
    kwargs = {
        "generation_id": "generation-one",
        "title": "Limits",
        "chunks": [{"chunk_id": "c1", "source_id": "s1", "text": "Limits."}],
        "learner_strategy": {"mode": "scaffolded"},
        "slr_support": {},
        "language": "en",
        "learning_targets": {"courseware_targets": ["limits"]},
        "visual_seed": {"title": "Limits", "visual_targets": ["limits"]},
    }

    first = await service._generate_courseware_with_orchestrator(**kwargs)
    second = await service._generate_courseware_with_orchestrator(**kwargs)

    assert first == second
    assert calls == {"courseware": 1, "visual": 1}
    assert store.get_by_key(first["trace"][0]["run_key"]) is not None


@pytest.mark.asyncio
async def test_orchestrated_page_published_to_page_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """WS-9B: the orchestrator's validated page is published to the PageStore the
    router reads (keyed ``f"{generation_id}:page"``), so the page is served directly
    instead of re-projected from the legacy GenerationResult — which would drop
    every region except ``concept_explanation``."""
    calls = {"courseware": 0, "visual": 0}

    async def courseware(**_kwargs: Any) -> Any:
        calls["courseware"] += 1
        return SimpleNamespace(
            lesson={
                "title": "Limits",
                "sections": [{"section_title": "Intro", "core_content": "A limit."}],
            },
            trace=[],
        )

    async def visual(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["visual"] += 1
        return {"status": "completed", "asset": {"url": "/media/limit.png", "alt": "Limit"}}

    run_store = OrchestratorRunStore(tmp_path / "orchestrator-runs.json")
    page_store = PageStore(path=tmp_path / "pages.json")
    monkeypatch.setattr(service, "generate_courseware", courseware)
    monkeypatch.setattr(service, "generate_learning_visual", visual)
    monkeypatch.setattr(service, "_orchestrator_run_store", lambda: run_store)
    monkeypatch.setattr(service, "_page_store", lambda: page_store)

    await service._generate_courseware_with_orchestrator(
        generation_id="gen-ws9b",
        title="Limits",
        chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "Limits."}],
        learner_strategy={"mode": "scaffolded"},
        slr_support={},
        language="en",
        learning_targets={"courseware_targets": ["limits"]},
        visual_seed={"title": "Limits", "visual_targets": ["limits"]},
    )

    # The orchestrator page reached the router's store under its lookup key.
    published = page_store.get("gen-ws9b:page")
    assert published is not None
    component_types = {
        region.component.component_type
        for region in published.regions
        if region.component is not None
    }
    assert "concept_explanation" in component_types


@pytest.mark.asyncio
async def test_selected_visual_component_keeps_visual_agent_in_adaptive_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    calls = {"visual": 0}

    async def courseware(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            lesson={
                "title": "Cell respiration",
                "sections": [
                    {
                        "section_title": "Energy flow",
                        "core_content": "Cells transfer energy through linked reactions.",
                    }
                ],
            },
            trace=[],
        )

    async def visual(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["visual"] += 1
        return {
            "status": "completed",
            "asset": {"url": "/media/respiration.png", "alt": "Cell energy flow"},
        }

    monkeypatch.setattr(service, "generate_courseware", courseware)
    monkeypatch.setattr(service, "generate_learning_visual", visual)
    monkeypatch.setattr(
        service,
        "_orchestrator_run_store",
        lambda: OrchestratorRunStore(tmp_path / "orchestrator-runs.json"),
    )
    page_store = PageStore(path=tmp_path / "pages.json")
    monkeypatch.setattr(service, "_page_store", lambda: page_store)

    await service._generate_courseware_with_orchestrator(
        generation_id="generation-selected-visual",
        title="Cell respiration",
        chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "Cell respiration."}],
        learner_strategy={"mode": "scaffolded"},
        slr_support={},
        language="en",
        learning_targets={"courseware_targets": ["respiration"]},
        visual_seed={
            "title": "Cell respiration",
            "visual_targets": ["energy flow"],
            "component_id": "cmp-visual",
            "component_type": "visual_map",
        },
        requested_component_types=("concept_explanation",),
    )

    page = page_store.get("generation-selected-visual:page")
    assert page is not None
    assert calls == {"visual": 1}
    assert "visual_map" in {
        region.component.component_type for region in page.regions if region.component is not None
    }


@pytest.mark.asyncio
async def test_selected_video_component_uses_video_body_and_publishes_player_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    calls = {"image": 0, "video": 0}

    async def courseware(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            lesson={
                "title": "Cell respiration",
                "sections": [
                    {
                        "section_title": "Energy flow",
                        "core_content": "Cells transfer energy through linked reactions.",
                    }
                ],
            },
            trace=[],
        )

    async def image(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["image"] += 1
        return {"status": "failed", "message": "image should not run"}

    async def video(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["video"] += 1
        return {
            "status": "completed",
            "asset": {
                "url": "/api/outputs/learning-video.mp4",
                "alt": "Cell energy flow animation",
            },
        }

    monkeypatch.setattr(service, "generate_courseware", courseware)
    monkeypatch.setattr(service, "generate_learning_visual", image)
    monkeypatch.setattr(service, "generate_learning_video", video)
    monkeypatch.setattr(
        service,
        "_orchestrator_run_store",
        lambda: OrchestratorRunStore(tmp_path / "orchestrator-runs.json"),
    )
    page_store = PageStore(path=tmp_path / "pages.json")
    monkeypatch.setattr(service, "_page_store", lambda: page_store)

    await service._generate_courseware_with_orchestrator(
        generation_id="generation-selected-video",
        title="Cell respiration",
        chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "Cell respiration."}],
        learner_strategy={"mode": "scaffolded"},
        slr_support={},
        language="en",
        learning_targets={"courseware_targets": ["respiration"]},
        visual_seed={
            "title": "Cell respiration",
            "visual_targets": ["energy flow"],
            "component_id": "cmp-video",
            "component_type": "video_explanation",
        },
        requested_component_types=("concept_explanation",),
    )

    page = page_store.get("generation-selected-video:page")
    assert page is not None
    assert calls == {"image": 0, "video": 1}
    component = next(
        region.component
        for region in page.regions
        if region.component is not None and region.component.component_type == "video_explanation"
    )
    assert component.props["media_url"] == "/api/outputs/learning-video.mp4"


@pytest.mark.asyncio
async def test_selected_audio_component_generates_podcast_once_and_keeps_text_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    calls = {"courseware": 0, "podcast": 0}

    async def courseware(**_kwargs: Any) -> Any:
        calls["courseware"] += 1
        return SimpleNamespace(
            lesson={
                "title": "Linear algebra",
                "sections": [
                    {
                        "section_title": "Matrix transformations",
                        "core_content": "A matrix can represent a linear transformation.",
                    }
                ],
            },
            trace=[],
        )

    async def podcast(**_kwargs: Any) -> dict[str, Any]:
        calls["podcast"] += 1
        return {
            "status": "completed",
            "title": "Matrix podcast",
            "script": "Welcome. A matrix can represent a linear transformation.",
        }

    monkeypatch.setattr(service, "generate_courseware", courseware)
    monkeypatch.setattr(service, "generate_podcast_narration", podcast)
    monkeypatch.setattr(
        service,
        "_orchestrator_run_store",
        lambda: OrchestratorRunStore(tmp_path / "orchestrator-runs.json"),
    )
    page_store = PageStore(path=tmp_path / "pages.json")
    monkeypatch.setattr(service, "_page_store", lambda: page_store)
    kwargs = {
        "generation_id": "generation-selected-audio",
        "title": "Linear algebra",
        "chunks": [{"chunk_id": "c1", "source_id": "s1", "text": "Matrices."}],
        "learner_strategy": {"mode": "scaffolded"},
        "slr_support": {},
        "language": "en",
        "learning_targets": {"courseware_targets": ["matrices"]},
        "visual_seed": {
            "title": "Linear algebra",
            "visual_targets": [],
            "component_id": "cmp-audio",
            "component_type": "audio_explanation",
        },
        "requested_component_types": ("audio_explanation",),
    }

    first = await service._generate_courseware_with_orchestrator(**kwargs)
    second = await service._generate_courseware_with_orchestrator(**kwargs)

    assert first == second
    assert calls == {"courseware": 1, "podcast": 1}
    assert first["podcast_title"] == "Matrix podcast"
    assert first["podcast_script"].startswith("Welcome")
    page = page_store.get("generation-selected-audio:page")
    assert page is not None
    assert {
        region.component.component_type for region in page.regions if region.component is not None
    } == {"audio_explanation"}


@pytest.mark.asyncio
async def test_production_executor_map_emits_instruction_practice_and_srl_components(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    calls = {"courseware": 0}

    async def courseware(**_kwargs: Any) -> Any:
        calls["courseware"] += 1
        return SimpleNamespace(
            lesson={
                "title": "Limits",
                "lesson_goal": "Explain nearby-value behavior.",
                "sections": [
                    {
                        "section_title": "Approaching a value",
                        "goal": "Interpret the notation.",
                        "core_content": "A limit describes nearby behavior.",
                        "checkpoint": {
                            "question": "What does approaching 2 mean?",
                            "feedback_if_confused": "Compare values on both sides.",
                        },
                        "reflection_prompt": "Explain the trend in your own words.",
                        "references": ["c1"],
                        "external_claims": [],
                    }
                ],
                "next_step_guidance": "Try another nearby-value example.",
            },
            trace=[],
        )

    run_store = OrchestratorRunStore(tmp_path / "orchestrator-runs.json")
    page_store = PageStore(path=tmp_path / "pages.json")
    monkeypatch.setattr(service, "generate_courseware", courseware)
    monkeypatch.setattr(service, "_orchestrator_run_store", lambda: run_store)
    monkeypatch.setattr(service, "_page_store", lambda: page_store)

    result = await service._generate_courseware_with_orchestrator(
        generation_id="generation-specialists",
        title="Limits",
        chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "Limits."}],
        learner_strategy={"mode": "scaffolded"},
        slr_support={"reflection_transfer": {"emphasis": "standard"}},
        language="en",
        learning_targets={"courseware_targets": ["limits"]},
        visual_seed={"title": "Limits", "visual_targets": []},
        requested_component_types=(
            "concept_explanation",
            "guided_practice",
            "reflection_prompt",
        ),
    )

    assert calls == {"courseware": 1}
    page = page_store.get("generation-specialists:page")
    assert page is not None
    assert [
        region.component.component_type for region in page.regions if region.component is not None
    ] == ["concept_explanation", "guided_practice", "reflection_prompt"]
    task_results = {item["task_id"]: item for item in result["trace"][0]["task_results"]}
    assert task_results["material"]["status"] == "succeeded"
    assert task_results["practice"]["component_count"] > 0
    assert task_results["srl"]["component_count"] > 0
    assert task_results["ui_composer"]["status"] == "succeeded"
    assert task_results["evaluator"]["status"] == "succeeded"
    assert all(
        set(item) == {"task_id", "status", "component_count"} for item in task_results.values()
    )
    assert result["orchestration"] == {
        "run_id": result["trace"][0]["orchestrator_run_id"],
        "status": "succeeded",
        "mode": "deterministic",
        "agents": [
            {
                "task_id": task_id,
                "status": task_results[task_id]["status"],
                "component_count": task_results[task_id]["component_count"],
            }
            for task_id in (
                "material",
                "instruction",
                "practice",
                "srl",
                "ui_composer",
                "evaluator",
            )
        ],
    }
