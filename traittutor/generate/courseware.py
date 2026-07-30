"""Three-stage, source-grounded TraitTutor courseware generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Any, Awaitable, Callable, Mapping

from .catalog import load_prompt
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


def _require_keys(keys: set[str]) -> Callable[[Mapping[str, Any]], None]:
    def validate(value: Mapping[str, Any]) -> None:
        missing = keys - set(value)
        if missing:
            raise ValueError(f"missing fields: {sorted(missing)}")
    return validate


def _lesson_schema(value: Mapping[str, Any]) -> None:
    _require_keys({"title", "lesson_goal", "sections", "final_takeaways", "next_step_guidance"})(value)
    if not isinstance(value["sections"], list) or not value["sections"]:
        raise ValueError("courseware requires non-empty sections")
    for section in value["sections"]:
        if not isinstance(section, Mapping):
            raise ValueError("courseware section must be an object")
        _require_keys({"section_title", "goal", "core_content", "checkpoint", "reflection_prompt", "references"})(section)
        if not isinstance(section["checkpoint"], Mapping):
            raise ValueError("courseware checkpoint must be an object")
        _require_keys({"question", "success_criteria", "feedback_if_confused"})(section["checkpoint"])


async def generate_courseware(
    *,
    chunks: list[Mapping[str, Any]],
    learner_strategy: Mapping[str, Any],
    slr_support: Mapping[str, Any] | None = None,
    language: str = "zh-CN",
    run: StructuredRunner = run_structured_prompt,
) -> CoursewareArtifact:
    """Generate a lesson through analysis, bounded adaptation, then rendering."""
    material_chunks = json.dumps(chunks, ensure_ascii=False)
    analysis_prompt = load_prompt("courseware/content-analysis.md", {"language": language, "material_chunks": material_chunks})
    analysis, analysis_meta = await run(analysis_prompt, validate=_require_keys({"topic", "core_concepts", "difficulty_points"}))
    plan_prompt = load_prompt(
        "courseware/adaptation-plan.md",
        {"language": language, "content_analysis": analysis, "learner_strategy": learner_strategy, "slr_support": dict(slr_support or {})},
    )
    plan, plan_meta = await run(plan_prompt, validate=_require_keys({"lesson_structure", "scaffolding", "checkpoints", "visible_teaching_moves"}))
    lesson_prompt = load_prompt(
        "courseware/traittutor-courseware.md",
        {"language": language, "material_chunks": material_chunks, "content_analysis": analysis, "adaptation_plan": plan},
    )
    lesson, lesson_meta = await run(lesson_prompt, validate=_lesson_schema)
    return CoursewareArtifact(
        lesson=lesson,
        content_analysis=analysis,
        adaptation_plan=plan,
        trace=[asdict(analysis_meta), asdict(plan_meta), asdict(lesson_meta)],
    )


__all__ = ["CoursewareArtifact", "generate_courseware"]
