"""Learning-pack APIs shared by courseware, card, and quiz pages."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import logging
from typing import Any, Literal, cast
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from traittutor import learning_packs
from traittutor.generate.image_material import image_ocr_capability
from traittutor.generate.material_analysis import load_material_analysis
from traittutor.generate.runner import (
    GenerationConfigurationError,
    GenerationModelExhaustedError,
    GenerationStructuredOutputExhaustedError,
)
from traittutor.generate.tasks import get_generation_task_manager
from traittutor.learning.event_chain import (
    CanonicalAnswerEventChain,
    stable_answer_identity,
)
from traittutor.learning.grading import classify_error, grade_answer
from traittutor.learning.intent import scan_untrusted_learning_payload
from traittutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
)
from traittutor.learning.service import (
    LearningService,
    project_canonical_event_to_existing_progress,
)
from traittutor.learning.storage import LearningStore
from traittutor.learning_components import (
    _EVIDENCE_ASSESSMENT_COMPONENT_TYPES,
    LearningComponentPlan,
    arrange_learning_component_plan,
    build_calibrated_followup_plan,
    build_learning_component_plan,
    judge_and_generate_pre_assessment,
)
from traittutor.learning_model.events import is_strong_evidence
from traittutor.learning_support import build_progress_calibration, calibration_record, due_reviews
from traittutor.multi_user.context import get_current_user
from traittutor.personalization.models import SubjectRef
from traittutor.services.path_service import get_path_service

router = APIRouter()
logger = logging.getLogger(__name__)


def _reject_unsafe_learning_payload(payload: object) -> None:
    """Keep direct Pack APIs behind the same input boundary as Learn."""
    action, _category = scan_untrusted_learning_payload(payload)
    if action == "block":
        raise HTTPException(
            status_code=422,
            detail="Please remove instruction-like content and describe only the learning goal or source.",
        )


class CreatePackRequest(BaseModel):
    title: str = ""
    material: dict[str, Any] = Field(default_factory=dict)
    profile_id: str | None = None
    goal: dict[str, Any] | str | None = None
    sources: list[dict[str, Any]] = Field(default_factory=list)


class UpdatePackRequest(BaseModel):
    title: str | None = None
    persona: str | None = None
    profile_id: str | None = None
    # Learner's explicit choice on the Learn intermediate page: may the LLM
    # auto-select this path's components? "basic" suppresses the canvas's
    # "arrangement still pending" notice because the opt-out is deliberate.
    arrangement_preference: Literal["auto", "basic"] | None = None
    artifact: dict[str, Any] | None = None
    generation_id: str | None = Field(default=None, min_length=1, max_length=128)
    flashcard_progress: dict[str, Any] | None = None
    review_id: str | None = Field(default=None, min_length=1, max_length=128)
    quiz_attempt: dict[str, Any] | None = None
    goal: dict[str, Any] | str | None = None
    sources: list[dict[str, Any]] | None = None
    source: dict[str, Any] | None = None


class PackMaterialInput(BaseModel):
    """Learner-supplied material reference; identity and hash stay server-owned."""

    model_config = ConfigDict(extra="allow")

    source_type: str = Field(min_length=1, max_length=40)
    source_id: str | None = Field(default=None, max_length=240)
    title: str = Field(default="Learning source", max_length=240)
    text: str = Field(default="", max_length=250_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendPackMaterialRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    material: PackMaterialInput


class RemovePackMaterialRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class ReorderPackMaterialsRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)
    material_ids: list[str] = Field(max_length=100)


class DeletePacksRequest(BaseModel):
    pack_ids: list[str] = Field(min_length=1, max_length=100)


class CreateLearningPlanRequest(BaseModel):
    instruction: str = Field(default="", max_length=1200)
    preferred_modalities: list[Literal["text", "visual", "audio", "interactive"]] = Field(
        default_factory=list, max_length=4
    )
    accessibility: dict[str, Any] = Field(default_factory=dict)
    supersedes_plan_id: str | None = Field(default=None, max_length=128)


class CreatePackWithPlanRequest(CreatePackRequest):
    idempotency_key: str = Field(min_length=1, max_length=128)
    plan: CreateLearningPlanRequest = Field(default_factory=CreateLearningPlanRequest)


class PreAssessmentAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=160)
    selected_index: int = Field(ge=0, le=5)
    # Deprecated learner-facing confidence control. The human product decision
    # removed the confidence picker from the starting-point check; the field is
    # kept optional so older clients and stored responses remain readable.
    confidence: Literal["低", "中", "高"] | None = None


class SubmitPreAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[PreAssessmentAnswer] = Field(min_length=1, max_length=5)
    event_id: str | None = Field(default=None, min_length=1, max_length=128)


class BindLearningPathRequest(BaseModel):
    """Select an already-owned progress graph for one Pack explicitly."""

    learning_path_id: str = Field(min_length=1, max_length=160)
    # Omission means the complete persisted module graph.  A supplied subset
    # remains useful for a Pack that only owns assessment items for those KCs.
    allowed_kc_ids: list[str] | None = Field(default=None, max_length=256)


class ComponentInteractionRequest(BaseModel):
    event_id: str | None = Field(default=None, max_length=128)
    action: Literal["start", "complete", "skip", "retry", "degrade", "feedback"]
    observation: Literal["correct", "incorrect", "known", "uncertain", "unknown"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    question_id: str | None = Field(default=None, max_length=160)
    answer: str | None = Field(default=None, max_length=4000)
    concept_id: str | None = Field(default=None, max_length=160)
    concept_label: str | None = Field(default=None, max_length=160)
    output_ref: str | None = Field(default=None, max_length=240)
    media_url: str | None = Field(default=None, max_length=500)
    feedback: str | None = Field(default=None, max_length=600)
    occurred_at: str | None = Field(default=None, max_length=80)
    replan: bool = True


class RepairRetryRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    answer: str = Field(min_length=1, max_length=4000)


class ReviewResultRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    answer: str | None = Field(default=None, max_length=4000)
    rating: Literal["known", "uncertain", "unknown"] | None = None


class AssessmentAttemptView(BaseModel):
    """Owner-only, post-submission projection of one Pack assessment attempt."""

    attempt_id: str
    component_id: str
    question_id: str
    generated_result_id: str = ""
    user_answer: str = ""
    confidence: float | None = None
    correct: bool
    reference_answer: str | None = None
    explanation: str | None = None
    submitted_at: str
    read_only: bool = True
    historical_explanation_available: bool = False


def _dict_field(container: Mapping[str, Any], key: str) -> dict[Any, Any]:
    """Return one JSON object field with a type-narrowed empty fallback."""
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _list_field(container: Mapping[str, Any], key: str) -> list[Any]:
    """Return one JSON array field with a type-narrowed empty fallback."""
    value = container.get(key)
    return value if isinstance(value, list) else []


def _assessment_attempt_views(
    pack: Mapping[str, Any],
    plan_id: str,
) -> list[AssessmentAttemptView]:
    """Join submitted events to exact artifacts across one immutable plan chain."""
    plans = {
        str(item.get("plan_id") or ""): item
        for item in _list_field(pack, "component_plans")
        if isinstance(item, Mapping) and str(item.get("plan_id") or "")
    }
    plan = plans.get(plan_id)
    if plan is None:
        return []
    components = {
        str(item.get("component_id") or ""): item
        for item in _list_field(plan, "components")
        if isinstance(item, Mapping)
    }
    artifacts = {
        str(item.get("verified_generation_id") or ""): item
        for item in _list_field(_dict_field(pack, "artifacts"), "quiz")
        if isinstance(item, Mapping)
    }
    lineage: list[Mapping[str, Any]] = []
    seen_plan_ids: set[str] = set()
    cursor: Mapping[str, Any] | None = plan
    while cursor is not None:
        cursor_id = str(cursor.get("plan_id") or "")
        if not cursor_id or cursor_id in seen_plan_ids:
            break
        lineage.append(cursor)
        seen_plan_ids.add(cursor_id)
        parent_id = str(cursor.get("supersedes_plan_id") or "").strip()
        cursor = plans.get(parent_id) if parent_id else None
    all_progress = _dict_field(pack, "component_progress")
    views: list[AssessmentAttemptView] = []
    seen_attempt_ids: set[str] = set()
    events = [
        event
        for lineage_plan in lineage
        for event in _list_field(
            _dict_field(all_progress, str(lineage_plan.get("plan_id") or "")), "events"
        )
    ]
    for event in events:
        if not isinstance(event, Mapping):
            continue
        observation = str(event.get("observation") or "")
        question_id = str(event.get("question_id") or "").strip()
        attempt_id = str(event.get("event_id") or "").strip()
        component_id = str(event.get("component_id") or "").strip()
        if (
            observation not in {"correct", "incorrect"}
            or not question_id
            or not attempt_id
            or attempt_id in seen_attempt_ids
        ):
            continue
        output_ref = str(event.get("output_ref") or "").strip()
        component = components.get(component_id)
        artifact = None
        item = None
        if (
            component is not None
            and output_ref
            and str(component.get("output_ref") or "") == output_ref
        ):
            artifact = artifacts.get(output_ref)
        if artifact is not None:
            item = next(
                (
                    candidate
                    for candidate in _list_field(artifact, "items")
                    if isinstance(candidate, Mapping)
                    and str(candidate.get("question_id") or "") == question_id
                ),
                None,
            )
        reference_answer = str(item.get("correct_answer") or "") if item else ""
        explanation = str(item.get("explanation") or "") if item else ""
        raw_confidence = event.get("confidence")
        views.append(
            AssessmentAttemptView(
                attempt_id=attempt_id,
                component_id=component_id,
                question_id=question_id,
                generated_result_id=output_ref,
                user_answer=str(event.get("answer") or ""),
                confidence=(
                    float(raw_confidence) if isinstance(raw_confidence, int | float) else None
                ),
                correct=observation == "correct",
                reference_answer=reference_answer or None,
                explanation=explanation or None,
                submitted_at=str(event.get("occurred_at") or ""),
                historical_explanation_available=bool(reference_answer or explanation),
            )
        )
        seen_attempt_ids.add(attempt_id)
    views.sort(key=lambda value: value.submitted_at, reverse=True)
    return views


def _public_learning_path_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the selected graph contract, never its owner-internal token."""
    return {
        key: binding[key]
        for key in (
            "binding_id",
            "revision",
            "learning_path_id",
            "subject_id",
            "allowed_kc_ids",
            "graph_fingerprint",
            "graph_version",
            "linked_at",
            "status",
        )
        if key in binding
    }


def _learning_graph_snapshot(progress: Any) -> tuple[str, list[str]]:
    """Fingerprint the persisted module graph the binding is allowed to use."""
    modules: list[dict[str, Any]] = [
        {
            "module_id": str(module.id),
            "knowledge_points": [
                {"id": str(kp.id), "type": str(kp.type.value)} for kp in module.knowledge_points
            ],
        }
        for module in progress.modules
    ]
    all_kcs = sorted({kp["id"] for module in modules for kp in module["knowledge_points"]})
    body = json.dumps(
        {
            "learning_path_id": progress.book_id,
            "subject_id": progress.subject_id,
            "modules": modules,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest(), all_kcs


def _trusted_pack_material_analyses(pack: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reload material analysis from the owner-bound server store.

    The copy embedded in a Pack arrived through an HTTP request and is useful
    for display only.  It must never become the authority for subject/KC
    attribution, even though the Learn UI originally obtained it from the
    server.
    """
    materials = _list_field(pack, "materials")
    if not materials or not isinstance(materials[0], Mapping):
        return []
    metadata = _dict_field(materials[0], "metadata")
    session_id = str(metadata.get("learning_session_id") or "").strip()
    embedded = metadata.get("learner_analyses")
    candidates = embedded if isinstance(embedded, list) else [metadata.get("learner_analysis")]
    source_materials = metadata.get("source_materials")
    allowed_source_ids = {
        str(item.get("source_id") or "").strip()
        for item in (source_materials if isinstance(source_materials, list) else [])
        if isinstance(item, Mapping) and str(item.get("source_id") or "").strip()
    }
    primary_source_id = str(materials[0].get("source_id") or "").strip()
    if primary_source_id:
        allowed_source_ids.add(primary_source_id)
    trusted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        analysis_id = str(candidate.get("analysis_id") or "").strip()
        candidate_session_id = str(candidate.get("session_id") or session_id).strip()
        if not analysis_id or not candidate_session_id or analysis_id in seen:
            continue
        try:
            analysis = load_material_analysis(
                analysis_id,
                candidate_session_id,
                enforce_owner=True,
            )
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            continue
        source_id = str(analysis.get("source_id") or "").strip()
        if allowed_source_ids and source_id not in allowed_source_ids:
            continue
        trusted.append(analysis)
        seen.add(analysis_id)
    return trusted


def _pack_learning_modules(
    analyses: list[dict[str, Any]], *, subject_id: str
) -> list[LearningModule]:
    """Build temporary, chunk-grounded KCs from trusted analysis snapshots."""
    grouped: dict[str, dict[str, tuple[str, KnowledgeType]]] = {}
    assigned_kcs: set[str] = set()
    for analysis in analyses:
        if str(analysis.get("subject") or "").strip() != subject_id:
            continue
        for candidate in _list_field(analysis, "concept_candidates"):
            if not isinstance(candidate, Mapping):
                continue
            module_id = str(candidate.get("module_id") or subject_id).strip() or subject_id
            label = str(candidate.get("label") or candidate.get("name") or "Material concept")
            raw_type = str(candidate.get("knowledge_type") or "memory").strip().lower()
            try:
                knowledge_type = KnowledgeType(raw_type)
            except ValueError:
                knowledge_type = KnowledgeType.MEMORY
            candidate_id = str(candidate.get("concept_id") or "").strip()
            ids: list[str] = []
            evidence_ids = candidate.get("evidence_chunk_ids")
            if isinstance(evidence_ids, list):
                ids.extend(str(value).strip() for value in evidence_ids)
            source_refs = candidate.get("source_refs")
            if isinstance(source_refs, list):
                ids.extend(str(value).strip() for value in source_refs)
            # Model-authored concept labels remain candidates.  Only explicit
            # source handles (or the abstraction layer's marked temporary
            # chunk handle) are reliable enough to become a BKT partition.
            if candidate.get("temporary") is True or candidate_id in ids:
                ids.append(candidate_id)
            module = grouped.setdefault(module_id, {})
            for kc_id in ids:
                if kc_id and kc_id not in assigned_kcs:
                    module.setdefault(kc_id, (label[:160] or kc_id, knowledge_type))
                    assigned_kcs.add(kc_id)
        # Classification evidence is also a server-validated chunk handle and
        # covers low-detail analyses whose model omitted concept candidates.
        for evidence in _list_field(analysis, "evidence"):
            if not isinstance(evidence, Mapping):
                continue
            kc_id = str(evidence.get("chunk_id") or "").strip()
            if kc_id and kc_id not in assigned_kcs:
                grouped.setdefault(subject_id, {}).setdefault(
                    kc_id, (str(evidence.get("excerpt") or kc_id)[:160], KnowledgeType.MEMORY)
                )
                assigned_kcs.add(kc_id)
    modules: list[LearningModule] = []
    for order, (module_id, entries) in enumerate(grouped.items(), start=1):
        points = [
            KnowledgePoint(id=kc_id, name=name, type=knowledge_type, module_id=module_id)
            for kc_id, (name, knowledge_type) in entries.items()
        ]
        if points:
            modules.append(
                LearningModule(
                    id=module_id,
                    name=module_id.replace("_", " "),
                    order=order,
                    knowledge_points=points,
                )
            )
    return modules


def _ensure_initial_learning_path_binding(
    pack: dict[str, Any], plan: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Idempotently bind a Learn Pack to its trusted temporary KC graph."""
    owner_id = get_current_user().id
    existing = learning_packs.active_learning_path_binding(pack, owner_id=owner_id)
    if existing is not None:
        return existing
    analyses = _trusted_pack_material_analyses(pack)
    if not analyses:
        return None
    primary = analyses[0]
    subject_id = str(primary.get("subject") or "").strip()
    confidence = float(primary.get("confidence") or 0)
    raw_subject = plan.get("subject_ref")
    plan_subject_id = ""
    if isinstance(raw_subject, Mapping):
        try:
            plan_subject_id = SubjectRef.model_validate(raw_subject).subject_id.strip()
        except (TypeError, ValueError):
            return None
    if not subject_id or confidence < 0.65 or plan_subject_id != subject_id:
        return None
    modules = _pack_learning_modules(analyses, subject_id=subject_id)
    if not modules:
        return None
    learning_path_id = f"pack-{pack['pack_id']}"
    store = LearningStore(owner_id=owner_id)
    progress = store.load(learning_path_id)
    if progress is None:
        progress = LearningProgress(
            book_id=learning_path_id,
            subject_id=subject_id,
            modules=modules,
            current_module_id=modules[0].id,
            knowledge_types={
                point.id: point.type for module in modules for point in module.knowledge_points
            },
        )
        store.save(progress)
    elif progress.subject_id != subject_id:
        return None
    graph_fingerprint, graph_kc_ids = _learning_graph_snapshot(progress)
    result = learning_packs.create_learning_path_binding(
        str(pack["pack_id"]),
        owner_id=owner_id,
        learning_path_id=progress.book_id,
        subject_id=subject_id,
        allowed_kc_ids=graph_kc_ids,
        graph_fingerprint=graph_fingerprint,
        graph_version=progress.version,
    )
    return result[0] if result is not None else None


def _subject_id_from_server_item(
    item: Mapping[str, Any], *, artifact: Mapping[str, Any] | None = None
) -> str:
    """Read subject only from the server-held item/artifact, never a classifier.

    A Pack-level material classification is a teaching cue, not an immutable
    assessment partition.  Missing explicit item/artifact subject therefore
    stays attribution-pending even when its answer key is private.
    """
    for source in (item, artifact or {}):
        raw = source.get("subject_ref")
        if isinstance(raw, Mapping):
            try:
                ref = SubjectRef.model_validate(raw)
            except (TypeError, ValueError):
                continue
            if ref.confirmed:
                return ref.subject_id.strip()
        value = str(source.get("subject_id") or "").strip()
        if value:
            return value
    return ""


def _bound_pack_assessment_target(
    pack: Mapping[str, Any],
    *,
    owner_id: str,
    subject_id: str,
    kc_id: str,
) -> dict[str, Any] | None:
    """Accept only an exact active binding/server-item partition match."""
    binding = learning_packs.active_learning_path_binding(dict(pack), owner_id=owner_id)
    if binding is None:
        return None
    if subject_id.strip() != str(binding.get("subject_id") or ""):
        return None
    if kc_id.strip() not in {str(value).strip() for value in binding.get("allowed_kc_ids") or []}:
        return None
    return binding


def _learner_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Return a learner-facing Pack without server-owned repair answer keys."""
    public = deepcopy(pack)
    public.pop("_initial_create_idempotency_key", None)
    public.pop("_initial_create_request_fingerprint", None)
    public.pop("_initial_create_plan_id", None)
    # Bindings include owner IDs plus prior graph snapshots for audit and must
    # never be copied wholesale into the browser payload.  The authenticated
    # learner can query the current contract through its dedicated endpoint.
    public.pop("learning_path_bindings", None)
    public.pop("active_learning_path_binding_revision", None)
    public.pop("review_attempts", None)
    # Revision snapshots retain removed material for explicit recovery.  Keep
    # them off ordinary list/detail responses and expose one requested revision
    # through the dedicated owner-scoped endpoint below.
    public.pop("material_revisions", None)
    if isinstance(public.get("pre_assessment"), dict):
        public["pre_assessment"] = _public_pre_assessment_state(public["pre_assessment"])
    artifacts = _dict_field(public, "artifacts")
    for artifact in artifacts.get("flashcards") or []:
        if not isinstance(artifact, dict):
            continue
        for item in artifact.get("items") or []:
            if not isinstance(item, dict):
                continue
            item.pop("back", None)
            item.pop("answer", None)
    for artifact in artifacts.get("quiz") or []:
        if not isinstance(artifact, dict):
            continue
        for item in artifact.get("items") or []:
            if not isinstance(item, dict):
                continue
            item.pop("correct_answer", None)
            item.pop("is_correct", None)
            item.pop("explanation", None)
            for option in item.get("options") or []:
                if isinstance(option, dict):
                    option.pop("is_correct", None)
    public["repairs"] = [
        _public_repair(repair, reveal_content=False)
        for repair in public.get("repairs") or []
        if isinstance(repair, dict)
    ]
    reviews = due_reviews(public)
    public["due_review_count"] = len(reviews)
    future_dates = [
        str(item.get("due_at"))
        for item in public.get("review_states") or []
        if isinstance(item, dict) and item.get("due_at")
    ]
    public["next_review_at"] = min(future_dates) if future_dates else None
    return public


def _public_pre_assessment_state(assessment: Mapping[str, Any]) -> dict[str, Any]:
    """Expose lifecycle and questions while retaining all answer keys server-side."""
    questions = [
        {
            key: probe[key]
            for key in ("question_id", "concept_id", "concept_label", "question", "options")
            if key in probe
        }
        for probe in assessment.get("probes") or []
        if isinstance(probe, Mapping)
    ]
    return {
        "assessment_id": assessment.get("assessment_id"),
        "status": assessment.get("status"),
        "created_at": assessment.get("created_at"),
        "updated_at": assessment.get("updated_at"),
        "questions": questions,
    }


def _pre_assessment_response(assessment: Mapping[str, Any]) -> dict[str, Any]:
    if assessment.get("status") == "not_needed":
        return {"needed": False}
    public = _public_pre_assessment_state(assessment)
    # The confidence picker was removed from the starting-point check by
    # product decision; questions are answer-only now.
    return {
        "needed": assessment.get("status") != "not_needed",
        "assessment_id": public["assessment_id"],
        "questions": public["questions"],
        "status": public["status"],
    }


def _public_repair(repair: Mapping[str, Any], *, reveal_content: bool) -> dict[str, Any]:
    """Project repair metadata, revealing teaching content only on demand."""
    public = deepcopy(dict(repair))
    # Canonical provenance, answer keys, and compact idempotency receipts are
    # server-only. They never form part of a browser grading contract.
    for key in (
        "retry_expected_answer",
        "canonical_source_event_id",
        "review_owner_id",
        "review_subject_id",
        "review_kc_id",
        "retry_question_id",
        "retry_event_receipts",
    ):
        public.pop(key, None)
    if not reveal_content:
        for key in (
            "user_answer",
            "correct_rule",
            "contrast",
            "retry_prompt",
            "retry_options",
        ):
            public.pop(key, None)
    return public


def _public_material_revision(
    pack_id: str, revision: Mapping[str, Any], *, replayed: bool = False
) -> dict[str, Any]:
    """Expose material order/content without idempotency fingerprints."""
    return {
        "pack_id": pack_id,
        "revision": revision.get("revision"),
        "operation": revision.get("operation"),
        "material_ids": list(revision.get("material_ids") or []),
        "materials": deepcopy(list(revision.get("materials") or [])),
        "created_at": revision.get("created_at"),
        "idempotent_replay": replayed,
    }


def _raise_material_mutation_error(exc: Exception) -> None:
    if isinstance(exc, learning_packs.MaterialRevisionConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "material_revision_conflict",
                "expected_revision": exc.expected_revision,
                "actual_revision": exc.actual_revision,
            },
        ) from exc
    if isinstance(exc, learning_packs.MaterialIdempotencyConflict):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "material_idempotency_conflict",
                "message": str(exc),
            },
        ) from exc
    if isinstance(exc, learning_packs.InvalidPackMaterialOperation):
        code = (
            "material_capability_unavailable"
            if str(exc) == "image_ocr_unavailable"
            else "invalid_material_operation"
        )
        detail: dict[str, Any] = {"code": code, "message": str(exc)}
        if code == "material_capability_unavailable":
            detail["capability"] = "image_ocr"
        raise HTTPException(status_code=422, detail=detail) from exc
    raise exc


def _retrieval_review_card(pack: Mapping[str, Any], review: Mapping[str, Any]) -> Mapping[str, Any]:
    artifacts = _dict_field(pack, "artifacts")
    concept_id = str(review.get("concept_id") or "")
    return next(
        (
            item
            for artifact in artifacts.get("flashcards") or []
            if isinstance(artifact, Mapping)
            for item in artifact.get("items") or []
            if isinstance(item, Mapping)
            and str(item.get("node_id") or item.get("concept_id") or "") == concept_id
        ),
        {},
    )


def _learner_due_reviews(pack: dict[str, Any]) -> list[dict[str, Any]]:
    """Attach learner-safe prompts to the bounded server-owned due queue."""
    result: list[dict[str, Any]] = []
    for review in due_reviews(pack):
        public = dict(review)
        if review.get("source") == "repair":
            repair_id = str(review.get("review_id") or "").removeprefix("review-repair-")
            repair: dict[str, Any] = next(
                (
                    item
                    for item in _list_field(pack, "repairs")
                    if isinstance(item, dict) and item.get("repair_id") == repair_id
                ),
                {},
            )
            public.update(
                {
                    "prompt": repair.get("retry_prompt"),
                    "question_type": repair.get("retry_question_type") or "short",
                    "options": repair.get("retry_options") or [],
                }
            )
        elif review.get("source") == "retrieval":
            concept_id = str(review.get("concept_id") or "")
            card = _retrieval_review_card(pack, review)
            public.update(
                {
                    "prompt": card.get("front") or card.get("question") or concept_id,
                }
            )
        result.append(public)
    return result


def _canonical_repair_provenance(
    *,
    chain: CanonicalAnswerEventChain | None,
    user_id: str,
    component_attempt_id: str,
    subject_id: str,
    kc_id: str,
) -> tuple[str, str, str]:
    """Return only source provenance that is already canonical and strong.

    A repair is created from a server-verified Component interaction, but a
    component request ID is merely a boundary-issued attempt token.  Persisting
    it as if it were a canonical event would let a later review manufacture
    BKT provenance.  Recover the chain's deterministic event ID and verify its
    owner/partition before retaining it for the repair review.
    """
    normalized_subject = subject_id.strip()
    normalized_kc = kc_id.strip()
    if chain is None or not component_attempt_id.strip():
        return "", normalized_subject, normalized_kc
    source_event_id, _ = stable_answer_identity(
        user_id=user_id,
        attempt_id=component_attempt_id,
    )
    source = chain.ledger.get(source_event_id)
    if (
        source is None
        or source.user_id != user_id
        or source.subject_id != normalized_subject
        or normalized_kc not in source.kc_ids
        or not is_strong_evidence(source)
    ):
        return "", normalized_subject, normalized_kc
    return source.event_id, normalized_subject, normalized_kc


def _verified_repair_source_binding(
    *,
    pack_id: str,
    source: Any,
    user_id: str,
    subject_id: str,
    kc_id: str,
) -> str | None:
    """Return a path only when the repair source still has an explicit link.

    Repair and review routes do not own a path association.  They may reuse a
    prior canonical assessment only when that immutable source event names the
    Pack's current owner-held binding exactly.  Historic Pack-ID-as-path
    records are intentionally left pending rather than replayed into BKT.
    """
    pack = learning_packs.get_pack(pack_id)
    if pack is None or source is None:
        return None
    binding = learning_packs.active_learning_path_binding(pack, owner_id=user_id)
    if binding is None:
        return None
    path_id = str(binding.get("learning_path_id") or "")
    allowed_kcs = {str(value).strip() for value in binding.get("allowed_kc_ids") or []}
    if (
        not path_id
        or source.user_id != user_id
        or source.subject_id != subject_id
        or kc_id not in source.kc_ids
        or source.learning_path_id != path_id
        or str(binding.get("subject_id") or "") != subject_id
        or kc_id not in allowed_kcs
    ):
        return None
    return path_id


def _record_canonical_repair_review_evidence(
    *,
    pack_id: str,
    review_id: str,
    repair: Mapping[str, Any],
    correct: bool,
    attempt_id: str,
    user_id: str,
    chain: CanonicalAnswerEventChain,
):
    """Write a repair-review event before changing its legacy schedule.

    Only a repair owns an answer key.  Retrieval ratings and imported quiz
    reviews deliberately never enter here because their apparent correctness
    is learner-reported.  A strong review additionally requires its stored
    subject/KC to agree with the original canonical assessment event; missing
    or mismatched provenance stays immutable but cannot influence BKT.
    """
    source_event_id = str(repair.get("canonical_source_event_id") or "").strip()
    owner_id = str(repair.get("review_owner_id") or "").strip()
    subject_id = str(repair.get("review_subject_id") or "").strip()
    kc_id = str(repair.get("review_kc_id") or "").strip()
    question_id = str(repair.get("retry_question_id") or "").strip()
    expected_answer = str(repair.get("retry_expected_answer") or "").strip()
    source = chain.ledger.get(source_event_id) if source_event_id else None
    base_provenance_matches = bool(
        source is not None
        and owner_id == user_id
        and source.user_id == user_id
        and source.subject_id == subject_id
        and kc_id in source.kc_ids
        and is_strong_evidence(source)
    )
    learning_path_id = (
        _verified_repair_source_binding(
            pack_id=pack_id,
            source=source,
            user_id=user_id,
            subject_id=subject_id,
            kc_id=kc_id,
        )
        if base_provenance_matches
        else None
    )
    distinct_unrevealed_item = bool(
        question_id and question_id != str(repair.get("question_id") or "").strip()
    )
    attribution_reliable = learning_path_id is not None and distinct_unrevealed_item
    event, _outcome = chain.record_server_graded(
        user_id=user_id,
        subject_id=subject_id if attribution_reliable else "",
        question_id=question_id,
        kc_ids=(kc_id,) if kc_id else (),
        is_correct=correct,
        # `correct` comes only from the private Pack grade above.  Still
        # require the exact retried item/key to be present before treating the
        # record as a valid assessment item.
        item_valid=bool(question_id and expected_answer),
        attribution_reliable=attribution_reliable,
        derived=lambda _event: None,
        attempt_id=attempt_id,
        surface_type="review",
        learning_path_id=learning_path_id,
        module_id=review_id,
    )
    return event


def _record_canonical_repair_retry_evidence(
    *,
    pack_id: str,
    repair_id: str,
    repair: Mapping[str, Any],
    correct: bool,
    attempt_id: str,
    user_id: str,
    chain: CanonicalAnswerEventChain,
):
    """Persist a server-graded repair retry before its legacy state changes.

    The private repair record, not the request, owns both the expected answer
    and the partition/source provenance.  Any missing or mismatched owner,
    subject, KC, or source event is still retained as an immutable weak event
    but cannot become BKT evidence.
    """
    source_event_id = str(repair.get("canonical_source_event_id") or "").strip()
    owner_id = str(repair.get("review_owner_id") or "").strip()
    subject_id = str(repair.get("review_subject_id") or "").strip()
    kc_id = str(repair.get("review_kc_id") or "").strip()
    question_id = str(repair.get("retry_question_id") or "").strip()
    expected_answer = str(repair.get("retry_expected_answer") or "").strip()
    source = chain.ledger.get(source_event_id) if source_event_id else None
    base_provenance_matches = bool(
        source is not None
        and owner_id == user_id
        and source.user_id == user_id
        and source.subject_id == subject_id
        and kc_id in source.kc_ids
        and is_strong_evidence(source)
    )
    learning_path_id = (
        _verified_repair_source_binding(
            pack_id=pack_id,
            source=source,
            user_id=user_id,
            subject_id=subject_id,
            kc_id=kc_id,
        )
        if base_provenance_matches
        else None
    )
    distinct_unrevealed_item = bool(
        question_id and question_id != str(repair.get("question_id") or "").strip()
    )
    attribution_reliable = learning_path_id is not None and distinct_unrevealed_item
    return chain.record_server_graded(
        user_id=user_id,
        subject_id=subject_id if attribution_reliable else "",
        question_id=question_id,
        kc_ids=(kc_id,) if kc_id else (),
        is_correct=correct,
        item_valid=bool(question_id and expected_answer),
        attribution_reliable=attribution_reliable,
        derived=lambda _event: None,
        # Namespace the browser token by its immutable retry target so one
        # shared client request ID cannot collide with another answer surface.
        attempt_id=f"repair-retry:{pack_id}:{repair_id}:{attempt_id}",
        surface_type="practice",
        learning_path_id=learning_path_id,
        module_id=repair_id,
    )


def _is_output_reference_persistence_event(request: ComponentInteractionRequest) -> bool:
    """Allow a generated artifact to be durably referenced before answering.

    Assessment components must not require an answer merely to save the task
    identity needed for refresh/reconnect.  This narrow feedback form is not a
    grading event: it cannot carry an observation or answer and therefore
    cannot update BKT or complete the component.
    """
    return (
        request.action == "feedback"
        and bool(request.output_ref)
        and request.answer is None
        and request.observation is None
    )


def _validate_component_output_reference(request: ComponentInteractionRequest) -> None:
    if not _is_output_reference_persistence_event(request):
        return
    task = get_generation_task_manager().get(str(request.output_ref))
    if task is None or task.status not in {"completed", "needs_review"}:
        raise HTTPException(
            status_code=422, detail="A completed or review-required generation output is required"
        )


def _validate_assessment_output_attachment(
    pack: dict[str, Any],
    component: dict[str, Any],
    request: ComponentInteractionRequest,
) -> None:
    """Reject a released assessment reference that is not yet gradable.

    The generated task and the Pack artifact are separate durable records. A
    component must never expose a completed Quiz by ``output_ref`` before the
    same server-owned artifact is attached to the Pack, otherwise rendering
    works while answer verification cannot resolve the answer key.
    """

    if not _is_output_reference_persistence_event(request):
        return
    if component.get("component_type") not in {
        "diagnostic_check",
        "guided_practice",
        "transfer_challenge",
    }:
        return
    task = get_generation_task_manager().get(str(request.output_ref))
    if task is None or task.status != "completed":
        # A review-required task is intentionally unreleased and can retain a
        # pointer while waiting for confirmation.
        return
    if _verified_assessment_artifact(pack, request) is None:
        raise HTTPException(
            status_code=422,
            detail="Attach the completed Quiz artifact before binding it to an assessment",
        )


def _trusted_audio_media_url(
    component: dict[str, Any], request: ComponentInteractionRequest
) -> str | None:
    """Accept only the audio file emitted by this user's generation workspace.

    ``media_url`` is supplied by the browser after it receives the TTS response,
    but it must never be persisted as an arbitrary external link.
    """
    if request.media_url is None:
        return None
    if component.get("executor") != "audio" or not request.output_ref:
        raise HTTPException(
            status_code=422, detail="Audio media requires an audio generation output"
        )
    prefix = "/api/outputs/"
    if not request.media_url.startswith(prefix):
        raise HTTPException(status_code=422, detail="Audio media URL is not a TraitTutor output")
    service = get_path_service()
    root = service.get_public_outputs_root().resolve()
    candidate = (root / unquote(request.media_url[len(prefix) :])).resolve()
    expected_dir = service.get_task_workspace("chat", f"traittutor-{request.output_ref}") / "media"
    if (
        candidate.parent != expected_dir.resolve()
        or not candidate.name.startswith("learning-audio.")
        or not service.is_public_output_path(candidate)
    ):
        raise HTTPException(status_code=422, detail="Audio media is not owned by this generation")
    return request.media_url


def _checked_quiz_indexes(values: Any) -> set[int] | None:
    if not isinstance(values, list):
        return None
    indexes: set[int] = set()
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            indexes.add(value)
            continue
        text = str(value).strip()
        if text.isdigit():
            indexes.add(int(text))
    return indexes


async def _record_pack_learning_events(
    pack: dict[str, Any],
    patch: dict[str, Any],
    *,
    chain: CanonicalAnswerEventChain | None = None,
    learning_service: LearningService | None = None,
) -> None:
    """Record server-graded Pack Quiz answers through the canonical ledger."""
    attempt = patch.get("quiz_attempt")
    if not isinstance(attempt, dict):
        return
    pack_id = str(pack.get("pack_id") or "")
    artifacts = _dict_field(pack, "artifacts")

    quizzes = _list_field(artifacts, "quiz")
    if not quizzes or not isinstance(quizzes[-1], dict):
        return
    artifact = quizzes[-1]
    if not artifact.get("verified_generation_id"):
        return
    answers = _dict_field(attempt, "answers")
    attempt_ids = _dict_field(attempt, "attempt_ids")
    checked_indexes = _checked_quiz_indexes(attempt.get("checked"))
    submitted = str(attempt.get("submitted_at") or pack.get("updated_at") or "")
    for index, item in enumerate(_list_field(artifact, "items")):
        if not isinstance(item, dict):
            continue
        answer = str(answers.get(str(index), answers.get(index, ""))).strip()
        if not answer or (checked_indexes is not None and index not in checked_indexes):
            continue
        expected = str(item.get("correct_answer") or "").strip()
        selected = next(
            (
                option
                for option in _list_field(item, "options")
                if isinstance(option, dict)
                and answer
                in {
                    str(option.get("text") or "").strip(),
                    str(option.get("key") or option.get("id") or "").strip(),
                }
            ),
            None,
        )
        correct = (
            bool(selected.get("is_correct"))
            if isinstance(selected, dict)
            else bool(
                expected
                and grade_answer(answer, expected, str(item.get("question_type") or "short"))
            )
        )
        question_id = str(item.get("question_id", index))
        event_id = f"pack-{pack_id}-quiz-{question_id}-{submitted}"
        server_kc_id = str(item.get("node_id") or "").strip()
        server_subject_id = _subject_id_from_server_item(item, artifact=artifact)
        active_binding = learning_packs.active_learning_path_binding(
            pack, owner_id=get_current_user().id
        )
        if not server_subject_id and active_binding is not None:
            server_subject_id = str(active_binding.get("subject_id") or "").strip()
        binding = _bound_pack_assessment_target(
            pack,
            owner_id=get_current_user().id,
            subject_id=server_subject_id,
            kc_id=server_kc_id,
        )

        def project_bound_event(recorded: Any, *, binding: Any = binding) -> None:
            if binding is not None:
                project_canonical_event_to_existing_progress(recorded, service=learning_service)

        base_attempt_id = str(attempt.get("attempt_id") or submitted or event_id)
        question_attempt_id = str(attempt_ids.get(str(index), attempt_ids.get(index, ""))).strip()
        (chain or CanonicalAnswerEventChain()).record_server_graded(
            user_id=get_current_user().id,
            subject_id=server_subject_id if binding is not None else "",
            question_id=question_id,
            kc_ids=(server_kc_id,) if binding is not None else (),
            is_correct=correct,
            item_valid=bool(isinstance(selected, dict) or expected),
            attribution_reliable=binding is not None,
            derived=project_bound_event,
            attempt_id=question_attempt_id or f"{base_attempt_id}:{question_id}",
            surface_type="quiz",
            module_id=str(item.get("module_id") or "") or None,
            learning_path_id=(
                str(binding.get("learning_path_id") or "") or None if binding is not None else None
            ),
            error_tag=(classify_error(answer).value if not correct else None),
        )


def _build_component_plan(
    pack: dict[str, Any],
    request: CreateLearningPlanRequest,
) -> LearningComponentPlan:
    return build_learning_component_plan(
        pack,
        instruction=request.instruction,
        preferred_modalities=list(request.preferred_modalities),
        supersedes_plan_id=request.supersedes_plan_id,
    )


def _record_component_learning_event_sync(
    pack: dict[str, Any],
    plan: dict[str, Any],
    component: dict[str, Any],
    request: ComponentInteractionRequest,
    event_id: str,
    *,
    chain: CanonicalAnswerEventChain | None = None,
    learning_service: LearningService | None = None,
    defer_derived: bool = False,
) -> bool:
    """Record only server-verified assessment answers as BKT-facing evidence.

    Retrieval self-ratings are intentionally kept in the append-only component
    event stream, but are not a trustworthy measurement of mastery.  They can
    inform a future learner-facing reflection without changing BKT or causing
    an automatic replan.
    """
    if request.observation is None:
        return False
    component_type = str(component.get("component_type") or "")
    assessment = component_type in _EVIDENCE_ASSESSMENT_COMPONENT_TYPES
    retrieval = component_type in {"retrieval_card", "review_queue"}
    if assessment and request.observation not in {"correct", "incorrect"}:
        return False
    if retrieval and request.observation not in {"known", "uncertain", "unknown"}:
        return False
    if not assessment and not retrieval:
        return False
    if retrieval:
        # A learner's own "I know this" rating is participation data, not
        # graded evidence. `record_component_event` already persists it with
        # the component progress, so deliberately stop before the trusted
        # personalization service / BKT boundary.
        return False
    raw_subject = plan.get("subject_ref")
    subject = SubjectRef.model_validate(raw_subject) if isinstance(raw_subject, dict) else None
    item = _verified_assessment_item(pack, request)
    server_kc_id = str((item or {}).get("node_id") or "").strip()
    artifact = _verified_assessment_artifact(pack, request)
    plan_subject_id = subject.subject_id.strip() if subject else ""
    item_subject_id = _subject_id_from_server_item(item or {}, artifact=artifact)
    active_binding = learning_packs.active_learning_path_binding(
        pack, owner_id=get_current_user().id
    )
    binding_subject_id = (
        str(active_binding.get("subject_id") or "").strip() if active_binding is not None else ""
    )
    # An explicit server-held item subject wins.  Otherwise the persisted
    # owner-bound graph is the authority, but only while the immutable plan
    # agrees with that subject.  The plan's unconfirmed classifier output can
    # therefore corroborate a binding; it cannot create one by itself.
    server_subject_id = item_subject_id or binding_subject_id
    if (
        not server_subject_id
        or (plan_subject_id and plan_subject_id != server_subject_id)
        or (item_subject_id and binding_subject_id and item_subject_id != binding_subject_id)
    ):
        server_subject_id = ""
    binding = _bound_pack_assessment_target(
        pack,
        owner_id=get_current_user().id,
        subject_id=server_subject_id,
        kc_id=server_kc_id,
    )
    previously_revealed = any(
        isinstance(previous, Mapping)
        and str(previous.get("event_id") or "") != event_id
        and str(previous.get("question_id") or "") == str(request.question_id or "")
        and previous.get("observation") in {"correct", "incorrect"}
        for plan_progress in _dict_field(pack, "component_progress").values()
        if isinstance(plan_progress, Mapping)
        for previous in _list_field(plan_progress, "events")
    )
    evidence_binding = None if previously_revealed else binding

    def project_bound_event(recorded: Any) -> None:
        if evidence_binding is not None:
            project_canonical_event_to_existing_progress(recorded, service=learning_service)

    options = (item or {}).get("options")
    server_verifiable = bool(str((item or {}).get("correct_answer") or "").strip()) or any(
        isinstance(option, dict) and "is_correct" in option
        for option in (options if isinstance(options, list) else [])
    )
    event, _outcome = (chain or CanonicalAnswerEventChain()).record_server_graded(
        user_id=get_current_user().id,
        subject_id=server_subject_id if evidence_binding is not None else "",
        question_id=str(request.question_id or component["component_id"]),
        kc_ids=(server_kc_id,) if evidence_binding is not None else (),
        is_correct=request.observation == "correct",
        item_valid=bool(item is not None and server_verifiable),
        attribution_reliable=evidence_binding is not None,
        derived=project_bound_event,
        attempt_id=event_id,
        surface_type="practice",
        module_id=str(component.get("component_id") or "") or None,
        learning_path_id=(
            str(evidence_binding.get("learning_path_id") or "") or None
            if evidence_binding is not None
            else None
        ),
        error_tag=(
            classify_error(request.answer or "").value
            if request.observation == "incorrect"
            else None
        ),
        defer_derived=defer_derived,
    )
    return is_strong_evidence(event)


async def _record_component_learning_event(
    pack: dict[str, Any],
    plan: dict[str, Any],
    component: dict[str, Any],
    request: ComponentInteractionRequest,
    event_id: str,
    *,
    chain: CanonicalAnswerEventChain | None = None,
    learning_service: LearningService | None = None,
    defer_derived: bool = False,
) -> bool:
    """Async-compatible wrapper for direct callers and existing integrations."""
    return _record_component_learning_event_sync(
        pack,
        plan,
        component,
        request,
        event_id,
        chain=chain,
        learning_service=learning_service,
        defer_derived=defer_derived,
    )


def _verified_assessment_observation(
    pack: dict[str, Any],
    request: ComponentInteractionRequest,
) -> Literal["correct", "incorrect"] | None:
    """Grade an assessment answer against a server-owned generated artifact."""
    if not request.question_id or request.answer is None or not request.output_ref:
        return None
    artifacts = _dict_field(pack, "artifacts")
    quizzes = _list_field(artifacts, "quiz")
    artifact = next(
        (
            item
            for item in reversed(quizzes)
            if isinstance(item, dict) and item.get("verified_generation_id") == request.output_ref
        ),
        None,
    )
    if artifact is None:
        return None
    item = next(
        (
            item
            for item in artifact.get("items", [])
            if isinstance(item, dict) and str(item.get("question_id") or "") == request.question_id
        ),
        None,
    )
    if item is None:
        return None
    answer = request.answer.strip()
    options = _list_field(item, "options")
    selected = next(
        (
            option
            for option in options
            if isinstance(option, dict)
            and answer
            in {
                str(option.get("text") or "").strip(),
                str(option.get("key") or option.get("id") or "").strip(),
            }
        ),
        None,
    )
    if isinstance(selected, dict):
        return "correct" if bool(selected.get("is_correct")) else "incorrect"
    expected = str(item.get("correct_answer") or "").strip()
    if not expected:
        return None
    return (
        "correct"
        if grade_answer(answer, expected, str(item.get("question_type") or "short"))
        else "incorrect"
    )


def _verified_assessment_item(
    pack: dict[str, Any], request: ComponentInteractionRequest
) -> dict[str, Any] | None:
    artifact = _verified_assessment_artifact(pack, request)
    return next(
        (
            entry
            for entry in (artifact or {}).get("items", [])
            if isinstance(entry, dict)
            and str(entry.get("question_id") or "") == request.question_id
        ),
        None,
    )


def _verified_assessment_artifact(
    pack: dict[str, Any], request: ComponentInteractionRequest
) -> dict[str, Any] | None:
    artifacts = _dict_field(pack, "artifacts")
    quizzes = _list_field(artifacts, "quiz")
    return next(
        (
            entry
            for entry in reversed(quizzes)
            if isinstance(entry, dict) and entry.get("verified_generation_id") == request.output_ref
        ),
        None,
    )


def _repair_retry_item(
    artifact: dict[str, Any] | None, original: dict[str, Any]
) -> dict[str, Any] | None:
    """Choose a distinct server-owned item, preferring the same grounded concept."""
    candidates = [
        item
        for item in (artifact or {}).get("items", [])
        if isinstance(item, dict)
        and str(item.get("question_id") or "") != str(original.get("question_id") or "")
        and item.get("correct_answer")
    ]
    concept_id = str(original.get("node_id") or "")
    return next(
        (
            item
            for item in candidates
            if concept_id and str(item.get("node_id") or "") == concept_id
        ),
        None,
    ) or (candidates[0] if candidates else None)


def _repair_rule(item: dict[str, Any], *, reveal_answer: bool) -> str:
    explanation = str(item.get("explanation") or "Review the source-grounded rule for this item.")
    answer = str(item.get("correct_answer") or "").strip()
    if reveal_answer or not answer or not explanation.casefold().startswith(answer.casefold()):
        return explanation
    remainder = explanation[len(answer) :].lstrip(" .,:;，。；：-—")
    return remainder or "Review the source-grounded rule, then solve the item again."


def _verified_assessment_concept(
    pack: dict[str, Any],
    component: dict[str, Any],
    request: ComponentInteractionRequest,
) -> tuple[str, str]:
    """Use the attached server artifact, never browser fields, for BKT attribution."""
    artifacts = _dict_field(pack, "artifacts")
    quizzes = _list_field(artifacts, "quiz")
    artifact = next(
        (
            entry
            for entry in reversed(quizzes)
            if isinstance(entry, dict) and entry.get("verified_generation_id") == request.output_ref
        ),
        None,
    )
    item = next(
        (
            entry
            for entry in (artifact or {}).get("items", [])
            if isinstance(entry, dict)
            and str(entry.get("question_id") or "") == request.question_id
        ),
        None,
    )
    if isinstance(item, dict):
        concept_id = str(item.get("node_id") or "").strip()
        label = str(
            item.get("node_name") or item.get("concept_label") or item.get("question") or ""
        ).strip()
        if concept_id:
            return concept_id, label or concept_id
    refs = _list_field(component, "concept_refs")
    concept_id = (
        str(refs[0]).strip() if refs else str(component.get("component_id") or "assessment")
    )
    return concept_id, concept_id


@router.get("")
async def list_learning_packs():
    packs = learning_packs.list_packs()
    return {"packs": [_learner_pack(pack) for pack in packs], "total": len(packs)}


@router.get("/materials/capabilities")
async def get_learning_pack_material_capabilities():
    """Describe only material input capabilities available end to end.

    Availability requires both the server rollout gate and a configured
    vision-capable Gateway route. Browser input cannot enable the route.
    """
    return {
        "source_types": sorted(learning_packs.PACK_MATERIAL_SOURCE_TYPES),
        "operations": ["append", "remove", "reorder"],
        "image_ocr": image_ocr_capability(),
    }


@router.delete("")
async def delete_learning_packs(request: DeletePacksRequest):
    """Delete up to 100 selected Packs from the authenticated workspace.

    A Learn session created on /home solely to serve a Pack becomes an orphan
    in the sidebar Recents once its Pack is gone.  Clean up those sessions too,
    but only when no remaining Pack still references them: a session may host
    several uploads, and an Assist conversation must never be removed here.
    """
    requested = list(
        dict.fromkeys(pack_id.strip() for pack_id in request.pack_ids if pack_id.strip())
    )
    if not requested:
        raise HTTPException(status_code=422, detail="At least one learning pack is required")
    removed = learning_packs.delete_packs(requested)
    deleted_ids = [str(pack["pack_id"]) for pack in removed]
    deleted_set = set(deleted_ids)
    try:
        await _delete_orphaned_learn_sessions(removed)
    except Exception:
        # Pack deletion already committed; session cleanup is best-effort so a
        # stale Recents entry must never roll back the user's delete request.
        logger.exception("Learn session cleanup after Pack deletion failed")
    return {
        "deleted_ids": deleted_ids,
        "missing_ids": [pack_id for pack_id in requested if pack_id not in deleted_set],
        "deleted_count": len(deleted_ids),
    }


async def _delete_orphaned_learn_sessions(removed: list[dict[str, Any]]) -> None:
    """Remove Learn sessions that no Pack references after a batch delete."""
    from traittutor.services.session import get_session_store

    def referenced_session_ids(packs: list[dict[str, Any]]) -> set[str]:
        ids: set[str] = set()
        for pack in packs:
            materials = _list_field(pack, "materials")
            if not materials or not isinstance(materials[0], Mapping):
                continue
            metadata = _dict_field(materials[0], "metadata")
            session_id = str(metadata.get("learning_session_id") or "").strip()
            if session_id:
                ids.add(session_id)
        return ids

    deleted_session_ids = referenced_session_ids(removed)
    if not deleted_session_ids:
        return
    still_used = referenced_session_ids(learning_packs.list_packs())
    store = get_session_store()
    for session_id in sorted(deleted_session_ids):
        if session_id in still_used:
            continue
        try:
            await store.delete_session(session_id)
        except Exception:
            logger.exception("Failed to delete orphaned Learn session %s", session_id)


@router.get("/by-session/{session_id}")
async def get_learning_pack_for_session(session_id: str):
    """Return the newest Pack explicitly linked to this durable Learn session."""
    for pack in learning_packs.list_packs():
        materials = _list_field(pack, "materials")
        material = materials[0] if materials and isinstance(materials[0], dict) else {}
        metadata = _dict_field(material, "metadata")
        if str(metadata.get("learning_session_id") or "") == session_id:
            return _learner_pack(pack)
    raise HTTPException(status_code=404, detail="Learning pack not found for session")


@router.post("")
async def create_learning_pack(request: CreatePackRequest):
    _reject_unsafe_learning_payload(request.model_dump())
    try:
        return _learner_pack(
            learning_packs.create_pack(
                title=request.title,
                material=request.material,
                profile_id=request.profile_id,
                goal=request.goal,
                sources=request.sources,
            )
        )
    except learning_packs.InvalidPackMaterialOperation as exc:
        _raise_material_mutation_error(exc)


@router.post("/with-plan")
async def create_learning_pack_with_plan(request: CreatePackWithPlanRequest):
    """Create the initial Pack and Plan without exposing a partial Pack."""
    _reject_unsafe_learning_payload(request.model_dump(exclude={"plan", "idempotency_key"}))
    try:
        pack, plan = learning_packs.create_pack_with_component_plan(
            title=request.title,
            material=request.material,
            profile_id=request.profile_id,
            goal=request.goal,
            sources=request.sources,
            idempotency_key=request.idempotency_key,
            request_fingerprint_payload=request.plan.model_dump(mode="json"),
            plan_builder=lambda draft: _build_component_plan(draft, request.plan).model_dump(),
        )
    except learning_packs.InvalidPackMaterialOperation as exc:
        _raise_material_mutation_error(exc)
    except learning_packs.InvalidComponentPlanChain as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _ensure_initial_learning_path_binding(pack, plan)
    return {
        "pack": _learner_pack(pack),
        "plan": {**plan, "start_url": f"/learning/{pack['pack_id']}"},
    }


@router.post("/{pack_id}/materials")
async def append_learning_pack_material(pack_id: str, request: AppendPackMaterialRequest):
    material = request.material.model_dump()
    _reject_unsafe_learning_payload(material)
    try:
        result = learning_packs.append_pack_material(
            pack_id,
            material=material,
            expected_revision=request.expected_revision,
            idempotency_key=request.idempotency_key,
        )
    except (
        learning_packs.InvalidPackMaterialOperation,
        learning_packs.MaterialIdempotencyConflict,
        learning_packs.MaterialRevisionConflict,
    ) as exc:
        _raise_material_mutation_error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    revision, replayed = result
    return _public_material_revision(pack_id, revision, replayed=replayed)


@router.delete("/{pack_id}/materials/{material_id}")
async def remove_learning_pack_material(
    pack_id: str, material_id: str, request: RemovePackMaterialRequest
):
    try:
        result = learning_packs.remove_pack_material(
            pack_id,
            material_id=material_id,
            expected_revision=request.expected_revision,
            idempotency_key=request.idempotency_key,
        )
    except (
        learning_packs.InvalidPackMaterialOperation,
        learning_packs.MaterialIdempotencyConflict,
        learning_packs.MaterialRevisionConflict,
    ) as exc:
        _raise_material_mutation_error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    revision, replayed = result
    return _public_material_revision(pack_id, revision, replayed=replayed)


@router.post("/{pack_id}/materials/reorder")
async def reorder_learning_pack_materials(pack_id: str, request: ReorderPackMaterialsRequest):
    try:
        result = learning_packs.reorder_pack_materials(
            pack_id,
            material_ids=request.material_ids,
            expected_revision=request.expected_revision,
            idempotency_key=request.idempotency_key,
        )
    except (
        learning_packs.InvalidPackMaterialOperation,
        learning_packs.MaterialIdempotencyConflict,
        learning_packs.MaterialRevisionConflict,
    ) as exc:
        _raise_material_mutation_error(exc)
    if result is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    revision, replayed = result
    return _public_material_revision(pack_id, revision, replayed=replayed)


@router.get("/{pack_id}/materials/revisions/{revision}")
async def get_learning_pack_material_revision(pack_id: str, revision: int):
    if revision < 1:
        raise HTTPException(status_code=422, detail="Material revision must be positive")
    if learning_packs.get_pack(pack_id) is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    snapshot = learning_packs.get_pack_material_revision(pack_id, revision)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Material revision not found")
    return _public_material_revision(pack_id, snapshot)


@router.get("/{pack_id}")
async def get_learning_pack(pack_id: str):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    return _learner_pack(pack)


@router.delete("/{pack_id}")
async def delete_learning_pack(pack_id: str):
    pack = learning_packs.delete_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    try:
        await _delete_orphaned_learn_sessions([pack])
    except Exception:
        # Pack deletion already committed; session cleanup is best-effort so a
        # stale Recents entry must never roll back the user's delete request.
        logger.exception("Learn session cleanup after Pack deletion failed")
    return {"deleted_id": pack_id}


@router.get("/{pack_id}/learning-path-link")
async def get_learning_pack_path_link(pack_id: str):
    """Return this owner's current explicit Pack-to-path binding."""
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    binding = learning_packs.active_learning_path_binding(pack, owner_id=get_current_user().id)
    if binding is None:
        raise HTTPException(status_code=404, detail="Learning pack has no linked progress path")
    return _public_learning_path_binding(binding)


@router.post("/{pack_id}/learning-path-link")
async def bind_learning_pack_path(pack_id: str, request: BindLearningPathRequest):
    """Bind one Pack to an existing owner-held, subject-bound module graph.

    The request can select a persisted LearningProgress path and optionally a
    subset of its KCs.  It cannot choose the subject, owner, or graph fields:
    those are re-derived from the server-owned path under the current user
    workspace and persisted as an immutable revision.
    """
    if learning_packs.get_pack(pack_id) is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    user_id = get_current_user().id
    try:
        progress = LearningStore().load(request.learning_path_id)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid learning path identifier") from exc
    if progress is None:
        raise HTTPException(status_code=404, detail="Learning progress path not found")
    subject_id = progress.subject_id.strip()
    if not subject_id:
        raise HTTPException(
            status_code=422,
            detail="Learning progress must have a confirmed persisted subject before linking",
        )
    graph_fingerprint, graph_kc_ids = _learning_graph_snapshot(progress)
    requested_kc_ids = (
        [value.strip() for value in request.allowed_kc_ids if value.strip()]
        if request.allowed_kc_ids is not None
        else graph_kc_ids
    )
    if not requested_kc_ids:
        raise HTTPException(
            status_code=422, detail="At least one persisted knowledge point is required"
        )
    unknown = sorted(set(requested_kc_ids) - set(graph_kc_ids))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="Every allowed knowledge point must belong to the persisted learning path",
        )
    try:
        result = learning_packs.create_learning_path_binding(
            pack_id,
            owner_id=user_id,
            learning_path_id=progress.book_id,
            subject_id=subject_id,
            allowed_kc_ids=requested_kc_ids,
            graph_fingerprint=graph_fingerprint,
            graph_version=progress.version,
        )
    except learning_packs.InvalidLearningPathBinding as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    binding, replayed = result
    return {**_public_learning_path_binding(binding), "idempotent_replay": replayed}


@router.patch("/{pack_id}")
async def update_learning_pack(pack_id: str, request: UpdatePackRequest):
    patch = request.model_dump(exclude_none=True)
    _reject_unsafe_learning_payload(
        {
            key: value
            for key, value in patch.items()
            if key in {"title", "goal", "sources", "source"}
        }
    )
    generation_id = patch.pop("generation_id", None)
    if generation_id:
        task = get_generation_task_manager().get(generation_id)
        if task is None or task.status != "completed" or task.result is None:
            raise HTTPException(
                status_code=422,
                detail="Only confirmed generation tasks can be attached to a learning pack",
            )
        verified_artifact = dict(task.result.result)
        verified_artifact["verified_generation_id"] = generation_id
        patch["artifact"] = verified_artifact
    elif "artifact" in patch:
        # Artifacts are server-owned generation outputs.  Do not allow a client
        # to insert its own answer key and then submit it as a graded quiz.
        raise HTTPException(
            status_code=422, detail="Use generation_id to attach a generated artifact"
        )
    pack = learning_packs.update_pack(pack_id, patch) if patch else learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    await _record_pack_learning_events(pack, patch)
    return _learner_pack(pack)


@router.post("/{pack_id}/plans")
async def create_learning_component_plan(pack_id: str, request: CreateLearningPlanRequest):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    if (
        request.supersedes_plan_id
        and learning_packs.get_component_plan(pack_id, request.supersedes_plan_id) is None
    ):
        raise HTTPException(status_code=404, detail="Previous learning plan not found")
    plan = _build_component_plan(pack, request)
    try:
        saved = learning_packs.create_component_plan(pack_id, plan.model_dump())
    except learning_packs.InvalidComponentPlanChain as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=409, detail="Learning plan could not be saved")
    return {**saved, "start_url": f"/learning/{pack_id}"}


@router.post("/{pack_id}/pre-assessment")
async def create_learning_pre_assessment(pack_id: str):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    existing = pack.get("pre_assessment")
    if isinstance(existing, Mapping):
        return _pre_assessment_response(existing)
    try:
        decision = await judge_and_generate_pre_assessment(pack)
    except GenerationConfigurationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "model_configuration_required",
                "message": "No generation model is configured for pre-assessment.",
            },
        ) from exc
    except (GenerationStructuredOutputExhaustedError, GenerationModelExhaustedError) as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "pre_assessment_failed",
                "message": "Pre-assessment generation failed validation or model routing; retry.",
            },
        ) from exc
    except Exception as exc:
        logger.exception("Pre-assessment generation failed for Pack %s", pack_id)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "pre_assessment_failed",
                "message": "Pre-assessment generation failed; retry.",
            },
        ) from exc

    timestamp = datetime.now(UTC).isoformat()
    assessment_id = f"pre-{uuid4().hex}"
    probes = [
        {**dict(probe), "question_id": f"q{index}"}
        for index, probe in enumerate(decision.get("probes") or [], start=1)
    ]
    assessment = {
        "assessment_id": assessment_id,
        "status": "pending" if decision.get("needed") else "not_needed",
        "reason": str(decision.get("reason") or ""),
        "created_at": timestamp,
        "updated_at": timestamp,
        "probes": probes,
        "responses": [],
    }
    saved = learning_packs.save_pre_assessment(pack_id, assessment)
    if saved is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    return _pre_assessment_response(saved[0])


@router.post("/{pack_id}/pre-assessment/{assessment_id}/submit")
async def submit_learning_pre_assessment(
    pack_id: str, assessment_id: str, request: SubmitPreAssessmentRequest
):
    try:
        saved = learning_packs.submit_pre_assessment(
            pack_id,
            assessment_id,
            answers=[answer.model_dump(mode="json") for answer in request.answers],
            event_id=request.event_id,
        )
    except learning_packs.InvalidPreAssessmentTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if saved is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    assessment, replayed = saved
    probes = {
        str(probe.get("question_id") or ""): probe
        for probe in assessment.get("probes") or []
        if isinstance(probe, Mapping)
    }
    return {
        "assessment_id": assessment_id,
        "results": [
            {
                "question_id": response.get("question_id"),
                "correct": response.get("correct"),
                "confidence": response.get("confidence"),
                "rationale": probes.get(str(response.get("question_id") or ""), {}).get(
                    "rationale", ""
                ),
            }
            for response in assessment.get("responses") or []
            if isinstance(response, Mapping)
        ],
        "idempotent_replay": replayed,
    }


@router.post("/{pack_id}/pre-assessment/{assessment_id}/skip")
async def skip_learning_pre_assessment(pack_id: str, assessment_id: str):
    try:
        assessment = learning_packs.skip_pre_assessment(pack_id, assessment_id)
    except learning_packs.InvalidPreAssessmentTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if assessment is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    return {"assessment_id": assessment_id, "status": "skipped"}


@router.post("/{pack_id}/plans/arrange")
async def arrange_learning_component_path(pack_id: str):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    active_plan_id = str(pack.get("active_plan_id") or "")
    active_plan = learning_packs.get_component_plan(pack_id, active_plan_id)
    if active_plan is None:
        raise HTTPException(status_code=409, detail="Learning pack has no active plan")
    if active_plan.get("arrangement", "pending") == "llm":
        if isinstance(pack.get("pre_assessment"), Mapping) and pack["pre_assessment"].get(
            "status"
        ) in {"answered", "skipped", "not_needed"}:
            learning_packs.consume_pre_assessment(pack_id)
        return {**active_plan, "fallback": False, "idempotent_replay": True}
    # No started-guard here: a canvas retry of a pending/fallen-back arrangement
    # may happen after a component already started (the goal map auto-completes
    # on open). ``build_arranged_learning_component_plan`` preserves the started
    # prefix and only re-arranges the not-yet-started tail, so arranging after
    # start never rewrites started work — it creates a new superseding plan.
    pre_assessment = pack.get("pre_assessment")
    if isinstance(pre_assessment, Mapping) and pre_assessment.get("status") == "pending":
        raise HTTPException(
            status_code=409,
            detail="Answer or skip the pending pre-assessment before arranging the path",
        )
    try:
        arranged = await arrange_learning_component_plan(pack, active_plan)
        saved = learning_packs.create_component_plan(pack_id, arranged.model_dump(mode="json"))
        if saved is None:
            raise learning_packs.InvalidComponentPlanChain(
                "Arranged learning plan could not be saved"
            )
    except learning_packs.InvalidComponentPlanChain as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GenerationConfigurationError as exc:
        # No generation model is configured: the LLM arrangement cannot
        # succeed, so retrying would only burn a doomed model call. Surface
        # the typed 409 the client renders as "open Model settings" instead of
        # silently falling back and looping on "retry".
        raise HTTPException(
            status_code=409,
            detail={
                "code": "model_configuration_required",
                "message": "No generation model is configured for path arrangement.",
            },
        ) from exc
    except Exception:
        logger.warning(
            "LLM learning-path arrangement failed; preserving deterministic plan",
            exc_info=True,
        )
        fallback = learning_packs.mark_active_plan_arrangement(pack_id, "deterministic_fallback")
        if fallback is None:
            raise HTTPException(
                status_code=409, detail="Learning plan could not be recovered"
            ) from None
        return {
            **fallback,
            "fallback": True,
            "idempotent_replay": False,
            "fallback_message": "Learning-path arrangement failed; using the deterministic plan.",
        }
    if isinstance(pre_assessment, Mapping) and pre_assessment.get("status") in {
        "answered",
        "skipped",
        "not_needed",
    }:
        try:
            learning_packs.consume_pre_assessment(pack_id)
        except learning_packs.InvalidPreAssessmentTransition:
            logger.warning(
                "Arranged plan saved but pre-assessment lifecycle changed concurrently for Pack %s",
                pack_id,
            )
    return {**saved, "fallback": False, "idempotent_replay": False}


@router.get("/{pack_id}/reviews/due")
async def get_due_learning_reviews(pack_id: str):
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    items = _learner_due_reviews(pack)
    return {"items": items, "total": len(items), "estimated_minutes": len(items)}


@router.post("/{pack_id}/reviews/{review_id}/reveal")
async def reveal_learning_review_answer(pack_id: str, review_id: str):
    """Reveal one due retrieval answer without exposing it in the queue projection."""
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    review = next(
        (item for item in due_reviews(pack) if item.get("review_id") == review_id),
        None,
    )
    if review is None:
        raise HTTPException(status_code=404, detail="Due review item not found")
    if review.get("source") != "retrieval":
        raise HTTPException(status_code=409, detail="This review requires a submitted answer")
    card = _retrieval_review_card(pack, review)
    answer = str(card.get("back") or card.get("answer") or "").strip()
    if not answer:
        raise HTTPException(status_code=422, detail="Review item has no server-held answer")
    return {"review_id": review_id, "answer": answer}


async def _record_learning_review_result(
    pack_id: str,
    review_id: str,
    request: ReviewResultRequest,
    *,
    user_id: str | None = None,
    chain: CanonicalAnswerEventChain | None = None,
):
    """Grade a due review and persist its canonical evidence before scheduling.

    The private Pack is the only source used to derive ``correct``.  This
    helper's injectable chain/user seam makes the event-first ordering
    testable without granting a caller control over either value.
    """
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    review_item = next(
        (item for item in pack.get("review_states") or [] if item.get("review_id") == review_id),
        None,
    )
    if review_item is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    verified = False
    if review_item.get("source") == "repair" and request.answer is not None:
        repair_id = str(review_id).removeprefix("review-repair-")
        repair = next(
            (item for item in pack.get("repairs") or [] if item.get("repair_id") == repair_id), None
        )
        expected = str((repair or {}).get("retry_expected_answer") or "")
        if not expected:
            raise HTTPException(
                status_code=422, detail="This review has no server-owned answer key"
            )
        correct = grade_answer(
            request.answer,
            expected,
            str((repair or {}).get("retry_question_type") or "short"),
        )
        verified = True
    elif review_item.get("source") == "retrieval" and request.rating is not None:
        # A recall self-rating may schedule the next card but remains
        # participation data and never becomes BKT/mastery evidence.
        correct = request.rating == "known"
    else:
        raise HTTPException(
            status_code=422, detail="A server-verifiable answer or retrieval rating is required"
        )
    try:
        before_schedule = None
        if review_item.get("source") == "repair":
            # An answer-bearing repair is the sole review source that may
            # update canonical BKT. Retrieval ratings bypass this branch.
            resolved_user_id = user_id or get_current_user().id
            event_chain = chain or CanonicalAnswerEventChain()

            def before_schedule() -> None:
                _record_canonical_repair_review_evidence(
                    pack_id=pack_id,
                    review_id=review_id,
                    repair=repair or {},
                    correct=correct,
                    attempt_id=request.event_id,
                    user_id=resolved_user_id,
                    chain=event_chain,
                )

        update_args = {
            "correct": correct,
            "event_id": request.event_id,
        }
        if before_schedule is not None:
            update_args["before_schedule"] = before_schedule
        review = learning_packs.update_review_result(
            pack_id, review_id, **cast(dict[str, Any], update_args)
        )
    except learning_packs.InvalidComponentTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if review is None:
        raise HTTPException(status_code=404, detail="Review item not found")
    return {
        "accepted": True,
        "verified": verified,
        "correct": correct if verified else None,
        "review": review,
        "next_review_at": review.get("due_at"),
    }


@router.post("/{pack_id}/reviews/{review_id}/result")
async def record_learning_review_result(pack_id: str, review_id: str, request: ReviewResultRequest):
    return await _record_learning_review_result(pack_id, review_id, request)


async def _record_learning_repair_retry(
    pack_id: str,
    repair_id: str,
    request: RepairRetryRequest,
    *,
    user_id: str | None = None,
    chain: CanonicalAnswerEventChain | None = None,
):
    """Server-grade a repair retry and persist canonical evidence first."""
    try:
        resolved_user_id = user_id or get_current_user().id
        event_chain = chain or CanonicalAnswerEventChain()

        def before_mutation(repair: dict[str, Any], correct: bool) -> None:
            _record_canonical_repair_retry_evidence(
                pack_id=pack_id,
                repair_id=repair_id,
                repair=repair,
                correct=correct,
                attempt_id=request.event_id,
                user_id=resolved_user_id,
                chain=event_chain,
            )

        repair = learning_packs.record_repair_retry(
            pack_id,
            repair_id,
            answer=request.answer,
            event_id=request.event_id,
            before_mutation=before_mutation,
        )
    except learning_packs.InvalidComponentTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if repair is None:
        raise HTTPException(status_code=404, detail="Repair not found")
    public_repair = _public_repair(repair, reveal_content=True)
    return {
        "accepted": True,
        "verified_correct": repair.get("last_retry_correct"),
        "repair": public_repair,
        "next_review_at": repair.get("next_review_at"),
        "recovery": {
            "deferred": repair.get("status") == "deferred",
            "suggested_next_component_id": repair.get("suggested_next_component_id"),
        },
        "evidence_strength": repair.get("retry_evidence_strength") or "weak",
    }


@router.get("/{pack_id}/repairs/{repair_id}")
async def get_learning_repair(pack_id: str, repair_id: str):
    """Reveal one owner-bound repair only when the learner opens that item."""
    pack = learning_packs.get_pack(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    repair = next(
        (
            item
            for item in pack.get("repairs") or []
            if isinstance(item, dict) and item.get("repair_id") == repair_id
        ),
        None,
    )
    if repair is None:
        raise HTTPException(status_code=404, detail="Repair not found")
    return _public_repair(repair, reveal_content=True)


@router.post("/{pack_id}/repairs/{repair_id}/retry")
async def record_learning_repair_retry(pack_id: str, repair_id: str, request: RepairRetryRequest):
    return await _record_learning_repair_retry(pack_id, repair_id, request)


@router.get("/{pack_id}/plans")
async def list_learning_component_plans(pack_id: str):
    if learning_packs.get_pack(pack_id) is None:
        raise HTTPException(status_code=404, detail="Learning pack not found")
    plans = learning_packs.list_component_plans(pack_id)
    return {"plans": plans, "total": len(plans)}


@router.get("/{pack_id}/plans/{plan_id}")
async def get_learning_component_plan(pack_id: str, plan_id: str):
    plan = learning_packs.get_component_plan(pack_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    return plan


@router.get("/{pack_id}/plans/{plan_id}/attempts")
async def list_learning_assessment_attempts(
    pack_id: str,
    plan_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    pack = learning_packs.get_pack(pack_id)
    plan = learning_packs.get_component_plan(pack_id, plan_id)
    if pack is None or plan is None:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    attempts = _assessment_attempt_views(pack, plan_id)
    return {
        "items": attempts[offset : offset + limit],
        "total": len(attempts),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{pack_id}/plans/{plan_id}/events")
async def stream_learning_component_events(pack_id: str, plan_id: str):
    pack = learning_packs.get_pack(pack_id)
    plan = learning_packs.get_component_plan(pack_id, plan_id)
    if pack is None or plan is None:
        raise HTTPException(status_code=404, detail="Learning plan not found")

    async def stream():
        progress = (pack.get("component_progress") or {}).get(plan_id) or {}
        yield f"event: snapshot\ndata: {json.dumps({'plan': plan, 'progress': progress}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0)
        yield "event: ready\ndata: {}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/{pack_id}/plans/{plan_id}/components/{component_id}/events")
async def record_learning_component_event(
    pack_id: str,
    plan_id: str,
    component_id: str,
    request: ComponentInteractionRequest,
):
    pack = learning_packs.get_pack(pack_id)
    plan = learning_packs.get_component_plan(pack_id, plan_id)
    if pack is None or plan is None:
        raise HTTPException(status_code=404, detail="Learning plan not found")
    component = next(
        (item for item in plan.get("components", []) if item.get("component_id") == component_id),
        None,
    )
    if component is None:
        raise HTTPException(status_code=404, detail="Learning component not found")
    trusted_media_url = _trusted_audio_media_url(component, request)
    persistence_only = _is_output_reference_persistence_event(request)
    _validate_component_output_reference(request)
    _validate_assessment_output_attachment(pack, component, request)
    effective_request = request
    server_graded_assessment = False
    if component.get("component_type") in {
        "diagnostic_check",
        "guided_practice",
        "transfer_challenge",
    }:
        if not persistence_only and (
            request.observation is not None or request.action in {"feedback", "complete"}
        ):
            if request.confidence is None:
                raise HTTPException(
                    status_code=422,
                    detail="Predict confidence before submitting an assessment answer",
                )
            verified_observation = _verified_assessment_observation(pack, request)
            if verified_observation is None:
                raise HTTPException(
                    status_code=422, detail="A verified assessment answer is required"
                )
            concept_id, concept_label = _verified_assessment_concept(pack, component, request)
            effective_request = request.model_copy(
                update={
                    "observation": verified_observation,
                    "concept_id": concept_id,
                    "concept_label": concept_label,
                }
            )
            server_graded_assessment = True
    event = request.model_dump(exclude_none=True, exclude={"media_url"})
    if trusted_media_url:
        event["media_url"] = trusted_media_url
    event["event_id"] = request.event_id or f"component-{uuid4().hex}"
    if effective_request is not request:
        event["action"] = effective_request.action
        event["observation"] = effective_request.observation
        event["concept_id"] = effective_request.concept_id
        event["concept_label"] = effective_request.concept_label
    if server_graded_assessment:
        event["_server_graded"] = True
    canonical_chain = CanonicalAnswerEventChain()
    recorded_learner_state = False

    def append_canonical_event(
        locked_pack: dict[str, Any],
        locked_plan: dict[str, Any],
        locked_component: dict[str, Any],
    ) -> None:
        nonlocal recorded_learner_state
        # The Pack store invokes this only after validating the authoritative
        # state while holding its cross-process lock. This closes the old
        # preflight/mutation race while preserving event-before-projection.
        recorded_learner_state = _record_component_learning_event_sync(
            locked_pack,
            locked_plan,
            locked_component,
            effective_request,
            str(event["event_id"]),
            chain=canonical_chain,
            defer_derived=True,
        )

    try:
        recorded = learning_packs.record_component_event(
            pack_id,
            plan_id,
            component_id,
            event,
            before_mutation=append_canonical_event,
        )
    except learning_packs.InvalidComponentTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if recorded is None:
        raise HTTPException(status_code=409, detail="Learning component event could not be saved")
    updated_pack, updated_component = recorded
    duplicate = bool(event.pop("_idempotent_replay", False))
    # The immutable event and Pack projection are now committed together.
    # Apply cross-domain projections afterwards so their own stores cannot
    # implicitly commit or block the shared transaction. Stable IDs keep this
    # replay safe after a crash between the two phases.
    if recorded_learner_state:
        _record_component_learning_event_sync(
            updated_pack,
            plan,
            updated_component,
            effective_request,
            str(event["event_id"]),
            chain=canonical_chain,
        )
    # Replays repair a possible interrupted secondary write, but they must
    # never trigger a second replan or claim a fresh learner-model update.
    learner_state_updated = recorded_learner_state and not duplicate

    component_type = str(updated_component.get("component_type") or "")
    calibration = None
    repair = None
    verified_feedback = None
    if component_type == "diagnostic_check" and effective_request.observation in {
        "correct",
        "incorrect",
    }:
        # The diagnostic is a one-time, non-blocking judgement. Keep its
        # server-verified feedback in component history, but do not project it
        # into BKT, calibration, repairs, reviews, or a superseding plan.
        item = _verified_assessment_item(updated_pack, effective_request) or {}
        verified_feedback = str(item.get("explanation") or "") or None
    elif (
        effective_request.observation in {"correct", "incorrect"}
        and effective_request.confidence is not None
        and effective_request.question_id
    ):
        calibration = calibration_record(
            effective_request.question_id,
            effective_request.confidence,
            effective_request.observation == "correct",
            artifact_ref=str(effective_request.output_ref),
        ).model_dump()
        learning_packs.record_calibration(pack_id, calibration)
        if effective_request.observation == "incorrect":
            item = _verified_assessment_item(updated_pack, effective_request) or {}
            verified_feedback = str(item.get("explanation") or "") or None
            retry_item = _repair_retry_item(
                _verified_assessment_artifact(updated_pack, effective_request),
                item,
            )
            raw_subject = plan.get("subject_ref")
            review_subject_id = ""
            if isinstance(raw_subject, dict):
                review_subject_id = SubjectRef.model_validate(raw_subject).subject_id
            # A fallback retry item can come from another KC when an artifact
            # has no unused near item.  Attribute a review only to the item
            # actually graded; if it no longer agrees with the original
            # canonical event, provenance deliberately degrades to weak.
            review_item_kc_id = str(
                (retry_item or item).get("node_id") or effective_request.concept_id or ""
            )
            canonical_source_event_id, review_subject_id, review_kc_id = (
                _canonical_repair_provenance(
                    chain=canonical_chain,
                    user_id=get_current_user().id,
                    component_attempt_id=str(event["event_id"]),
                    subject_id=review_subject_id,
                    kc_id=review_item_kc_id,
                )
            )
            repair = learning_packs.create_repair(
                pack_id,
                action_id=str(updated_component.get("component_id") or component_id),
                question_id=effective_request.question_id,
                artifact_ref=str(effective_request.output_ref),
                concept_id=str(
                    effective_request.concept_id
                    or updated_component.get("component_id")
                    or "assessment"
                ),
                user_answer=str(effective_request.answer or ""),
                correct_rule=_repair_rule(item, reveal_answer=retry_item is not None),
                error_type="metacognitive" if effective_request.confidence >= 0.75 else "deviation",
                contrast=str(item.get("correct_answer") or "") if retry_item is not None else "",
                retry_prompt=str(
                    (retry_item or item).get("question") or "Apply the corrected rule again."
                ),
                retry_expected_answer=str((retry_item or item).get("correct_answer") or ""),
                retry_question_id=str((retry_item or item).get("question_id") or ""),
                source_event_id=str(event["event_id"]),
                canonical_source_event_id=canonical_source_event_id,
                review_owner_id=get_current_user().id,
                review_subject_id=review_subject_id,
                review_kc_id=review_kc_id,
            )

    replanned = None
    progress_calibration = None
    calibration_complete = (
        updated_component.get("component_type") == "calibration_checkpoint"
        and effective_request.action == "complete"
    )
    if calibration_complete and not duplicate:
        # Progress calibration: aggregate the round's accumulated server-graded
        # evidence into a deterministic difficulty evaluation. The learner's
        # strategy is no longer chosen in the browser — the evaluation derives
        # it — and nothing here writes BKT or rewrites completed components.
        plan_events = ((updated_pack.get("component_progress") or {}).get(plan_id) or {}).get(
            "events"
        ) or []
        progress_calibration = build_progress_calibration(
            plan=plan,
            events=plan_events,
            calibrations=updated_pack.get("calibrations") or [],
        )
        learning_packs.save_progress_calibration(
            pack_id, progress_calibration.model_dump(mode="json")
        )
        if request.replan:
            # The follow-up plan only inserts the supports the evaluation names
            # (keeping the started prefix and the LLM order intact); a smooth
            # or evidence-insufficient round leaves the plan unchanged.
            followup = build_calibrated_followup_plan(
                plan, progress_calibration.model_dump(mode="json")
            )
            if followup is not None:
                try:
                    replanned = learning_packs.create_component_plan(
                        pack_id, followup.model_dump(mode="json")
                    )
                except learning_packs.InvalidComponentPlanChain as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
    elif request.replan and not duplicate and learner_state_updated:
        refreshed = learning_packs.get_pack(pack_id) or updated_pack
        replanned_model = _build_component_plan(
            refreshed,
            CreateLearningPlanRequest(
                instruction=str((refreshed.get("goal") or {}).get("text") or ""),
                supersedes_plan_id=plan_id,
            ),
        )
        try:
            replanned = learning_packs.create_component_plan(pack_id, replanned_model.model_dump())
        except learning_packs.InvalidComponentPlanChain as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "component": updated_component,
        "learner_state_updated": learner_state_updated,
        "replanned_plan": replanned,
        "progress_calibration": (
            progress_calibration.model_dump(mode="json") if progress_calibration else None
        ),
        # The browser needs only the verified outcome to give assessment
        # feedback. Never return the answer key or grading details here.
        "verified_observation": effective_request.observation
        if effective_request.observation in {"correct", "incorrect"}
        else None,
        "verified_feedback": verified_feedback,
        "calibration": calibration,
        "created_repair_id": repair.get("repair_id") if repair else None,
    }
