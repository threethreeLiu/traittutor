"""Read-time forgetting projection (decay-on-read) tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from traittutor.learning_model.decay import (
    DEFAULT_HALF_LIFE_DAYS,
    HALF_LIFE_DAYS,
    days_since,
    decay_factor,
    decayed_support_level,
    effective_mastery,
    half_life_days,
    project_concept_signal,
)
from traittutor.learning_model.knowledge_state import KnowledgeStateUnit
from traittutor.learning_model.stage_policy import qualitative_evidence_state
from traittutor.personalization.models import ConceptSignal


def _signal(
    *,
    mastery: float = 0.9,
    initial: float = 0.2,
    support: str = "supported",
    calibrated: bool = True,
    observations: int = 5,
    practised_days_ago: float | None = 0,
) -> ConceptSignal:
    practised = (
        (datetime.now(UTC) - timedelta(days=practised_days_ago)).isoformat()
        if practised_days_ago is not None
        else None
    )
    return ConceptSignal(
        concept_id="kc-1",
        label="KC 1",
        support_level=support,  # type: ignore[arg-type]
        confidence=0.9,
        attempt_count=observations,
        verified_observation_count=observations,
        mastery_probability=mastery,
        initial_mastery_probability=initial,
        bkt_calibrated=calibrated,
        last_practised_at=practised,
    )


def test_half_life_table_and_default() -> None:
    assert HALF_LIFE_DAYS == {"MEMORY": 30, "PROCEDURE": 60, "CONCEPT": 90, "DESIGN": 120}
    assert half_life_days("memory") == 30
    assert half_life_days("PROCEDURE") == 60
    assert half_life_days(None) == DEFAULT_HALF_LIFE_DAYS == 90
    assert half_life_days("unknown") == DEFAULT_HALF_LIFE_DAYS


def test_decay_factor_at_zero_and_half_life() -> None:
    assert decay_factor(0, 90) == 1.0
    assert decay_factor(-5, 90) == 1.0
    assert abs(decay_factor(90, 90) - 0.5) < 1e-9
    assert abs(decay_factor(180, 90) - 0.25) < 1e-9


def test_effective_mastery_converges_toward_prior() -> None:
    # posterior 0.9, prior 0.2, half-life 90 days
    assert effective_mastery(prior=0.2, posterior=0.9, days_since_last_practice=0) == pytest.approx(
        0.9
    )
    half = effective_mastery(prior=0.2, posterior=0.9, days_since_last_practice=90)
    assert half == pytest.approx(0.2 + (0.9 - 0.2) / 2)  # 0.55
    far = effective_mastery(prior=0.2, posterior=0.9, days_since_last_practice=720)
    assert far < 0.3  # two years of no practice -> nearly back to prior


def test_days_since_parsing() -> None:
    now = datetime.now(UTC)
    assert days_since(now.isoformat(), now=now) == 0.0
    assert days_since((now - timedelta(days=30)).isoformat(), now=now) >= 29.9
    assert days_since(None) == 0.0
    assert days_since("not-a-date") == 0.0


def test_projection_only_applies_to_calibrated_observed_successes() -> None:
    now = datetime.now(UTC)
    # calibrated + 5 observations + 180 days ago -> decays
    decayed = project_concept_signal(_signal(mastery=0.9, practised_days_ago=180), now=now)
    assert decayed.mastery_probability < 0.7
    # uncalibrated -> unchanged
    raw = _signal(calibrated=False, practised_days_ago=180)
    assert project_concept_signal(raw, now=now) is raw
    # too few observations -> unchanged
    raw = _signal(observations=2, practised_days_ago=180)
    assert project_concept_signal(raw, now=now) is raw
    # recent failure (needs_support) -> unchanged, never decayed upward
    raw = _signal(support="needs_support", mastery=0.3, practised_days_ago=180)
    assert project_concept_signal(raw, now=now) is raw
    # no practice time -> unchanged
    raw = _signal(practised_days_ago=None)
    assert project_concept_signal(raw, now=now) is raw


def test_projection_downgrades_supported_to_developing() -> None:
    now = datetime.now(UTC)
    # 0.9 supported; after ~2 half-lives (CONCEPT 90d) -> ~0.375 -> needs_support
    decayed = project_concept_signal(
        _signal(mastery=0.9, practised_days_ago=200, support="supported"), now=now
    )
    assert decayed.support_level == "needs_support"
    assert decayed.mastery_probability < 0.4
    # after one half-life -> ~0.55 -> developing
    mid = project_concept_signal(
        _signal(mastery=0.9, practised_days_ago=90, support="supported"), now=now
    )
    assert mid.support_level == "developing"


def test_projection_uses_knowledge_type_half_life() -> None:
    now = datetime.now(UTC)
    # MEMORY half-life 30d: 60 days -> factor 0.25 -> 0.2 + 0.7*0.25 = 0.375
    decayed = project_concept_signal(
        _signal(mastery=0.9, practised_days_ago=60), now=now, knowledge_type="MEMORY"
    )
    assert abs(decayed.mastery_probability - 0.375) < 1e-9
    # Explicit argument wins over the signal-carried type.
    carried = _signal(mastery=0.9, practised_days_ago=60)
    carried = carried.model_copy(update={"knowledge_type": "MEMORY"})
    from_arg = project_concept_signal(carried, now=now, knowledge_type="CONCEPT")
    # CONCEPT half-life 90d at 60 days -> factor 2**(-60/90) ~ 0.630 -> 0.641
    assert from_arg.mastery_probability == pytest.approx(0.2 + 0.7 * 2 ** (-60 / 90), abs=1e-6)


def test_projection_falls_back_to_signal_carried_knowledge_type() -> None:
    now = datetime.now(UTC)
    carried = _signal(mastery=0.9, practised_days_ago=60).model_copy(
        update={"knowledge_type": "MEMORY"}
    )
    decayed = project_concept_signal(carried, now=now)
    assert decayed.mastery_probability == pytest.approx(0.375, abs=1e-9)
    # Untyped signals still decay with the default half-life.
    untyped = project_concept_signal(_signal(mastery=0.9, practised_days_ago=60), now=now)
    assert untyped.mastery_probability == pytest.approx(
        0.2 + 0.7 * 2 ** (-60 / DEFAULT_HALF_LIFE_DAYS), abs=1e-9
    )


def test_decay_factor_rejects_nonpositive_half_life() -> None:
    with pytest.raises(ValueError, match="half_life"):
        decay_factor(30, 0)
    with pytest.raises(ValueError, match="half_life"):
        decay_factor(30, -1)
    assert decay_factor(0, 90) == 1.0  # no elapsed time is not an error


def test_decayed_support_level_thresholds() -> None:
    assert decayed_support_level(posterior=0.9, days_since_last_practice=0) == "supported"
    assert decayed_support_level(posterior=0.5, days_since_last_practice=0) == "developing"
    assert decayed_support_level(posterior=0.3, days_since_last_practice=0) == "needs_support"


def test_projection_uses_the_signal_prior_for_state_thresholds() -> None:
    now = datetime.now(UTC)
    projected = project_concept_signal(
        _signal(mastery=0.9, initial=0.6, practised_days_ago=180),
        now=now,
    )
    assert projected.mastery_probability == pytest.approx(0.675, abs=1e-6)
    assert projected.support_level == "developing"


def test_canonical_public_state_uses_the_same_read_time_decay() -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    unit = KnowledgeStateUnit(
        user_id="u1",
        subject_id="math",
        kc_id="limits",
        mastery_probability=0.9,
        initial_mastery_probability=0.2,
        verified_observation_count=8,
        param_version="cal-v1",
        calibrated=True,
        updated_at=(now - timedelta(days=200)).isoformat(),
    )

    assert qualitative_evidence_state(unit, now=now) == "needs_support"
