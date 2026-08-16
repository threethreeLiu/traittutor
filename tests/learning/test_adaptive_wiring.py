"""T1b regression guard: BKT/SLR mastery must drive *real* adaptation.

These tests lock the one correctness trap in wiring the adaptive selector into
the live courseware path (invariant #3). ``ConceptSignal`` carries a
``@field_serializer`` that nulls ``mastery_probability`` in a *public*
``model_dump()`` unless ``bkt_calibrated`` or the internal dump context is set.
If ``_select_adaptive_component_types`` ever reverts to reading the public
payload, every learner's posterior becomes ``None`` -> ``_stage`` falls back to
0.2 -> every learner is classified "developing" -> identical, non-adaptive
component selection. These tests fail in exactly that case.
"""

from __future__ import annotations

from traittutor.generate.service import _select_adaptive_component_types
from traittutor.orchestration import CoursewareOrchestrator, CoursewarePromptBundle
from traittutor.personalization.models import (
    ConceptSignal,
    PersonalizationContext,
    TeachingStrategyPlan,
)

CREATED_AT = "2026-08-09T08:00:00+00:00"

# A concept with calibrated strong evidence that clears the supported policy.
_SUPPORTED_CONCEPT = ConceptSignal(
    concept_id="k1",
    label="K1",
    support_level="supported",
    confidence=1.0,
    attempt_count=5,
    mastery_probability=0.85,
    observation_count=5,
    verified_observation_count=5,
    bkt_calibrated=True,
)


def _context(signals: list[ConceptSignal]) -> PersonalizationContext:
    return PersonalizationContext(
        purpose="courseware",
        plan=TeachingStrategyPlan(),
        trace_id="trace-adaptive",
        relevant_concept_signals=signals,
    )


def _select(signals: list[ConceptSignal]) -> tuple[str, ...]:
    return _select_adaptive_component_types(
        personalization=_context(signals),
        analysis=None,
        title="Derivatives",
        chunks=[{"text": "the limit definition of the derivative"}],
        options={"instruction": "explain derivatives"},
        strategy={},
        analysis_id="",
        generation_id="gen-adaptive",
    )


def test_supported_learner_keeps_real_posterior() -> None:
    """A calibrated supported learner receives the *supported* mix.

    ``transfer_challenge`` is emitted only at the supported stage (cleanest
    positive marker); ``guided_practice`` appears at developing/unobserved and
    is absent at supported (negative marker). Reading the nulled public payload
    collapses the posterior to 0.2 -> "developing", flipping both assertions.
    """
    supported = _select([_SUPPORTED_CONCEPT])
    assert "transfer_challenge" in supported  # supported-stage marker
    # Would reappear if the posterior were nulled -> "developing":
    assert "guided_practice" not in supported


def test_component_selection_varies_with_mastery() -> None:
    """An unobserved learner and a mastered learner get different components."""
    unobserved = _select([])
    supported = _select([_SUPPORTED_CONCEPT])
    assert unobserved != supported
    # Missing evidence offers one optional judgement, but it is not a gate:
    # teaching and practice components are available in the same plan.
    assert "concept_explanation" in unobserved
    assert "guided_practice" in unobserved
    assert "diagnostic_check" not in supported
    assert "diagnostic_check" in unobserved


def _bundle(requested: tuple[str, ...]) -> CoursewarePromptBundle:
    return CoursewarePromptBundle(
        prompt_bundle_id="bundle-adaptive-wiring",
        version="v1",
        context_snapshot_id="snapshot-1",
        context_snapshot_hash="snapshot-hash-1",
        material_language="zh-CN",
        requested_component_types=requested,
        teaching_goal="Adapt the activity mix to the learner's mastery.",
        created_at=CREATED_AT,
    )


def test_adaptive_selection_reaches_orchestrator_dag() -> None:
    """The selector's output flows through the bundle into differing task contracts.

    Guards the full B->A bridge: selector plan -> ``requested_component_types``
    -> ``CoursewareOrchestrator.plan`` -> ``produces_component_types``. The two
    mastery states yield a different practice task contract, so two learners
    never share one cached page (invariant #4).
    """
    unobserved_types = _select([])
    supported_types = _select([_SUPPORTED_CONCEPT])

    practice_unobserved = (
        CoursewareOrchestrator()
        .plan(_bundle(unobserved_types))
        .tasks["practice"]
        .produces_component_types
    )
    practice_supported = (
        CoursewareOrchestrator()
        .plan(_bundle(supported_types))
        .tasks["practice"]
        .produces_component_types
    )
    assert practice_unobserved != practice_supported
    # Unobserved learners receive both a non-blocking judgement and direct
    # practice; mastered practice asks for transfer.
    assert "guided_practice" in practice_unobserved
    assert "diagnostic_check" in practice_unobserved
    assert "transfer_challenge" in practice_supported
