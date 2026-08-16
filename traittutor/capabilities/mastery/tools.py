"""Mastery Path tools — the seam between the chat-loop tutor and the pure
mastery engine (:mod:`traittutor.learning`).

These six tools are auto-mounted only when a mastery path is active on the
turn (via the chat loop mastery capability). The chat agent loop IS the tutor;
these tools let it read the gate and record outcomes, while the pedagogy —
what to teach, how to question, when to explain — stays the model's job. The
arithmetic (mastery, gate, spaced repetition) stays in the engine.

The pipeline injects a server-derived ``_mastery_binding`` containing an
owner/path/subject/KC-graph fence; the model never supplies it. Each call
revalidates that binding against durable state before constructing a fresh
store + service, so concurrent turns cannot race on a shared object or train
against a guessed subject.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
import uuid

from traittutor.capabilities.mastery.choices import (
    format_options,
    has_option_bodies,
    parse_options,
    recover_options_from_turn,
    resolve_answer,
)
from traittutor.core.tool_protocol import BaseTool, ToolDefinition, ToolParameter, ToolResult

# ``learning.models`` and ``learning.policy`` only depend on pydantic — safe to
# import at module load. ``learning.service`` / ``storage`` / ``scheduler``
# reach the path service (and so the runtime + tool registry), so importing
# them here would close an import cycle through the built-in registry. They
# are imported lazily inside the call paths instead (same pattern as the other
# builtin tools).
from traittutor.learning.models import (
    PendingQuestion,
)
from traittutor.learning.policy import (
    QUALITATIVE_TYPES,
    find_knowledge_point,
    is_mastered,
    map_summary,
    next_objective,
)

if TYPE_CHECKING:
    from traittutor.capabilities.mastery.binding import MasteryPathBinding
    from traittutor.learning.service import LearningService

# Tool names the pipeline mounts together when a mastery path is active. Kept
# here so the mount policy and the registration list can't disagree.
MASTERY_TOOL_NAMES: tuple[str, ...] = (
    "mastery_status",
    "mastery_quiz",
    "mastery_grade",
    "mastery_assess",
    "mastery_resume",
    "mastery_build",
)

_QUESTION_TYPES = ("choice", "short", "open")
logger = logging.getLogger(__name__)


def _new_service() -> LearningService:
    from traittutor.learning.service import LearningService
    from traittutor.learning.storage import LearningStore

    return LearningService(LearningStore())


def _resolve_bound_progress(
    kwargs: dict[str, Any],
) -> tuple[MasteryPathBinding, Any] | None:
    """Return the current owner's revalidated path, or fail closed.

    Private kwargs may be supplied only by :class:`MasteryLoopCapability`, but
    this second validation is still required: a tool call can occur after an
    asynchronous map replacement and must not grade a now-different KC graph.
    """
    # Keep this import inside the execution boundary.  Tool registration runs
    # while PathService imports the runtime registry; importing LearningStore
    # during that bootstrap would form a cycle before any mastery turn exists.
    from traittutor.capabilities.mastery.binding import load_bound_mastery_progress

    return load_bound_mastery_progress(kwargs.get("_mastery_binding"))


def _resolve_session_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("_session_id") or "").strip()


def _resolve_turn_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("_turn_id") or "").strip()


def _question_bank_type(question_type: str) -> str:
    qtype = str(question_type or "").strip().lower()
    if qtype == "choice":
        return "choice"
    if qtype == "open":
        return "written"
    return "short_answer"


async def _resolve_pending_choice(
    pending: PendingQuestion, turn_id: str
) -> tuple[dict[str, str], str]:
    """Resolve a pending choice question's ``({label: body}, expected_label)``.

    Re-parses the bodies stored at registration; for legacy paths that stored
    only ``["A", "B", ...]`` it recovers the real bodies from the turn's
    ``ask_user`` event. The expected answer is normalised to a stable label
    when it resolves, else left as registered.
    """
    options = parse_options(list(pending.options or []))
    if not has_option_bodies(options):
        try:
            from traittutor.services.session import get_sqlite_session_store

            options = await recover_options_from_turn(
                get_sqlite_session_store(), turn_id, pending.prompt
            )
        except Exception:
            logger.warning("Failed to recover legacy mastery choice options", exc_info=True)
            options = {}
    return options, resolve_answer(pending.expected_answer, options) or pending.expected_answer


async def _sync_mastery_attempt_to_question_bank(
    *,
    session_id: str,
    turn_id: str,
    pending: PendingQuestion,
    user_answer: str,
    is_correct: bool,
    choice_options: dict[str, str] | None = None,
    correct_answer: str | None = None,
) -> None:
    if not session_id:
        return
    item = {
        "turn_id": turn_id,
        "question_id": pending.question_id,
        "question": pending.prompt,
        "question_type": _question_bank_type(pending.question_type),
        "options": choice_options or parse_options(list(pending.options or [])),
        "correct_answer": correct_answer or pending.expected_answer,
        "explanation": "",
        "difficulty": "",
        "user_answer": user_answer,
        "is_correct": is_correct,
    }
    try:
        from traittutor.services.session import get_sqlite_session_store

        await get_sqlite_session_store().upsert_notebook_entries(session_id, [item])
    except Exception:
        logger.warning(
            "Failed to sync mastery question %s to question bank for session %s",
            pending.question_id,
            session_id,
            exc_info=True,
        )


def _json_result(payload: dict[str, Any], *, meta_key: str, success: bool = True) -> ToolResult:
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False),
        success=success,
        metadata={meta_key: payload},
    )


def _unbound_result(*, action: str = "use mastery tools") -> ToolResult:
    return ToolResult(
        content=(
            "No verified owner/path/subject binding is active; cannot "
            f"{action}. No learner event or BKT update was recorded."
        ),
        success=False,
    )


class MasteryStatusTool(BaseTool):
    """Read the current objective + map snapshot. Call FIRST every turn."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="mastery_status",
            description=(
                "Read the learner's mastery path: the next objective to work on "
                "(decided by a hard mastery gate), any question awaiting an "
                "answer, due reviews, and a map of every objective's status "
                "(new / learning / mastered). Call this FIRST on every mastery "
                "turn — it tells you what to do; never guess the next objective."
            ),
            parameters=[],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        resolved = _resolve_bound_progress(kwargs)
        if resolved is None:
            # The canonical flag must never fall back to an unbound legacy
            # map: no subject means an honest unknown, not a fabricated 0%.
            return _json_result(
                {
                    "status": "unknown",
                    "reason": "missing_or_stale_subject_binding",
                    "next": {"action": "probe", "knowledge_point_id": ""},
                    "map": None,
                },
                meta_key="mastery_status",
                success=False,
            )
        binding, progress = resolved
        service = _new_service()
        if not any(module.knowledge_points for module in progress.modules):
            return _json_result(
                {
                    "status": "empty",
                    "message": (
                        "No mastery path has been built yet. Design one from the "
                        "learner's materials and call mastery_build."
                    ),
                },
                meta_key="mastery_status",
            )
        mastery_read_view = service.mastery_read_view(progress, user_id=binding.owner_id)
        payload = {
            "status": "active",
            "next": next_objective(
                progress,
                mastery_read_view=mastery_read_view,
            ).to_dict(),
            "map": map_summary(
                progress,
                mastery_read_view=mastery_read_view,
            ),
        }
        return _json_result(payload, meta_key="mastery_status")


class MasteryQuizTool(BaseTool):
    """Register an objective-type question; the engine holds the answer."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="mastery_quiz",
            description=(
                "Pose a question for a MEMORY or PROCEDURE objective and register "
                "its expected answer with the engine (so grading is deterministic "
                "and you never re-state the answer later). After calling this, "
                "present the question with the ask_user tool so the learner answers "
                "on an interactive card (for choices, give ask_user options short "
                "labels like A/B/C, pass every full option body here, and set the "
                "correct label as expected_answer); "
                "then call mastery_grade with their answer. For CONCEPT / DESIGN "
                "objectives use mastery_assess instead."
            ),
            parameters=[
                ToolParameter(
                    name="knowledge_point_id",
                    type="string",
                    description="Objective id from mastery_status (verbatim).",
                ),
                ToolParameter(
                    name="question",
                    type="string",
                    description="The question text shown to the learner.",
                ),
                ToolParameter(
                    name="expected_answer",
                    type="string",
                    description="The correct answer, used only server-side for grading.",
                ),
                ToolParameter(
                    name="question_type",
                    type="string",
                    description=(
                        "'choice' (exact match), 'short' (exact / fuzzy for ≤30 "
                        "chars), or 'open' (keyword overlap). Default 'short'."
                    ),
                    required=False,
                    default="short",
                    enum=list(_QUESTION_TYPES),
                ),
                ToolParameter(
                    name="options",
                    type="array",
                    description=(
                        "For question_type='choice', every full option in label order, "
                        "for example ['A: first answer', 'B: second answer']. Never "
                        "pass bare labels such as ['A', 'B', 'C', 'D']. Use the same "
                        "bodies as the ask_user option descriptions."
                    ),
                    required=False,
                    items={"type": "string"},
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        resolved = _resolve_bound_progress(kwargs)
        if resolved is None:
            return _unbound_result(action="register a mastery question")
        _binding, progress = resolved
        kp_id = str(kwargs.get("knowledge_point_id") or "").strip()
        question = str(kwargs.get("question") or "").strip()
        expected = str(kwargs.get("expected_answer") or "").strip()
        if not kp_id or not question or not expected:
            return ToolResult(
                content="mastery_quiz needs knowledge_point_id, question, and expected_answer.",
                success=False,
            )
        q_type = str(kwargs.get("question_type") or "short").strip().lower()
        if q_type not in _QUESTION_TYPES:
            q_type = "short"
        options = [str(o) for o in (kwargs.get("options") or []) if str(o).strip()]
        if q_type == "choice":
            choice_options = parse_options(options)
            if not has_option_bodies(choice_options):
                return ToolResult(
                    content=(
                        "Choice questions need full option bodies in mastery_quiz.options "
                        "(for example ['A: first answer', 'B: second answer']), not only "
                        "the labels A/B/C/D. Retry mastery_quiz with the exact option "
                        "descriptions you will show through ask_user."
                    ),
                    success=False,
                )
            resolved_expected = resolve_answer(expected, choice_options)
            if not resolved_expected:
                return ToolResult(
                    content=(
                        "Choice expected_answer must be an option label such as A/B/C/D, "
                        "or uniquely match one full option body. Retry mastery_quiz with "
                        "the correct label."
                    ),
                    success=False,
                )
            expected = resolved_expected
            options = format_options(choice_options)

        service = _new_service()
        kp, module_id, _ = find_knowledge_point(progress, kp_id)
        if kp is None:
            return ToolResult(
                content=f"Unknown objective {kp_id!r}; call mastery_status for valid ids.",
                success=False,
            )
        if kp_id in progress.deferred_knowledge_points:
            return ToolResult(
                content=(
                    f"Objective {kp.name!r} is temporarily paused after two failures. "
                    "Follow mastery_status.next instead; use mastery_resume only after "
                    "the learner explicitly asks to continue this paused objective."
                ),
                success=False,
            )
        pending = PendingQuestion(
            question_id=uuid.uuid4().hex,
            knowledge_point_id=kp_id,
            module_id=module_id,
            prompt=question,
            question_type=q_type,
            expected_answer=expected,
            options=options,
        )
        service.set_pending_question(progress, pending)
        return _json_result(
            {
                "status": "registered",
                "knowledge_point_id": kp_id,
                "question": question,
                "options": options,
                "instruction": (
                    "Present this question with the ask_user tool (use its options "
                    "for multiple choice; the option labels must match the "
                    "expected_answer you registered), then call mastery_grade with "
                    "the learner's answer."
                ),
            },
            meta_key="mastery_quiz",
        )


class MasteryGradeTool(BaseTool):
    """Grade the learner's answer to the pending question (deterministic)."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="mastery_grade",
            description=(
                "Grade the learner's answer to the question you registered with "
                "mastery_quiz. Grading is deterministic against the stored "
                "expected answer; this updates mastery, advances spaced "
                "repetition, and tells you whether the objective's gate is now "
                "cleared. Then give the learner feedback."
            ),
            parameters=[
                ToolParameter(
                    name="answer",
                    type="string",
                    description="The learner's answer, verbatim.",
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        resolved = _resolve_bound_progress(kwargs)
        if resolved is None:
            return _unbound_result(action="grade a mastery answer")
        binding, progress = resolved
        from traittutor.learning.scheduler import SpacedRepetitionScheduler

        answer = str(kwargs.get("answer") or "")
        service = _new_service()
        scheduler = SpacedRepetitionScheduler()
        pending = progress.pending_question
        if pending is None:
            return ToolResult(
                content="No question is awaiting an answer. Pose one with mastery_quiz first.",
                success=False,
            )
        choice_options: dict[str, str] = {}
        expected_answer = pending.expected_answer
        if pending.question_type == "choice":
            choice_options, expected_answer = await _resolve_pending_choice(
                pending, _resolve_turn_id(kwargs)
            )

        is_correct = service.grade_and_record(
            progress,
            question_id=pending.question_id,
            knowledge_point_id=pending.knowledge_point_id,
            module_id=pending.module_id,
            user_answer=answer,
            expected_answer=expected_answer,
            question_type=pending.question_type,
            scheduler=scheduler,
            user_id=binding.owner_id,
            subject_id=binding.subject_id,
            attempt_id=pending.attempt_id,
        )
        await _sync_mastery_attempt_to_question_bank(
            session_id=_resolve_session_id(kwargs),
            turn_id=_resolve_turn_id(kwargs),
            pending=pending,
            user_answer=answer,
            is_correct=is_correct,
            choice_options=choice_options,
            correct_answer=expected_answer,
        )
        service.clear_pending_question(progress)
        kp, _, _ = find_knowledge_point(progress, pending.knowledge_point_id)
        mastery_read_view = service.mastery_read_view(progress, user_id=binding.owner_id)
        mastered = bool(
            kp
            and is_mastered(
                progress,
                kp,
                mastery_read_view=mastery_read_view,
            )
        )
        evidence = mastery_read_view.read(kp.id) if kp and mastery_read_view is not None else None
        payload = {
            "is_correct": is_correct,
            "knowledge_point_id": pending.knowledge_point_id,
            "mastered": mastered,
            "evidence_state": (
                evidence.evidence_state if evidence is not None else "insufficient_evidence"
            ),
            "change_signal": evidence.change_signal if evidence is not None else "none",
            "verified_observation_count": (
                evidence.verified_observation_count if evidence is not None else 0
            ),
            "model_version": evidence.model_version if evidence is not None else None,
            "stage_policy_version": (
                evidence.stage_policy_version if evidence is not None else "bkt-stage-policy-v1"
            ),
            "next": next_objective(
                progress,
                mastery_read_view=mastery_read_view,
            ).to_dict(),
        }
        return _json_result(payload, meta_key="mastery_grade")


class MasteryAssessTool(BaseTool):
    """Record the qualitative (CONCEPT / DESIGN) gate from a Feynman check."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="mastery_assess",
            description=(
                "Record your judgement of a CONCEPT or DESIGN objective after the "
                "learner explains it in their own words (a Feynman-style check). "
                "Pass passed=true only when the explanation is correct and "
                "complete enough to count as mastery — this is the gate for these "
                "objective types. For MEMORY / PROCEDURE objectives use "
                "mastery_quiz + mastery_grade instead."
            ),
            parameters=[
                ToolParameter(
                    name="knowledge_point_id",
                    type="string",
                    description="Objective id from mastery_status (verbatim).",
                ),
                ToolParameter(
                    name="passed",
                    type="boolean",
                    description="True if the explanation demonstrates mastery.",
                ),
                ToolParameter(
                    name="feedback",
                    type="string",
                    description="Short note on what was strong or missing (stored as evidence).",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        resolved = _resolve_bound_progress(kwargs)
        if resolved is None:
            return _unbound_result(action="assess mastery")
        binding, progress = resolved
        kp_id = str(kwargs.get("knowledge_point_id") or "").strip()
        if not kp_id:
            return ToolResult(content="mastery_assess needs a knowledge_point_id.", success=False)
        passed = bool(kwargs.get("passed"))
        feedback = str(kwargs.get("feedback") or "").strip()

        service = _new_service()
        kp, _, _ = find_knowledge_point(progress, kp_id)
        if kp is None:
            return ToolResult(
                content=f"Unknown objective {kp_id!r}; call mastery_status for valid ids.",
                success=False,
            )
        if kp.type not in QUALITATIVE_TYPES:
            return ToolResult(
                content=(
                    f"Objective {kp.name!r} is a {kp.type.value} type — gate it with "
                    "mastery_quiz + mastery_grade, not mastery_assess."
                ),
                success=False,
            )
        if kp_id in progress.deferred_knowledge_points:
            return ToolResult(
                content=(
                    f"Objective {kp.name!r} is temporarily paused after two failures. "
                    "Do not reassess it until it is released by another objective's "
                    "trusted attempt or the learner explicitly requests mastery_resume."
                ),
                success=False,
            )
        service.record_qualitative(progress, kp_id, passed=passed, evidence=feedback)
        mastery_read_view = service.mastery_read_view(progress, user_id=binding.owner_id)
        payload = {
            "knowledge_point_id": kp_id,
            "passed": passed,
            "mastered": is_mastered(
                progress,
                kp,
                mastery_read_view=mastery_read_view,
            ),
            "evidence_state": "supported" if passed else "needs_support",
            "change_signal": "none",
            "next": next_objective(
                progress,
                mastery_read_view=mastery_read_view,
            ).to_dict(),
        }
        return _json_result(payload, meta_key="mastery_assess")


class MasteryResumeTool(BaseTool):
    """Release a single-objective recovery pause on explicit learner request."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="mastery_resume",
            description=(
                "Resume one temporarily paused objective without changing mastery or "
                "deleting evidence. Call this only when mastery_status returns "
                "recovery_pause and the learner explicitly asks to continue trying."
            ),
            parameters=[
                ToolParameter(
                    name="knowledge_point_id",
                    type="string",
                    description="Paused objective id from mastery_status (verbatim).",
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        resolved = _resolve_bound_progress(kwargs)
        if resolved is None:
            return _unbound_result(action="resume a paused mastery objective")
        binding, progress = resolved
        kp_id = str(kwargs.get("knowledge_point_id") or "").strip()
        kp, _, _ = find_knowledge_point(progress, kp_id)
        if kp is None:
            return ToolResult(
                content=f"Unknown objective {kp_id!r}; call mastery_status for valid ids.",
                success=False,
            )
        service = _new_service()
        if not service.resume_deferred_objective(progress, kp_id):
            return ToolResult(
                content=f"Objective {kp.name!r} is not paused; no state was changed.",
                success=False,
            )
        mastery_read_view = service.mastery_read_view(progress, user_id=binding.owner_id)
        return _json_result(
            {
                "status": "resumed",
                "knowledge_point_id": kp_id,
                "next": next_objective(
                    progress,
                    mastery_read_view=mastery_read_view,
                ).to_dict(),
            },
            meta_key="mastery_resume",
        )


class MasteryBuildTool(BaseTool):
    """Reject model-authored maps after a canonical path is bound."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="mastery_build",
            description=(
                "Unavailable for a verified mastery path. The path's existing "
                "server-authored module/KC graph is the canonical attribution "
                "contract; do not call this tool to create, append, or replace it."
            ),
            parameters=[],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        resolved = _resolve_bound_progress(kwargs)
        if resolved is None:
            return _unbound_result(action="build or replace a mastery map")
        # The bound module/KC graph is the attribution contract for canonical
        # BKT.  Letting the model replace it after binding would manufacture a
        # new KC namespace and make old evidence point at a different target.
        return ToolResult(
            content=(
                "This verified mastery path already owns its module/KC graph. "
                "mastery_build is disabled; update the learning path through its "
                "authoritative planning flow, then explicitly reselect it."
            ),
            success=False,
        )


MASTERY_TOOL_TYPES: tuple[type[BaseTool], ...] = (
    MasteryStatusTool,
    MasteryQuizTool,
    MasteryGradeTool,
    MasteryAssessTool,
    MasteryResumeTool,
    MasteryBuildTool,
)


__all__ = [
    "MASTERY_TOOL_NAMES",
    "MASTERY_TOOL_TYPES",
    "MasteryStatusTool",
    "MasteryQuizTool",
    "MasteryGradeTool",
    "MasteryAssessTool",
    "MasteryResumeTool",
    "MasteryBuildTool",
]
