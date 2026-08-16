from __future__ import annotations

from datetime import UTC, datetime
import importlib.util
from pathlib import Path
import sys

import pytest

from traittutor.learning_governance.models import SubjectKnowledgeEvidence
from traittutor.learning_model import LearnerEvent, LearnerEventLedger
from traittutor.learning_model.calibration import (
    BKTObservationSequence,
    build_observation_sequences,
    deterministic_owner_folds,
)
from traittutor.learning_model.events import LearnerEventAmendment
from traittutor.learning_model.mastery_read_view import MasteryReadResult
from traittutor.learning_model.production_calibration import (
    collect_production_calibration_dataset,
)
from traittutor.services.path_service import PathService

_CALIBRATION_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_bkt.py"
_CALIBRATION_SPEC = importlib.util.spec_from_file_location("calibrate_bkt", _CALIBRATION_SCRIPT)
assert _CALIBRATION_SPEC is not None and _CALIBRATION_SPEC.loader is not None
calibrate_bkt = importlib.util.module_from_spec(_CALIBRATION_SPEC)
_CALIBRATION_SPEC.loader.exec_module(calibrate_bkt)


def _event(event_id: str, *, correct: bool) -> LearnerEvent:
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"key:{event_id}",
        user_id="u1",
        subject_id="math",
        kc_ids=("fractions",),
        surface_type="quiz",
        answer_correct=correct,
        evidence_strength="strong",
        attribution_status="reliable",
        created_at="2026-08-14T00:00:00+00:00",
    )


def test_owner_folds_never_split_one_learner_across_train_and_validation() -> None:
    sequences = tuple(
        BKTObservationSequence(
            key=(f"owner-{owner}", "math", f"kc-{kc}"),
            outcomes=(False, True, True),
        )
        for owner in range(10)
        for kc in range(2)
    )
    folds = deterministic_owner_folds(sequences)

    owner_sets = [{item.key[0] for item in fold} for fold in folds]
    assert len(folds) == 5
    assert all(
        left.isdisjoint(right)
        for index, left in enumerate(owner_sets)
        for right in owner_sets[index + 1 :]
    )
    assert set().union(*owner_sets) == {f"owner-{owner}" for owner in range(10)}


def test_production_calibration_floors_cannot_be_lowered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "calibrate_bkt.py",
            "--version",
            "too-small",
            "--code-version",
            "0" * 40,
            "--min-events",
            "5",
        ],
    )

    with pytest.raises(SystemExit, match="cannot be lower than the production floor 500"):
        calibrate_bkt.main()


def test_production_collector_uses_effective_strong_events_and_anonymizes_ids(
    tmp_path: Path,
) -> None:
    path_service = PathService(workspace_root=tmp_path / "owner")
    ledger = LearnerEventLedger(
        path_service.get_workspace_dir() / "learning_model" / "learner_events.json",
        path_service=path_service,
    )
    retained = _event("event-retained", correct=True)
    voided = _event("event-voided", correct=False)
    ledger.append(retained)
    ledger.append(voided)
    ledger.append_amendment(
        LearnerEventAmendment(
            amendment_id="amend-1",
            idempotency_key="void:event-voided",
            target_event_id=voided.event_id,
            user_id="u1",
            subject_id="math",
            kc_ids=("fractions",),
            reason_code="item_invalid",
            created_at="2026-08-14T00:01:00+00:00",
        )
    )

    dataset = collect_production_calibration_dataset(
        pseudonym_key=b"x" * 32,
        accounts=[{"id": "u1", "role": "user", "disabled": False}],
        path_service_resolver=lambda scope: (
            path_service
            if scope.kind == "user"
            else PathService(workspace_root=tmp_path / "empty-admin")
        ),
    )

    assert dataset.source_event_count == 1
    assert dataset.observation_count == 1
    serialized = " ".join(item.model_dump_json() for item in dataset.events)
    assert "u1" not in serialized
    assert "math" not in serialized
    assert "fractions" not in serialized
    assert "event-retained" not in serialized


def test_public_mastery_models_never_serialize_probability_interval_or_percentage() -> None:
    read = MasteryReadResult(
        evidence_state="developing",
        verified_observation_count=4,
        model_version="cal-v1",
        stage_policy_version="bkt-stage-policy-v1",
    )
    governance = SubjectKnowledgeEvidence(
        kc_id="fractions",
        evidence_state="supported",
        verified_observation_count=8,
        model_version="cal-v1",
        stage_policy_version="bkt-stage-policy-v1",
    )

    payload = {**read.model_dump(mode="json"), **governance.model_dump(mode="json")}
    assert not {
        "mastery_probability",
        "mastery_interval",
        "percentage",
        "calibrated",
        "param_version",
    }.intersection(payload)
    assert not {
        "mastery_probability",
        "mastery_interval",
        "calibrated",
        "param_version",
        "state",
    }.intersection(MasteryReadResult.model_json_schema()["properties"])
    assert not {
        "mastery_probability",
        "mastery_interval",
        "calibrated",
        "param_version",
    }.intersection(SubjectKnowledgeEvidence.model_json_schema()["properties"])


def _owner_event(owner: str, kc: str, event_id: str, *, correct: bool) -> LearnerEvent:
    return LearnerEvent(
        event_id=event_id,
        idempotency_key=f"key:{event_id}",
        user_id=owner,
        subject_id="math",
        kc_ids=(kc,),
        surface_type="quiz",
        answer_correct=correct,
        evidence_strength="strong",
        attribution_status="reliable",
        created_at=f"2026-08-14T{int(event_id.split('-')[-1]) % 24:02d}:00:00+00:00",
    )


def test_calibration_gates_pass_on_learnable_synthetic_data() -> None:
    from traittutor.learning_model.calibration import (
        calibrate_with_owner_folds,
    )
    from traittutor.learning_model.parameters import DEFAULT_PARAMS

    events: list[LearnerEvent] = []
    index = 0
    for owner in range(40):
        for kc in range(4):
            for attempt in range(6):
                events.append(
                    _owner_event(
                        f"owner-{owner}",
                        f"kc-{kc}",
                        f"e-{index:04d}",
                        correct=attempt != 5,  # 5/6 correct, learnable
                    )
                )
                index += 1
    sequences = build_observation_sequences(events)
    result = calibrate_with_owner_folds(
        sequences,
        baseline=DEFAULT_PARAMS,
        version="cal-pass-test-v1",
        candidate_count=800,
        bootstrap_samples=300,
    )
    assert result.quality.gates_passed is True
    assert result.quality.fold_count == 5
    assert result.parameters.calibrated is True
    assert result.quality.log_loss < result.quality.baseline_log_loss
    assert result.quality.log_loss_delta_ci95[1] < 0


def test_calibration_gates_fail_when_baseline_cannot_be_beaten() -> None:
    from traittutor.learning_model.calibration import (
        BKTCalibrationGateError,
        calibrate_with_owner_folds,
    )
    from traittutor.learning_model.parameters import BKTParamSet

    # All-correct data: an oracle baseline with slip=0 predicts perfectly
    # (loss ~0), while the constrained fit cannot drop slip below 0.001, so
    # every gate fails deterministically.
    events: list[LearnerEvent] = []
    for owner in range(20):
        for kc in range(2):
            for attempt in range(5):
                events.append(
                    _owner_event(
                        f"owner-{owner}", f"kc-{kc}", f"f-{owner}-{kc}-{attempt}", correct=True
                    )
                )
    sequences = build_observation_sequences(events)
    oracle = BKTParamSet(
        version="oracle-all-correct",
        transition=0.001,
        guess=0.001,
        slip=0.0,
        prior=0.99,
        calibrated=True,
    )
    with pytest.raises(BKTCalibrationGateError) as excinfo:
        calibrate_with_owner_folds(
            sequences,
            baseline=oracle,
            version="cal-fail-test-v1",
            candidate_count=400,
            bootstrap_samples=200,
        )
    assert excinfo.value.quality.gates_passed is False


def test_bkt_artifact_v2_validator_rejects_each_failing_gate() -> None:

    from traittutor.learning_model.parameters import (
        BKTCalibrationProvenance,
        BKTCalibrationQuality,
        BKTFoldQuality,
        BKTParameterArtifact,
        BKTParamSet,
        BKTSubjectSliceQuality,
    )

    params = BKTParamSet(
        version="cal-v2-test",
        transition=0.12,
        guess=0.2,
        slip=0.1,
        prior=0.2,
        calibrated=True,
    )
    provenance = BKTCalibrationProvenance(
        source="traittutor-canonical-ledger",
        dataset_id="dataset-1",
        method="owner-disjoint five-fold CV",
        calibrated_at=datetime(2026, 8, 14, tzinfo=UTC),
        training_event_count=1000,
        validation_event_count=500,
        sequence_count=50,
        user_count=20,
        subject_count=1,
        kc_count=4,
        validation_log_loss=0.5,
        baseline_log_loss=0.7,
    )
    folds = tuple(
        BKTFoldQuality(
            fold=index,
            training_owner_count=16,
            validation_owner_count=4,
            validation_event_count=100,
            log_loss=0.5,
            baseline_log_loss=0.7,
            brier_score=0.2,
            baseline_brier_score=0.3,
            ece=0.05,
            prior=0.2,
            transition=0.12,
            guess=0.2,
            slip=0.1,
        )
        for index in range(5)
    )

    def artifact(
        *,
        gates_passed: bool = True,
        log_loss: float = 0.5,
        baseline_log_loss: float = 0.7,
        brier: float = 0.2,
        baseline_brier: float = 0.3,
        ci: tuple[float, float] = (-0.3, -0.1),
        slice_log_loss: float | None = None,
        fold_count: int = 5,
    ) -> BKTParameterArtifact:
        slices: tuple[BKTSubjectSliceQuality, ...] = ()
        if slice_log_loss is not None:
            slices = (
                BKTSubjectSliceQuality(
                    subject_key="math",
                    event_count=200,
                    log_loss=slice_log_loss,
                    baseline_log_loss=0.69,
                ),
            )
        quality = BKTCalibrationQuality(
            gates_passed=gates_passed,
            fold_count=fold_count,
            log_loss=log_loss,
            baseline_log_loss=baseline_log_loss,
            brier_score=brier,
            baseline_brier_score=baseline_brier,
            ece=0.05,
            log_loss_delta_ci95=ci,
            bootstrap_unit="owner",
            bootstrap_samples=300,
            folds=folds,
            subject_slices=slices,
        )
        return BKTParameterArtifact(
            schema_version=2,
            parameters=params,
            provenance=provenance,
            quality=quality,
        )

    # A fully passing artifact validates.
    artifact()
    with pytest.raises(ValueError, match="production quality gates"):
        artifact(gates_passed=False)
    with pytest.raises(ValueError, match="log loss"):
        artifact(log_loss=0.7, baseline_log_loss=0.7)
    with pytest.raises(ValueError, match="bootstrap interval"):
        artifact(ci=(0.05, 0.2))
    with pytest.raises(ValueError, match="Brier"):
        artifact(brier=0.35, baseline_brier=0.3)
    with pytest.raises(ValueError, match="subject slice"):
        artifact(slice_log_loss=0.72)
    with pytest.raises(ValueError, match="fold report"):
        artifact(fold_count=4)
