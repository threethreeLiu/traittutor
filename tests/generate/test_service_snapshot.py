"""Generation consumes canonical context snapshot references."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from traittutor.context_assembler.snapshot import (
    ConceptSignalRef,
    LearningContextSnapshot,
    SnapshotReadRanges,
)
from traittutor.generate import service
from traittutor.generate.service import GenerationRequest, MaterialSource
from traittutor.multi_user.paths import local_admin_user
from traittutor.personalization.models import (
    ConceptSignal,
    PersonalizationContext,
    TeachingStrategyPlan,
)


@pytest.fixture(autouse=True)
def _use_deterministic_rollback_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE", "deterministic")


def _personalization(*, degraded: bool = False) -> PersonalizationContext:
    return PersonalizationContext(
        purpose="courseware",
        plan=TeachingStrategyPlan(),
        trace_id="personalization-test",
        degraded=degraded,
        degradation_reason="read_failed" if degraded else None,
    )


def test_agentic_support_state_is_qualitative_and_evidence_gated() -> None:
    context = _personalization().model_copy(
        update={
            "relevant_concept_signals": [
                ConceptSignal(
                    concept_id="limits",
                    label="Limits",
                    support_level="supported",
                    confidence=0.9,
                    attempt_count=3,
                    mastery_probability=0.98,
                    verified_observation_count=3,
                    bkt_param_version="calibrated-v2",
                    bkt_calibrated=True,
                ),
                ConceptSignal(
                    concept_id="derivatives",
                    label="Derivatives",
                    support_level="supported",
                    confidence=0.9,
                    attempt_count=1,
                    mastery_probability=0.95,
                    verified_observation_count=1,
                    bkt_param_version="calibrated-v2",
                    bkt_calibrated=True,
                ),
            ]
        }
    )

    support = service._qualitative_courseware_support_state(context)

    assert support["limits"]["evidence_state"] == "supported"
    assert support["derivatives"]["evidence_state"] == "insufficient_evidence"
    serialized = str(support)
    assert "mastery_probability" not in serialized
    assert "0.98" not in serialized


def _snapshot(*, degraded: bool = False) -> LearningContextSnapshot:
    return LearningContextSnapshot(
        trace_id="snapshot-test",
        created_at="2026-08-09T08:00:00+00:00",
        user_id="local-admin",
        token_budget=8_000,
        read_ranges=SnapshotReadRanges(
            thread_version="2",
            learner_profile_version="profile-v1",
            concept_signal_refs=[ConceptSignalRef(concept_id="limits", version="concept-v1")],
        ),
        degraded=degraded,
        degradation_reason="read_failed" if degraded else None,
    )


async def _run(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> object:
    async def fake_courseware(**kwargs: Any) -> object:
        captured.update(kwargs["learner_strategy"])
        return SimpleNamespace(
            lesson={
                "title": "Limits",
                "sections": [{"section_title": "Intro", "core_content": "x"}],
            },
            trace=[],
        )

    monkeypatch.setattr(service, "generate_courseware", fake_courseware)
    monkeypatch.setattr(
        service,
        "should_generate_learning_visual",
        lambda **_kwargs: {
            "should_generate": False,
            "reason": "test",
            "visual_targets": [],
            "support_reasons": [],
        },
    )
    return await service.generate_traittutor_content_async(
        GenerationRequest(
            "courseware", MaterialSource("paste", "Limits are foundational.", "Limits")
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("degraded", [False, True])
async def test_snapshot_grounds_prompt_and_tolerates_degradation(
    monkeypatch: pytest.MonkeyPatch, degraded: bool
) -> None:

    class FakeAssembler:
        def __init__(self) -> None:
            self.personalization_context = _personalization(degraded=degraded)
            self.tutor_persona_context = None

        def assemble(self, **_kwargs: object) -> LearningContextSnapshot:
            return _snapshot(degraded=degraded)

    monkeypatch.setattr(service, "ContextAssembler", FakeAssembler)
    captured: dict[str, Any] = {}
    await _run(monkeypatch, captured)

    assert captured["learning_focus"][0]["concept_id"] == "limits"
    assert captured["context_references"]["thread_version"] == "2"
    assert captured["context_references"]["learner_profile_version"] == "profile-v1"


@pytest.mark.asyncio
async def test_page_schema_uses_orchestrator_exclusively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Assembler:
        def __init__(self) -> None:
            self.personalization_context = _personalization()
            self.tutor_persona_context = None

        def assemble(self, **_kwargs: Any) -> LearningContextSnapshot:
            return _snapshot()

    monkeypatch.setattr(service, "ContextAssembler", _Assembler)
    calls = {"orchestrator": 0}

    async def orchestrated(**_kwargs: Any) -> dict[str, Any]:
        calls["orchestrator"] += 1
        return {
            "kind": "courseware",
            "title": "Limits",
            "sections": [],
            "markdown": "",
            "save_target": "notebook",
            "trace": [],
        }

    async def direct(**_kwargs: Any) -> object:
        raise AssertionError("the canonical PageSchema path must not call the direct generator")

    monkeypatch.setattr(service, "_generate_courseware_with_orchestrator", orchestrated)
    monkeypatch.setattr(service, "generate_courseware", direct)
    monkeypatch.setattr(
        service,
        "should_generate_learning_visual",
        lambda **_kwargs: {
            "should_generate": False,
            "reason": "test",
            "visual_targets": [],
            "support_reasons": [],
        },
    )

    await service.generate_traittutor_content_async(
        GenerationRequest(
            "courseware", MaterialSource("paste", "Limits are foundational.", "Limits")
        )
    )
    assert calls == {"orchestrator": 1}


@pytest.mark.asyncio
async def test_context_authorization_is_derived_not_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invariant #7: the generation context gate is derived from identity and fails
    closed for an anonymous caller — never a literal ``user_authorized=True`` that
    would silently pull cross-partition memory."""
    captured: dict[str, Any] = {}

    class _CapturingAssembler:
        def __init__(self) -> None:
            self.personalization_context = _personalization()
            self.tutor_persona_context = None

        def assemble(self, **kwargs: Any) -> LearningContextSnapshot:
            captured.update(kwargs)
            return _snapshot()

    monkeypatch.setattr(service, "ContextAssembler", _CapturingAssembler)

    # Anonymous / missing identity → the own-behalf gate must fail closed (#7).
    monkeypatch.setattr(
        service, "get_current_user", lambda: dataclasses.replace(local_admin_user(), id="")
    )
    await _run(monkeypatch, {})
    assert captured.get("user_authorized") is False

    # An authenticated caller operating on their own behalf keeps their context
    # (a derived True, consistent with agent_runtime/graph.py — not a leak).
    captured.clear()
    monkeypatch.setattr(service, "get_current_user", local_admin_user)
    await _run(monkeypatch, {})
    assert captured.get("user_authorized") is True


# ---------------------------------------------------------------------------
# B+C material-analysis reuse: per-component generation recovers the persisted
# upload analysis from material metadata so it never re-runs content-analysis.
# ---------------------------------------------------------------------------


def _analysis_payload() -> dict[str, Any]:
    return {
        "analysis_id": "analysis-reused-1",
        "session_id": "session-reused-1",
        "owner_id": "local-admin",
        "source_id": "material-upload-1",
        "created_at": "2026-08-14T00:00:00+00:00",
        "component_affordances": {"practice": {"suitable": True}},
        "concept_candidates": [
            {"concept_id": "c1", "label": "Limits", "evidence_chunk_ids": ["chunk-1"]}
        ],
        "language": "en",
        "language_confidence": 0.9,
        "trace": [],
        "version": 1,
        "subject": "other",
        "sub_subject": "Calculus foundations",
        "chinese_grade": "unknown",
        "international_grade": "unknown",
        "difficulty": "standard",
        "confidence": 0.7,
        "evidence": [],
        "augmentation_needed": False,
        "augmentation_reason": "",
    }


async def _generate_with_metadata(
    monkeypatch: pytest.MonkeyPatch,
    metadata: dict[str, Any],
    *,
    captured: dict[str, Any],
) -> None:
    class _Assembler:
        def __init__(self) -> None:
            self.personalization_context = _personalization()
            self.tutor_persona_context = None

        def assemble(self, **_kwargs: Any) -> LearningContextSnapshot:
            return _snapshot()

    monkeypatch.setattr(service, "ContextAssembler", _Assembler)

    async def orchestrated(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "kind": "courseware",
            "title": "Limits",
            "sections": [],
            "markdown": "",
            "save_target": "notebook",
            "trace": [],
        }

    monkeypatch.setattr(service, "_generate_courseware_with_orchestrator", orchestrated)
    monkeypatch.setattr(
        service,
        "should_generate_learning_visual",
        lambda **_kwargs: {
            "should_generate": False,
            "reason": "test",
            "visual_targets": [],
            "support_reasons": [],
        },
    )
    request = GenerationRequest(
        "courseware",
        MaterialSource(
            "paste",
            "Limits are foundational.",
            "Limits.pdf",
            source_id="material-upload-1",
            metadata=metadata,
        ),
    )
    await service.generate_traittutor_content_async(request)


@pytest.mark.asyncio
async def test_generation_reuses_persisted_analysis_from_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Pack material carries ``learner_analyses``; per-component generation
    must recover that record so the B+C content-analysis skip actually fires."""
    loaded: dict[str, Any] = {}
    monkeypatch.setattr(
        service,
        "load_material_analysis",
        lambda analysis_id, session_id, enforce_owner=False: (
            loaded.update(analysis_id=analysis_id, session_id=session_id) or _analysis_payload()
        ),
    )
    captured: dict[str, Any] = {}
    await _generate_with_metadata(
        monkeypatch,
        {
            "learning_session_id": "session-reused-1",
            "learner_analyses": [_analysis_payload()],
        },
        captured=captured,
    )

    assert loaded == {"analysis_id": "analysis-reused-1", "session_id": "session-reused-1"}
    analysis = captured.get("material_analysis")
    assert analysis is not None
    assert analysis["analysis_id"] == "analysis-reused-1"
    assert analysis["source_id"] == "material-upload-1"


@pytest.mark.asyncio
async def test_generation_without_metadata_keeps_llm_analysis_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No persisted analysis → no load attempt and the existing fallback path."""
    loaded: list[Any] = []
    monkeypatch.setattr(
        service,
        "load_material_analysis",
        lambda *args, **kwargs: loaded.append(args) or _analysis_payload(),
    )
    captured: dict[str, Any] = {}
    await _generate_with_metadata(monkeypatch, {}, captured=captured)

    assert loaded == []
    assert captured.get("material_analysis") is None


@pytest.mark.asyncio
async def test_generation_stale_analysis_falls_back_not_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deleted/unreadable persisted analysis degrades to the LLM stage
    instead of failing the whole component generation."""
    monkeypatch.setattr(
        service,
        "load_material_analysis",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("analysis-reused-1")),
    )
    captured: dict[str, Any] = {}
    await _generate_with_metadata(
        monkeypatch,
        {
            "learning_session_id": "session-reused-1",
            "learner_analyses": [_analysis_payload()],
        },
        captured=captured,
    )

    assert captured.get("material_analysis") is None


@pytest.mark.asyncio
async def test_generation_mismatched_analysis_falls_back_not_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted analysis whose source does not match the material falls back
    to the LLM stage instead of failing the component generation."""
    stored = {"analysis-reused-1": {**_analysis_payload(), "source_id": "material-some-other-file"}}

    def load(analysis_id: str, session_id: str, *, enforce_owner: bool = False) -> dict[str, Any]:
        payload = stored[analysis_id]
        assert payload["session_id"] == session_id
        return payload

    monkeypatch.setattr(service, "load_material_analysis", load)
    captured: dict[str, Any] = {}
    await _generate_with_metadata(
        monkeypatch,
        {
            "learning_session_id": "session-reused-1",
            "learner_analyses": [_analysis_payload()],
        },
        captured=captured,
    )

    assert captured.get("material_analysis") is None


# ---------------------------------------------------------------------------
# Client-supplied analysis_id: an unreadable record degrades to the LLM stage,
# but a readable record naming a different source stays a hard error.
# ---------------------------------------------------------------------------


async def _generate_with_client_analysis(
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, Any],
    *,
    captured: dict[str, Any],
) -> None:
    class _Assembler:
        def __init__(self) -> None:
            self.personalization_context = _personalization()
            self.tutor_persona_context = None

        def assemble(self, **_kwargs: Any) -> LearningContextSnapshot:
            return _snapshot()

    monkeypatch.setattr(service, "ContextAssembler", _Assembler)

    async def orchestrated(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "kind": "courseware",
            "title": "Limits",
            "sections": [],
            "markdown": "",
            "save_target": "notebook",
            "trace": [],
        }

    monkeypatch.setattr(service, "_generate_courseware_with_orchestrator", orchestrated)
    monkeypatch.setattr(
        service,
        "should_generate_learning_visual",
        lambda **_kwargs: {
            "should_generate": False,
            "reason": "test",
            "visual_targets": [],
            "support_reasons": [],
        },
    )
    request = GenerationRequest(
        "courseware",
        MaterialSource(
            "paste",
            "Limits are foundational.",
            "Limits.pdf",
            source_id="material-upload-1",
            metadata={},
        ),
        options=options,
    )
    await service.generate_traittutor_content_async(request)


@pytest.mark.asyncio
async def test_generation_client_supplied_unreadable_analysis_falls_back_not_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client-supplied analysis id whose record was deleted or belongs to
    another owner degrades to the LLM stage instead of failing generation."""
    monkeypatch.setattr(
        service,
        "load_material_analysis",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("analysis-reused-1")),
    )
    captured: dict[str, Any] = {}
    await _generate_with_client_analysis(
        monkeypatch,
        {"analysis_id": "analysis-reused-1", "session_id": "session-reused-1"},
        captured=captured,
    )

    assert captured.get("material_analysis") is None


@pytest.mark.asyncio
async def test_generation_client_supplied_mismatched_analysis_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A readable client-supplied analysis naming a different source remains a
    hard ValueError — client bug / tamper evidence, not staleness."""
    stored = {"analysis-reused-1": {**_analysis_payload(), "source_id": "material-some-other-file"}}

    def load(analysis_id: str, session_id: str, *, enforce_owner: bool = False) -> dict[str, Any]:
        payload = stored[analysis_id]
        assert payload["session_id"] == session_id
        return payload

    monkeypatch.setattr(service, "load_material_analysis", load)
    captured: dict[str, Any] = {}
    with pytest.raises(ValueError, match="does not belong to this material"):
        await _generate_with_client_analysis(
            monkeypatch,
            {"analysis_id": "analysis-reused-1", "session_id": "session-reused-1"},
            captured=captured,
        )


# ---------------------------------------------------------------------------
# Media generation: a video_explanation component must actually start the
# image and video tasks (previously the tasks were never created, so every
# media component silently degraded to skipped).
# ---------------------------------------------------------------------------


async def _generate_component_with_media(
    monkeypatch: pytest.MonkeyPatch,
    component_type: str,
    *,
    media_calls: dict[str, int],
) -> service.GenerationResult:
    class _Assembler:
        def __init__(self) -> None:
            self.personalization_context = _personalization()
            self.tutor_persona_context = None

        def assemble(self, **_kwargs: Any) -> LearningContextSnapshot:
            return _snapshot()

    monkeypatch.setattr(service, "ContextAssembler", _Assembler)

    async def orchestrated(**kwargs: Any) -> dict[str, Any]:
        return {
            "kind": "courseware",
            "title": "Limits",
            "sections": [],
            "markdown": "",
            "save_target": "notebook",
            "trace": [],
        }

    async def visual(prompt_source: Any, *, generation_id: str) -> dict[str, Any]:
        media_calls["visual"] += 1
        return {
            "status": "completed",
            "asset": {
                "url": "/media/limits.png",
                "alt": "Limits diagram",
                "component_id": str(prompt_source.get("component_id") or ""),
            },
        }

    async def video(prompt_source: Any, *, generation_id: str) -> dict[str, Any]:
        media_calls["video"] += 1
        return {
            "status": "completed",
            "asset": {
                "url": "/media/limits.mp4",
                "alt": "Limits animation",
                "component_id": str(prompt_source.get("component_id") or ""),
            },
        }

    monkeypatch.setattr(service, "_generate_courseware_with_orchestrator", orchestrated)
    monkeypatch.setattr(service, "generate_learning_visual", visual)
    monkeypatch.setattr(service, "generate_learning_video", video)
    monkeypatch.setattr(
        service,
        "should_generate_learning_visual",
        lambda **_kwargs: {
            "should_generate": True,
            "reason": "test",
            "visual_targets": [{"concept_id": "limits", "label": "Limits"}],
            "support_reasons": [],
        },
    )
    request = GenerationRequest(
        "courseware",
        MaterialSource("paste", "Limits are foundational.", "Limits.pdf", metadata={}),
        options={
            "learning_component": {
                "component_id": "cmp-media",
                "component_type": component_type,
                "concept_refs": ["limits"],
            }
        },
    )
    return await service.generate_traittutor_content_async(request)


@pytest.mark.asyncio
async def test_video_component_starts_image_and_video_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """video_explanation must run both media tasks instead of skipping them."""
    media_calls: dict[str, int] = {"visual": 0, "video": 0}
    result = await _generate_component_with_media(
        monkeypatch, "video_explanation", media_calls=media_calls
    )

    assert media_calls == {"visual": 1, "video": 1}
    payload = result.result
    assert payload["image_generation"]["status"] == "completed"
    assert payload["video_generation"]["status"] == "completed"
    assert any(video.get("url") == "/media/limits.mp4" for video in payload.get("videos") or [])


@pytest.mark.asyncio
async def test_visual_map_component_starts_image_but_not_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """visual_map generates the illustration and skips video generation."""
    media_calls: dict[str, int] = {"visual": 0, "video": 0}
    result = await _generate_component_with_media(
        monkeypatch, "visual_map", media_calls=media_calls
    )

    assert media_calls == {"visual": 1, "video": 0}
    payload = result.result
    assert payload["image_generation"]["status"] == "completed"
    assert payload["video_generation"]["status"] == "skipped"
