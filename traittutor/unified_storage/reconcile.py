"""Read-only reconciliation of legacy sources against the unified DB (Phase 5).

Cross-checks that every migrated source is faithfully represented in the unified
database — per-section record counts, primary-key coverage, owner fidelity and
verbatim payload — and that no source file was reverse-overwritten during the
migration (its sha still matches the pre-migration archive).

Read-only against both the sources (SQLite opened ``mode=ro``) and the unified
DB.  Reports mismatches; **never repairs them** — repair is a human decision
(plan §6 forbids half-migrations and silent rewrites).  This is the "final hash
and count verification" step of plan §5 Phase 5 item 3.

The reconciliation reuses the *same* record-reading, owner-resolution and
record-id paths as the migrator, so a row is counted as migrated only when its
primary key, owner and payload sha all agree with the source it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

from traittutor.services.path_service import PathService, get_path_service

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
from .mapping import (
    FILE_SECTION,
    SOURCE_SPECS,
    OwnerResolver,
    SourceSpec,
)
from .migrator import (
    _GRAPH_PK_COLUMNS,
    _SQLITE_TARGET_OVERRIDES,
    _detect_pk_column,
    _iter_section_payloads,
    _record_id,
    _source_sha,
    path_scoped_suffix,
)
from .models import OwnerStrategy
from .store import UnifiedStore, _utc_now, initialize_database

# Every source migrated by Phase 2/3/4 is reconciled by default.  A source whose
# migration never produced DB rows reconciles only if it also has zero source
# records — otherwise it surfaces as missing-in-db.
DEFAULT_SOURCE_NAMES: tuple[str, ...] = (
    # Phase 2
    "capability_decisions",
    "page_schemas",
    "tutor_personas",
    "orchestrator_runs",
    "conversations",
    "generation_tasks",
    "generation_results",
    "chat_history",
    # Phase 3
    "learning_packs",
    "learner_events",
    "misconceptions",
    "knowledge_graph",
    # Phase 4
    "memory_v2",
    "memory_index",
    "research_workspaces",
)


@dataclass(frozen=True)
class SectionReconciliation:
    """Outcome of reconciling one source section against its target table."""

    source_name: str
    target_table: str
    source_section: str  # the DB source_section tag (incl. #path_scoped)
    source_record_count: int  # real records read (residue excluded, dups kept)
    db_record_count: int  # rows in the target table for this source_section
    deferred_record_count: int  # source records with no resolvable owner
    duplicate_id_count: int  # source records sharing an id within the section
    migrated_record_count: int  # source records in DB with matching payload sha
    payload_mismatch_count: int  # source records in DB whose payload sha differs
    missing_in_db_count: int  # resolved-owner source records absent from DB
    extra_in_db_count: int  # DB rows whose record_id has no source record
    owner_ids: tuple[str, ...]
    note: str | None = None
    reconciled: bool = False


@dataclass(frozen=True)
class SourceIntactCheck:
    """Whether a source file on disk still matches its archived sha."""

    source_name: str
    relative_path: str
    intact: bool
    note: str | None = None


@dataclass(frozen=True)
class ReconciliationReport:
    """Structured outcome of reconciling a set of sources against the DB."""

    generated_at: str
    database_path: str
    archive_root: str | None
    sections: tuple[SectionReconciliation, ...]
    source_intact: tuple[SourceIntactCheck, ...]
    reconciled: bool
    integrity_check: str


def _db_rows_for(
    connection: sqlite3.Connection, table: str, source_section: str
) -> dict[str, dict[str, object]]:
    """Return ``{record_id: {owner_id, source_sha256}}`` for one table+section.

    Only rows whose ``source_section`` matches are read, so multi-tenant or
    multi-source tables are scoped to the section being reconciled.
    """
    try:
        cur = connection.execute(
            f"SELECT record_id, owner_id, source_sha256 FROM {table} "  # noqa: S608 - identifier-validated upstream
            "WHERE source_section=?",
            (source_section,),
        )
    except sqlite3.DatabaseError:
        # Table absent (source never migrated / target not created) → no rows.
        return {}
    return {
        str(row["record_id"]): {
            "owner_id": row["owner_id"],
            "source_sha256": row["source_sha256"],
        }
        for row in cur.fetchall()
    }


def _classify(
    *,
    record_id: str,
    owner: str | None,
    payload_sha: str,
    db_rows: dict[str, dict[str, object]],
) -> tuple[str | None, str | None]:
    """Classify one source record against the DB rows for its section.

    Returns ``(status, observed_owner)`` where status is one of
    ``"deferred"`` / ``"missing"`` / ``"mismatch"`` / ``"migrated"``.
    """
    if owner is None:
        return "deferred", None
    row = db_rows.get(record_id)
    if row is None:
        return "missing", None
    if row["source_sha256"] != payload_sha:
        return "mismatch", str(row["owner_id"])
    return "migrated", str(row["owner_id"])


def _is_section_reconciled(
    real: int,
    db_count: int,
    deferred: int,
    duplicates: int,
    mismatch: int,
    missing: int,
    extra: int,
) -> bool:
    """A section reconciles when the DB holds exactly its verbatim records.

    No payload drift (``mismatch == 0``), no resolved-owner record failed to
    land (``missing == 0``), no orphan rows (``extra == 0``), and the DB row
    count equals the migrated count.  Deferred (unresolvable-owner) and
    duplicate-id records are accounted for separately and do not by themselves
    fail reconciliation — this matches the migrator's own reconciliation gate.
    """
    return (
        mismatch == 0 and missing == 0 and extra == 0 and db_count == real - deferred - duplicates
    )


def _reconcile_json_sections(
    spec: SourceSpec,
    path: Path,
    resolver: OwnerResolver,
    owner_scope: str,
    connection: sqlite3.Connection,
    owner_id: str,
) -> list[SectionReconciliation]:
    payload, parse_error = _load_json(path)
    if parse_error is not None or not isinstance(payload, (dict, list)):
        return []
    section_payloads = _iter_section_payloads(spec, payload)
    owner_maps = build_section_owner_map(spec, section_payloads, resolver, owner_scope)
    suffix = path_scoped_suffix(spec)

    results: list[SectionReconciliation] = []
    for section_spec, records in section_payloads:
        target = section_spec.target_table
        section = f"{spec.name}/{section_spec.section}{suffix}"
        db_rows = _db_rows_for(connection, target, section)
        seen_ids: set[str] = set()
        migrated = mismatch = missing = deferred = real = 0
        owners: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                continue
            real += 1
            rid = _record_id(record, section_spec.id_field)
            if rid in seen_ids:
                continue  # intra-section duplicate; counted via duplicate_id_count
            seen_ids.add(rid)
            owner = resolve_section_owner(
                spec, section_spec, record, resolver, owner_scope, owner_maps, owner_id
            )
            status, _ = _classify(
                record_id=rid,
                owner=owner,
                payload_sha=_source_sha(record),
                db_rows=db_rows,
            )
            if status == "deferred":
                deferred += 1
            elif status == "missing":
                missing += 1
            elif status == "mismatch":
                mismatch += 1
            else:
                migrated += 1
                if owner:
                    owners.add(owner)
        duplicates = _count_duplicates(_section_record_ids(records, section_spec.id_field))
        extra = max(0, len(db_rows) - migrated - mismatch)
        results.append(
            SectionReconciliation(
                source_name=spec.name,
                target_table=target,
                source_section=section,
                source_record_count=real,
                db_record_count=len(db_rows),
                deferred_record_count=deferred,
                duplicate_id_count=duplicates,
                migrated_record_count=migrated,
                payload_mismatch_count=mismatch,
                missing_in_db_count=missing,
                extra_in_db_count=extra,
                owner_ids=tuple(sorted(owners)),
                note=(
                    "owner inferred from workspace path, not row-attested; "
                    "evidence is structural, not BKT strong evidence"
                    if suffix
                    else None
                ),
                reconciled=_is_section_reconciled(
                    real, len(db_rows), deferred, duplicates, mismatch, missing, extra
                ),
            )
        )
    return results


def _reconcile_per_task_files(
    spec: SourceSpec,
    base: Path,
    resolver: OwnerResolver,
    owner_scope: str,
    connection: sqlite3.Connection,
    owner_id: str,
) -> list[SectionReconciliation]:
    files = sorted(base.glob(spec.relative_path))
    section_spec = spec.sections[0] if spec.sections else None
    target = section_spec.target_table if section_spec else None
    id_field = section_spec.id_field if section_spec else None
    if target is None:
        return []
    section = f"{spec.name}/{FILE_SECTION}"
    db_rows = _db_rows_for(connection, target, section)

    seen_ids: set[str] = set()
    migrated = mismatch = missing = deferred = residue = real = 0
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
        rid = _record_id(payload, id_field)
        if id_field is not None:
            value = payload.get(id_field)
            real_ids.append(str(value) if value is not None else None)
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        owner = _resolve_single_owner(spec, payload, resolver, owner_scope, owner_id)
        status, _ = _classify(
            record_id=rid, owner=owner, payload_sha=_source_sha(payload), db_rows=db_rows
        )
        if status == "deferred":
            deferred += 1
        elif status == "missing":
            missing += 1
        elif status == "mismatch":
            mismatch += 1
        else:
            migrated += 1
            if owner:
                owners.add(owner)
    duplicates = _count_duplicates(real_ids)
    extra = max(0, len(db_rows) - migrated - mismatch)
    return [
        SectionReconciliation(
            source_name=spec.name,
            target_table=target,
            source_section=section,
            source_record_count=real,
            db_record_count=len(db_rows),
            deferred_record_count=deferred,
            duplicate_id_count=duplicates,
            migrated_record_count=migrated,
            payload_mismatch_count=mismatch,
            missing_in_db_count=missing,
            extra_in_db_count=extra,
            owner_ids=tuple(sorted(owners)),
            note=f"{residue} residue file(s) excluded (never produced)" if residue else None,
            reconciled=_is_section_reconciled(
                real, len(db_rows), deferred, duplicates, mismatch, missing, extra
            ),
        )
    ]


def _reconcile_sqlite_source(
    spec: SourceSpec,
    path: Path,
    connection: sqlite3.Connection,
    owner_id: str,
) -> list[SectionReconciliation]:
    if not path.is_file():
        return []
    ownerless = spec.owner.strategy is OwnerStrategy.PATH_SCOPE
    suffix = path_scoped_suffix(spec)
    overrides = _SQLITE_TARGET_OVERRIDES.get(spec.name, {})
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    results: list[SectionReconciliation] = []
    try:
        for legacy_table in spec.sqlite_tables:
            target = overrides.get(legacy_table, legacy_table)
            exists = src.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy_table,)
            ).fetchone()
            if not exists:
                continue
            section = f"{spec.name}/{legacy_table}{suffix}"
            db_rows = _db_rows_for(connection, target, section)
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({legacy_table})").fetchall()]
            pk_col = _GRAPH_PK_COLUMNS.get(legacy_table) or _detect_pk_column(src, legacy_table)
            seen_ids: set[str] = set()
            migrated = mismatch = missing = real = 0
            owners: set[str] = set()
            real_ids: list[str | None] = []
            for row in src.execute(f"SELECT * FROM {legacy_table}").fetchall():
                record = {cols[i]: row[i] for i in range(len(cols))}
                real += 1
                rid = (
                    _record_id(record, pk_col)
                    if pk_col and pk_col in record
                    else _record_id(record, None)
                )
                real_ids.append(rid)
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                owner = owner_id if ownerless else None
                row_db = db_rows.get(rid)
                if row_db is None:
                    missing += 1
                elif row_db["source_sha256"] != _source_sha(record):
                    mismatch += 1
                else:
                    migrated += 1
                    if owner:
                        owners.add(owner)
            duplicates = _count_duplicates(real_ids)
            extra = max(0, len(db_rows) - migrated - mismatch)
            results.append(
                SectionReconciliation(
                    source_name=spec.name,
                    target_table=target,
                    source_section=section,
                    source_record_count=real,
                    db_record_count=len(db_rows),
                    deferred_record_count=0,
                    duplicate_id_count=duplicates,
                    migrated_record_count=migrated,
                    payload_mismatch_count=mismatch,
                    missing_in_db_count=missing,
                    extra_in_db_count=extra,
                    owner_ids=tuple(sorted(owners)),
                    note=(
                        "owner inferred from workspace path, not row-attested; "
                        "graph evidence is structural, not BKT strong evidence"
                        if ownerless
                        else None
                    ),
                    reconciled=_is_section_reconciled(
                        real, len(db_rows), 0, duplicates, mismatch, missing, extra
                    ),
                )
            )
    finally:
        src.close()
    return results


def _current_source_sha(spec: SourceSpec, base: Path) -> str:
    if spec.kind == "json_per_task_file":
        files = sorted(base.glob(spec.relative_path))
        if not files:
            return ""
        import hashlib

        return hashlib.sha256(
            "\n".join(f"{p.name}:{_sha256_file(p)}" for p in files).encode("utf-8")
        ).hexdigest()
    path = base / spec.relative_path
    return _sha256_file(path) if path.is_file() else ""


def _archived_source_sha(spec: SourceSpec, archive_root: Path) -> str:
    """Read the archived sha for a source from the backup manifest."""
    import hashlib
    import json

    manifest_path = archive_root / "manifest.json"
    if not manifest_path.is_file():
        return ""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if spec.kind == "json_per_task_file":
        # Aggregate over the archived per-task files listed in the manifest.
        prefix = spec.relative_path[: -len("*.json")]
        pairs: list[str] = []
        for entry in manifest.get("files", []):
            rel = entry.get("relative_path", "")
            if rel.startswith(prefix):
                pairs.append(f"{Path(rel).name}:{entry.get('sha256', '')}")
        if not pairs:
            return ""
        return hashlib.sha256("\n".join(sorted(pairs)).encode("utf-8")).hexdigest()
    rel = spec.relative_path
    for entry in manifest.get("files", []):
        if entry.get("relative_path") == rel:
            return entry.get("sha256", "")
    return ""


def _check_sources_intact(
    base: Path, archive_root: Path | None, source_names: tuple[str, ...]
) -> list[SourceIntactCheck]:
    """Verify each source file on disk still matches its archived snapshot.

    Without an archive the check is skipped (``intact=True`` with a note) — the
    archive is created by Phase 5 item 3 and is the verifiable reference.  A
    source whose file is absent now but was absent at archive time is intact.

    SQLite sources are compared by size and a read-only integrity check rather
    than raw file sha, because the backup API used to create the archive folds
    WAL contents into a logically-consistent snapshot that can differ at the
    byte level from the live file (which may still carry un-checkpointed WAL
    pages).  A different size or failed integrity check still counts as a
    reverse-overwrite.
    """
    checks: list[SourceIntactCheck] = []
    by_name = {s.name: s for s in SOURCE_SPECS}
    for name in source_names:
        spec = by_name.get(name)
        if spec is None:  # pragma: no cover - defensive
            continue
        if archive_root is None:
            checks.append(SourceIntactCheck(name, spec.relative_path, True, "no archive provided"))
            continue
        if spec.kind == "sqlite":
            checks.append(_check_sqlite_intact(spec, base, archive_root))
            continue
        current = _current_source_sha(spec, base)
        archived = _archived_source_sha(spec, archive_root)
        intact = current == archived
        note = None if intact else "source sha differs from archive (reverse-overwrite?)"
        checks.append(SourceIntactCheck(name, spec.relative_path, intact, note))
    return checks


def _check_sqlite_intact(spec: SourceSpec, base: Path, archive_root: Path) -> SourceIntactCheck:
    """SQLite-specific intact check: size + read-only integrity."""
    current_path = base / spec.relative_path
    archived_path = archive_root / spec.relative_path
    if not current_path.is_file():
        return SourceIntactCheck(
            spec.name, spec.relative_path, archived_path.is_file(), "source missing"
        )
    if not archived_path.is_file():
        return SourceIntactCheck(spec.name, spec.relative_path, False, "archive missing")
    size_ok = current_path.stat().st_size == archived_path.stat().st_size
    current_ok = _sqlite_integrity_ok(current_path)
    archive_ok = _sqlite_integrity_ok(archived_path)
    intact = size_ok and current_ok and archive_ok
    if not intact:
        reasons = []
        if not size_ok:
            reasons.append("size differs")
        if not current_ok:
            reasons.append("source integrity failed")
        if not archive_ok:
            reasons.append("archive integrity failed")
        note = f"sqlite check failed ({', '.join(reasons)}); possible reverse-overwrite"
    else:
        note = "sqlite intact (size + integrity); backup-API snapshot may differ at byte level"
    return SourceIntactCheck(spec.name, spec.relative_path, intact, note)


def _sqlite_integrity_ok(path: Path) -> bool:
    """Run ``PRAGMA integrity_check`` read-only on a SQLite file."""
    if not path.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            result = con.execute("PRAGMA integrity_check").fetchone()
            return bool(result and result[0] == "ok")
        finally:
            con.close()
    except sqlite3.Error:
        return False


def reconcile_sources(
    *,
    source_names: tuple[str, ...] = DEFAULT_SOURCE_NAMES,
    path_service: PathService | None = None,
    owner_scope: str = "default",
    owner_id: str = "local-admin",
    archive_root: Path | None = None,
) -> ReconciliationReport:
    """Reconcile ``source_names`` against the unified DB (strictly read-only)."""
    service = path_service or get_path_service()
    base = service.user_data_dir
    db_path = service.get_traittutor_database_path()
    initialize_database(db_path)
    resolver, _ = build_generation_task_resolver(base)

    specs_by_name = {s.name: s for s in SOURCE_SPECS}
    selected = [specs_by_name[n] for n in source_names if n in specs_by_name]

    store = UnifiedStore(owner_id, path_service=service)
    sections: list[SectionReconciliation] = []
    # Open the unified DB read-only (``mode=ro``, as the inventory opens source
    # DBs) so the reconciler provably cannot mutate it.
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        for spec in selected:
            if spec.kind in ("json_sectioned", "json_list"):
                path = base / spec.relative_path
                sections.extend(
                    _reconcile_json_sections(
                        spec, path, resolver, owner_scope, connection, owner_id
                    )
                )
            elif spec.kind == "json_per_task_file":
                sections.extend(
                    _reconcile_per_task_files(
                        spec, base, resolver, owner_scope, connection, owner_id
                    )
                )
            elif spec.kind == "sqlite":
                path = base / spec.relative_path
                sections.extend(_reconcile_sqlite_source(spec, path, connection, owner_id))
    finally:
        connection.close()

    intact = _check_sources_intact(base, archive_root, source_names)
    # A run with no sections (all requested sources absent on disk) reconciles:
    # there is nothing to disagree.
    reconciled = all(s.reconciled for s in sections)

    return ReconciliationReport(
        generated_at=_utc_now(),
        database_path=str(db_path),
        archive_root=str(archive_root) if archive_root else None,
        sections=tuple(sections),
        source_intact=tuple(intact),
        reconciled=reconciled,
        integrity_check=store.integrity_check(),
    )
