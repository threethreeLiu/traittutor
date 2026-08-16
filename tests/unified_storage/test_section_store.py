"""Tests for the SectionedRecordStore adapter (Phase 5 task 10).

The adapter is the bridge that lets a legacy ``json_sectioned`` / ``json_list``
store swap its file-lock + atomic-write seam for the unified database without
changing business logic.  These tests pin the contract the swap relies on:
round-trip fidelity, append-order, per-record owner resolution matching the
migration, PATH_SCOPE tagging, scalar-marker losslessness, rollback, and
idempotent re-save.

All tests use ``tmp_path`` only — never the real ``data/`` tree.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from traittutor.services.path_service import PathService
from traittutor.unified_storage import SectionedRecordStore
from traittutor.unified_storage.mapping import get_source
from traittutor.unified_storage.migrator import migrate_sources, path_scoped_suffix


def _service(tmp_path: Path) -> PathService:
    return PathService(workspace_root=tmp_path)


def _read_db(
    service: PathService, table: str, where: str = "", params: tuple[object, ...] = ()
) -> list[sqlite3.Row]:
    db = service.get_traittutor_database_path()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        query = f"SELECT * FROM {table}"  # noqa: S608 - test-local fixed table name
        if where:
            query += f" WHERE {where}"
        return con.execute(query, params).fetchall()
    finally:
        con.close()


# ── Round-trip fidelity ──────────────────────────────────────────────────────


def test_round_trip_preserves_records_and_schema_version(tmp_path: Path) -> None:
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    payload = {
        "schema_version": 1,
        "workspaces": [
            {"workspace_id": "ws-1", "owner_id": "local-admin", "name": "alpha"},
            {"workspace_id": "ws-2", "owner_id": "local-admin", "name": "beta"},
        ],
    }
    store.replace_all(payload)

    loaded = store.snapshot()
    assert loaded["schema_version"] == 1
    assert loaded["workspaces"] == payload["workspaces"]


def test_round_trip_preserves_append_order(tmp_path: Path) -> None:
    """rowid ordering keeps file/append order — conversations depend on it."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    ordered = [
        {"workspace_id": f"ws-{i}", "owner_id": "local-admin", "name": str(i)} for i in range(5)
    ]
    store.replace_all({"schema_version": 1, "workspaces": ordered})

    ids = [row["workspace_id"] for row in store.snapshot()["workspaces"]]
    assert ids == [f"ws-{i}" for i in range(5)]


def test_round_trip_preserves_revisions_with_the_same_research_entity_id(
    tmp_path: Path,
) -> None:
    """Append-only research history must not collapse onto the natural id."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    revisions = [
        {
            "workspace_id": "ws-1",
            "owner_id": "local-admin",
            "revision": 1,
            "name": "original",
        },
        {
            "workspace_id": "ws-1",
            "owner_id": "local-admin",
            "revision": 2,
            "name": "revised",
        },
    ]

    store.replace_all({"schema_version": 1, "workspaces": revisions})

    assert store.snapshot()["workspaces"] == revisions


def test_workspace_wide_load_returns_all_owners(tmp_path: Path) -> None:
    """The adapter reads every row for a section (legacy 'one file = all
    records'); owner filtering stays in the business module."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 1,
            "workspaces": [
                {"workspace_id": "ws-a", "owner_id": "alice", "name": "a"},
                {"workspace_id": "ws-b", "owner_id": "bob", "name": "b"},
            ],
        }
    )
    owners = sorted(row["owner_id"] for row in store.snapshot()["workspaces"])
    assert owners == ["alice", "bob"]


def test_replace_all_overwrites_previous_section_state(tmp_path: Path) -> None:
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "o", "name": "x"}],
        }
    )
    # A second replace_all with a different set must not leave stale rows.
    store.replace_all({"schema_version": 1, "workspaces": []})
    assert store.snapshot()["workspaces"] == []


def test_idempotent_re_save_does_not_duplicate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    payload = {
        "schema_version": 1,
        "workspaces": [{"workspace_id": "ws-1", "owner_id": "o", "name": "x"}],
    }
    store.replace_all(payload)
    store.replace_all(payload)
    store.replace_all(payload)
    assert len(store.snapshot()["workspaces"]) == 1


# ── Per-record owner resolution (matches the migration) ──────────────────────


def test_owner_resolved_per_record_user_id_field(tmp_path: Path) -> None:
    """learner_events uses USER_ID_FIELD; the stored owner_id must equal the
    record's user_id — the same value the migration wrote."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "learner_events", "local-admin", schema_version=2, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 2,
            "events": [
                {
                    "event_id": "ev-1",
                    "user_id": "alice",
                    "evidence_strength": "strong",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "amendments": [],
            "derived_applied": [],
            "derived_queue": [],
        }
    )

    rows = _read_db(service, "learner_events", "record_id=?", ("ev-1",))
    assert len(rows) == 1
    assert rows[0]["owner_id"] == "alice"  # from user_id, not the workspace scope
    assert rows[0]["source_section"] == "learner_events/events"


def test_owner_join_inherited_from_referenced_event(tmp_path: Path) -> None:
    """A derived-queue row (no user_id) inherits its event's owner via the
    intra-source event_id join — the same path the migration used."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "learner_events", "local-admin", schema_version=2, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 2,
            "events": [
                {
                    "event_id": "ev-1",
                    "user_id": "alice",
                    "evidence_strength": "exposure",
                    "created_at": "2026-01-01T00:00:00+00:00",
                }
            ],
            "amendments": [],
            "derived_applied": [],
            "derived_queue": [
                {
                    "event_id": "ev-1",
                    "operation": "bkt_update",
                    "queued_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                }
            ],
        }
    )

    # The derived_queue row's record_id is a content hash (id_field is None), so
    # we select by section and assert the inherited owner landed on the row.
    rows = _read_db(
        service,
        "learner_event_derived_queue",
        "source_section=?",
        ("learner_events/derived_queue",),
    )
    assert len(rows) == 1
    assert rows[0]["owner_id"] == "alice"  # inherited, not workspace-scoped


# ── Scalar markers (derived_applied string list) ─────────────────────────────


def test_scalar_markers_round_trip_losslessly(tmp_path: Path) -> None:
    """derived_applied is a list[str]; the migration skipped non-dict rows, but
    the adapter stores them verbatim so no idempotency marker is lost."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "learner_events", "local-admin", schema_version=2, path_service=service
    )
    markers = ["ev-1\x1fbkt_update", "ev-2\x1ferror_record"]
    store.replace_all(
        {
            "schema_version": 2,
            "events": [],
            "amendments": [],
            "derived_applied": markers,
            "derived_queue": [],
        }
    )
    loaded = store.snapshot()
    assert loaded["derived_applied"] == markers


def test_scalar_marker_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "learner_events", "local-admin", schema_version=2, path_service=service
    )
    payload = {
        "schema_version": 2,
        "events": [],
        "amendments": [],
        "derived_applied": ["ev-1\x1fop"],
        "derived_queue": [],
    }
    store.replace_all(payload)
    store.replace_all(payload)
    assert store.snapshot()["derived_applied"] == ["ev-1\x1fop"]


# ── PATH_SCOPE tagging (memory_index) ────────────────────────────────────────


def test_path_scope_section_tagged(tmp_path: Path) -> None:
    """A PATH_SCOPE source's source_section carries the #path_scoped tag — the
    same tag the migrator/reconciler query on."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "memory_index", "local-admin", schema_version=2, path_service=service
    )
    assert path_scoped_suffix(get_source("memory_index")) == "#path_scoped"
    store.replace_all(
        {
            "schema_version": 2,
            "states": [{"owner_id": "local-admin", "generation": 1}],
            "indexes": [],
            "invalidations": [],
        }
    )

    rows = _read_db(service, "memory_index_states")
    assert len(rows) == 1
    assert rows[0]["source_section"] == "memory_index/states#path_scoped"
    assert rows[0]["owner_id"] == "local-admin"  # path scope, not row-attested


# ── Transaction semantics ────────────────────────────────────────────────────


def test_rollback_on_exception_leaves_db_unchanged(tmp_path: Path) -> None:
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "o", "name": "x"}],
        }
    )

    with pytest.raises(RuntimeError):
        with store.locked() as payload:
            payload["workspaces"].append({"workspace_id": "ws-2", "owner_id": "o", "name": "y"})
            store.replace_all(payload)
            raise RuntimeError("simulate downstream failure")

    # The exception rolled the transaction back; ws-2 must not have persisted.
    ids = [row["workspace_id"] for row in store.snapshot()["workspaces"]]
    assert ids == ["ws-1"]


def test_locked_without_persist_commits_nothing(tmp_path: Path) -> None:
    """A read-only locked block (no replace_all) must not write — matching the
    legacy 'lock, read, maybe write' semantics."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "o", "name": "x"}],
        }
    )
    with store.locked() as payload:
        payload["workspaces"].append({"workspace_id": "ws-2", "owner_id": "o", "name": "y"})
        # deliberately no replace_all
    assert len(store.snapshot()["workspaces"]) == 1


def test_snapshot_inside_locked_reuses_active_transaction(tmp_path: Path) -> None:
    """A _load() inside a _locked() block must not open a nested connection
    (that would self-deadlock on SQLite's write lock)."""
    service = _service(tmp_path)
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "o", "name": "x"}],
        }
    )
    with store.locked() as payload:
        snapshot = store.snapshot()  # would deadlock if it opened a new conn
        assert snapshot == payload


def test_fresh_database_behaves_like_empty_store(tmp_path: Path) -> None:
    """No migration run → tables created empty → snapshot returns empty lists."""
    service = _service(tmp_path)
    store = SectionedRecordStore("memory_v2", "local-admin", schema_version=2, path_service=service)
    payload = store.snapshot()
    assert payload["schema_version"] == 2
    for key in (
        "items",
        "candidates",
        "lifecycle",
        "access_records",
        "grants",
        "mutation_receipts",
    ):
        assert payload[key] == []


# ── Fidelity vs. the migration ───────────────────────────────────────────────


def test_live_write_record_id_matches_migration(tmp_path: Path) -> None:
    """A live write must land on the same record_id the migration produced, so
    idempotent INSERT OR REPLACE updates the row instead of duplicating."""
    service = _service(tmp_path)

    # 1) Seed via the migration path (simulating the cut-over state).
    src = service.user_data_dir / "workspace/traittutor/research_workspaces.json"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "workspaces": [{"workspace_id": "ws-1", "owner_id": "local-admin", "name": "seed"}],
            }
        ),
        encoding="utf-8",
    )
    migrate_sources(
        source_names=("research_workspaces",),
        path_service=service,
        owner_id="local-admin",
    )

    # 2) A live adapter write for the same record must reuse record_id "ws-1".
    store = SectionedRecordStore(
        "research_workspaces", "local-admin", schema_version=1, path_service=service
    )
    store.replace_all(
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "ws-1", "owner_id": "local-admin", "name": "updated"}],
        }
    )
    rows = store.snapshot()["workspaces"]
    assert len(rows) == 1  # updated, not duplicated
    assert rows[0]["name"] == "updated"
