from pathlib import Path

from traittutor.generate.service import (
    GenerationRequest,
    MaterialSource,
    generate_traittutor_content,
    load_generation,
    save_generation,
)


def _request(generation_type: str) -> GenerationRequest:
    return GenerationRequest(
        generation_type=generation_type,
        material=MaterialSource(
            source_type="paste",
            title="搜索引擎营销",
            text="搜索引擎营销需要理解关键词、出价和转化路径。学习者应先识别目标受众，再选择匹配的内容策略。",
        ),
        learner_profile={"scores": {"O": 8, "C": 4, "E": 6, "A": 6, "N": 7}},
    )


def test_generate_courseware_first_event_and_schema():
    result = generate_traittutor_content(_request("courseware"))

    assert result.events[0]["type"] == "accepted"
    assert result.result["kind"] == "courseware"
    assert result.result["sections"]
    assert result.result["save_target"] == "notebook"


def test_generate_flashcards_batch_validates():
    result = generate_traittutor_content(_request("flashcards"))

    assert result.result["kind"] == "flashcards"
    assert result.result["batch"]["valid"] is True
    assert {"front", "back", "references"} <= set(result.result["items"][0])


def test_generate_quiz_targets_question_bank():
    result = generate_traittutor_content(_request("quiz"))

    assert result.result["kind"] == "quiz"
    assert result.result["save_target"] == "question_bank"
    assert result.result["items"][0]["question_type"] == "SHORT_ANSWER"


def test_generation_persists_json(tmp_path: Path):
    result = generate_traittutor_content(_request("flashcards"))

    save_generation(result, root=tmp_path)
    loaded = load_generation(result.generation_id, root=tmp_path)

    assert loaded["generation_id"] == result.generation_id
    assert loaded["events"][0]["type"] == "accepted"
