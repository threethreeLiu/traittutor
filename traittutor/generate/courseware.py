"""Three-stage, source-grounded TraitTutor courseware generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .catalog import load_prompt
from .grounding import is_source_metadata_assessment
from .runner import LLMRunMetadata, run_structured_prompt

StructuredRunner = Callable[..., Awaitable[tuple[dict[str, Any], LLMRunMetadata]]]


@dataclass(frozen=True)
class CoursewareArtifact:
    lesson: dict[str, Any]
    content_analysis: dict[str, Any]
    adaptation_plan: dict[str, Any]
    trace: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _courseware_reasoning_effort(goal_map_mode: bool) -> str:
    """Pick the bounded reasoning tier for the lesson-planning stages.

    The goal map only projects milestones; it runs a low reasoning tier (per
    human decision, ``none`` proved unreliable on the demo provider), and every
    other courseware component also runs ``low`` while demoing. Raise these
    back to ``high`` before general availability.
    """
    return "low"


# Demo-mode output caps. Slower providers (e.g. MiniMax-M3 at ~50 tokens/s)
# need several minutes for a full 12k-token lesson, which exceeds the
# instruction executor budget and fails the whole component. The goal map only
# projects milestones from the lesson skeleton, so it gets a much tighter cap;
# other components get a bounded but still usable cap. Raise these back toward
# the prompt frontmatter values before general availability.
_GOAL_MAP_LESSON_TOKENS = 2_500
_GOAL_MAP_PLAN_TOKENS = 2_000
_DEMO_LESSON_TOKENS = 6_000
_DEMO_PLAN_TOKENS = 3_000


def _bounded_prompt(
    prompt: Any,
    *,
    max_output_tokens: int,
    brevity: str,
) -> Any:
    """Return a copy of ``prompt`` with a smaller output cap and a brevity note.

    ``PromptDefinition`` is frozen, so the copy keeps the same name/signature
    while bounding how much the provider may emit. The brevity note is appended
    to the user prompt so the model does not pad to the old expectation.
    """
    import dataclasses

    bounded = dataclasses.replace(prompt, max_output_tokens=max_output_tokens)
    bounded = dataclasses.replace(
        bounded,
        user_prompt=f"{bounded.user_prompt}\n\n{brevity}",
    )
    return bounded


def _require_keys(keys: set[str]) -> Callable[[Mapping[str, Any]], None]:
    def validate(value: Mapping[str, Any]) -> None:
        missing = keys - set(value)
        if missing:
            raise ValueError(f"missing fields: {sorted(missing)}")

    return validate


def _lesson_schema(
    value: Mapping[str, Any],
    *,
    external_chunk_ids: frozenset[str] = frozenset(),
) -> None:
    _require_keys({"title", "lesson_goal", "sections", "final_takeaways", "next_step_guidance"})(
        value
    )
    if not isinstance(value["sections"], list) or not value["sections"]:
        raise ValueError("courseware requires non-empty sections")
    for section in value["sections"]:
        if not isinstance(section, Mapping):
            raise ValueError("courseware section must be an object")
        _require_keys(
            {
                "section_title",
                "goal",
                "core_content",
                "checkpoint",
                "reflection_prompt",
                "references",
                "external_claims",
            }
        )(section)
        if not isinstance(section["checkpoint"], Mapping):
            raise ValueError("courseware checkpoint must be an object")
        _require_keys({"question", "success_criteria", "feedback_if_confused"})(
            section["checkpoint"]
        )
        checkpoint_question = section["checkpoint"]["question"]
        if not isinstance(checkpoint_question, str) or not checkpoint_question.strip():
            raise ValueError("courseware checkpoint question must be non-empty text")
        if is_source_metadata_assessment(checkpoint_question):
            raise ValueError(
                "courseware checkpoint must test subject knowledge, not source metadata"
            )
        reflection_prompt = section["reflection_prompt"]
        if not isinstance(reflection_prompt, str) or not reflection_prompt.strip():
            raise ValueError("courseware reflection prompt must be non-empty text")
        if is_source_metadata_assessment(reflection_prompt):
            raise ValueError("courseware reflection must address learning, not source metadata")
        # ``figure`` is optional presentation data, never grading content: the
        # executor re-validates it structurally and drops an invalid figure
        # instead of failing the section (a visual must not sink a lesson).
        figure = section.get("figure")
        if figure is not None and not isinstance(figure, Mapping):
            raise ValueError("courseware section figure must be an object when present")
        references = section["references"]
        if not isinstance(references, list) or any(
            not isinstance(reference, str) or not reference.strip() for reference in references
        ):
            raise ValueError("courseware section references must be non-empty chunk-id strings")
        external_claims = section["external_claims"]
        if not isinstance(external_claims, list):
            raise ValueError("courseware section external_claims must be a list")
        claimed_external_chunks: set[str] = set()
        for claim in external_claims:
            if not isinstance(claim, Mapping):
                raise ValueError("courseware external claim must be an object")
            if set(claim) != {"claim", "source_chunk_id"}:
                raise ValueError(
                    "courseware external claim requires only claim and source_chunk_id"
                )
            claim_text = claim["claim"]
            source_chunk_id = claim["source_chunk_id"]
            if not isinstance(claim_text, str) or not claim_text.strip() or len(claim_text) > 2_000:
                raise ValueError("courseware external claim text is invalid")
            if not isinstance(source_chunk_id, str) or source_chunk_id not in external_chunk_ids:
                raise ValueError("courseware external claim must cite a supplied external chunk")
            if source_chunk_id not in references:
                raise ValueError("courseware external claim source must also appear in references")
            claimed_external_chunks.add(source_chunk_id)
        referenced_external_chunks = external_chunk_ids.intersection(references)
        if referenced_external_chunks != claimed_external_chunks:
            raise ValueError(
                "every referenced external chunk requires an explicit external claim record"
            )


async def generate_courseware(
    *,
    chunks: Sequence[Mapping[str, Any]],
    learner_strategy: Mapping[str, Any],
    slr_support: Mapping[str, Any] | None = None,
    arrangement_context: Mapping[str, Any] | None = None,
    language: str = "zh-CN",
    precomputed_analysis: Mapping[str, Any] | None = None,
    goal_map_mode: bool = False,
    run: StructuredRunner = run_structured_prompt,
) -> CoursewareArtifact:
    """Generate a lesson through analysis, bounded adaptation, then rendering.

    ``precomputed_analysis`` reuses the already-paid material analysis from the
    upload pipeline (material_analysis payload) so the first LLM stage is
    skipped: the analysis node exists to ground the lesson, and the analysis is
    already grounded. The payload must already satisfy the content-analysis
    contract (topic / core_concepts / difficulty_points at minimum); when it is
    missing a required key the deterministic stage runs instead of failing.

    ``goal_map_mode`` bounds the remaining two stages for the goal-map entry
    point: medium reasoning effort and a smaller lesson output, because the
    goal map only projects milestones from the lesson structure.
    """
    # File names, paths, page locators, and upload metadata are provenance, not
    # teachable content. Keep them in server-owned records, but never expose them
    # to the model as candidate lesson or assessment facts.
    prompt_chunks = [
        {
            "source_id": str(chunk.get("source_id") or ""),
            "chunk_id": str(chunk.get("chunk_id") or ""),
            "text": str(chunk.get("text") or ""),
        }
        for chunk in chunks
    ]
    material_chunks = json.dumps(prompt_chunks, ensure_ascii=False)
    analysis_meta = LLMRunMetadata(
        model="material-analysis",
        provider="reused",
        prompt_name="traittutor-content-analysis",
        prompt_signature="precomputed",
        reasoning_effort="none",
    )
    if precomputed_analysis is not None:
        analysis = dict(precomputed_analysis)
        missing = {"topic", "core_concepts", "difficulty_points"} - set(analysis)
        if not missing:
            analysis_meta = LLMRunMetadata(
                model="material-analysis",
                provider="reused",
                prompt_name="traittutor-content-analysis",
                prompt_signature="reused-material-analysis",
                reasoning_effort="none",
            )
        else:
            # The analysis node is deterministic context, never a user-visible
            # claim. Fall back to the LLM stage rather than inventing content.
            analysis_prompt = load_prompt(
                "courseware/content-analysis.md",
                {"language": language, "material_chunks": material_chunks},
            )
            analysis, analysis_meta = await run(
                analysis_prompt,
                validate=_require_keys({"topic", "core_concepts", "difficulty_points"}),
            )
    else:
        analysis_prompt = load_prompt(
            "courseware/content-analysis.md",
            {"language": language, "material_chunks": material_chunks},
        )
        analysis, analysis_meta = await run(
            analysis_prompt,
            validate=_require_keys({"topic", "core_concepts", "difficulty_points"}),
        )
    plan_prompt = load_prompt(
        "courseware/adaptation-plan.md",
        {
            "language": language,
            "content_analysis": analysis,
            "learner_strategy": learner_strategy,
            "slr_support": dict(slr_support or {}),
            "arrangement_context": dict(arrangement_context) if arrangement_context else "",
        },
    )
    if goal_map_mode:
        plan_prompt = _bounded_prompt(
            plan_prompt,
            max_output_tokens=_GOAL_MAP_PLAN_TOKENS,
            brevity=(
                "Demo mode: this plan only feeds a goal-map milestone projection. "
                "Return a compact plan (short checkpoints, no padded prose)."
            ),
        )
    elif _DEMO_PLAN_TOKENS < (plan_prompt.max_output_tokens or _DEMO_PLAN_TOKENS):
        plan_prompt = _bounded_prompt(
            plan_prompt,
            max_output_tokens=_DEMO_PLAN_TOKENS,
            brevity=("Demo mode: keep the plan compact so generation stays responsive."),
        )
    plan, plan_meta = await run(
        plan_prompt,
        validate=_require_keys(
            {"lesson_structure", "scaffolding", "checkpoints", "visible_teaching_moves"}
        ),
        reasoning_effort=_courseware_reasoning_effort(goal_map_mode),
    )
    lesson_prompt = load_prompt(
        "courseware/traittutor-courseware.md",
        {
            "language": language,
            "material_chunks": material_chunks,
            "content_analysis": analysis,
            "adaptation_plan": plan,
            "arrangement_context": dict(arrangement_context) if arrangement_context else "",
        },
    )
    if goal_map_mode:
        # The goal map only projects milestones (titles + reasons) from the
        # lesson skeleton; a full 12k-token course would blow the instruction
        # executor budget on slow providers for no learner-facing gain.
        lesson_prompt = _bounded_prompt(
            lesson_prompt,
            max_output_tokens=_GOAL_MAP_LESSON_TOKENS,
            brevity=(
                "Demo mode, goal-map preview: emit a compact lesson skeleton only "
                "(short sections, one- or two-sentence core content, terse "
                "checkpoints). Milestones will be projected from the arrangement, "
                "so detailed prose is not needed."
            ),
        )
    elif _DEMO_LESSON_TOKENS < (lesson_prompt.max_output_tokens or _DEMO_LESSON_TOKENS):
        lesson_prompt = _bounded_prompt(
            lesson_prompt,
            max_output_tokens=_DEMO_LESSON_TOKENS,
            brevity=("Demo mode: keep sections concise so generation stays responsive."),
        )
    external_chunk_ids = frozenset(
        str(chunk.get("chunk_id") or "").strip()
        for chunk in chunks
        if str(chunk.get("source_url") or "").startswith(("http://", "https://"))
    )

    def validate_lesson(value: Mapping[str, Any]) -> None:
        _lesson_schema(value, external_chunk_ids=external_chunk_ids)

    lesson, lesson_meta = await run(
        lesson_prompt,
        validate=validate_lesson,
        reasoning_effort=_courseware_reasoning_effort(goal_map_mode),
    )
    return CoursewareArtifact(
        lesson=lesson,
        content_analysis=analysis,
        adaptation_plan=plan,
        trace=[asdict(analysis_meta), asdict(plan_meta), asdict(lesson_meta)],
    )


__all__ = ["CoursewareArtifact", "generate_courseware"]
