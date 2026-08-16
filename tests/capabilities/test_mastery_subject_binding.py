"""Regression coverage for the trusted Mastery Chat subject-binding seam."""

from __future__ import annotations

import asyncio
import json

import pytest

from traittutor.capabilities.mastery import binding
from traittutor.capabilities.mastery import tools as mastery_tools
from traittutor.capabilities.mastery.binding import (
    create_mastery_path_binding,
    load_bound_mastery_progress,
    resolve_mastery_path_binding,
)
from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
)
from traittutor.learning.service import LearningService
from traittutor.learning.storage import LearningStore
from traittutor.learning_model import KnowledgeStateKey, LearnerEventLedger
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.services.session.turn_runtime import TurnRuntimeManager


def _user(user_id: str) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        role="user",
        scope=scope_for_user(user_id, is_admin=False),
    )


def _progress(*, subject_id: str = "math") -> LearningProgress:
    return LearningProgress(
        book_id="path-algebra",
        subject_id=subject_id,
        modules=[
            LearningModule(
                id="module-1",
                name="Algebra",
                order=1,
                knowledge_points=[
                    KnowledgePoint(
                        id="kc-linear",
                        name="Linear equations",
                        type=KnowledgeType.PROCEDURE,
                        module_id="module-1",
                    )
                ],
            )
        ],
    )


class _PersonalizationRecorder:
    async def record_event(self, _event: object, *, trusted: bool) -> list[object]:
        assert trusted is True
        return []


@pytest.fixture
def owner_store(monkeypatch: pytest.MonkeyPatch, tmp_path):
    store = LearningStore(tmp_path / "learning")
    store.save(_progress())
    monkeypatch.setattr(binding, "LearningStore", lambda: store)
    return store


def test_binding_is_owner_subject_and_kc_graph_fenced(owner_store) -> None:
    owner_token = set_current_user(_user("owner-a"))
    try:
        bound = create_mastery_path_binding("path-algebra")
        assert bound is not None
        assert bound.owner_id == "owner-a"
        assert bound.subject_id == "math"
        assert load_bound_mastery_progress(bound) is not None

        # A copied session preference cannot target another authenticated user.
        other_token = set_current_user(_user("owner-b"))
        try:
            assert load_bound_mastery_progress(bound) is None
        finally:
            reset_current_user(other_token)

        # The binding is also invalidated if the authoritative subject or KC
        # graph changed after selection. No stale BKT partition remains usable.
        changed = owner_store.load("path-algebra")
        assert changed is not None
        changed.subject_id = "physics"
        owner_store.save(changed)
        assert load_bound_mastery_progress(bound) is None
    finally:
        reset_current_user(owner_token)


def test_missing_or_invalid_selection_never_falls_back_to_an_old_binding(owner_store) -> None:
    token = set_current_user(_user("owner-a"))
    try:
        stored = create_mastery_path_binding("path-algebra")
        assert stored is not None
        assert (
            resolve_mastery_path_binding(
                requested_path_id=None,
                persisted_binding=stored.model_dump(),
            )
            == stored
        )
        # An explicit bad request must not silently reuse the old math path.
        assert (
            resolve_mastery_path_binding(
                requested_path_id="model-invented-path",
                persisted_binding=stored.model_dump(),
            )
            is None
        )
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_mastery_grade_uses_bound_subject_and_replay_never_double_scores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    owner_store,
) -> None:
    token = set_current_user(_user("owner-a"))
    try:
        bound = create_mastery_path_binding("path-algebra")
        assert bound is not None
        chain = CanonicalAnswerEventChain(
            LearnerEventLedger(tmp_path / "events.json"),
            personalization_service_factory=_PersonalizationRecorder,
        )
        service = LearningService(
            owner_store,
            event_chain=chain,
            resume_canonical_derivations=False,
        )
        monkeypatch.setattr(mastery_tools, "_new_service", lambda: service)
        private = {"_mastery_binding": bound.model_dump()}

        registered = await mastery_tools.MasteryQuizTool().execute(
            **private,
            knowledge_point_id="kc-linear",
            question="Solve x + 2 = 4.",
            expected_answer="2",
        )
        assert registered.success is True

        first = await mastery_tools.MasteryGradeTool().execute(**private, answer="2")
        assert first.success is True
        payload = json.loads(first.content)
        assert payload["knowledge_point_id"] == "kc-linear"
        assert "mastery" not in payload
        assert payload["evidence_state"] == "insufficient_evidence"

        event = next(iter(chain.ledger))
        assert (event.user_id, event.subject_id, event.learning_path_id, event.kc_ids) == (
            "owner-a",
            "math",
            "path-algebra",
            ("kc-linear",),
        )
        state = chain.rebuild_bkt().get(
            KnowledgeStateKey(user_id="owner-a", subject_id="math", kc_id="kc-linear")
        )
        assert state is not None and state.verified_observation_count == 1

        # A retried browser/tool submission cannot recreate either a pending
        # answer or a second canonical event after the first server mutation.
        replay = await mastery_tools.MasteryGradeTool().execute(**private, answer="2")
        assert replay.success is False
        assert len(chain.ledger) == 1
        replayed = chain.rebuild_bkt().get(
            KnowledgeStateKey(user_id="owner-a", subject_id="math", kc_id="kc-linear")
        )
        assert replayed is not None and replayed.verified_observation_count == 1
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_unbound_mastery_tools_are_unknown_and_make_no_progress_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A missing binding does not call get_or_create, so a session ID can never
    # become an accidental learning path or BKT namespace.
    monkeypatch.setattr(
        mastery_tools,
        "_new_service",
        lambda: pytest.fail("unbound status must not open a LearningService"),
    )
    result = await mastery_tools.MasteryStatusTool().execute()
    assert result.success is False
    payload = json.loads(result.content)
    assert payload["status"] == "unknown"
    assert payload["reason"] == "missing_or_stale_subject_binding"


class _RuntimeStore:
    def __init__(self) -> None:
        self.session = {"id": "session-mastery", "preferences": {}}
        self.preference_updates: list[dict[str, object]] = []

    async def ensure_session(self, _session_id: str | None) -> dict[str, object]:
        return self.session

    async def list_active_turns(self, _session_id: str) -> list[dict[str, object]]:
        return []

    async def update_session_preferences(
        self, _session_id: str, preferences: dict[str, object]
    ) -> bool:
        self.preference_updates.append(preferences)
        return True

    async def create_turn(self, _session_id: str, capability: str = "") -> dict[str, str]:
        return {"id": "turn-mastery", "capability": capability}


@pytest.mark.asyncio
async def test_runtime_persists_only_server_derived_mastery_binding(
    monkeypatch: pytest.MonkeyPatch,
    owner_store,
) -> None:
    # Local-admin is the normal no-auth test identity.  The binding still
    # derives that owner from the server ContextVar, never from the request.
    binding_value = create_mastery_path_binding("path-algebra")
    assert binding_value is not None
    store = _RuntimeStore()
    manager = TurnRuntimeManager(store=store)  # type: ignore[arg-type]
    blocker = asyncio.Event()

    async def hold_turn(_execution: object) -> None:
        await blocker.wait()

    monkeypatch.setattr(manager, "_run_turn", hold_turn)
    _session, turn = await manager.start_turn(
        {
            "capability": "mastery_path",
            "content": "Practise equations",
            "tools": [],
            "knowledge_bases": [],
            "language": "en",
            "config": {
                "product_mode": "assist",
                "learning_path_id": "path-algebra",
            },
        }
    )
    persisted = store.preference_updates[-1]["mastery_path_binding"]
    assert persisted == binding_value.model_dump()
    execution = manager._executions[turn["id"]]
    assert execution.payload["_mastery_path_binding"] == binding_value.model_dump()
    assert execution.task is not None
    execution.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution.task
