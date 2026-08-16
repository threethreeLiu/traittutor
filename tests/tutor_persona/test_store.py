from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from traittutor.tutor_persona.models import TutorPersonaSettings
from traittutor.tutor_persona.store import (
    TutorPersonaIdempotencyConflict,
    TutorPersonaStore,
    TutorPersonaStoreError,
    TutorPersonaVersionConflict,
)

T0 = "2026-08-10T00:00:00+00:00"
T1 = "2026-08-10T00:01:00+00:00"


def test_store_is_durable_versioned_and_owner_isolated(tmp_path: Path) -> None:
    path = tmp_path / "personas.json"
    owner_a = TutorPersonaStore("owner-a", path=path)
    initial = owner_a.get_or_create_default(created_at=T0)
    updated = owner_a.update(
        TutorPersonaSettings(tone="calm", feedback_format="concise"),
        expected_version=1,
        idempotency_key="update-a-1",
        updated_at=T1,
    )

    assert updated.persona_id == initial.persona_id
    assert updated.version == 2
    assert updated.created_at == T0
    assert updated.updated_at == T1
    assert [profile.version for profile in owner_a.history()] == [1, 2]
    assert TutorPersonaStore("owner-a", path=path).get_current() == updated

    owner_b = TutorPersonaStore("owner-b", path=path)
    assert owner_b.get_current() is None
    profile_b = owner_b.get_or_create_default(created_at=T0)
    assert profile_b.owner_id == "owner-b"
    assert profile_b.persona_id != initial.persona_id
    assert [profile.owner_id for profile in owner_b.history()] == ["owner-b"]


def test_cas_rejects_stale_update_without_new_version(tmp_path: Path) -> None:
    store = TutorPersonaStore("owner", path=tmp_path / "personas.json")
    store.get_or_create_default(created_at=T0)
    store.update(
        TutorPersonaSettings(tone="calm"),
        expected_version=1,
        idempotency_key="first",
        updated_at=T1,
    )

    with pytest.raises(TutorPersonaVersionConflict) as exc_info:
        store.update(
            TutorPersonaSettings(tone="energetic"),
            expected_version=1,
            idempotency_key="stale",
        )

    assert exc_info.value.expected_version == 1
    assert exc_info.value.actual_version == 2
    assert [profile.version for profile in store.history()] == [1, 2]


def test_failed_initial_cas_has_no_persistent_side_effect(tmp_path: Path) -> None:
    path = tmp_path / "personas.json"
    store = TutorPersonaStore("owner", path=path)

    with pytest.raises(TutorPersonaVersionConflict):
        store.update(
            TutorPersonaSettings(tone="calm"),
            expected_version=2,
            idempotency_key="wrong-initial-version",
        )

    assert not path.exists()
    assert store.get_current() is None


def test_idempotency_replays_original_version_and_rejects_changed_command(
    tmp_path: Path,
) -> None:
    path = tmp_path / "personas.json"
    store = TutorPersonaStore("owner", path=path)
    store.get_or_create_default(created_at=T0)
    settings = TutorPersonaSettings(tone="neutral", proactivity="reminders_only")

    first = store.update(
        settings,
        expected_version=1,
        idempotency_key="same-request",
        updated_at=T1,
    )
    replay = TutorPersonaStore("owner", path=path).update(
        settings,
        expected_version=1,
        idempotency_key="same-request",
        updated_at="2026-08-10T00:02:00+00:00",
    )

    assert replay == first
    assert len(store.history()) == 2
    raw = store._adapter.snapshot()
    assert all(record.get("key_hash") != "same-request" for record in raw["idempotency"])
    assert len(raw["idempotency"][0]["key_hash"]) == 64

    with pytest.raises(TutorPersonaIdempotencyConflict):
        store.update(
            TutorPersonaSettings(tone="warm"),
            expected_version=1,
            idempotency_key="same-request",
        )


def test_concurrent_cas_allows_exactly_one_writer(tmp_path: Path) -> None:
    path = tmp_path / "personas.json"
    TutorPersonaStore("owner", path=path).get_or_create_default(created_at=T0)
    barrier = threading.Barrier(2)

    def update(tone: str) -> str:
        store = TutorPersonaStore("owner", path=path)
        barrier.wait(timeout=5)
        try:
            store.update(
                TutorPersonaSettings(tone=tone),  # type: ignore[arg-type]
                expected_version=1,
                idempotency_key=f"concurrent-{tone}",
            )
        except TutorPersonaVersionConflict:
            return "conflict"
        return "updated"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["calm", "energetic"]))

    assert sorted(results) == ["conflict", "updated"]
    assert [profile.version for profile in TutorPersonaStore("owner", path=path).history()] == [
        1,
        2,
    ]


def test_corrupt_store_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "personas.json"
    (tmp_path / "traittutor.sqlite3").write_text("not-sqlite", encoding="utf-8")

    with pytest.raises(TutorPersonaStoreError):
        TutorPersonaStore("owner", path=path).get_current()
