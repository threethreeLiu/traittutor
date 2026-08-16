from __future__ import annotations

from traittutor.learning_components import _stage
from traittutor.personalization.models import ConceptSignal


def _high_mastery_uncalibrated_signal() -> ConceptSignal:
    """A learner with a strong (but not yet calibrated) posterior on a KC."""
    return ConceptSignal(
        concept_id="kc-addition",
        label="Addition",
        support_level="supported",
        confidence=1.0,
        attempt_count=5,
        mastery_probability=0.9,
        bkt_calibrated=False,
    )


def test_public_dump_hides_uncalibrated_posterior() -> None:
    # Invariant #3: public/display dumps hide the pseudo-precise uncalibrated
    # posterior. A plain model_dump() must therefore null mastery_probability.
    assert _high_mastery_uncalibrated_signal().model_dump()["mastery_probability"] is None


def test_internal_dump_preserves_posterior_but_stays_unobserved_until_calibrated() -> None:
    # The deterministic selector may read the raw posterior internally, but an
    # uncalibrated estimate is still insufficient evidence for a learning
    # stage, even when its numeric value is high.
    signal = _high_mastery_uncalibrated_signal()
    internal = [signal.model_dump(context={"include_uncalibrated_posterior": True})]
    assert internal[0]["mastery_probability"] == 0.9
    assert _stage(internal) == "unobserved"


def test_public_dump_degrades_to_unobserved() -> None:
    # A public dump nulls the posterior, so the stage gate classifies the
    # learner as unobserved (no pseudo-precise claims from hidden data).
    signal = _high_mastery_uncalibrated_signal()
    assert _stage([signal.model_dump()]) == "unobserved"


def test_zero_posterior_is_not_treated_as_fallback_prior() -> None:
    # A calibrated, observed 0.0 posterior must not be swallowed by a fallback
    # prior: the exact value maps to "developing", never "unobserved".
    assert (
        _stage(
            [
                {
                    "support_level": "developing",
                    "mastery_probability": 0.0,
                    "bkt_calibrated": True,
                    "verified_observation_count": 3,
                }
            ]
        )
        == "developing"
    )


def test_empty_string_posterior_stays_unobserved_without_crashing() -> None:
    # A malformed empty posterior fails the stage gate (treated as missing),
    # never raising ValueError from float("").
    assert (
        _stage(
            [
                {
                    "support_level": "supported",
                    "mastery_probability": "",
                    "bkt_calibrated": True,
                    "verified_observation_count": 3,
                }
            ]
        )
        == "unobserved"
    )
