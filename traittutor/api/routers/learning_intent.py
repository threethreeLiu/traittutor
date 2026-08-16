"""Safe intent-routing endpoint for the Learn entry surface."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter
from pydantic import BaseModel, Field

from traittutor.learning.intent import classify_learn_intent
from traittutor.multi_user.audit import log_intent_security_event
from traittutor.multi_user.context import get_current_user

router = APIRouter()


class LearnIntentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    session_id: str | None = Field(default=None, max_length=128)
    # Transient excerpt for deterministic screening only. It never reaches the
    # Gateway classifier and is never retained in audit records.
    attachment_text: str | None = Field(default=None, max_length=240_000)


@router.post("/intent")
async def route_learn_intent(request: LearnIntentRequest) -> dict[str, object]:
    """Classify an untrusted Learn message without reading attachment content."""
    user = get_current_user()
    result = await classify_learn_intent(
        request.message,
        attachment_text=request.attachment_text,
        user_id=user.id,
    )
    if result.safety_action != "allow":
        session_hash = (
            hashlib.sha256(request.session_id.encode("utf-8")).hexdigest()[:16]
            if request.session_id
            else None
        )
        log_intent_security_event(
            action=result.safety_action,
            category=result.safety_category,
            session_hash=session_hash,
        )
    return result.public_dict()
