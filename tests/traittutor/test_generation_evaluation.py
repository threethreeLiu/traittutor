from __future__ import annotations

from copy import deepcopy
import json

from typer.testing import CliRunner

from traittutor.generate.benchmark import run_benchmark, write_benchmark_report
from traittutor.generate.evaluation import evaluate_generation
from traittutor_cli.main import app

MATERIAL = {
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


def test_courseware_reports_missing_grounding_and_personality_leak_without_mutating_output() -> (
    None
):
    output = {
        "kind": "courseware",
        "title": "水循环",
        "sections": [
            {
                "title": "核心过程",
                "content": [
                    "太阳能使地表水蒸发成水蒸气。",
                    "你的高神经质分数说明你需要更简单的内容。",
                ],
            },
            {
                "title": "自我检查",
                "content": ["按顺序写出蒸发、凝结和降水，并用一句话自查。"],
            },
        ],
        "markdown": "## 核心过程\n内容\n\n## 自我检查\n问题",
    }
    original = deepcopy(output)

    report = evaluate_generation("courseware", output, material=MATERIAL)

    assert report.scores["structure"].score == 100
    assert report.scores["grounding_and_citations"].score == 0
    assert report.scores["personality_safety"].score == 0
    assert {issue.code for issue in report.issues} >= {
        "missing_citations",
        "personality_leakage",
    }
    assert report.auto_repaired is False
    assert report.suggestions
    assert output == original


def test_flashcard_atomicity_flags_multi_fact_card_without_auto_repair() -> None:
    output = {
        "kind": "flashcards",
        "items": [
            {
                "front": "请解释蒸发、凝结和降水分别是什么，并比较它们的关系。",
                "back": "蒸发是液态水变成水蒸气。凝结是水蒸气变成液态水。降水是水滴回到地表。",
                "references": [{"text_snippet": "太阳能使地表水蒸发成水蒸气。"}],
            }
        ],
    }
    original = deepcopy(output)

    report = evaluate_generation("flashcards", output, material=MATERIAL)

    assert report.scores["flashcard_atomicity"].score < 100
    assert "flashcard_not_atomic" in {issue.code for issue in report.issues}
    assert output == original


def test_english_courseware_actions_and_personality_leakage_are_detected() -> None:
    material = {
        "chunks": [
            {
                "chunk_id": "water-en-1",
                "text": "Heat changes liquid water into water vapor. Cooling changes water vapor into liquid water.",
            }
        ]
    }
    output = {
        "kind": "courseware",
        "title": "Water cycle",
        "sections": [
            {
                "title": "Step 1",
                "content": [
                    "Review how heat changes liquid water into water vapor.",
                    "Check your explanation before the next step.",
                    "Your high neuroticism score means this section is simplified.",
                ],
                "references": [{"text_snippet": "Heat changes liquid water into water vapor."}],
            },
            {
                "title": "Step 2",
                "content": [
                    "Practice explaining how cooling changes water vapor into liquid water."
                ],
                "references": [{"text_snippet": "Cooling changes water vapor into liquid water."}],
            },
        ],
        "markdown": "## Step 1\nReview\n\n## Step 2\nPractice",
    }

    report = evaluate_generation("courseware", output, material=material)

    assert report.scores["teaching_actions"].score == 100
    assert report.scores["personality_safety"].score == 0
    assert "personality_leakage" in {issue.code for issue in report.issues}


def test_quiz_answerability_and_grounding_pass_for_a_cited_choice_question() -> None:
    output = {
        "kind": "quiz",
        "items": [
            {
                "question_id": "q1",
                "question": "水蒸气冷却后变成液态水的过程称为什么？",
                "question_type": "MULTIPLE_CHOICE",
                "difficulty": "easy",
                "options": ["蒸发", "凝结", "降水", "渗透"],
                "correct_answer": "凝结",
                "explanation": "材料说明水蒸气冷却后凝结成液态水。",
                "references": [{"text_snippet": "水蒸气冷却后凝结成液态水。"}],
            }
        ],
    }

    report = evaluate_generation("quiz", output, material=MATERIAL)

    assert report.scores["structure"].score == 100
    assert report.scores["grounding_and_citations"].score == 100
    assert report.scores["quiz_answerability"].score == 100
    assert report.verdict == "pass"


def test_benchmark_uses_an_injected_mock_generator_and_writes_a_json_report(tmp_path) -> None:
    calls: list[str] = []

    def mock_generator(fixture):
        calls.append(fixture.fixture_id)
        return fixture.output

    report = run_benchmark(generator=mock_generator)
    output_path = tmp_path / "generation-benchmark.json"
    write_benchmark_report(report, output_path)

    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert {case["generation_type"] for case in report["cases"]} == {
        "courseware",
        "flashcards",
        "quiz",
    }
    assert report["summary"]["passed"] is True
    assert calls == [case["fixture_id"] for case in report["cases"]]
    assert saved == report


def test_benchmark_reports_a_malformed_mock_output_without_repairing_it() -> None:
    def malformed_mock_generator(fixture):
        if fixture.generation_type == "quiz":
            return {"kind": "quiz", "items": []}
        return fixture.output

    report = run_benchmark(generator=malformed_mock_generator)
    quiz_case = next(case for case in report["cases"] if case["generation_type"] == "quiz")

    assert report["summary"]["passed"] is False
    assert quiz_case["passed"] is False
    assert quiz_case["evaluation"]["verdict"] == "fail"
    assert "missing_items" in {issue["code"] for issue in quiz_case["evaluation"]["issues"]}
    assert quiz_case["evaluation"]["auto_repaired"] is False


def test_benchmark_cli_emits_json_and_can_save_a_report(tmp_path) -> None:
    output_path = tmp_path / "cli-benchmark.json"
    result = CliRunner().invoke(app, ["benchmark", "--output", str(output_path)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["summary"]["passed"] is True
    assert json.loads(output_path.read_text(encoding="utf-8"))["report_version"] == "1.0"
