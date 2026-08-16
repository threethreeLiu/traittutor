"""Offline BKT calibration is strong-evidence-only and deterministic."""

from __future__ import annotations

from traittutor.learning_model import BKTParamSet, LearnerEvent
from traittutor.learning_model.calibration import (
    build_observation_sequences,
    deterministic_train_validation_split,
    fit_bkt_parameters,
    mean_log_loss,
)


def _event(
    event_id: str,
    *,
    user_id: str,
    correct: bool | None,
    strong: bool = True,
) -> LearnerEvent:
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"key:{event_id}",
        user_id=user_id,
        subject_id="math",
        kc_ids=("fractions",),
        surface_type="quiz" if strong else "reading",
        answer_correct=correct,
        evidence_strength="strong" if strong else "exposure",
        attribution_status="reliable",
        created_at=f"2026-08-10T00:00:{event_id[-2:]}+00:00",
    )


def test_sequences_exclude_non_strong_evidence_and_preserve_partitions() -> None:
    sequences = build_observation_sequences(
        [
            _event("event-01", user_id="u1", correct=True),
            _event("event-02", user_id="u1", correct=None, strong=False),
            _event("event-03", user_id="u2", correct=False),
        ]
    )

    assert [(item.key, item.outcomes) for item in sequences] == [
        (("u1", "math", "fractions"), (True,)),
        (("u2", "math", "fractions"), (False,)),
    ]


def test_fit_is_deterministic_and_improves_training_likelihood() -> None:
    events: list[LearnerEvent] = []
    for user_index in range(12):
        # A learnable sequence: early errors followed by stable correctness.
        outcomes = (False, False, True, True, True, True)
        for attempt_index, correct in enumerate(outcomes):
            events.append(
                _event(
                    f"event-{user_index:02d}-{attempt_index:02d}",
                    user_id=f"u{user_index:02d}",
                    correct=correct,
                )
            )
    sequences = build_observation_sequences(events)
    train, validation = deterministic_train_validation_split(sequences)
    baseline = BKTParamSet(
        version="baseline",
        transition=0.01,
        guess=0.45,
        slip=0.45,
        prior=0.01,
    )

    first = fit_bkt_parameters(train, version="cal-v1", candidate_count=500, seed=9)
    second = fit_bkt_parameters(train, version="cal-v1", candidate_count=500, seed=9)

    assert first == second
    assert first.calibrated is True
    assert first.guess + first.slip < 1
    assert mean_log_loss(train, first) < mean_log_loss(train, baseline)
    assert mean_log_loss(validation, first) < mean_log_loss(validation, baseline)
