"""Deterministic answer grading + coarse error classification for Mastery Path."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from traittutor.learning.models import ErrorType


def grade_answer(user_answer: str, expected_answer: str, question_type: str = "short") -> bool:
    """Grade user answer against expected answer.

    Args:
        user_answer: The user's submitted answer.
        expected_answer: The stored expected answer.
        question_type: A legacy learning type or generated Quiz ``QuestionType``.

    Returns:
        True if answer is correct.
    """
    user = user_answer.strip().lower()
    expected = expected_answer.strip().lower()

    if not expected:
        return False

    normalized_type = re.sub(r"[\s-]+", "_", question_type.strip().lower())
    if normalized_type in {"choice", "options", "delay_options", "multiple_choice"}:
        user_norm = re.sub(r"\s+", "", user)
        expected_norm = re.sub(r"\s+", "", expected)
        return user_norm == expected_norm

    if normalized_type in {"tf", "true_false", "truefalse", "boolean"}:
        if user == expected:
            return True
        truth_values = {
            "true": True,
            "t": True,
            "1": True,
            "yes": True,
            "y": True,
            "是": True,
            "对": True,
            "正确": True,
            "真": True,
            "false": False,
            "f": False,
            "0": False,
            "no": False,
            "n": False,
            "否": False,
            "错": False,
            "错误": False,
            "假": False,
        }
        return (
            user in truth_values
            and expected in truth_values
            and truth_values[user] is truth_values[expected]
        )

    if normalized_type in {"short", "short_answer", "fill_blank", "fill_in_blank"}:
        if user == expected:
            return True
        if len(expected) <= 30:
            return SequenceMatcher(None, user, expected).ratio() >= 0.85
        return False

    if normalized_type in {"open", "open_answer"}:
        keywords = [k.strip() for k in re.split(r"[,;，；。\n]+", expected) if k.strip()]
        if not keywords:
            return False
        matched = sum(1 for kw in keywords if kw in user)
        return matched / len(keywords) >= 0.6

    return False


def classify_error(user_answer: str) -> ErrorType:
    """Coarse error classification for a wrong answer.

    A blank answer signals the student did not know (metacognitive); anything
    else is treated as a wrong application. The richer four-type taxonomy is
    assigned later by the LLM in the error-diagnosis stage.
    """
    from traittutor.learning.models import ErrorType

    return ErrorType.METACOGNITIVE if not user_answer.strip() else ErrorType.APPLICATION_ERROR


__all__ = ["grade_answer", "classify_error"]
