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
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Mapping, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# ``load_prompt`` is imported lazily inside the functions that need it:
# ``traittutor.generate.catalog`` sits behind ``traittutor.generate.__init__``,
# which imports ``generate.service`` -> ``generate.material_analysis`` -> this
# module, so a module-level import here creates a circular import that fails
# ``import traittutor.learning_components`` directly.
#
# The same applies to ``generate.runner``: importing ANY ``traittutor.generate.*``
# submodule executes ``generate/__init__`` first, which starts the chain back
# into this module.  ``run_structured_prompt`` is therefore imported lazily at
# call sites and ``LLMRunMetadata`` is only a typing forward reference.
if TYPE_CHECKING:
    from traittutor.generate.runner import LLMRunMetadata

LearningComponentType = Literal[
    "goal_map",
    "concept_explanation",
    "worked_example",
    "visual_map",
    "video_explanation",
    "audio_explanation",
    "diagnostic_check",
    "guided_practice",
    "calibration_checkpoint",
    "retrieval_card",
    "progress_checkpoint",
    "reflection_prompt",
    "transfer_challenge",
    "review_queue",
]
ComponentExecutor = Literal[
    "deterministic", "lesson", "retrieval", "assessment", "image", "video", "audio"
]
ComponentStatus = Literal["pending", "active", "completed", "skipped", "degraded"]
BKTStage = Literal["unobserved", "needs_support", "developing", "supported"]
ArrangementState = Literal["pending", "llm", "deterministic_fallback"]
StructuredRunner = Callable[..., Awaitable[tuple[dict[str, Any], "LLMRunMetadata"]]]

_SUPPORT_DIMENSIONS = frozenset(
    {"goal_planning", "monitoring_regulation", "motivation_emotion", "reflection_transfer"}
)

# Server-graded component types. A diagnostic is deliberately excluded from
# BKT and calibration projections: it gives one local judgement, while guided
# practice and transfer remain trusted learning evidence.
_ASSESSMENT_COMPONENT_TYPES = frozenset(
    {"diagnostic_check", "guided_practice", "transfer_challenge"}
)
_EVIDENCE_ASSESSMENT_COMPONENT_TYPES = frozenset({"guided_practice", "transfer_challenge"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _mapping_field(container: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = container.get(key)
    return value if isinstance(value, Mapping) else {}


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
    modality: Literal["text", "interactive", "visual", "video", "audio"]
    dependencies: list[str] = Field(default_factory=list, max_length=8)
    required: bool = True
    reason: str
    evidence_refs: list[str] = Field(default_factory=list, max_length=24)
    completion_event: str
    status: ComponentStatus = "pending"
    output_ref: str | None = None
    media_url: str | None = None
    reattempt_of_component_id: str | None = None


class LearningComponentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    pack_id: str
    version: int = Field(default=1, ge=1)
    goal: str
    subject_ref: dict[str, Any] | None = None
    analysis_id: str | None = None
    support_state_snapshot: SubjectSupportState = Field(default_factory=SubjectSupportState)
    bkt_snapshot_ref: str | None = None
    components: list[LearningComponent] = Field(min_length=1)
    status: Literal["active", "completed", "superseded"] = "active"
    supersedes_plan_id: str | None = None
    reattempt_of_component_id: str | None = None
    reattempt_component_id: str | None = None
    reattempt_idempotency_key: str | None = None
    arrangement: ArrangementState = "pending"
    arrangement_rationale: str | None = None
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
    evidence = [
        item
        for item in data.get("page_evidence") or data.get("evidence") or []
        if isinstance(item, Mapping)
    ]
    refs = [str(item.get("chunk_id") or item.get("page") or "") for item in evidence if item]
    haystack = " ".join(
        (title, text[:5000], str(data.get("subject") or ""), str(data.get("sub_subject") or ""))
    ).lower()
    request = instruction.lower()

    visual_terms = (
        "diagram",
        "graph",
        "chart",
        "流程",
        "结构",
        "关系",
        "函数",
        "几何",
        "地图",
        "图表",
    )
    audio_terms = (
        "english",
        "language",
        "pronunciation",
        "listening",
        "英语",
        "语言",
        "发音",
        "听力",
        "朗读",
    )
    example_terms = (
        "equation",
        "calculation",
        "procedure",
        "code",
        "case",
        "公式",
        "计算",
        "步骤",
        "代码",
        "案例",
    )
    visual = any(term in haystack for term in visual_terms) or any(
        term in request for term in ("图解", "图表", "visual", "diagram")
    )
    audio = any(term in haystack for term in audio_terms) or any(
        term in request for term in ("语音", "听", "朗读", "audio", "pronounce")
    )
    worked = any(term in haystack for term in example_terms)
    has_source = bool(title or text or data)

    def affordance(suitable: bool, confidence: float, reason: str) -> MaterialAffordance:
        return MaterialAffordance(
            suitable=suitable,
            confidence=confidence if suitable else min(confidence, 0.45),
            reasons=[reason],
            evidence_refs=refs[:12],
        )

    return MaterialComponentAffordances(
        visual=affordance(
            visual,
            0.72 if visual else 0.35,
            "The material contains relationships or structures that benefit from a visual representation.",
        ),
        audio=affordance(
            audio,
            0.78 if audio else 0.25,
            "The goal or material benefits from listening, pronunciation, or spoken explanation.",
        ),
        worked_example=affordance(
            worked,
            0.74 if worked else 0.42,
            "The material contains procedures, calculations, code, or cases that benefit from a worked example.",
        ),
        practice=affordance(
            has_source,
            0.82 if has_source else 0.55,
            "Practice can produce answerable evidence for the current learning goal.",
        ),
    )


def _primary_material(pack: Mapping[str, Any]) -> Mapping[str, Any]:
    materials = pack.get("materials")
    if isinstance(materials, list) and materials and isinstance(materials[0], Mapping):
        return materials[0]
    return {}


def _material_analysis(pack: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _mapping_field(_primary_material(pack), "metadata")
    raw = metadata.get("learner_analysis") or metadata.get("analysis") or {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _prompt_concepts(
    analysis: Mapping[str, Any], baseline: LearningComponentPlan
) -> list[dict[str, Any]]:
    """Build stable, server-owned concept handles for structured prompts."""
    candidates = analysis.get("concept_candidates") or analysis.get("core_concepts") or []
    rows: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for index, candidate in enumerate(candidates, start=1):
            if isinstance(candidate, Mapping):
                concept_id = str(
                    candidate.get("concept_id") or candidate.get("id") or f"concept-{index}"
                ).strip()
                label = str(candidate.get("label") or candidate.get("name") or concept_id).strip()
                verified = candidate.get("verified_observation_count")
                support_level = str(candidate.get("support_level") or "")
            else:
                concept_id = f"concept-{index}"
                label = str(candidate).strip()
                verified = 0
                support_level = ""
            if concept_id and label:
                rows.append(
                    {
                        "concept_id": concept_id[:160],
                        "label": label[:240],
                        "support_level": support_level or baseline.components[0].bkt_stage,
                        "verified_observation_count": (
                            verified
                            if isinstance(verified, int) and not isinstance(verified, bool)
                            else 0
                        ),
                    }
                )
    known_ids = {str(item["concept_id"]) for item in rows}
    for concept_id in dict.fromkeys(
        ref for component in baseline.components for ref in component.concept_refs if ref
    ):
        if concept_id in known_ids:
            continue
        rows.append(
            {
                "concept_id": concept_id,
                "label": concept_id,
                "support_level": baseline.components[0].bkt_stage,
                "verified_observation_count": 0,
            }
        )
    return rows[:12]


def collect_learning_arrangement_inputs(
    pack: Mapping[str, Any],
    *,
    baseline_plan: Mapping[str, Any] | LearningComponentPlan | None = None,
) -> dict[str, Any]:
    """Collect the trusted Pack/BKT/SRL/material slice used by both new prompts."""
    baseline = (
        baseline_plan
        if isinstance(baseline_plan, LearningComponentPlan)
        else LearningComponentPlan.model_validate(baseline_plan)
        if baseline_plan is not None
        else build_learning_component_plan(pack)
    )
    material = _primary_material(pack)
    analysis = _material_analysis(pack)
    goal_payload = _mapping_field(pack, "goal")
    affordances = infer_material_affordances(
        analysis,
        title=str(material.get("title") or pack.get("title") or ""),
        text=str(material.get("text") or ""),
        instruction=baseline.goal,
    )
    concepts = _prompt_concepts(analysis, baseline)
    return {
        "goal": str(goal_payload.get("text") or baseline.goal),
        "subject": dict(baseline.subject_ref or {}),
        "material_analysis": {
            key: analysis[key]
            for key in ("summary", "core_concepts", "difficulty_points")
            if key in analysis
        },
        "concepts": concepts,
        "bkt_stage": baseline.components[0].bkt_stage,
        "srl_support": baseline.support_state_snapshot.model_dump(mode="json"),
        "affordances": affordances.model_dump(mode="json"),
        "material_text_excerpt": str(material.get("text") or "")[:4000],
    }


def validate_pre_assessment_payload(
    value: Mapping[str, Any], *, concept_ids: set[str]
) -> Mapping[str, Any]:
    if set(value) != {"needed", "reason", "probes"}:
        raise ValueError("pre-assessment requires only needed, reason, and probes")
    needed = value.get("needed")
    if not isinstance(needed, bool):
        raise ValueError("pre-assessment needed must be boolean")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 600:
        raise ValueError("pre-assessment reason must be non-empty text")
    probes = value.get("probes")
    if not isinstance(probes, list):
        raise ValueError("pre-assessment probes must be a list")
    if not needed:
        if probes:
            raise ValueError("pre-assessment probes must be empty when needed is false")
        return {"needed": False, "reason": reason.strip(), "probes": []}
    if not 1 <= len(probes) <= 5:
        raise ValueError("pre-assessment requires one to five probes")
    validated: list[dict[str, Any]] = []
    for probe in probes:
        if not isinstance(probe, Mapping) or set(probe) != {
            "concept_id",
            "concept_label",
            "question",
            "options",
            "correct_index",
            "rationale",
        }:
            raise ValueError("pre-assessment probe shape is invalid")
        concept_id = str(probe.get("concept_id") or "").strip()
        if not concept_id or (concept_ids and concept_id not in concept_ids):
            raise ValueError("pre-assessment probe concept_id is not in the supplied concepts")
        concept_label = str(probe.get("concept_label") or "").strip()
        question = str(probe.get("question") or "").strip()
        rationale = str(probe.get("rationale") or "").strip()
        options = probe.get("options")
        correct_index = probe.get("correct_index")
        if not concept_label or not question or not rationale:
            raise ValueError("pre-assessment probe text fields must be non-empty")
        if not isinstance(options, list) or not 2 <= len(options) <= 6:
            raise ValueError("pre-assessment probe requires two to six options")
        normalized_options = [str(option).strip() for option in options]
        if any(not option for option in normalized_options):
            raise ValueError("pre-assessment options must be non-empty")
        if (
            not isinstance(correct_index, int)
            or isinstance(correct_index, bool)
            or not 0 <= correct_index < len(normalized_options)
        ):
            raise ValueError("pre-assessment correct_index is outside the option range")
        validated.append(
            {
                "concept_id": concept_id,
                "concept_label": concept_label[:240],
                "question": question[:1200],
                "options": normalized_options,
                "correct_index": correct_index,
                "rationale": rationale[:1200],
            }
        )
    return {"needed": True, "reason": reason.strip(), "probes": validated}


async def judge_and_generate_pre_assessment(
    pack: Mapping[str, Any], *, run: StructuredRunner | None = None
) -> dict[str, Any]:
    inputs = collect_learning_arrangement_inputs(pack)
    concept_ids = {
        str(item.get("concept_id") or "")
        for item in inputs["concepts"]
        if isinstance(item, Mapping) and item.get("concept_id")
    }
    # Delayed import: a module-level ``generate.catalog`` import would create a
    # circular import through ``generate/__init__`` -> ``service`` ->
    # ``material_analysis`` -> this module (see the module docstring note).
    from traittutor.generate.catalog import load_prompt
    from traittutor.generate.runner import run_structured_prompt

    prompt = load_prompt("pre-assessment/judge-and-probe.md", {"input_json": inputs})

    def validate(value: Mapping[str, Any]) -> Mapping[str, Any]:
        return validate_pre_assessment_payload(value, concept_ids=concept_ids)

    payload, _metadata = await (run or run_structured_prompt)(
        prompt,
        validate=validate,
        # Demo-mode speed bound: the runner's default ``high`` reasoning tier
        # can exceed the 180s generation-gateway budget on slower providers
        # (e.g. MiniMax), failing the pre-assessment's first step. Keep ``low``
        # while demoing; restore ``high`` before general availability (same
        # failure mode and rationale as ``arrange_learning_component_plan``).
        reasoning_effort="low",
    )
    return payload


def validate_arrangement_payload(
    value: Mapping[str, Any], *, catalog: Mapping[str, Any]
) -> Mapping[str, Any]:
    if set(value) != {"rationale", "components"}:
        raise ValueError("arrangement requires only rationale and components")
    rationale = value.get("rationale")
    components = value.get("components")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1200:
        raise ValueError("arrangement rationale must be non-empty text")
    if not isinstance(components, list) or not 1 <= len(components) <= 12:
        raise ValueError("arrangement requires one to twelve components")
    allowed = set(dict(catalog.get("components") or {}))
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for component in components:
        if not isinstance(component, Mapping) or set(component) != {
            "component_type",
            "reason",
            "support_dimensions",
            "required",
        }:
            raise ValueError("arrangement component shape is invalid; dependencies are forbidden")
        component_type = str(component.get("component_type") or "").strip()
        reason = str(component.get("reason") or "").strip()
        dimensions = component.get("support_dimensions")
        required = component.get("required")
        if component_type not in allowed:
            raise ValueError(f"unknown arrangement component type: {component_type}")
        if component_type in seen:
            raise ValueError(f"duplicate arrangement component type: {component_type}")
        if not reason or len(reason) > 300:
            raise ValueError("arrangement component reason is invalid")
        if not isinstance(dimensions, list) or any(
            not isinstance(item, str) or item not in _SUPPORT_DIMENSIONS for item in dimensions
        ):
            raise ValueError("arrangement support_dimensions are invalid")
        if not isinstance(required, bool):
            raise ValueError("arrangement required must be boolean")
        seen.add(component_type)
        validated.append(
            {
                "component_type": component_type,
                "reason": reason,
                "support_dimensions": list(dict.fromkeys(dimensions)),
                "required": required,
            }
        )
    if validated[0]["component_type"] != "goal_map":
        raise ValueError("goal_map must be the first arranged component")
    if not any(component["required"] for component in validated):
        raise ValueError("arrangement requires at least one required component")
    for index, component in enumerate(validated):
        if component["component_type"] != "calibration_checkpoint":
            continue
        if index == 0 or validated[index - 1]["component_type"] not in _ASSESSMENT_COMPONENT_TYPES:
            raise ValueError("calibration_checkpoint must immediately follow an assessment")
    return {"rationale": rationale.strip(), "components": validated}


def _component_modality(
    component_type: str, executor: str
) -> Literal["text", "interactive", "visual", "video", "audio"]:
    if component_type == "visual_map":
        return "visual"
    if component_type == "video_explanation":
        return "video"
    if component_type == "audio_explanation":
        return "audio"
    if executor in {"assessment", "retrieval"}:
        return "interactive"
    return "text"


def build_arranged_learning_component_plan(
    active_plan: Mapping[str, Any], arrangement: Mapping[str, Any]
) -> LearningComponentPlan:
    baseline = LearningComponentPlan.model_validate(active_plan)
    catalog = load_learning_component_catalog()
    validated = validate_arrangement_payload(arrangement, catalog=catalog)
    definitions = dict(catalog.get("components") or {})
    stage = baseline.components[0].bkt_stage
    concept_refs = list(
        dict.fromkeys(ref for component in baseline.components for ref in component.concept_refs)
    )[:12]
    evidence_refs = list(
        dict.fromkeys(ref for component in baseline.components for ref in component.evidence_refs)
    )[:24]
    # The canvas may retry a pending/fallen-back arrangement after the learner
    # has already started (e.g. the goal map auto-completes on open). The
    # arranged plan is a NEW superseding plan, so started work is never
    # rewritten: keep the full started prefix immutable (mirroring the replan
    # rule, including the calibration that follows a preserved assessment) and
    # apply the LLM arrangement only to the not-yet-started tail.
    preserved = [component for component in baseline.components if component.status != "pending"]
    if (
        preserved
        and preserved[-1].component_type in _ASSESSMENT_COMPONENT_TYPES
        and len(baseline.components) > len(preserved)
        and baseline.components[len(preserved)].component_type == "calibration_checkpoint"
    ):
        preserved = baseline.components[: len(preserved) + 1]
    arranged_decisions = list(validated["components"])
    if preserved:
        preserved_types = {component.component_type for component in preserved}
        arranged_decisions = [
            decision
            for decision in arranged_decisions
            if decision["component_type"] not in preserved_types
        ]
        # Two graded assessments may never be adjacent: drop an arranged
        # assessment that would land directly after a preserved (possibly
        # in-flight) assessment without its own calibration — the learner's
        # current attempt stays the active evidence step.
        if (
            preserved[-1].component_type in _EVIDENCE_ASSESSMENT_COMPONENT_TYPES
            and arranged_decisions
            and arranged_decisions[0]["component_type"] in _EVIDENCE_ASSESSMENT_COMPONENT_TYPES
        ):
            arranged_decisions = arranged_decisions[1:]
    components: list[LearningComponent] = list(preserved)
    pending_assessment_id: str | None = next(
        (
            component.component_id
            for component in reversed(preserved)
            if component.component_type in _ASSESSMENT_COMPONENT_TYPES
        ),
        None,
    )
    for index, decision in enumerate(arranged_decisions, start=len(components) + 1):
        component_type = cast(LearningComponentType, str(decision["component_type"]))
        definition = dict(definitions[component_type])
        component_id = f"cmp_{index:02d}_{uuid4().hex[:10]}"
        components.append(
            LearningComponent(
                component_id=component_id,
                component_type=component_type,
                executor=definition["executor"],
                label_zh=definition["label_zh"],
                label_en=definition["label_en"],
                concept_refs=concept_refs,
                support_dimensions=list(decision["support_dimensions"]),
                bkt_stage=stage,
                modality=_component_modality(component_type, str(definition["executor"])),
                dependencies=(
                    [pending_assessment_id]
                    if component_type == "calibration_checkpoint" and pending_assessment_id
                    else []
                ),
                required=bool(decision["required"]),
                reason=str(decision["reason"]),
                evidence_refs=evidence_refs,
                completion_event=definition["completion_event"],
            )
        )
        if component_type in _ASSESSMENT_COMPONENT_TYPES:
            pending_assessment_id = component_id
        elif component_type == "calibration_checkpoint":
            pending_assessment_id = None
    created = _now()
    return LearningComponentPlan(
        plan_id=f"plan_{uuid4().hex}",
        pack_id=baseline.pack_id,
        version=baseline.version + 1,
        goal=baseline.goal,
        subject_ref=baseline.subject_ref,
        analysis_id=baseline.analysis_id,
        support_state_snapshot=baseline.support_state_snapshot,
        bkt_snapshot_ref=baseline.bkt_snapshot_ref,
        components=components,
        supersedes_plan_id=baseline.plan_id,
        arrangement="llm",
        arrangement_rationale=str(validated["rationale"]),
        created_at=created,
        updated_at=created,
    )


# Progress-calibration strategy -> support components the follow-up plan
# inserts right after the calibration. ``transfer_or_schedule_review`` inserts
# nothing: a smooth round keeps the plan unchanged.
_CALIBRATION_FOLLOWUP_SUPPORT: dict[str, tuple[LearningComponentType, ...]] = {
    "repair_with_contrast": ("worked_example",),
    "worked_example_then_guided_retry": (
        "worked_example",
        "guided_practice",
        "calibration_checkpoint",
    ),
    "self_explain_then_retrieve": ("reflection_prompt",),
    "transfer_or_schedule_review": (),
}

_CALIBRATION_FOLLOWUP_REASONS: dict[str, str] = {
    "worked_example": (
        "The progress calibration suggests reviewing a modeled example before continuing."
    ),
    "guided_practice": "The progress calibration suggests another graded practice round.",
    "calibration_checkpoint": (
        "Compare predicted confidence with verified feedback after the retry round."
    ),
    "reflection_prompt": (
        "The progress calibration suggests restating the key steps in your own words."
    ),
}


def build_calibrated_followup_plan(
    active_plan: Mapping[str, Any], progress_calibration: Mapping[str, Any]
) -> LearningComponentPlan | None:
    """Build the follow-up plan a completed progress calibration implies.

    Minimal-insertion semantics: the started prefix stays immutable (including
    the calibration that follows a preserved assessment), the LLM-arranged tail
    keeps its order, and only supports the strategy names — that are not
    already present anywhere in the plan — are inserted right after the
    calibration. The plan keeps its ``arrangement`` state (no fallback banner)
    and returns ``None`` when nothing needs to change (a smooth round).
    """
    strategy = str(progress_calibration.get("recommended_strategy") or "").strip()
    support = _CALIBRATION_FOLLOWUP_SUPPORT.get(strategy)
    if not support:
        return None
    baseline = LearningComponentPlan.model_validate(active_plan)
    preserved = [component for component in baseline.components if component.status != "pending"]
    if (
        preserved
        and preserved[-1].component_type in _ASSESSMENT_COMPONENT_TYPES
        and len(baseline.components) > len(preserved)
        and baseline.components[len(preserved)].component_type == "calibration_checkpoint"
    ):
        preserved = baseline.components[: len(preserved) + 1]
    tail = baseline.components[len(preserved) :]
    present_types = {component.component_type for component in baseline.components}
    insert_types = [
        component_type for component_type in support if component_type not in present_types
    ]
    if not insert_types:
        return None
    catalog = load_learning_component_catalog()
    definitions = dict(catalog.get("components") or {})
    stage = baseline.components[0].bkt_stage
    concept_refs = list(
        dict.fromkeys(ref for component in baseline.components for ref in component.concept_refs)
    )[:12]
    evidence_refs = list(
        dict.fromkeys(ref for component in baseline.components for ref in component.evidence_refs)
    )[:24]
    inserted: list[LearningComponent] = []
    pending_assessment_id: str | None = None
    for offset, component_type in enumerate(insert_types, start=1):
        definition = dict(definitions[str(component_type)])
        component_id = f"cmp_{len(preserved) + offset:02d}_{uuid4().hex[:10]}"
        inserted.append(
            LearningComponent(
                component_id=component_id,
                component_type=component_type,
                executor=definition["executor"],
                label_zh=definition["label_zh"],
                label_en=definition["label_en"],
                concept_refs=concept_refs,
                support_dimensions=["monitoring_regulation"],
                bkt_stage=stage,
                modality=_component_modality(str(component_type), str(definition["executor"])),
                dependencies=(
                    [pending_assessment_id]
                    if component_type == "calibration_checkpoint" and pending_assessment_id
                    else []
                ),
                required=True,
                reason=_CALIBRATION_FOLLOWUP_REASONS[str(component_type)],
                evidence_refs=evidence_refs,
                completion_event=definition["completion_event"],
            )
        )
        if component_type in _ASSESSMENT_COMPONENT_TYPES:
            pending_assessment_id = component_id
        elif component_type == "calibration_checkpoint":
            pending_assessment_id = None
    created = _now()
    return LearningComponentPlan(
        plan_id=f"plan_{uuid4().hex}",
        pack_id=baseline.pack_id,
        version=baseline.version + 1,
        goal=baseline.goal,
        subject_ref=baseline.subject_ref,
        analysis_id=baseline.analysis_id,
        support_state_snapshot=baseline.support_state_snapshot,
        bkt_snapshot_ref=baseline.bkt_snapshot_ref,
        components=[*preserved, *inserted, *tail],
        supersedes_plan_id=baseline.plan_id,
        # The follow-up plan only inserts supports; it does not re-arrange the
        # LLM order, so the original arrangement state stays truthful and the
        # canvas does not surface a pending/fallback banner.
        arrangement=baseline.arrangement,
        arrangement_rationale=baseline.arrangement_rationale,
        created_at=created,
        updated_at=created,
    )


async def arrange_learning_component_plan(
    pack: Mapping[str, Any],
    active_plan: Mapping[str, Any],
    *,
    run: StructuredRunner | None = None,
) -> LearningComponentPlan:
    baseline = LearningComponentPlan.model_validate(active_plan)
    inputs = collect_learning_arrangement_inputs(pack, baseline_plan=baseline)
    catalog = load_learning_component_catalog()
    pre_assessment = pack.get("pre_assessment")
    pre_payload: dict[str, Any] = {}
    if isinstance(pre_assessment, Mapping):
        probes = {
            str(item.get("question_id") or ""): item
            for item in pre_assessment.get("probes") or []
            if isinstance(item, Mapping)
        }
        pre_payload = {
            "status": pre_assessment.get("status"),
            "results": [
                {
                    "question_id": response.get("question_id"),
                    "concept_id": probes.get(str(response.get("question_id") or ""), {}).get(
                        "concept_id"
                    ),
                    "correct": response.get("correct"),
                    "confidence": response.get("confidence"),
                }
                for response in pre_assessment.get("responses") or []
                if isinstance(response, Mapping)
            ],
        }
    prompt_input = {
        **inputs,
        "pre_assessment": pre_payload,
        "catalog": {
            "components": {
                key: {
                    field: definition[field]
                    for field in ("label_zh", "label_en", "executor")
                    if field in definition
                }
                for key, definition in dict(catalog.get("components") or {}).items()
                if isinstance(definition, Mapping)
            }
        },
        "current_plan": {
            key: active_plan.get(key)
            for key in (
                "plan_id",
                "version",
                "subject_ref",
                "analysis_id",
                "support_state_snapshot",
            )
        },
    }
    # Delayed import: a module-level ``generate.catalog`` import would create a
    # circular import through ``generate/__init__`` -> ``service`` ->
    # ``material_analysis`` -> this module (see the module docstring note).
    from traittutor.generate.catalog import load_prompt
    from traittutor.generate.runner import run_structured_prompt

    prompt = load_prompt("arrangement/arrange-components.md", {"input_json": prompt_input})

    def validate(value: Mapping[str, Any]) -> Mapping[str, Any]:
        return validate_arrangement_payload(value, catalog=catalog)

    payload, _metadata = await (run or run_structured_prompt)(
        prompt,
        validate=validate,
        # Demo-mode speed bound: the runner's default ``high`` reasoning tier
        # can exceed the 180s generation-gateway budget on slower providers
        # (same failure mode as the instruction executor), failing the whole
        # arrangement and forcing the deterministic fallback. Keep ``low``
        # while demoing; restore ``high`` before general availability.
        reasoning_effort="low",
    )
    return build_arranged_learning_component_plan(active_plan, payload)


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
    candidates: dict[str, list[tuple[int, str, Mapping[str, Any]]]] = {}
    for item in evidence:
        strategy = _mapping_field(item, "strategy")
        inferred_dimensions = set(item.get("support_dimensions") or [])
        if strategy.get("structure") or strategy.get("scaffolding"):
            inferred_dimensions.update({"goal_planning", "monitoring_regulation"})
        if strategy.get("feedback"):
            inferred_dimensions.update({"monitoring_regulation", "motivation_emotion"})
        if strategy.get("pacing") or strategy.get("interaction"):
            inferred_dimensions.add("motivation_emotion")
        if strategy.get("challenge") or strategy.get("example_style"):
            inferred_dimensions.add("reflection_transfer")
        positive = int(float(item.get("positive_weight") or 0))
        negative = int(float(item.get("negative_weight") or 0))
        event_ids = {str(event_id) for event_id in item.get("event_ids") or [] if str(event_id)}
        if positive + negative > len(event_ids):
            # Corrupt/imported weights may never manufacture more observations
            # than the auditable events attached to this exact strategy.
            continue
        direction = "strong" if positive > negative else "light" if negative > positive else ""
        consistent_count = min(max(positive, negative), len(event_ids))
        # StrategyEvidence is already grouped by exact strategy. Only one
        # direction from one strategy may cross the automatic-adjustment gate.
        if not direction or consistent_count < 3:
            continue
        for dimension in inferred_dimensions:
            if dimension not in dimensions:
                continue
            candidates.setdefault(dimension, []).append((consistent_count, direction, item))
    evidence_applied = False
    for dimension, options in candidates.items():
        count, direction, _item = max(
            options,
            key=lambda option: (option[0], option[1] == "strong", str(option[2].get("id") or "")),
        )
        current = dict(dimensions[dimension])
        # An explicit learner choice is the highest-priority regulation
        # signal. Automatic subject evidence may inform other dimensions but
        # cannot silently undo a choice or refusal.
        if current.get("source") == "learner_choice":
            continue
        current.update(
            {
                "evidence_count": count,
                "emphasis": direction,
                "source": "subject_evidence",
                "confidence": min(0.95, 0.5 + count * 0.1),
            }
        )
        dimensions[dimension] = current
        evidence_applied = True
    source: Literal["initial_profile", "subject_evidence", "default"] = (
        "subject_evidence" if evidence_applied else "initial_profile" if dimensions else "default"
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

    # Canonical Packs store an ordered ``materials`` collection.  The retired
    # singular ``material`` field is intentionally not revived: using it here
    # made every ordinary Learn upload look empty to subject classification
    # and the deterministic component selector.
    materials = pack.get("materials")
    material = (
        materials[0]
        if isinstance(materials, list) and materials and isinstance(materials[0], Mapping)
        else {}
    )
    metadata = _mapping_field(material, "metadata")
    analysis = dict(metadata.get("learner_analysis") or metadata.get("analysis") or {})
    goal_payload = _mapping_field(pack, "goal")
    goal = str(goal_payload.get("text") or instruction or pack.get("title") or "").strip()
    service = get_personalization_service()
    subject = service.classify_subject(
        material_analysis=analysis,
        title=str(material.get("title") or pack.get("title") or ""),
        text=" ".join((str(material.get("text") or "")[:6000], goal)),
    )
    context = service.build_context(
        purpose="courseware",
        subject=subject,
        current_instruction=instruction or goal,
        material_analysis=analysis,
        title=str(material.get("title") or pack.get("title") or ""),
        text=str(material.get("text") or "")[:6000],
        session_id=f"learning-pack:{pack['pack_id']}",
    )
    subject_profile = service.subject_profile(subject.subject_id) if subject else None
    strategy_evidence: list[Mapping[str, Any]] = [
        item.model_dump() for item in (subject_profile.strategy_evidence if subject_profile else [])
    ]
    profiles = list_trait_profiles()
    profile_id = str(pack.get("profile_id") or "")
    profile = next((item for item in profiles if str(item.get("profile_id")) == profile_id), None)
    if profile is None and profiles:
        profile = max(profiles, key=lambda item: str(item.get("created_at") or ""))
    profile_metadata = _mapping_field(profile, "metadata") if profile else {}
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
    # Similar-item practice is inserted into the immutable path rather than
    # replacing it, so later calibration replans must preserve that real
    # started prefix just like every other superseding plan.
    baseline = previous
    # A replan may replace only work that has not started.  Preserve the full
    # historical prefix, rather than just completed rows: an active assessment
    # can emit useful feedback before it is completed.
    previous_components = list((baseline or {}).get("components", []))
    goal_only = str(metadata.get("source_kind") or "") == "learning_goal"
    if goal_only and previous_components:
        first_teaching_index = next(
            (
                index
                for index, item in enumerate(previous_components)
                if item.get("component_type")
                in {
                    "concept_explanation",
                    "worked_example",
                    "visual_map",
                    "video_explanation",
                    "audio_explanation",
                }
            ),
            len(previous_components),
        )
        # Plans created before the goal-only quality fix may already have an
        # unattempted diagnostic before any teaching content. Do not carry that
        # invalid step into a superseding plan. Completed assessments remain
        # immutable historical evidence in the old plan.
        previous_components = [
            item
            for index, item in enumerate(previous_components)
            if not (
                index < first_teaching_index
                and item.get("component_type")
                in {"diagnostic_check", "guided_practice", "transfer_challenge"}
                and item.get("status") != "completed"
            )
        ]
    last_started = max(
        (
            index
            for index, item in enumerate(previous_components)
            if item.get("status") != "pending"
        ),
        default=-1,
    )
    preserved = previous_components[: last_started + 1]
    # Keep the calibration that follows a preserved assessment even when it is
    # still pending: dropping it would orphan the assessment's confidence
    # feedback and could place two graded assessments back to back in the
    # superseding plan (assessment-then-calibration invariant).
    if (
        preserved
        and preserved[-1].get("component_type") in _ASSESSMENT_COMPONENT_TYPES
        and last_started + 1 < len(previous_components)
        and previous_components[last_started + 1].get("component_type") == "calibration_checkpoint"
    ):
        preserved = previous_components[: last_started + 2]
    plan = LearningComponentSelector().select(
        pack_id=str(pack["pack_id"]),
        goal=goal,
        subject_ref=subject.model_dump() if subject else None,
        analysis_id=str(analysis.get("analysis_id") or "") or None,
        # The selector is a deterministic internal computation (not a user
        # display), so it may read the raw posterior. The ConceptSignal
        # field_serializer hides mastery_probability for public/display dumps to
        # honour invariant #3 (no pseudo-precise uncalibrated posterior shown);
        # without this context it returns None -> _stage falls back to .2 and
        # classifies every learner as "developing".
        concept_signals=[
            item.model_dump(context={"include_uncalibrated_posterior": True})
            for item in context.relevant_concept_signals
        ],
        support_state=support_state,
        affordances=affordances,
        goal_only=goal_only,
        supersedes_plan_id=supersedes_plan_id,
        completed_components=preserved,
    )
    return (
        plan.model_copy(update={"version": int(previous.get("version") or 1) + 1})
        if previous
        else plan
    )


def _numeric_or_none(value: Any) -> float | None:
    """Coerce a posterior to float; malformed values degrade to None."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stage(signals: list[Mapping[str, Any]]) -> BKTStage:
    if not signals:
        return "unobserved"
    if any(str(item.get("support_level")) == "needs_support" for item in signals):
        return "needs_support"
    # The planner/selector may use private posterior thresholds only after the
    # calibrated three-observation gate. Public qualitative dumps intentionally
    # omit the posterior and therefore remain unobserved here.
    if any(
        not bool(item.get("bkt_calibrated"))
        or int(item.get("verified_observation_count") or 0) < 3
        or _numeric_or_none(item.get("mastery_probability")) is None
        for item in signals
    ):
        return "unobserved"
    posteriors = [
        posterior
        for item in signals
        if (posterior := _numeric_or_none(item.get("mastery_probability"))) is not None
    ]
    if not posteriors:
        return "unobserved"
    return "supported" if min(posteriors) >= 0.75 else "developing"


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
        goal_only: bool = False,
        supersedes_plan_id: str | None = None,
        completed_components: list[Mapping[str, Any]] | None = None,
    ) -> LearningComponentPlan:
        stage = _stage(concept_signals)
        concept_refs = [
            str(item.get("concept_id") or "") for item in concept_signals if item.get("concept_id")
        ][:8]
        evidence_refs = list(
            dict.fromkeys(
                str(ref) for item in concept_signals for ref in item.get("evidence_refs") or []
            )
        )[:24]
        strong = {
            key
            for key, value in support_state.dimensions.items()
            if str(value.get("emphasis") or "") == "strong"
        }
        preserved = [
            LearningComponent.model_validate(item) for item in list(completed_components or [])
        ]
        # Structural guard for superseding plans: when the preserved prefix
        # contains an assessment whose calibration is not preserved next to it
        # (an in-flight assessment or legacy data), appending another graded
        # assessment would put two
        # assessments back to back.  Skip the assessment moves in that case —
        # the learner's current attempt stays the active evidence step.
        preserved_assessment_without_calibration = any(
            item.component_type in _EVIDENCE_ASSESSMENT_COMPONENT_TYPES
            and (
                index + 1 >= len(preserved)
                or (
                    preserved[index + 1].component_type != "calibration_checkpoint"
                    and preserved[index + 1].reattempt_of_component_id != item.component_id
                )
            )
            for index, item in enumerate(preserved)
        )
        sequence: list[tuple[LearningComponentType, list[str], bool, str]] = []

        def assessment_with_calibration(
            component_type: LearningComponentType,
            dimensions: list[str],
            required: bool,
            reason: str,
        ) -> None:
            """Keep verified performance and metacognitive calibration adjacent.

            A calibration prompt is useful only when the learner has just made
            a prediction (their confidence) and can compare it with feedback.
            It remains separate from BKT: self-report is never mastery data.
            """
            sequence.append((component_type, dimensions, required, reason))
            sequence.append(
                (
                    "calibration_checkpoint",
                    ["monitoring_regulation"],
                    True,
                    "Compare predicted confidence with verified feedback, then choose a concrete next-study strategy.",
                )
            )

        goal_map_missing = not any(item.component_type == "goal_map" for item in preserved)
        if goal_map_missing:
            sequence.append(
                (
                    "goal_map",
                    ["goal_planning"],
                    True,
                    "Generate a source-grounded goal map with milestones and completion evidence.",
                )
            )

        if stage == "unobserved":
            sequence.append(
                (
                    "concept_explanation",
                    [],
                    True,
                    (
                        "Build a teaching foundation from the learning goal before offering optional practice components."
                        if goal_only
                        else "Start directly with a source-grounded explanation; an initial diagnostic is not a prerequisite for using learning components."
                    ),
                )
            )
            if not any(
                item.component_type == "diagnostic_check" and item.status == "completed"
                for item in preserved
            ):
                sequence.append(
                    (
                        "diagnostic_check",
                        ["monitoring_regulation"],
                        False,
                        "Offer one non-blocking starting judgement; its result never updates BKT or creates another plan.",
                    )
                )
        elif stage == "needs_support":
            sequence.append(
                (
                    "concept_explanation",
                    ["monitoring_regulation"],
                    True,
                    "Current concept evidence indicates that explanation and recovery support are needed.",
                )
            )
            sequence.append(
                (
                    "worked_example",
                    ["monitoring_regulation"],
                    True,
                    "A worked example reduces the gap between explanation and independent practice.",
                )
            )
        elif stage == "developing":
            if not preserved_assessment_without_calibration:
                assessment_with_calibration(
                    "guided_practice",
                    ["monitoring_regulation"],
                    True,
                    "Developing knowledge needs supported retrieval and immediate feedback.",
                )
        elif not preserved_assessment_without_calibration:
            assessment_with_calibration(
                "transfer_challenge",
                ["reflection_transfer"],
                True,
                "Current evidence supports applying the concept in a new context.",
            )

        if "monitoring_regulation" in strong and not any(
            item[0] == "worked_example" for item in sequence
        ):
            sequence.append(
                (
                    "worked_example",
                    ["monitoring_regulation"],
                    False,
                    "Current subject feedback supports adding one more modeled step before independent work.",
                )
            )

        if affordances.worked_example.suitable and not any(
            item[0] == "worked_example" for item in sequence
        ):
            sequence.append(
                (
                    "worked_example",
                    [],
                    False,
                    "The material contains a procedure, calculation, code path, or case suitable for a worked example.",
                )
            )
        sequence.append(
            (
                "visual_map",
                ["goal_planning"],
                False,
                (
                    "The source contains relationships or structure that can be clarified visually."
                    if affordances.visual.suitable
                    else "Add one source-grounded concept illustration for the focal learning goal."
                ),
            )
        )
        sequence.append(
            (
                "video_explanation",
                ["goal_planning"],
                False,
                "Offer one short source-grounded concept animation when the learner requests it.",
            )
        )
        sequence.append(
            (
                "audio_explanation",
                [],
                False,
                (
                    "The source benefits from a spoken podcast explanation."
                    if affordances.audio.suitable
                    else "Offer one optional source-grounded podcast narration for the focal learning goal."
                ),
            )
        )
        # Calibration and optional media may follow an assessment move. Check
        # the whole sequence rather than only the last component; otherwise a
        # visual/audio step can make the selector append a duplicate graded
        # practice + calibration pair.
        has_assessment = any(
            item[0] in _EVIDENCE_ASSESSMENT_COMPONENT_TYPES for item in sequence
        ) or any(item.component_type in _EVIDENCE_ASSESSMENT_COMPONENT_TYPES for item in preserved)
        if affordances.practice.suitable and not has_assessment:
            if not preserved_assessment_without_calibration:
                assessment_with_calibration(
                    "guided_practice",
                    ["monitoring_regulation"],
                    True,
                    "Practice produces evidence that can safely adjust the next step.",
                )
        sequence.append(
            (
                "retrieval_card",
                ["monitoring_regulation"],
                True,
                "Active recall keeps focal concepts available for later review.",
            )
        )
        checkpoint_dimensions: list[str] = [
            dimension
            for dimension in ("goal_planning", "monitoring_regulation", "motivation_emotion")
            if dimension in strong
        ]
        if checkpoint_dimensions:
            sequence.append(
                (
                    "progress_checkpoint",
                    checkpoint_dimensions,
                    False,
                    "Current support evidence adds a low-pressure checkpoint, visible progress, and a recovery option.",
                )
            )
        if "reflection_transfer" in strong:
            sequence.append(
                (
                    "reflection_prompt",
                    ["reflection_transfer"],
                    False,
                    "Reflection support asks the learner to restate and connect the idea.",
                )
            )
        if stage != "unobserved" and concept_signals:
            sequence.append(
                (
                    "review_queue",
                    ["motivation_emotion"],
                    False,
                    "Observed concepts remain available in the subject-scoped review queue.",
                )
            )

        components: list[LearningComponent] = []
        pending_assessment_id: str | None = None
        catalog = dict(self.catalog.get("components") or {})
        if goal_map_missing and preserved:
            # ``goal_map`` must be the first component of every plan (the
            # arrangement validator and the learner-facing path both assume
            # it). A legacy preserved prefix without one — e.g. an old
            # single-component reattempt plan — would otherwise push the goal
            # map to second place and break the invariant, so prepend it
            # before the preserved prefix instead of appending after it.
            definition = dict(catalog["goal_map"])
            components.append(
                LearningComponent(
                    component_id=f"cmp_{len(components) + 1:02d}_{uuid4().hex[:10]}",
                    component_type="goal_map",
                    executor=definition["executor"],
                    label_zh=definition["label_zh"],
                    label_en=definition["label_en"],
                    concept_refs=concept_refs,
                    support_dimensions=["goal_planning"],
                    bkt_stage=stage,
                    modality=_component_modality("goal_map", str(definition["executor"])),
                    required=True,
                    reason="Generate a source-grounded goal map with milestones and completion evidence.",
                    evidence_refs=evidence_refs,
                    completion_event=definition["completion_event"],
                )
            )
        components.extend(preserved)
        for index, (component_type, dimensions, required, reason) in enumerate(sequence, start=1):
            if goal_map_missing and preserved and index == 1:
                # Already emitted as the prepended lead component above.
                continue
            definition = dict(catalog[component_type])
            component_id = f"cmp_{len(components) + 1:02d}_{uuid4().hex[:10]}"
            modality: Literal["text", "interactive", "visual", "video", "audio"] = (
                "visual"
                if component_type == "visual_map"
                else "video"
                if component_type == "video_explanation"
                else "audio"
                if component_type == "audio_explanation"
                else "interactive"
                if definition["executor"] in {"assessment", "retrieval"}
                else "text"
            )
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
                # Components are independently accessible. Calibration is the
                # sole local dependency because it interprets the immediately
                # preceding server-graded assessment; it is not a global path
                # gate for lessons, media, flashcards, or other tools.
                dependencies=(
                    [pending_assessment_id]
                    if component_type == "calibration_checkpoint" and pending_assessment_id
                    else []
                ),
                required=required,
                reason=reason,
                evidence_refs=evidence_refs,
                completion_event=definition["completion_event"],
            )
            components.append(component)
            if component_type in _ASSESSMENT_COMPONENT_TYPES:
                pending_assessment_id = component_id
            elif component_type == "calibration_checkpoint":
                pending_assessment_id = None

        created = _now()
        return LearningComponentPlan(
            plan_id=f"plan_{uuid4().hex}",
            pack_id=pack_id,
            # A fresh plan is always version 1; ``build_learning_component_plan``
            # rewrites the version from the superseded plan's chain.
            version=1,
            goal=goal.strip()[:240] or "Build understanding from the selected learning source",
            subject_ref=dict(subject_ref) if subject_ref else None,
            analysis_id=analysis_id,
            support_state_snapshot=support_state,
            bkt_snapshot_ref=(
                f"subject:{subject_ref.get('subject_id')}"
                if subject_ref and subject_ref.get("subject_id")
                else None
            ),
            components=components,
            supersedes_plan_id=supersedes_plan_id,
            created_at=created,
            updated_at=created,
        )


__all__ = [
    "LearningComponent",
    "LearningComponentPlan",
    "LearningComponentSelector",
    "MaterialComponentAffordances",
    "SubjectSupportState",
    "build_subject_support_state",
    "infer_material_affordances",
    "build_learning_component_plan",
    "build_calibrated_followup_plan",
    "load_learning_component_catalog",
]
