"""Product-facing quality checks for TraitTutor generation artifacts.

The evaluator deliberately reports evidence and revision guidance without
rewriting generated content. It is a deterministic first-pass guardrail for
courseware, flashcards, and quizzes; it does not make claims about learner
ability or diagnose personality.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Literal

GenerationType = Literal["courseware", "flashcards", "quiz"]
IssueSeverity = Literal["warning", "error"]
SUPPORTED_GENERATION_TYPES: frozenset[str] = frozenset({"courseware", "flashcards", "quiz"})


@dataclass(frozen=True)
class EvaluationIssue:
    """A concrete quality concern found without changing the artifact."""

    code: str
    dimension: str
    severity: IssueSeverity
    message: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationScore:
    """A normalized 0-100 score with short, inspectable evidence."""

    score: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class GenerationEvaluation:
    """A serialized report suitable for persistence alongside a generation."""

    evaluation_version: str
    generation_type: str
    overall_score: int
    verdict: Literal["pass", "revise", "fail"]
    scores: dict[str, EvaluationScore]
    issues: tuple[EvaluationIssue, ...]
    suggestions: tuple[str, ...]
    auto_repaired: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_version": self.evaluation_version,
            "generation_type": self.generation_type,
            "overall_score": self.overall_score,
            "verdict": self.verdict,
            "scores": {
                name: {"score": section.score, "evidence": list(section.evidence)}
                for name, section in self.scores.items()
            },
            "issues": [
                {
                    "code": issue.code,
                    "dimension": issue.dimension,
                    "severity": issue.severity,
                    "message": issue.message,
                    "evidence": list(issue.evidence),
                }
                for issue in self.issues
            ],
            "suggestions": list(self.suggestions),
            "auto_repaired": self.auto_repaired,
        }


_LEARNER_PERSONALITY_PATTERNS = (
    re.compile(
        r"(?:你的|您(?:的)?|学习者(?:的)?)\s*(?:高|低)?(?:大五|人格|人格特质|"
        r"开放性|尽责性|外向性|宜人性|神经质|OCEAN|TIPI|BFI)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:你的|您(?:的)?|学习者(?:的)?).{0,24}(?:分数|得分|画像|测评)", re.IGNORECASE),
    re.compile(
        r"(?:基于|根据).{0,16}(?:你的|您(?:的)?|学习者(?:的)?).{0,16}(?:人格|大五|画像)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|[^A-Za-z])(?:O|C|E|A|N)\s*(?:分数|得分|score|:|：|=)\s*\d", re.IGNORECASE),
    re.compile(
        r"\b(?:your|the learner(?:'s)?|student(?:'s)?)\s+(?:high|low)?\s*"
        r"(?:big five|personality|openness|conscientiousness|extraversion|"
        r"agreeableness|neuroticism|ocean|tipi|bfi)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:based on|according to)\s+(?:your|the learner(?:'s)?|student(?:'s)?)"
        r"\s+(?:personality|big five|profile)\b",
        re.IGNORECASE,
    ),
)

_TEACHING_ACTION_MARKERS = (
    "步骤",
    "按顺序",
    "下一步",
    "检查",
    "自查",
    "练习",
    "反思",
    "总结",
    "回顾",
    "提示",
    "尝试",
    "任务",
    "step",
    "next",
    "check",
    "practice",
    "reflect",
    "summarize",
    "review",
    "prompt",
    "try",
    "task",
)

_QUESTION_MARKERS = (
    "?",
    "？",
    "什么",
    "如何",
    "为什么",
    "请",
    "判断",
    "选择",
    "what",
    "how",
    "why",
    "please",
    "explain",
    "choose",
    "write",
)
_CHOICE_TYPES = frozenset({"MULTIPLE_CHOICE", "SINGLE_CHOICE", "CHOICE", "MCQ"})

_SUGGESTIONS_BY_CODE = {
    "invalid_output": "重新生成符合该类型结构的 JSON 结果后再保存。",
    "unsupported_generation_type": "仅对 courseware、flashcards 和 quiz 运行此评估。",
    "missing_sections": "为课件提供至少两个有标题且有正文的学习段落。",
    "missing_items": "为卡片或测验提供至少一个完整条目。",
    "invalid_courseware_section": "补齐每个课件段落的标题和可读正文。",
    "duplicate_section_titles": "使用不重复的段落标题，让学习路径可扫描。",
    "missing_markdown": "同时保存课件的结构化 sections 和渲染 Markdown。",
    "invalid_flashcard": "补齐卡片的 front 和 back，并保持一个卡片只考察一个知识点。",
    "invalid_quiz_item": "补齐题干、题型、答案和解析，让题目可以独立作答。",
    "missing_material_text": "提供可解析的材料 chunk，才能验证内容依据。",
    "missing_citations": "为每个学习段落、卡片或题目附上可回溯的材料引用。",
    "incomplete_citation_coverage": "把引用下沉到每个生成单元，而不是只放在结果顶层。",
    "unverified_citation": "让引用中的 text_snippet 与已选材料 chunk 保持可核验的重合。",
    "strategy_not_visible": "把支架、检查点或节奏安排写进学习者可见的任务与反馈中。",
    "missing_teaching_actions": "加入明确的分步提示、检查点、练习或反思动作。",
    "personality_leakage": "移除对学习者人格标签、分数或画像的直接提及，只保留教学动作。",
    "flashcard_not_atomic": "将复合卡片拆成一次只要求回忆一个概念或关系的卡片。",
    "invalid_quiz_options": "为选择题提供至少两个互不重复的选项，并让正确答案可匹配。",
    "quiz_answer_revealed": "不要在题干中直接给出正确答案。",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, Mapping):
        return " ".join(_clean_text(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return " ".join(_clean_text(item) for item in value)
    return str(value).strip()


def _normalize_for_match(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", _clean_text(value).lower())


def _tokens(value: Any) -> set[str]:
    normalized = _normalize_for_match(value)
    latin = re.findall(r"[a-z0-9]+", normalized)
    cjk_blocks = re.findall(r"[\u4e00-\u9fff]+", normalized)
    cjk_bigrams = {
        block[index : index + 2] for block in cjk_blocks for index in range(max(len(block) - 1, 1))
    }
    return set(latin) | cjk_bigrams


def _as_mappings(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _add_issue(
    issues: list[EvaluationIssue],
    code: str,
    dimension: str,
    severity: IssueSeverity,
    message: str,
    *evidence: str,
) -> None:
    if any(issue.code == code and issue.dimension == dimension for issue in issues):
        return
    issues.append(
        EvaluationIssue(
            code=code,
            dimension=dimension,
            severity=severity,
            message=message,
            evidence=tuple(item for item in evidence if item),
        )
    )


def _generation_units(generation_type: str, output: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if generation_type == "courseware":
        return _as_mappings(output.get("sections"))
    return _as_mappings(output.get("items"))


def _section_content(section: Mapping[str, Any]) -> str:
    return _clean_text(section.get("content", section.get("core_content", "")))


def _visible_text(generation_type: str, output: Mapping[str, Any]) -> str:
    parts = [_clean_text(output.get("title"))]
    if generation_type == "courseware":
        for section in _generation_units(generation_type, output):
            parts.extend((_clean_text(section.get("title")), _section_content(section)))
        parts.append(_clean_text(output.get("markdown")))
    elif generation_type == "flashcards":
        for item in _generation_units(generation_type, output):
            parts.extend((_clean_text(item.get("front")), _clean_text(item.get("back"))))
    elif generation_type == "quiz":
        for item in _generation_units(generation_type, output):
            parts.extend(
                (
                    _clean_text(item.get("question")),
                    _clean_text(item.get("options")),
                    _clean_text(item.get("explanation")),
                )
            )
    return "\n".join(part for part in parts if part)


def _evaluate_structure(
    generation_type: str, output: Mapping[str, Any], issues: list[EvaluationIssue]
) -> EvaluationScore:
    if generation_type not in SUPPORTED_GENERATION_TYPES:
        _add_issue(
            issues,
            "unsupported_generation_type",
            "structure",
            "error",
            "不支持的生成类型。",
        )
        return EvaluationScore(0)

    units = _generation_units(generation_type, output)
    if not units:
        _add_issue(
            issues,
            "missing_sections" if generation_type == "courseware" else "missing_items",
            "structure",
            "error",
            "生成结果缺少可评估的学习单元。",
        )
        return EvaluationScore(0)

    score = 100
    if generation_type == "courseware":
        titles: list[str] = []
        for index, section in enumerate(units, start=1):
            title = _clean_text(section.get("title"))
            content = _section_content(section)
            if not title or not content:
                score -= 25
                _add_issue(
                    issues,
                    "invalid_courseware_section",
                    "structure",
                    "error",
                    "课件段落需要同时包含标题和正文。",
                    f"section {index}",
                )
            if title:
                titles.append(_normalize_for_match(title))
        if len(units) < 2:
            score -= 20
            _add_issue(
                issues,
                "missing_sections",
                "structure",
                "warning",
                "课件段落不足，难以形成明确学习路径。",
            )
        if len(titles) != len(set(titles)):
            score -= 20
            _add_issue(
                issues,
                "duplicate_section_titles",
                "structure",
                "warning",
                "课件存在重复段落标题。",
            )
        if not _clean_text(output.get("markdown")):
            score -= 15
            _add_issue(
                issues,
                "missing_markdown",
                "structure",
                "warning",
                "课件缺少可渲染的 Markdown。",
            )
    elif generation_type == "flashcards":
        for index, item in enumerate(units, start=1):
            if not _clean_text(item.get("front")) or not _clean_text(item.get("back")):
                score -= 30
                _add_issue(
                    issues,
                    "invalid_flashcard",
                    "structure",
                    "error",
                    "卡片需要同时包含 front 和 back。",
                    f"item {index}",
                )
    else:
        required = ("question", "question_type", "correct_answer", "explanation")
        for index, item in enumerate(units, start=1):
            missing = [field for field in required if not _clean_text(item.get(field))]
            if missing:
                score -= 25
                _add_issue(
                    issues,
                    "invalid_quiz_item",
                    "structure",
                    "error",
                    "测验题需要题干、题型、答案和解析。",
                    f"item {index}: {', '.join(missing)}",
                )
    if output.get("kind") and _clean_text(output.get("kind")) != generation_type:
        score -= 15
        _add_issue(
            issues,
            "invalid_output",
            "structure",
            "error",
            "结果 kind 与请求的生成类型不一致。",
        )
    return EvaluationScore(max(score, 0), (f"已检查 {len(units)} 个学习单元。",))


def _material_texts(material: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None) -> list[str]:
    if material is None:
        return []
    candidates: list[Mapping[str, Any]] = []
    if isinstance(material, Mapping):
        chunks = material.get("chunks", material.get("material_chunks"))
        candidates = _as_mappings(chunks) if chunks is not None else [material]
    else:
        candidates = _as_mappings(material)
    texts = []
    for candidate in candidates:
        text = _clean_text(
            candidate.get("text")
            or candidate.get("content")
            or candidate.get("excerpt")
            or candidate.get("body")
            or candidate.get("text_snippet")
        )
        if text:
            texts.append(text)
    return texts


def _reference_text(reference: Any) -> str:
    if isinstance(reference, Mapping):
        return _clean_text(
            reference.get("text_snippet")
            or reference.get("quote")
            or reference.get("excerpt")
            or reference.get("text")
            or reference.get("content")
        )
    return _clean_text(reference)


def _references_for_unit(
    unit: Mapping[str, Any], output: Mapping[str, Any]
) -> tuple[list[Any], bool]:
    references = unit.get("references")
    if references is not None:
        return list(references) if isinstance(references, Sequence) and not isinstance(
            references, str
        ) else [references], True
    references = output.get("references")
    if references is not None:
        return list(references) if isinstance(references, Sequence) and not isinstance(
            references, str
        ) else [references], False
    return [], False


def _reference_matches_material(reference: Any, material_texts: Sequence[str]) -> bool:
    snippet = _normalize_for_match(_reference_text(reference))
    if len(snippet) < 4:
        return False
    reference_tokens = _tokens(snippet)
    for material_text in material_texts:
        candidate = _normalize_for_match(material_text)
        if snippet in candidate:
            return True
        material_tokens = _tokens(candidate)
        if reference_tokens and material_tokens:
            overlap = len(reference_tokens & material_tokens) / len(reference_tokens)
            if overlap >= 0.7:
                return True
    return False


def _evaluate_grounding(
    generation_type: str,
    output: Mapping[str, Any],
    material: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
    issues: list[EvaluationIssue],
) -> EvaluationScore:
    units = _generation_units(generation_type, output)
    material_texts = _material_texts(material)
    if not material_texts:
        _add_issue(
            issues,
            "missing_material_text",
            "grounding_and_citations",
            "error",
            "没有可核验的材料文本，无法确认内容依据。",
        )
        return EvaluationScore(0)
    if not units:
        return EvaluationScore(0)

    citation_coverage = 0.0
    verified_coverage = 0.0
    has_any_citation = False
    has_unverified = False
    for unit in units:
        references, is_unit_level = _references_for_unit(unit, output)
        if not references:
            continue
        has_any_citation = True
        unit_weight = 1.0 if is_unit_level else 0.5
        citation_coverage += unit_weight
        if any(_reference_matches_material(reference, material_texts) for reference in references):
            verified_coverage += unit_weight
        else:
            has_unverified = True

    total = len(units)
    cited_ratio = min(citation_coverage / total, 1.0)
    verified_ratio = min(verified_coverage / total, 1.0)
    if not has_any_citation:
        _add_issue(
            issues,
            "missing_citations",
            "grounding_and_citations",
            "error",
            "生成单元没有附带材料引用。",
        )
    elif cited_ratio < 1:
        _add_issue(
            issues,
            "incomplete_citation_coverage",
            "grounding_and_citations",
            "warning",
            "部分生成单元没有自己的材料引用，或只使用了结果顶层引用。",
        )
    if has_unverified or (has_any_citation and verified_ratio < cited_ratio):
        _add_issue(
            issues,
            "unverified_citation",
            "grounding_and_citations",
            "warning",
            "至少一个引用片段无法与选定材料 chunk 核验。",
        )

    score = round(100 * (0.3 * cited_ratio + 0.7 * verified_ratio))
    return EvaluationScore(
        score,
        (
            f"{round(cited_ratio * 100)}% 的单元具有引用，"
            f"{round(verified_ratio * 100)}% 的单元具有可核验引用。",
        ),
    )


def _strategy_context(
    learner_profile: Mapping[str, Any] | None, strategy: Mapping[str, Any] | None
) -> bool:
    if strategy:
        return True
    if not learner_profile:
        return False
    return bool(learner_profile.get("strategy") or learner_profile.get("teaching_adjustments"))


def _evaluate_teaching_actions(
    generation_type: str,
    output: Mapping[str, Any],
    learner_profile: Mapping[str, Any] | None,
    strategy: Mapping[str, Any] | None,
    issues: list[EvaluationIssue],
) -> EvaluationScore:
    units = _generation_units(generation_type, output)
    visible_text = _visible_text(generation_type, output)
    normalized = visible_text.lower()
    if not units:
        return EvaluationScore(0)

    if generation_type == "courseware":
        action_hits = [marker for marker in _TEACHING_ACTION_MARKERS if marker in normalized]
        checkpoint_present = any(
            marker in normalized
            for marker in ("检查", "自查", "练习", "反思", "问题", "check", "practice", "reflect")
        )
        if len(action_hits) >= 3 and checkpoint_present:
            score = 100
        elif action_hits:
            score = 60
        else:
            score = 0
    elif generation_type == "flashcards":
        prompt_rate = sum(
            any(marker in _clean_text(item.get("front")).lower() for marker in _QUESTION_MARKERS)
            for item in units
        ) / len(units)
        score = round(40 + 60 * prompt_rate)
    else:
        explained_rate = sum(
            bool(_clean_text(item.get("explanation")))
            and any(
                marker in _clean_text(item.get("question")).lower() for marker in _QUESTION_MARKERS
            )
            for item in units
        ) / len(units)
        score = round(40 + 60 * explained_rate)

    if score == 0:
        _add_issue(
            issues,
            "missing_teaching_actions",
            "teaching_actions",
            "warning",
            "学习者可见内容没有明确的支架、检查点或练习动作。",
        )
    elif _strategy_context(learner_profile, strategy) and score < 100:
        _add_issue(
            issues,
            "strategy_not_visible",
            "teaching_actions",
            "warning",
            "存在教学策略上下文，但可见学习动作还不够明确。",
        )
    return EvaluationScore(score, ("检查了学习者可见的支架、检查或练习动作。",))


def _evaluate_personality_safety(
    generation_type: str, output: Mapping[str, Any], issues: list[EvaluationIssue]
) -> EvaluationScore:
    visible_text = _visible_text(generation_type, output)
    matches = []
    for pattern in _LEARNER_PERSONALITY_PATTERNS:
        match = pattern.search(visible_text)
        if match:
            matches.append(match.group(0))
    if matches:
        _add_issue(
            issues,
            "personality_leakage",
            "personality_safety",
            "error",
            "学习者可见内容直接暴露了人格标签、分数或画像。",
            *matches[:3],
        )
        return EvaluationScore(0, tuple(matches[:3]))
    return EvaluationScore(100, ("未发现学习者人格标签、分数或画像泄露。",))


def _sentence_count(value: str) -> int:
    parts = [part for part in re.split(r"[。！？!?；;]+", value) if part.strip()]
    return max(len(parts), 1) if value else 0


def _evaluate_flashcard_atomicity(
    output: Mapping[str, Any], issues: list[EvaluationIssue]
) -> EvaluationScore:
    items = _generation_units("flashcards", output)
    if not items:
        return EvaluationScore(0)
    atomic_count = 0
    for index, item in enumerate(items, start=1):
        front = _clean_text(item.get("front"))
        back = _clean_text(item.get("back"))
        compound_prompt = (
            front.count("、") >= 2
            or sum(token in front for token in ("以及", "并比较", "分别")) >= 2
        )
        too_broad = len(front) > 100 or len(back) > 280 or _sentence_count(back) > 2
        if front and back and not compound_prompt and not too_broad:
            atomic_count += 1
        else:
            _add_issue(
                issues,
                "flashcard_not_atomic",
                "flashcard_atomicity",
                "warning",
                "卡片一次考察了多个事实，或答案范围过宽。",
                f"item {index}",
            )
    score = round(100 * atomic_count / len(items))
    return EvaluationScore(score, (f"{atomic_count}/{len(items)} 张卡片满足原子化检查。",))


def _answer_matches_options(answer: str, options: Sequence[Any]) -> bool:
    normalized_answer = _normalize_for_match(answer)
    if not normalized_answer:
        return False
    for index, option in enumerate(options, start=1):
        normalized_option = _normalize_for_match(option)
        if normalized_answer == normalized_option or normalized_answer in normalized_option:
            return True
        if normalized_answer in {str(index), chr(64 + index).lower()}:
            return True
    return False


def _evaluate_quiz_answerability(
    output: Mapping[str, Any], issues: list[EvaluationIssue]
) -> EvaluationScore:
    items = _generation_units("quiz", output)
    if not items:
        return EvaluationScore(0)
    answerable_count = 0
    for index, item in enumerate(items, start=1):
        question = _clean_text(item.get("question"))
        answer = _clean_text(item.get("correct_answer"))
        explanation = _clean_text(item.get("explanation"))
        question_type = _clean_text(item.get("question_type")).upper()
        options = item.get("options", [])
        options = (
            list(options) if isinstance(options, Sequence) and not isinstance(options, str) else []
        )
        valid = bool(question and answer and explanation and question_type)
        if question_type in _CHOICE_TYPES:
            normalized_options = {
                _normalize_for_match(option) for option in options if _clean_text(option)
            }
            if len(normalized_options) < 2 or not _answer_matches_options(answer, options):
                valid = False
                _add_issue(
                    issues,
                    "invalid_quiz_options",
                    "quiz_answerability",
                    "error",
                    "选择题的选项或正确答案不可匹配。",
                    f"item {index}",
                )
        if answer and _normalize_for_match(answer) in _normalize_for_match(question):
            valid = False
            _add_issue(
                issues,
                "quiz_answer_revealed",
                "quiz_answerability",
                "warning",
                "题干直接包含了正确答案。",
                f"item {index}",
            )
        if valid:
            answerable_count += 1
    score = round(100 * answerable_count / len(items))
    return EvaluationScore(score, (f"{answerable_count}/{len(items)} 道题可独立作答。",))


def _suggestions(issues: Sequence[EvaluationIssue]) -> tuple[str, ...]:
    suggestions: list[str] = []
    for issue in issues:
        suggestion = _SUGGESTIONS_BY_CODE.get(issue.code)
        if suggestion and suggestion not in suggestions:
            suggestions.append(suggestion)
    return tuple(suggestions)


def _verdict(
    scores: Mapping[str, EvaluationScore], issues: Sequence[EvaluationIssue]
) -> tuple[int, Literal["pass", "revise", "fail"]]:
    overall_score = round(sum(section.score for section in scores.values()) / max(len(scores), 1))
    structural_score = scores.get("structure", EvaluationScore(0)).score
    grounding_score = scores.get("grounding_and_citations", EvaluationScore(0)).score
    personality_score = scores.get("personality_safety", EvaluationScore(0)).score
    if structural_score < 50 or grounding_score < 50 or personality_score < 100:
        return overall_score, "fail"
    if overall_score >= 80 and not any(issue.severity == "error" for issue in issues):
        return overall_score, "pass"
    return overall_score, "revise"


def evaluate_generation(
    generation_type: GenerationType | str,
    output: Mapping[str, Any] | None,
    *,
    material: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    learner_profile: Mapping[str, Any] | None = None,
    strategy: Mapping[str, Any] | None = None,
) -> GenerationEvaluation:
    """Evaluate a structured generation artifact without modifying it.

    ``material`` accepts either a material record with ``chunks`` or a sequence
    of chunks. Each chunk only needs a text-like field such as ``text`` or
    ``content``. The evaluator is intentionally heuristic: it verifies citation
    traceability, not semantic truth beyond the supplied material.
    """

    normalized_type = _clean_text(generation_type).lower()
    safe_output = output if isinstance(output, Mapping) else {}
    issues: list[EvaluationIssue] = []
    if not isinstance(output, Mapping):
        _add_issue(
            issues,
            "invalid_output",
            "structure",
            "error",
            "生成结果必须是结构化对象。",
        )

    scores: dict[str, EvaluationScore] = {
        "structure": _evaluate_structure(normalized_type, safe_output, issues),
        "grounding_and_citations": _evaluate_grounding(
            normalized_type, safe_output, material, issues
        ),
        "teaching_actions": _evaluate_teaching_actions(
            normalized_type, safe_output, learner_profile, strategy, issues
        ),
        "personality_safety": _evaluate_personality_safety(normalized_type, safe_output, issues),
    }
    if normalized_type == "flashcards":
        scores["flashcard_atomicity"] = _evaluate_flashcard_atomicity(safe_output, issues)
    elif normalized_type == "quiz":
        scores["quiz_answerability"] = _evaluate_quiz_answerability(safe_output, issues)
    elif normalized_type == "courseware":
        sections = _generation_units(normalized_type, safe_output)
        nonempty_sections = sum(bool(_section_content(section)) for section in sections)
        score = round(100 * nonempty_sections / len(sections)) if sections else 0
        scores["courseware_structure"] = EvaluationScore(
            score, (f"{nonempty_sections}/{len(sections)} 个段落具有正文。",) if sections else ()
        )

    overall_score, verdict = _verdict(scores, issues)
    return GenerationEvaluation(
        evaluation_version="1.0",
        generation_type=normalized_type,
        overall_score=overall_score,
        verdict=verdict,
        scores=scores,
        issues=tuple(issues),
        suggestions=_suggestions(issues),
    )
