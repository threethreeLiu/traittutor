"""Owner-bound, file-locked persistence for versioned Tutor Persona profiles."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from pydantic import ValidationError

from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore

from .models import TutorPersonaProfile, TutorPersonaSettings, default_persona_settings

_STORE_SCHEMA_VERSION = 1


class TutorPersonaStoreError(RuntimeError):
    """The durable profile store cannot safely complete the operation."""


class TutorPersonaVersionConflict(TutorPersonaStoreError):
    """The supplied expected version no longer matches the active profile."""

    def __init__(self, *, expected_version: int, actual_version: int) -> None:
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(f"expected version {expected_version}, found {actual_version}")


class TutorPersonaIdempotencyConflict(TutorPersonaStoreError):
    """An idempotency key was replayed with a different command."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TutorPersonaStore:
    """Persist exactly one versioned profile per owner.

    The owner is bound at construction so caller-controlled request data can
    never broaden a read. Every write, CAS check, and idempotency registration
    occurs under one process-shared file lock and one atomic JSON replacement.
    """

    def __init__(self, owner_id: str, *, path: Path | None = None) -> None:
        owner_id = owner_id.strip()
        if not owner_id:
            raise ValueError("owner_id is required")
        self.owner_id = owner_id
        self._store_path = path
        self._adapter = SectionedRecordStore(
            "tutor_personas",
            owner_id,
            schema_version=_STORE_SCHEMA_VERSION,
            path_service=get_path_service() if path is None else None,
            legacy_path=path,
        )

    def _path(self) -> Path:
        return self._store_path or (
            get_path_service().get_workspace_dir() / "traittutor" / "tutor_personas.json"
        )

    def _lock_path(self) -> Path:
        return self._path().with_suffix(".lock")

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"schema_version": _STORE_SCHEMA_VERSION, "profiles": [], "idempotency": []}

    def _load(self) -> dict[str, Any]:
        try:
            payload = self._adapter.snapshot()
        except Exception as exc:
            raise TutorPersonaStoreError("unable to read Tutor Persona data") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _STORE_SCHEMA_VERSION
            or not isinstance(payload.get("profiles"), list)
            or not isinstance(payload.get("idempotency"), list)
        ):
            raise TutorPersonaStoreError("Tutor Persona data has an invalid format")
        return payload

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        with self._adapter.locked() as payload:
            yield payload

    def _owned_profiles(self, payload: dict[str, Any]) -> list[TutorPersonaProfile]:
        try:
            return [
                TutorPersonaProfile.model_validate(record)
                for record in payload["profiles"]
                if isinstance(record, dict) and record.get("owner_id") == self.owner_id
            ]
        except ValidationError as exc:
            raise TutorPersonaStoreError("Tutor Persona profile data is invalid") from exc

    def _latest(self, payload: dict[str, Any]) -> TutorPersonaProfile | None:
        profiles = self._owned_profiles(payload)
        return max(profiles, key=lambda profile: profile.version, default=None)

    def _append_default_locked(
        self, payload: dict[str, Any], *, created_at: str | None = None
    ) -> TutorPersonaProfile:
        now = created_at or _now()
        profile = TutorPersonaProfile(
            **default_persona_settings().model_dump(mode="python"),
            persona_id=f"tp_{uuid4().hex[:20]}",
            owner_id=self.owner_id,
            version=1,
            created_at=now,
            updated_at=now,
        )
        payload["profiles"].append(profile.model_dump(mode="json"))
        return profile

    def get_or_create_default(self, *, created_at: str | None = None) -> TutorPersonaProfile:
        with self._locked() as payload:
            current = self._latest(payload)
            if current is not None:
                return current
            profile = self._append_default_locked(payload, created_at=created_at)
            self._adapter.replace_all(payload)
            return profile

    def get_current(self) -> TutorPersonaProfile | None:
        """Return only this store's owner profile, hiding all other owners."""

        return self._latest(self._load())

    def history(self) -> tuple[TutorPersonaProfile, ...]:
        return tuple(sorted(self._owned_profiles(self._load()), key=lambda item: item.version))

    @staticmethod
    def _key_hash(idempotency_key: str) -> str:
        normalized = idempotency_key.strip()
        if not normalized or len(normalized) > 160:
            raise ValueError("idempotency_key must contain 1 to 160 characters")
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _request_hash(settings: TutorPersonaSettings, expected_version: int) -> str:
        canonical = json.dumps(
            {
                "expected_version": expected_version,
                "settings": settings.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _replayed_profile(
        self,
        payload: dict[str, Any],
        *,
        key_hash: str,
        request_hash: str,
    ) -> TutorPersonaProfile | None:
        record = next(
            (
                item
                for item in payload["idempotency"]
                if isinstance(item, dict)
                and item.get("owner_id") == self.owner_id
                and item.get("key_hash") == key_hash
            ),
            None,
        )
        if record is None:
            return None
        if record.get("request_hash") != request_hash:
            raise TutorPersonaIdempotencyConflict("idempotency key reused with another update")
        profile = next(
            (
                item
                for item in self._owned_profiles(payload)
                if item.persona_id == record.get("persona_id")
                and item.version == record.get("version")
            ),
            None,
        )
        if profile is None:
            raise TutorPersonaStoreError("idempotency record references a missing profile")
        return profile

    def update(
        self,
        settings: TutorPersonaSettings,
        *,
        expected_version: int,
        idempotency_key: str,
        updated_at: str | None = None,
    ) -> TutorPersonaProfile:
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        key_hash = self._key_hash(idempotency_key)
        request_hash = self._request_hash(settings, expected_version)
        with self._locked() as payload:
            replay = self._replayed_profile(payload, key_hash=key_hash, request_hash=request_hash)
            if replay is not None:
                return replay
            current = self._latest(payload)
            if current is None:
                current = self._append_default_locked(payload, created_at=updated_at)
            if current.version != expected_version:
                raise TutorPersonaVersionConflict(
                    expected_version=expected_version,
                    actual_version=current.version,
                )
            next_profile = TutorPersonaProfile(
                **settings.model_dump(mode="python"),
                persona_id=current.persona_id,
                owner_id=self.owner_id,
                version=current.version + 1,
                created_at=current.created_at,
                updated_at=updated_at or _now(),
            )
            payload["profiles"].append(next_profile.model_dump(mode="json"))
            payload["idempotency"].append(
                {
                    "owner_id": self.owner_id,
                    "key_hash": key_hash,
                    "request_hash": request_hash,
                    "persona_id": next_profile.persona_id,
                    "version": next_profile.version,
                }
            )
            self._adapter.replace_all(payload)
            return next_profile


__all__ = [
    "TutorPersonaIdempotencyConflict",
    "TutorPersonaStore",
    "TutorPersonaStoreError",
    "TutorPersonaVersionConflict",
]
