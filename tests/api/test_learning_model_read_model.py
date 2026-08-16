from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header
import httpx
import pytest
import pytest_asyncio

from traittutor.api.routers import learning_model as learning_model_router
from traittutor.learning.models import (
    ErrorRecord,
    ErrorRecordStatus,
    ErrorType,
    KnowledgeType,
    LearningProgress,
    RepetitionState,
    ReviewTask,
)
from traittutor.learning.storage import LearningStore
from traittutor.learning_governance.models import (
    ErrorSummary,
    GovernanceAttributionStatus,
    LearnerSubjectLearningState,
    LearningGovernanceSnapshot,
    ReviewStatus,
    ReviewSummary,
    SubjectKnowledgeEvidence,
)
from traittutor.learning_governance.repository import (
    LearningGovernanceRepository,
    OwnerBoundLearningStore,
)
from traittutor.learning_model.events import (
    LearnerEvent,
    LearnerEventAmendment,
    LearnerEventLedger,
)
from traittutor.learning_model.misconception import MisconceptionStore
from traittutor.learning_model.read_service import (
    CanonicalLearningModelSources,
    LearningModelReadService,
    SubjectSeed,
    SupportFacts,
)
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user
from traittutor.personalization.graph_repository import LearningKnowledgeGraphRepository
from traittutor.personalization.models import LearningKnowledgeGraph, SubjectRef

_FORBIDDEN_KEYS = {
    "answer",
    "answer_correct",
    "user_answer",
    "expected_answer",
    "rubric",
    "rubric_ref",
    "prompt",
    "raw_prompt",
    "system_prompt",
    "owner_id",
    "user_id",
}


@dataclass
class FakeSources:
    owner_id: str
    fail_governance: bool = False

    def profile_subjects(self) -> Sequence[SubjectSeed]:
        if self.owner_id == "user-b":
            return (SubjectSeed("history", "History", source_refs=("profile-b",)),)
        return (
            SubjectSeed(
                "math",
                "Mathematics",
                updated_at="2026-08-11T08:00:00+00:00",
                source_refs=("profile-math",),
            ),
            SubjectSeed(
                "draft-science",
                "Science",
                confirmed=False,
                updated_at="2026-08-11T07:00:00+00:00",
                source_refs=("profile-pending",),
            ),
        )

    def progress_subjects(self) -> Sequence[SubjectSeed]:
        if self.owner_id == "user-b":
            return ()
        return (
            SubjectSeed(
                "math",
                "math",
                covered_kc_count=2,
                source_refs=("learning-path:book-private",),
            ),
        )

    def pack_subjects(self) -> Sequence[SubjectSeed]:
        if self.owner_id == "user-b":
            return ()
        return (
            SubjectSeed(
                "math",
                "math",
                covered_kc_count=2,
                source_refs=("learning-pack:pack-private",),
            ),
        )

    def event_subjects(self) -> Sequence[SubjectSeed]:
        if self.owner_id == "user-b":
            return ()
        return (
            SubjectSeed(
                "math",
                "math",
                strong_evidence_count=3,
                attribution_pending_count=1,
                source_refs=("learner-event-ledger",),
            ),
            SubjectSeed(
                "draft-science",
                "draft-science",
                strong_evidence_count=1,
                source_refs=("learner-event-ledger",),
            ),
        )

    def governance_subjects(self) -> Sequence[SubjectSeed]:
        if self.owner_id == "user-b":
            return ()
        return (
            SubjectSeed(
                "error-only",
                "Error only",
                source_refs=("error-records",),
            ),
            SubjectSeed(
                "misconception-only",
                "Misconception only",
                source_refs=("misconceptions",),
            ),
            SubjectSeed(
                "review-only",
                "Review only",
                source_refs=("review-items",),
            ),
        )

    def knowledge_graph_subjects(self) -> Sequence[SubjectSeed]:
        if self.owner_id == "user-b":
            return ()
        return (
            SubjectSeed(
                "graph-only",
                "Graph only",
                source_refs=("knowledge-graph",),
            ),
        )

    def governance(self, subject_id: str) -> LearningGovernanceSnapshot:
        if self.fail_governance:
            raise OSError("governance unavailable")
        if subject_id != "math":
            return LearningGovernanceSnapshot(subject_id=subject_id)
        return LearningGovernanceSnapshot(
            subject_id=subject_id,
            errors=(
                ErrorSummary(
                    error_id="error-private",
                    question_id="question-private",
                    subject_id="math",
                    kc_id="fractions",
                    error_type=ErrorType.APPLICATION_ERROR,
                    status=ErrorRecordStatus.OPEN,
                    attribution_status=GovernanceAttributionStatus.VERIFIED,
                    source_event_ids=("event-private",),
                    created_at=1.0,
                ),
            ),
            reviews=(
                ReviewSummary(
                    review_id="review-private",
                    learning_path_id="book-private",
                    subject_id="math",
                    kc_id="fractions",
                    knowledge_type=KnowledgeType.CONCEPT,
                    due_at=1.0,
                    priority=1,
                    status=ReviewStatus.DUE,
                    attribution_status=GovernanceAttributionStatus.VERIFIED,
                    interval_index=1,
                ),
            ),
        )

    def learning_state(self, subject_id: str) -> LearnerSubjectLearningState:
        return LearnerSubjectLearningState(
            subject_id=subject_id,
            source_revision="a" * 64,
            param_version="v1-uncalibrated",
            calibrated=False,
            strong_event_count=3 if subject_id == "math" else 0,
            knowledge=(
                SubjectKnowledgeEvidence(
                    kc_id="fractions",
                    evidence_state="insufficient_evidence",
                    change_signal="none",
                    verified_observation_count=3,
                    model_version="v1-uncalibrated",
                    stage_policy_version="bkt-stage-policy-v1",
                ),
            )
            if subject_id == "math"
            else (),
        )

    def knowledge_graph(self, subject_id: str) -> LearningKnowledgeGraph | None:
        return LearningKnowledgeGraph(
            subject=SubjectRef(
                subject_id=subject_id,
                label=subject_id,
                confidence=1.0,
                source="user",
                confirmed=True,
            ),
            source_refs=["graph-private-source"],
            updated_at="2026-08-11T08:30:00+00:00",
        )

    def support(self, subject_id: str | None = None) -> SupportFacts:
        return SupportFacts(
            inference_enabled=True,
            confirmed_preference_count=1,
            confirmed_reflection_count=1,
            compass_signal_count=0,
            updated_at="2026-08-11T08:00:00+00:00",
            source_refs=("learner-profile", "reflection"),
        )


def _assert_safe_keys(value: Any) -> None:
    if isinstance(value, dict):
        assert _FORBIDDEN_KEYS.isdisjoint(value)
        for child in value.values():
            _assert_safe_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_safe_keys(child)


def test_service_deduplicates_subject_union_and_separates_pending() -> None:
    result = LearningModelReadService(
        owner_id="user-a",
        sources=FakeSources("user-a"),
    ).overview()

    assert [item.subject_id for item in result.confirmed_subjects.items] == [
        "error-only",
        "graph-only",
        "math",
        "misconception-only",
        "review-only",
    ]
    math = next(item for item in result.confirmed_subjects.items if item.subject_id == "math")
    assert math.strong_evidence_count == 3
    assert "learning-pack:pack-private" in math.source_refs
    assert (
        "error-records"
        in next(
            item for item in result.confirmed_subjects.items if item.subject_id == "error-only"
        ).source_refs
    )
    assert [item.subject_id for item in result.pending_subjects.items] == ["draft-science"]
    assert result.today.active_subject_count == 5
    assert result.today.attribution_pending_count == 1


def test_canonical_event_discovery_excludes_void_and_other_owner(tmp_path) -> None:
    ledger = LearnerEventLedger(tmp_path / "events.json")
    for event_id, owner, subject in (
        ("voided", "user-a", "science"),
        ("kept", "user-a", "math"),
        ("other-owner", "user-b", "history"),
    ):
        ledger.append(
            LearnerEvent(
                event_id=event_id,
                idempotency_key=f"idem-{event_id}",
                user_id=owner,
                subject_id=subject,
                kc_ids=("kc-1",),
                surface_type="quiz",
                answer_correct=True,
                evidence_strength="strong",
                created_at="2026-08-11T00:00:00+00:00",
            )
        )
    ledger.append_amendment(
        LearnerEventAmendment(
            amendment_id="void-science",
            idempotency_key="void-science-idem",
            target_event_id="voided",
            user_id="user-a",
            subject_id="science",
            kc_ids=("kc-1",),
            reason_code="attribution_error",
            created_at="2026-08-11T00:01:00+00:00",
        )
    )
    sources = object.__new__(CanonicalLearningModelSources)
    sources.owner_id = "user-a"
    sources._event_ledger = ledger

    assert [item.subject_id for item in sources.event_subjects()] == ["math"]


def test_owner_bound_stores_enumerate_governance_and_graph_subjects(tmp_path) -> None:
    learning_store = LearningStore(tmp_path / "learning")
    learning_store.save(
        LearningProgress(
            book_id="error-book",
            subject_id="error-only",
            error_records=[
                ErrorRecord(
                    id="error-1",
                    question_id="question-1",
                    knowledge_point_id="kc-error",
                    module_id="module-1",
                    error_type=ErrorType.APPLICATION_ERROR,
                )
            ],
        )
    )
    learning_store.save(
        LearningProgress(
            book_id="review-book",
            subject_id="review-only",
            review_queue=[
                ReviewTask(
                    id="review-1",
                    knowledge_point_id="kc-review",
                    knowledge_type=KnowledgeType.CONCEPT,
                    due_at=1.0,
                    priority=1,
                    state=RepetitionState(next_review_at=1.0),
                )
            ],
        )
    )
    misconception_store = MisconceptionStore(
        tmp_path / "misconceptions.json",
        owner_id="user-a",
    )
    misconception_store.propose(
        hypothesis_id="misconception-1",
        user_id="user-a",
        subject_id="misconception-only",
        kc_ids=("kc-misconception",),
        rubric_ref="rubric-private",
        pattern="Repeated denominator error",
        created_at="2026-08-11T00:00:00+00:00",
    )
    repository = LearningGovernanceRepository(
        owner_id="user-a",
        learning_source=OwnerBoundLearningStore(owner_id="user-a", store=learning_store),
        event_ledger=LearnerEventLedger(tmp_path / "events.json"),
        misconception_store=misconception_store,
    )

    assert repository.subject_sources() == {
        "error-only": ("error-records",),
        "misconception-only": ("misconceptions",),
    }
    with pytest.raises(PermissionError):
        misconception_store.list_subject_ids(user_id="user-b")

    graph_repository = LearningKnowledgeGraphRepository(tmp_path / "knowledge-graph.sqlite3")
    graph_repository.merge(
        LearningKnowledgeGraph(
            subject=SubjectRef(
                subject_id="graph-only",
                label="Graph only",
                confidence=1.0,
                source="user",
                confirmed=True,
            ),
            source_refs=["graph-source-private"],
            updated_at="2026-08-11T00:00:00+00:00",
        ),
        source_ref="graph-source-private",
    )
    assert graph_repository.list_subject_ids() == ("graph-only",)


@pytest.fixture
def learning_model_app(monkeypatch) -> FastAPI:
    sources = {
        "user-a": FakeSources("user-a"),
        "user-b": FakeSources("user-b"),
    }

    def source_factory(user: CurrentUser) -> FakeSources:
        return sources[user.id]

    monkeypatch.setattr(
        learning_model_router,
        "learning_model_sources_factory",
        source_factory,
    )

    async def install_test_user(
        x_test_user: Annotated[str, Header()],
    ) -> AsyncIterator[None]:
        user = CurrentUser(
            id=x_test_user,
            username=x_test_user,
            role="user",
            scope=scope_for_user(x_test_user, is_admin=False),
        )
        token = set_current_user(user)
        try:
            yield
        finally:
            reset_current_user(token)

    app = FastAPI()
    app.state.sources = sources
    app.include_router(
        learning_model_router.router,
        prefix="/api/v1",
        dependencies=[Depends(install_test_user)],
    )
    return app


@pytest_asyncio.fixture
async def client(learning_model_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=learning_model_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://traittutor.test",
        headers={"X-Test-User": "user-a"},
    ) as api_client:
        yield api_client


@pytest.mark.asyncio
async def test_api_hides_uncalibrated_mastery_and_sensitive_source_fields(
    client: httpx.AsyncClient,
) -> None:
    overview = await client.get("/api/v1/learning-model/overview")
    detail = await client.get("/api/v1/learning-model/subjects/math")

    assert overview.status_code == 200
    assert detail.status_code == 200
    body = detail.json()
    mastery = body["tabs"]["knowledge"]["mastery_items"][0]
    assert mastery["evidence_state"] == "insufficient_evidence"
    assert mastery["verified_observation_count"] == 3
    assert not {"status", "probability", "interval", "percentage"}.intersection(mastery)
    _assert_safe_keys(overview.json())
    _assert_safe_keys(body)
    serialized = f"{overview.json()} {body}"
    assert "question-private" not in serialized
    assert "event-private" not in serialized


@pytest.mark.asyncio
async def test_partial_source_failure_marks_sections_stale_without_page_failure(
    client: httpx.AsyncClient,
    learning_model_app: FastAPI,
) -> None:
    learning_model_app.state.sources["user-a"].fail_governance = True

    response = await client.get("/api/v1/learning-model/overview")

    assert response.status_code == 200
    body = response.json()
    assert [item["subject_id"] for item in body["confirmed_subjects"]["items"]] == [
        "error-only",
        "graph-only",
        "math",
        "misconception-only",
        "review-only",
    ]
    assert body["confirmed_subjects"]["meta"]["status"] == "stale"
    assert "learning-governance" in body["confirmed_subjects"]["meta"]["unavailable_sources"]


@pytest.mark.asyncio
async def test_subject_lookup_is_owner_bound_and_uses_generic_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get(
        "/api/v1/learning-model/subjects/history",
        params={"user_id": "user-b"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Learning subject not found"}
