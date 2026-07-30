"""TraitTutor learner-support profile ported from the flask research pipeline.

This is a bounded generation profile, not an SLR questionnaire or diagnosis.
Only core-zone trait extremes may add structural support; edge traits affect
wording lightly and neutral traits stay silent.
"""
from __future__ import annotations

from typing import Any, Mapping
import json
from pathlib import Path


def _config() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("generation_support.json").read_text(encoding="utf-8"))


def _action_catalog() -> dict[str, Any]:
    """Load visible learning actions separately from trait-to-support policy."""
    return json.loads(Path(__file__).with_name("slr_action_catalog.json").read_text(encoding="utf-8"))


def _bucket(score: Any) -> str:
    config = _config()["score_bands"]
    value = float(score or 0)
    return "low" if value <= config["low_max"] else "high" if value >= config["high_min"] else "mid"


def _zone(score: Any) -> str:
    config = _config()["score_bands"]
    value = float(score or 0)
    return "core" if value <= config["core_low_max"] or value >= config["core_high_min"] else "edge" if value in {config["edge_low"], config["edge_high"]} else "neutral"


def _level(score: Any) -> str:
    config = _config()["score_bands"]
    value = float(score or 0)
    return "high_core" if value >= config["core_high_min"] else "high_edge" if value == config["edge_high"] else "neutral" if value in {5, 6} else "low_edge" if value == config["edge_low"] else "low_core"


def _simple(bucket: str, zone: str, high: int, default: int, edge: int) -> int:
    return high if bucket == "high" and zone == "core" else edge if zone == "edge" else default


def _complex(first_bucket: str, first_zone: str, first_high: int, second_bucket: str, second_zone: str, second_high: int, default: int) -> int:
    if first_bucket == "high" and first_zone == "core":
        return first_high
    if second_bucket == "high" and second_zone == "core":
        return second_high
    return 3 if first_zone == "edge" or second_zone == "edge" else default


def build_generation_support_profile(scores: Mapping[str, Any], *, task_type: str = "concept_learning") -> dict[str, Any]:
    config = _config()
    copy = config["copy"]
    normalized = {key: float(scores.get(key, 6) or 6) for key in ("O", "C", "E", "A", "N")}
    buckets = {name: _bucket(normalized[key]) for key, name in (("O", "openness"), ("C", "conscientiousness"), ("E", "extraversion"), ("A", "agreeableness"), ("N", "neuroticism"))}
    zones = {name: _zone(normalized[key]) for key, name in (("O", "openness"), ("C", "conscientiousness"), ("E", "extraversion"), ("A", "agreeableness"), ("N", "neuroticism"))}
    levels = {f"{name}_level": _level(normalized[key]) for key, name in (("O", "openness"), ("C", "conscientiousness"), ("E", "extraversion"), ("A", "agreeableness"), ("N", "neuroticism"))}

    support = {
        "structure_need": 4 if buckets["conscientiousness"] == "high" and zones["conscientiousness"] == "core" else 5 if buckets["conscientiousness"] == "low" and zones["conscientiousness"] == "core" else 3 if zones["conscientiousness"] == "edge" else 3,
        "scaffolding_need": _complex(buckets["neuroticism"], zones["neuroticism"], 5, buckets["openness"], zones["openness"], 4, 2),
        "autonomy_support_need": _simple(buckets["openness"], zones["openness"], 5, 2, 3),
        "reassurance_need": _complex(buckets["neuroticism"], zones["neuroticism"], 5, buckets["agreeableness"], zones["agreeableness"], 4, 2),
        "activation_need": _complex(buckets["extraversion"], zones["extraversion"], 5, buckets["neuroticism"], zones["neuroticism"], 4, 2),
        "interaction_tolerance": _simple(buckets["extraversion"], zones["extraversion"], 5, 1, 3),
        "conceptual_depth_readiness": _simple(buckets["openness"], zones["openness"], 5, 2, 3),
        "novelty_tolerance": _simple(buckets["openness"], zones["openness"], 5, 1, 3),
    }
    risk_flags = [
        flag for flag, condition in (("ambiguity_stress", buckets["neuroticism"] == "high"), ("start_friction", buckets["conscientiousness"] == "low"), ("social_overload_risk", buckets["extraversion"] == "low"), ("novelty_resistance", buckets["openness"] == "low")) if condition
    ]
    if buckets["agreeableness"] == "high" and zones["agreeableness"] == "core":
        tone = copy["tone_high_agreeableness"]
    elif buckets["agreeableness"] == "low" and zones["agreeableness"] == "core":
        tone = copy["tone_low_agreeableness"]
    elif zones["agreeableness"] == "edge":
        tone = copy["tone_edge"]
    else:
        tone = copy["tone_default"]
    if buckets["conscientiousness"] == "high" and zones["conscientiousness"] == "core":
        structure = copy["structure_high"]
    elif buckets["conscientiousness"] == "low" and zones["conscientiousness"] == "core":
        structure = copy["structure_low"]
    else:
        structure = copy["structure_default"]
    if buckets["extraversion"] == "high" and zones["extraversion"] == "core":
        interaction = copy["interaction_high"]
    elif buckets["extraversion"] == "low" and zones["extraversion"] == "core":
        interaction = copy["interaction_low"]
    else:
        interaction = copy["interaction_default"]
    if buckets["neuroticism"] == "high" and zones["neuroticism"] == "core":
        scaffolding = copy["scaffolding_high"]
    elif buckets["neuroticism"] == "low" and zones["neuroticism"] == "core":
        scaffolding = copy["scaffolding_low"]
    else:
        scaffolding = copy["scaffolding_depth"] if support["conceptual_depth_readiness"] >= 4 else copy["scaffolding_default"]
    interpretive_notes = {
        "tone_preference": tone,
        "structure_preference": structure,
        "interaction_preference": interaction,
        "difficulty_handling_preference": scaffolding,
    }
    profile = {
        "trait_scores": normalized, "trait_buckets": {f"{key}_bucket": value for key, value in buckets.items()},
        "trait_intervention_zones": {f"{key}_zone": value for key, value in zones.items()},
        "trait_strategy_levels": levels, "learner_support_profile": support,
        "baseline_learning_constraints": {"prior_knowledge_level": "medium", "recommended_pacing": "moderate", "explanation_granularity": "medium"},
        "prior_knowledge_level": "medium",
        "risk_flags": risk_flags,
        "support_recommendations": {"tone": tone, "structure": structure, "scaffolding": scaffolding, "motivation_support": copy["motivation"], "interaction_mode": interaction},
        "interpretive_notes": interpretive_notes,
        "support_profile": dict(support),
        "do_not_assume": ["Do not assume a fixed learner type or immutable preference.", "Do not assume prior knowledge changes personality-driven support needs.", "Do not create standalone scaffold modules for edge-zone or neutral-zone traits."],
        "short_rationale": copy["rationale"],
        "context": {"task_type": task_type},
    }
    return {"learner_profile": profile, "source": "huangyuan_traittutor_flask_app", "config_version": config["version"], "status": "generation_support_only"}


def build_slr_action_support(scores: Mapping[str, Any], *, task_type: str = "concept_learning") -> dict[str, Any]:
    """Create the learner-visible SLR action plan from the JSON action catalog.

    The support-policy JSON decides strength; the action-catalog JSON owns all
    learner-facing wording.  Keeping them separate prevents a prompt-only
    change from silently changing personality-to-support rules.
    """
    generated = build_generation_support_profile(scores, task_type=task_type)
    profile = generated["learner_profile"]
    needs = profile["learner_support_profile"]
    catalog = _action_catalog()
    strengths = {
        "goal_planning": needs["structure_need"],
        "monitoring_regulation": max(needs["scaffolding_need"], needs["reassurance_need"]),
        "reflection_transfer": max(needs["autonomy_support_need"], needs["conceptual_depth_readiness"]),
        "motivation_emotion": max(needs["activation_need"], needs["reassurance_need"]),
    }
    dimensions = {}
    for key, definition in dict(catalog.get("dimensions") or {}).items():
        strength = int(strengths.get(key, 2))
        dimensions[key] = {
            "label": str(definition.get("label") or key),
            "detail": str(definition.get("detail") or ""),
            "actions": list(definition.get("actions") or []),
            "emphasis": "strong" if strength >= 4 else "standard" if strength >= 3 else "light",
            "evidence_count": 0,
        }
    return {
        "version": catalog.get("version", "traittutor-slr-actions-v1"),
        "source": "generation_support_action_catalog",
        "status": "initial",
        "dimensions": dimensions,
        "boundary": " ".join(str(item) for item in catalog.get("boundaries") or []),
        "generation_support_profile": profile,
    }
