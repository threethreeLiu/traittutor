"""Research evidence enters generation only as a minimal frozen reference."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from traittutor.context_assembler import ContextAssembler
from traittutor.orchestration import CoursewarePromptBundle, content_hash
from traittutor.research_workspace.provenance import (
    ResearchCoursewareProvenance,
    ResearchCoursewareSourceRef,
)

CREATED_AT = "2026-08-10T00:00:00+00:00"


def _provenance(*, revision: int = 1, source_revision: int = 1) -> ResearchCoursewareProvenance:
    return ResearchCoursewareProvenance(
        workspace_id="workspace-owner-a",
        research_run_id="run-owner-a",
        report_id="report-owner-a",
        report_revision=revision,
        report_body_hash="a" * 64,
        source_refs=(
            ResearchCoursewareSourceRef(source_id="source-owner-a", revision=source_revision),
        ),
    )


def _snapshot(provenance: ResearchCoursewareProvenance):
    return ContextAssembler().assemble(
        intent="learn",
        user_id="owner-a",
        token_budget=100,
        created_at=CREATED_AT,
        trace_id="trace-owner-a",
        research_provenance=provenance,
    )


def _bundle(provenance: ResearchCoursewareProvenance) -> CoursewarePromptBundle:
    snapshot = _snapshot(provenance)
    return CoursewarePromptBundle(
        prompt_bundle_id="bundle-owner-a",
        version="v1",
        context_snapshot_id=snapshot.snapshot_id,
        context_snapshot_hash=snapshot.content_hash(),
        material_language="en",
        requested_component_types=("concept_explanation",),
        teaching_goal="Teach evidence safely",
        created_at=CREATED_AT,
        research_provenance=provenance,
    )


def test_research_provenance_changes_snapshot_and_bundle_replay_hashes() -> None:
    first = _provenance()
    changed = _provenance(revision=2, source_revision=2)

    first_snapshot = _snapshot(first)
    changed_snapshot = _snapshot(changed)
    first_bundle = _bundle(first)
    changed_bundle = _bundle(changed)

    assert first_snapshot.read_ranges.research_run_id == first.research_run_id
    assert first_snapshot.read_ranges.research_provenance == first
    assert first_snapshot.content_hash() != changed_snapshot.content_hash()
    assert content_hash(first_bundle) != content_hash(changed_bundle)


def test_snapshot_ref_has_no_report_or_source_browser_content() -> None:
    reference = _snapshot(_provenance()).read_ranges.research_provenance
    assert reference is not None
    serialized = reference.model_dump(mode="json")
    assert "body" not in serialized
    assert "claim" not in serialized
    assert "url" not in serialized
    assert "title" not in serialized


def test_assembler_rejects_mismatched_research_run_reference() -> None:
    with pytest.raises(ValueError, match="must match"):
        ContextAssembler().assemble(
            intent="learn",
            user_id="owner-a",
            token_budget=100,
            research_run_id="different-run",
            research_provenance=_provenance(),
        )


def test_prompt_bundle_rejects_untyped_or_extra_provenance_content() -> None:
    with pytest.raises(ValidationError):
        # Pydantic's frozen contract rejects a browser-shaped URL/title packet;
        # orchestration can receive only the server-built minimal model.
        ResearchCoursewareProvenance.model_validate(
            {
                **_provenance().model_dump(mode="json"),
                "source_refs": [
                    {"source_id": "source-owner-a", "revision": 1, "url": "https://bad.test"}
                ],
            }
        )


@pytest.mark.asyncio
async def test_generation_composition_puts_the_same_ref_in_the_real_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE", "deterministic")
    """Exercise service composition, not merely the standalone Pydantic model."""
    from traittutor.generate import service
    from traittutor.orchestration import CoursewareOrchestrator

    provenance = _provenance()
    snapshot = _snapshot(provenance)
    captured: dict[str, CoursewarePromptBundle] = {}

    class _StopAfterBundle(RuntimeError):
        pass

    def stop_after_bundle(self: object, bundle: CoursewarePromptBundle) -> object:
        del self
        captured["bundle"] = bundle
        raise _StopAfterBundle()

    monkeypatch.setattr(CoursewareOrchestrator, "plan", stop_after_bundle)
    with pytest.raises(_StopAfterBundle):
        await service._generate_courseware_with_orchestrator(
            generation_id="generation-owner-a",
            title="Evidence lesson",
            chunks=[{"chunk_id": "chunk-1", "source_id": "run-owner-a", "text": "Evidence"}],
            learner_strategy={},
            slr_support={},
            language="en",
            learning_targets={},
            visual_seed={},
            context_snapshot=snapshot,
            research_provenance=provenance,
        )

    bundle = captured["bundle"]
    assert bundle.research_provenance == provenance
    assert bundle.context_snapshot_id == snapshot.snapshot_id
    assert bundle.context_snapshot_hash == snapshot.content_hash()
