"""Focused acceptance tests for the WS-1 frozen context boundary."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from traittutor.context_assembler import (
    AssistantContextSnapshot,
    ContextAssembler,
    MemoryAccessLog,
    MemoryAccessRecord,
)
from traittutor.context_assembler.snapshot import MemoryRef, SnapshotReadRanges

CREATED_AT = "2026-08-09T08:00:00+00:00"


def _snapshot(**updates: object) -> AssistantContextSnapshot:
    values: dict[str, object] = {
        "snapshot_id": "",
        "trace_id": "trace_test",
        "created_at": CREATED_AT,
        "intent": "chat",
        "user_id": "user-1",
        "token_budget": 1024,
        "read_ranges": SnapshotReadRanges(
            thread_version="thread-v3",
            memory_refs=[MemoryRef(scope="L2", key="chat", version="7")],
        ),
    }
    values.update(updates)
    return AssistantContextSnapshot.model_validate(values)


def test_identical_snapshot_inputs_have_equal_content_hash() -> None:
    first = _snapshot()
    second = _snapshot()

    assert first.snapshot_id == second.snapshot_id
    assert first.content_hash() == second.content_hash()


def test_snapshot_is_frozen_and_forbids_unknown_fields() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValidationError):
        snapshot.token_used = 12  # type: ignore[misc]

    with pytest.raises(ValidationError):
        _snapshot(unknown_field="must be rejected")


def test_memory_access_log_append_is_idempotent_by_record_id() -> None:
    access_log = MemoryAccessLog()
    record = MemoryAccessRecord(
        record_id="mar_same",
        snapshot_id="ctx_test",
        created_at=CREATED_AT,
        scope="canonical_memory:global",
        key="preferences",
        version_read="4",
        purpose="context_assembler:chat",
        user_authorized=True,
    )

    access_log.append(record)
    access_log.append(record)

    assert access_log.for_snapshot("ctx_test") == [record]


def test_assemble_degrades_when_an_internal_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembler = ContextAssembler()

    def fail_read(**_kwargs: object) -> object:
        raise RuntimeError("personalization is temporarily unavailable")

    monkeypatch.setattr(assembler, "_read_personalization_context", fail_read)

    snapshot = assembler.assemble(
        intent="learn",
        user_id="user-1",
        subject_id="statistics",
        token_budget=2048,
        created_at=CREATED_AT,
        trace_id="trace_degraded",
        user_authorized=True,
    )

    assert snapshot.degraded is True
    assert snapshot.degradation_reason == "personalization_read_failed"


def test_assemble_fail_closed_without_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    # user_authorized defaults to False (fail-closed, invariants #7/#12): a
    # caller that forgets the flag must NOT touch personalization at all.
    assembler = ContextAssembler()

    def fail_read(**_kwargs: object) -> object:
        raise AssertionError("personalization must not be read when unauthorized")

    monkeypatch.setattr(assembler, "_read_personalization_context", fail_read)

    snapshot = assembler.assemble(
        intent="learn",
        user_id="user-1",
        subject_id="statistics",
        token_budget=2048,
        created_at=CREATED_AT,
        trace_id="trace_unauthorized",
    )

    assert snapshot.degraded is False
    assert snapshot.degradation_reason is None
