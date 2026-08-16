"""v2.7 learning-model canonical layer (F-09/F-10, WS-10 phase 1).

Additive objects: immutable event ledger, per-KC knowledge-state, versioned
BKT, and misconception hypotheses. These converge toward the canonical
single-path mastery model. WS-10B wires the live grading entry points through
``learning.event_chain`` behind a reversible rollout flag.
"""

from __future__ import annotations

from .bkt import rebuild_knowledge_states, update_with_evidence
from .events import (
    AmendmentAction,
    AmendmentReason,
    AppendOutcome,
    AttributionStatus,
    DerivedClaim,
    DerivedFailure,
    DerivedOutcome,
    EvidenceStrength,
    LearnerEvent,
    LearnerEventAmendment,
    LearnerEventLedger,
    LearnerEventLedgerError,
    SurfaceType,
    is_strong_evidence,
    stable_amendment_identity,
)
from .knowledge_state import (
    MIN_OBSERVATIONS_FOR_PROBABILITY,
    KnowledgeStateKey,
    KnowledgeStateStore,
    KnowledgeStateUnit,
    display_mastery,
)
from .misconception import (
    MISCONCEPTION_EVIDENCE_THRESHOLD,
    MisconceptionConfirmationError,
    MisconceptionHypothesis,
    MisconceptionStatus,
    MisconceptionStore,
)
from .parameters import (
    BKT_ARTIFACT_SCHEMA_VERSION,
    BKT_MIN_PRODUCTION_EVENTS,
    BKT_MIN_PRODUCTION_SEQUENCES,
    BKT_MIN_PRODUCTION_USERS,
    BKT_PARAMETERS_PATH_ENV,
    BKT_REQUIRE_CALIBRATED_ENV,
    DEFAULT_PARAMS,
    UNCALIBRATED_FALLBACK_PARAMS,
    BKTCalibrationProvenance,
    BKTCalibrationQuality,
    BKTFoldQuality,
    BKTParameterArtifact,
    BKTParameterConfigurationError,
    BKTParamSet,
    BKTSubjectSliceQuality,
    active_bkt_parameters_path,
    get_active_bkt_params,
    load_bkt_parameter_artifact,
    require_production_bkt_artifact,
)

__all__ = [
    "AppendOutcome",
    "AmendmentAction",
    "AmendmentReason",
    "AttributionStatus",
    "BKTParamSet",
    "BKT_ARTIFACT_SCHEMA_VERSION",
    "BKT_MIN_PRODUCTION_EVENTS",
    "BKT_MIN_PRODUCTION_SEQUENCES",
    "BKT_MIN_PRODUCTION_USERS",
    "BKT_PARAMETERS_PATH_ENV",
    "BKT_REQUIRE_CALIBRATED_ENV",
    "BKTCalibrationProvenance",
    "BKTCalibrationQuality",
    "BKTFoldQuality",
    "BKTParameterArtifact",
    "BKTParameterConfigurationError",
    "BKTSubjectSliceQuality",
    "DEFAULT_PARAMS",
    "DerivedClaim",
    "DerivedFailure",
    "DerivedOutcome",
    "EvidenceStrength",
    "KnowledgeStateKey",
    "KnowledgeStateStore",
    "KnowledgeStateUnit",
    "LearnerEvent",
    "LearnerEventAmendment",
    "LearnerEventLedger",
    "LearnerEventLedgerError",
    "MIN_OBSERVATIONS_FOR_PROBABILITY",
    "MISCONCEPTION_EVIDENCE_THRESHOLD",
    "MisconceptionConfirmationError",
    "MisconceptionHypothesis",
    "MisconceptionStatus",
    "MisconceptionStore",
    "SurfaceType",
    "UNCALIBRATED_FALLBACK_PARAMS",
    "active_bkt_parameters_path",
    "display_mastery",
    "is_strong_evidence",
    "get_active_bkt_params",
    "load_bkt_parameter_artifact",
    "require_production_bkt_artifact",
    "stable_amendment_identity",
    "rebuild_knowledge_states",
    "update_with_evidence",
]
