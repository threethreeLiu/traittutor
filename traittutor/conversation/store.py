"""File-locked, atomic persistence for versioned conversation facts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Literal
from uuid import uuid4

from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .models import (
    ConversationEpisode,
    ConversationRole,
    ConversationSessionBinding,
    ConversationThread,
    ConversationTurn,
    MemorySensitivity,
    OpenLoop,
    OpenLoopStatus,
    ScalarValue,
    SessionWorkingState,
    TurnSafetyStatus,
)

_SCHEMA_VERSION = 1


class ConversationStoreError(RuntimeError):
    """The durable conversation store cannot safely serve a request."""


class ConversationAccessError(PermissionError):
    """A caller attempted to read or link another owner's conversation."""


class RiskyInputRejected(ValueError):
    """A blocked input was rejected before any persistent side effect."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationStore:
    """Owner-bound durable conversation repository.

    Binding the owner at construction keeps the required owner check out of
    caller-controlled filters. IDs belonging to another owner fail closed and
    are reported as absent, avoiding an object-existence side channel.
    """

    def __init__(self, owner_id: str, *, path: Path | None = None) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._store_path = path
        self._adapter = SectionedRecordStore(
            "conversations",
            owner_id,
            schema_version=_SCHEMA_VERSION,
            path_service=get_path_service() if path is None else None,
            legacy_path=path,
        )

    def _path(self) -> Path:
        return self._store_path or (
            get_path_service().get_workspace_dir() / "traittutor" / "conversations.json"
        )

    def _lock_path(self) -> Path:
        return self._path().with_suffix(".lock")

    def _empty(self) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "threads": [],
            "turns": [],
            "episodes": [],
            "working_states": [],
            "open_loops": [],
            "session_bindings": [],
        }

    def _load(self) -> dict[str, Any]:
        try:
            payload = self._adapter.snapshot()
        except Exception as exc:
            raise ConversationStoreError("Unable to read conversation data") from exc
        required = {"threads", "turns", "episodes", "working_states", "open_loops"}
        if not isinstance(payload, dict) or any(
            not isinstance(payload.get(key), list) for key in required
        ):
            raise ConversationStoreError("Conversation data has an invalid format")
        # v1 files predate online session bindings.  This additive migration
        # changes no existing conversation facts and keeps upgrades readable.
        if "session_bindings" not in payload:
            payload["session_bindings"] = []
        if not isinstance(payload["session_bindings"], list):
            raise ConversationStoreError("Conversation data has an invalid format")
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        try:
            self._adapter.replace_all(payload)
        except Exception as exc:
            raise ConversationStoreError("Unable to persist conversation data") from exc

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        with self._adapter.locked() as payload:
            yield payload

    @staticmethod
    def _latest(
        records: list[dict[str, Any]], object_id: str, id_key: str
    ) -> dict[str, Any] | None:
        matching = [record for record in records if record.get(id_key) == object_id]
        if not matching:
            return None
        return max(matching, key=lambda record: int(record.get("version", 1)))

    def _owned_record(
        self,
        records: list[dict[str, Any]],
        object_id: str,
        id_key: str,
        *,
        latest: bool = False,
    ) -> dict[str, Any] | None:
        record = (
            self._latest(records, object_id, id_key)
            if latest
            else next((item for item in records if item.get(id_key) == object_id), None)
        )
        if record is None or record.get("owner_id") != self.owner_id:
            return None
        return record

    # -- threads and immutable turns -------------------------------------
    def create_thread(
        self,
        *,
        title: str,
        subject_id: str | None = None,
        project_id: str | None = None,
        current_topic: str | None = None,
        created_at: str | None = None,
    ) -> ConversationThread:
        now = created_at or _now()
        thread = ConversationThread(
            thread_id=f"thr_{uuid4().hex[:16]}",
            owner_id=self.owner_id,
            title=title.strip() or "Untitled conversation",
            subject_id=subject_id,
            project_id=project_id,
            current_topic=current_topic,
            active_branch_id=f"br_{uuid4().hex[:16]}",
            created_at=now,
            updated_at=now,
        )
        with self._locked() as payload:
            payload["threads"].append(thread.model_dump(mode="json"))
            self._save(payload)
        return thread

    def get_thread_for_session(self, session_id: str) -> ConversationThread | None:
        """Return the thread mapped to a live session only for its owner."""
        binding = self._owned_record(self._load()["session_bindings"], session_id, "session_id")
        if binding is None:
            return None
        return self.get_thread(str(binding["thread_id"]))

    def get_or_create_thread_for_session(
        self,
        session_id: str,
        *,
        title: str,
        subject_id: str | None = None,
        project_id: str | None = None,
        current_topic: str | None = None,
        created_at: str | None = None,
    ) -> ConversationThread:
        """Atomically bind one owner session to one durable conversation.

        A foreign owner never receives the binding or the thread.  Reusing a
        session identifier under another owner is treated as an authorization
        error rather than silently forking private history.
        """
        normalized_session_id = session_id.strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        now = created_at or _now()
        with self._locked() as payload:
            matching = next(
                (
                    item
                    for item in payload["session_bindings"]
                    if item.get("session_id") == normalized_session_id
                ),
                None,
            )
            if matching is not None:
                if matching.get("owner_id") != self.owner_id:
                    raise ConversationAccessError("session is not owned by this user")
                record = self._owned_record(
                    payload["threads"], str(matching.get("thread_id") or ""), "thread_id"
                )
                if record is None:
                    raise ConversationStoreError("conversation session binding is corrupt")
                return ConversationThread.model_validate(record)

            thread = ConversationThread(
                thread_id=f"thr_{uuid4().hex[:16]}",
                owner_id=self.owner_id,
                title=title.strip() or "Untitled conversation",
                subject_id=subject_id,
                project_id=project_id,
                current_topic=current_topic,
                active_branch_id=f"br_{uuid4().hex[:16]}",
                created_at=now,
                updated_at=now,
            )
            binding = ConversationSessionBinding(
                session_id=normalized_session_id,
                thread_id=thread.thread_id,
                owner_id=self.owner_id,
                created_at=now,
            )
            payload["threads"].append(thread.model_dump(mode="json"))
            payload["session_bindings"].append(binding.model_dump(mode="json"))
            self._save(payload)
        return thread

    def get_thread(self, thread_id: str) -> ConversationThread | None:
        record = self._owned_record(self._load()["threads"], thread_id, "thread_id")
        return ConversationThread.model_validate(record) if record is not None else None

    def get_thread_version(self, thread_id: str) -> str | None:
        """Return an owner-authorized version ref for ContextAssembler wiring."""
        thread = self.get_thread(thread_id)
        if thread is None:
            return None
        return f"{thread.thread_id}:v{thread.version}"

    def append_turn(
        self,
        thread_id: str,
        *,
        role: ConversationRole,
        content: str,
        attachment_refs: tuple[str, ...] = (),
        tool_call_refs: tuple[str, ...] = (),
        source_refs: tuple[str, ...] = (),
        parent_turn_id: str | None = None,
        supersedes_turn_id: str | None = None,
        idempotency_key: str | None = None,
        safety_status: TurnSafetyStatus | Literal["blocked"] = "allowed",
        created_at: str | None = None,
    ) -> ConversationTurn:
        if safety_status == "blocked":
            # Invariant #10: reject before lock acquisition or ID creation.
            raise RiskyInputRejected("blocked input cannot create a conversation turn")
        if idempotency_key is not None and not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        with self._locked() as payload:
            thread_record = self._owned_record(payload["threads"], thread_id, "thread_id")
            if thread_record is None:
                raise KeyError(thread_id)
            thread = ConversationThread.model_validate(thread_record)
            if idempotency_key is not None:
                existing_record = next(
                    (
                        record
                        for record in payload["turns"]
                        if record.get("owner_id") == self.owner_id
                        and record.get("thread_id") == thread_id
                        and record.get("idempotency_key") == idempotency_key
                    ),
                    None,
                )
                if existing_record is not None:
                    existing = ConversationTurn.model_validate(existing_record)
                    if existing.role != role or existing.content != content:
                        raise ConversationStoreError("conversation turn idempotency conflict")
                    return existing
            branch_id = thread.active_branch_id
            if supersedes_turn_id is not None:
                superseded = self._owned_record(payload["turns"], supersedes_turn_id, "turn_id")
                if superseded is None or superseded.get("thread_id") != thread_id:
                    raise ConversationAccessError("superseded turn is not owned by this thread")
                # Editing creates a new branch. The published old turn and all
                # of its outputs remain byte-for-byte unchanged.
                branch_id = f"br_{uuid4().hex[:16]}"
                parent_turn_id = superseded.get("parent_turn_id")
            elif parent_turn_id is not None:
                parent = self._owned_record(payload["turns"], parent_turn_id, "turn_id")
                if parent is None or parent.get("thread_id") != thread_id:
                    raise ConversationAccessError("parent turn is not owned by this thread")
                # The live session protocol represents an edit/alternate
                # continuation as an explicit older parent.  Preserve the
                # published lineage by opening a new branch instead of
                # reusing the current active branch's identity.
                if thread.turn_ids and parent_turn_id != thread.turn_ids[-1]:
                    branch_id = f"br_{uuid4().hex[:16]}"
            elif thread.turn_ids:
                parent_turn_id = thread.turn_ids[-1]

            turn = ConversationTurn(
                turn_id=f"turn_{uuid4().hex[:16]}",
                thread_id=thread_id,
                owner_id=self.owner_id,
                branch_id=branch_id,
                sequence=len(thread.turn_ids) + 1,
                role=role,
                content=content,
                attachment_refs=attachment_refs,
                tool_call_refs=tool_call_refs,
                source_refs=source_refs,
                parent_turn_id=parent_turn_id,
                supersedes_turn_id=supersedes_turn_id,
                idempotency_key=idempotency_key,
                safety_status=safety_status,
                created_at=created_at or _now(),
            )
            # Event-before-derived: append the immutable L0 fact before
            # updating thread pointers in the same atomic transaction.
            payload["turns"].append(turn.model_dump(mode="json"))
            updated = thread.model_copy(
                update={
                    "active_branch_id": branch_id,
                    "turn_ids": (*thread.turn_ids, turn.turn_id),
                    "version": thread.version + 1,
                    "updated_at": turn.created_at,
                }
            )
            thread_record.clear()
            thread_record.update(updated.model_dump(mode="json"))
            self._save(payload)
        return turn

    def get_turn(self, turn_id: str) -> ConversationTurn | None:
        record = self._owned_record(self._load()["turns"], turn_id, "turn_id")
        return ConversationTurn.model_validate(record) if record is not None else None

    def get_turn_by_idempotency_key(
        self,
        thread_id: str,
        idempotency_key: str,
    ) -> ConversationTurn | None:
        """Resolve a server-generated online source key inside one thread."""
        if self.get_thread(thread_id) is None:
            return None
        record = next(
            (
                item
                for item in self._load()["turns"]
                if item.get("owner_id") == self.owner_id
                and item.get("thread_id") == thread_id
                and item.get("idempotency_key") == idempotency_key
            ),
            None,
        )
        return ConversationTurn.model_validate(record) if record is not None else None

    def list_turns(self, thread_id: str) -> list[ConversationTurn]:
        if self.get_thread(thread_id) is None:
            return []
        records = [
            ConversationTurn.model_validate(record)
            for record in self._load()["turns"]
            if record.get("thread_id") == thread_id and record.get("owner_id") == self.owner_id
        ]
        return sorted(records, key=lambda turn: turn.sequence)

    def get_active_branch(self, thread_id: str) -> list[ConversationTurn]:
        thread = self.get_thread(thread_id)
        if thread is None:
            return []
        turns = {turn.turn_id: turn for turn in self.list_turns(thread_id)}
        branch_turns = [
            turn for turn in turns.values() if turn.branch_id == thread.active_branch_id
        ]
        if not branch_turns:
            return []
        cursor: ConversationTurn | None = max(branch_turns, key=lambda turn: turn.sequence)
        lineage: list[ConversationTurn] = []
        seen: set[str] = set()
        while cursor is not None and cursor.turn_id not in seen:
            seen.add(cursor.turn_id)
            lineage.append(cursor)
            cursor = turns.get(cursor.parent_turn_id or "")
        return list(reversed(lineage))

    def get_active_branch_version(self, thread_id: str) -> str | None:
        """Return a non-content reference for the active immutable branch."""
        thread = self.get_thread(thread_id)
        if thread is None:
            return None
        return f"{thread.thread_id}:{thread.active_branch_id}:v{thread.version}"

    def get_active_episode_ids(self, thread_id: str, *, limit: int = 2) -> tuple[str, ...]:
        """Return recent active-branch episode IDs without exposing summaries."""
        thread = self.get_thread(thread_id)
        if thread is None or limit <= 0:
            return ()
        episodes = [
            episode
            for episode in self.list_episodes(thread_id)
            if episode.branch_id == thread.active_branch_id and episode.status == "closed"
        ]
        return tuple(episode.episode_id for episode in episodes[-limit:])

    # -- versioned derived conversation state ----------------------------
    def create_episode(
        self,
        thread_id: str,
        *,
        start_turn_id: str,
        end_turn_id: str,
        task_type: str,
        summary: str,
        topic_refs: tuple[str, ...] = (),
        entity_refs: tuple[str, ...] = (),
        source_refs: tuple[str, ...] = (),
        open_loop_refs: tuple[str, ...] = (),
        sensitivity: MemorySensitivity = "personal",
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> ConversationEpisode:
        with self._locked() as payload:
            thread_record = self._owned_record(payload["threads"], thread_id, "thread_id")
            if thread_record is None:
                raise KeyError(thread_id)
            thread = ConversationThread.model_validate(thread_record)
            turns = {
                turn.turn_id: turn
                for turn in (ConversationTurn.model_validate(record) for record in payload["turns"])
                if turn.owner_id == self.owner_id and turn.thread_id == thread_id
            }
            if start_turn_id not in turns or end_turn_id not in turns:
                raise ConversationAccessError("episode turn range is not owned by this thread")
            episode = ConversationEpisode(
                episode_id=f"ep_{uuid4().hex[:16]}",
                thread_id=thread_id,
                owner_id=self.owner_id,
                branch_id=turns[end_turn_id].branch_id,
                start_turn_id=start_turn_id,
                end_turn_id=end_turn_id,
                started_at=started_at or turns[start_turn_id].created_at,
                ended_at=ended_at or turns[end_turn_id].created_at,
                topic_refs=topic_refs,
                entity_refs=entity_refs,
                task_type=task_type,
                summary=summary,
                source_refs=source_refs,
                open_loop_refs=open_loop_refs,
                sensitivity=sensitivity,
                created_at=_now(),
            )
            payload["episodes"].append(episode.model_dump(mode="json"))
            updated = thread.model_copy(
                update={
                    "episode_ids": (*thread.episode_ids, episode.episode_id),
                    "version": thread.version + 1,
                    "updated_at": episode.created_at,
                }
            )
            thread_record.clear()
            thread_record.update(updated.model_dump(mode="json"))
            self._save(payload)
        return episode

    def revise_episode(self, episode_id: str, *, summary: str) -> ConversationEpisode:
        with self._locked() as payload:
            owned_versions = [
                record
                for record in payload["episodes"]
                if record.get("episode_id") == episode_id
                and record.get("owner_id") == self.owner_id
            ]
            record = max(
                owned_versions,
                key=lambda item: int(item.get("summary_version", 1)),
                default=None,
            )
            if record is None:
                raise KeyError(episode_id)
            previous = ConversationEpisode.model_validate(record)
            revised = previous.model_copy(
                update={
                    "summary": summary,
                    "summary_version": previous.summary_version + 1,
                    "supersedes_episode_version": previous.summary_version,
                    "created_at": _now(),
                }
            )
            payload["episodes"].append(revised.model_dump(mode="json"))
            self._save(payload)
        return revised

    def upsert_derived_episode(
        self,
        *,
        episode_id: str,
        thread_id: str,
        branch_id: str,
        start_turn_id: str,
        end_turn_id: str,
        task_type: str,
        summary: str,
        source_refs: tuple[str, ...],
        derivation_input_hash: str,
    ) -> ConversationEpisode:
        """Atomically create or version a deterministic online Episode.

        The stable ``episode_id`` names one branch-local boundary. Replaying an
        identical Turn window returns its latest version without a write; a
        larger window appends a version and never mutates the prior record.
        """
        with self._locked() as payload:
            thread_record = self._owned_record(payload["threads"], thread_id, "thread_id")
            if thread_record is None:
                raise KeyError(thread_id)
            thread = ConversationThread.model_validate(thread_record)
            if thread.active_branch_id != branch_id:
                raise ConversationStoreError("derived episode branch is no longer active")

            turns = {
                turn.turn_id: turn
                for turn in (ConversationTurn.model_validate(record) for record in payload["turns"])
                if turn.owner_id == self.owner_id and turn.thread_id == thread_id
            }
            start_turn = turns.get(start_turn_id)
            end_turn = turns.get(end_turn_id)
            if start_turn is None or end_turn is None or end_turn.branch_id != branch_id:
                raise ConversationAccessError("derived episode range is not owned by this branch")
            cursor: ConversationTurn | None = end_turn
            lineage: set[str] = set()
            while cursor is not None and cursor.turn_id not in lineage:
                lineage.add(cursor.turn_id)
                cursor = turns.get(cursor.parent_turn_id or "")
            if start_turn_id not in lineage:
                raise ConversationAccessError("derived episode range is not contiguous")

            owned_versions = [
                record
                for record in payload["episodes"]
                if record.get("episode_id") == episode_id
                and record.get("owner_id") == self.owner_id
            ]
            previous_record = max(
                owned_versions,
                key=lambda item: int(item.get("summary_version", 1)),
                default=None,
            )
            now = _now()
            if previous_record is not None:
                previous = ConversationEpisode.model_validate(previous_record)
                if (
                    previous.thread_id != thread_id
                    or previous.branch_id != branch_id
                    or previous.start_turn_id != start_turn_id
                ):
                    raise ConversationStoreError("derived episode boundary conflict")
                if previous.derivation_input_hash == derivation_input_hash:
                    return previous
                revised = previous.model_copy(
                    update={
                        "end_turn_id": end_turn_id,
                        "ended_at": end_turn.created_at,
                        "task_type": task_type,
                        "summary": summary,
                        "source_refs": source_refs,
                        "summary_version": previous.summary_version + 1,
                        "supersedes_episode_version": previous.summary_version,
                        "derivation_input_hash": derivation_input_hash,
                        "created_at": now,
                    }
                )
                payload["episodes"].append(revised.model_dump(mode="json"))
                self._save(payload)
                return revised

            episode = ConversationEpisode(
                episode_id=episode_id,
                thread_id=thread_id,
                owner_id=self.owner_id,
                branch_id=branch_id,
                start_turn_id=start_turn_id,
                end_turn_id=end_turn_id,
                started_at=start_turn.created_at,
                ended_at=end_turn.created_at,
                task_type=task_type,
                summary=summary,
                source_refs=source_refs,
                derivation_input_hash=derivation_input_hash,
                created_at=now,
            )
            payload["episodes"].append(episode.model_dump(mode="json"))
            updated = thread.model_copy(
                update={
                    "episode_ids": (*thread.episode_ids, episode.episode_id),
                    "version": thread.version + 1,
                    "updated_at": episode.created_at,
                }
            )
            thread_record.clear()
            thread_record.update(updated.model_dump(mode="json"))
            self._save(payload)
            return episode

    def get_episode(self, episode_id: str) -> ConversationEpisode | None:
        owned_versions = [
            record
            for record in self._load()["episodes"]
            if record.get("episode_id") == episode_id and record.get("owner_id") == self.owner_id
        ]
        record = max(
            owned_versions,
            key=lambda item: int(item.get("summary_version", 1)),
            default=None,
        )
        return ConversationEpisode.model_validate(record) if record is not None else None

    def list_episodes(self, thread_id: str) -> list[ConversationEpisode]:
        thread = self.get_thread(thread_id)
        if thread is None:
            return []
        return [
            episode
            for episode_id in thread.episode_ids
            if (episode := self.get_episode(episode_id)) is not None
        ]

    def save_working_state(
        self,
        thread_id: str,
        *,
        active_goal: str | None = None,
        temporary_variables: dict[str, ScalarValue] | None = None,
        recent_turn_ids: tuple[str, ...] = (),
        tool_result_refs: tuple[str, ...] = (),
        open_loop_ids: tuple[str, ...] = (),
    ) -> SessionWorkingState:
        with self._locked() as payload:
            thread_record = self._owned_record(payload["threads"], thread_id, "thread_id")
            if thread_record is None:
                raise KeyError(thread_id)
            thread = ConversationThread.model_validate(thread_record)
            previous = (
                self._owned_record(payload["working_states"], thread.working_state_id, "state_id")
                if thread.working_state_id
                else None
            )
            previous_state = (
                SessionWorkingState.model_validate(previous) if previous is not None else None
            )
            state = SessionWorkingState(
                state_id=f"state_{uuid4().hex[:16]}",
                thread_id=thread_id,
                owner_id=self.owner_id,
                branch_id=thread.active_branch_id,
                active_goal=active_goal,
                temporary_variables=temporary_variables or {},
                recent_turn_ids=recent_turn_ids,
                tool_result_refs=tool_result_refs,
                open_loop_ids=open_loop_ids,
                supersedes_state_id=previous_state.state_id if previous_state else None,
                version=(previous_state.version + 1) if previous_state else 1,
                created_at=_now(),
            )
            payload["working_states"].append(state.model_dump(mode="json"))
            updated = thread.model_copy(
                update={
                    "working_state_id": state.state_id,
                    "version": thread.version + 1,
                    "updated_at": state.created_at,
                }
            )
            thread_record.clear()
            thread_record.update(updated.model_dump(mode="json"))
            self._save(payload)
        return state

    def get_working_state(self, thread_id: str) -> SessionWorkingState | None:
        thread = self.get_thread(thread_id)
        if thread is None or thread.working_state_id is None:
            return None
        record = self._owned_record(
            self._load()["working_states"], thread.working_state_id, "state_id"
        )
        return SessionWorkingState.model_validate(record) if record is not None else None

    def create_open_loop(
        self,
        thread_id: str,
        *,
        title: str,
        details: str | None = None,
        episode_id: str | None = None,
        source_turn_ids: tuple[str, ...] = (),
        due_at: str | None = None,
    ) -> OpenLoop:
        with self._locked() as payload:
            thread_record = self._owned_record(payload["threads"], thread_id, "thread_id")
            if thread_record is None:
                raise KeyError(thread_id)
            thread = ConversationThread.model_validate(thread_record)
            if episode_id is not None:
                episode_record = self._owned_record(payload["episodes"], episode_id, "episode_id")
                if episode_record is None or episode_record.get("thread_id") != thread_id:
                    raise ConversationAccessError("episode is not owned by this thread")
            now = _now()
            loop = OpenLoop(
                open_loop_id=f"loop_{uuid4().hex[:16]}",
                thread_id=thread_id,
                owner_id=self.owner_id,
                episode_id=episode_id,
                title=title,
                details=details,
                source_turn_ids=source_turn_ids,
                due_at=due_at,
                created_at=now,
                updated_at=now,
            )
            payload["open_loops"].append(loop.model_dump(mode="json"))
            updated = thread.model_copy(
                update={
                    "open_loop_ids": (*thread.open_loop_ids, loop.open_loop_id),
                    "version": thread.version + 1,
                    "updated_at": now,
                }
            )
            thread_record.clear()
            thread_record.update(updated.model_dump(mode="json"))
            self._save(payload)
        return loop

    def transition_open_loop(
        self,
        open_loop_id: str,
        *,
        status: OpenLoopStatus,
        due_at: str | None = None,
    ) -> OpenLoop:
        with self._locked() as payload:
            record = self._owned_record(
                payload["open_loops"], open_loop_id, "open_loop_id", latest=True
            )
            if record is None:
                raise KeyError(open_loop_id)
            previous = OpenLoop.model_validate(record)
            revised = previous.model_copy(
                update={
                    "status": status,
                    "due_at": due_at if due_at is not None else previous.due_at,
                    "version": previous.version + 1,
                    "supersedes_version": previous.version,
                    "updated_at": _now(),
                }
            )
            payload["open_loops"].append(revised.model_dump(mode="json"))
            self._save(payload)
        return revised

    def get_open_loop(self, open_loop_id: str) -> OpenLoop | None:
        record = self._owned_record(
            self._load()["open_loops"], open_loop_id, "open_loop_id", latest=True
        )
        return OpenLoop.model_validate(record) if record is not None else None

    def list_open_loops(self, thread_id: str, *, include_closed: bool = False) -> list[OpenLoop]:
        thread = self.get_thread(thread_id)
        if thread is None:
            return []
        loops = [
            loop
            for loop_id in thread.open_loop_ids
            if (loop := self.get_open_loop(loop_id)) is not None
        ]
        if include_closed:
            return loops
        return [loop for loop in loops if loop.status == "open"]
