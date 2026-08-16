"""Shared, versioned canonical BKT parameter source.

Production parameters are loaded from one deployment-owned calibration
artifact.  The historical constants remain only as an explicitly
uncalibrated cold-start fallback, so a missing or invalid calibration can
never turn four convenient defaults into a learner-facing probability.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from traittutor.runtime.home import get_runtime_home

BKT_PARAMETERS_PATH_ENV = "TRAITTUTOR_BKT_PARAMETERS_PATH"
BKT_REQUIRE_CALIBRATED_ENV = "TRAITTUTOR_REQUIRE_CALIBRATED_BKT"
BKT_ARTIFACT_SCHEMA_VERSION: Literal[2] = 2
BKT_MIN_PRODUCTION_EVENTS = 500
BKT_MIN_PRODUCTION_SEQUENCES = 50
BKT_MIN_PRODUCTION_USERS = 20


class BKTParameterConfigurationError(RuntimeError):
    """Raised when an explicitly deployed calibration artifact is unusable."""


class BKTParamSet(BaseModel):
    """A versioned, calibration-flagged BKT parameter set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=32)
    transition: float = Field(ge=0, le=1)
    guess: float = Field(ge=0, le=1)
    slip: float = Field(ge=0, le=1)
    prior: float = Field(ge=0, le=1)
    calibrated: bool = False
    notes: str = ""


class BKTCalibrationProvenance(BaseModel):
    """Auditable evidence attached to a calibrated parameter version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["traittutor-canonical-ledger", "public-dataset"]
    dataset_id: str = Field(min_length=1, max_length=160)
    method: str = Field(min_length=1, max_length=160)
    calibrated_at: datetime
    training_event_count: int = Field(gt=0)
    validation_event_count: int = Field(gt=0)
    sequence_count: int = Field(gt=0)
    user_count: int = Field(gt=0)
    subject_count: int = Field(gt=0)
    kc_count: int = Field(gt=0)
    validation_log_loss: float = Field(ge=0)
    baseline_log_loss: float = Field(ge=0)
    fold_strategy: str = Field(default="legacy-holdout", min_length=1, max_length=160)
    code_version: str = Field(default="unknown", min_length=1, max_length=96)
    calibration_policy_version: str = Field(
        default="legacy-calibration-policy", min_length=1, max_length=96
    )


class BKTFoldQuality(BaseModel):
    """One owner-disjoint validation fold without owner identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fold: int = Field(ge=0, le=20)
    training_owner_count: int = Field(gt=0)
    validation_owner_count: int = Field(gt=0)
    validation_event_count: int = Field(gt=0)
    log_loss: float = Field(ge=0)
    baseline_log_loss: float = Field(ge=0)
    brier_score: float = Field(ge=0, le=1)
    baseline_brier_score: float = Field(ge=0, le=1)
    ece: float = Field(ge=0, le=1)
    prior: float = Field(gt=0, lt=1)
    transition: float = Field(gt=0, le=0.5)
    guess: float = Field(ge=0, lt=0.5)
    slip: float = Field(ge=0, lt=0.5)


class BKTSubjectSliceQuality(BaseModel):
    """Anonymous subject slice used by the calibration degradation gate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # A short natural subject label is a valid slice key; the calibration
    # collector pseudonymizes production keys ("subject-"+64 hex), but the
    # gate API must not crash on plain short keys. Length is not a security
    # boundary here — the slice payload carries no owner identifiers.
    subject_key: str = Field(min_length=1, max_length=80)
    event_count: int = Field(ge=100)
    log_loss: float = Field(ge=0)
    baseline_log_loss: float = Field(ge=0)


class BKTCalibrationQuality(BaseModel):
    """Hard quality gates and non-blocking diagnostics for artifact v2."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    gates_passed: bool
    fold_count: int = Field(default=5, ge=2, le=20)
    log_loss: float = Field(ge=0)
    baseline_log_loss: float = Field(ge=0)
    brier_score: float = Field(ge=0, le=1)
    baseline_brier_score: float = Field(ge=0, le=1)
    ece: float = Field(ge=0, le=1)
    log_loss_delta_ci95: tuple[float, float]
    bootstrap_unit: Literal["owner"] = "owner"
    bootstrap_samples: int = Field(ge=200)
    folds: tuple[BKTFoldQuality, ...]
    subject_slices: tuple[BKTSubjectSliceQuality, ...] = ()


class BKTParameterArtifact(BaseModel):
    """Deployment contract for one calibrated, immutable BKT version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1, 2] = 1
    parameters: BKTParamSet
    provenance: BKTCalibrationProvenance
    quality: BKTCalibrationQuality | None = None

    @model_validator(mode="after")
    def require_real_calibration_marker(self) -> BKTParameterArtifact:
        if not self.parameters.calibrated:
            raise ValueError("deployed BKT parameters must be marked calibrated")
        if "uncalibrated" in self.parameters.version.lower():
            raise ValueError("a calibrated BKT version cannot be named uncalibrated")
        if not 0 < self.parameters.prior < 1:
            raise ValueError("calibrated BKT prior must be strictly between zero and one")
        if not 0 < self.parameters.transition <= 0.5:
            raise ValueError("calibrated BKT transition must be in (0, 0.5]")
        if not 0 <= self.parameters.guess < 0.5:
            raise ValueError("calibrated BKT guess must be below 0.5")
        if not 0 <= self.parameters.slip < 0.5:
            raise ValueError("calibrated BKT slip must be below 0.5")
        if self.parameters.guess + self.parameters.slip >= 1:
            raise ValueError("calibrated BKT guess and slip must sum to less than one")
        validation_loss = self.provenance.validation_log_loss
        baseline_loss = self.provenance.baseline_log_loss
        if not math.isfinite(validation_loss) or not math.isfinite(baseline_loss):
            raise ValueError("calibration losses must be finite")
        if validation_loss >= baseline_loss:
            raise ValueError("calibrated BKT must improve held-out log loss")
        if self.schema_version == BKT_ARTIFACT_SCHEMA_VERSION:
            quality = self.quality
            if quality is None or not quality.gates_passed:
                raise ValueError("BKT artifact v2 requires passed production quality gates")
            if len(quality.folds) != quality.fold_count:
                raise ValueError("BKT artifact fold report is incomplete")
            if quality.log_loss >= quality.baseline_log_loss:
                raise ValueError("cross-validated BKT log loss must improve the baseline")
            if quality.log_loss_delta_ci95[1] >= 0:
                raise ValueError("owner-block bootstrap interval must be entirely below zero")
            if quality.brier_score > quality.baseline_brier_score:
                raise ValueError("cross-validated BKT Brier score must not regress")
            if any(
                item.log_loss - item.baseline_log_loss > 0.02 for item in quality.subject_slices
            ):
                raise ValueError("a reported subject slice exceeds the degradation limit")
        return self


# These values match historical persisted ConceptSignal / KnowledgeStateUnit
# documents. They are migration defaults, not a production calibration.
UNCALIBRATED_FALLBACK_PARAMS = BKTParamSet(
    version="v1-uncalibrated",
    transition=0.12,
    guess=0.2,
    slip=0.1,
    prior=0.2,
    calibrated=False,
    notes="hardcoded defaults from personalization/models.py; not yet calibrated",
)
DEFAULT_PARAMS = UNCALIBRATED_FALLBACK_PARAMS


def active_bkt_parameters_path() -> Path:
    """Return the global deployment artifact path, never an owner partition."""
    configured = os.getenv(BKT_PARAMETERS_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    versioned_current = get_runtime_home() / "config" / "bkt-parameters" / "current.json"
    legacy = get_runtime_home() / "config" / "bkt-parameters.json"
    return versioned_current if versioned_current.exists() or not legacy.exists() else legacy


def load_bkt_parameter_artifact(path: Path) -> BKTParameterArtifact:
    """Load and validate one calibration artifact without silent fallback."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return BKTParameterArtifact.model_validate(payload)
    except (OSError, ValueError) as exc:
        raise BKTParameterConfigurationError(f"invalid BKT calibration artifact at {path}") from exc


def require_production_bkt_artifact(
    artifact: BKTParameterArtifact,
) -> BKTParameterArtifact:
    """Reject artifacts that did not pass the current production policy.

    Legacy schema-v1 artifacts remain readable for offline migration and local
    replay, but they cannot become the production ``current.json`` target.
    Keeping the fixed data floors here prevents CLI flags or hand-authored JSON
    from weakening the release gate.
    """
    if artifact.schema_version != BKT_ARTIFACT_SCHEMA_VERSION:
        raise BKTParameterConfigurationError(
            f"production BKT requires artifact schema {BKT_ARTIFACT_SCHEMA_VERSION}"
        )
    quality = artifact.quality
    if quality is None or not quality.gates_passed:
        raise BKTParameterConfigurationError("production BKT quality gates did not pass")
    provenance = artifact.provenance
    if provenance.training_event_count < BKT_MIN_PRODUCTION_EVENTS:
        raise BKTParameterConfigurationError(
            f"production BKT requires at least {BKT_MIN_PRODUCTION_EVENTS} strong events"
        )
    if provenance.sequence_count < BKT_MIN_PRODUCTION_SEQUENCES:
        raise BKTParameterConfigurationError(
            f"production BKT requires at least {BKT_MIN_PRODUCTION_SEQUENCES} sequences"
        )
    if provenance.user_count < BKT_MIN_PRODUCTION_USERS:
        raise BKTParameterConfigurationError(
            f"production BKT requires at least {BKT_MIN_PRODUCTION_USERS} learners"
        )
    return artifact


def get_active_bkt_params() -> BKTParamSet:
    """Resolve the calibrated deployment version or the honest cold start.

    An absent default artifact means calibration has not happened yet and
    therefore returns the uncalibrated fallback. If an operator explicitly
    configures a path, absence is a deployment error rather than a silent
    downgrade to different model semantics.
    """
    path = active_bkt_parameters_path()
    explicitly_configured = bool(os.getenv(BKT_PARAMETERS_PATH_ENV, "").strip())
    calibrated_required = os.getenv(BKT_REQUIRE_CALIBRATED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not path.exists():
        if explicitly_configured or calibrated_required:
            raise BKTParameterConfigurationError(
                f"configured BKT calibration artifact does not exist: {path}"
            )
        return UNCALIBRATED_FALLBACK_PARAMS
    artifact = load_bkt_parameter_artifact(path)
    if calibrated_required:
        require_production_bkt_artifact(artifact)
    return artifact.parameters


__all__ = [
    "BKT_PARAMETERS_PATH_ENV",
    "BKT_REQUIRE_CALIBRATED_ENV",
    "BKT_ARTIFACT_SCHEMA_VERSION",
    "BKT_MIN_PRODUCTION_EVENTS",
    "BKT_MIN_PRODUCTION_SEQUENCES",
    "BKT_MIN_PRODUCTION_USERS",
    "BKTCalibrationProvenance",
    "BKTCalibrationQuality",
    "BKTFoldQuality",
    "BKTParameterArtifact",
    "BKTParameterConfigurationError",
    "BKTParamSet",
    "BKTSubjectSliceQuality",
    "DEFAULT_PARAMS",
    "UNCALIBRATED_FALLBACK_PARAMS",
    "active_bkt_parameters_path",
    "get_active_bkt_params",
    "load_bkt_parameter_artifact",
    "require_production_bkt_artifact",
]
