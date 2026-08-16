from __future__ import annotations

from datetime import UTC, datetime, timedelta

from traittutor.api.routers.personalization import _public_profile
from traittutor.personalization.models import ConceptSignal, LearnerProfile, SubjectUnderstanding


def _private_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        found = {
            key
            for key in value
            if key
            in {
                "owner_id",
                "mastery_probability",
                "initial_mastery_probability",
                "transition_probability",
                "guess_probability",
                "slip_probability",
                "mastery_interval",
                "verified_mastery",
                "bkt_calibrated",
                "bkt_param_version",
            }
        }
        for child in value.values():
            found.update(_private_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_private_keys(child))
        return found
    return set()


def test_public_profile_applies_decay_before_allowlisted_qualitative_projection() -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    signal = ConceptSignal(
        concept_id="limits",
        label="Limits",
        support_level="supported",
        confidence=0.9,
        attempt_count=8,
        mastery_probability=0.9,
        initial_mastery_probability=0.2,
        observation_count=8,
        verified_observation_count=8,
        bkt_param_version="cal-v1",
        bkt_calibrated=True,
        last_practised_at=(now - timedelta(days=200)).isoformat(),
    )
    profile = LearnerProfile(
        owner_id="private-owner",
        scope="subject",
        concept_signals=[signal],
        understanding=SubjectUnderstanding(
            status="verified",
            concept_count=1,
            observed_concept_count=1,
            coverage=1,
            verified_mastery=0.9,
            verified_observation_count=8,
            confidence=0.9,
            review_load=0,
        ),
        updated_at=now.isoformat(),
    )

    public = _public_profile(profile, now=now)
    concept = public["concept_signals"][0]
    assert concept["evidence_state"] == "needs_support"
    assert concept["change_signal"] == "none"
    assert public["understanding"]["status"] == "learning"
    assert public["understanding"]["review_load"] == 1
    assert _private_keys(public) == set()


def test_public_projection_exact_allowlisted_key_sets() -> None:
    """Positive contract: the browser DTO carries exactly the qualitative
    allowlist — a renamed or newly added numeric field is caught even when it
    is not in the private-key blacklist."""
    now = datetime(2026, 8, 14, tzinfo=UTC)
    signal = ConceptSignal(
        concept_id="limits",
        label="Limits",
        support_level="supported",
        confidence=0.9,
        attempt_count=8,
        mastery_probability=0.9,
        initial_mastery_probability=0.2,
        observation_count=8,
        verified_observation_count=8,
        bkt_param_version="cal-v1",
        bkt_calibrated=True,
        last_practised_at=(now - timedelta(days=1)).isoformat(),
    )
    profile = LearnerProfile(
        owner_id="private-owner",
        scope="subject",
        concept_signals=[signal],
        understanding=SubjectUnderstanding(
            status="verified",
            concept_count=1,
            observed_concept_count=1,
            coverage=1,
            verified_mastery=0.9,
            verified_observation_count=8,
            confidence=0.9,
            review_load=0,
        ),
        updated_at=now.isoformat(),
    )

    public = _public_profile(profile, now=now)
    assert set(public) == {
        "scope",
        "subject",
        "inference_enabled",
        "preferences",
        "strategy_evidence",
        "evidence_refs",
        "schema_version",
        "updated_at",
        "needs_rebuild",
        "concept_signals",
        "understanding",
    }
    assert set(public["concept_signals"][0]) == {
        "concept_id",
        "label",
        "evidence_state",
        "change_signal",
        "confidence",
        "attempt_count",
        "misconception_tags",
        "evidence_refs",
        "last_practised_at",
        "module_id",
        "observation_count",
        "verified_observation_count",
        "last_observation_source",
        "model_version",
        "stage_policy_version",
    }
    assert set(public["understanding"]) == {
        "status",
        "concept_count",
        "observed_concept_count",
        "coverage",
        "verified_observation_count",
        "confidence",
        "recent_activity_at",
        "review_load",
    }
