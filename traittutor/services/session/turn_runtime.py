"""
Turn-level runtime manager for unified chat streaming.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
import contextlib
from contextvars import Token
from dataclasses import dataclass, field
import json
import logging
from typing import TYPE_CHECKING, Any, Literal

from traittutor.agents._shared.tool_composition import AUTO_MOUNTED_TOOLS
from traittutor.core.stream import StreamEvent, StreamEventType
from traittutor.personalization.knowledge_diagram_accumulator import (
    accumulate_knowledge_diagram_message,
)
from traittutor.services.llm.utils import clean_thinking_tags
from traittutor.services.session.protocol import SessionStoreProtocol

if TYPE_CHECKING:
    from traittutor.context_assembler.snapshot import AssistantContextSnapshot, AssistantIntent
    from traittutor.services.llm.config import LLMConfig

logger = logging.getLogger(__name__)

MemoryReference = Literal["recent", "profile", "scope", "preferences", "summary"]
_AUTO_ARTIFACT_BUILTINS = sorted(AUTO_MOUNTED_TOOLS - {"ask_user"})
_AUTO_ARTIFACT_MODES = frozenset({"knowledge_diagram", "learning_exploration", "solve"})
_CHAT_MODE_CAPABILITIES = {
    "solve": "deep_solve",
    "learning_exploration": "learning_exploration",
    "knowledge_diagram": "knowledge_diagram",
    "humanizer": "humanizer",
}
_CHAT_LOOP_CAPABILITIES = frozenset({"", "chat", *_CHAT_MODE_CAPABILITIES.values(), "mastery_path"})
_AUTO_ARTIFACT_FENCE_MARKERS = (
    "traittutor-knowledge-graph",
    "traittutor-learning-exploration",
    "traittutor-guided-solve",
)


# Content call_kinds that make up the persisted answer. The chat agent loop
# streams every round's text as ``content`` with ``agent_loop_round``; the
# finish round (and forced-finish) are the answer, narration rounds are
# filtered back out via their ``call_role`` marker (see _narration_marker_call_id).
_ANSWER_CONTENT_CALL_KINDS = frozenset({"llm_final_response", "agent_loop_round"})


def _should_capture_assistant_content(event: StreamEvent) -> bool:
    if event.type != StreamEventType.CONTENT:
        return False
    metadata = event.metadata or {}
    call_id = metadata.get("call_id")
    if not call_id:
        return True
    return metadata.get("call_kind") in _ANSWER_CONTENT_CALL_KINDS


def _narration_marker_call_id(event: StreamEvent) -> str | None:
    """call_id of a chat-loop round that resolved as narration (a short
    preamble streamed alongside a tool call). Its text belongs to the trace,
    not the persisted answer, so it is excluded when assembling content."""
    metadata = event.metadata or {}
    if (
        metadata.get("trace_kind") == "call_status"
        and metadata.get("call_state") == "complete"
        and metadata.get("call_role") == "narration"
    ):
        call_id = metadata.get("call_id")
        return str(call_id) if call_id else None
    return None


_QUIZ_PUBLIC_FIELDS = frozenset(
    {
        "question_id",
        "question",
        "question_type",
        "options",
        "difficulty",
        "concentration",
    }
)


def _learner_safe_quiz_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Project one private quiz item to the browser-safe transport shape."""
    question_id = str(raw.get("question_id") or "").strip()
    question = str(raw.get("question") or "").strip()
    if not question_id or not question:
        return None
    safe = {key: raw[key] for key in _QUIZ_PUBLIC_FIELDS if key in raw}
    safe["question_id"] = question_id
    safe["question"] = question
    options = raw.get("options")
    safe["options"] = options if isinstance(options, dict) else {}
    return safe


def _private_quiz_items_from_event(event: StreamEvent) -> list[dict[str, Any]]:
    """Read server-generated items before their public event is projected."""
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    items: list[dict[str, Any]] = []
    qa_pair = metadata.get("qa_pair")
    if isinstance(qa_pair, dict):
        items.append(qa_pair)
    summary = metadata.get("summary")
    if isinstance(summary, dict):
        for result in summary.get("results") or []:
            if not isinstance(result, dict):
                continue
            qa_pair = result.get("qa_pair", result)
            if isinstance(qa_pair, dict):
                items.append(qa_pair)
    return items


def _artifact_attachments(event: StreamEvent) -> list[dict[str, Any]]:
    """Generated-file attachments carried by a stream event.

    The ``exec`` / ``code_execution`` tools surface files written to the turn
    workspace ({filename, url, mime_type, size_bytes}) in two places: each
    ``tool_result`` event carries them in ``metadata.tool_metadata.artifacts``
    the moment the tool finishes (the source that survives cancelled turns),
    and the loop's final SOURCES event aggregates them as ``type=="artifact"``
    sources. Both are read — the caller dedupes by URL. Persisting them as
    assistant-message attachments lets the chat UI render openable cards —
    same Viewer path as user uploads — instead of relying on the model
    pasting a raw ``/api/outputs`` URL.
    """
    metadata = event.metadata or {}
    raw: list[Any] = []
    if event.type == StreamEventType.SOURCES:
        raw = [
            entry
            for entry in metadata.get("sources") or []
            if isinstance(entry, dict) and entry.get("type") == "artifact"
        ]
    elif event.type == StreamEventType.TOOL_RESULT:
        tool_meta = metadata.get("tool_metadata")
        if isinstance(tool_meta, dict):
            raw = [e for e in tool_meta.get("artifacts") or [] if isinstance(e, dict)]
    attachments: list[dict[str, Any]] = []
    for entry in raw:
        url = str(entry.get("url") or "")
        if not url:
            continue
        mime = str(entry.get("mime_type") or "")
        attachments.append(
            {
                "type": "image" if mime.startswith("image/") else "document",
                "filename": str(entry.get("filename") or "file"),
                "mime_type": mime,
                "url": url,
                "size_bytes": entry.get("size_bytes"),
                "generated": True,
            }
        )
    return attachments


def _build_learning_support_materials(
    *,
    source_index: dict[str, str],
    learning_support: bool,
    learning_canvas_excerpt: str,
    learning_pack_id: str | None,
    learning_plan_id: str | None,
) -> list[str]:
    """Assemble the read-only source materials for a learning-support turn.

    Replaces the inline material assembly that lived in the retired
    ``agent_runtime`` branch.  Only learner-visible content is included:
    correct answers, rubrics and inactive hints stay server-held and are
    never serialized into this Q&A source.  Returns ``[]`` for non
    learning-support turns (the chat capability uses attachment text then).
    """

    if not learning_support:
        return []
    materials = [text for text in source_index.values() if isinstance(text, str) and text.strip()]
    if learning_canvas_excerpt:
        materials.insert(0, "# Current visible learning content\n" + learning_canvas_excerpt)
    if learning_pack_id:
        from traittutor import learning_packs

        support_pack = learning_packs.get_pack(learning_pack_id)
        support_plan = (
            learning_packs.get_component_plan(learning_pack_id, learning_plan_id or "")
            if support_pack is not None and learning_plan_id
            else None
        )
        if support_pack is not None:
            goal_value = support_pack.get("goal")
            goal_text = (
                str(goal_value.get("text") or "")
                if isinstance(goal_value, dict)
                else str(goal_value or "")
            )
            plan_components = (
                support_plan.get("components", []) if isinstance(support_plan, dict) else []
            )
            visible_steps = [
                str(item.get("label_zh") or item.get("label_en") or "")
                for item in plan_components
                if isinstance(item, dict)
                and str(item.get("label_zh") or item.get("label_en") or "").strip()
            ]
            materials.append(
                "# Current Learning Canvas\n"
                f"Goal: {goal_text or support_pack.get('title') or ''}\n"
                f"Plan steps: {'; '.join(visible_steps[:20])}"
            )
    return materials


def _assistant_snapshot_intent(
    *, capability: str | None, learning_support: bool
) -> AssistantIntent:
    """Map an explicit capability selection to the bounded snapshot taxonomy."""

    if learning_support:
        return "learn"
    selected = str(capability or "").strip()
    if selected == "search":
        return "search"
    if selected == "research":
        return "research"
    if selected in {"courseware", "flashcards", "quiz", "writing"}:
        return "create"
    return "chat"


def _assemble_assistant_context_snapshot(
    *,
    capability: str | None,
    learning_support: bool,
    user_id: str,
    session_id: str,
    subject_id: str | None,
    learning_plan_id: str | None,
    include_tutor_persona: bool,
) -> AssistantContextSnapshot:
    """Freeze the owner-authorized context reference before capability execution."""

    from traittutor.context_assembler import ContextAssembler

    intent = _assistant_snapshot_intent(
        capability=capability,
        learning_support=learning_support,
    )
    snapshot = ContextAssembler().assemble(
        intent=intent,
        user_id=user_id,
        subject_id=subject_id,
        thread_id=session_id,
        token_budget=8_000,
        user_authorized=True,
        include_personalization=learning_support,
        include_tutor_persona=include_tutor_persona,
        component_plan_ref=learning_plan_id if learning_support else None,
        surface_type="learning_canvas" if learning_support else None,
    )
    return snapshot


async def _accumulate_knowledge_diagram_from_answer(
    *,
    session_id: str,
    message_id: int | str | None,
    content: str,
) -> None:
    lowered = (content or "").lower()
    if not any(marker in lowered for marker in _AUTO_ARTIFACT_FENCE_MARKERS):
        return
    try:
        merged = await asyncio.to_thread(
            accumulate_knowledge_diagram_message,
            content,
            session_id=session_id,
            message_id=message_id,
        )
        if merged:
            logger.info(
                "Accumulated %s inline knowledge diagram(s) for session %s",
                merged,
                session_id,
            )
    except Exception:
        logger.debug("Failed to accumulate inline learning artifact", exc_info=True)


def _clip_text(value: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


_TITLE_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("`", "`"),
)
_TITLE_PREFIXES: tuple[str, ...] = (
    "Title:",
    "title:",
    "TITLE:",
    "Title-",
    "标题：",
    "标题:",
    "对话标题：",
    "对话标题:",
)
_TITLE_TRAILING_PUNCT = ".。!！?？,，;；、 \t"
_INTERRUPTED_TURN_ERROR = "Turn interrupted by server restart. Please retry your message."


def _sanitize_session_title(raw: str) -> str:
    """Trim the noise LLMs love to add to short titles.

    Strips model reasoning tags, surrounding quotes, leading "Title:" labels,
    trailing punctuation, and Markdown bold/italic markers. Caps length at
    80 characters so a chatty model can't blow past the sidebar layout.
    """
    text = clean_thinking_tags(raw or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip()
    # Iterate until the text stops shrinking — models often nest the
    # noise (e.g. ``**Title:** "Hello"``) so a single pass leaves
    # leftover wrappers.
    for _ in range(8):
        prev = text
        text = text.lstrip("*_#- \t").rstrip("*_ \t")
        for prefix in _TITLE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        for opener, closer in _TITLE_QUOTE_PAIRS:
            if len(text) >= 2 and text.startswith(opener) and text.endswith(closer):
                text = text[len(opener) : len(text) - len(closer)].strip()
                break
        text = text.rstrip(_TITLE_TRAILING_PUNCT)
        if text == prev:
            break
    return text[:80]


def _extract_memory_references(payload: dict[str, Any]) -> list[MemoryReference]:
    """Return the bounded canonical-memory categories selected for this turn."""
    refs = payload.get("memory_references", []) or []
    if not isinstance(refs, list):
        return []
    allowed = {"recent", "profile", "scope", "preferences", "summary"}
    out: list[MemoryReference] = []
    for item in refs:
        if item in allowed and item not in out:
            out.append(item)
    return out


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _llm_selection_dict(value: Any) -> dict[str, str] | None:
    from traittutor.services.model_selection import LLMSelection

    selection = LLMSelection.from_payload(value)
    return selection.to_dict() if selection else None


def _request_snapshot_metadata(
    *,
    payload: dict[str, Any],
    content: str,
    capability: str,
    config: dict[str, Any],
    attachments: list[dict[str, Any]],
    notebook_references: list[Any],
    history_references: list[Any],
    question_notebook_references: list[Any],
    learning_artifact_references: list[Any],
    persona: str,
    memory_references: Sequence[str],
    llm_selection: dict[str, str] | None,
) -> dict[str, Any]:
    """Persist the front-end context chips with the user message."""
    snapshot: dict[str, Any] = {
        "content": content,
        "capability": capability,
        "enabledTools": _string_list(payload.get("tools")),
        "knowledgeBases": _string_list(payload.get("knowledge_bases")),
        "language": str(payload.get("language", "en") or "en"),
    }
    if attachments:
        snapshot["attachments"] = attachments
    if config:
        snapshot["config"] = dict(config)
    if notebook_references:
        snapshot["notebookReferences"] = notebook_references
    if history_references:
        snapshot["historyReferences"] = history_references
    if question_notebook_references:
        snapshot["questionNotebookReferences"] = question_notebook_references
    if learning_artifact_references:
        snapshot["learningArtifactReferences"] = learning_artifact_references
    if persona:
        snapshot["persona"] = persona
    if memory_references:
        snapshot["memoryReferences"] = memory_references
    if llm_selection:
        snapshot["llmSelection"] = llm_selection
    return {"request_snapshot": snapshot}


def _format_question_bank_entry(entry: dict[str, Any]) -> str:
    """Render a single Question Bank entry as a structured Markdown block."""
    lines: list[str] = []
    title = str(entry.get("session_title", "") or "Untitled session")
    difficulty = str(entry.get("difficulty", "") or "").strip()
    qtype = str(entry.get("question_type", "") or "").strip()
    is_correct = bool(entry.get("is_correct"))

    badges: list[str] = []
    if qtype:
        badges.append(qtype)
    if difficulty:
        badges.append(difficulty)
    badges.append("correct" if is_correct else "incorrect")
    badge_text = " · ".join(badges)

    lines.append(f"### Question (from {title}) [{badge_text}]")
    lines.append(_clip_text(str(entry.get("question", "") or ""), limit=2000))

    options = entry.get("options") or {}
    if isinstance(options, dict) and options:
        lines.append("")
        lines.append("**Options:**")
        for key in sorted(options.keys()):
            lines.append(f"- {key}. {options[key]}")

    user_answer = str(entry.get("user_answer", "") or "").strip()
    correct_answer = str(entry.get("correct_answer", "") or "").strip()
    if user_answer:
        lines.append("")
        lines.append(f"**User's Answer:** {_clip_text(user_answer, limit=1000)}")
    if correct_answer:
        lines.append(f"**Reference Answer:** {_clip_text(correct_answer, limit=1500)}")

    explanation = str(entry.get("explanation", "") or "").strip()
    if explanation:
        lines.append("")
        lines.append("**Explanation:**")
        lines.append(_clip_text(explanation, limit=2000))

    return "\n".join(lines)


async def _count_branch_user_turns(
    store: SessionStoreProtocol,
    session_id: str,
    leaf_message_id: int | None,
) -> int:
    """Count user messages on the active branch's ancestor chain.

    Used by the chat source inventory to assign ``first_seen_turn`` for
    *fresh* sources (= current turn = past_user_turns + 1). When
    ``leaf_message_id`` is ``None`` (legacy linear append) all messages
    in the session are counted; otherwise we walk the
    ``parent_message_id`` chain so sibling branches don't inflate the
    count. Kept tiny and protocol-only (``get_messages``) so it stays
    compatible with every store backend.
    """
    all_msgs = await store.get_messages(session_id)
    if leaf_message_id is None:
        return sum(1 for m in all_msgs if m.get("role") == "user")
    by_id: dict[int, dict[str, Any]] = {}
    for m in all_msgs:
        mid = m.get("id")
        if mid is not None:
            by_id[int(mid)] = m
    count = 0
    current: int | None = int(leaf_message_id)
    safety = 10_000
    while current is not None and safety > 0:
        current_message = by_id.get(int(current))
        if current_message is None:
            break
        if current_message.get("role") == "user":
            count += 1
        parent = current_message.get("parent_message_id")
        current = int(parent) if parent is not None else None
        safety -= 1
    return count


async def _build_question_bank_context(
    store: SessionStoreProtocol,
    entry_ids: list[Any],
) -> str:
    """Fetch the requested Question Bank entries and render them as context."""
    get_entry = getattr(store, "get_notebook_entry", None)
    if not callable(get_entry):
        return ""

    seen: set[int] = set()
    blocks: list[str] = []
    for raw in entry_ids:
        try:
            entry_id = int(raw)
        except (TypeError, ValueError):
            continue
        if entry_id in seen:
            continue
        seen.add(entry_id)
        try:
            entry = await get_entry(entry_id)
        except Exception:
            entry = None
        if not entry:
            continue
        blocks.append(_format_question_bank_entry(entry))
    return "\n\n---\n\n".join(blocks)


def _normalize_filename_list(raw: dict[str, Any]) -> list[str]:
    """Normalize the canonical multi-image answer field."""
    candidates = raw.get("user_answer_image_filenames")
    if not isinstance(candidates, list):
        return []
    cleaned: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name:
            cleaned.append(name)
    return cleaned


def _extract_followup_question_context(
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(config, dict):
        return None
    raw = config.pop("followup_question_context", None)
    if not isinstance(raw, dict):
        return None

    question = str(raw.get("question", "") or "").strip()
    question_id = str(raw.get("question_id", "") or "").strip()
    if not question:
        return None

    options = raw.get("options")
    normalized_options: dict[str, str] | None = None
    if isinstance(options, dict):
        normalized_options = {
            str(key).strip().upper()[:1]: str(value or "").strip()
            for key, value in options.items()
            if str(value or "").strip()
        }

    return {
        "parent_quiz_session_id": str(raw.get("parent_quiz_session_id", "") or "").strip(),
        "question_id": question_id,
        "question": question,
        "question_type": str(raw.get("question_type", "") or "").strip(),
        "options": normalized_options,
        "correct_answer": str(raw.get("correct_answer", "") or "").strip(),
        "explanation": str(raw.get("explanation", "") or "").strip(),
        "difficulty": str(raw.get("difficulty", "") or "").strip(),
        "concentration": str(raw.get("concentration", "") or "").strip(),
        "knowledge_context": _clip_text(str(raw.get("knowledge_context", "") or "").strip()),
        "user_answer": str(raw.get("user_answer", "") or "").strip(),
        "is_correct": raw.get("is_correct"),
        # Filenames of the learner's image answers, when any were attached.
        # The bytes are sent as regular WS attachments on the first
        # follow-up turn — we just record the filenames here so the system
        # prompt can tell the LLM *what* those attached images actually
        # are.
        "user_answer_image_filenames": _normalize_filename_list(raw),
        # Most recent AI-judge output the learner saw, if they ran the
        # judge. Forwarded so the follow-up tutor can build on the same
        # assessment rather than starting fresh.
        "ai_judgment": _clip_text(str(raw.get("ai_judgment", "") or "").strip()),
    }


def _extract_persist_user_message(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return True
    raw = config.pop("_persist_user_message", True)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() not in {"false", "0", "no"}
    return bool(raw)


def _extract_regenerate_flag(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    raw = config.pop("_regenerate", False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "1", "yes"}
    return bool(raw)


def _format_followup_question_context(context: dict[str, Any], language: str = "en") -> str:
    options = context.get("options") or {}
    option_lines = []
    if isinstance(options, dict) and options:
        for key, value in options.items():
            if value:
                option_lines.append(f"{key}. {value}")
    correctness = context.get("is_correct")
    correctness_text = (
        "correct" if correctness is True else "incorrect" if correctness is False else "unknown"
    )

    if str(language or "en").lower().startswith("zh"):
        lines = [
            "你正在处理一道测验题的后续追问。",
            "下面是本题上下文，请在后续回答中优先围绕这道题进行解释、纠错、延展和追问。",
            "如果用户提出超出本题的内容，也可以正常回答，但要保持和本题的连续性。",
            "",
            "[Question Follow-up Context]",
            f"Question ID: {context.get('question_id') or '(none)'}",
            f"Parent quiz session: {context.get('parent_quiz_session_id') or '(none)'}",
            f"Question type: {context.get('question_type') or '(none)'}",
            f"Difficulty: {context.get('difficulty') or '(none)'}",
            f"Concentration: {context.get('concentration') or '(none)'}",
            "",
            "Question:",
            context.get("question") or "(none)",
        ]
        if option_lines:
            lines.extend(["", "Options:", *option_lines])
        lines.extend(
            [
                "",
                f"User answer: {context.get('user_answer') or '(not provided)'}",
                f"User result: {correctness_text}",
                f"Reference answer: {context.get('correct_answer') or '(none)'}",
                "",
                "Explanation:",
                context.get("explanation") or "(none)",
            ]
        )
        image_filenames = context.get("user_answer_image_filenames") or []
        if isinstance(image_filenames, list) and image_filenames:
            filename_text = "、".join(image_filenames)
            count_text = f"{len(image_filenames)} 张" if len(image_filenames) > 1 else "一张"
            lines.extend(
                [
                    "",
                    "学习者作答附图：",
                    f"该作答共附了{count_text}图片（文件名：{filename_text}），"
                    f"随首条追问消息一起发送，是用户提交的作答内容的一部分，不是无关上下文。"
                    f"请结合图片中的文字/公式/草图进行解读，并将其视为对上面 “User answer” 文本的补充。",
                ]
            )
        ai_judgment = context.get("ai_judgment")
        if ai_judgment:
            lines.extend(
                [
                    "",
                    "AI 评判（之前已对学习者作答给出的评判，请基于此继续，不要重复完整重写）：",
                    ai_judgment,
                ]
            )
        if context.get("knowledge_context"):
            lines.extend(
                [
                    "",
                    "Knowledge context:",
                    context["knowledge_context"],
                ]
            )
        return "\n".join(lines).strip()

    lines = [
        "You are handling follow-up questions about a single quiz item.",
        "Use the question context below as the primary grounding for future turns in this session.",
        "If the user asks something broader, you may answer normally, but maintain continuity with this quiz item.",
        "",
        "[Question Follow-up Context]",
        f"Question ID: {context.get('question_id') or '(none)'}",
        f"Parent quiz session: {context.get('parent_quiz_session_id') or '(none)'}",
        f"Question type: {context.get('question_type') or '(none)'}",
        f"Difficulty: {context.get('difficulty') or '(none)'}",
        f"Concentration: {context.get('concentration') or '(none)'}",
        "",
        "Question:",
        context.get("question") or "(none)",
    ]
    if option_lines:
        lines.extend(["", "Options:", *option_lines])
    lines.extend(
        [
            "",
            f"User answer: {context.get('user_answer') or '(not provided)'}",
            f"User result: {correctness_text}",
            f"Reference answer: {context.get('correct_answer') or '(none)'}",
            "",
            "Explanation:",
            context.get("explanation") or "(none)",
        ]
    )
    image_filenames = context.get("user_answer_image_filenames") or []
    if isinstance(image_filenames, list) and image_filenames:
        joined = ", ".join(image_filenames)
        plural = "images were" if len(image_filenames) > 1 else "image was"
        plural_noun = (
            "Learner answer images" if len(image_filenames) > 1 else "Learner answer image"
        )
        lines.extend(
            [
                "",
                f"{plural_noun}:",
                f"{len(image_filenames)} {plural} attached to the first follow-up message "
                f"(filenames: {joined}). They are part of the learner's answer — read their "
                "text/formulas/sketches and treat them as a supplement to the typed `User answer` "
                "above, not unrelated context.",
            ]
        )
    ai_judgment = context.get("ai_judgment")
    if ai_judgment:
        lines.extend(
            [
                "",
                "Prior AI judgment (already shown to the learner — build on it instead of restating it in full):",
                ai_judgment,
            ]
        )
    if context.get("knowledge_context"):
        lines.extend(
            [
                "",
                "Knowledge context:",
                context["knowledge_context"],
            ]
        )
    return "\n".join(lines).strip()


@dataclass
class _LiveSubscriber:
    queue: asyncio.Queue[dict[str, Any] | None]


@dataclass
class _TurnExecution:
    turn_id: str
    session_id: str
    capability: str
    payload: dict[str, Any]
    task: asyncio.Task[None] | None = None
    subscribers: list[_LiveSubscriber] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    next_seq: int = 1
    events_flushed: bool = False
    # Created only by the server-side runtime together with the durable turn.
    # Gateway agentic chat receives this exact event through its private
    # context metadata; it is never reconstructed from a browser payload.
    gateway_cancellation_event: asyncio.Event = field(default_factory=asyncio.Event)


class TurnRuntimeManager:
    """Run one turn in the background and multiplex persisted/live events."""

    def __init__(self, store: SessionStoreProtocol | None = None) -> None:
        from traittutor.services.session import get_session_store

        self.store = store or get_session_store()
        self._lock = asyncio.Lock()
        self._executions: dict[str, _TurnExecution] = {}
        # Per-turn reply queues used by tools that pause the agentic
        # loop (e.g. ``ask_user``). Queue is created in ``_run_turn``
        # before the orchestrator is invoked and cleaned up in the
        # ``finally`` block, so callers of ``submit_user_reply`` see
        # ``False`` for any turn that is no longer awaiting input.
        # Each entry contains the canonical structured per-question replies.
        # Shape: {"answers": list[{"questionId": str, "text": str}]}.
        self._reply_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}

    async def has_live_execution(self, turn_id: str) -> bool:
        """Public check for whether this process still owns the turn's runner.

        Lets transport callers (e.g. the unified WS router) avoid reaching into
        ``_lock`` / ``_executions`` directly.
        """
        return await self._has_live_execution(turn_id)

    async def get_owned_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Resolve ``turn_id`` only when its session belongs to this request.

        WebSocket payloads contain opaque turn ids, not an authority grant.
        The store derives its owner boundary from ``get_current_user()`` and
        returns no row for a foreign session; checking the parent session as
        well keeps this correct for stores that persist turn/session records
        separately.  Call this before touching live execution maps or reply
        queues, which are process-wide and therefore cannot provide isolation
        on their own.
        """
        turn = await self.store.get_turn(turn_id)
        if turn is None:
            return None
        session_id = str(turn.get("session_id") or "").strip()
        if not session_id:
            return None
        session = await self.store.get_session(session_id)
        if session is None:
            return None
        resolved_session_id = str(session.get("id") or session.get("session_id") or "").strip()
        if resolved_session_id != session_id:
            return None
        return turn

    async def get_owned_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a session only through the current authenticated store scope."""
        session = await self.store.get_session(session_id)
        if session is None:
            return None
        resolved_session_id = str(session.get("id") or session.get("session_id") or "").strip()
        return session if resolved_session_id == session_id else None

    async def _has_live_execution(self, turn_id: str) -> bool:
        """Whether this process still owns the turn's in-memory runner."""
        async with self._lock:
            execution = self._executions.get(turn_id)
            if execution is None:
                return False
            # Some tests and pause/resubscribe paths create an execution
            # placeholder without a task. Treat its presence as live so we do
            # not falsely fail a turn that is still owned by this process.
            return execution.task is None or not execution.task.done()

    async def _fail_orphan_running_turn(self, turn: dict[str, Any] | None) -> dict[str, Any] | None:
        """Finalize a persisted running turn that has no local execution.

        Running turns are process-local: after a server/container restart the
        database row may still say ``running`` while the task and subscriber
        queues are gone. The runtime owns that liveness check, not the store,
        so recovery stays backend-agnostic.
        """
        if turn is None or str(turn.get("status") or "") != "running":
            return turn
        turn_id = str(turn.get("id") or turn.get("turn_id") or "").strip()
        if not turn_id or await self._has_live_execution(turn_id):
            return turn
        await self.store.update_turn_status(turn_id, "failed", _INTERRUPTED_TURN_ERROR)
        return await self.store.get_turn(turn_id)

    async def _recover_orphan_running_turns_for_session(self, session_id: str) -> None:
        """Clear stale active turns before creating a fresh turn."""
        for turn in await self.store.list_active_turns(session_id):
            await self._fail_orphan_running_turn(turn)

    async def start_turn(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        # Reject deterministic prompt-injection indicators before session
        # creation, preferences, turn rows, or the canonical ConversationStore
        # can be touched.  The online bridge repeats the guard so direct
        # callers cannot bypass this transport boundary.
        from traittutor.security.prompt_guard import PromptGuardRejected, enforce_prompt_guard

        try:
            enforce_prompt_guard(str(payload.get("content") or payload.get("message") or ""))
        except PromptGuardRejected as exc:
            raise RuntimeError(str(exc)) from exc
        capability = str(payload.get("capability") or "chat")
        raw_config = dict(payload.get("config", {}) or {})
        # The assistant's Solve shortcut is a user-facing entry point into
        # the server-owned deep-solve capability. Keep the browser payload
        # simple (`traittutor_mode=solve`) while ensuring the actual
        # turn gets the solve tools, deterministic step gate, and solve prompt
        # library. Explicit capability selections still win.
        if capability == "chat":
            traittutor_mode = str(raw_config.get("traittutor_mode") or "").strip()
            capability = _CHAT_MODE_CAPABILITIES.get(traittutor_mode, capability)
        # Validate the resolved runtime target before creating or updating any
        # durable session state. The orchestrator also rejects unknown names,
        # but that happens after the user message and turn are persisted and
        # used to leave an empty assistant message behind on configuration
        # drift (for example, a frontend shortcut targeting an unregistered
        # capability).
        from traittutor.runtime.registry.capability_registry import get_capability_registry

        capability_registry = get_capability_registry()
        if capability_registry.get(capability) is None:
            available = capability_registry.list_capabilities()
            raise RuntimeError(f"Unknown capability: {capability}. Available: {available}")
        runtime_only_keys = (
            "_persist_user_message",
            "_regenerate",
            "_regenerated_from_message_id",
            "_superseded_turn_id",
            "followup_question_context",
            # Product routing is evaluated by this runtime after capability
            # validation; it must never be sent to a strict chat/capability
            # request schema as though it were a tool option.
            "product_mode",
            "learning_pack_id",
            "learning_plan_id",
            "learning_support",
            "learning_canvas_excerpt",
            "use_tutor_persona",
            # TraitTutor generation shortcuts (courseware / flashcards / quiz
            # / exploration / diagrams) are handled by the chat runtime after
            # strict public config validation. Keep the selected mode in the
            # turn config, but do not pass it into ChatRequestConfig.
            "traittutor_mode",
        )
        runtime_only_config = {
            key: raw_config.pop(key) for key in runtime_only_keys if key in raw_config
        }
        product_mode = str(runtime_only_config.get("product_mode") or "").strip()
        if product_mode not in {"learn", "assist"}:
            raise RuntimeError("config.product_mode must be 'learn' or 'assist'.")
        try:
            from traittutor.runtime.request_contracts import validate_capability_config

            validated_public_config = validate_capability_config(capability, raw_config)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        payload = {
            **payload,
            "capability": capability,
            "config": {**validated_public_config, **runtime_only_config},
        }
        requested_session_id = str(payload.get("session_id") or "").strip()
        if requested_session_id:
            # A caller may continue only a durably owned session.  Do not
            # turn an unavailable id into a fresh session implicitly: that
            # masks an authorization failure and leaves an optimistic client
            # attached to the wrong transcript.
            session = await self.get_owned_session(requested_session_id)
            if session is None:
                raise RuntimeError("Session not found or unavailable.")
        else:
            session = await self.store.ensure_session(None)
        preferences = session.get("preferences") or {}
        # Persona is a session-level preference (mirrors llm_selection): an
        # explicit ``persona`` key in the payload — including an empty string,
        # which means "Default" / no persona — wins and is persisted below; an
        # absent key falls back to the session's stored preference so the
        # active persona survives reloads and follows the session.
        persona_explicit = "persona" in payload
        persona_pref = str(
            (payload.get("persona") if persona_explicit else preferences.get("persona")) or ""
        ).strip()
        payload = {**payload, "persona": persona_pref}
        mastery_binding: dict[str, Any] | None = None
        if capability == "mastery_path":
            # The public request can choose only a path ID.  Its owner,
            # subject and KC graph are loaded from the current user's durable
            # LearningProgress document, then persisted as a server-authored
            # session link.  No session id, book reference, or model content
            # participates in subject attribution.
            from traittutor.capabilities.mastery.binding import resolve_mastery_path_binding

            binding = resolve_mastery_path_binding(
                requested_path_id=validated_public_config.get("learning_path_id"),
                persisted_binding=preferences.get("mastery_path_binding"),
            )
            mastery_binding = binding.model_dump() if binding is not None else None
            payload = {**payload, "_mastery_path_binding": mastery_binding}
        raw_llm_selection = payload.get("llm_selection")
        if raw_llm_selection is None:
            raw_llm_selection = preferences.get("llm_selection")
        try:
            llm_selection = _llm_selection_dict(raw_llm_selection)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        if llm_selection:
            try:
                from traittutor.multi_user.model_access import apply_allowed_llm_selection

                llm_selection = apply_allowed_llm_selection(llm_selection) or {}
            except PermissionError as exc:
                raise RuntimeError(f"Invalid LLM selection: {exc}") from exc
        else:
            # Non-admin users MUST end up with a concrete llm_selection so we
            # never silently fall through to the global LLM client (which is
            # configured from admin runtime settings). Admin keeps the existing behavior
            # (None llm_selection → default config from admin scope).
            from traittutor.multi_user.context import get_current_user
            from traittutor.multi_user.model_access import (
                has_capability_access,
                redacted_model_access,
            )

            current_user = get_current_user()
            if not current_user.is_admin:
                # Single gate, shared with the frontend lock and any HTTP
                # surface: no usable LLM grant → a clear terminal error here
                # instead of a silent fall-through to the global client.
                if not has_capability_access("llm"):
                    raise RuntimeError(
                        "No LLM model is assigned to your account. Please contact an administrator."
                    )
                # Pin the first granted-and-available model as the selection.
                assigned_llms = [
                    item
                    for item in redacted_model_access(current_user.id).get("llm", [])
                    if item.get("available")
                ]
                llm_selection = {
                    "profile_id": str(assigned_llms[0].get("profile_id") or ""),
                    "model_id": str(assigned_llms[0].get("model_id") or ""),
                }
        if llm_selection:
            from traittutor.services.config import get_model_catalog_service
            from traittutor.services.model_selection import (
                LLMSelection,
                apply_llm_selection_to_catalog,
            )

            try:
                apply_llm_selection_to_catalog(
                    get_model_catalog_service().load(),
                    LLMSelection.from_payload(llm_selection),
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        # If the caller didn't pin a per-turn tool list (e.g. non-web
        # channels or the new web UI which sources tools from
        # /settings/tools), back-fill from the user's saved toggleable-tool
        # preference so the chat pipeline sees the same set the user picked
        # in Settings. Callers that explicitly pass ``tools`` (including
        # an empty list) keep their value untouched.
        if payload.get("tools") is None:
            try:
                from traittutor.api.routers.settings import get_enabled_optional_tools

                payload = {**payload, "tools": list(get_enabled_optional_tools())}
            except Exception:
                payload = {**payload, "tools": []}
        # Admin-imposed per-user tool whitelist (grant v2). Sits after the
        # back-fill so explicit caller lists and settings defaults pass the
        # same gate; this is the single enforcement point for every
        # capability's turn.
        from traittutor.multi_user.tool_access import allowed_optional_tools

        allowed_tools = allowed_optional_tools()
        if allowed_tools is not None:
            payload = {
                **payload,
                "tools": [t for t in (payload.get("tools") or []) if t in allowed_tools],
            }
        payload = {**payload, "llm_selection": llm_selection}
        await self._recover_orphan_running_turns_for_session(session["id"])
        preference_update: dict[str, Any] = {
            "capability": capability,
            "tools": list(payload.get("tools") or []),
            "knowledge_bases": list(payload.get("knowledge_bases") or []),
            "language": str(payload.get("language") or "en"),
        }
        saved_workspace_mode = str(preferences.get("workspace_mode") or "")
        if requested_session_id:
            if saved_workspace_mode not in {"learn", "assist"}:
                raise RuntimeError("Session is missing its canonical workspace mode.")
            if saved_workspace_mode != product_mode:
                raise RuntimeError("Session workspace mode does not match this entry point.")
            preference_update["workspace_mode"] = saved_workspace_mode
        else:
            preference_update["workspace_mode"] = product_mode
        if llm_selection:
            preference_update["llm_selection"] = llm_selection
        if persona_explicit:
            # Persist explicit set AND explicit clear ("" = back to Default).
            preference_update["persona"] = persona_pref
        if mastery_binding is not None:
            # Keep a useful, explicit selection for later turns/replay.  It
            # is revalidated at every use, so a copied or stale preference is
            # not an authority by itself.
            preference_update["mastery_path_binding"] = mastery_binding
        await self.store.update_session_preferences(session["id"], preference_update)
        turn = await self.store.create_turn(session["id"], capability=capability)
        execution = _TurnExecution(
            turn_id=turn["id"],
            session_id=session["id"],
            capability=capability,
            payload=dict(payload),
        )
        session_metadata: dict[str, Any] = {
            "session_id": session["id"],
            "turn_id": turn["id"],
        }
        regenerated_from = runtime_only_config.get("_regenerated_from_message_id")
        if regenerated_from is not None:
            session_metadata["regenerated_from_message_id"] = regenerated_from
        superseded_turn_id = runtime_only_config.get("_superseded_turn_id")
        if superseded_turn_id:
            session_metadata["superseded_turn_id"] = str(superseded_turn_id)
        if runtime_only_config.get("_regenerate"):
            session_metadata["regenerate"] = True
        await self._publish_live_event(
            execution,
            StreamEvent(
                type=StreamEventType.SESSION,
                source="turn_runtime",
                metadata=session_metadata,
            ),
        )
        async with self._lock:
            self._executions[turn["id"]] = execution
            execution.task = asyncio.create_task(self._run_turn(execution))
        return session, turn

    async def regenerate_last_turn(
        self,
        session_id: str,
        overrides: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Re-run the prior user message in ``session_id``.

        Deletes the trailing assistant message (if any), then dispatches a new
        turn with ``_persist_user_message=False`` and ``_regenerate=True`` so
        the runtime knows not to duplicate the user row or refresh long-term
        memory a second time. The original user message stays in place.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            raise RuntimeError("nothing_to_regenerate")

        session = await self.store.get_session(session_id)
        if session is None:
            raise RuntimeError("nothing_to_regenerate")

        active = await self.store.get_active_turn(session_id)
        if active is not None:
            raise RuntimeError("regenerate_busy")

        last_user = await self.store.get_last_message(session_id, role="user")
        if last_user is None:
            raise RuntimeError("nothing_to_regenerate")

        last_message = await self.store.get_last_message(session_id)
        previous_turn_id: str | None = None
        if last_message is not None and last_message.get("role") == "assistant":
            for event in last_message.get("events") or []:
                turn_id = str((event or {}).get("turn_id") or "")
                if turn_id:
                    previous_turn_id = turn_id
                    break
            await self.store.delete_message(last_message["id"])

        preferences = session.get("preferences") or {}
        overrides = overrides or {}
        snapshot = {}
        metadata = last_user.get("metadata") or {}
        if isinstance(metadata, dict):
            candidate = metadata.get("request_snapshot") or metadata.get("requestSnapshot")
            if isinstance(candidate, dict):
                snapshot = candidate

        capability = str(
            overrides.get("capability")
            or last_user.get("capability")
            or preferences.get("capability")
            or "chat"
        )
        tools_value = (
            overrides.get("tools")
            if overrides.get("tools") is not None
            else preferences.get("tools") or []
        )
        tools = list(tools_value) if isinstance(tools_value, list) else []
        knowledge_bases_value = (
            overrides.get("knowledge_bases")
            if overrides.get("knowledge_bases") is not None
            else preferences.get("knowledge_bases") or []
        )
        knowledge_bases = (
            list(knowledge_bases_value) if isinstance(knowledge_bases_value, list) else []
        )
        language = str(overrides.get("language") or preferences.get("language") or "en")

        config: dict[str, Any] = dict(overrides.get("config") or {})
        config.update(
            {
                "_persist_user_message": False,
                "_regenerate": True,
                "_regenerated_from_message_id": int(last_user["id"]),
            }
        )
        if previous_turn_id:
            config["_superseded_turn_id"] = previous_turn_id
        llm_selection = (
            overrides.get("llm_selection")
            if overrides.get("llm_selection") is not None
            else snapshot.get("llmSelection") or preferences.get("llm_selection")
        )

        payload: dict[str, Any] = {
            "session_id": session_id,
            "capability": capability,
            "content": str(last_user.get("content", "") or ""),
            "tools": tools,
            "knowledge_bases": knowledge_bases,
            "language": language,
            "attachments": list(last_user.get("attachments") or []),
            "notebook_references": [],
            "history_references": [],
            "config": config,
        }
        notebook_references = (
            overrides.get("notebook_references")
            if overrides.get("notebook_references") is not None
            else preferences.get("notebook_references") or []
        )
        history_references = (
            overrides.get("history_references")
            if overrides.get("history_references") is not None
            else preferences.get("history_references") or []
        )
        payload["notebook_references"] = (
            list(notebook_references) if isinstance(notebook_references, list) else []
        )
        payload["history_references"] = (
            list(history_references) if isinstance(history_references, list) else []
        )
        if llm_selection:
            payload["llm_selection"] = llm_selection
        return await self.start_turn(payload)

    async def cancel_turn(self, turn_id: str) -> bool:
        # Authorize before consulting ``_executions``: an in-memory execution
        # belongs to a process, not a WebSocket caller, and checking it first
        # would let a foreign authenticated user cancel another user's live turn.
        if await self.get_owned_turn(turn_id) is None:
            return False
        async with self._lock:
            execution = self._executions.get(turn_id)
        if execution is not None:
            # Owner/session authorization was resolved above before looking up
            # the process-local execution.  Signal the exact server-created
            # event so an opt-in Gateway stream can stop cooperatively even
            # when this runtime has not attached its task yet.
            execution.gateway_cancellation_event.set()
        if execution is None or execution.task is None or execution.task.done():
            turn = await self.store.get_turn(turn_id)
            if turn is None or turn.get("status") != "running":
                return False
            await self.store.update_turn_status(turn_id, "cancelled", "Turn cancelled")
            return True
        execution.task.cancel()
        # Wait for the task to finish so its finally block (including save)
        # completes before the caller proceeds.
        try:
            await execution.task
        except asyncio.CancelledError:
            pass
        return True

    async def submit_user_reply(
        self,
        turn_id: str,
        *,
        answers: list[dict[str, str]],
    ) -> bool:
        """Deliver a user reply to a turn that's paused on ``ask_user``.

        Returns ``True`` if the turn was waiting and the reply was
        accepted; ``False`` if no waiter is registered (turn finished,
        was cancelled, or the model never asked).

        Accepts the canonical list of ``{questionId, text}`` pairs. The payload is enqueued —
        the pipeline's ``await waiter()`` call unblocks on the next
        event-loop tick and substitutes the reply into the matching
        ``role=tool`` message.
        """
        # The reply queue is process-wide.  Never use its presence as an
        # ownership signal; resolve the durable turn/session boundary first.
        if await self.get_owned_turn(turn_id) is None:
            return False
        queue = self._reply_queues.get(turn_id)
        if queue is None:
            return False
        payload: dict[str, Any] = {"answers": answers}
        await queue.put(payload)
        return True

    async def subscribe_turn(
        self,
        turn_id: str,
        after_seq: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        # Fetching replay events is a read of the learner's transcript, so it
        # gets the same durable owner check as cancel/reply before any store
        # or live-subscriber access occurs.
        if await self.get_owned_turn(turn_id) is None:
            return
        backlog = await self.store.get_turn_events(turn_id, after_seq=after_seq)
        last_seq = after_seq
        # Track whether we ever yielded a terminal event (DONE) — if the live
        # queue ends WITHOUT one (e.g. a transient send-side stall on
        # ``safe_send`` swallowed it), we synthesise one before returning so
        # the frontend's ``isStreaming`` state clears immediately rather than
        # waiting on the 45s heartbeat-timeout + reconnect catchup path.
        done_yielded = False

        def _track(item: dict[str, Any]) -> dict[str, Any]:
            nonlocal done_yielded
            if str(item.get("type") or "") == "done":
                done_yielded = True
            return item

        for item in backlog:
            last_seq = max(last_seq, int(item.get("seq") or 0))
            yield _track(item)

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        subscriber = _LiveSubscriber(queue=queue)
        execution: _TurnExecution | None = None
        live_backlog: list[dict[str, Any]] = []
        async with self._lock:
            execution = self._executions.get(turn_id)
            if execution is not None:
                execution.subscribers.append(subscriber)
                live_backlog = [
                    item for item in execution.events if int(item.get("seq") or 0) > last_seq
                ]

        for item in live_backlog:
            seq = int(item.get("seq") or 0)
            if seq <= last_seq:
                continue
            last_seq = seq
            yield _track(item)

        catchup = []
        if execution is None:
            catchup = await self.store.get_turn_events(turn_id, after_seq=last_seq)
        for item in catchup:
            seq = int(item.get("seq") or 0)
            if seq <= last_seq:
                continue
            last_seq = seq
            yield _track(item)

        turn = await self.store.get_turn(turn_id)
        if execution is None:
            turn = await self._fail_orphan_running_turn(turn)
            if turn is None or turn.get("status") != "running":
                # Turn already finished and we didn't see a DONE in any of the
                # persisted history above — synthesise one so the caller can
                # still close out its streaming state cleanly.
                if not done_yielded:
                    if turn is not None and str(turn.get("status") or "") == "failed":
                        error_event = self._synthesize_error_event(turn_id, turn)
                        if error_event is not None:
                            yield error_event
                    yield self._synthesize_done_event(turn_id, turn)
                return
        try:
            while True:
                live_item = await queue.get()
                if live_item is None:
                    break
                seq = int(live_item.get("seq") or 0)
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield _track(live_item)
        finally:
            async with self._lock:
                execution = self._executions.get(turn_id)
                if execution is not None:
                    execution.subscribers = [
                        sub for sub in execution.subscribers if sub is not subscriber
                    ]
            # Safety net: if we drained the live queue (None sentinel arrived)
            # without ever yielding a DONE, the turn is over server-side but
            # the frontend wouldn't know. Read the persisted turn status one
            # more time and synthesise a terminal DONE only for genuinely
            # terminal turns so ``isStreaming`` clears without waiting on
            # the heartbeat-reconnect fallback. A running turn may be paused
            # on ``ask_user`` or may have had this subscription replaced; in
            # that case a synthetic DONE would falsely mark the turn
            # completed while the backend is still awaiting input.
            if not done_yielded:
                final_turn = await self.store.get_turn(turn_id)
                final_status = str((final_turn or {}).get("status") or "").strip()
                if final_turn is None or final_status in {"failed", "cancelled", "completed"}:
                    yield self._synthesize_done_event(turn_id, final_turn)

    @staticmethod
    def _synthesize_done_event(turn_id: str, turn: dict[str, Any] | None) -> dict[str, Any]:
        """Build a DONE event payload from the persisted turn status.

        Used as a recovery path when ``subscribe_turn`` finishes without
        ever observing a live or persisted DONE event for a turn that has
        nonetheless terminated server-side. Mirrors the shape of the
        events the runtime would normally publish so the frontend doesn't
        need a special code path to consume it.
        """
        status = "completed"
        error: str | None = None
        if turn is not None:
            raw_status = str(turn.get("status") or "").strip()
            if raw_status in {"failed", "cancelled", "completed"}:
                status = raw_status
            error_text = str(turn.get("error") or "").strip()
            if error_text:
                error = error_text
        metadata: dict[str, Any] = {"status": status, "synthesized": True}
        if error:
            metadata["error"] = error
        return {
            "type": "done",
            "source": "turn_runtime",
            "stage": "",
            "content": "",
            "metadata": metadata,
            "session_id": "",
            "turn_id": turn_id,
            "seq": 0,
        }

    @staticmethod
    def _synthesize_error_event(turn_id: str, turn: dict[str, Any] | None) -> dict[str, Any] | None:
        """Build a terminal ERROR event from a failed persisted turn."""
        error = str((turn or {}).get("error") or "").strip()
        if not error:
            return None
        return {
            "type": "error",
            "source": "turn_runtime",
            "stage": "",
            "content": error,
            "metadata": {"status": "failed", "synthesized": True},
            "session_id": str((turn or {}).get("session_id") or ""),
            "turn_id": turn_id,
            "seq": 0,
        }

    async def subscribe_session(
        self,
        session_id: str,
        after_seq: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        if await self.get_owned_session(session_id) is None:
            return
        active_turn = await self.store.get_active_turn(session_id)
        if active_turn is None:
            return
        async for item in self.subscribe_turn(active_turn["id"], after_seq=after_seq):
            yield item

    async def _run_turn(self, execution: _TurnExecution) -> None:  # noqa: C901 —
        # one-pass streaming turn engine; decomposition needs the dual-entry
        # Playwright suite green before and after. Gate stays active at 40.
        payload = execution.payload
        session_id = execution.session_id
        capability_name = execution.capability
        turn_id = execution.turn_id
        attachments = []
        attachment_records = []
        assistant_events: list[dict[str, Any]] = []
        assistant_content = ""
        new_user_message_id: int | None = None
        assistant_message_id: int | None = None
        regenerated_from_message_id: int | None = None
        # Per-round content segments + narration call_ids: a chat-loop round's
        # text is captured live but a round that resolves as narration is
        # dropped from the persisted answer (mirrors the frontend bubble).
        content_segments: list[tuple[str | None, str]] = []
        narration_call_ids: set[str] = set()

        def _persisted_answer() -> str:
            # clean_thinking_tags is a second line of defence: providers that
            # inline <think> in the content channel are split at streaming
            # time by the agent loop, but anything that slips through must
            # never be persisted as the user-facing answer.
            return clean_thinking_tags(
                "".join(
                    text
                    for call_id, text in content_segments
                    if not (call_id and call_id in narration_call_ids)
                )
            )

        # Files the model generated this turn (exec/code_execution artifacts),
        # persisted as assistant-message attachments so the UI shows openable
        # cards. Deduped by URL across the turn's SOURCES events.
        generated_attachments: list[dict[str, Any]] = []
        seen_artifact_urls: set[str] = set()
        stream_done_sent = False
        llm_scope_token: Token[LLMConfig | None] | None = None
        reset_active_llm_selection: Callable[[Token[LLMConfig | None] | None], None] | None = None
        # One queue per turn for ``ask_user`` style pause-resume.
        # Created here (BEFORE the orchestrator runs) so the pipeline can
        # await on the awaitable we publish into ``context.metadata``.
        # Cleaned up unconditionally in the outer ``finally``.
        reply_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reply_queues[turn_id] = reply_queue

        async def _wait_for_user_reply() -> dict[str, Any] | None:
            return await reply_queue.get()

        try:
            from traittutor.agents.notebook import NotebookAnalysisAgent
            from traittutor.core.context import Attachment, UnifiedContext
            from traittutor.runtime.orchestrator import ChatOrchestrator
            from traittutor.services.model_selection.runtime import (
                activate_llm_selection,
            )
            from traittutor.services.model_selection.runtime import (
                reset_llm_selection as reset_active_llm_selection,
            )
            from traittutor.services.notebook import get_notebook_manager
            from traittutor.services.session.context_builder import ContextBuilder
            from traittutor.services.skill import get_skill_service

            request_config = dict(payload.get("config", {}) or {})
            # ``product_mode`` is validated in ``start_turn`` and drives the
            # workspace-mode preference there. It is not a capability setting,
            # so it is stripped from the per-turn config to avoid Pydantic
            # rejecting an otherwise valid chat turn as an unknown field.
            # Executor selection no longer branches on it (ADR-0009): every
            # turn runs through ChatOrchestrator → CapabilityRegistry.
            request_config.pop("product_mode", None)
            learning_pack_id = str(request_config.pop("learning_pack_id", "") or "").strip() or None
            learning_plan_id = str(request_config.pop("learning_plan_id", "") or "").strip() or None
            learning_support = request_config.pop("learning_support", False) is True
            learning_canvas_excerpt = str(
                request_config.pop("learning_canvas_excerpt", "") or ""
            ).strip()[:24_000]
            use_tutor_persona = request_config.pop("use_tutor_persona", True) is not False
            followup_question_context = _extract_followup_question_context(request_config)
            persist_user_message = _extract_persist_user_message(request_config)
            is_regenerate = _extract_regenerate_flag(request_config)
            raw_regenerated_from = request_config.pop("_regenerated_from_message_id", None)
            try:
                regenerated_from_message_id = (
                    int(raw_regenerated_from) if raw_regenerated_from is not None else None
                )
            except (TypeError, ValueError):
                regenerated_from_message_id = None
            request_config.pop("_superseded_turn_id", None)
            traittutor_mode = str(request_config.get("traittutor_mode", "") or "").strip()
            raw_user_content = str(payload.get("content", "") or "")
            # Every non-empty conversation must identify the exact parent.
            # Only the first message of an empty session may omit the field;
            # it is normalized here to an explicit root parent.
            branch_parent_explicit = "parent_message_id" in payload
            branch_parent_raw = payload.get("parent_message_id")
            branch_parent_id: int | None
            if branch_parent_explicit:
                try:
                    branch_parent_id = (
                        int(branch_parent_raw) if branch_parent_raw is not None else None
                    )
                except (TypeError, ValueError):
                    branch_parent_id = None
                    branch_parent_explicit = False
            else:
                branch_parent_id = None
                if await self.store.get_messages(session_id):
                    raise RuntimeError("parent_message_id is required for a non-empty session.")
                branch_parent_explicit = True
            notebook_references = payload.get("notebook_references", []) or []
            history_references = payload.get("history_references", []) or []
            question_notebook_references = payload.get("question_notebook_references", []) or []
            learning_artifact_references = payload.get("learning_artifact_references", []) or []
            memory_references = _extract_memory_references(payload)
            notebook_context = ""
            history_context = ""
            question_bank_context = ""

            import base64 as _b64
            import uuid as _uuid

            from traittutor.services.storage import get_attachment_store

            for item in payload.get("attachments", []):
                record = {
                    "type": item.get("type", "file"),
                    "url": item.get("url", ""),
                    "base64": item.get("base64", ""),
                    "filename": item.get("filename", ""),
                    "mime_type": item.get("mime_type", ""),
                    "id": item.get("id", "") or _uuid.uuid4().hex[:12],
                }
                attachment_records.append(record)

            # Persist original bytes to the attachment store before extraction
            # so the frontend preview drawer can fetch the file later. The
            # extractor will clear base64 on documents to keep DB rows lean,
            # but the URL we record here outlives that pruning. Upload errors
            # are non-fatal — extraction still runs from the in-memory base64.
            attachment_store = get_attachment_store()
            for record in attachment_records:
                if record.get("url"):
                    continue  # already hosted (e.g. legacy URL)
                b64 = record.get("base64") or ""
                if not b64:
                    continue
                try:
                    raw_bytes = _b64.b64decode(b64, validate=False)
                except Exception as exc:
                    logger.warning(
                        "skipping attachment upload for %r: invalid base64 (%s)",
                        record.get("filename"),
                        exc,
                    )
                    continue
                try:
                    record["url"] = await attachment_store.put(
                        session_id=session_id,
                        attachment_id=record["id"],
                        filename=record.get("filename", "") or "file",
                        data=raw_bytes,
                        mime_type=record.get("mime_type", "") or "",
                    )
                except Exception as exc:
                    logger.warning(
                        "attachment store rejected %r: %s",
                        record.get("filename"),
                        exc,
                    )

            from traittutor.utils.document_extractor import extract_documents_from_records

            document_texts, attachment_records = extract_documents_from_records(attachment_records)
            attachments = [
                Attachment(
                    type=r.get("type", "file"),
                    url=r.get("url", ""),
                    base64=r.get("base64", ""),
                    filename=r.get("filename", ""),
                    mime_type=r.get("mime_type", ""),
                    id=r.get("id", ""),
                    extracted_text=r.get("extracted_text", ""),
                )
                for r in attachment_records
            ]
            # DB persistence copy: drop base64 unconditionally now that the
            # original bytes live in the attachment store. Image attachments
            # used to keep base64 here (which bloated message rows); the URL
            # is now the stable source for previews.
            persisted_attachment_records = [
                {
                    **{k: v for k, v in r.items() if k != "base64"},
                    "base64": "",
                }
                for r in attachment_records
            ]

            if followup_question_context:
                existing_messages = await self.store.get_messages_for_context(
                    session_id, leaf_message_id=branch_parent_id
                )
                if not existing_messages:
                    await self.store.add_message(
                        session_id=session_id,
                        role="system",
                        content=_format_followup_question_context(
                            followup_question_context,
                            language=str(payload.get("language", "en") or "en"),
                        ),
                        capability=capability_name or "chat",
                        parent_message_id=None,
                    )

            llm_config, llm_scope_token = activate_llm_selection(payload.get("llm_selection"))
            builder = ContextBuilder(self.store)

            async def _emit_context_event(event: StreamEvent) -> None:
                if event.source in {"context", "context_builder"}:
                    return
                await self._publish_live_event(execution, event)

            history_result = await builder.build(
                session_id=session_id,
                llm_config=llm_config,
                language=payload.get("language", "en"),
                on_event=_emit_context_event,
                leaf_message_id=branch_parent_id,
            )
            # Learner personalization passes a minimal, versioned Compass-like
            # teaching plan, never a raw memory document.
            memory_context = ""
            try:
                from traittutor.personalization import get_personalization_service

                learner_context = get_personalization_service().build_context(
                    purpose="chat",
                    # Fresh attachments are the most trustworthy current-turn
                    # subject cue.  Their extracted text is passed only to the
                    # local classifier; the resulting minimal context, never
                    # raw attachment text or legacy memory documents, goes to the model.
                    title=" ".join(str(item.get("filename") or "") for item in attachment_records),
                    text="\n".join(
                        [
                            str(payload.get("content") or payload.get("message") or ""),
                            *document_texts,
                        ]
                    )[:24000],
                    current_instruction=str(payload.get("content") or payload.get("message") or ""),
                    session_id=session_id,
                )
                memory_context = (
                    "<personalization_context>\n"
                    f"{json.dumps(learner_context.model_dump(exclude={'trace_id'}), ensure_ascii=False)}\n"
                    "</personalization_context>\n"
                    "Use this only as bounded teaching guidance. Do not mention personality scores, "
                    "diagnoses, learning styles, or hidden reasoning."
                )
            except Exception:
                logger.warning(
                    "Learner-model context unavailable; continuing with standard chat",
                    exc_info=True,
                )

            # Tutor style comes from the user's typed Tutor Persona profile.
            # The retired file-based persona presets are deliberately not
            # consulted here: the configured tutor is the single conversation
            # role and its allowlisted expression contract is the only style
            # input sent to the model.
            from traittutor.multi_user.context import get_current_user
            from traittutor.multi_user.paths import get_admin_path_service
            from traittutor.multi_user.skill_access import assigned_skill_ids
            from traittutor.services.skill.service import SkillService, render_skills_manifest
            from traittutor.tutor_persona.context_adapter import TutorPersonaContextAdapter
            from traittutor.tutor_persona.store import TutorPersonaStore

            current_user = get_current_user()
            persona_context = ""
            tutor_profile = (
                TutorPersonaStore(current_user.id).get_current() if use_tutor_persona else None
            )
            tutor_expression: dict[str, Any] | None = None
            if tutor_profile is not None:
                tutor_context = TutorPersonaContextAdapter.adapt(tutor_profile)
                expression = tutor_context.contract.expression.model_dump(mode="json")
                tutor_expression = expression
                persona_context = (
                    "<tutor_persona>\n"
                    f"{json.dumps({'profile_ref': tutor_context.profile_ref, 'contract_hash': tutor_context.contract_hash, 'expression': expression}, ensure_ascii=False)}\n"
                    "</tutor_persona>\n"
                    "Use this configured tutor style for expression only. It must not change grading, answers, learning state, or safety decisions."
                )
            active_persona = "tutor-persona" if persona_context else ""

            # Skills: never user-selected per turn. The model sees a
            # one-line manifest of every skill visible to this user (own +
            # builtin, plus admin-assigned for non-admin users) and pulls
            # full content on demand via ``read_skill``. ``always`` skills
            # are the exception — their bodies are injected eagerly.
            user_skill_service = get_skill_service()
            skill_entries = user_skill_service.summary_entries()
            always_blocks = [user_skill_service.load_always_for_context()]
            if not current_user.is_admin:
                assigned_service = SkillService(
                    root=get_admin_path_service().get_workspace_dir() / "skills",
                    builtin_root=None,
                )
                allowed_skills = assigned_skill_ids(current_user.id)
                assigned_entries = [
                    e for e in assigned_service.summary_entries() if e.name in allowed_skills
                ]
                skill_entries = skill_entries + assigned_entries
                always_blocks.append(
                    assigned_service.load_for_context(
                        [e.name for e in assigned_entries if e.always and e.available]
                    )
                )
            skills_manifest = "\n\n".join(
                part for part in (*always_blocks, render_skills_manifest(skill_entries)) if part
            )

            # Chat capability uses the lightweight manifest + read_source
            # affordance (no upstream LLM call, no wholesale-dump into the
            # user message). All other capabilities keep the legacy concat
            # path because their internal pipelines consume the named blocks
            # (``[Notebook Context]`` etc.) directly.
            is_chat_capability = (capability_name or "") in _CHAT_LOOP_CAPABILITIES

            source_manifest_text = ""
            source_index: dict[str, str] = {}

            if is_chat_capability:
                from traittutor.services.session.source_inventory import (
                    build_inventory,
                    render_manifest,
                )

                resolved_notebook_records = (
                    get_notebook_manager().get_records_by_references(notebook_references)
                    if notebook_references
                    else []
                )
                # Current turn ordinal = (#user messages on this branch's
                # ancestor chain) + 1. ``_count_branch_user_turns`` walks
                # the same lineage the inventory builder uses, so we agree
                # on what "turn N" means for the historical labels.
                current_turn_ordinal = (
                    await _count_branch_user_turns(self.store, session_id, branch_parent_id) + 1
                )
                inventory = await build_inventory(
                    self.store,
                    session_id=session_id,
                    leaf_message_id=branch_parent_id,
                    current_turn_ordinal=current_turn_ordinal,
                    fresh_attachment_records=attachment_records,
                    fresh_notebook_records=resolved_notebook_records,
                    fresh_history_session_ids=history_references,
                    fresh_question_entry_ids=question_notebook_references,
                    fresh_learning_artifact_references=learning_artifact_references,
                    language=str(payload.get("language", "en") or "en"),
                )
                source_manifest_text, source_index = render_manifest(inventory)
                effective_user_message = raw_user_content
            else:
                if notebook_references:
                    referenced_records = get_notebook_manager().get_records_by_references(
                        notebook_references
                    )
                    if referenced_records:
                        analysis_agent = NotebookAnalysisAgent(
                            language=str(payload.get("language", "en") or "en")
                        )
                        notebook_context = await analysis_agent.analyze(
                            user_question=raw_user_content,
                            records=referenced_records,
                            emit=_emit_context_event,
                        )

                if history_references:
                    from traittutor.services.session.source_inventory import (
                        serialize_referenced_transcript,
                    )

                    history_records: list[dict[str, Any]] = []
                    for session_ref in history_references:
                        history_session_id = str(session_ref or "").strip()
                        if not history_session_id:
                            continue

                        history_session = await self.store.get_session(history_session_id)
                        if not history_session:
                            continue

                        history_messages = await self.store.get_messages_for_context(
                            history_session_id
                        )
                        transcript = serialize_referenced_transcript(
                            history_session,
                            history_messages,
                            language=str(payload.get("language", "en") or "en"),
                        )
                        if not transcript:
                            continue

                        history_summary = str(
                            history_session.get("compressed_summary", "") or ""
                        ).strip()
                        if not history_summary:
                            history_summary = _clip_text(
                                " ".join(
                                    str(message.get("content", "") or "").strip()
                                    for message in history_messages[-4:]
                                    if str(message.get("content", "") or "").strip()
                                ),
                                limit=400,
                            )
                        if not history_summary:
                            history_summary = f"{len(history_messages)} messages"

                        history_records.append(
                            {
                                "id": history_session_id,
                                "notebook_id": "__history__",
                                "notebook_name": "History",
                                "title": str(
                                    history_session.get("title", "") or "Untitled session"
                                ),
                                "summary": history_summary,
                                "output": transcript,
                                "metadata": {
                                    "session_id": history_session_id,
                                    "source": "history",
                                },
                            }
                        )

                    if history_records:
                        analysis_agent = NotebookAnalysisAgent(
                            language=str(payload.get("language", "en") or "en")
                        )
                        history_context = await analysis_agent.analyze(
                            user_question=raw_user_content,
                            records=history_records,
                            emit=_emit_context_event,
                        )
                        if not history_context.strip():
                            MAX_FALLBACK_CHARS = 8000
                            parts: list[str] = []
                            total = 0
                            for record in history_records:
                                output = record.get("output")
                                if not output:
                                    continue
                                part = f"## Session: {record.get('title', 'Untitled')}\n{output}"
                                if total + len(part) > MAX_FALLBACK_CHARS:
                                    remaining = MAX_FALLBACK_CHARS - total
                                    if remaining > 100:
                                        parts.append(part[:remaining] + "\n...(truncated)")
                                    break
                                parts.append(part)
                                total += len(part)
                            history_context = "\n\n".join(parts)

                if question_notebook_references:
                    question_bank_context = await _build_question_bank_context(
                        self.store, question_notebook_references
                    )

                effective_user_message = raw_user_content
                context_parts: list[str] = []
                if document_texts:
                    context_parts.append("[Attached Documents]\n" + "\n\n".join(document_texts))
                if notebook_context:
                    context_parts.append(f"[Notebook Context]\n{notebook_context}")
                if history_context:
                    context_parts.append(f"[History Context]\n{history_context}")
                if question_bank_context:
                    context_parts.append(f"[Question Bank Context]\n{question_bank_context}")
                if context_parts:
                    context_parts.append(f"[User Question]\n{raw_user_content}")
                    effective_user_message = "\n\n".join(context_parts)

            conversation_history = list(history_result.conversation_history)
            conversation_context_text = history_result.context_text

            if persist_user_message:
                new_user_message_id = await self.store.add_message(
                    session_id=session_id,
                    role="user",
                    content=raw_user_content,
                    capability=capability_name,
                    attachments=persisted_attachment_records,
                    metadata=_request_snapshot_metadata(
                        payload=payload,
                        content=raw_user_content,
                        capability=capability_name,
                        config=request_config,
                        attachments=persisted_attachment_records,
                        notebook_references=notebook_references,
                        history_references=history_references,
                        question_notebook_references=question_notebook_references,
                        learning_artifact_references=learning_artifact_references,
                        persona=active_persona,
                        memory_references=memory_references,
                        llm_selection=payload.get("llm_selection"),
                    ),
                    parent_message_id=branch_parent_id,
                )

            mastery_binding = payload.get("_mastery_path_binding")
            bound_subject_id = (
                str(mastery_binding.get("subject_id") or "").strip()
                if isinstance(mastery_binding, dict)
                else ""
            )
            assistant_context_snapshot = _assemble_assistant_context_snapshot(
                capability=capability_name,
                learning_support=learning_support,
                user_id=current_user.id,
                session_id=session_id,
                subject_id=bound_subject_id or None,
                learning_plan_id=learning_plan_id,
                include_tutor_persona=use_tutor_persona,
            )

            context = UnifiedContext(
                session_id=session_id,
                user_message=effective_user_message,
                conversation_history=conversation_history,
                enabled_tools=payload.get("tools"),
                allowed_builtin_tools=(
                    _AUTO_ARTIFACT_BUILTINS if traittutor_mode in _AUTO_ARTIFACT_MODES else None
                ),
                active_capability=payload.get("capability"),
                knowledge_bases=payload.get("knowledge_bases", []),
                attachments=attachments,
                config_overrides=request_config,
                language=payload.get("language", "en"),
                memory_context=memory_context,
                persona_context=persona_context,
                skills_manifest=skills_manifest,
                source_manifest=source_manifest_text,
                metadata={
                    "conversation_summary": history_result.conversation_summary,
                    "conversation_context_text": conversation_context_text,
                    "history_token_count": history_result.token_count,
                    "history_budget": history_result.budget,
                    "turn_id": turn_id,
                    "question_followup_context": followup_question_context or {},
                    "notebook_references": notebook_references,
                    "history_references": history_references,
                    "question_notebook_references": question_notebook_references,
                    "learning_artifact_references": learning_artifact_references,
                    "memory_references": memory_references,
                    "question_bank_context": question_bank_context,
                    "memory_context": memory_context,
                    "active_persona": active_persona,
                    "llm_selection": payload.get("llm_selection") or {},
                    "llm_model": str(getattr(llm_config, "model", "") or ""),
                    "llm_provider": str(getattr(llm_config, "provider_name", "") or ""),
                    "traittutor_mode": traittutor_mode,
                    # Only TurnRuntimeManager can populate this. The public
                    # request carries at most an existing path ID; the binding
                    # itself contains the owner-derived subject/KC graph fence.
                    "mastery_path_binding": payload.get("_mastery_path_binding"),
                    # Per-turn full-text payload for read_source. Empty when
                    # the manifest is empty (non-chat capabilities, or chat
                    # turns with no attached sources). Consumed by the chat
                    # pipeline's tool kwargs injector.
                    "source_index": source_index,
                    # Pause-resume hook: the agentic chat pipeline awaits
                    # this callable when ``ask_user`` (or any other
                    # ``pause_for_user``-emitting tool) pauses the loop.
                    # The callable resolves when the frontend POSTs a
                    # reply via the ``submit_user_reply`` WS message.
                    "wait_for_user_reply": _wait_for_user_reply,
                    # These are server-authored controls, not public request
                    # data.  Agentic Gateway calls fail closed without them
                    # so a browser cannot forge ownership or cancel scope.
                    "gateway_owner_id": str(get_current_user().id),
                    "gateway_cancellation_event": execution.gateway_cancellation_event,
                    # Learning-support contract: the read-only canvas Q&A path.
                    # ChatCapability reads these to inject the source materials
                    # and the read-only/tutor-expression prompt contracts that
                    # the retired agent_runtime LangGraph used to assemble.
                    "learning_support": learning_support,
                    "learning_support_materials": _build_learning_support_materials(
                        source_index=source_index,
                        learning_support=learning_support,
                        learning_canvas_excerpt=learning_canvas_excerpt,
                        learning_pack_id=learning_pack_id,
                        learning_plan_id=learning_plan_id,
                    ),
                    "tutor_expression": tutor_expression,
                    # Immutable, owner-authorized references assembled before
                    # capability execution. The snapshot never carries raw
                    # answers, rubrics, chats, or BKT posterior values.
                    "assistant_context_snapshot": assistant_context_snapshot,
                },
            )

            # All Assistant dialog — learn or assist — runs through the single
            # ChatOrchestrator → CapabilityRegistry chain (ADR-0009).  The
            # retired agent_runtime LangGraph branch is removed; classification
            # is owned by assistant_routing and execution by the registered
            # capability, with learning-support contracts carried via context.
            pending_done_event: StreamEvent | None = None
            orch = ChatOrchestrator()
            async for event in orch.handle(context):
                if event.type == StreamEventType.SESSION:
                    continue
                if event.type == StreamEventType.DONE:
                    pending_done_event = event
                    continue
                payload_event = await self._publish_live_event(execution, event)
                if payload_event.get("type") not in {"done", "session"}:
                    assistant_events.append(payload_event)
                if _should_capture_assistant_content(event):
                    call_id = (event.metadata or {}).get("call_id")
                    content_segments.append((str(call_id) if call_id else None, event.content))
                narration_call_id = _narration_marker_call_id(event)
                if narration_call_id:
                    narration_call_ids.add(narration_call_id)
                for attachment in _artifact_attachments(event):
                    if attachment["url"] not in seen_artifact_urls:
                        seen_artifact_urls.add(attachment["url"])
                        generated_attachments.append(attachment)

            # The persisted answer is the captured content minus any narration
            # rounds (their text stayed in the trace, never the answer).
            assistant_content = _persisted_answer()

            # Assistant continues the exact branch selected for this turn.
            if new_user_message_id is not None:
                assistant_message_id = await self.store.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    capability=capability_name,
                    events=assistant_events,
                    attachments=generated_attachments or None,
                    parent_message_id=new_user_message_id,
                )
            elif branch_parent_explicit:
                assistant_message_id = await self.store.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    capability=capability_name,
                    events=assistant_events,
                    attachments=generated_attachments or None,
                    parent_message_id=branch_parent_id,
                )
            else:  # pragma: no cover - normalized before orchestration
                raise RuntimeError("Turn is missing an explicit message parent.")
            await _accumulate_knowledge_diagram_from_answer(
                session_id=session_id,
                message_id=assistant_message_id,
                content=assistant_content,
            )
            await self._record_online_conversation(
                execution=execution,
                user_content=raw_user_content,
                user_message_id=new_user_message_id or regenerated_from_message_id,
                assistant_content=assistant_content,
                assistant_message_id=assistant_message_id,
                parent_message_id=branch_parent_id if branch_parent_explicit else None,
                subject_id=str(payload.get("subject_id") or "").strip() or None,
            )
            await self._flush_buffered_events(execution)
            await self.store.update_turn_status(turn_id, "completed")
            if pending_done_event is None:
                pending_done_event = StreamEvent(
                    type=StreamEventType.DONE,
                    source=capability_name,
                    metadata={"status": "completed"},
                )
            await self._publish_live_event(execution, pending_done_event)
            stream_done_sent = True
            if not is_regenerate:
                # Title generation is post-turn metadata. Keep it after DONE
                # so the composer and duration clock stop as soon as the
                # assistant answer is saved; the frontend keeps this socket
                # open briefly so the later ``session_meta`` title update can
                # still arrive.
                try:
                    await self._maybe_generate_session_title(
                        execution=execution,
                        session_id=session_id,
                        ui_language=str(payload.get("language", "en") or "en"),
                    )
                except Exception:
                    logger.debug("Failed to generate session title", exc_info=True)
        except asyncio.CancelledError:
            if not stream_done_sent:
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.ERROR,
                        source=capability_name,
                        content="Turn cancelled",
                        metadata={"turn_terminal": True, "status": "cancelled"},
                    ),
                )
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.DONE,
                        source=capability_name,
                        metadata={"status": "cancelled"},
                    ),
                )
            with contextlib.suppress(Exception):
                await self._flush_buffered_events(execution)
            # Best-effort: persist what the turn already produced (streamed
            # answer text, trace events, generated files) so cancelling a
            # turn does not erase visible work — files the model created are
            # on disk either way and must stay reachable. Shielded because
            # we are already unwinding a cancellation. Every step is
            # suppressed separately so the status update below always runs —
            # a turn left "running" gets mislabelled as a restart orphan.
            partial_content = _persisted_answer()
            if partial_content or generated_attachments or assistant_events:
                with contextlib.suppress(Exception):
                    partial_message_id = await asyncio.shield(
                        self.store.add_message(
                            session_id=session_id,
                            role="assistant",
                            content=partial_content,
                            capability=capability_name,
                            events=assistant_events,
                            attachments=generated_attachments or None,
                            parent_message_id=(
                                new_user_message_id
                                if new_user_message_id is not None
                                else branch_parent_id
                            ),
                        )
                    )
                    await asyncio.shield(
                        _accumulate_knowledge_diagram_from_answer(
                            session_id=session_id,
                            message_id=partial_message_id,
                            content=partial_content,
                        )
                    )
            with contextlib.suppress(Exception):
                await self.store.update_turn_status(turn_id, "cancelled", "Turn cancelled")
            raise
        except Exception as exc:
            if stream_done_sent:
                logger.error(
                    "Post-stream persistence for turn %s failed: %s",
                    turn_id,
                    exc,
                    exc_info=True,
                )
                # Suppress each step separately: a flush failure must not
                # also skip the status update, or the turn stays "running"
                # forever and gets mislabelled as a server-restart orphan.
                with contextlib.suppress(Exception):
                    await self._flush_buffered_events(execution)
                with contextlib.suppress(Exception):
                    await self.store.update_turn_status(turn_id, "failed", str(exc))
            else:
                logger.error("Turn %s failed: %s", turn_id, exc, exc_info=True)
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.ERROR,
                        source=capability_name,
                        content=str(exc),
                        metadata={"turn_terminal": True, "status": "failed"},
                    ),
                )
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.DONE,
                        source=capability_name,
                        metadata={"status": "failed"},
                    ),
                )
                with contextlib.suppress(Exception):
                    await self._flush_buffered_events(execution)
                # A terminal error is still a user-visible response.  Record
                # an explicit, content-free degradation only when the user
                # message already has a server-issued ID; a failure before
                # that point has no safe immutable turn pair to publish.
                source_user_message_id = new_user_message_id or regenerated_from_message_id
                if source_user_message_id is not None:
                    with contextlib.suppress(Exception):
                        await self._record_online_conversation(
                            execution=execution,
                            user_content=str(payload.get("content") or ""),
                            user_message_id=source_user_message_id,
                            assistant_content=(
                                assistant_content
                                if assistant_message_id is not None and assistant_content.strip()
                                else "I couldn't complete that response. Please try again."
                            ),
                            assistant_message_id=assistant_message_id,
                            parent_message_id=None,
                            subject_id=str(payload.get("subject_id") or "").strip() or None,
                            assistant_degraded=True,
                        )
                await self.store.update_turn_status(turn_id, "failed", str(exc))
        finally:
            if llm_scope_token is not None and reset_active_llm_selection is not None:
                reset_active_llm_selection(llm_scope_token)
            # Drop the reply queue first — any in-flight ``submit_user_reply``
            # that finds the queue gone will return ``False`` rather than
            # accumulating on a dead turn.
            self._reply_queues.pop(turn_id, None)
            async with self._lock:
                current = self._executions.get(turn_id)
                if current is not None:
                    for subscriber in current.subscribers:
                        with contextlib.suppress(asyncio.QueueFull):
                            subscriber.queue.put_nowait(None)
                    self._executions.pop(turn_id, None)

    async def _record_online_conversation(
        self,
        *,
        execution: _TurnExecution,
        user_content: str,
        user_message_id: int | None,
        assistant_content: str,
        assistant_message_id: int | None,
        parent_message_id: int | None,
        subject_id: str | None,
        assistant_degraded: bool = False,
    ) -> None:
        """Mirror a terminal live response into immutable WS-5 conversation facts.

        This happens after the normal session message write, so a request that
        fails validation or is interrupted before a user-visible response
        cannot manufacture a canonical conversation.  Conversation storage is
        intentionally not a learning-evidence path: this method has no BKT,
        grading, Memory, or persona write side effect.
        """
        if user_message_id is None:
            return
        from traittutor.conversation import ConversationOnlineBridge
        from traittutor.multi_user.context import get_current_user

        session_title = "Conversation"
        with contextlib.suppress(Exception):
            session = await self.store.get_session(execution.session_id)
            if session is not None:
                session_title = str(session.get("title") or session_title)
        bridge = ConversationOnlineBridge(get_current_user().id)
        bridge.record_terminal_turn(
            session_id=execution.session_id,
            runtime_turn_id=execution.turn_id,
            session_title=session_title,
            user_content=user_content,
            user_message_id=user_message_id,
            assistant_content=assistant_content,
            assistant_message_id=assistant_message_id,
            parent_message_id=parent_message_id,
            subject_id=subject_id,
            assistant_degraded=assistant_degraded,
        )

    async def _publish_live_event(
        self,
        execution: _TurnExecution,
        event: StreamEvent,
    ) -> dict[str, Any]:
        await self._capture_and_project_quiz_event(execution, event)
        if event.type == StreamEventType.DONE and not event.metadata.get("status"):
            event.metadata = {**event.metadata, "status": "completed"}
        event.session_id = execution.session_id
        event.turn_id = execution.turn_id
        payload = event.to_dict()
        async with self._lock:
            current = self._executions.get(execution.turn_id, execution)
            seq = int(payload.get("seq") or 0)
            if seq <= 0:
                seq = current.next_seq
                current.next_seq += 1
                if current is not execution:
                    execution.next_seq = max(execution.next_seq, current.next_seq)
            else:
                current.next_seq = max(current.next_seq, seq + 1)
                execution.next_seq = max(execution.next_seq, seq + 1)
            payload["seq"] = seq
            current.events.append(payload)
            if current is not execution:
                execution.events.append(payload)
            subscribers = list(current.subscribers)
        for subscriber in subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                subscriber.queue.put_nowait(payload)
        return payload

    async def _capture_and_project_quiz_event(
        self,
        execution: _TurnExecution,
        event: StreamEvent,
    ) -> None:
        """Persist deep-question keys before emitting a learner-safe event.

        The old wire protocol carried ``correct_answer`` and ``explanation``
        inside streaming metadata, then let the browser decide correctness.
        Capture those fields in the SQLite-only private table first and expose
        just a question reference and renderable stem afterwards.  If the
        private capture is unavailable, omit the item entirely: a client must
        never fall back to a browser-supplied answer key.
        """
        if execution.capability != "deep_question":
            return
        private_items = _private_quiz_items_from_event(event)
        if not private_items:
            return
        capturable: list[dict[str, Any]] = []
        for raw_item in private_items:
            if (
                not isinstance(raw_item, dict)
                or not str(raw_item.get("question_id") or "").strip()
                or not str(raw_item.get("question") or "").strip()
                or not str(raw_item.get("correct_answer") or "").strip()
            ):
                continue
            # Agent output is never a state-writing authority. In particular,
            # discard a model-emitted path ID and recover a link only from the
            # explicit, server-validated turn context below.
            item = dict(raw_item)
            item.pop("learning_path_id", None)
            learning_path_id = self._verified_quiz_learning_path_id(execution, item)
            if learning_path_id:
                item["learning_path_id"] = learning_path_id
            capturable.append(item)
        if not capturable:
            event.content = ""
            event.metadata = {}
            return
        save_items = getattr(self.store, "upsert_server_quiz_items", None)
        if save_items is None:
            logger.error("deep_question quiz item store is unavailable; withholding quiz event")
            event.content = ""
            event.metadata = {}
            return
        try:
            saved = await save_items(execution.session_id, execution.turn_id, capturable)
        except Exception:
            logger.exception("Failed to persist server-held quiz items; withholding quiz event")
            event.content = ""
            event.metadata = {}
            return
        saved_ids = {str(item.get("question_id") or "").strip() for item in capturable}
        if saved != len(capturable) or not saved_ids:
            event.content = ""
            event.metadata = {}
            return

        metadata = dict(event.metadata or {})
        raw_pair = metadata.get("qa_pair")
        if isinstance(raw_pair, dict):
            safe = _learner_safe_quiz_item(raw_pair)
            if safe is None or safe["question_id"] not in saved_ids:
                metadata.pop("qa_pair", None)
            else:
                metadata["qa_pair"] = safe
            # Question markdown from the pipeline contains the reference
            # answer/explanation. QuizViewer renders the safe structured item.
            event.content = ""

        raw_summary = metadata.get("summary")
        if isinstance(raw_summary, dict):
            # ``templates`` can carry source/exam reference answers.  Keep
            # only terminal counters and safe rendered question records.
            summary = {
                key: raw_summary[key]
                for key in (
                    "success",
                    "source",
                    "requested",
                    "template_count",
                    "completed",
                    "failed",
                )
                if key in raw_summary
            }
            safe_results: list[dict[str, Any]] = []
            for result in raw_summary.get("results") or []:
                if not isinstance(result, dict):
                    continue
                raw = result.get("qa_pair", result)
                if not isinstance(raw, dict):
                    continue
                safe = _learner_safe_quiz_item(raw)
                if safe is None or safe["question_id"] not in saved_ids:
                    continue
                result_meta = result.get("metadata")
                safe_results.append(
                    {
                        "qa_pair": safe,
                        # Keep only benign status metadata. The original
                        # generation metadata can include provider traces.
                        "metadata": {"error": bool(result_meta.get("error"))}
                        if isinstance(result_meta, dict) and result_meta.get("error")
                        else {},
                    }
                )
            summary["results"] = safe_results
            metadata["summary"] = summary
            # Mimic-mode ``response`` is rendered from the raw question
            # markdown and therefore includes answer keys. The structured
            # safe records above are the only quiz payload the browser needs.
            metadata.pop("response", None)
        event.metadata = metadata

    @staticmethod
    def _verified_quiz_learning_path_id(
        execution: _TurnExecution,
        item: dict[str, Any],
    ) -> str:
        """Resolve the optional path association without trusting agent output.

        A deep-question session normally has no learning-path provenance, so
        it should later remain attribution-pending. The only supported link is
        an explicit Learning Pack id in the server's already-validated turn
        context, and it must resolve to a current-user ``LearningProgress``
        with the exact subject/KC before it is persisted privately.
        """
        config = execution.payload.get("config")
        candidate = (
            str(config.get("learning_pack_id") or "").strip() if isinstance(config, dict) else ""
        )
        subject_id = str(item.get("subject_id") or "").strip()
        kc_id = str(item.get("kc_id") or item.get("node_id") or "").strip()
        if not candidate or not subject_id or not kc_id:
            return ""
        try:
            from traittutor.learning.service import LearningService
            from traittutor.multi_user.context import get_current_user

            service = LearningService(resume_canonical_derivations=False)
            if service.has_existing_canonical_target(
                user_id=get_current_user().id,
                subject_id=subject_id,
                kc_id=kc_id,
                learning_path_id=candidate,
            ):
                return candidate
        except Exception:
            logger.warning("Could not resolve server quiz learning-path provenance", exc_info=True)
        return ""

    async def _maybe_generate_session_title(
        self,
        *,
        execution: _TurnExecution,
        session_id: str,
        ui_language: str,
    ) -> None:
        """Generate a short LLM-written title for a freshly-named session.

        Runs only when the session still carries the ``New conversation``
        sentinel — once a user manually renames the chat (or this method
        has already filled in a title), it short-circuits. Uses the LLM
        scope already active on the calling task, which is the user's
        currently selected model.
        """
        if not session_id:
            return
        session = await self.store.get_session(session_id)
        if not session:
            return
        current_title = str(session.get("title") or "").strip()
        if current_title and current_title != "New conversation":
            return

        messages = await self.store.get_messages(session_id)
        first_user = ""
        first_assistant = ""
        for m in messages:
            role = str(m.get("role") or "")
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            if role == "user" and not first_user:
                first_user = content
            elif role == "assistant" and not first_assistant:
                first_assistant = content
            if first_user and first_assistant:
                break
        if not first_user or not first_assistant:
            return

        title = ""
        try:
            zh = str(ui_language or "").lower().startswith("zh")
            if zh:
                sys_prompt = (
                    "你需要为一段对话生成一个简洁的标题。"
                    "直接输出标题文本，不要引号、不要 Markdown 格式、"
                    '不要末尾标点、不要 "标题：" 这类前缀。'
                    "标题控制在 4-10 个汉字以内。"
                )
                user_prompt = (
                    "请基于以下对话生成标题：\n\n"
                    f"[用户]\n{_clip_text(first_user, 800)}\n\n"
                    f"[助手]\n{_clip_text(first_assistant, 1500)}"
                )
            else:
                sys_prompt = (
                    "You generate a concise, descriptive title for a "
                    "conversation. Output only the title as plain text "
                    "— no quotes, no markdown, no trailing punctuation, "
                    'no "Title:" prefix. Keep it 4-8 words.'
                )
                user_prompt = (
                    "Generate a title for this conversation:\n\n"
                    f"[User]\n{_clip_text(first_user, 800)}\n\n"
                    f"[Assistant]\n{_clip_text(first_assistant, 1500)}"
                )

            from traittutor.gateway import GatewayMessage, GatewayRequest, get_gateway

            request = GatewayRequest(
                prompt=user_prompt,
                system_prompt=sys_prompt,
                purpose="session:title",
                messages=(
                    GatewayMessage(role="system", content=sys_prompt),
                    GatewayMessage(role="user", content=user_prompt),
                ),
                temperature=0.3,
                max_tokens=80,
                timeout_seconds=20.0,
            )
            chunks: list[str] = []
            async for event in get_gateway().stream(request):
                if event.type == "text" and event.text:
                    chunks.append(event.text)
            raw_title = "".join(chunks)
            title = _sanitize_session_title(raw_title)
        except asyncio.TimeoutError:
            logger.debug("Title LLM call timed out — falling back")
        except Exception:
            logger.debug("Title LLM call failed", exc_info=True)

        if not title:
            # Fallback: truncate the first user message so the sidebar
            # doesn't sit on "New conversation" indefinitely when the
            # title model errors out.
            title = first_user[:50] + ("..." if len(first_user) > 50 else "")

        if not title:
            return

        try:
            await self.store.update_session_title(session_id, title)
        except Exception:
            logger.debug("update_session_title failed", exc_info=True)
            return

        await self._publish_live_event(
            execution,
            StreamEvent(
                type=StreamEventType.SESSION_META,
                source="turn_runtime",
                stage="title",
                content=title,
                metadata={"title": title, "session_id": session_id},
            ),
        )

    async def _flush_buffered_events(self, execution: _TurnExecution) -> None:
        """Persist buffered turn events after the live stream has already drained."""
        async with self._lock:
            if execution.events_flushed:
                return
            execution.events_flushed = True
            events = list(execution.events)

        for payload in events:
            try:
                await self.store.append_turn_event(execution.turn_id, payload)
            except ValueError as exc:
                # A turn can disappear when the session is deleted while the turn task
                # is draining post-stream persistence. Avoid cascading failures.
                if "Turn not found:" not in str(exc):
                    raise
                logger.warning(
                    "Skip persisting event for missing turn %s (%s)",
                    execution.turn_id,
                    payload.get("type", ""),
                )
                continue


import threading

_runtime_lock = threading.Lock()
_runtime_instances: dict[str, TurnRuntimeManager] = {}


def get_turn_runtime_manager() -> TurnRuntimeManager:
    from traittutor.services.session import get_session_store

    store = get_session_store()
    key = str(getattr(store, "db_path", id(store)))
    with _runtime_lock:
        if key not in _runtime_instances:
            _runtime_instances[key] = TurnRuntimeManager(store=store)
        return _runtime_instances[key]


__all__ = ["TurnRuntimeManager", "get_turn_runtime_manager"]
