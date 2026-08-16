"""Context snapshot wiring at the unified turn boundary."""

from __future__ import annotations

from typing import Any

import pytest

from traittutor.services.session.turn_runtime import (
    _assemble_assistant_context_snapshot,
    _assistant_snapshot_intent,
)


def test_snapshot_intent_uses_explicit_capability_without_keyword_routing() -> None:
    assert _assistant_snapshot_intent(capability="research", learning_support=False) == "research"
    assert _assistant_snapshot_intent(capability="quiz", learning_support=False) == "create"
    assert _assistant_snapshot_intent(capability="chat", learning_support=False) == "chat"
    assert _assistant_snapshot_intent(capability="research", learning_support=True) == "learn"


def test_turn_runtime_assembles_owner_authorized_snapshot_before_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    sentinel = object()

    class _Assembler:
        def assemble(self, **kwargs: Any) -> object:
            captured.append(kwargs)
            return sentinel

    monkeypatch.setattr("traittutor.context_assembler.ContextAssembler", _Assembler)

    snapshot = _assemble_assistant_context_snapshot(
        capability="chat",
        learning_support=True,
        user_id="owner-1",
        session_id="session-1",
        subject_id="subject-1",
        learning_plan_id="plan-1",
        include_tutor_persona=False,
    )

    assert snapshot is sentinel
    assert captured == [
        {
            "intent": "learn",
            "user_id": "owner-1",
            "subject_id": "subject-1",
            "thread_id": "session-1",
            "token_budget": 8_000,
            "user_authorized": True,
            "include_personalization": True,
            "include_tutor_persona": False,
            "component_plan_ref": "plan-1",
            "surface_type": "learning_canvas",
        }
    ]
