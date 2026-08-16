"""BFI-10/TIPI scoring and bounded learner-profile summaries.

The profile is used as a personalization cue for teaching strategy only. It is
not a diagnosis, a learning-style category, or a claim about learner ability.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from traittutor.multi_user.context import get_current_user
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

TIPI_SCALE_MIN = 1
TIPI_SCALE_MAX = 5
TIPI_SCALE_NEUTRAL = 3
TIPI_TRAIT_MIN_TOTAL = TIPI_SCALE_MIN * 2
TIPI_TRAIT_MAX_TOTAL = TIPI_SCALE_MAX * 2

TRAIT_ORDER: tuple[str, ...] = ("O", "C", "E", "A", "N")
TRAIT_LABELS: dict[str, dict[str, str]] = {
    "O": {"label": "开放性", "subtitle": "Openness"},
    "C": {"label": "尽责性", "subtitle": "Conscientiousness"},
    "E": {"label": "外向性", "subtitle": "Extraversion"},
    "A": {"label": "宜人性", "subtitle": "Agreeableness"},
    "N": {"label": "神经质", "subtitle": "Neuroticism"},
}
SOURCE_TIEBREAK_ORDER: tuple[str, ...] = ("N", "C", "O", "A", "E")

TIPI_RESPONSE_OPTIONS: tuple[dict[str, Any], ...] = (
    {"value": 1, "label": "非常不同意"},
    {"value": 2, "label": "有些不同意"},
    {"value": 3, "label": "既不同意也不反对"},
    {"value": 4, "label": "有些同意"},
    {"value": 5, "label": "非常同意"},
)

TIPI_QUESTIONS: tuple[dict[str, Any], ...] = (
    {"id": 1, "text": "我认为自己是外向的、热情的。", "trait": "E", "reverse": False},
    {"id": 2, "text": "我认为自己是挑剔的、爱争论的。", "trait": "A", "reverse": True},
    {"id": 3, "text": "我认为自己是可靠的、自律的。", "trait": "C", "reverse": False},
    {"id": 4, "text": "我认为自己是焦虑的、易心烦的。", "trait": "N", "reverse": False},
    {"id": 5, "text": "我认为自己是愿意接触新事物的、思维复杂的。", "trait": "O", "reverse": False},
    {"id": 6, "text": "我认为自己是内敛的、安静的。", "trait": "E", "reverse": True},
    {"id": 7, "text": "我认为自己是有同情心的、温暖的。", "trait": "A", "reverse": False},
    {"id": 8, "text": "我认为自己是缺乏条理的、粗心的。", "trait": "C", "reverse": True},
    {"id": 9, "text": "我认为自己是冷静的、情绪稳定的。", "trait": "N", "reverse": True},
    {"id": 10, "text": "我认为自己是循规蹈矩的、缺乏创造性的。", "trait": "O", "reverse": True},
)


class IncompleteTIPIError(ValueError):
    """Raised when the learner has not answered all 10 TIPI items."""


@dataclass(frozen=True)
class TraitProfile:
    profile_id: str
    scores: dict[str, int]
    levels: dict[str, str]
    dominant_traits: list[str]
    summary: str
    answers: dict[str, int]
    created_at: str
    user_id: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_answers(answers: Mapping[Any, Any]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for question in TIPI_QUESTIONS:
        qid = str(question["id"])
        raw = answers.get(qid, answers.get(f"q{qid}", answers.get(int(qid))))
        if raw in (None, ""):
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid tipi value for q{qid}") from exc
        if value < TIPI_SCALE_MIN or value > TIPI_SCALE_MAX:
            raise ValueError(f"invalid tipi value for q{qid}")
        normalized[qid] = value
    return normalized


def validate_complete_tipi_answers(answers: Mapping[Any, Any]) -> dict[str, int]:
    normalized = _normalize_answers(answers)
    missing = [
        str(question["id"]) for question in TIPI_QUESTIONS if str(question["id"]) not in normalized
    ]
    if missing:
        raise IncompleteTIPIError(f"missing TIPI answers: {', '.join(missing)}")
    return normalized


def calculate_tipi_scores(answers: Mapping[Any, Any]) -> dict[str, int]:
    """Score BFI-10/TIPI as two 1-5 items per trait, yielding 2-10 totals."""
    normalized = validate_complete_tipi_answers(answers)
    scores = {trait: 0 for trait in TRAIT_ORDER}

    for question in TIPI_QUESTIONS:
        qid = str(question["id"])
        value = normalized[qid]
        if question["reverse"]:
            value = TIPI_SCALE_MIN + TIPI_SCALE_MAX - value
        scores[str(question["trait"])] += value

    return {trait: int(scores[trait]) for trait in TRAIT_ORDER}


def classify_trait_level(score: int) -> str:
    if score <= 4:
        return "low"
    if score == 5:
        return "low_edge"
    if score == 6:
        return "neutral"
    if score == 7:
        return "high_edge"
    return "high"


def _dominant_traits(scores: Mapping[str, int]) -> list[str]:
    ordered = sorted(
        SOURCE_TIEBREAK_ORDER,
        key=lambda trait: (scores.get(trait, 0), -SOURCE_TIEBREAK_ORDER.index(trait)),
        reverse=True,
    )
    return [trait for trait in ordered if scores.get(trait, 0) == scores.get(ordered[0], 0)]


def build_profile_summary(scores: Mapping[str, int]) -> str:
    dominant = _dominant_traits(scores)
    dominant_labels = "、".join(TRAIT_LABELS[trait]["label"] for trait in dominant)
    high = [TRAIT_LABELS[t]["label"] for t in TRAIT_ORDER if scores[t] >= 8]
    low = [TRAIT_LABELS[t]["label"] for t in TRAIT_ORDER if scores[t] <= 4]
    parts = [
        f"本次 BFI-10/TIPI 画像显示相对突出的教学线索是：{dominant_labels}。",
        "这些分数仅用于调整讲解密度、支架强度、检查点频率、语气和练习节奏。",
    ]
    if high:
        parts.append(f"较高维度可用于增加自主探索或开放练习：{'、'.join(high)}。")
    if low:
        parts.append(f"较低维度可用于增加结构化提示和阶段性确认：{'、'.join(low)}。")
    parts.append("系统不会据此判断学习能力、做心理诊断，或把学习者归入固定学习风格。")
    return "".join(parts)


def build_initial_slr_support(scores: Mapping[str, int]) -> dict[str, Any]:
    """Create visible, non-diagnostic SLR teaching supports from Big Five cues.

    This is an initial support plan, not an SLR assessment or a statement about
    what the learner can or cannot do. Learning activity can later add evidence
    to the same four dimensions.
    """
    normalized = {trait: int(scores.get(trait, 6) or 6) for trait in TRAIT_ORDER}
    low_c = normalized["C"] <= 5
    high_n = normalized["N"] >= 7
    high_o = normalized["O"] >= 7
    high_e = normalized["E"] >= 7
    low_a = normalized["A"] <= 5

    def dimension(
        label: str,
        detail: str,
        actions: list[str],
        emphasis: str = "standard",
    ) -> dict[str, Any]:
        return {
            "label": label,
            "detail": detail,
            "actions": actions,
            "emphasis": emphasis,
            "evidence_count": 0,
            "source": "initial_profile",
            "confidence": 0.5,
        }

    return {
        "version": "initial-big-five-v1",
        "source": "big_five_initial",
        "status": "initial",
        "dimensions": {
            "goal_planning": dimension(
                "目标与计划",
                "把学习任务拆成可完成的小步，并明确完成标准。",
                ["开始前列出本次目标", "用清单标记完成进度"],
                "strong" if low_c else "standard",
            ),
            "monitoring_regulation": dimension(
                "监控与调节",
                "在关键节点用低压力方式检查理解，必要时回到上一小步。",
                ["每节后进行一分钟自检", "遇到卡点时使用恢复提示"],
                "strong" if low_c or high_n else "standard",
            ),
            "reflection_transfer": dimension(
                "反思与迁移",
                "把关键概念用自己的话复述，并连接到另一个情境。",
                ["结束后写一句自己的总结", "尝试把概念应用到新例子"],
                "strong" if high_o else "standard",
            ),
            "motivation_emotion": dimension(
                "动机与情绪支持",
                "以可见进度、选择空间和非评判性反馈维持学习节奏。",
                [
                    "展示下一步而非只显示总任务",
                    "提供可选练习节奏",
                    *(["使用支持性、非评判性反馈"] if high_n or low_a else []),
                    *(["加入互动式练习提示"] if high_e else []),
                ],
                "strong" if high_n else "standard",
            ),
        },
        "boundary": (
            "这是根据本次大五画像生成的初始学习支持建议，不是 SLR 测评结果，"
            "也不用于判断学习能力、心理状态或固定学习风格。"
        ),
    }


def build_trait_profile(
    answers: Mapping[Any, Any],
    *,
    profile_id: str | None = None,
    user_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> TraitProfile:
    normalized = validate_complete_tipi_answers(answers)
    scores = calculate_tipi_scores(normalized)
    levels = {trait: classify_trait_level(score) for trait, score in scores.items()}
    profile_metadata = dict(metadata or {})
    # The action catalog is product-owned JSON; older saved profiles retain
    # their historic payload on read, while every newly created profile starts
    # from the current visible action set.
    from traittutor.assessment.support_profile import build_slr_action_support

    profile_metadata.setdefault("slr_support", build_slr_action_support(scores))
    return TraitProfile(
        profile_id=profile_id or uuid4().hex,
        scores=scores,
        levels=levels,
        dominant_traits=_dominant_traits(scores),
        summary=build_profile_summary(scores),
        answers=normalized,
        user_id=user_id,
        metadata=profile_metadata,
        created_at=datetime.now(UTC).isoformat(),
    )


def _profiles_dir(root: Path | None = None) -> Path:
    base = root or get_path_service().get_workspace_dir()
    return base / "traittutor" / "profiles"


def _profile_store(root: Path | None = None) -> SectionedRecordStore:
    return SectionedRecordStore(
        "trait_profiles",
        get_current_user().id,
        schema_version=1,
        path_service=get_path_service() if root is None else None,
        db_path=None if root is None else root / "traittutor.sqlite3",
    )


def save_trait_profile(profile: TraitProfile, *, root: Path | None = None) -> Path:
    adapter = _profile_store(root)
    owner_id = profile.user_id or get_current_user().id
    record = {**profile.to_dict(), "owner_id": owner_id}
    with adapter.locked() as payload:
        payload["profiles"] = [
            item for item in payload["profiles"] if item.get("profile_id") != profile.profile_id
        ]
        payload["profiles"].append(record)
        adapter.replace_all(payload)
    return (
        root / "traittutor.sqlite3"
        if root is not None
        else get_path_service().get_traittutor_database_path()
    )


def load_trait_profile(profile_id: str, *, root: Path | None = None) -> dict[str, Any]:
    record = next(
        (
            item
            for item in _profile_store(root).snapshot()["profiles"]
            if item.get("profile_id") == profile_id
        ),
        None,
    )
    if record is None:
        raise FileNotFoundError(profile_id)
    return {key: value for key, value in record.items() if key != "owner_id"}


def list_trait_profiles(*, root: Path | None = None) -> list[dict[str, Any]]:
    owner = get_current_user().id
    profiles = [
        {key: value for key, value in item.items() if key != "owner_id"}
        for item in _profile_store(root).snapshot()["profiles"]
        if item.get("owner_id") == owner
    ]
    return sorted(profiles, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def delete_trait_profile(profile_id: str, *, root: Path | None = None) -> bool:
    """Delete an owner-scoped profile from canonical SQLite storage."""
    adapter = _profile_store(root)
    owner = get_current_user().id
    with adapter.locked() as payload:
        retained = [
            item
            for item in payload["profiles"]
            if not (item.get("profile_id") == profile_id and item.get("owner_id") == owner)
        ]
        if len(retained) == len(payload["profiles"]):
            return False
        payload["profiles"] = retained
        adapter.replace_all(payload)
    return True
