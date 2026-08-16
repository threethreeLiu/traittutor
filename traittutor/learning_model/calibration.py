"""Offline calibration helpers for the canonical four-parameter BKT model.

Only effective, strong, reliably attributed server-graded events are accepted.
The fitter mirrors ``personalization.bkt_math.bkt_update`` exactly: learning
transition is applied before the observation likelihood. It never mutates the
ledger or a learner profile.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import random

from .events import LearnerEvent, is_strong_evidence
from .parameters import (
    BKTCalibrationQuality,
    BKTFoldQuality,
    BKTParamSet,
    BKTSubjectSliceQuality,
)


@dataclass(frozen=True)
class BKTObservationSequence:
    """Chronological binary observations for one user + subject + KC."""

    key: tuple[str, str, str]
    outcomes: tuple[bool, ...]


@dataclass(frozen=True)
class _Prediction:
    owner_key: str
    subject_key: str
    correct: bool
    probability: float
    baseline_probability: float


@dataclass(frozen=True)
class BKTProductionCalibration:
    """A passed cross-validation report plus the all-data formal fit."""

    parameters: BKTParamSet
    quality: BKTCalibrationQuality


class BKTCalibrationGateError(ValueError):
    """Raised before a calibrated artifact can be constructed or written."""

    def __init__(self, message: str, *, quality: BKTCalibrationQuality) -> None:
        super().__init__(message)
        self.quality = quality


def build_observation_sequences(events: list[LearnerEvent]) -> tuple[BKTObservationSequence, ...]:
    """Build isolated sequences from canonical strong evidence only."""
    grouped: dict[tuple[str, str, str], list[bool]] = {}
    for event in sorted(events, key=lambda item: (item.created_at, item.event_id)):
        if not is_strong_evidence(event) or event.subject_id is None:
            continue
        for kc_id in event.kc_ids:
            key = (event.user_id, event.subject_id, kc_id)
            grouped.setdefault(key, []).append(bool(event.answer_correct))
    return tuple(
        BKTObservationSequence(key=key, outcomes=tuple(outcomes))
        for key, outcomes in sorted(grouped.items())
        if outcomes
    )


def deterministic_train_validation_split(
    sequences: tuple[BKTObservationSequence, ...],
    *,
    validation_fraction: float = 0.2,
) -> tuple[tuple[BKTObservationSequence, ...], tuple[BKTObservationSequence, ...]]:
    """Split whole identity-bound sequences without leaking a learner-KC tail."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be between zero and one")
    if len(sequences) < 2:
        raise ValueError("at least two BKT sequences are required")
    ranked = sorted(
        sequences,
        key=lambda item: hashlib.sha256("\x1f".join(item.key).encode()).hexdigest(),
    )
    validation_count = max(1, min(len(ranked) - 1, round(len(ranked) * validation_fraction)))
    return tuple(ranked[validation_count:]), tuple(ranked[:validation_count])


def mean_log_loss(
    sequences: tuple[BKTObservationSequence, ...],
    params: BKTParamSet,
) -> float:
    """Return per-observation negative log likelihood for the live BKT math."""
    loss = 0.0
    count = 0
    epsilon = 1e-12
    for sequence in sequences:
        mastery = params.prior
        for correct in sequence.outcomes:
            predicted = mastery + (1.0 - mastery) * params.transition
            probability_correct = predicted * (1.0 - params.slip) + (1.0 - predicted) * params.guess
            observation_probability = probability_correct if correct else 1.0 - probability_correct
            loss -= math.log(max(epsilon, min(1.0 - epsilon, observation_probability)))
            likelihood = observation_probability
            numerator = predicted * (1.0 - params.slip if correct else params.slip)
            mastery = predicted if likelihood <= 0 else numerator / likelihood
            mastery = max(0.0, min(1.0, mastery))
            count += 1
    if count == 0:
        raise ValueError("at least one BKT observation is required")
    return loss / count


def _predict_sequence(sequence: BKTObservationSequence, params: BKTParamSet) -> tuple[float, ...]:
    mastery = params.prior
    predictions: list[float] = []
    for correct in sequence.outcomes:
        predicted = mastery + (1.0 - mastery) * params.transition
        probability_correct = predicted * (1.0 - params.slip) + (1.0 - predicted) * params.guess
        predictions.append(probability_correct)
        likelihood = probability_correct if correct else 1.0 - probability_correct
        numerator = predicted * (1.0 - params.slip if correct else params.slip)
        mastery = predicted if likelihood <= 0 else numerator / likelihood
        mastery = max(0.0, min(1.0, mastery))
    return tuple(predictions)


def mean_brier_score(
    sequences: tuple[BKTObservationSequence, ...],
    params: BKTParamSet,
) -> float:
    """Return the mean squared probability error over sequential predictions."""
    values = [
        (probability - float(correct)) ** 2
        for sequence in sequences
        for correct, probability in zip(
            sequence.outcomes, _predict_sequence(sequence, params), strict=True
        )
    ]
    if not values:
        raise ValueError("at least one BKT observation is required")
    return sum(values) / len(values)


def expected_calibration_error(
    observations: tuple[tuple[bool, float], ...], *, bin_count: int = 10
) -> float:
    """Report ECE as a diagnostic; it is intentionally not a hard gate."""
    if not observations:
        raise ValueError("at least one probability observation is required")
    if bin_count < 2:
        raise ValueError("bin_count must be at least two")
    total = len(observations)
    error = 0.0
    for index in range(bin_count):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        bucket = [
            (correct, probability)
            for correct, probability in observations
            if lower <= probability < upper or (index == bin_count - 1 and probability == 1.0)
        ]
        if not bucket:
            continue
        accuracy = sum(float(correct) for correct, _ in bucket) / len(bucket)
        confidence = sum(probability for _, probability in bucket) / len(bucket)
        error += len(bucket) / total * abs(accuracy - confidence)
    return error


def deterministic_owner_folds(
    sequences: tuple[BKTObservationSequence, ...], *, fold_count: int = 5
) -> tuple[tuple[BKTObservationSequence, ...], ...]:
    """Assign every owner's complete history to exactly one deterministic fold."""
    if fold_count < 2:
        raise ValueError("fold_count must be at least two")
    owners = sorted(
        {sequence.key[0] for sequence in sequences},
        key=lambda owner: hashlib.sha256(owner.encode()).hexdigest(),
    )
    if len(owners) < fold_count:
        raise ValueError(f"at least {fold_count} owners are required for grouped folds")
    owner_fold = {owner: index % fold_count for index, owner in enumerate(owners)}
    folds = tuple(
        tuple(sequence for sequence in sequences if owner_fold[sequence.key[0]] == fold)
        for fold in range(fold_count)
    )
    if any(not fold for fold in folds):
        raise ValueError("every grouped validation fold must contain observations")
    return folds


def _loss(correct: bool, probability: float) -> float:
    epsilon = 1e-12
    observed = probability if correct else 1.0 - probability
    return -math.log(max(epsilon, min(1.0 - epsilon, observed)))


def _percentile(values: list[float], fraction: float) -> float:
    ranked = sorted(values)
    if not ranked:
        raise ValueError("percentile requires observations")
    position = (len(ranked) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ranked[lower]
    weight = position - lower
    return ranked[lower] * (1.0 - weight) + ranked[upper] * weight


def _owner_block_bootstrap_interval(
    predictions: tuple[_Prediction, ...], *, samples: int, seed: int
) -> tuple[float, float]:
    if samples < 200:
        raise ValueError("owner bootstrap requires at least 200 samples")
    blocks: dict[str, list[tuple[float, float]]] = {}
    for item in predictions:
        blocks.setdefault(item.owner_key, []).append(
            (
                _loss(item.correct, item.probability),
                _loss(item.correct, item.baseline_probability),
            )
        )
    owners = sorted(blocks)
    generator = random.Random(seed)
    deltas: list[float] = []
    for _ in range(samples):
        fitted_loss = 0.0
        baseline_loss = 0.0
        count = 0
        for _owner in owners:
            sampled = blocks[generator.choice(owners)]
            fitted_loss += sum(item[0] for item in sampled)
            baseline_loss += sum(item[1] for item in sampled)
            count += len(sampled)
        deltas.append((fitted_loss - baseline_loss) / count)
    return _percentile(deltas, 0.025), _percentile(deltas, 0.975)


def calibrate_with_owner_folds(
    sequences: tuple[BKTObservationSequence, ...],
    *,
    baseline: BKTParamSet,
    version: str,
    fold_count: int = 5,
    candidate_count: int = 20_000,
    seed: int = 27,
    bootstrap_samples: int = 2_000,
    subject_slice_min_events: int = 100,
    subject_log_loss_tolerance: float = 0.02,
) -> BKTProductionCalibration:
    """Run owner-disjoint CV, enforce quality gates, then refit all observations."""
    folds = deterministic_owner_folds(sequences, fold_count=fold_count)
    predictions: list[_Prediction] = []
    fold_quality: list[BKTFoldQuality] = []
    for fold_index, validation in enumerate(folds):
        train = tuple(
            sequence
            for other_index, other in enumerate(folds)
            if other_index != fold_index
            for sequence in other
        )
        params = fit_bkt_parameters(
            train,
            version=version,
            candidate_count=candidate_count,
            seed=seed + fold_index,
        )
        fitted_observations: list[tuple[bool, float]] = []
        for sequence in validation:
            fitted = _predict_sequence(sequence, params)
            baseline_predictions = _predict_sequence(sequence, baseline)
            for correct, probability, baseline_probability in zip(
                sequence.outcomes, fitted, baseline_predictions, strict=True
            ):
                fitted_observations.append((correct, probability))
                predictions.append(
                    _Prediction(
                        owner_key=sequence.key[0],
                        subject_key=sequence.key[1],
                        correct=correct,
                        probability=probability,
                        baseline_probability=baseline_probability,
                    )
                )
        fold_quality.append(
            BKTFoldQuality(
                fold=fold_index,
                training_owner_count=len({item.key[0] for item in train}),
                validation_owner_count=len({item.key[0] for item in validation}),
                validation_event_count=sum(len(item.outcomes) for item in validation),
                log_loss=mean_log_loss(validation, params),
                baseline_log_loss=mean_log_loss(validation, baseline),
                brier_score=mean_brier_score(validation, params),
                baseline_brier_score=mean_brier_score(validation, baseline),
                ece=expected_calibration_error(tuple(fitted_observations)),
                prior=params.prior,
                transition=params.transition,
                guess=params.guess,
                slip=params.slip,
            )
        )

    prediction_tuple = tuple(predictions)
    log_loss = sum(_loss(item.correct, item.probability) for item in prediction_tuple) / len(
        prediction_tuple
    )
    baseline_log_loss = sum(
        _loss(item.correct, item.baseline_probability) for item in prediction_tuple
    ) / len(prediction_tuple)
    brier = sum((item.probability - float(item.correct)) ** 2 for item in prediction_tuple) / len(
        prediction_tuple
    )
    baseline_brier = sum(
        (item.baseline_probability - float(item.correct)) ** 2 for item in prediction_tuple
    ) / len(prediction_tuple)
    interval = _owner_block_bootstrap_interval(
        prediction_tuple, samples=bootstrap_samples, seed=seed + 10_000
    )
    slices: list[BKTSubjectSliceQuality] = []
    for subject_key in sorted({item.subject_key for item in prediction_tuple}):
        subject = [item for item in prediction_tuple if item.subject_key == subject_key]
        if len(subject) < subject_slice_min_events:
            continue
        slices.append(
            BKTSubjectSliceQuality(
                subject_key=subject_key,
                event_count=len(subject),
                log_loss=sum(_loss(item.correct, item.probability) for item in subject)
                / len(subject),
                baseline_log_loss=sum(
                    _loss(item.correct, item.baseline_probability) for item in subject
                )
                / len(subject),
            )
        )
    gates_passed = (
        log_loss < baseline_log_loss
        and interval[1] < 0
        and brier <= baseline_brier
        and all(
            item.log_loss - item.baseline_log_loss <= subject_log_loss_tolerance for item in slices
        )
    )
    quality = BKTCalibrationQuality(
        gates_passed=gates_passed,
        fold_count=fold_count,
        log_loss=log_loss,
        baseline_log_loss=baseline_log_loss,
        brier_score=brier,
        baseline_brier_score=baseline_brier,
        ece=expected_calibration_error(
            tuple((item.correct, item.probability) for item in prediction_tuple)
        ),
        log_loss_delta_ci95=interval,
        bootstrap_samples=bootstrap_samples,
        folds=tuple(fold_quality),
        subject_slices=tuple(slices),
    )
    if not gates_passed:
        raise BKTCalibrationGateError(
            "production BKT quality gates failed; no calibrated artifact may be written",
            quality=quality,
        )
    parameters = fit_bkt_parameters(
        sequences,
        version=version,
        candidate_count=candidate_count,
        seed=seed,
    )
    return BKTProductionCalibration(parameters=parameters, quality=quality)


def fit_bkt_parameters(
    sequences: tuple[BKTObservationSequence, ...],
    *,
    version: str,
    candidate_count: int = 20_000,
    seed: int = 27,
) -> BKTParamSet:
    """Fit a constrained four-parameter model by deterministic random search.

    This bounded offline search keeps runtime dependencies small and avoids
    unconstrained degenerate fits. Guess/slip stay below 0.5, their sum stays
    below one, and the no-forgetting learning transition stays non-negative.
    Held-out acceptance belongs to the caller, never to the training search.
    """
    if candidate_count < 100:
        raise ValueError("candidate_count must be at least 100")
    if not version.strip() or "uncalibrated" in version.lower():
        raise ValueError("calibrated version must be nonblank and not named uncalibrated")
    generator = random.Random(seed)
    best: BKTParamSet | None = None
    best_loss = math.inf
    for _ in range(candidate_count):
        candidate = BKTParamSet(
            version=version,
            prior=generator.uniform(0.01, 0.95),
            transition=generator.uniform(0.001, 0.5),
            guess=generator.uniform(0.001, 0.49),
            slip=generator.uniform(0.001, 0.49),
            calibrated=True,
            notes="constrained offline maximum-likelihood fit",
        )
        if candidate.guess + candidate.slip >= 1.0:
            continue
        loss = mean_log_loss(sequences, candidate)
        if loss < best_loss:
            best = candidate
            best_loss = loss
    if best is None:
        raise RuntimeError("BKT calibration search produced no valid candidate")
    return best


def calibration_dataset_id(events: list[LearnerEvent]) -> str:
    """Hash bounded event facts so a calibration can be reproduced/audited."""
    payload = [
        {
            "event_id": event.event_id,
            "user_id": event.user_id,
            "subject_id": event.subject_id,
            "kc_ids": event.kc_ids,
            "answer_correct": event.answer_correct,
            "created_at": event.created_at,
        }
        for event in sorted(events, key=lambda item: (item.created_at, item.event_id))
        if is_strong_evidence(event)
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"canonical-ledger-sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "BKTObservationSequence",
    "BKTCalibrationGateError",
    "BKTProductionCalibration",
    "build_observation_sequences",
    "calibration_dataset_id",
    "deterministic_train_validation_split",
    "deterministic_owner_folds",
    "calibrate_with_owner_folds",
    "expected_calibration_error",
    "fit_bkt_parameters",
    "mean_log_loss",
    "mean_brier_score",
]
