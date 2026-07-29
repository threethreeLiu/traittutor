#!/usr/bin/env python
"""
Single-call follow-up agent for quiz question threads.
"""

from __future__ import annotations

from typing import Any

from traittutor.agents.base_agent import BaseAgent
from traittutor.core.trace import build_trace_metadata, new_call_id
from traittutor.services.prompt.language import append_language_directive


class FollowupAgent(BaseAgent):
    """Answer follow-up questions about a single quiz item in one LLM call."""

    def __init__(self, language: str = "en", **kwargs: Any) -> None:
        super().__init__(
            module_name="question",
            agent_name="followup_agent",
            language=language,
            **kwargs,
        )

    async def process(
        self,
        *,
        user_message: str,
        question_context: dict[str, Any],
        history_context: str = "",
        attachments: list[Any] | None = None,
    ) -> str:
        system_prompt = append_language_directive(
            self.get_prompt("system", ""),
            self.language,
        )
        user_prompt_template = self.get_prompt("answer_followup", "")
        if not user_prompt_template:
            user_prompt_template = (
                "Question context:\n{question_context}\n\n"
                "Conversation history:\n{history_context}\n\n"
                "User follow-up:\n{user_message}\n"
            )

        user_prompt = user_prompt_template.format(
            question_context=self._render_question_context(question_context),
            history_context=history_context or "(none)",
            user_message=user_message.strip() or "(empty)",
        )

        _chunks: list[str] = []
        async for _c in self.stream_llm(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stage="followup_answer",
            attachments=attachments,
            trace_meta=build_trace_metadata(
                call_id=new_call_id(
                    f"quiz-followup-{question_context.get('question_id', 'question')}"
                ),
                phase="generation",
                label=f"Answer follow-up for {self._humanize_question_id(question_context.get('question_id', 'question'))}",
                call_kind="llm_generation",
                trace_id=str(question_context.get("question_id", "question")),
                question_id=str(question_context.get("question_id", "")),
            ),
        ):
            _chunks.append(_c)
        return "".join(_chunks)

    @staticmethod
    def _humanize_question_id(question_id: Any) -> str:
        raw = str(question_id or "").strip()
        if raw.lower().startswith("q_") and raw[2:].isdigit():
            return f"Question {raw[2:]}"
        return raw or "question"

    @staticmethod
    def _render_question_context(question_context: dict[str, Any]) -> str:
        options = question_context.get("options") or {}
        option_lines: list[str] = []
        if isinstance(options, dict):
            for key, value in options.items():
                if str(value or "").strip():
                    option_lines.append(f"{key}. {value}")

        correctness = question_context.get("is_correct")
        correctness_text = (
            "correct" if correctness is True else "incorrect" if correctness is False else "unknown"
        )

        lines = [
            f"Question ID: {question_context.get('question_id') or '(none)'}",
            f"Question type: {question_context.get('question_type') or '(none)'}",
            f"Difficulty: {question_context.get('difficulty') or '(none)'}",
            f"Concentration: {question_context.get('concentration') or '(none)'}",
            "",
            "Question:",
            str(question_context.get("question", "") or "(none)"),
        ]
        if option_lines:
            lines.extend(["", "Options:", *option_lines])
        lines.extend(
            [
                "",
                f"Learner answer: {question_context.get('user_answer') or '(not provided)'}",
                f"Learner result: {correctness_text}",
                f"Reference answer: {question_context.get('correct_answer') or '(none)'}",
                "",
                "Explanation:",
                str(question_context.get("explanation", "") or "(none)"),
            ]
        )
        knowledge_context = str(question_context.get("knowledge_context", "") or "").strip()
        if knowledge_context:
            lines.extend(["", "Knowledge context:", knowledge_context])
        return "\n".join(lines).strip()
