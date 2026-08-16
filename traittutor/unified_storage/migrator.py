"""Idempotent, reconcilable migration of legacy sources into the unified DB.

Phase 2 engine.  For every source in scope it reads the source records, resolves
each record's owner via the *same* path the dry-run uses (so the migration never
attributes ownership differently from the plan), skips never-produced residue,
and upserts each record verbatim into its target table inside one transaction.
A ``storage_migration_runs`` row records each source migration for replay
safety.

Iron-law guarantees (PRD §5.2 / §8 / plan §6):

* **Idempotent** — primary-key upserts; re-running a completed source is a
  no-op and never duplicates a row.  The same source sha256 + schema version
  already marked ``completed`` short-circuits the source entirely.
* **Reversible** — the source record is stored verbatim as ``payload_json``;
  nothing is dropped, inferred, or rewritten.  Owner ids are preserved exactly
  (no silent merge of fragmented ids — that is a separate human decision).
* **No guessing owners** — records whose owner cannot be resolved are
  *deferred* (left in their read-only source file), never force-attributed.
* **Per-source atomicity** — each source migrates in one transaction; a failure
  rolls that source back and is reported, leaving prior sources intact and the
  run resumable.
* **Reconciliation gate** — source counts are cross-checked against table
  counts; a mismatch marks the run unreconciled.

It writes only to the unified ``traittutor.sqlite3``; legacy source files are
opened read-only and never reverse-overwritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sqlite3

from traittutor.services.path_service import PathService, get_path_service
from traittutor.unified_storage.mapping import (
    FILE_SECTION,
    LIST_SECTION,
    SOURCE_SPECS,
    OwnerResolver,
    SectionSpec,
    SourceSpec,
)
from traittutor.unified_storage.models import OwnerStrategy
from traittutor.unified_storage.schema import create_business_tables
from traittutor.unified_storage.store import UnifiedStore, _utc_now, initialize_database

from .inventory import (
    _count_duplicates,
    _is_residue,
    _load_json,
    _resolve_single_owner,
    _section_record_ids,
    _sha256_file,
    build_generation_task_resolver,
    build_section_owner_map,
    resolve_section_owner,
)

# Source names migrated in Phase 2: the low-coupling business records (plan §9
# task 5 — routing, pages, tutor, runs, conversations) plus the generation run
# data they join against and the chat-history SQLite store.  Learning evidence,
# memory and the knowledge graph belong to Phase 3/4 and are excluded here.
PHASE_2_SOURCE_NAMES: tuple[str, ...] = (
    "capability_decisions",
    "page_schemas",
    "tutor_personas",
    "orchestrator_runs",
    "conversations",
    "generation_tasks",
    "generation_results",
    "chat_history",
)

# Source names migrated in Phase 3: the learning domain and its evidence chain
# (plan §9 task 7).  learning_packs is the pack aggregate (one row per pack,
# verbatim — normalization is a Phase 5 read-adapter concern, not a lossy
# migration step).  learner_events carries the immutable event log plus its
# derived apply/queue ledger; its event_id PK makes replay idempotent.
# knowledge_graph is an owner-less SQLite source attributed by workspace path
# scope (see :func:`_migrate_sqlite_source`).  Memory is Phase 4.
PHASE_3_SOURCE_NAMES: tuple[str, ...] = (
    "learning_packs",
    "learner_events",
    "misconceptions",
    "knowledge_graph",
)

# Source names migrated in Phase 4: memory + research workspaces (plan §9 task 8).
# memory_v2 is the canonical memory store (active facts, candidates, lifecycle,
# access records, grants, mutation receipts); memory_index is the per-owner
# index-generation/invalidation ledger (PATH_SCOPE — preserved verbatim so
# fail-closed generation fencing survives the migration).  research_workspaces
# carries research-project state.  The runtime delete→state→generation→rebuild
# ordering (plan §5 Phase 4 item 3) is a unified-Store write-adapter concern for
# Phase 5; the migration preserves every ledger row needed to enforce it.
PHASE_4_SOURCE_NAMES: tuple[str, ...] = (
    "memory_v2",
    "memory_index",
    "research_workspaces",
)

# Legacy chat_history SQLite tables → unified target tables.  chat_history has no
# per-table mapping in SOURCE_SPECS (only a sqlite_tables list); this fixes the
# real target name for each legacy table.
_CHAT_HISTORY_TARGETS: dict[str, str] = {
    "sessions": "chat_sessions",
    "messages": "chat_messages",
    "turns": "chat_turns",
    "turn_events": "chat_turn_events",
    "notebook_items": "chat_notebook_items",
    "notebook_sections": "chat_notebook_sections",
    "server_quiz_items": "chat_server_quiz_items",
}

# Per-source overrides of the legacy table name → unified target table.  Sources
# not listed here (e.g. knowledge_graph) migrate each legacy table to a target
# of the same name — their SOURCE_SPECS names are already the desired targets.
_SQLITE_TARGET_OVERRIDES: dict[str, dict[str, str]] = {
    "chat_history": _CHAT_HISTORY_TARGETS,
}

# Natural single-column PK for owner-less graph tables that have one, so the row
# keeps a stable, human-readable id instead of a synthetic content hash.  Tables
# without an entry (graph_modules / graph_concepts / graph_edges / graph_evidence)
# have composite or no natural PK and use the deterministic content-hash id.
_GRAPH_PK_COLUMNS: dict[str, str] = {
    "graph_subjects": "subject_id",
    "graph_versions": "version_id",
}


def _detect_pk_column(src: sqlite3.Connection, table: str) -> str | None:
    """Detect a single-column primary key for a legacy SQLite table.

    Returns the column name when the table has exactly one PK column; otherwise
    ``None`` (composite or no PK → use deterministic content-hash id).  This is
    how chat_history tables keep their ``id`` column as the unified record id
    without hard-coding every legacy schema.
    """
    pk_cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})").fetchall() if r[5]]
    return pk_cols[0] if len(pk_cols) == 1 else None


# Owner written when a chat_history row's owner column is NULL but the database
# resolves to a single owner at the source level (the common local-admin case).
# Reported explicitly so it is never mistaken for a real per-row owner.
_SCOPE_FALLBACK_OWNER = "__scope_fallback__"

# Suffix tagged onto ``source_section`` for owner-less (path-scoped) sources, so
# a downstream reader can tell the owner was inferred from the workspace path
# rather than attested by a row-level column.  Shared with the reconciler so the
# two never disagree on which rows belong to a path-scoped source.
_PATH_SCOPED_TAG = "#path_scoped"


def path_scoped_suffix(spec: SourceSpec) -> str:
    """The ``source_section`` suffix used for owner-less (PATH_SCOPE) sources.

    Empty for sources whose owner is row-attested; ``#path_scoped`` otherwise.
    Centralised so the migrator and the reconciler tag (and query) the same rows.
    """
    return _PATH_SCOPED_TAG if spec.owner.strategy is OwnerStrategy.PATH_SCOPE else ""


@dataclass(frozen=True)
class TableMigrationResult:
    """Outcome of migrating one source section/table into one target table."""

    target_table: str
    source_name: str
    source_ref: str
    source_record_count: int  # real records read (residue excluded)
    migrated_record_count: int  # rows now in the table from this source
    inserted_record_count: int  # rows newly inserted this run
    deferred_record_count: int  # unresolved-owner records skipped
    residue_record_count: int  # never-produced files/rows skipped
    # Source records that shared an id with another record in the same section;
    # idempotent PK upsert collapses them to one row, so reconciliation subtracts
    # this from the expected count (the baseline reports each as a conflict).
    duplicate_id_count: int = 0
    owner_ids: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True)
class MigrationReport:
    """Structured outcome of one migration run over a set of sources."""

    started_at: str
    completed_at: str
    owner_scope: str
    database_path: str
    results: tuple[TableMigrationResult, ...] = ()
    migration_ids: tuple[str, ...] = ()
    deferred_results: tuple[TableMigrationResult, ...] = ()
    integrity_check: str = "unknown"
    reconciled: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_inserted(self) -> int:
        return sum(r.inserted_record_count for r in self.results)

    @property
    def total_deferred(self) -> int:
        return sum(r.deferred_record_count for r in self.results)


def _record_id(record: dict, id_field: str | None) -> str:
    """Return a stable row id without collapsing explicit record revisions.

    Natural ids remain verbatim for ordinary mutable records so migration,
    reconciliation and operator tooling can address them directly.  Records
    that explicitly declare a ``revision``/``version`` are append-only
    snapshots, so their natural id is qualified by the version coordinates.
    ``lease_revision`` participates when present because research heartbeats
    are independently versioned within one public lifecycle revision.
    """
    if id_field:
        value = record.get(id_field)
        if isinstance(value, str) and value:
            base_id = value
        elif value is not None:
            base_id = str(value)
        else:
            base_id = ""
        if base_id:
            coordinates = {
                key: record[key]
                for key in ("revision", "version", "lease_revision")
                if record.get(key) is not None
            }
            if not coordinates:
                return base_id
            digest = hashlib.sha256(
                json.dumps(coordinates, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            return f"{base_id}@version:{digest[:16]}"
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()
    return f"synthetic:{digest[:24]}"


def _source_sha(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _already_completed(
    connection: sqlite3.Connection,
    *,
    owner_id: str,
    source_path: str,
    source_sha256: str,
) -> bool:
    row = connection.execute(
        "SELECT status FROM storage_migration_runs "
        "WHERE owner_id=? AND source_path=? AND source_sha256=? AND target_schema_version=?",
        (owner_id, source_path, source_sha256, 1),
    ).fetchone()
    return bool(row and row["status"] == "completed")


def _record_migration_run(
    connection: sqlite3.Connection,
    *,
    migration_id: str,
    owner_id: str,
    source_kind: str,
    source_path: str,
    source_sha256: str,
    status: str,
    started_at: str,
    details: dict,
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO storage_migration_runs("
        "migration_id, owner_id, source_kind, source_path, source_sha256, "
        "target_schema_version, status, started_at, completed_at, details_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            migration_id,
            owner_id,
            source_kind,
            source_path,
            source_sha256,
            1,
            status,
            started_at,
            _utc_now(),
            json.dumps(details, sort_keys=True),
        ),
    )


def _count_table(connection: sqlite3.Connection, table: str, source_section: str) -> int:
    return connection.execute(
        f"SELECT count(*) FROM {table} WHERE source_section=?",
        (source_section,),
    ).fetchone()[0]


def _upsert_record(
    connection: sqlite3.Connection,
    *,
    table: str,
    record_id: str,
    owner_id: str,
    source_section: str,
    payload: dict,
    source_sha: str,
    migrated_at: str,
) -> int:
    """Insert one row unless its pk already exists; return 1 if newly inserted."""
    cur = connection.execute(
        f"INSERT OR IGNORE INTO {table} ("  # noqa: S608 - table is identifier-validated upstream
        "record_id, owner_id, source_section, payload_json, source_sha256, migrated_at) "
        "VALUES (?,?,?,?,?,?)",
        (
            record_id,
            owner_id,
            source_section,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            source_sha,
            migrated_at,
        ),
    )
    return cur.rowcount


def _iter_section_payloads(
    spec: SourceSpec, payload: dict | list
) -> list[tuple[SectionSpec, list[dict]]]:
    """Yield (section_spec, records) pairs for a json_sectioned / json_list file.

    For ``json_list`` the whole file is one array mapped to the ``LIST_SECTION``
    spec; for ``json_sectioned`` each top-level key is one section.
    """
    pairs: list[tuple[SectionSpec, list[dict]]] = []
    if spec.kind == "json_list":
        for section_spec in spec.sections:
            if section_spec.section == LIST_SECTION:
                raw: list = payload if isinstance(payload, list) else []
                pairs.append((section_spec, list(raw)))
        return pairs
    if not isinstance(payload, dict):
        return pairs
    for section_spec in spec.sections:
        value = payload.get(section_spec.section)
        records = value if isinstance(value, list) else []
        pairs.append((section_spec, list(records)))
    return pairs


def _migrate_json_sections(
    spec: SourceSpec,
    path: Path,
    resolver: OwnerResolver,
    owner_scope: str,
    connection: sqlite3.Connection,
    migrated_at: str,
    owner_id: str,
) -> list[TableMigrationResult]:
    """Migrate a json_sectioned / json_list source file into its target tables."""
    payload, parse_error = _load_json(path)
    if parse_error is not None or not isinstance(payload, (dict, list)):
        # A malformed source is reported (its baseline anomaly stands); we do
        # not half-migrate it.  An empty results list leaves reconciliation to
        # flag the gap via the source's missing migration_run.
        return []

    section_payloads = _iter_section_payloads(spec, payload)
    # Build the intra-source owner-inheritance map once, so sections declaring
    # owner_join_field inherit the owner of the record they reference (e.g. a
    # derived-ledger row inherits its event's owner).  Same path the inventory
    # uses, so the migration never attributes ownership differently from the
    # dry-run.
    owner_maps = build_section_owner_map(spec, section_payloads, resolver, owner_scope)

    # A PATH_SCOPE source (memory_index) is owner-less by design; its owner is
    # the workspace it lives in.  Tag its source_section #path_scoped so the
    # attribution is explicit and auditable — the same tag the sqlite path
    # (knowledge_graph) applies.  Index evidence is structural and is NEVER
    # treated as BKT strong evidence (PRD §5.2, iron law #2).
    suffix = path_scoped_suffix(spec)

    results: list[TableMigrationResult] = []
    for section_spec, records in section_payloads:
        target = section_spec.target_table
        create_business_tables(connection, [target])
        section = f"{spec.name}/{section_spec.section}{suffix}"
        inserted = 0
        deferred = 0
        owners: set[str] = set()
        real = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            real += 1
            owner = resolve_section_owner(
                spec, section_spec, record, resolver, owner_scope, owner_maps, owner_id
            )
            if owner is None:
                deferred += 1
                continue
            owners.add(owner)
            inserted += _upsert_record(
                connection,
                table=target,
                record_id=_record_id(record, section_spec.id_field),
                owner_id=owner,
                source_section=section,
                payload=record,
                source_sha=_source_sha(record),
                migrated_at=migrated_at,
            )
        migrated = _count_table(connection, target, section)
        duplicates = _count_duplicates(_section_record_ids(records, section_spec.id_field))
        results.append(
            TableMigrationResult(
                target_table=target,
                source_name=spec.name,
                source_ref=f"{spec.relative_path}::{section_spec.section}",
                source_record_count=real,
                migrated_record_count=migrated,
                inserted_record_count=inserted,
                deferred_record_count=deferred,
                residue_record_count=0,
                duplicate_id_count=duplicates,
                owner_ids=tuple(sorted(owners)),
                note=(
                    "owner inferred from workspace path, not row-attested; "
                    "index evidence is structural, not BKT strong evidence"
                    if suffix
                    else None
                ),
            )
        )
    return results


def _migrate_per_task_files(
    spec: SourceSpec,
    base: Path,
    resolver: OwnerResolver,
    owner_scope: str,
    connection: sqlite3.Connection,
    migrated_at: str,
    owner_id: str,
) -> list[TableMigrationResult]:
    """Migrate a json_per_task_file source (one record per file) into its table."""
    files = sorted(base.glob(spec.relative_path))
    section_spec = spec.sections[0] if spec.sections else None
    target = section_spec.target_table if section_spec else None
    id_field = section_spec.id_field if section_spec else None
    if target is None:
        return []
    create_business_tables(connection, [target])

    inserted = 0
    deferred = 0
    residue = 0
    real = 0
    owners: set[str] = set()
    real_ids: list[str | None] = []
    for file_path in files:
        payload, parse_error = _load_json(file_path)
        if parse_error is not None or not isinstance(payload, dict):
            deferred += 1  # unreadable file: deferred, not dropped
            continue
        if _is_residue(payload, spec.residue_if_empty_fields):
            residue += 1
            continue
        real += 1
        if id_field is not None:
            value = payload.get(id_field)
            real_ids.append(str(value) if value is not None else None)
        owner = _resolve_single_owner(spec, payload, resolver, owner_scope, owner_id)
        if owner is None:
            deferred += 1
            continue
        owners.add(owner)
        inserted += _upsert_record(
            connection,
            table=target,
            record_id=_record_id(payload, id_field),
            owner_id=owner,
            source_section=f"{spec.name}/{FILE_SECTION}",
            payload=payload,
            source_sha=_source_sha(payload),
            migrated_at=migrated_at,
        )
    migrated = _count_table(connection, target, f"{spec.name}/{FILE_SECTION}")
    duplicates = _count_duplicates(real_ids)
    return [
        TableMigrationResult(
            target_table=target,
            source_name=spec.name,
            source_ref=spec.relative_path,
            source_record_count=real,
            migrated_record_count=migrated,
            inserted_record_count=inserted,
            deferred_record_count=deferred,
            residue_record_count=residue,
            duplicate_id_count=duplicates,
            owner_ids=tuple(sorted(owners)),
        )
    ]


def _migrate_sqlite_source(
    spec: SourceSpec,
    path: Path,
    connection: sqlite3.Connection,
    migrated_at: str,
    owner_id: str,
) -> list[TableMigrationResult]:
    """Migrate a legacy SQLite source row-by-row into its target tables.

    Two shapes:

    * **Owner-attested** (chat_history): rows carry an owner column; each row's
      owner is read from it.  Rows with a NULL owner fall back to the single
      source owner only when the source resolves to exactly one (the common
      local-admin case); otherwise they are deferred (never guessed).
    * **Owner-less / path-scoped** (knowledge_graph): the source has no owner
      column at all, so every row takes the workspace ``owner_id`` by path scope.
      Its ``source_section`` is tagged ``#path_scoped`` so the attribution is
      explicit and auditable, and the result note records that graph evidence is
      structural — it is never treated as BKT strong evidence (PRD §5.2, iron
      law #2).  No KC attribution is backfilled from the graph onto events.
    """
    if not path.is_file():
        return []
    results: list[TableMigrationResult] = []
    owner_col = spec.sqlite_owner_column
    # PATH_SCOPE sqlite sources (the knowledge graph) have no owner column by
    # design; every row takes the workspace owner by path scope.  A genuinely
    # UNRESOLVED sqlite source would fall through to the owner-attested branch,
    # read a None owner column, and defer every row (never force-attribute).
    ownerless = spec.owner.strategy is OwnerStrategy.PATH_SCOPE
    suffix = path_scoped_suffix(spec)
    overrides = _SQLITE_TARGET_OVERRIDES.get(spec.name, {})
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        distinct_owners: list[str] = []
        if spec.sqlite_owner_table and owner_col:
            distinct_owners = [
                r[0]
                for r in src.execute(
                    f"SELECT DISTINCT {owner_col} FROM {spec.sqlite_owner_table} "
                    f"WHERE {owner_col} IS NOT NULL"
                ).fetchall()
                if isinstance(r[0], str)
            ]
        single_source_owner = distinct_owners[0] if len(distinct_owners) == 1 else None

        for legacy_table in spec.sqlite_tables:
            target = overrides.get(legacy_table, legacy_table)
            exists = src.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy_table,)
            ).fetchone()
            if not exists:
                continue
            create_business_tables(connection, [target])
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({legacy_table})").fetchall()]
            pk_col = _GRAPH_PK_COLUMNS.get(legacy_table) or _detect_pk_column(src, legacy_table)
            section = f"{spec.name}/{legacy_table}{suffix}"
            inserted = 0
            deferred = 0
            owners: set[str] = set()
            real = 0
            real_ids: list[str | None] = []
            for row in src.execute(f"SELECT * FROM {legacy_table}").fetchall():
                record = {cols[i]: row[i] for i in range(len(cols))}
                real += 1
                if ownerless:
                    owner = owner_id
                else:
                    raw_owner = record.get(owner_col) if owner_col else None
                    if isinstance(raw_owner, str) and raw_owner.strip():
                        owner = raw_owner.strip()
                    elif single_source_owner is not None:
                        owner = single_source_owner
                        owners.add(_SCOPE_FALLBACK_OWNER)
                    else:
                        deferred += 1
                        continue
                owners.add(owner)
                rid = (
                    _record_id(record, pk_col)
                    if pk_col and pk_col in record
                    else _record_id(record, None)
                )
                real_ids.append(rid)
                inserted += _upsert_record(
                    connection,
                    table=target,
                    record_id=rid,
                    owner_id=owner,
                    source_section=section,
                    payload=record,
                    source_sha=_source_sha(record),
                    migrated_at=migrated_at,
                )
            migrated = _count_table(connection, target, section)
            duplicates = _count_duplicates(real_ids)
            note: str | None
            if ownerless:
                note = (
                    "owner inferred from workspace path, not row-attested; "
                    "graph evidence is structural, not BKT strong evidence"
                )
            elif _SCOPE_FALLBACK_OWNER in owners:
                note = f"{_SCOPE_FALLBACK_OWNER} used for NULL-owner rows"
            else:
                note = None
            results.append(
                TableMigrationResult(
                    target_table=target,
                    source_name=spec.name,
                    source_ref=f"{spec.relative_path}::{legacy_table}",
                    source_record_count=real,
                    migrated_record_count=migrated,
                    inserted_record_count=inserted,
                    deferred_record_count=deferred,
                    residue_record_count=0,
                    duplicate_id_count=duplicates,
                    owner_ids=tuple(sorted(owners)),
                    note=note,
                )
            )
    finally:
        src.close()
    return results


def migrate_sources(
    *,
    source_names: tuple[str, ...] = PHASE_2_SOURCE_NAMES,
    path_service: PathService | None = None,
    owner_scope: str = "default",
    owner_id: str = "local-admin",
) -> MigrationReport:
    """Migrate ``source_names`` into the unified DB and return a report.

    ``owner_id`` is the server-resolved scope owner recorded against each
    ``storage_migration_runs`` row (the owner of the workspace being migrated,
    not the per-record owner — per-record owners are read from the records and
    preserved verbatim).
    """
    service = path_service or get_path_service()
    base = service.user_data_dir
    db_path = service.get_traittutor_database_path()
    initialize_database(db_path)
    started_at = _utc_now()
    migrated_at = started_at
    resolver, _ = build_generation_task_resolver(base)

    specs_by_name = {s.name: s for s in SOURCE_SPECS}
    selected = [specs_by_name[n] for n in source_names if n in specs_by_name]

    all_results: list[TableMigrationResult] = []
    deferred_results: list[TableMigrationResult] = []
    migration_ids: list[str] = []
    warnings: list[str] = []

    store = UnifiedStore(owner_id, path_service=service)
    for spec in selected:
        with store.transaction() as connection:
            sha = _source_sha_for(spec, base)
            if sha and _already_completed(
                connection,
                owner_id=owner_id,
                source_path=spec.relative_path,
                source_sha256=sha,
            ):
                warnings.append(f"{spec.name}: already completed for this sha, skipped")
                continue

            if spec.kind in ("json_sectioned", "json_list"):
                path = base / spec.relative_path
                results = _migrate_json_sections(
                    spec, path, resolver, owner_scope, connection, migrated_at, owner_id
                )
            elif spec.kind == "json_per_task_file":
                results = _migrate_per_task_files(
                    spec, base, resolver, owner_scope, connection, migrated_at, owner_id
                )
            elif spec.kind == "sqlite":
                path = base / spec.relative_path
                results = _migrate_sqlite_source(spec, path, connection, migrated_at, owner_id)
            else:  # pragma: no cover - exhaustive over SourceKind
                continue

            all_results.extend(results)
            deferred_results.extend(r for r in results if r.deferred_record_count)
            migration_id = f"{spec.name}:{sha[:16]}" if sha else f"{spec.name}:empty"
            _record_migration_run(
                connection,
                migration_id=migration_id,
                owner_id=owner_id,
                source_kind=spec.kind,
                source_path=spec.relative_path,
                source_sha256=sha,
                status="completed",
                started_at=started_at,
                details={
                    "tables": ",".join(sorted({r.target_table for r in results})),
                    "inserted": sum(r.inserted_record_count for r in results),
                    "deferred": sum(r.deferred_record_count for r in results),
                    "residue": sum(r.residue_record_count for r in results),
                },
            )
            migration_ids.append(migration_id)

    reconciled = all(
        r.migrated_record_count
        == r.source_record_count - r.deferred_record_count - r.duplicate_id_count
        for r in all_results
    )
    integrity = store.integrity_check()

    return MigrationReport(
        started_at=started_at,
        completed_at=_utc_now(),
        owner_scope=owner_scope,
        database_path=str(db_path),
        results=tuple(all_results),
        migration_ids=tuple(migration_ids),
        deferred_results=tuple(deferred_results),
        integrity_check=integrity,
        reconciled=reconciled,
        warnings=tuple(warnings),
    )


def _source_sha_for(spec: SourceSpec, base: Path) -> str:
    """Stable sha for a source: file hash, or aggregate hash over globbed files."""
    if spec.kind == "json_per_task_file":
        files = sorted(base.glob(spec.relative_path))
        if not files:
            return ""
        return hashlib.sha256(
            "\n".join(f"{p.name}:{_sha256_file(p)}" for p in files).encode("utf-8")
        ).hexdigest()
    path = base / spec.relative_path
    return _sha256_file(path) if path.is_file() else ""
