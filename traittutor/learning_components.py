"""Deterministic learning-component planning for TraitTutor.

The selector decides *which teaching supports to use* before any model writes
content. BKT supplies the subject-scoped knowledge stage, material analysis
gates useful modalities, and SLR support changes the component mix. Big Five
is only an initial bounded cue and never becomes a learning-style label.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


LearningComponentType = Literal[
    "goal_map", "concept_explanation", "worked_example", "visual_map",
    "audio_explanation", "diagnostic_check", "guided_practice",
    "retrieval_card", "progress_checkpoint", "reflection_prompt",
    "transfer_challenge", "review_queue",
]
ComponentExecutor = Literal["deterministic", "lesson", "retrieval", "assessment", "image", "audio"]
ComponentStatus = Literal["pending", "active", "completed", "skipped", "degraded"]
BKTStage = Literal["unobserved", "needs_support", "developing", "supported"]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def load_learning_component_catalog() -> dict[str, Any]:
    path = Path(__file__).parent / "assessment" / "learning_component_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


class MaterialAffordance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suitable: bool
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list, max_length=6)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)


class MaterialComponentAffordances(BaseModel):
    visual: MaterialAffordance
    audio: MaterialAffordance
    worked_example: MaterialAffordance
    practice: MaterialAffordance


class SubjectSupportState(BaseModel):
    subject_id: str | None = None
    source: Literal["initial_profile", "subject_evidence", "default"] = "default"
    dimensions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    boundary: str = (
        "Support state selects temporary teaching actions. It does not diagnose "
        "ability, personality, mood, or a fixed learning style."
    )


class LearningComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str
    component_type: LearningComponentType
    executor: ComponentExecutor
    label_zh: str
    label_en: str
    concept_refs: list[str] = Field(default_factory=list, max_length=12)
    support_dimensions: list[str] = Field(default_factory=list, max_length=4)
    bkt_stage: BKTStage
    modality: Literal["text", "interactive", "visual", "audio"]
    dependencies: list[str] = Field(default_factory=list, max_length=8)
    required: bool = True
    reason: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    completion_event: str
    status: ComponentStatus = "pending"
    output_ref: str | None = None


class LearningComponentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    pack_id: str
    version: int = Field(default=1, ge=1)
    goal: str
    subject_ref: dict[str, Any] | None = None
    analysis_id: str | None = None
    support_state_snapshot: SubjectSupportState
    bkt_snapshot_ref: str | None = None
    components: list[LearningComponent] = Field(min_length=1, max_length=18)
    status: Literal["active", "completed", "superseded"] = "active"
    supersedes_plan_id: str | None = None
    created_at: str
    updated_at: str
    boundary: str = (
        "The plan is an explainable teaching sequence. Only graded learner "
        "events may update subject knowledge state."
    )


def infer_material_affordances(
    analysis: Mapping[str, Any] | None,
    *,
    title: str = "",
    text: str = "",
    instruction: str = "",
) -> MaterialComponentAffordances:
    """Derive conservative modality gates from source-grounded metadata."""
    data = dict(analysis or {})
    evidence = [item for item in data.get("page_evidence") or data.get("evidence") or [] if isinstance(item, Mapping)]
    refs = [str(item.get("chunk_id") or item.get("page") or "") for item in evidence if item]
    haystack = " ".join((title, text[:5000], str(data.get("subject") or ""), str(data.get("sub_subject") or ""))).lower()
    request = instruction.lower()

    visual_terms = ("diagram", "graph", "chart", "流程", "结构", "关系", "函数", "几何", "地图", "图表")
    audio_terms = ("english", "language", "pronunciation", "listening", "英语", "语言", "发音", "听力", "朗读")
    example_terms = ("equation", "calculation", "procedure", "code", "case", "公式", "计算", "步骤", "代码", "案例")
    visual = any(term in haystack for term in visual_terms) or any(term in request for term in ("图解", "图表", "visual", "diagram"))
    audio = any(term in haystack for term in audio_terms) or any(term in request for term in ("语音", "听", "朗读", "audio", "pronounce"))
    worked = any(term in haystack for term in example_terms)
    has_source = bool(title or text or data)

    def affordance(suitable: bool, confidence: float, reason: str) -> MaterialAffordance:
        return MaterialAffordance(
            suitable=suitable,
            confidence=confidence if suitable else min(confidence, .45),
            reasons=[reason],
            evidence_refs=refs[:12],
        )

    return MaterialComponentAffordances(
        visual=affordance(visual, .72 if visual else .35, "The material contains relationships or structures that benefit from a visual representation."),
        audio=affordance(audio, .78 if audio else .25, "The goal or material benefits from listening, pronunciation, or spoken explanation."),
        worked_example=affordance(worked, .74 if worked else .42, "The material contains procedures, calculations, code, or cases that benefit from a worked example."),
        practice=affordance(has_source, .82 if has_source else .55, "Practice can produce answerable evidence for the current learning goal."),
    )


def build_subject_support_state(
    slr_support: Mapping[str, Any] | None,
    *,
    subject_id: str | None,
    strategy_evidence: list[Mapping[str, Any]] | None = None,
) -> SubjectSupportState:
    dimensions = {
        str(key): dict(value)
        for key, value in dict((slr_support or {}).get("dimensions") or {}).items()
        if isinstance(value, Mapping)
    }
    evidence = list(strategy_evidence or [])
    for item in evidence:
        for dimension in item.get("support_dimensions") or []:
            if dimension not in dimensions:
                continue
            current = dict(dimensions[dimension])
            current["evidence_count"] = int(current.get("evidence_count") or 0) + 1
            if float(item.get("positive_weight") or 0) > float(item.get("negative_weight") or 0) and current["evidence_count"] >= 3:
                current["emphasis"] = "strong"
            dimensions[dimension] = current
    source: Literal["initial_profile", "subject_evidence", "default"] = (
        "subject_evidence" if evidence else "initial_profile" if dimensions else "default"
    )
    return SubjectSupportState(subject_id=subject_id, source=source, dimensions=dimensions)


def build_learning_component_plan(
    pack: Mapping[str, Any],
    *,
    instruction: str = "",
    preferred_modalities: list[str] | None = None,
    supersedes_plan_id: str | None = None,
) -> LearningComponentPlan:
    """Build a plan from the shared product seams used by API and chat.

    This function intentionally performs no model call. A path therefore
    remains available when content generation providers are unconfigured.
    """
    from traittutor import learning_packs
    from traittutor.assessment.big_five import list_trait_profiles
    from traittutor.assessment.support_profile import build_slr_action_support
    from traittutor.personalization.service import get_personalization_service

    material = pack.get("material") if isinstance(pack.get("material"), Mapping) else {}
    metadata = material.get("metadata") if isinstance(material.get("metadata"), Mapping) else {}
    analysis = dict(metadata.get("learner_analysis") or metadata.get("analysis") or {})
    goal_payload = pack.get("goal") if isinstance(pack.get("goal"), Mapping) else {}
    goal = str(goal_payload.get("text") or instruction or pack.get("title") or "").strip()
    service = get_personalization_service()
    subject = service.classify_subject(
        material_analysis=analysis,
        title=str(material.get("title") or pack.get("title") or ""),
        text=" ".join((str(material.get("text") or "")[:6000], goal)),
    )
    context = service.build_context(
        purpose="courseware", subject=subject,
        current_instruction=instruction or goal,
        material_analysis=analysis,
        title=str(material.get("title") or pack.get("title") or ""),
        text=str(material.get("text") or "")[:6000],
        session_id=f"learning-pack:{pack['pack_id']}",
    )
    subject_profile = service.subject_profile(subject.subject_id) if subject else None
    strategy_evidence = [item.model_dump() for item in (subject_profile.strategy_evidence if subject_profile else [])]
    profiles = list_trait_profiles()
    profile_id = str(pack.get("profile_id") or "")
    profile = next((item for item in profiles if str(item.get("profile_id")) == profile_id), None)
    if profile is None and profiles:
        profile = max(profiles, key=lambda item: str(item.get("created_at") or ""))
    profile_metadata = profile.get("metadata") if profile and isinstance(profile.get("metadata"), Mapping) else {}
    slr_support = dict(
        profile_metadata.get("slr_support")
        or (build_slr_action_support(profile.get("scores") or {}) if profile else {})
    )
    support_state = build_subject_support_state(
        slr_support,
        subject_id=subject.subject_id if subject else None,
        strategy_evidence=strategy_evidence,
    )
    affordances = infer_material_affordances(
        analysis,
        title=str(material.get("title") or pack.get("title") or ""),
        text=str(material.get("text") or ""),
        instruction=" ".join((instruction, " ".join(preferred_modalities or []))),
    )
    previous = learning_packs.get_component_plan(str(pack["pack_id"]), supersedes_plan_id or "")
    completed = [item for item in (previous or {}).get("components", []) if item.get("status") == "completed"]
    plan = LearningComponentSelector().select(
        pack_id=str(pack["pack_id"]), goal=goal,
        subject_ref=subject.model_dump() if subject else None,
        analysis_id=str(analysis.get("analysis_id") or "") or None,
        concept_signals=[item.model_dump() for item in context.relevant_concept_signals],
        support_state=support_state, affordances=affordances,
        supersedes_plan_id=supersedes_plan_id,
        completed_components=completed,
    )
    return plan.model_copy(update={"version": int(previous.get("version") or 1) + 1}) if previous else plan


def _stage(signals: list[Mapping[str, Any]]) -> BKTStage:
    if not signals:
        return "unobserved"
    if any(str(item.get("support_level")) == "needs_support" for item in signals):
        return "needs_support"
    probability = min(float(item.get("mastery_probability") or .2) for item in signals)
    return "supported" if probability >= .75 else "developing"


class LearningComponentSelector:
    """Select a small, explainable component sequence without an LLM call."""

    def __init__(self, catalog: Mapping[str, Any] | None = None) -> None:
        self.catalog = dict(catalog or load_learning_component_catalog())

    def select(
        self,
        *,
        pack_id: str,
        goal: str,
        subject_ref: Mapping[str, Any] | None,
        analysis_id: str | None,
        concept_signals: list[Mapping[str, Any]],
        support_state: SubjectSupportState,
        affordances: MaterialComponentAffordances,
        supersedes_plan_id: str | None = None,
        completed_components: list[Mapping[str, Any]] | None = None,
    ) -> LearningComponentPlan:
        stage = _stage(concept_signals)
        concept_refs = [str(item.get("concept_id") or "") for item in concept_signals if item.get("concept_id")][:8]
        evidence_refs = list(dict.fromkeys(
            str(ref) for item in concept_signals for ref in item.get("evidence_refs") or []
        ))[:24]
        strong = {
            key for key, value in support_state.dimensions.items()
            if str(value.get("emphasis") or "") == "strong"
        }
        sequence: list[tuple[LearningComponentType, list[str], bool, str]] = []

        sequence.append(("goal_map", ["goal_planning"], True, "Make the learning target and completion criteria visible."))
        if stage == "unobserved":
            sequence.append(("diagnostic_check", ["monitoring_regulation"], True, "There is no graded evidence for this subject yet, so begin with a short diagnostic."))
            sequence.append(("concept_explanation", [], True, "Establish a source-grounded foundation after the diagnostic."))
        elif stage == "needs_support":
            sequence.append(("concept_explanation", ["monitoring_regulation"], True, "Current concept evidence indicates that explanation and recovery support are needed."))
            sequence.append(("worked_example", ["monitoring_regulation"], True, "A worked example reduces the gap between explanation and independent practice."))
        elif stage == "developing":
            sequence.append(("guided_practice", ["monitoring_regulation"], True, "Developing knowledge needs supported retrieval and immediate feedback."))
        else:
            sequence.append(("transfer_challenge", ["reflection_transfer"], True, "Current evidence supports applying the concept in a new context."))

        if "monitoring_regulation" in strong and not any(item[0] == "worked_example" for item in sequence):
            sequence.append(("worked_example", ["monitoring_regulation"], False, "Current subject feedback supports adding one more modeled step before independent work."))

        if affordances.worked_example.suitable and not any(item[0] == "worked_example" for item in sequence):
            sequence.append(("worked_example", [], False, "The material contains a procedure, calculation, code path, or case suitable for a worked example."))
        if affordances.visual.suitable:
            sequence.append(("visual_map", ["goal_planning"], False, "The source contains relationships or structure that can be clarified visually."))
        if affordances.audio.suitable:
            sequence.append(("audio_explanation", [], False, "The goal or material benefits from spoken explanation or pronunciation support."))
        if affordances.practice.suitable and not any(item[0] in {"diagnostic_check", "guided_practice", "transfer_challenge"} for item in sequence[-1:]):
            sequence.append(("guided_practice", ["monitoring_regulation"], True, "Practice produces evidence that can safely adjust the next step."))
        sequence.append(("retrieval_card", ["monitoring_regulation"], True, "Active recall keeps focal concepts available for later review."))
        checkpoint_dimensions = [
            dimension for dimension in ("goal_planning", "monitoring_regulation", "motivation_emotion")
            if dimension in strong
        ]
        if checkpoint_dimensions:
            sequence.append(("progress_checkpoint", checkpoint_dimensions, False, "Current support evidence adds a low-pressure checkpoint, visible progress, and a recovery option."))
        if "reflection_transfer" in strong:
            sequence.append(("reflection_prompt", ["reflection_transfer"], False, "Reflection support asks the learner to restate and connect the idea."))
        if stage != "unobserved" and concept_signals:
            sequence.append(("review_queue", ["motivation_emotion"], False, "Observed concepts remain available in the subject-scoped review queue."))

        completed = [LearningComponent.model_validate(item) for item in list(completed_components or [])]
        if any(item.component_type == "goal_map" for item in completed):
            sequence = [item for item in sequence if item[0] != "goal_map"]
        components: list[LearningComponent] = completed[:]
        previous_id = components[-1].component_id if components else None
        catalog = dict(self.catalog.get("components") or {})
        for index, (component_type, dimensions, required, reason) in enumerate(sequence, start=1):
            definition = dict(catalog[component_type])
            component_id = f"cmp_{index:02d}_{uuid4().hex[:10]}"
            modality = "visual" if component_type == "visual_map" else "audio" if component_type == "audio_explanation" else "interactive" if definition["executor"] in {"assessment", "retrieval"} else "text"
            component = LearningComponent(
                component_id=component_id,
                component_type=component_type,
                executor=definition["executor"],
                label_zh=definition["label_zh"],
                label_en=definition["label_en"],
                concept_refs=concept_refs,
                support_dimensions=dimensions,
                bkt_stage=stage,
                modality=modality,
                dependencies=[previous_id] if previous_id else [],
                required=required,
                reason=reason,
                evidence_refs=evidence_refs,
                completion_event=definition["completion_event"],
            )
            components.append(component)
            previous_id = component_id

        created = _now()
        return LearningComponentPlan(
            plan_id=f"plan_{uuid4().hex}",
            pack_id=pack_id,
            version=1 + (1 if supersedes_plan_id else 0),
            goal=goal.strip()[:240] or "Build understanding from the selected learning source",
            subject_ref=dict(subject_ref) if subject_ref else None,
            analysis_id=analysis_id,
            support_state_snapshot=support_state,
            bkt_snapshot_ref=(f"subject:{subject_ref.get('subject_id')}" if subject_ref and subject_ref.get("subject_id") else None),
            components=components,
            supersedes_plan_id=supersedes_plan_id,
            created_at=created,
            updated_at=created,
        )


__all__ = [
    "LearningComponent", "LearningComponentPlan", "LearningComponentSelector",
    "MaterialComponentAffordances", "SubjectSupportState",
    "build_subject_support_state", "infer_material_affordances",
    "build_learning_component_plan",
    "load_learning_component_catalog",
]
