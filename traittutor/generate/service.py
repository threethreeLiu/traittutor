"""Unified courseware, flashcard, and quiz generation service.

This module is intentionally model-agnostic. The first product version uses a
local deterministic generator so API, persistence, batching, and UI can be
verified without model credentials. A later LLM runner can replace only the
``_generate_*`` functions while keeping event and result contracts stable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any, Literal, Mapping
from uuid import uuid4

from traittutor.assessment.big_five import (
    TRAIT_LABELS,
    TRAIT_ORDER,
    build_initial_slr_support,
)
from traittutor.services.path_service import get_path_service

from .catalog import load_prompt
from .courseware import generate_courseware
from .evaluation import evaluate_generation
from .flashcards import plan_flashcard_batches, validate_flashcard_payload
from .materials import MaterialResolver
from .quiz import plan_quiz_batches, validate_quiz_payload
from .runner import GenerationConfigurationError, run_structured_prompt

GenerationType = Literal["courseware", "flashcards", "quiz"]
MaterialSourceType = Literal["knowledge", "notebook", "upload", "paste"]

SUPPORTED_GENERATION_TYPES: tuple[GenerationType, ...] = (
    "courseware",
    "flashcards",
    "quiz",
)

PROMPT_ASSETS: dict[GenerationType, str] = {
    "courseware": "prompts/courseware/sg-full-note.yml",
    "flashcards": "prompts/flashcards/km-card-note.yml",
    "quiz": "prompts/quiz/km-question-note.yml",
}


@dataclass(frozen=True)
class MaterialSource:
    source_type: MaterialSourceType
    text: str
    title: str = "Untitled material"
    source_id: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class GenerationRequest:
    generation_type: GenerationType
    material: MaterialSource
    learner_profile: dict[str, Any] | None = None
    options: dict[str, Any] | None = None


@dataclass(frozen=True)
class GenerationEvent:
    type: str
    message: str
    created_at: str
    data: dict[str, Any]


@dataclass(frozen=True)
class GenerationResult:
    generation_id: str
    generation_type: GenerationType
    status: Literal["completed", "failed"]
    events: list[dict[str, Any]]
    result: dict[str, Any]
    created_at: str
    prompt_asset: str
    material: dict[str, Any]
    learner_profile: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event(event_type: str, message: str, **data: Any) -> dict[str, Any]:
    return asdict(GenerationEvent(type=event_type, message=message, created_at=_now(), data=data))


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _sentences(text: str, limit: int = 5) -> list[str]:
    compact = _clean_text(text)
    parts = re.split(r"(?<=[。！？.!?])\s+|[\n\r]+", compact)
    sentences = [part.strip(" -#\t") for part in parts if part.strip(" -#\t")]
    if not sentences and compact:
        sentences = [compact]
    return sentences[:limit]


def _excerpt(text: str, length: int = 700) -> str:
    compact = _clean_text(text)
    return compact[:length] + ("..." if len(compact) > length else "")


def _profile_strategy(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    scores = dict((profile or {}).get("scores") or {})
    if not scores:
        scores = {trait: 6 for trait in TRAIT_ORDER}
    normalized = {trait: int(scores.get(trait, 6) or 6) for trait in TRAIT_ORDER}
    high = [trait for trait, value in normalized.items() if value >= 8]
    low = [trait for trait, value in normalized.items() if value <= 4]
    if "N" in high or "C" in low:
        persona = {"id": "teacher", "name": "Structured Tutor"}
    elif "O" in high:
        persona = {"id": "peer", "name": "Exploration Partner"}
    else:
        persona = {"id": "teacher", "name": "Learning Coach"}
    slr_support = dict((profile or {}).get("metadata") or {}).get("slr_support")
    if not slr_support:
        slr_support = build_initial_slr_support(normalized)
    return {
        "scores": normalized,
        "high_traits": high,
        "low_traits": low,
        "teaching_adjustments": {
            "information_density": "higher" if "O" in high and "N" not in high else "moderate",
            "scaffold_strength": "strong" if "C" in low or "N" in high else "standard",
            "checkpoint_frequency": "high" if "N" in high or "C" in low else "medium",
            "tone": "warm and structured" if "A" in low or "N" in high else "direct and exploratory",
            "practice_pace": "stepwise" if "C" in low else "mixed",
        },
        "persona": persona,
        "slr_support": slr_support,
        "boundary": (
            "Personality cues adjust teaching strategy only; they do not diagnose, "
            "predict learning ability, or assign a fixed learning style."
        ),
    }


def _generate_courseware(material: MaterialSource, strategy: Mapping[str, Any]) -> dict[str, Any]:
    points = _sentences(material.text, limit=4)
    sections = [
        {
            "title": "学习目标",
            "content": [
                f"理解 {material.title} 的核心概念。",
                "能够用自己的话解释关键关系，并完成一个小练习。",
            ],
        },
        {
            "title": "核心讲解",
            "content": points or [_excerpt(material.text, 300) or "暂无可用材料。"],
        },
        {
            "title": "个性化支架",
            "content": [
                f"信息密度：{strategy['teaching_adjustments']['information_density']}",
                f"检查点频率：{strategy['teaching_adjustments']['checkpoint_frequency']}",
                f"练习节奏：{strategy['teaching_adjustments']['practice_pace']}",
            ],
        },
        {
            "title": "练习检查",
            "content": ["用 3 句话总结材料重点。", "列出一个仍不确定的问题并继续 Chat。"],
        },
    ]
    markdown = "\n\n".join(
        [f"## {section['title']}\n" + "\n".join(f"- {item}" for item in section["content"]) for section in sections]
    )
    return {
        "kind": "courseware",
        "title": f"{material.title} - TraitTutor 课件",
        "sections": sections,
        "markdown": markdown,
        "save_target": "notebook",
    }


def _reference(sentence: str, index: int) -> list[dict[str, Any]]:
    return [{"page_number": index, "text_snippet": sentence[:180]}]


def _generate_flashcards(material: MaterialSource, strategy: Mapping[str, Any]) -> dict[str, Any]:
    sentences = _sentences(material.text, limit=6)
    if not sentences:
        sentences = ["请先提供可用于生成卡片的学习材料。"]
    items = []
    for index, sentence in enumerate(sentences[:5], start=1):
        front = sentence[:18].rstrip("，。,. ") or f"概念 {index}"
        items.append(
            {
                "node_id": f"material.{index}",
                "node_name": material.title[:16],
                "front": front,
                "back": sentence[:80],
                "references": _reference(sentence, index),
            }
        )
    return {
        "kind": "flashcards",
        "title": f"{material.title} - Flashcards",
        "items": items,
        "batch": {"index": 1, "valid": True, "count": len(items)},
        "save_target": "notebook",
        "strategy": strategy["teaching_adjustments"],
    }


def _generate_quiz(material: MaterialSource, strategy: Mapping[str, Any]) -> dict[str, Any]:
    sentences = _sentences(material.text, limit=5)
    if not sentences:
        sentences = ["请先提供可用于生成测验题的学习材料。"]
    items = []
    for index, sentence in enumerate(sentences[:4], start=1):
        stem = sentence[:60].rstrip("，。,. ")
        items.append(
            {
                "node_id": f"material.{index}",
                "node_name": material.title[:16],
                "question_id": index,
                "question": f"[Difficulty: easy] [Type: SHORT_ANSWER] {stem} 的关键含义是什么？",
                "question_type": "SHORT_ANSWER",
                "difficulty": "easy",
                "options": [],
                "correct_answer": sentence[:80],
                "explanation": f"答案应紧扣材料：{sentence[:120]}",
                "references": _reference(sentence, index),
            }
        )
    return {
        "kind": "quiz",
        "title": f"{material.title} - Quiz",
        "items": items,
        "batch": {"index": 1, "valid": True, "count": len(items)},
        "save_target": "question_bank",
        "strategy": strategy["teaching_adjustments"],
    }


def _validate_result(generation_type: GenerationType, result: Mapping[str, Any]) -> None:
    if generation_type == "courseware":
        if not result.get("sections") or not result.get("markdown"):
            raise ValueError("courseware result requires sections and markdown")
        return
    items = result.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"{generation_type} result requires non-empty items")
    required = {
        "flashcards": {"front", "back", "node_id", "node_name", "references"},
        "quiz": {
            "question_id",
            "question",
            "question_type",
            "difficulty",
            "explanation",
            "references",
        },
    }[generation_type]
    for index, item in enumerate(items, start=1):
        missing = required - set(item)
        if missing:
            raise ValueError(f"{generation_type} batch item {index} missing {sorted(missing)}")


def generate_traittutor_content(request: GenerationRequest) -> GenerationResult:
    if request.generation_type not in SUPPORTED_GENERATION_TYPES:
        raise ValueError(f"unsupported generation type: {request.generation_type}")
    text = request.material.text or ""
    generation_id = uuid4().hex
    events = [
        _event("accepted", "Generation request accepted", generation_id=generation_id),
        _event(
            "material_parsed",
            "Material parsed",
            source_type=request.material.source_type,
            chars=len(text),
            title=request.material.title,
        ),
    ]
    strategy = _profile_strategy(request.learner_profile)
    events.append(
        _event(
            "profile_applied",
            "Learner profile strategy applied",
            strategy=strategy["teaching_adjustments"],
            boundary=strategy["boundary"],
        )
    )

    if request.generation_type == "courseware":
        result = _generate_courseware(request.material, strategy)
    elif request.generation_type == "flashcards":
        result = _generate_flashcards(request.material, strategy)
    else:
        result = _generate_quiz(request.material, strategy)

    _validate_result(request.generation_type, result)
    events.append(
        _event(
            "batch_validated",
            "Structured output batch validated",
            generation_type=request.generation_type,
            item_count=len(result.get("items", result.get("sections", []))),
        )
    )
    events.append(_event("completed", "Generation completed", generation_id=generation_id))

    return GenerationResult(
        generation_id=generation_id,
        generation_type=request.generation_type,
        status="completed",
        events=events,
        result=result,
        created_at=_now(),
        prompt_asset=PROMPT_ASSETS[request.generation_type],
        material={
            "source_type": request.material.source_type,
            "title": request.material.title,
            "source_id": request.material.source_id,
            "metadata": request.material.metadata or {},
            "excerpt": _excerpt(text),
        },
        learner_profile={
            "scores": strategy["scores"],
            "high_traits": [
                {"key": trait, **TRAIT_LABELS[trait]} for trait in strategy["high_traits"]
            ],
            "low_traits": [
                {"key": trait, **TRAIT_LABELS[trait]} for trait in strategy["low_traits"]
            ],
            "strategy": strategy["teaching_adjustments"],
            "persona": strategy["persona"],
            "boundary": strategy["boundary"],
        },
    )


def _generations_dir(root: Path | None = None) -> Path:
    base = root or get_path_service().get_workspace_dir()
    return base / "traittutor" / "generations"


def save_generation(result: GenerationResult, *, root: Path | None = None) -> Path:
    directory = _generations_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{result.generation_id}.json"
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def load_generation(generation_id: str, *, root: Path | None = None) -> dict[str, Any]:
    path = _generations_dir(root) / f"{generation_id}.json"
    if not path.exists():
        raise FileNotFoundError(generation_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_generations(*, root: Path | None = None) -> list[dict[str, Any]]:
    directory = _generations_dir(root)
    if not directory.exists():
        return []
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(directory.glob("*.json"), reverse=True)
        if path.is_file()
    ]


async def generate_traittutor_content_async(
    request: GenerationRequest,
    *,
    resolver: MaterialResolver | None = None,
) -> GenerationResult:
    """Prefer the configured LLM; retain an explicitly labeled local fallback."""
    resolver = resolver or MaterialResolver()
    resolved = resolver.resolve(request.material)
    strategy = _profile_strategy(request.learner_profile)
    chunks = [chunk.to_dict() for chunk in resolved.chunks]
    generation_id = uuid4().hex
    events = [
        _event("accepted", "Generation request accepted", generation_id=generation_id),
        _event("material_resolved", "Material resolved", source_type=resolved.source_type, chunks=len(chunks)),
        _event("profile_strategy_ready", "Learner teaching strategy is ready", strategy=strategy["teaching_adjustments"]),
        _event("generation_started", "Generating structured learning content", generation_type=request.generation_type),
    ]
    try:
        if request.generation_type == "courseware":
            artifact = await generate_courseware(
                chunks=chunks,
                learner_strategy=strategy["teaching_adjustments"],
                language=str((request.options or {}).get("language") or "zh-CN"),
            )
            lesson = artifact.lesson
            result = {
                "kind": "courseware", "title": lesson["title"], "sections": lesson["sections"],
                "markdown": "\n\n".join(f"## {item['section_title']}\n{item['core_content']}" for item in lesson["sections"]),
                "save_target": "notebook", "trace": artifact.trace,
            }
            prompt_asset = "prompts/courseware/traittutor-courseware.yml"
        else:
            is_flashcards = request.generation_type == "flashcards"
            plans = plan_flashcard_batches(chunks) if is_flashcards else plan_quiz_batches(chunks)
            items: list[dict[str, Any]] = []
            prompt_path = "flashcards/km-card-note.yml" if is_flashcards else "quiz/km-question-note.yml"
            for plan in plans:
                batch_chunks = [item.model_dump() for item in plan.source_chunks]
                prompt = load_prompt(prompt_path, {
                    "language": str((request.options or {}).get("language") or "zh"),
                    "material_title": resolved.title,
                    "learner_strategy_json": strategy["teaching_adjustments"],
                    "batch_plan_json": plan.__dict__,
                    "generation_options_json": dict(request.options or {}),
                    "material_chunks_json": batch_chunks,
                })
                if is_flashcards:
                    validator = lambda value, source=batch_chunks: validate_flashcard_payload(value, source)
                else:
                    validator = lambda value, source=batch_chunks: validate_quiz_payload(value, source)
                payload, _metadata = await run_structured_prompt(prompt, validate=validator)
                items.extend(payload["items"])
                events.append(_event("batch_validated", "Structured output batch validated", batch_id=str(plan.batch_index), items=payload["items"]))
            result = {
                "kind": request.generation_type,
                "title": f"{resolved.title} - {'Flashcards' if is_flashcards else 'Quiz'}",
                "items": items, "save_target": "notebook" if is_flashcards else "question_bank",
                "batch": {"valid": True, "count": len(items)}, "strategy": strategy["teaching_adjustments"],
                "generation_options": dict(request.options or {}),
            }
            prompt_asset = f"prompts/{prompt_path}"
        execution_mode = "llm"
    except GenerationConfigurationError:
        # Compatibility path for a first-run workspace. It is deliberately visible
        # in persisted metadata and evaluation rather than presented as an LLM result.
        fallback = generate_traittutor_content(request)
        result = fallback.result
        prompt_asset = fallback.prompt_asset
        execution_mode = "local_fallback"

    evaluation = evaluate_generation(
        request.generation_type,
        result,
        material=chunks,
        strategy=strategy["teaching_adjustments"],
    )
    result["evaluation"] = evaluation.to_dict()
    events.append(_event("evaluation_completed", "Generation evaluation completed", evaluation=evaluation.to_dict()))
    events.append(_event("completed", "Generation completed", generation_id=generation_id, execution_mode=execution_mode))
    return GenerationResult(
        generation_id=generation_id, generation_type=request.generation_type, status="completed", events=events,
        result=result, created_at=_now(), prompt_asset=prompt_asset,
        material={**resolved.to_dict(), "excerpt": _excerpt(" ".join(chunk.text for chunk in resolved.chunks))},
        learner_profile={"scores": strategy["scores"], "strategy": strategy["teaching_adjustments"], "persona": strategy["persona"], "boundary": strategy["boundary"]},
    )
