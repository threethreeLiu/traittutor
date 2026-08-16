from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from traittutor.services.path_service import PathService
from traittutor.unified_storage.migrator import migrate_sources


def _service(tmp_path: Path) -> PathService:
    return PathService(workspace_root=tmp_path)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_system_task_sqlite(workspace_root: Path, rows: list[tuple[str, str]]) -> None:
    db_path = workspace_root / "system" / "generation-tasks.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE generation_tasks (generation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL)"
    )
    con.executemany("INSERT INTO generation_tasks VALUES (?,?)", rows)
    con.commit()
    con.close()


def _row_count(db_path: Path, table: str, source_section: str | None = None) -> int:
    con = sqlite3.connect(db_path)
    if source_section is None:
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    else:
        n = con.execute(
            f"SELECT count(*) FROM {table} WHERE source_section=?", (source_section,)
        ).fetchone()[0]
    con.close()
    return n


def test_migrate_json_sectioned_preserves_owner_and_payload(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/capability_decisions.json",
        {
            "schema_version": 1,
            "decisions": [
                {"decision_id": "d1", "owner_id": "u-a", "choice": "route_x"},
                {"decision_id": "d2", "owner_id": "u-a", "choice": "route_y"},
            ],
            "idempotency": [],
        },
    )

    report = migrate_sources(
        source_names=("capability_decisions",), path_service=service, owner_scope="test"
    )

    assert report.reconciled is True
    decisions = next(r for r in report.results if r.target_table == "capability_decisions")
    assert decisions.inserted_record_count == 2
    assert decisions.deferred_record_count == 0
    assert decisions.owner_ids == ("u-a",)

    db = service.get_traittutor_database_path()
    assert _row_count(db, "capability_decisions", "capability_decisions/decisions") == 2
    # payload_json preserves the source record verbatim.
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM capability_decisions WHERE record_id=?", ("d1",)).fetchone()
    con.close()
    assert row["owner_id"] == "u-a"
    assert json.loads(row["payload_json"])["choice"] == "route_x"


def test_migrate_is_idempotent_on_rerun(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/capability_decisions.json",
        {
            "schema_version": 1,
            "decisions": [{"decision_id": "d1", "owner_id": "u-a"}],
            "idempotency": [],
        },
    )

    first = migrate_sources(source_names=("capability_decisions",), path_service=service)
    second = migrate_sources(source_names=("capability_decisions",), path_service=service)

    assert first.total_inserted == 1
    # Second run short-circuits the source (same sha already completed).
    assert second.total_inserted == 0
    assert any("already completed" in w for w in second.warnings)
    db = service.get_traittutor_database_path()
    assert _row_count(db, "capability_decisions") == 1


def test_migrate_join_source_attributes_and_defers_orphans(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_system_task_sqlite(tmp_path, [("gen_known", "u-a")])
    _write_json(
        base / "workspace/traittutor/page-schemas.json",
        {
            "schema_version": 1,
            "pages": [
                {"page_schema_id": "pg1", "generation_run_id": "gen_known"},
                {"page_schema_id": "pg2", "generation_run_id": "gen_orphan"},
            ],
        },
    )

    report = migrate_sources(source_names=("page_schemas",), path_service=service)
    pages = next(r for r in report.results if r.target_table == "page_schemas")
    assert pages.inserted_record_count == 1  # pg1 resolved
    assert pages.deferred_record_count == 1  # pg2 orphan deferred, not force-attributed
    assert pages.owner_ids == ("u-a",)


def test_migrate_generation_results_skips_residue(tmp_path: Path) -> None:
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_system_task_sqlite(tmp_path, [("gen_real", "u-a")])
    _write_json(
        base / "workspace/traittutor/generations/gen_real.json",
        {"generation_id": "gen_real", "result": {"items": [{"q": 1}]}},
    )
    _write_json(
        base / "workspace/traittutor/generations/gen_empty.json",
        {"generation_id": "gen_empty", "result": {"items": []}},
    )

    report = migrate_sources(source_names=("generation_results",), path_service=service)
    results = next(r for r in report.results if r.target_table == "generation_results")
    assert results.inserted_record_count == 1
    assert results.residue_record_count == 1
    db = service.get_traittutor_database_path()
    assert _row_count(db, "generation_results") == 1


def test_migrate_chat_history_uses_owner_column_and_scope_fallback(tmp_path: Path) -> None:
    service = _service(tmp_path)
    db_path = service.user_data_dir / "chat_history.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE sessions (id TEXT, owner_id TEXT)")
    con.execute("INSERT INTO sessions VALUES ('s1', 'u-a')")
    con.execute("INSERT INTO sessions VALUES ('s2', NULL)")  # NULL → scope fallback
    con.commit()
    con.close()

    report = migrate_sources(source_names=("chat_history",), path_service=service)
    sessions = next(r for r in report.results if r.target_table == "chat_sessions")
    assert sessions.inserted_record_count == 2
    assert sessions.deferred_record_count == 0
    assert "u-a" in sessions.owner_ids
    assert any("__scope_fallback__" in (r.note or "") for r in report.results)


def test_migrate_reconciliation_flag_set(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _write_json(
        service.user_data_dir / "workspace/traittutor/tutor_personas.json",
        {
            "schema_version": 1,
            "profiles": [{"persona_id": "tp1", "owner_id": "u-a"}],
            "idempotency": [],
        },
    )

    report = migrate_sources(source_names=("tutor_personas",), path_service=service)
    assert report.reconciled is True
    assert report.integrity_check == "ok"


# ─── Phase 3: learning domain + evidence chain ────────────────────────────────


def _write_knowledge_graph(workspace_root: Path) -> Path:
    """Create an owner-less knowledge-graph sqlite mirroring the real schema."""
    db_path = workspace_root / "user" / "workspace" / "learner" / "knowledge-graph.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE graph_subjects (subject_id TEXT, subject_json TEXT, updated_at TEXT)")
    con.execute(
        "CREATE TABLE graph_concepts "
        "(subject_id TEXT, concept_id TEXT, label TEXT, module_id TEXT, confidence REAL, updated_at TEXT)"
    )
    con.execute(
        "INSERT INTO graph_subjects VALUES ('subj1', '{\"name\":\"algebra\"}', '2026-08-01T00:00:00Z')"
    )
    con.execute(
        "INSERT INTO graph_concepts VALUES ('subj1','c1','variables','m1',0.8,'2026-08-01T00:00:00Z')"
    )
    con.execute(
        "INSERT INTO graph_concepts VALUES ('subj1','c2','equations','m1',0.5,'2026-08-01T00:00:00Z')"
    )
    con.commit()
    con.close()
    return db_path


def test_migrate_learning_packs_preserves_aggregate_verbatim(tmp_path: Path) -> None:
    """The pack aggregate migrates as one verbatim row — normalization is a
    Phase 5 read-adapter concern, never a lossy migration step."""
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/learning-packs.json",
        [
            {
                "pack_id": "pk1",
                "owner_id": "u-a",
                "goal": "learn algebra",
                "journeys": [{"journey_id": "j1", "supersedes_plan_id": None}],
                "materials": [{"material_id": "m1", "revision": 3}],
            }
        ],
    )

    report = migrate_sources(source_names=("learning_packs",), path_service=service)
    assert report.reconciled is True
    packs = next(r for r in report.results if r.target_table == "learning_packs")
    assert packs.inserted_record_count == 1
    assert packs.owner_ids == ("u-a",)

    db = service.get_traittutor_database_path()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM learning_packs WHERE record_id=?", ("pk1",)).fetchone()
    con.close()
    payload = json.loads(row["payload_json"])
    # Embedded journey/material structure is preserved untouched.
    assert payload["journeys"][0]["journey_id"] == "j1"
    assert payload["materials"][0]["revision"] == 3


def test_migrate_learner_events_user_id_is_owner(tmp_path: Path) -> None:
    """Iron law #2: events carry user_id (the learning owner), not owner_id.
    The USER_ID_FIELD strategy resolves the owner from user_id."""
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/learning_model/learner_events.json",
        {
            "schema_version": 1,
            "events": [
                {
                    "event_id": "ev1",
                    "user_id": "u_learner",
                    "attribution_status": "reliable",
                    "evidence_strength": "strong",
                    "answer_correct": True,
                    "idempotency_key": "ik1",
                }
            ],
            "amendments": [],
            "derived_applied": [],
            "derived_queue": [],
        },
    )

    report = migrate_sources(source_names=("learner_events",), path_service=service)
    events = next(r for r in report.results if r.target_table == "learner_events")
    assert events.inserted_record_count == 1
    assert events.owner_ids == ("u_learner",)  # user_id, not owner_id
    db = service.get_traittutor_database_path()
    con = sqlite3.connect(db)
    row = con.execute("SELECT owner_id FROM learner_events WHERE record_id=?", ("ev1",)).fetchone()
    con.close()
    assert row[0] == "u_learner"


def test_migrate_learner_events_replay_is_idempotent_and_preserves_attribution(
    tmp_path: Path,
) -> None:
    """Event-replay protection (plan §5 Phase 3 gate): re-migrating is a no-op
    (event_id PK + sha guard), the derived ledger travels verbatim, and weak
    evidence is never promoted — attribution_pending events stay pending."""
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/learning_model/learner_events.json",
        {
            "schema_version": 1,
            "events": [
                {
                    "event_id": "ev_strong",
                    "user_id": "u1",
                    "attribution_status": "reliable",
                    "evidence_strength": "strong",
                    "answer_correct": True,
                    "idempotency_key": "ik_s",
                },
                {
                    "event_id": "ev_weak",
                    "user_id": "u_learner",
                    "attribution_status": "attribution_pending",
                    "evidence_strength": "none",
                    "answer_correct": False,
                    "idempotency_key": "ik_w",
                },
            ],
            "amendments": [],
            "derived_applied": [],
            "derived_queue": [
                {
                    "event_id": "ev_strong",
                    "operation": "apply_bkt",
                    "queued_at": "2026-08-01T00:00:00Z",
                }
            ],
        },
    )

    first = migrate_sources(source_names=("learner_events",), path_service=service)
    second = migrate_sources(source_names=("learner_events",), path_service=service)

    # First run: 2 events + 1 derived_queue row inserted (3 total across sections).
    assert first.total_inserted == 3
    # Re-run is a no-op: the source sha is already completed, nothing re-inserted.
    assert second.total_inserted == 0
    assert any("already completed" in w for w in second.warnings)

    db = service.get_traittutor_database_path()
    # event_id is the PK: replay never duplicates the event row.
    assert _row_count(db, "learner_events") == 2
    # The derived_queue ledger traveled verbatim — a future replay knows what is
    # already queued, so the same event_id is not re-derived.
    assert _row_count(db, "learner_event_derived_queue") == 1

    # Weak evidence was preserved, NOT promoted to strong (iron law #2).
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    weak = con.execute(
        "SELECT payload_json FROM learner_events WHERE record_id=?", ("ev_weak",)
    ).fetchone()
    con.close()
    payload = json.loads(weak["payload_json"])
    assert payload["attribution_status"] == "attribution_pending"
    assert payload["evidence_strength"] == "none"


def test_migrate_knowledge_graph_uses_path_scope_and_marks_not_bkt_evidence(
    tmp_path: Path,
) -> None:
    """The graph store has no owner column; rows take the workspace owner by
    path scope, source_section is tagged #path_scoped, and the note records that
    graph evidence is structural — never BKT strong evidence."""
    service = _service(tmp_path)
    _write_knowledge_graph(tmp_path)

    report = migrate_sources(
        source_names=("knowledge_graph",), path_service=service, owner_id="local-admin"
    )
    assert report.reconciled is True

    subjects = next(r for r in report.results if r.target_table == "graph_subjects")
    concepts = next(r for r in report.results if r.target_table == "graph_concepts")
    # Every row attributed to the workspace owner (path scope), not deferred.
    assert subjects.deferred_record_count == 0
    assert concepts.deferred_record_count == 0
    assert subjects.owner_ids == ("local-admin",)
    assert concepts.owner_ids == ("local-admin",)
    # source_section is tagged so path-scoped attribution is auditable.
    assert subjects.source_ref.endswith("::graph_subjects")
    # The not-BKT-strong-evidence note is present on every graph result.
    assert all("not BKT strong evidence" in (r.note or "") for r in report.results)

    db = service.get_traittutor_database_path()
    # graph_subjects uses its natural PK (subject_id); graph_concepts has no
    # single-column PK and uses a deterministic content-hash id.
    assert _row_count(db, "graph_subjects") == 1
    assert _row_count(db, "graph_concepts") == 2
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT source_section FROM graph_subjects WHERE record_id=?", ("subj1",)
    ).fetchone()
    con.close()
    assert row["source_section"].endswith("#path_scoped")


# ─── Phase 4: memory + research workspaces ────────────────────────────────────


def test_migrate_memory_v2_preserves_all_sections_and_owners(tmp_path: Path) -> None:
    """Every memory-v2 section carries owner_id and migrates verbatim — active
    facts, candidates, lifecycle, access envelopes, grants and receipts.  Iron
    law #7: provenance/status travel untouched; no silent fact rewrite."""
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/memory-v2.json",
        {
            "schema_version": 2,
            "items": [
                {
                    "memory_id": "m1",
                    "owner_id": "u-a",
                    "key": "goal",
                    "value": "alg",
                    "status": "active",
                    "confidence": 0.9,
                    "provenance": {},
                    "valid_from": "2026-08-01T00:00:00Z",
                    "created_at": "2026-08-01T00:00:00Z",
                    "updated_at": "2026-08-01T00:00:00Z",
                }
            ],
            "candidates": [
                {
                    "candidate_id": "c1",
                    "owner_id": "u-a",
                    "key": "k",
                    "value": "v",
                    "confidence": 0.5,
                    "provenance": {},
                    "created_at": "2026-08-01T00:00:00Z",
                }
            ],
            "lifecycle": [
                {
                    "record_id": "lc1",
                    "owner_id": "u-a",
                    "action": "activate",
                    "memory_id": "m1",
                    "created_at": "2026-08-01T00:00:00Z",
                }
            ],
            "access_records": [
                # Envelope: owner_id at top level, record nested (real shape).
                {
                    "owner_id": "u-a",
                    "record": {
                        "record_id": "mar1",
                        "snapshot_id": "s1",
                        "purpose": "recall",
                        "user_authorized": True,
                    },
                }
            ],
            "grants": [{"grant_id": "g1", "owner_id": "u-a", "granting_scope": "subject:math"}],
            "mutation_receipts": [
                {
                    "operation_id": "op1",
                    "owner_id": "u-a",
                    "action": "create",
                    "resource_id": "m1",
                    "created_at": "2026-08-01T00:00:00Z",
                }
            ],
        },
    )

    report = migrate_sources(source_names=("memory_v2",), path_service=service)
    assert report.reconciled is True
    by_table = {r.target_table: r for r in report.results}
    # Six sections migrated, all attributed to u-a, none deferred.
    assert all(r.deferred_record_count == 0 for r in report.results)
    assert by_table["memories"].owner_ids == ("u-a",)
    assert by_table["memory_candidates"].inserted_record_count == 1
    assert by_table["memory_lifecycle"].inserted_record_count == 1
    assert by_table["memory_access_records"].inserted_record_count == 1
    assert by_table["memory_grants"].inserted_record_count == 1
    assert by_table["memory_mutation_receipts"].inserted_record_count == 1

    db = service.get_traittutor_database_path()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    # access_records envelope: owner read from envelope, record preserved verbatim.
    ar = con.execute("SELECT owner_id, payload_json FROM memory_access_records").fetchone()
    assert ar["owner_id"] == "u-a"
    assert json.loads(ar["payload_json"])["record"]["record_id"] == "mar1"
    # Active fact preserved verbatim (no silent rewrite).
    item = con.execute("SELECT payload_json FROM memories WHERE record_id=?", ("m1",)).fetchone()
    assert json.loads(item["payload_json"])["value"] == "alg"
    con.close()


def test_migrate_memory_index_uses_path_scope_and_preserves_ledger(tmp_path: Path) -> None:
    """The index file is owner-less; rows take the workspace owner by path scope
    and the generation/invalidation ledger travels verbatim — fail-closed index
    fencing survives the migration."""
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/memory-index-v1.json",
        {
            "schema_version": 2,
            "states": [{"generation": 3, "built_at": "2026-08-01T00:00:00Z"}],
            "indexes": [{"memory_id": "m1", "generation": 3, "token": "t"}],
            "invalidations": [{"generation": 2, "reason": "superseded"}],
        },
    )

    report = migrate_sources(
        source_names=("memory_index",), path_service=service, owner_id="local-admin"
    )
    assert report.reconciled is True
    entries = next(r for r in report.results if r.target_table == "memory_index_entries")
    assert entries.deferred_record_count == 0
    assert entries.owner_ids == ("local-admin",)  # path scope

    db = service.get_traittutor_database_path()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    inv = con.execute(
        "SELECT payload_json, source_section FROM memory_index_invalidations"
    ).fetchone()
    con.close()
    assert json.loads(inv["payload_json"])["generation"] == 2
    assert inv["source_section"].endswith("#path_scoped")


def test_migrate_research_workspaces_preserves_owner(tmp_path: Path) -> None:
    """Research workspaces carry owner_id and migrate verbatim."""
    service = _service(tmp_path)
    base = service.user_data_dir
    _write_json(
        base / "workspace/traittutor/research_workspaces.json",
        {
            "schema_version": 1,
            "workspaces": [{"workspace_id": "rw1", "owner_id": "u-a", "query": "evidence review"}],
        },
    )

    report = migrate_sources(source_names=("research_workspaces",), path_service=service)
    ws = next(r for r in report.results if r.target_table == "research_workspaces")
    assert ws.inserted_record_count == 1
    assert ws.owner_ids == ("u-a",)
    assert report.reconciled is True
