from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from traittutor import learning_packs
from traittutor.api.routers.learning_packs import _record_pack_learning_events
from traittutor.personalization.models import LearnerEvent
from traittutor.personalization.service import PersonalizationService
from traittutor.services.session.source_inventory import build_inventory, render_manifest


class _FakeChatStore:
    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        return []

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        return None

    async def get_messages_for_context(
        self,
        session_id: str,
        leaf_message_id: int | None = None,
    ) -> list[dict[str, Any]]:
        return []

    async def get_notebook_entry(self, entry_id: int) -> dict[str, Any] | None:
        return None


@pytest.fixture
def business_loop_env(tmp_path, monkeypatch):
    from traittutor.personalization import service as service_module
    from traittutor.services import path_service

    active_user = SimpleNamespace(id="learner-business-loop")
    path_service_instance = path_service.get_path_service()

    monkeypatch.setattr(service_module.memory_paths, "memory_root", lambda: tmp_path / "memory")
    monkeypatch.setattr(service_module, "get_current_user", lambda: active_user)
    monkeypatch.setattr(service_module, "_service", None)
    monkeypatch.setattr(path_service_instance, "get_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: path_service_instance)

    return PersonalizationService()


def _material(
    *,
    title: str,
    text: str,
    subject: str,
    sub_subject: str,
    grade_band: str,
    difficulty: str,
    concept_ids: list[str],
) -> dict[str, Any]:
    return {
        "source_type": "upload",
        "source_id": f"material-{subject}",
        "title": title,
        "text": text,
        "metadata": {
            "learner_analysis": {
                "analysis_id": f"analysis-{subject}",
                "subject": subject,
                "sub_subject": sub_subject,
                "grade_band": grade_band,
                "difficulty": difficulty,
                "confidence": 0.93,
                "concept_candidates": [
                    {"concept_id": concept_id, "label": concept_id, "confidence": 0.86}
                    for concept_id in concept_ids
                ],
                "page_evidence": [
                    {"page": index + 1, "quote": concept_id}
                    for index, concept_id in enumerate(concept_ids)
                ],
                "augmentation_decision": {"needed": False, "reason": "fixture is source-grounded"},
            }
        },
    }


def _create_pack(title: str, material: dict[str, Any]) -> dict[str, Any]:
    pack = learning_packs.create_pack(title=title, material=material, profile_id="profile-a")
    # Keep ids deterministic so evidence refs and source ids are stable.
    return learning_packs.update_pack(pack["pack_id"], {"title": title}) or pack


def _attach_artifact(pack: dict[str, Any], artifact: dict[str, Any]) -> dict[str, Any]:
    updated = learning_packs.update_pack(pack["pack_id"], {"artifact": artifact})
    assert updated is not None
    return updated


def _concept_by_id(service: PersonalizationService, subject_id: str, concept_id: str):
    profile = service.subject_profile(subject_id)
    return next(item for item in profile.concept_signals if item.concept_id == concept_id)


def _concept_ids(service: PersonalizationService, subject_id: str) -> set[str]:
    return {item.concept_id for item in service.subject_profile(subject_id).concept_signals}


def _stable_subject_state(service: PersonalizationService, subject_id: str) -> dict[str, Any]:
    profile = service.subject_profile(subject_id)
    return {
        "subject_id": profile.subject.subject_id if profile.subject else None,
        "concepts": sorted(
            {
                "concept_id": item.concept_id,
                "label": item.label,
                "support_level": item.support_level,
                "evidence_refs": item.evidence_refs,
                "mastery_probability": item.mastery_probability,
                "observation_count": item.observation_count,
                "verified_observation_count": item.verified_observation_count,
                "last_observation_source": item.last_observation_source,
            }
            for item in profile.concept_signals
        ),
    }


@pytest.mark.asyncio
async def test_multi_subject_multi_conversation_learning_business_loop(business_loop_env):
    service = business_loop_env
    math_material = _material(
        title="函数与斜率.pdf",
        text="一次函数的斜率表示变化率，截距表示与坐标轴的交点。",
        subject="mathematics",
        sub_subject="functions",
        grade_band="middle-high",
        difficulty="developing",
        concept_ids=["slope", "intercept"],
    )
    physics_material = _material(
        title="牛顿第二定律.pdf",
        text="牛顿第二定律说明合力、质量和加速度的关系。",
        subject="physics",
        sub_subject="mechanics",
        grade_band="middle-high",
        difficulty="foundation",
        concept_ids=["newton_second_law", "force"],
    )
    business_material = _material(
        title="全球运营实战手册.pdf",
        text="跨境电商进入东南亚市场时，需要协调 KOL 营销、供应链履约和本地化渠道。",
        subject="business",
        sub_subject="cross-border operations",
        grade_band="professional",
        difficulty="developing",
        concept_ids=["kol_supply_chain", "localization"],
    )

    math_pack = _attach_artifact(
        _create_pack("函数学习包", math_material),
        {
            "kind": "quiz",
            "title": "函数 Quiz",
            "verified_generation_id": "gen-math-quiz",
            "items": [
                {
                    "question_id": "math-q1",
                    "node_id": "slope",
                    "node_name": "斜率",
                    "question_type": "OPTIONS",
                    "correct_answer": "变化率",
                    "options": [
                        {"key": "A", "text": "变化率", "is_correct": True},
                        {"key": "B", "text": "面积", "is_correct": False},
                    ],
                },
                {
                    "question_id": "math-q2",
                    "node_id": "intercept",
                    "node_name": "截距",
                    "question_type": "OPTIONS",
                    "correct_answer": "与坐标轴交点",
                    "options": [
                        {"key": "A", "text": "函数最大值", "is_correct": False},
                        {"key": "B", "text": "与坐标轴交点", "is_correct": True},
                    ],
                },
            ],
        },
    )
    physics_pack = _attach_artifact(
        _create_pack("物理学习包", physics_material),
        {
            "kind": "flashcards",
            "title": "牛顿第二定律 Flashcards",
            "verified_generation_id": "gen-physics-cards",
            "items": [
                {
                    "node_id": "newton_second_law",
                    "node_name": "牛顿第二定律",
                    "front": "F=ma 说明了什么？",
                    "back": "合力等于质量乘以加速度。",
                }
            ],
        },
    )
    business_pack = _attach_artifact(
        _create_pack("跨境电商运营课件包", business_material),
        {
            "kind": "courseware",
            "title": "东南亚运营课件",
            "verified_generation_id": "gen-business-courseware",
            "markdown": "## KOL 与供应链\n直播电商需要协同 KOL 营销、库存配置和跨境履约。",
            "sections": [
                {
                    "title": "KOL 与供应链协同",
                    "content": ["先验证内容转化，再规划库存与履约节奏。"],
                }
            ],
        },
    )

    await _record_pack_learning_events(
        math_pack,
        {
            "quiz_attempt": {
                "submitted_at": "2026-08-01T09:00:00+00:00",
                "session_id": "session-math",
                "answers": {"0": "A", "1": "A"},
                "checked": [0, 1],
                "total": 2,
            }
        },
    )
    await _record_pack_learning_events(
        physics_pack,
        {
            "flashcard_progress": {"newton_second_law": "uncertain"},
            "review_id": "session-physics-review-1",
            "session_id": "session-physics",
        },
    )
    business_subject_for_event = service.classify_subject(
        material_analysis=business_material["metadata"]["learner_analysis"],
        title=business_material["title"],
        text=business_material["text"],
    )
    assert business_subject_for_event is not None
    await service.record_event(
        LearnerEvent(
            event_id=f"pack-{business_pack['pack_id']}-courseware-engaged-session-business",
            event_type="courseware_outcome",
            subject=business_subject_for_event,
            concept_id="kol_supply_chain",
            concept_label="KOL 与供应链协同",
            module_id="cross-border-operations",
            observation="engaged",
            confidence=0.8,
            evidence_refs=[f"learning-pack:{business_pack['pack_id']}", "session:session-business"],
            payload={"artifact_type": "courseware", "artifact_index": 0},
            occurred_at="2026-08-01T09:20:00+00:00",
        ),
        trusted=True,
    )

    assert {
        profile.subject.subject_id
        for profile in service.subjects()
        if profile.subject is not None
    } == {"business", "mathematics", "physics"}

    math_profile = service.subject_profile("mathematics")
    physics_profile = service.subject_profile("physics")
    business_profile = service.subject_profile("business")
    assert math_profile.subject and math_profile.subject.label == "mathematics"
    assert physics_profile.subject and physics_profile.subject.label == "physics"
    assert business_profile.subject and business_profile.subject.label == "business"

    assert _concept_ids(service, "mathematics") == {"slope", "intercept"}
    assert _concept_ids(service, "physics") == {"newton_second_law"}
    assert _concept_ids(service, "business") == {"kol_supply_chain"}

    slope = _concept_by_id(service, "mathematics", "slope")
    intercept = _concept_by_id(service, "mathematics", "intercept")
    newton = _concept_by_id(service, "physics", "newton_second_law")
    assert slope.mastery_probability > 0.2
    assert slope.support_level == "developing"
    assert slope.verified_observation_count == 1
    assert intercept.mastery_probability < 0.4
    assert intercept.support_level == "needs_support"
    assert intercept.verified_observation_count == 1
    assert newton.mastery_probability < 0.5
    assert newton.support_level == "needs_support"
    assert newton.last_observation_source == "flashcard_review"
    business_signal = _concept_by_id(service, "business", "kol_supply_chain")
    assert business_signal.mastery_probability == pytest.approx(0.2)
    assert business_signal.observation_count == 1
    assert business_signal.verified_observation_count == 0
    assert business_signal.last_observation_source == "courseware_outcome"

    math_signal_ids = {signal.signal_id for signal in service.evidence(subject_id="mathematics")}
    physics_signal_ids = {signal.signal_id for signal in service.evidence(subject_id="physics")}
    business_signal_ids = {signal.signal_id for signal in service.evidence(subject_id="business")}
    assert math_signal_ids == {
        f"pack-{math_pack['pack_id']}-quiz-math-q1-2026-08-01T09:00:00+00:00",
        f"pack-{math_pack['pack_id']}-quiz-math-q2-2026-08-01T09:00:00+00:00",
    }
    assert physics_signal_ids == {
        f"pack-{physics_pack['pack_id']}-card-newton_second_law-session-physics-review-1"
    }
    assert business_signal_ids == {f"pack-{business_pack['pack_id']}-courseware-engaged-session-business"}

    math_subject = math_profile.subject
    physics_subject = physics_profile.subject
    business_subject = business_profile.subject
    assert math_subject is not None
    assert physics_subject is not None
    assert business_subject is not None
    math_context = service.build_context(
        purpose="flashcards",
        subject=math_subject,
        current_instruction="把错题补成闪卡",
        session_id="session-math",
    )
    physics_context = service.build_context(
        purpose="quiz",
        subject=physics_subject,
        current_instruction="围绕不熟的物理概念出题",
        session_id="session-physics",
    )
    business_context = service.build_context(
        purpose="chat",
        subject=business_subject,
        current_instruction="继续解释课件里的 KOL 与供应链",
        session_id="session-business",
    )
    assert [item.concept_id for item in math_context.relevant_concept_signals] == ["intercept", "slope"]
    assert [item.concept_id for item in physics_context.relevant_concept_signals] == ["newton_second_law"]
    assert [item.concept_id for item in business_context.relevant_concept_signals] == ["kol_supply_chain"]
    assert all(item.concept_id not in {"newton_second_law", "kol_supply_chain"} for item in math_context.relevant_concept_signals)
    assert all(item.concept_id not in {"slope", "intercept", "kol_supply_chain"} for item in physics_context.relevant_concept_signals)

    inv = await build_inventory(
        _FakeChatStore(),
        session_id="session-business",
        leaf_message_id=None,
        current_turn_ordinal=3,
        fresh_attachment_records=[],
        fresh_notebook_records=[],
        fresh_book_context_text="",
        fresh_book_references=[],
        fresh_history_session_ids=[],
        fresh_question_entry_ids=[],
        fresh_learning_artifact_references=[
            {
                "pack_id": business_pack["pack_id"],
                "artifact_type": "courseware",
                "artifact_index": 0,
            }
        ],
        language="zh",
    )
    manifest, source_index = render_manifest(inv)
    assert "type=learning_artifact" in manifest
    assert "东南亚运营课件" in manifest
    assert "KOL 与供应链" in next(iter(source_index.values()))
    assert "斜率" not in manifest
    assert "牛顿第二定律" not in manifest
    assert _concept_by_id(service, "business", "kol_supply_chain").verified_observation_count == 0

    physics_before_delete = deepcopy(_stable_subject_state(service, "physics"))
    business_before_delete = deepcopy(_stable_subject_state(service, "business"))
    assert await service.delete_evidence(
        f"pack-{math_pack['pack_id']}-quiz-math-q2-2026-08-01T09:00:00+00:00"
    ) is True

    assert _stable_subject_state(service, "physics") == physics_before_delete
    assert _stable_subject_state(service, "business") == business_before_delete
    assert _concept_ids(service, "mathematics") == {"slope"}
    rebuilt_math_context = service.build_context(
        purpose="courseware",
        subject=math_subject,
        current_instruction="基于剩余证据生成课件",
        session_id="session-math",
    )
    assert [item.concept_id for item in rebuilt_math_context.relevant_concept_signals] == ["slope"]
