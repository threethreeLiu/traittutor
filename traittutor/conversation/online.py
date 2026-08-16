"""Safe bridge from live unified-session turns to canonical conversations.

The unified session store owns transport, streaming, and browser message IDs.
This module records a *completed* online turn as immutable L0 conversation
facts only after the request passes the deterministic safety gate.  It does
not grade, infer mastery, create memories, or invoke an LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from traittutor.security.prompt_guard import PromptGuardRejected, enforce_prompt_guard

from .episode_derivation import EpisodeDerivationService
from .models import ConversationEpisode, ConversationThread, ConversationTurn
from .store import ConversationAccessError, ConversationStore, RiskyInputRejected

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OnlineConversationRecord:
    """Canonical facts written for one successfully terminal online turn."""

    thread: ConversationThread
    user_turn: ConversationTurn
    assistant_turn: ConversationTurn
    episode: ConversationEpisode | None


class ConversationOnlineBridge:
    """Append an owner-local unified-session turn without replay duplicates."""

    def __init__(
        self,
        owner_id: str,
        *,
        store_factory: Callable[[str], ConversationStore] = ConversationStore,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._store_factory = store_factory

    @staticmethod
    def _message_key(session_id: str, message_id: int | str) -> str:
        return f"session:{session_id}:message:{message_id}"

    @staticmethod
    def _degraded_assistant_key(session_id: str, runtime_turn_id: str) -> str:
        return f"session:{session_id}:turn:{runtime_turn_id}:degraded-assistant"

    def record_terminal_turn(
        self,
        *,
        session_id: str,
        runtime_turn_id: str,
        session_title: str,
        user_content: str,
        user_message_id: int | str,
        assistant_content: str,
        assistant_message_id: int | str | None,
        parent_message_id: int | str | None = None,
        subject_id: str | None = None,
        assistant_degraded: bool = False,
    ) -> OnlineConversationRecord:
        """Record a safe terminal response, preserving the requested branch.

        ``user_message_id`` and ``assistant_message_id`` are server-issued
        unified-session IDs.  Replaying the same terminal callback therefore
        returns existing immutable facts instead of appending a second pair.
        An explicit degraded output is retained as a redacted assistant fact;
        a failed turn with no user-facing fallback must not be recorded.
        """
        normalized_session_id = session_id.strip()
        normalized_runtime_turn_id = runtime_turn_id.strip()
        if not normalized_session_id or not normalized_runtime_turn_id:
            raise ValueError("session_id and runtime_turn_id are required")
        if not assistant_content.strip():
            raise ValueError("terminal assistant content is required")
        try:
            enforce_prompt_guard(user_content)
        except PromptGuardRejected as exc:
            # The store's blocked path is deliberately invoked before a
            # session binding/thread ID/lock is created, preserving invariant
            # #10 even if this bridge is called directly by another runtime.
            raise RiskyInputRejected("blocked input cannot create a conversation turn") from exc

        store = self._store_factory(self.owner_id)
        if store.owner_id != self.owner_id:
            raise PermissionError("conversation store does not own this online turn")
        thread = store.get_or_create_thread_for_session(
            normalized_session_id,
            title=session_title[:240],
            subject_id=subject_id,
        )
        user_key = self._message_key(normalized_session_id, user_message_id)
        parent_turn_id: str | None = None
        if parent_message_id is not None:
            parent = store.get_turn_by_idempotency_key(
                thread.thread_id,
                self._message_key(normalized_session_id, parent_message_id),
            )
            if parent is None:
                # Attaching a user edit to an unknown/foreign branch would
                # silently reshape history.  A first bridge write for an
                # older pre-WS-5 session has no canonical ancestor to point
                # at, so it starts a fresh immutable lineage; once this
                # thread has facts, a missing parent is a fail-closed error.
                if store.list_turns(thread.thread_id):
                    raise ConversationAccessError("online branch parent is not available")
            else:
                parent_turn_id = parent.turn_id

        user_turn = store.append_turn(
            thread.thread_id,
            role="user",
            content=user_content,
            parent_turn_id=parent_turn_id,
            idempotency_key=user_key,
        )
        assistant_key = (
            self._message_key(normalized_session_id, assistant_message_id)
            if assistant_message_id is not None
            else self._degraded_assistant_key(normalized_session_id, normalized_runtime_turn_id)
        )
        assistant_turn = store.append_turn(
            thread.thread_id,
            role="assistant",
            content=assistant_content,
            parent_turn_id=user_turn.turn_id,
            idempotency_key=assistant_key,
            safety_status="redacted" if assistant_degraded else "allowed",
        )
        episode: ConversationEpisode | None = None
        try:
            # Episode is a derivative: both immutable Turns are already durable
            # and a derivation failure must never roll them back or fail chat.
            episode = EpisodeDerivationService(store).derive_for_thread(thread.thread_id)
        except Exception:
            logger.warning(
                "Episode derivation failed after terminal Turns were persisted",
                exc_info=True,
            )
        return OnlineConversationRecord(
            thread=store.get_thread(thread.thread_id) or thread,
            user_turn=user_turn,
            assistant_turn=assistant_turn,
            episode=episode,
        )
