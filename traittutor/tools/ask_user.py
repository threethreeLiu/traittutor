"""Build the payload for the ``ask_user`` tool.

The tool packages one-to-four structured questions into a payload that
the chat pipeline interprets as a "pause this same turn until the user
answers" signal (``ToolResult.pause_for_user``). The frontend reads the
same payload off ``tool_result.metadata.ask_user`` and renders a card
that lets the user move between questions and submit answers in one
batch.

The schema is intentionally a list-of-questions even for the common
single-question case — every call wraps a list so the frontend has one
code path. Each option is a ``{label, description}`` pair (mirroring
Claude Code's ``AskUserQuestion``): the label is the short clickable
choice, and the description explains what picking it means. Retired aliases
and plain-string options are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_QUESTIONS = 4
MAX_OPTIONS = 8
MAX_OPTION_CHARS = 120  # option label
MAX_OPTION_DESC_CHARS = 200
MAX_HEADER_CHARS = 16
MAX_QUESTION_CHARS = 800
MAX_INTRO_CHARS = 400
MAX_PLACEHOLDER_CHARS = 120

# Labels the model sometimes adds as its own catch-all option. The card
# already renders a free-form "Other" row whenever ``allow_free_text``
# is on, so a model-supplied duplicate is dropped (exact match only —
# being clever here risks eating legitimate options).
_REDUNDANT_OTHER_LABELS = frozenset({"other", "其他", "其它"})


@dataclass(frozen=True)
class AskUserOption:
    """One clickable choice: short label + optional explanation."""

    label: str
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "description": self.description}


@dataclass(frozen=True)
class AskUserQuestion:
    """A single question rendered as one tab on the ask_user card."""

    id: str
    prompt: str
    options: tuple[AskUserOption, ...] = ()
    header: str | None = None
    multi_select: bool = False
    allow_free_text: bool = True
    placeholder: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "header": self.header,
            "multi_select": self.multi_select,
            "options": [o.to_dict() for o in self.options],
            "allow_free_text": self.allow_free_text,
            "placeholder": self.placeholder,
        }


@dataclass(frozen=True)
class AskUserPayload:
    """Structured payload that travels alongside the tool result.

    Mirrored on the frontend by ``AskUserOptions.tsx`` which reads the
    same field names off ``tool_result.metadata.ask_user``.
    """

    questions: tuple[AskUserQuestion, ...]
    intro: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intro": self.intro,
            "questions": [q.to_dict() for q in self.questions],
        }

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(q.id for q in self.questions)


def build_ask_user_payload(
    *,
    questions: Any = None,
    intro: Any = None,
) -> tuple[AskUserPayload | None, str | None]:
    """Validate + normalise the LLM-provided arguments.

    Accepts only ``{questions: [...], intro?}``. Returns ``(payload, None)`` on success, or
    ``(None, error_message)`` if arguments cannot be honoured — errors
    propagate back to the LLM as a tool failure rather than raising.
    """
    raw_questions = _coerce_questions(questions)
    if isinstance(raw_questions, str):
        return None, raw_questions
    if not raw_questions:
        return None, "`questions` must contain at least one question."
    if len(raw_questions) > MAX_QUESTIONS:
        raw_questions = raw_questions[:MAX_QUESTIONS]

    normalised: list[AskUserQuestion] = []
    used_ids: set[str] = set()
    for idx, raw in enumerate(raw_questions):
        q_or_err = _build_question(raw, idx, used_ids)
        if isinstance(q_or_err, str):
            return None, q_or_err
        normalised.append(q_or_err)
        used_ids.add(q_or_err.id)

    intro_text: str | None = None
    if intro is not None:
        intro_text = _coerce_string(intro).strip() or None
        if intro_text and len(intro_text) > MAX_INTRO_CHARS:
            intro_text = intro_text[:MAX_INTRO_CHARS].rstrip() + "…"

    return AskUserPayload(questions=tuple(normalised), intro=intro_text), None


def _coerce_questions(questions: Any) -> list[Any] | str:
    if not isinstance(questions, list):
        return "`questions` must be an array."
    return questions


def _build_question(raw: Any, idx: int, used_ids: set[str]) -> AskUserQuestion | str:
    if not isinstance(raw, dict):
        return f"Question #{idx + 1} must be an object."
    if "question" in raw or "multiSelect" in raw:
        return f"Question #{idx + 1} uses a retired field."

    prompt_raw = raw.get("prompt")
    prompt = _coerce_string(prompt_raw).strip()
    if not prompt:
        return f"Question #{idx + 1}: `prompt` must be a non-empty string."
    if len(prompt) > MAX_QUESTION_CHARS:
        prompt = prompt[:MAX_QUESTION_CHARS].rstrip() + "…"

    allow_free_text = raw.get("allow_free_text")
    allow_free_text = True if allow_free_text is None else bool(allow_free_text)

    options_raw = raw.get("options")
    options: tuple[AskUserOption, ...] = ()
    if options_raw is not None:
        if not isinstance(options_raw, (list, tuple)):
            return f"Question #{idx + 1}: `options` must be an array."
        cleaned: list[AskUserOption] = []
        seen_labels: set[str] = set()
        for opt in options_raw:
            normalised = _build_option(opt)
            if normalised is None:
                return f"Question #{idx + 1}: every option must be an object with a label."
            # The card auto-renders an "Other" free-text row; drop a
            # model-supplied duplicate so the user never sees two.
            if allow_free_text and normalised.label.lower() in _REDUNDANT_OTHER_LABELS:
                continue
            if normalised.label in seen_labels:
                continue
            seen_labels.add(normalised.label)
            cleaned.append(normalised)
            if len(cleaned) >= MAX_OPTIONS:
                break
        options = tuple(cleaned)

    multi_select_raw = raw.get("multi_select")
    multi_select = bool(multi_select_raw)

    header_raw = raw.get("header")
    header: str | None = None
    if header_raw is not None:
        header = _coerce_string(header_raw).strip() or None
        if header and len(header) > MAX_HEADER_CHARS:
            header = header[:MAX_HEADER_CHARS].rstrip()

    placeholder_raw = raw.get("placeholder")
    placeholder: str | None = None
    if placeholder_raw is not None:
        placeholder = _coerce_string(placeholder_raw).strip() or None
        if placeholder and len(placeholder) > MAX_PLACEHOLDER_CHARS:
            placeholder = placeholder[:MAX_PLACEHOLDER_CHARS].rstrip() + "…"

    qid = _coerce_string(raw.get("id")).strip()
    if not qid:
        qid = f"q{idx + 1}"
    # Disambiguate duplicate ids deterministically rather than rejecting.
    if qid in used_ids:
        suffix = 2
        while f"{qid}_{suffix}" in used_ids:
            suffix += 1
        qid = f"{qid}_{suffix}"

    return AskUserQuestion(
        id=qid,
        prompt=prompt,
        options=options,
        header=header,
        multi_select=multi_select,
        allow_free_text=allow_free_text,
        placeholder=placeholder,
    )


def _build_option(raw: Any) -> AskUserOption | None:
    """Validate one canonical ``{label, description?}`` option."""
    if not isinstance(raw, dict):
        return None
    label = _coerce_string(raw.get("label")).strip()
    description = _coerce_string(raw.get("description")).strip() or None
    if not label:
        return None
    if len(label) > MAX_OPTION_CHARS:
        label = label[:MAX_OPTION_CHARS].rstrip() + "…"
    if description and len(description) > MAX_OPTION_DESC_CHARS:
        description = description[:MAX_OPTION_DESC_CHARS].rstrip() + "…"
    return AskUserOption(label=label, description=description)


def _coerce_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


__all__ = [
    "AskUserOption",
    "AskUserPayload",
    "AskUserQuestion",
    "MAX_HEADER_CHARS",
    "MAX_INTRO_CHARS",
    "MAX_OPTION_CHARS",
    "MAX_OPTION_DESC_CHARS",
    "MAX_OPTIONS",
    "MAX_PLACEHOLDER_CHARS",
    "MAX_QUESTION_CHARS",
    "MAX_QUESTIONS",
    "build_ask_user_payload",
]
