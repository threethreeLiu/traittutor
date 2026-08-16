"""
Unified session history API.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from traittutor.services.session import get_session_store, get_sqlite_session_store
from traittutor.services.storage.attachment_store import get_attachment_store

logger = logging.getLogger(__name__)

router = APIRouter()

WorkspaceMode = Literal["learn", "assist"]


def _workspace_mode(session: dict[str, Any]) -> WorkspaceMode:
    """Return the session's required canonical workspace mode."""
    preferences = session.get("preferences")
    stored = preferences.get("workspace_mode") if isinstance(preferences, dict) else None
    if stored not in {"learn", "assist"}:
        raise HTTPException(
            status_code=409,
            detail="Session is missing its canonical workspace mode",
        )
    return stored


def _with_workspace_mode(session: dict[str, Any]) -> dict[str, Any]:
    return {**session, "mode": _workspace_mode(session)}


class SessionRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class CreateLearningSessionRequest(BaseModel):
    """Create an empty durable Learn session before a path is planned."""

    title: str = Field(default="My learning path", min_length=1, max_length=100)


class BranchSelectionRequest(BaseModel):
    """Edit-branch picker state: `{parent_message_id: chosen_child_id}`.

    Stored inside the session preferences blob so it survives reloads
    without a dedicated column.
    """

    selected_branches: dict[str, int] = Field(default_factory=dict)


class QuizAnswerImage(BaseModel):
    id: str = ""
    base64: str = ""
    url: str = ""
    filename: str = "answer.png"
    mime_type: str = "image/png"


async def _grade_server_held_quiz_item(
    *,
    session_id: str,
    turn_id: str,
    question_id: str,
    answer: str,
    attempt_id: str,
    user_answer_images: list[QuizAnswerImage] | None = None,
    chain: Any | None = None,
    learning_service: Any | None = None,
) -> dict[str, Any]:
    """Resolve and grade one deep-question attempt without browser answer keys."""
    from traittutor.learning.event_chain import CanonicalAnswerEventChain
    from traittutor.learning.grading import classify_error, grade_answer
    from traittutor.learning.service import (
        LearningService,
        project_canonical_event_to_existing_progress,
    )
    from traittutor.multi_user.context import get_current_user

    store = get_sqlite_session_store()
    item = await store.get_server_quiz_item(session_id, turn_id, question_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="Server-held quiz question not found; regenerate the quiz before submitting.",
        )
    expected = str(item.get("correct_answer") or "").strip()
    if not expected:
        raise HTTPException(status_code=422, detail="Quiz question is not server-verifiable")
    correct = grade_answer(answer, expected, str(item.get("question_type") or "short"))

    subject_id = str(item.get("subject_id") or "").strip()
    kc_id = str(item.get("kc_id") or "").strip()
    # The stream-private quiz record may carry a path link only when the
    # server created it as part of that path. A session id is not a path.
    learning_path_id = str(item.get("learning_path_id") or "").strip()
    projector = learning_service or LearningService(resume_canonical_derivations=False)
    has_target = projector.has_existing_canonical_target(
        user_id=get_current_user().id,
        subject_id=subject_id,
        kc_id=kc_id,
        learning_path_id=learning_path_id,
    )
    event, _outcome = (chain or CanonicalAnswerEventChain()).record_server_graded(
        user_id=get_current_user().id,
        subject_id=subject_id,
        question_id=question_id,
        kc_ids=(kc_id,) if kc_id else (),
        is_correct=correct,
        item_valid=True,
        attribution_reliable=has_target,
        derived=lambda recorded: project_canonical_event_to_existing_progress(
            recorded,
            service=projector,
        ),
        attempt_id=attempt_id,
        surface_type="quiz",
        learning_path_id=learning_path_id if has_target else None,
        error_tag=(classify_error(answer).value if not correct else None),
    )
    if event.page_id != question_id or event.answer_correct != correct:
        raise HTTPException(
            status_code=409, detail="attempt_id cannot be reused for a different Quiz answer"
        )

    image_records: list[dict[str, str]] | None = None
    if user_answer_images is not None:
        # Reuse the attachment boundary so only server-persisted URLs enter
        # the notebook row; clients never get to attach grading material.
        from traittutor.api.routers.question_notebook import (
            AnswerImageUpload,
            _persist_answer_images,
        )

        image_records = await _persist_answer_images(
            session_id,
            [AnswerImageUpload(**image.model_dump()) for image in user_answer_images],
        )
    internal_item: dict[str, Any] = {
        "turn_id": turn_id,
        "question_id": question_id,
        "question": str(item.get("question") or ""),
        "question_type": str(item.get("question_type") or ""),
        "options": item.get("options") if isinstance(item.get("options"), dict) else {},
        "correct_answer": expected,
        "explanation": str(item.get("explanation") or ""),
        "difficulty": str(item.get("difficulty") or ""),
        "user_answer": answer,
        "is_correct": correct,
    }
    if image_records is not None:
        internal_item["user_answer_images"] = image_records
    await store.upsert_notebook_entries(session_id, [internal_item])
    entry = await store.find_notebook_entry(session_id, question_id, turn_id=turn_id)
    return {
        "question_id": question_id,
        "attempt_id": attempt_id,
        "correct": correct,
        # Explanation is a post-submission feedback field, not an answer key.
        "explanation": str(item.get("explanation") or ""),
        "entry_id": int(entry["id"]) if entry is not None else None,
    }


@router.get("")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    mode: WorkspaceMode | None = Query(default=None),
):
    store = get_session_store()
    # Filter before slicing so paging stays meaningful for the selected
    # workspace. 200 is both the store fetch and public API maximum.
    sessions = [
        _with_workspace_mode(item) for item in await store.list_sessions(limit=200, offset=0)
    ]
    if mode is not None:
        sessions = [item for item in sessions if item["mode"] == mode]
    return {"sessions": sessions[offset : offset + limit]}


@router.post("")
async def create_learning_session(payload: CreateLearningSessionRequest):
    """Reserve a real session id for a Learn path before the first chat turn.

    Learn can create a Pack directly from a goal or upload, so waiting for a
    WebSocket message would leave that path without a durable session link.
    """
    store = get_session_store()
    session = await store.create_session(title=payload.title)
    session_id = str(session.get("session_id") or session.get("id") or "")
    if not session_id:
        raise HTTPException(status_code=500, detail="Could not create learning session")
    await store.update_session_preferences(session_id, {"workspace_mode": "learn"})
    saved = await store.get_session(session_id)
    return {"session": _with_workspace_mode(saved or session)}


# Cap (in characters) for a single event payload returned to the UI. RAG
# tools can attach whole KB documents to ``tool_result``/``observation``
# events; the frontend TraceSurface only needs a preview, and the LLM context
# is built from a separate content-only store, so capping here never affects
# model input.
MAX_EVENT_PAYLOAD = 1024 * 1024
_TRUNCATION_NOTICE = "\n\n[... content truncated]"
_TRUNCATABLE_EVENT_TYPES = ("tool_result", "observation")


def _truncate_oversized_events(
    messages: list[dict[str, Any]], limit: int = MAX_EVENT_PAYLOAD
) -> None:
    """Cap oversized ``tool_result``/``observation`` payloads in place.

    The session store already returns each message's events as a parsed
    ``events`` list (see ``SqliteSessionStore._serialize_message``), so we
    mutate that list directly. Only the UI rendering path is affected.
    """

    def _cap(container: dict[str, Any], field: str) -> bool:
        value = container.get(field)
        if isinstance(value, str) and len(value) > limit:
            container[field] = value[:limit] + _TRUNCATION_NOTICE
            return True
        return False

    for msg in messages:
        events = msg.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict) or event.get("type") not in _TRUNCATABLE_EVENT_TYPES:
                continue
            truncated = _cap(event, "content")
            tool_metadata = (event.get("metadata") or {}).get("tool_metadata")
            if isinstance(tool_metadata, dict):
                for field in ("content", "answer"):
                    truncated = _cap(tool_metadata, field) or truncated
            if truncated:
                event["_truncated"] = True


@router.get("/{session_id}")
async def get_session(session_id: str):
    store = get_session_store()
    session = await store.get_session_with_messages(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _truncate_oversized_events(session.get("messages", []))
    return _with_workspace_mode(session)


@router.patch("/{session_id}")
async def rename_session(session_id: str, payload: SessionRenameRequest):
    store = get_session_store()
    updated = await store.update_session_title(session_id, payload.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": _with_workspace_mode(session)}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    store = get_session_store()
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        await get_attachment_store().delete_session(session_id)
    except Exception:
        logger.exception("failed to clean up attachments for session %s", session_id)
    # Soft link with the learning map: a Learn session shown in Recents backs a
    # Pack's primary material (metadata.learning_session_id). Deleting the
    # session from Recents must remove that Pack too, mirroring the Pack-delete
    # side that already cleans up orphaned Learn sessions — otherwise deletion
    # stays inconsistent between the two lists. Only Packs that actually
    # reference this session are removed (an Assist conversation can never be
    # referenced by a Pack), and cleanup is best-effort so it never rolls back
    # the session deletion the user asked for.
    deleted_pack_ids: list[str] = []
    try:
        from traittutor import learning_packs as packs_domain

        linked = packs_domain.packs_referencing_learn_session(session_id)
        if linked:
            removed = packs_domain.delete_packs([str(pack["pack_id"]) for pack in linked])
            deleted_pack_ids = [str(pack["pack_id"]) for pack in removed]
    except Exception:
        logger.exception("linked learning-pack cleanup after session delete failed")
    return {
        "deleted": True,
        "session_id": session_id,
        "deleted_pack_ids": deleted_pack_ids,
    }


@router.put("/{session_id}/branch-selection")
async def update_branch_selection(session_id: str, payload: BranchSelectionRequest):
    store = get_sqlite_session_store()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    updated = await store.update_session_preferences(
        session_id, {"selected_branches": dict(payload.selected_branches)}
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"selected_branches": payload.selected_branches}


@router.delete("/{session_id}/messages/{message_id}")
async def delete_turn_by_message(session_id: str, message_id: int):
    store = get_sqlite_session_store()
    result = await store.delete_turn_by_message(session_id, message_id)
    if result["was_running"]:
        raise HTTPException(
            status_code=409, detail="Cannot delete a message while its turn is running"
        )
    if not result["deleted"]:
        raise HTTPException(status_code=404, detail="Message not found")
    attachment_store = get_attachment_store()
    for aid in result["attachment_ids"]:
        try:
            await attachment_store.delete_attachment(session_id, aid)
        except Exception:
            logger.exception("failed to delete attachment %s for session %s", aid, session_id)
    return result
