from __future__ import annotations

import pytest

from traittutor.learning_components import infer_material_affordances
from traittutor.personalization.service import PersonalizationService


@pytest.mark.parametrize(
    ("prompt", "subject_id"),
    [
        (
            "我想学习大学数据结构与算法，请结合代码案例和结构图安排第一课。",
            "computer-science",
        ),
        ("我想学习大学英语学术阅读，请先判断我的词汇与阅读起点。", "languages"),
        ("我想学习大学物理力学，请从受力分析、公式和计算开始。", "science"),
        (
            "I want to study university data structures and algorithms.",
            "computer-science",
        ),
        ("I want to study academic English reading at university.", "languages"),
        ("I want to study university physics mechanics.", "science"),
        ("我想学习三角函数与一元二次方程，从图像和公式开始。", "mathematics"),
        ("我想学习化学元素周期表与化学反应。", "science"),
        ("我想学习中国历史中的朝代更替。", "history"),
    ],
)
def test_quick_start_course_has_a_subject(prompt: str, subject_id: str) -> None:
    subject = PersonalizationService().classify_subject(title=prompt, text=prompt)

    assert subject is not None
    assert subject.subject_id == subject_id


def test_quick_start_courses_expose_distinct_material_affordances() -> None:
    algorithms = infer_material_affordances(
        {},
        title="数据结构与算法",
        text="结合代码案例和结构图学习。",
    )
    english = infer_material_affordances(
        {},
        title="大学英语学术阅读",
        text="训练英语词汇与阅读。",
    )
    physics = infer_material_affordances(
        {},
        title="大学物理力学",
        text="从受力分析、公式和计算开始。",
    )

    assert algorithms.visual.suitable is True
    assert algorithms.worked_example.suitable is True
    assert english.audio.suitable is True
    assert physics.worked_example.suitable is True
