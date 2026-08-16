"""Deployment-owned canonical BKT parameter selection."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from traittutor.learning.event_chain import CanonicalAnswerEventChain
from traittutor.learning_model import (
    BKT_PARAMETERS_PATH_ENV,
    BKTParameterArtifact,
    BKTParameterConfigurationError,
    KnowledgeStateKey,
    LearnerEventLedger,
    get_active_bkt_params,
)
from traittutor.learning_model.artifacts import (
    activate_artifact,
    restore_activation,
    write_immutable_artifact,
)
from traittutor.personalization.service import PersonalizationService


def _artifact(*, calibrated: bool = True, schema_version: int = 1) -> dict[str, object]:
    artifact: dict[str, object] = {
        "schema_version": schema_version,
        "parameters": {
            "version": "traittutor-ledger-2026-08-v1",
            "transition": 0.18,
            "guess": 0.16,
            "slip": 0.08,
            "prior": 0.24,
            "calibrated": calibrated,
            "notes": "held-out canonical ledger fit",
        },
        "provenance": {
            "source": "traittutor-canonical-ledger",
            "dataset_id": "ledger-export-sha256:abc123",
            "method": "constrained maximum likelihood with held-out validation",
            "calibrated_at": "2026-08-10T12:00:00+00:00",
            "training_event_count": 800,
            "validation_event_count": 200,
            "sequence_count": 120,
            "user_count": 40,
            "subject_count": 3,
            "kc_count": 18,
            "validation_log_loss": 0.49,
            "baseline_log_loss": 0.61,
        },
    }
    if schema_version == 2:
        artifact["quality"] = {
            "gates_passed": True,
            "fold_count": 5,
            "log_loss": 0.49,
            "baseline_log_loss": 0.61,
            "brier_score": 0.18,
            "baseline_brier_score": 0.2,
            "ece": 0.04,
            "log_loss_delta_ci95": [-0.2, -0.01],
            "bootstrap_unit": "owner",
            "bootstrap_samples": 200,
            "folds": [
                {
                    "fold": fold,
                    "training_owner_count": 32,
                    "validation_owner_count": 8,
                    "validation_event_count": 160,
                    "log_loss": 0.49,
                    "baseline_log_loss": 0.61,
                    "brier_score": 0.18,
                    "baseline_brier_score": 0.2,
                    "ece": 0.04,
                    "prior": 0.24,
                    "transition": 0.18,
                    "guess": 0.16,
                    "slip": 0.08,
                }
                for fold in range(5)
            ],
            "subject_slices": [],
        }
    return artifact


def test_missing_default_artifact_keeps_honest_uncalibrated_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(BKT_PARAMETERS_PATH_ENV, raising=False)
    monkeypatch.setenv("TRAITTUTOR_HOME", str(tmp_path))

    params = get_active_bkt_params()

    assert params.version == "v1-uncalibrated"
    assert params.calibrated is False


def test_versioned_artifact_activation_is_atomic_and_reversible(tmp_path: Path) -> None:
    directory = tmp_path / "bkt-parameters"
    artifact = BKTParameterArtifact.model_validate(_artifact(schema_version=2))

    target = write_immutable_artifact(directory, artifact)
    previous = activate_artifact(directory, artifact.parameters.version)

    assert previous is None
    assert (directory / "current.json").is_symlink()
    assert (directory / "current.json").resolve() == target.resolve()
    restore_activation(directory, previous)
    assert not (directory / "current.json").exists()


def test_production_activation_rejects_legacy_calibrated_artifact(tmp_path: Path) -> None:
    directory = tmp_path / "bkt-parameters"
    artifact = BKTParameterArtifact.model_validate(_artifact())
    write_immutable_artifact(directory, artifact)

    with pytest.raises(BKTParameterConfigurationError, match="artifact schema 2"):
        activate_artifact(directory, artifact.parameters.version)


def test_production_runtime_rejects_legacy_calibrated_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(path))
    monkeypatch.setenv("TRAITTUTOR_REQUIRE_CALIBRATED_BKT", "1")

    with pytest.raises(BKTParameterConfigurationError, match="artifact schema 2"):
        get_active_bkt_params()


def test_explicit_calibration_artifact_drives_rebuild_and_live_projection_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bkt-parameters.json"
    path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(path))
    params = get_active_bkt_params()
    assert params.version == "traittutor-ledger-2026-08-v1"
    assert params.calibrated is True

    captured: dict[str, object] = {}

    class Recorder:
        async def record_event(self, event: object, *, trusted: bool) -> list[object]:
            captured["event"] = event
            captured["trusted"] = trusted
            return []

    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=Recorder,
    )
    event, _ = chain.record_server_graded(
        user_id="u1",
        subject_id="math",
        question_id="q1",
        kc_ids=("kc1",),
        is_correct=True,
        item_valid=True,
        attribution_reliable=True,
        attempt_id="attempt-1",
        derived=lambda _event: None,
    )
    state = chain.rebuild_bkt().get(KnowledgeStateKey(user_id="u1", subject_id="math", kc_id="kc1"))

    assert state is not None
    assert state.param_version == params.version
    assert state.calibrated is True
    projected = chain._to_personalization_event(event)
    assert projected.payload["bkt_param_version"] == params.version


@pytest.mark.parametrize("payload", [_artifact(calibrated=False), {"schema_version": 1}])
def test_invalid_explicit_artifact_fails_closed(
    payload: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bkt-parameters.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(path))

    with pytest.raises(BKTParameterConfigurationError):
        get_active_bkt_params()


def test_missing_explicit_artifact_is_a_deployment_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(tmp_path / "missing.json"))

    with pytest.raises(BKTParameterConfigurationError):
        get_active_bkt_params()


@pytest.mark.parametrize("invalid_case", ["degenerate_parameters", "worse_holdout"])
def test_semantically_invalid_calibration_artifact_fails_closed(
    invalid_case: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _artifact()
    parameters = payload["parameters"]
    provenance = payload["provenance"]
    assert isinstance(parameters, dict)
    assert isinstance(provenance, dict)
    if invalid_case == "degenerate_parameters":
        parameters.update({"transition": 1.0, "guess": 1.0, "slip": 1.0, "prior": 1.0})
    else:
        provenance.update({"validation_log_loss": 0.9, "baseline_log_loss": 0.1})
    path = tmp_path / "invalid-bkt-parameters.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(path))

    with pytest.raises(BKTParameterConfigurationError):
        get_active_bkt_params()


def test_first_calibrated_version_replaces_old_live_posterior_from_full_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(BKT_PARAMETERS_PATH_ENV, raising=False)
    monkeypatch.setenv("TRAITTUTOR_HOME", str(tmp_path))
    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=lambda: service,
    )
    chain.record_server_graded(
        user_id="local-admin",
        subject_id="math",
        question_id="q1",
        kc_ids=("kc1",),
        is_correct=True,
        item_valid=True,
        attribution_reliable=True,
        attempt_id="attempt-1",
        derived=lambda _event: None,
    )
    assert service.subject_profile("math").concept_signals[0].bkt_calibrated is False

    artifact_path = tmp_path / "bkt-parameters.json"
    artifact_path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.setenv(BKT_PARAMETERS_PATH_ENV, str(artifact_path))
    chain.record_server_graded(
        user_id="local-admin",
        subject_id="math",
        question_id="q2",
        kc_ids=("kc1",),
        is_correct=False,
        item_valid=True,
        attribution_reliable=True,
        attempt_id="attempt-2",
        derived=lambda _event: None,
    )

    live = service.subject_profile("math").concept_signals[0]
    rebuilt = chain.rebuild_bkt().get(
        KnowledgeStateKey(user_id="local-admin", subject_id="math", kc_id="kc1")
    )
    assert rebuilt is not None
    assert live.mastery_probability == pytest.approx(rebuilt.mastery_probability)
    assert live.initial_mastery_probability == rebuilt.initial_mastery_probability
    assert live.verified_observation_count == rebuilt.verified_observation_count == 2
    assert live.bkt_param_version == rebuilt.param_version == "traittutor-ledger-2026-08-v1"
    assert live.bkt_calibrated is rebuilt.calibrated is True


def test_multi_kc_event_projects_and_retracts_every_canonical_cell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(BKT_PARAMETERS_PATH_ENV, raising=False)
    monkeypatch.setenv("TRAITTUTOR_HOME", str(tmp_path))
    service = PersonalizationService()
    monkeypatch.setattr(service, "_root", lambda: tmp_path / "learner")
    chain = CanonicalAnswerEventChain(
        LearnerEventLedger(tmp_path / "events.json"),
        personalization_service_factory=lambda: service,
    )

    event, _ = chain.record_server_graded(
        user_id="local-admin",
        subject_id="math",
        question_id="q-multi",
        kc_ids=("kc-1", "kc-2"),
        is_correct=True,
        item_valid=True,
        attribution_reliable=True,
        attempt_id="attempt-multi",
        derived=lambda _event: None,
    )

    canonical = chain.rebuild_bkt().all_for(user_id="local-admin", subject_id="math")
    live = service.subject_profile("math").concept_signals
    assert {item.kc_id for item in canonical} == {"kc-1", "kc-2"}
    assert {item.concept_id for item in live} == {"kc-1", "kc-2"}
    assert all(item.verified_observation_count == 1 for item in live)

    assert asyncio.run(service.delete_evidence(event.event_id)) is True
    assert service.subject_profile("math").concept_signals == []
