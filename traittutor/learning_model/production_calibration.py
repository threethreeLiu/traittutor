"""Privileged, read-only collection for production BKT calibration.

The collector enumerates server-owned account scopes and reads only each
scope's canonical effective learner-event ledger.  It emits a minimal,
keyed-pseudonymous stream: no question, answer text, material, chat, memory, or
persona data can enter the calibration dataset or artifact.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import hashlib
import hmac

from traittutor.multi_user.identity import list_user_info
from traittutor.multi_user.models import LOCAL_ADMIN_ID, UserScope
from traittutor.multi_user.paths import admin_scope, get_path_service_for_scope, scope_for_user
from traittutor.services.path_service import PathService

from .calibration import build_observation_sequences, calibration_dataset_id
from .events import LearnerEvent, LearnerEventLedger, is_strong_evidence


class ProductionCalibrationCollectionError(RuntimeError):
    """Production ledgers could not be collected without crossing an owner boundary."""


@dataclass(frozen=True)
class ProductionCalibrationDataset:
    events: tuple[LearnerEvent, ...]
    scope_count: int
    owner_count: int
    source_event_count: int
    observation_count: int
    sequence_count: int
    subject_count: int
    kc_count: int
    dataset_id: str


@dataclass(frozen=True)
class _ScopeRecord:
    scope: UserScope
    allowed_event_owners: frozenset[str]


def _pseudonym(key: bytes, namespace: str, value: str) -> str:
    digest = hmac.new(key, f"{namespace}\x1f{value}".encode(), hashlib.sha256).hexdigest()
    return f"{namespace}-{digest}"


def _production_scopes(accounts: Iterable[Mapping[str, object]]) -> tuple[_ScopeRecord, ...]:
    active = [item for item in accounts if not bool(item.get("disabled", False))]
    admin_ids = {
        str(item.get("id") or "").strip()
        for item in active
        if str(item.get("role") or "user") == "admin"
    }
    admin_ids.discard("")
    records: list[_ScopeRecord] = [
        _ScopeRecord(
            scope=admin_scope(),
            allowed_event_owners=frozenset({LOCAL_ADMIN_ID, *admin_ids}),
        )
    ]
    records.extend(
        _ScopeRecord(
            scope=scope_for_user(user_id, is_admin=False),
            allowed_event_owners=frozenset({user_id}),
        )
        for item in active
        if str(item.get("role") or "user") != "admin"
        if (user_id := str(item.get("id") or "").strip())
    )
    return tuple(records)


def collect_production_calibration_dataset(
    *,
    pseudonym_key: bytes,
    accounts: Iterable[Mapping[str, object]] | None = None,
    path_service_resolver: Callable[[UserScope], PathService] = get_path_service_for_scope,
) -> ProductionCalibrationDataset:
    """Collect effective strong evidence from every active owner scope.

    A missing owner database is an empty scope and is skipped without creating
    it.  A ledger row whose owner does not belong to the server-resolved scope
    fails the whole collection instead of being silently reassigned.
    """
    if len(pseudonym_key) < 32:
        raise ValueError("the calibration pseudonym key must contain at least 32 bytes")
    scope_records = _production_scopes(accounts if accounts is not None else list_user_info())
    anonymized: list[LearnerEvent] = []
    source_event_count = 0
    visited_databases: set[str] = set()
    for record in scope_records:
        path_service = path_service_resolver(record.scope)
        database_path = path_service.get_traittutor_database_path()
        database_identity = str(database_path.resolve())
        if database_identity in visited_databases:
            continue
        if not database_path.is_file():
            # A missing owner database is an empty scope and is skipped.
            continue
        visited_databases.add(database_identity)
        ledger = LearnerEventLedger(
            path_service.get_workspace_dir() / "learning_model" / "learner_events.json",
            path_service=path_service,
        )
        for event in ledger.effective_events():
            if event.user_id not in record.allowed_event_owners:
                raise ProductionCalibrationCollectionError(
                    "canonical ledger contains an event outside its owner scope"
                )
            if not is_strong_evidence(event) or event.subject_id is None or not event.kc_ids:
                continue
            source_event_count += 1
            owner_key = _pseudonym(pseudonym_key, "owner", event.user_id)
            subject_key = _pseudonym(pseudonym_key, "subject", event.subject_id)
            event_key = _pseudonym(pseudonym_key, "event", event.event_id)
            anonymized.append(
                LearnerEvent(
                    event_id=event_key,
                    idempotency_key=event_key,
                    user_id=owner_key,
                    subject_id=subject_key,
                    kc_ids=tuple(_pseudonym(pseudonym_key, "kc", kc_id) for kc_id in event.kc_ids),
                    surface_type=event.surface_type,
                    answer_correct=event.answer_correct,
                    evidence_strength="strong",
                    attribution_status="reliable",
                    created_at=event.created_at,
                )
            )
    sequences = build_observation_sequences(anonymized)
    return ProductionCalibrationDataset(
        events=tuple(anonymized),
        scope_count=len(visited_databases),
        owner_count=len({item.key[0] for item in sequences}),
        source_event_count=source_event_count,
        observation_count=sum(len(item.outcomes) for item in sequences),
        sequence_count=len(sequences),
        subject_count=len({item.key[1] for item in sequences}),
        kc_count=len({item.key[2] for item in sequences}),
        dataset_id=calibration_dataset_id(anonymized),
    )


__all__ = [
    "ProductionCalibrationCollectionError",
    "ProductionCalibrationDataset",
    "collect_production_calibration_dataset",
]
