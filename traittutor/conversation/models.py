"""Immutable conversation, episode, working-state, and open-loop contracts.

Conversation turns are L0 facts.  Summaries, episodes, and working state are
versioned derivatives that may be rebuilt without rewriting a published turn.
None of these objects is learning evidence and this module has no BKT write
surface (invariants #1, #2, and #11).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ConversationRole = Literal["user", "assistant", "tool", "system"]
ConversationStatus = Literal["active", "archived", "deleted"]
TurnSafetyStatus = Literal["allowed", "redacted"]
EpisodeStatus = Literal["open", "closed", "superseded", "deleted"]
WorkingStateStatus = Literal["active", "closed", "deleted"]
OpenLoopStatus = Literal["open", "completed", "cancelled", "expired", "deleted"]
MemorySensitivity = Literal["public", "personal", "sensitive"]
ScalarValue = str | int | float | bool | None


def _require_utc_iso(value: str) -> str:
    """Require an aware UTC ISO-8601 timestamp for stable ordering."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("timestamp must include a UTC offset")
    return value


class ConversationThread(BaseModel):
    """One durable conversation and its current versioned read pointers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    subject_id: str | None = Field(default=None, max_length=128)
    project_id: str | None = Field(default=None, max_length=128)
    current_topic: str | None = Field(default=None, max_length=500)
    active_branch_id: str = Field(min_length=1, max_length=96)
    turn_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=100_000)
    episode_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    open_loop_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=10_000)
    working_state_id: str | None = Field(default=None, max_length=96)
    version: int = Field(default=1, ge=1)
    status: ConversationStatus = "active"
    created_at: str
    updated_at: str

    _validate_created_at = field_validator("created_at")(_require_utc_iso)
    _validate_updated_at = field_validator("updated_at")(_require_utc_iso)


class ConversationSessionBinding(BaseModel):
    """Owner-bound stable mapping from a live session to one durable thread.

    The unified session database remains the transport/runtime source of
    truth.  This small record merely prevents an online reconnect or worker
    replay from creating a second canonical ConversationThread for that same
    session.  It deliberately carries no message text or learning evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    created_at: str

    _validate_created_at = field_validator("created_at")(_require_utc_iso)


class ConversationTurn(BaseModel):
    """An immutable published message/tool fact in one thread branch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_id: str = Field(min_length=1, max_length=96)
    thread_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    branch_id: str = Field(min_length=1, max_length=96)
    sequence: int = Field(ge=1)
    role: ConversationRole
    content: str = Field(max_length=200_000)
    attachment_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    tool_call_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    parent_turn_id: str | None = Field(default=None, max_length=96)
    supersedes_turn_id: str | None = Field(default=None, max_length=96)
    # A server-derived source key (normally the unified session turn/message
    # identity) makes online write replay safe without exposing a caller
    # supplied mutable idempotency surface.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)
    safety_status: TurnSafetyStatus = "allowed"
    created_at: str

    _validate_created_at = field_validator("created_at")(_require_utc_iso)


class ConversationEpisode(BaseModel):
    """A versioned L2 summary over an explicit immutable turn range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1, max_length=96)
    thread_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    branch_id: str = Field(min_length=1, max_length=96)
    start_turn_id: str = Field(min_length=1, max_length=96)
    end_turn_id: str = Field(min_length=1, max_length=96)
    started_at: str
    ended_at: str
    topic_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    entity_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    task_type: str = Field(min_length=1, max_length=96)
    summary: str = Field(max_length=20_000)
    source_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    open_loop_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    sensitivity: MemorySensitivity = "personal"
    status: EpisodeStatus = "closed"
    summary_version: int = Field(default=1, ge=1)
    supersedes_episode_version: int | None = Field(default=None, ge=1)
    # Present only for deterministic online derivatives. Older/manual Episode
    # records remain readable while replay-safe online updates can compare the
    # exact immutable Turn input without retaining a second raw transcript.
    derivation_input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    created_at: str

    _validate_started_at = field_validator("started_at")(_require_utc_iso)
    _validate_ended_at = field_validator("ended_at")(_require_utc_iso)
    _validate_created_at = field_validator("created_at")(_require_utc_iso)


class SessionWorkingState(BaseModel):
    """One immutable L1 checkpoint from which an interrupted turn can resume."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state_id: str = Field(min_length=1, max_length=96)
    thread_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    branch_id: str = Field(min_length=1, max_length=96)
    active_goal: str | None = Field(default=None, max_length=4_000)
    temporary_variables: dict[str, ScalarValue] = Field(default_factory=dict)
    recent_turn_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    tool_result_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    open_loop_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    supersedes_state_id: str | None = Field(default=None, max_length=96)
    version: int = Field(default=1, ge=1)
    status: WorkingStateStatus = "active"
    created_at: str

    _validate_created_at = field_validator("created_at")(_require_utc_iso)


class OpenLoop(BaseModel):
    """A versioned unfinished action, never a permanent personality claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    open_loop_id: str = Field(min_length=1, max_length=96)
    thread_id: str = Field(min_length=1, max_length=96)
    owner_id: str = Field(min_length=1, max_length=128)
    episode_id: str | None = Field(default=None, max_length=96)
    title: str = Field(min_length=1, max_length=500)
    details: str | None = Field(default=None, max_length=8_000)
    source_turn_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=1_000)
    status: OpenLoopStatus = "open"
    due_at: str | None = None
    version: int = Field(default=1, ge=1)
    supersedes_version: int | None = Field(default=None, ge=1)
    created_at: str
    updated_at: str

    _validate_due_at = field_validator("due_at")(
        lambda value: _require_utc_iso(value) if value is not None else value
    )
    _validate_created_at = field_validator("created_at")(_require_utc_iso)
    _validate_updated_at = field_validator("updated_at")(_require_utc_iso)
