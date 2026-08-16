"""T1 acceptance: ``requested_component_types`` drives adaptive selection.

The bundle's ``requested_component_types`` (the adaptive selector's output)
must (a) narrow each task's producible component types to the requested subset,
(b) prune branches whose types are all excluded, (c) fall back to the static
``_COMPONENT_TYPES`` map when the requested set is empty or matches no contract
type — page generation never blocks (invariant #8) — and (d) change the
bundle's content hash so differing mastery yields differing run-cache keys
(invariant #4).
"""

from __future__ import annotations

import asyncio

from traittutor.components import get_default_registry
from traittutor.orchestration import (
    AgentTask,
    CoursewareOrchestrator,
    CoursewarePromptBundle,
    VisualExecutor,
    content_hash,
)

CREATED_AT = "2026-08-09T08:00:00+00:00"


def _bundle(requested: tuple[str, ...]) -> CoursewarePromptBundle:
    return CoursewarePromptBundle(
        prompt_bundle_id="bundle-adaptive",
        version="v1",
        context_snapshot_id="snapshot-1",
        context_snapshot_hash="snapshot-hash-1",
        material_language="zh-CN",
        requested_component_types=requested,
        teaching_goal="Explain a concept and adapt the activity mix.",
        created_at=CREATED_AT,
    )


def test_plan_intersects_requested_component_types() -> None:
    bundle = _bundle(("concept_explanation", "diagnostic_check"))
    graph = CoursewareOrchestrator().plan(bundle)

    assert graph.tasks["instruction"].produces_component_types == ("concept_explanation",)
    assert graph.tasks["practice"].produces_component_types == ("diagnostic_check",)
    # srl and visual produce nothing in the requested subset -> pruned from the DAG.
    assert "srl" not in graph.tasks
    assert "visual" not in graph.tasks


def test_plan_falls_back_when_requested_unusable_or_empty() -> None:
    # A requested set matching no contract type, or an empty set (the production
    # default when the selector is absent/raised), both collapse to the
    # unrestricted static map so generation never blocks.
    bad = CoursewareOrchestrator().plan(_bundle(("nonexistent_type",)))
    empty = CoursewareOrchestrator().plan(_bundle(()))

    full = bad.tasks["instruction"].produces_component_types
    assert full == empty.tasks["instruction"].produces_component_types
    assert "concept_explanation" in full

    # A genuine single-type restriction is strictly narrower than the fallback.
    narrow = CoursewareOrchestrator().plan(_bundle(("concept_explanation",)))
    assert narrow.tasks["instruction"].produces_component_types == ("concept_explanation",)
    assert set(narrow.tasks["instruction"].produces_component_types) < set(full)


def test_content_hash_reflects_requested_component_types() -> None:
    # Invariant #4: differing adaptive selection => differing bundle hash so the
    # run cache cannot replay one learner's page for another.
    wide = _bundle(("concept_explanation", "guided_practice", "visual_map"))
    narrow = wide.model_copy(update={"requested_component_types": ("concept_explanation",)})
    assert content_hash(wide) != content_hash(narrow)


def test_visual_executor_skips_when_contract_excludes_visual_map() -> None:
    # Mirrors the CoursewareExecutor gate: when the adaptive plan restricts
    # visual_map out of the visual task's contract, the executor degrades
    # WITHOUT invoking the (billable) image-generation body.
    invoked: list[int] = []

    async def body(*_args: object, **_kwargs: object) -> dict[str, object]:
        invoked.append(1)
        return {}

    def payload_provider(_task: object, _bundle: object) -> dict[str, object]:
        return {}

    executor = VisualExecutor(payload_provider=payload_provider, body=body)  # type: ignore[arg-type]
    task = AgentTask(
        task_id="visual",
        task_type="visual",
        agent="Visual Agent",
        depends_on=("instruction",),
        input_refs=(),
        produces_component_types=(),  # visual_map restricted out
        budget_ms=1,
        timeout_ms=1,
    )
    result = asyncio.run(executor(task, _bundle(("concept_explanation",)), get_default_registry()))
    assert result.status == "degraded"
    assert result.produced_component_instances == ()
    assert invoked == []  # image body never called
