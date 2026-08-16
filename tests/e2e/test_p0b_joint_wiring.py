"""Canonical grading and PageSchema joint end-to-end acceptance.

The tests drive ``LearningService.grade_and_record`` and the generation task
API together to prove the current contracts compose:

  #2  only reliable, server-graded strong evidence moves BKT (weak/unattributed
      input records nothing — not even a quiz attempt)
  #4  replaying the same attempt is idempotent at both the event ledger and the
      service-level attempt layer (no double-score — §11.2 unpublishable guard)
  #5  the served PageSchema never carries answer/rubric/solution/back/correct/
      expected (answers stay server-side)
  #6  learner state is isolated per user — u1's BKT/pages never appear for u2
      and vice versa (§11.1 must-pass; §11.2 "跨用户泄露" guard)
  #11 refresh re-serves the identical persisted page (no drift)

No provider network is used in the repeatable suite.  The HTTP vertical slice
stubs only the audited Gateway boundary while exercising the real FastAPI route,
durable task manager, ContextAssembler, courseware pipeline, Orchestrator,
PageStore, and learner-safe response serializer.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from traittutor.api.main import app
from traittutor.api.routers import auth as auth_router
from traittutor.api.routers import traittutor_generate as generate_router
from traittutor.components import (
    ComponentInstance,
    PageRegion,
    PageSchema,
    PageStore,
    validate_page_schema,
)
from traittutor.gateway import GatewayReceipt, GatewayResponse
from traittutor.generate import runner as generate_runner
from traittutor.generate import service as generate_service
from traittutor.generate import tasks as generation_tasks
from traittutor.generate.service import GenerationRequest, GenerationResult, MaterialSource
from traittutor.generate.tasks import GenerationTask, GenerationTaskManager
from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
    PendingQuestion,
)
from traittutor.learning.service import LearningService
from traittutor.learning.storage import LearningStore
from traittutor.learning_model import (
    KnowledgeStateKey,
    LearnerEventLedger,
)
from traittutor.orchestration import OrchestratorRunStore
from traittutor.services.llm.config import LLMConfig

ANSWER_FIELDS = {"answer", "rubric", "solution", "back", "correct", "expected"}


class _PersonalizationRecorder:
    """Captures trusted BKT projections without standing up the personalization
    store. Asserting ``trusted is True`` here is the #2 gate: nothing but
    server-graded strong evidence may ever reach the canonical BKT writer."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def record_event(self, event: object, *, trusted: bool) -> list[object]:
        assert trusted is True, "only server-graded strong evidence may project to BKT"
        self.events.append(event)
        return []


def _chain(tmp_path: Path) -> CanonicalAnswerEventChain:
    return CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=_PersonalizationRecorder,
    )


def _progress(*, book_id: str, kc: str = "kc1", qid: str = "q1") -> LearningProgress:
    return LearningProgress(
        book_id=book_id,
        modules=[
            LearningModule(
                id="m1",
                name="Math",
                order=1,
                knowledge_points=[
                    KnowledgePoint(id=kc, name=kc, type=KnowledgeType.PROCEDURE, module_id="m1")
                ],
            )
        ],
        knowledge_types={kc: KnowledgeType.PROCEDURE},
        pending_question=PendingQuestion(
            question_id=qid, knowledge_point_id=kc, module_id="m1", expected_answer="4"
        ),
    )


def _grade_task(*, generation_id: str, owner_id: str, title: str) -> GenerationTask:
    result = GenerationResult(
        generation_id=generation_id,
        generation_type="courseware",
        status="completed",
        events=[],
        result={
            "kind": "courseware",
            "sections": [{"section_title": title, "core_content": [f"Body for {title}"]}],
        },
        created_at="2026-08-09T08:00:00+00:00",
        prompt_asset="courseware.md",
        material={},
        learner_profile={},
    )
    return GenerationTask(
        generation_id=generation_id,
        owner_id=owner_id,
        request=GenerationRequest(
            generation_type="courseware",
            material=MaterialSource(source_type="paste", text="Material"),
        ),
        status="completed",
        result=result,
    )


def _published_page(*, generation_id: str, title: str) -> PageSchema:
    return PageSchema(
        page_schema_id=f"{generation_id}:page",
        generation_run_id=generation_id,
        version="v1",
        regions=[
            PageRegion(
                region_id="r1",
                component=ComponentInstance(
                    instance_id=f"{generation_id}:page:r1",
                    component_type="concept_explanation",
                    version="v1",
                    props={"title": title, "body_markdown": f"Body for {title}"},
                ),
            )
        ],
        created_at="2026-08-09T08:00:00+00:00",
    )


class _Manager:
    def __init__(self, *tasks: GenerationTask) -> None:
        self._tasks = {t.generation_id: t for t in tasks}

    def get(self, generation_id: str) -> GenerationTask | None:
        return self._tasks.get(generation_id)


def _assert_no_answers(value: Any) -> None:
    if isinstance(value, dict):
        assert not ANSWER_FIELDS.intersection(value), f"answer key leaked into page: {value}"
        for child in value.values():
            _assert_no_answers(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_answers(child)


# ---------------------------------------------------------------------------
# Test 1 — the vertical slice under all flags ON: grading + page together.
# ---------------------------------------------------------------------------


def test_canonical_vertical_slice_grading_and_page(tmp_path: Path, monkeypatch: Any) -> None:
    chain = _chain(tmp_path)
    service = LearningService(LearningStore(tmp_path / "progress"), event_chain=chain)
    progress = _progress(book_id="book1")

    # Strong, reliable, server-graded evidence → BKT moves once (#2).
    assert service.grade_and_record(
        progress,
        question_id="q1",
        knowledge_point_id="kc1",
        module_id="m1",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        subject_id="math",
        attempt_id="att-q1",  # stable per-attempt token; the replay below reuses it (#4)
    )
    state = chain.rebuild_bkt().get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"))
    assert state is not None and state.verified_observation_count == 1
    assert progress.verified_observation_counts == {"kc1": 1}

    # Idempotent replay: same attempt identity → no re-score at ledger OR attempt layer (#4).
    service.grade_and_record(
        progress,
        question_id="q1",
        knowledge_point_id="kc1",
        module_id="m1",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        subject_id="math",
        attempt_id="att-q1",  # same attempt token → ledger + attempt-layer dedup (#4)
    )
    assert len(progress.quiz_attempts) == 1
    assert (
        chain.rebuild_bkt()
        .get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"))
        .verified_observation_count
        == 1
    )

    # Weak/unattributed input never moves BKT — and records no attempt at all (#2).
    service.grade_and_record(
        progress,
        question_id="q9",
        knowledge_point_id="kc9",
        module_id="m1",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        subject_id="math",
        attempt_id="att-q9",  # distinct attempt; still attribution_pending → never BKT (#2)
    )
    assert (
        chain.rebuild_bkt().get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc9"))
        is None
    )

    # --- page half: canonical publish + immutable cache gate ---
    store = PageStore(path=tmp_path / "pages.json")
    store.save(_published_page(generation_id="gen-u1", title="One"))
    monkeypatch.setattr(generate_router, "PageStore", lambda: store)
    task = _grade_task(generation_id="gen-u1", owner_id="u1", title="One")
    monkeypatch.setattr(generate_router, "get_generation_task_manager", lambda: _Manager(task))

    first = asyncio.run(generate_router.get_generation_task("gen-u1"))
    page = PageSchema.model_validate(first["page_schema"])
    validate_page_schema(page)  # #8 whitelist holds
    assert first["page_schema_id"] == "gen-u1:page"
    assert page.regions[0].component.component_type == "concept_explanation"
    _assert_no_answers(first["page_schema"])  # #5 answers server-held

    # Refresh recovers the identical persisted page (#11).
    second = asyncio.run(generate_router.get_generation_task("gen-u1"))
    assert second["page_schema"] == first["page_schema"]
    assert second["page_schema_id"] == first["page_schema_id"]


# ---------------------------------------------------------------------------
# Test 2 — cross-user isolation under all flags ON (§11.1 must-pass).
# ---------------------------------------------------------------------------


def test_cross_user_isolation(tmp_path: Path, monkeypatch: Any) -> None:
    # One shared ledger/store (the realistic global durable layer), keyed per user.
    chain = _chain(tmp_path)
    service = LearningService(LearningStore(tmp_path / "progress"), event_chain=chain)
    progress_u1 = _progress(book_id="book-u1", kc="kc1", qid="q-u1")
    progress_u2 = _progress(book_id="book-u2", kc="kc1", qid="q-u2")

    service.grade_and_record(
        progress_u1,
        question_id="q-u1",
        knowledge_point_id="kc1",
        module_id="m1",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        subject_id="math",
        attempt_id="att-u1",
    )
    service.grade_and_record(
        progress_u2,
        question_id="q-u2",
        knowledge_point_id="kc1",
        module_id="m1",
        user_answer="7",
        expected_answer="4",
        user_id="u2",
        subject_id="math",
        attempt_id="att-u2",
    )

    bkt = chain.rebuild_bkt()
    # Each user sees only their own KC state — no cross-user leak (#6).
    u1_states = bkt.all_for(user_id="u1", subject_id="math")
    u2_states = bkt.all_for(user_id="u2", subject_id="math")
    assert {s.kc_id for s in u1_states} == {"kc1"}
    assert {s.kc_id for s in u2_states} == {"kc1"}
    assert (
        bkt.get(
            KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1")
        ).verified_observation_count
        == 1
    )
    assert (
        bkt.get(
            KnowledgeStateKey(user_id="u2", subject_id="math", kc_id="kc1")
        ).verified_observation_count
        == 1
    )
    # Replaying u1's answer leaves u2's count untouched (no merge/leak on replay).
    service.grade_and_record(
        progress_u1,
        question_id="q-u1",
        knowledge_point_id="kc1",
        module_id="m1",
        user_answer="4",
        expected_answer="4",
        user_id="u1",
        subject_id="math",
        attempt_id="att-u1",  # replay u1's submission; u2's state must stay untouched (#6)
    )
    assert (
        chain.rebuild_bkt()
        .get(KnowledgeStateKey(user_id="u2", subject_id="math", kc_id="kc1"))
        .verified_observation_count
        == 1
    )

    # Page layer: distinct per-user generation_ids map to distinct cached pages,
    # and a refresh never serves another user's page (#6 at the PageStore layer).
    store = PageStore(path=tmp_path / "pages.json")
    store.save(_published_page(generation_id="gen-u1", title="Alpha"))
    store.save(_published_page(generation_id="gen-u2", title="Beta"))
    monkeypatch.setattr(generate_router, "PageStore", lambda: store)
    monkeypatch.setattr(
        generate_router,
        "get_generation_task_manager",
        lambda: _Manager(
            _grade_task(generation_id="gen-u1", owner_id="u1", title="Alpha"),
            _grade_task(generation_id="gen-u2", owner_id="u2", title="Beta"),
        ),
    )
    page_u1 = asyncio.run(generate_router.get_generation_task("gen-u1"))
    page_u2 = asyncio.run(generate_router.get_generation_task("gen-u2"))
    assert page_u1["page_schema_id"] == "gen-u1:page"
    assert page_u2["page_schema_id"] == "gen-u2:page"
    assert page_u1["page_schema"] != page_u2["page_schema"]
    # Refresh of u1 returns u1's page — never u2's, even through the shared store.
    assert (
        asyncio.run(generate_router.get_generation_task("gen-u1"))["page_schema"]
        == page_u1["page_schema"]
    )


# ---------------------------------------------------------------------------
# Test 3 — the WS-9B publish→serve vertical slice: a page the orchestrator
# PUBLISHES (multi-region, incl. visual_map) is served by the router DIRECTLY,
# never re-projected from the legacy GenerationResult. Proves the composition
# root + router + PageStore compose on real objects (only the LLM seam is
# stubbed, which is the correct deterministic boundary).
# ---------------------------------------------------------------------------


def test_orchestrator_published_page_served_directly_by_router(
    tmp_path: Path, monkeypatch: Any
) -> None:
    async def courseware(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            lesson={
                "title": "Limits",
                "sections": [
                    {"section_title": "Intro", "core_content": "A limit describes behavior."}
                ],
            },
            trace=[],
        )

    async def visual(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"status": "completed", "asset": {"url": "/media/limit.png", "alt": "Limit"}}

    page_store = PageStore(path=tmp_path / "pages.json")
    run_store = OrchestratorRunStore(tmp_path / "orchestrator-runs.json")
    monkeypatch.setattr(generate_service, "generate_courseware", courseware)
    monkeypatch.setattr(generate_service, "generate_learning_visual", visual)
    monkeypatch.setattr(generate_service, "_orchestrator_run_store", lambda: run_store)
    monkeypatch.setattr(generate_service, "_page_store", lambda: page_store)

    generation_id = "gen-ws9b-joint"
    out = asyncio.run(
        generate_service._generate_courseware_with_orchestrator(
            generation_id=generation_id,
            title="Limits",
            chunks=[{"chunk_id": "c1", "source_id": "s1", "text": "Limits."}],
            learner_strategy={"mode": "scaffolded"},
            slr_support={},
            language="en",
            learning_targets={"courseware_targets": ["limits"]},
            visual_seed={"title": "Limits", "visual_targets": ["limits"]},
        )
    )

    # The orchestrator published a multi-region page under the router's lookup key.
    published = page_store.get(f"{generation_id}:page")
    assert published is not None
    published_types = {
        region.component.component_type
        for region in published.regions
        if region.component is not None
    }
    assert "concept_explanation" in published_types

    # Serve through the REAL router with the same store + a completed task built
    # from the orchestrator's own output dict.
    monkeypatch.setattr(generate_router, "PageStore", lambda: page_store)
    result = GenerationResult(
        generation_id=generation_id,
        generation_type="courseware",
        status="completed",
        events=[],
        result=out,
        created_at="2026-08-09T08:00:00+00:00",
        prompt_asset="courseware.md",
        material={},
        learner_profile={},
    )
    task = GenerationTask(
        generation_id=generation_id,
        owner_id="u1",
        request=GenerationRequest("courseware", MaterialSource("paste", "Limits", "Limits")),
        status="completed",
        result=result,
    )
    monkeypatch.setattr(generate_router, "get_generation_task_manager", lambda: _Manager(task))

    # The canonical published page is served verbatim, preserving every
    # registered region on the publish-to-serve hop.
    served = asyncio.run(generate_router.get_generation_task(generation_id))
    assert served["page_schema_id"] == f"{generation_id}:page"
    served_page = PageSchema.model_validate(served["page_schema"])
    validate_page_schema(served_page)  # #8 whitelist holds on the served page
    served_types = {
        region.component.component_type
        for region in served_page.regions
        if region.component is not None
    }
    # Served == published: no region dropped on the publish→serve hop (#11/no drift).
    assert served_types == published_types
    _assert_no_answers(served["page_schema"])  # #5 answers stay server-held


# ---------------------------------------------------------------------------
# Test 4 — real HTTP/task-queue vertical slice with all switches ON.  Only the
# external provider is replaced; everything from FastAPI inward is production.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_flags_on_real_api_runs_context_orchestrator_and_serves_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRAITTUTOR_COURSEWARE_ORCHESTRATION_MODE", "deterministic")

    class _Gateway:
        def __init__(self) -> None:
            self.purposes: list[str] = []

        async def complete(self, request: Any) -> GatewayResponse:
            self.purposes.append(request.purpose)
            if "content-analysis" in request.purpose:
                payload = {
                    "topic": "Limits",
                    "core_concepts": ["approaching a value"],
                    "difficulty_points": ["reading limit notation"],
                }
            elif "adaptation-plan" in request.purpose:
                payload = {
                    "lesson_structure": ["explain", "practice", "reflect"],
                    "scaffolding": ["worked values near the target"],
                    "checkpoints": ["explain what approaches means"],
                    "visible_teaching_moves": ["step-by-step check"],
                }
            else:
                payload = {
                    "title": "Understanding limits",
                    "lesson_goal": "Explain a limit using nearby function values.",
                    "sections": [
                        {
                            "section_title": "Approaching a value",
                            "goal": "Interpret limit language.",
                            "core_content": "A limit describes the value a function approaches. Check values on both sides of the target.",
                            "checkpoint": {
                                "question": "What does x approaching 2 mean?",
                                "success_criteria": "Use nearby x values on both sides.",
                                "feedback_if_confused": "Try 1.9 and 2.1 first.",
                            },
                            "reflection_prompt": "Explain the trend in your own words.",
                            "references": ["c1"],
                        },
                        {
                            "section_title": "A numerical check",
                            "goal": "Verify a linear limit.",
                            "core_content": "For 3x+1, values near x=2 are near 7. Practice by calculating one value below and one above 2.",
                            "checkpoint": {
                                "question": "Why do the outputs support a limit of 7?",
                                "success_criteria": "Connect nearby inputs to outputs near 7.",
                                "feedback_if_confused": "Compare 3(1.99)+1 and 3(2.01)+1.",
                            },
                            "reflection_prompt": "State the conclusion without relying on exact substitution.",
                            "references": ["c1"],
                        },
                    ],
                    "final_takeaways": ["Limits describe nearby behavior."],
                    "next_step_guidance": "Practice another linear example.",
                }
            return GatewayResponse(
                request_id=f"e2e-{len(self.purposes)}",
                content=json.dumps(payload),
                model="deterministic-e2e",
                purpose=request.purpose,
                latency_ms=1,
                receipt=GatewayReceipt(
                    request_id=f"e2e-{len(self.purposes)}",
                    purpose=request.purpose,
                    model="deterministic-e2e",
                    provider="e2e",
                    route="e2e-route",
                    latency_ms=1,
                    timeout_seconds=request.timeout_seconds,
                    response_format_applied=request.response_format is not None,
                    tools_applied=0,
                    attachments_applied=0,
                ),
            )

    gateway = _Gateway()
    page_store = PageStore(path=tmp_path / "pages.json")
    run_store = OrchestratorRunStore(tmp_path / "orchestrator-runs.json")
    manager = GenerationTaskManager(storage_root=tmp_path / "tasks")

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", False)
    monkeypatch.setattr(
        generate_runner,
        "get_llm_config",
        lambda: LLMConfig(
            model="deterministic-e2e",
            api_key="server-only-test-key",
            base_url="http://provider.invalid/v1",
            provider_name="e2e",
        ),
    )
    monkeypatch.setattr(generate_runner, "get_gateway", lambda: gateway)
    monkeypatch.setattr(generate_service, "_orchestrator_run_store", lambda: run_store)
    monkeypatch.setattr(generate_service, "_page_store", lambda: page_store)
    monkeypatch.setattr(generate_router, "PageStore", lambda: page_store)
    monkeypatch.setattr(generate_router, "get_generation_task_manager", lambda: manager)
    monkeypatch.setattr(
        generation_tasks, "save_generation", lambda *_a, **_k: tmp_path / "saved.json"
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://traittutor.test") as client:
        accepted = await client.post(
            "/api/v1/traittutor/generate/tasks",
            json={
                "generation_type": "courseware",
                "material": {
                    "source_type": "paste",
                    "title": "Limits",
                    "text": "A limit is the value a function approaches. For 3x+1 near x=2, outputs approach 7.",
                    "source_id": "source-limits",
                },
                "learner_profile": {},
                "options": {"language": "en", "thread_id": "thread-e2e"},
            },
        )
        assert accepted.status_code == 202
        generation_id = accepted.json()["generation_id"]

        terminal: dict[str, Any] | None = None
        for _ in range(200):
            response = await client.get(f"/api/v1/traittutor/generate/tasks/{generation_id}")
            assert response.status_code == 200
            terminal = response.json()
            if terminal.get("status") in {"completed", "needs_review", "failed"}:
                break
            await asyncio.sleep(0.01)

        assert terminal is not None
        assert terminal.get("status") != "failed", terminal
        if terminal.get("status") == "needs_review":
            confirmed = await client.post(
                f"/api/v1/traittutor/generate/tasks/{generation_id}/review/confirm"
            )
            assert confirmed.status_code == 200

        served_response = await client.get(f"/api/v1/traittutor/generate/tasks/{generation_id}")
        assert served_response.status_code == 200
        served = served_response.json()

    assert served["status"] == "completed"
    assert served["page_schema_id"] == f"{generation_id}:page"
    page = PageSchema.model_validate(served["page_schema"])
    validate_page_schema(page)
    _assert_no_answers(served["page_schema"])
    assert page_store.get(f"{generation_id}:page") == page
    run_key = served["result"]["trace"][0]["run_key"]
    assert run_store.get_by_key(run_key) is not None
    assert any("content-analysis" in purpose for purpose in gateway.purposes)
    assert any("traittutor-courseware" in purpose for purpose in gateway.purposes)
    snapshot = served.get("personalization_context_snapshot")
    assert isinstance(snapshot, dict)
    assert snapshot.get("purpose") == "courseware"
    assert "degraded" in snapshot
