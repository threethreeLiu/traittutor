#!/usr/bin/env python3
"""Calibrate global BKT parameters from all canonical owner-bound ledgers."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import os
from pathlib import Path

from traittutor.learning_model import (
    BKT_ARTIFACT_SCHEMA_VERSION,
    BKT_MIN_PRODUCTION_EVENTS,
    BKT_MIN_PRODUCTION_SEQUENCES,
    BKT_MIN_PRODUCTION_USERS,
    DEFAULT_PARAMS,
    BKTCalibrationProvenance,
    BKTParameterArtifact,
)
from traittutor.learning_model.artifacts import activate_artifact, write_immutable_artifact
from traittutor.learning_model.calibration import (
    BKTCalibrationGateError,
    build_observation_sequences,
    calibrate_with_owner_folds,
)
from traittutor.learning_model.production_calibration import (
    collect_production_calibration_dataset,
)
from traittutor.runtime.home import get_runtime_home

CALIBRATION_POLICY_VERSION = "production-owner-5fold-v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate global BKT from effective strong evidence in every owner ledger."
    )
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--artifact-directory",
        type=Path,
        default=None,
        help="defaults to $TRAITTUTOR_HOME/config/bkt-parameters",
    )
    parser.add_argument("--code-version", required=True)
    parser.add_argument("--candidate-count", type=int, default=20_000)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument("--min-events", type=int, default=BKT_MIN_PRODUCTION_EVENTS)
    parser.add_argument("--min-sequences", type=int, default=BKT_MIN_PRODUCTION_SEQUENCES)
    parser.add_argument("--min-users", type=int, default=BKT_MIN_PRODUCTION_USERS)
    parser.add_argument("--activate", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    fixed_floors = (
        ("--min-events", args.min_events, BKT_MIN_PRODUCTION_EVENTS),
        ("--min-sequences", args.min_sequences, BKT_MIN_PRODUCTION_SEQUENCES),
        ("--min-users", args.min_users, BKT_MIN_PRODUCTION_USERS),
    )
    for name, configured, minimum in fixed_floors:
        if configured < minimum:
            raise SystemExit(f"{name} cannot be lower than the production floor {minimum}")
    secret = os.getenv("TRAITTUTOR_CALIBRATION_HASH_KEY", "").encode()
    if len(secret) < 32:
        raise SystemExit("TRAITTUTOR_CALIBRATION_HASH_KEY must contain at least 32 bytes")
    dataset = collect_production_calibration_dataset(pseudonym_key=secret)
    if dataset.source_event_count < args.min_events:
        raise SystemExit(
            f"insufficient strong events: {dataset.source_event_count} < {args.min_events}"
        )
    if dataset.sequence_count < args.min_sequences:
        raise SystemExit(
            "insufficient user-subject-KC sequences: "
            f"{dataset.sequence_count} < {args.min_sequences}"
        )
    if dataset.owner_count < args.min_users:
        raise SystemExit(
            f"insufficient distinct learners: {dataset.owner_count} < {args.min_users}"
        )
    sequences = build_observation_sequences(list(dataset.events))
    try:
        result = calibrate_with_owner_folds(
            sequences,
            baseline=DEFAULT_PARAMS,
            version=args.version,
            candidate_count=args.candidate_count,
            bootstrap_samples=args.bootstrap_samples,
        )
    except BKTCalibrationGateError as exc:
        report = exc.quality
        raise SystemExit(
            "BKT quality gates failed; no artifact written: "
            f"log_loss={report.log_loss:.6f} baseline={report.baseline_log_loss:.6f} "
            f"delta_ci95={report.log_loss_delta_ci95} "
            f"brier={report.brier_score:.6f} baseline_brier={report.baseline_brier_score:.6f}"
        ) from exc

    artifact = BKTParameterArtifact(
        schema_version=BKT_ARTIFACT_SCHEMA_VERSION,
        parameters=result.parameters,
        provenance=BKTCalibrationProvenance(
            source="traittutor-canonical-ledger",
            dataset_id=dataset.dataset_id,
            method="global four-parameter BKT with deterministic owner-disjoint five-fold CV",
            calibrated_at=datetime.now(UTC),
            # Distinct strong events (not per-KC observations: one event may
            # carry up to 24 kc_ids and must not be counted 24 times).
            training_event_count=dataset.source_event_count,
            validation_event_count=dataset.source_event_count,
            sequence_count=dataset.sequence_count,
            user_count=dataset.owner_count,
            subject_count=dataset.subject_count,
            kc_count=dataset.kc_count,
            validation_log_loss=result.quality.log_loss,
            baseline_log_loss=result.quality.baseline_log_loss,
            fold_strategy="deterministic owner-disjoint five-fold cross-validation",
            code_version=args.code_version,
            calibration_policy_version=CALIBRATION_POLICY_VERSION,
        ),
        quality=result.quality,
    )
    directory = args.artifact_directory or (get_runtime_home() / "config" / "bkt-parameters")
    output = write_immutable_artifact(directory, artifact)
    if args.activate:
        activate_artifact(directory, args.version)
    print(
        f"wrote {output}: version={result.parameters.version} "
        f"events={dataset.source_event_count} observations={dataset.observation_count} "
        f"owners={dataset.owner_count} "
        f"cv_log_loss={result.quality.log_loss:.6f} activated={args.activate}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
