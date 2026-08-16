"""Deterministically derive bounded Episodes from persisted conversation Turns.

The service reads only canonical immutable Turns. It uses fixed-size branch
windows and an extractive user-request label, so Episode derivation never calls
an LLM and can be replayed safely from the same inputs.
"""

from __future__ import annotations

import hashlib
import json

from .models import ConversationEpisode, ConversationTurn
from .store import ConversationStore

_DERIVATION_VERSION = "online-episode-v1"
_MAX_TURNS_PER_EPISODE = 4
_MAX_REQUEST_CHARS = 360
_TASK_TYPE = "chat"


def _normalized_excerpt(value: str) -> str:
    return " ".join(value.split())[:_MAX_REQUEST_CHARS]


def _input_hash(
    *,
    owner_id: str,
    thread_id: str,
    branch_id: str,
    turns: tuple[ConversationTurn, ...],
) -> str:
    payload = {
        "derivation_version": _DERIVATION_VERSION,
        "owner_id": owner_id,
        "thread_id": thread_id,
        "branch_id": branch_id,
        "task_type": _TASK_TYPE,
        "turns": [
            {
                "turn_id": turn.turn_id,
                "role": turn.role,
                "content": turn.content,
                "attachment_refs": turn.attachment_refs,
                "tool_call_refs": turn.tool_call_refs,
                "source_refs": turn.source_refs,
                "safety_status": turn.safety_status,
                "created_at": turn.created_at,
            }
            for turn in turns
        ],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _episode_id(*, owner_id: str, thread_id: str, branch_id: str, start_turn_id: str) -> str:
    boundary = "\x1f".join((_DERIVATION_VERSION, owner_id, thread_id, branch_id, start_turn_id))
    return f"ep_online_{hashlib.sha256(boundary.encode('utf-8')).hexdigest()[:24]}"


def _summary(turns: tuple[ConversationTurn, ...]) -> str:
    requests = [
        excerpt
        for turn in turns
        if turn.role == "user" and (excerpt := _normalized_excerpt(turn.content))
    ]
    if not requests:
        return "Conversation segment."
    return f"User requests: {' | '.join(requests)}"[:20_000]


class EpisodeDerivationService:
    """Create or version one active-branch Episode from a stable Turn window."""

    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    def derive_for_thread(
        self,
        thread_id: str,
    ) -> ConversationEpisode | None:
        thread = self._store.get_thread(thread_id)
        if thread is None:
            return None
        active_turns = tuple(
            turn
            for turn in self._store.get_active_branch(thread_id)
            if turn.branch_id == thread.active_branch_id
        )
        if len(active_turns) < 2 or active_turns[-1].role != "assistant":
            return None

        # Fixed windows are a deterministic boundary. A later terminal pair in
        # the same window versions that Episode; a new branch receives a new
        # stable ID and therefore cannot rewrite the old branch's derivative.
        start_index = ((len(active_turns) - 1) // _MAX_TURNS_PER_EPISODE) * (_MAX_TURNS_PER_EPISODE)
        window = tuple(active_turns[start_index:])
        if window[0].role != "user":
            return None
        input_hash = _input_hash(
            owner_id=self._store.owner_id,
            thread_id=thread_id,
            branch_id=thread.active_branch_id,
            turns=window,
        )
        return self._store.upsert_derived_episode(
            episode_id=_episode_id(
                owner_id=self._store.owner_id,
                thread_id=thread_id,
                branch_id=thread.active_branch_id,
                start_turn_id=window[0].turn_id,
            ),
            thread_id=thread_id,
            branch_id=thread.active_branch_id,
            start_turn_id=window[0].turn_id,
            end_turn_id=window[-1].turn_id,
            task_type=_TASK_TYPE,
            summary=_summary(window),
            source_refs=tuple(turn.turn_id for turn in window),
            derivation_input_hash=input_hash,
        )


__all__ = ["EpisodeDerivationService"]
