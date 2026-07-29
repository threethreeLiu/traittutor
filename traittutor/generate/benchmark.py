"""Offline, anonymous regression benchmark for TraitTutor generation quality."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .evaluation import evaluate_generation


@dataclass(frozen=True)
class BenchmarkFixture:
    """A fixed anonymous generation case used for product quality regression."""

    fixture_id: str
    generation_type: str
    material: dict[str, Any]
    learner_profile: dict[str, Any]
    output: dict[str, Any]
    minimum_overall_score: int = 80


_ANONYMOUS_WATER_CYCLE_MATERIAL = {
    "title": "匿名水循环材料",
    "chunks": [
        {
            "chunk_id": "water-1",
            "text": "太阳能使地表水蒸发成水蒸气。水蒸气冷却后凝结成液态水。",
        },
        {
            "chunk_id": "water-2",
            "text": "云中的水滴增大后会以降水形式回到地表，水循环由此持续进行。",
        },
    ],
}

_NEUTRAL_PROFILE = {
    "scores": {"O": 6, "C": 6, "E": 6, "A": 6, "N": 6},
    "strategy": {
        "information_density": "moderate",
        "scaffold_strength": "standard",
        "checkpoint_frequency": "medium",
        "practice_pace": "mixed",
    },
}

_TYPICAL_PROFILE = {
    "scores": {"O": 6, "C": 4, "E": 6, "A": 6, "N": 8},
    "strategy": {
        "information_density": "moderate",
        "scaffold_strength": "strong",
        "checkpoint_frequency": "high",
        "practice_pace": "stepwise",
    },
}


BENCHMARK_FIXTURES: tuple[BenchmarkFixture, ...] = (
    BenchmarkFixture(
        fixture_id="courseware-typical-profile",
        generation_type="courseware",
        material=_ANONYMOUS_WATER_CYCLE_MATERIAL,
        learner_profile=_TYPICAL_PROFILE,
        output={
            "kind": "courseware",
            "title": "水循环的三个过程",
            "sections": [
                {
                    "title": "先理解蒸发",
                    "content": [
                        "太阳能使地表水蒸发成水蒸气。",
                        "先用自己的话解释蒸发，再进入下一步。",
                    ],
                    "references": [{"text_snippet": "太阳能使地表水蒸发成水蒸气。"}],
                },
                {
                    "title": "再检查凝结与降水",
                    "content": [
                        "水蒸气冷却后凝结成液态水，云中的水滴增大后会形成降水。",
                        "按顺序写出三个过程，并自查是否遗漏了回到地表这一步。",
                    ],
                    "references": [
                        {"text_snippet": "水蒸气冷却后凝结成液态水。"},
                        {"text_snippet": "云中的水滴增大后会以降水形式回到地表。"},
                    ],
                },
            ],
            "markdown": "## 先理解蒸发\n- 解释蒸发\n\n## 再检查凝结与降水\n- 完成自查",
        },
    ),
    BenchmarkFixture(
        fixture_id="flashcards-neutral",
        generation_type="flashcards",
        material=_ANONYMOUS_WATER_CYCLE_MATERIAL,
        learner_profile=_NEUTRAL_PROFILE,
        output={
            "kind": "flashcards",
            "title": "水循环主动回忆卡",
            "items": [
                {
                    "front": "什么是蒸发？",
                    "back": "蒸发是地表水在太阳能作用下变成水蒸气。",
                    "references": [{"text_snippet": "太阳能使地表水蒸发成水蒸气。"}],
                },
                {
                    "front": "水蒸气冷却后会发生什么变化？",
                    "back": "水蒸气会凝结成液态水。",
                    "references": [{"text_snippet": "水蒸气冷却后凝结成液态水。"}],
                },
            ],
        },
    ),
    BenchmarkFixture(
        fixture_id="quiz-typical-profile",
        generation_type="quiz",
        material=_ANONYMOUS_WATER_CYCLE_MATERIAL,
        learner_profile=_TYPICAL_PROFILE,
        output={
            "kind": "quiz",
            "title": "水循环检查题",
            "items": [
                {
                    "question_id": "water-quiz-1",
                    "question": "水蒸气冷却后变成液态水的过程称为什么？",
                    "question_type": "MULTIPLE_CHOICE",
                    "difficulty": "easy",
                    "options": ["蒸发", "凝结", "降水", "渗透"],
                    "correct_answer": "凝结",
                    "explanation": "材料说明水蒸气冷却后凝结成液态水。",
                    "references": [{"text_snippet": "水蒸气冷却后凝结成液态水。"}],
                },
                {
                    "question_id": "water-quiz-2",
                    "question": "请写出水循环中使水回到地表的过程。",
                    "question_type": "SHORT_ANSWER",
                    "difficulty": "easy",
                    "options": [],
                    "correct_answer": "降水",
                    "explanation": "材料说明云中的水滴增大后会以降水形式回到地表。",
                    "references": [{"text_snippet": "云中的水滴增大后会以降水形式回到地表。"}],
                },
            ],
        },
    ),
)


BenchmarkGenerator = Callable[[BenchmarkFixture], Mapping[str, Any]]


def run_benchmark(generator: BenchmarkGenerator | None = None) -> dict[str, Any]:
    """Run fixed fixtures against an injected generator or their known-good output.

    The optional callable is intentionally narrow so tests and future LLM
    runners can supply a mock without this module owning provider access.
    """

    cases: list[dict[str, Any]] = []
    for fixture in BENCHMARK_FIXTURES:
        output = generator(fixture) if generator else fixture.output
        evaluation = evaluate_generation(
            fixture.generation_type,
            output,
            material=fixture.material,
            learner_profile=fixture.learner_profile,
        )
        passed = (
            evaluation.verdict == "pass"
            and evaluation.overall_score >= fixture.minimum_overall_score
        )
        cases.append(
            {
                "fixture_id": fixture.fixture_id,
                "generation_type": fixture.generation_type,
                "minimum_overall_score": fixture.minimum_overall_score,
                "passed": passed,
                "evaluation": evaluation.to_dict(),
            }
        )

    passed_count = sum(case["passed"] for case in cases)
    return {
        "report_version": "1.0",
        "benchmark": "traittutor-generation-quality",
        "generated_at": datetime.now(UTC).isoformat(),
        "runner": "injected_generator" if generator else "fixture_oracle",
        "cases": cases,
        "summary": {
            "total": len(cases),
            "passed_count": passed_count,
            "failed_count": len(cases) - passed_count,
            "passed": passed_count == len(cases),
        },
    }


def write_benchmark_report(report: Mapping[str, Any], output_path: Path) -> Path:
    """Persist a benchmark report atomically as UTF-8 JSON."""

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target
