from pathlib import Path

import pytest

from traittutor.assessment.big_five import (
    IncompleteTIPIError,
    TIPI_QUESTIONS,
    build_trait_profile,
    calculate_tipi_scores,
    load_trait_profile,
    save_trait_profile,
    build_initial_slr_support,
)


def test_tipi_questions_match_traittutor_source_order():
    assert [question["trait"] for question in TIPI_QUESTIONS] == [
        "E",
        "A",
        "C",
        "N",
        "O",
        "E",
        "A",
        "C",
        "N",
        "O",
    ]
    assert [question["reverse"] for question in TIPI_QUESTIONS] == [
        False,
        True,
        False,
        False,
        False,
        True,
        False,
        True,
        True,
        True,
    ]


def test_tipi_scoring_uses_reversed_items():
    answers = {str(index): 5 for index in range(1, 11)}

    assert calculate_tipi_scores(answers) == {
        "O": 6,
        "C": 6,
        "E": 6,
        "A": 6,
        "N": 6,
    }


def test_tipi_requires_all_ten_answers():
    with pytest.raises(IncompleteTIPIError):
        calculate_tipi_scores({"1": 4, "2": 3})


def test_trait_profile_persists_json(tmp_path: Path):
    answers = {str(index): 4 for index in range(1, 11)}
    profile = build_trait_profile(answers, user_id="learner-1")

    save_trait_profile(profile, root=tmp_path)
    loaded = load_trait_profile(profile.profile_id, root=tmp_path)

    assert loaded["profile_id"] == profile.profile_id
    assert loaded["user_id"] == "learner-1"
    assert "学习能力" in loaded["summary"]
    assert loaded["metadata"]["slr_support"]["status"] == "initial"


def test_initial_slr_support_turns_trait_cues_into_actions_not_assessment():
    support = build_initial_slr_support({"O": 8, "C": 4, "E": 8, "A": 6, "N": 8})

    assert support["source"] == "big_five_initial"
    assert support["dimensions"]["goal_planning"]["emphasis"] == "strong"
    assert support["dimensions"]["monitoring_regulation"]["emphasis"] == "strong"
    assert support["dimensions"]["reflection_transfer"]["emphasis"] == "strong"
    assert "加入互动式练习提示" in support["dimensions"]["motivation_emotion"]["actions"]
    assert "不是 SLR 测评结果" in support["boundary"]


def test_explicit_slr_metadata_is_preserved_on_profile_creation():
    provided = {"status": "initial", "source": "custom"}
    profile = build_trait_profile(
        {str(index): 3 for index in range(1, 11)},
        metadata={"slr_support": provided},
    )

    assert profile.metadata is not None
    assert profile.metadata["slr_support"] == provided
