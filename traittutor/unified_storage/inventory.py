"""Read-only source inventory that produces the baseline manifest (Phase 0).

This module walks the *actual* ``data/user`` tree — never the plan §2 table —
and, for every registered source, records SHA-256, byte size, per-section record
counts, duplicate ids, owner resolution and any anomaly.  Bad JSON, duplicate
ids, orphaned joins and unresolvable owners are surfaced as anomalies rather
than silently skipped (plan §5 Phase 0 completion gate).

It is strictly read-only: SQLite is opened with ``?mode=ro`` and nothing writes
to ``traittutor.sqlite3`` or inserts into ``storage_migration_runs``.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Sequence

from traittutor.services.path_service import PathService, get_path_service

from .mapping import (
    SOURCE_SPECS,
    OwnerResolver,
    SectionSpec,
    SourceSpec,
)
from .models import (
    Anomaly,
    AnomalySeverity,
    BaselineManifest,
    BaselineSummary,
    JsonSourceSection,
    JsonSourceSummary,
    OwnerResolution,
    OwnerStrategy,
    PerTaskFileCollection,
    SqliteSourceSummary,
    SqliteTableSummary,
)

_CHUNK = 65536


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> tuple[object | None, str | None]:
    """Return ``(payload, parse_error)``.  Never raises on malformed JSON."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"read error: {exc}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"malformed json: {exc}"


def _section_record_ids(records: Sequence[object], id_field: str | None) -> list[str | None]:
    if id_field is None:
        return [None] * len(records)
    ids: list[str | None] = []
    for record in records:
        value = record.get(id_field) if isinstance(record, dict) else None  # type: ignore[union-attr]
        ids.append(str(value) if value is not None else None)
    return ids


def _count_duplicates(ids: Sequence[str | None]) -> int:
    """Number of ids that appear more than once (each counted once)."""
    seen: dict[str, int] = {}
    for value in ids:
        if value is None:
            continue
        seen[value] = seen.get(value, 0) + 1
    return sum(1 for count in seen.values() if count > 1)


def _field_is_empty(value: object) -> bool:
    """True if a payload field is effectively absent / never produced.

    Covers ``None``, ``{}``, ``[]``, ``""`` and the ``{"items": []}`` shape a
    generation result takes when the run produced nothing.
    """
    if value is None or value == "" or value == {} or value == []:
        return True
    if isinstance(value, dict) and value.get("items") == [] and len(value) == 1:
        return True
    return False


def _is_residue(payload: dict, residue_if_empty_fields: tuple[str, ...]) -> bool:
    """True when every residue-marker field on the payload is empty.

    A residue file never produced real content; it is a Phase 5 deletion
    candidate, not business data to migrate or an owner to rebuild.
    """
    if not residue_if_empty_fields:
        return False
    return all(_field_is_empty(payload.get(f)) for f in residue_if_empty_fields)


def _resolve_single_owner(
    spec: SourceSpec,
    record: dict,
    resolver: OwnerResolver,
    owner_scope: str,
    path_scope_owner: str | None = None,
) -> str | None:
    """Resolve the owner id for one record, or ``None`` if unresolvable.

    Shared by the inventory (aggregate counts) and the migrator (per-row
    owner) so both paths attribute owners identically — the migration never
    decides ownership differently from the dry-run.

    ``path_scope_owner`` lets the migrator resolve PATH_SCOPE sources to the
    actual workspace owner id (the value written to the DB); the inventory
    omits it and falls back to the planning ``owner_scope`` label.
    """
    strategy = spec.owner.strategy
    field = spec.owner.field

    if strategy in (OwnerStrategy.DIRECT_OWNER_ID, OwnerStrategy.USER_ID_FIELD):
        assert field is not None  # noqa: S101 - invariant of these strategies
        value = record.get(field) if isinstance(record, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else None

    if strategy is OwnerStrategy.JOIN_GENERATION_TASK:
        assert field is not None  # noqa: S101
        join_value = record.get(field) if isinstance(record, dict) else None
        return resolver.resolve_join(join_value if isinstance(join_value, str) else None)

    if strategy is OwnerStrategy.PATH_SCOPE:
        # Migrator resolves PATH_SCOPE to the actual workspace owner id; the
        # inventory (no owner_id) passes the planning owner_scope label.
        return path_scope_owner if path_scope_owner else owner_scope

    # OwnerStrategy.UNRESOLVED
    return None


def build_section_owner_map(
    spec: SourceSpec,
    section_records: list[tuple[SectionSpec, list[dict]]],
    resolver: OwnerResolver,
    owner_scope: str,
) -> dict[str, dict[str, str]]:
    """Build intra-source owner-inheritance maps for ``owner_join_field``.

    Returns ``{field_name: {field_value: owner_id}}``.  A section whose records
    carry both an ``id_field`` and a directly-resolvable owner seeds the map for
    that id field; sections declaring ``owner_join_field`` then inherit an owner
    by looking up their join value in the matching map.

    This is how the learner-event derived ledger (amendments / derived_applied /
    derived_queue) inherits the owner of the event it references: the ``events``
    section seeds the ``event_id`` → owner map, and the derived sections join on
    ``event_id``.  Shared by the inventory and the migrator so both attribute
    ownership identically.
    """
    maps: dict[str, dict[str, str]] = {}
    for section_spec, records in section_records:
        if not section_spec.id_field:
            continue
        bucket = maps.setdefault(section_spec.id_field, {})
        for record in records:
            if not isinstance(record, dict):
                continue
            rid = record.get(section_spec.id_field)
            owner = _resolve_single_owner(spec, record, resolver, owner_scope)
            if owner and isinstance(rid, str) and rid:
                bucket[rid] = owner
    return maps


def resolve_section_owner(
    spec: SourceSpec,
    section_spec: SectionSpec,
    record: dict,
    resolver: OwnerResolver,
    owner_scope: str,
    owner_maps: dict[str, dict[str, str]],
    path_scope_owner: str | None = None,
) -> str | None:
    """Resolve one record's owner, falling back to intra-source inheritance.

    Tries the record's own owner field first; if that yields nothing and the
    section declares ``owner_join_field``, inherits the owner of the referenced
    record (e.g. the event a derived-ledger row points at).  Returns ``None``
    when neither path resolves — the caller defers the record rather than guess.

    ``path_scope_owner`` is forwarded to :func:`_resolve_single_owner` so the
    migrator resolves PATH_SCOPE sources to the workspace owner id.
    """
    owner = _resolve_single_owner(spec, record, resolver, owner_scope, path_scope_owner)
    if owner is not None:
        return owner
    join_field = section_spec.owner_join_field
    if join_field is None:
        return None
    join_value = record.get(join_field) if isinstance(record, dict) else None
    if isinstance(join_value, str) and join_value:
        return owner_maps.get(join_field, {}).get(join_value)
    return None


def _resolve_record_owners(
    spec: SourceSpec,
    records: list[dict],
    resolver: OwnerResolver,
    owner_scope: str,
    section_spec: SectionSpec | None = None,
    owner_maps: dict[str, dict[str, str]] | None = None,
) -> OwnerResolution:
    """Resolve owners for one flat record list according to ``spec.owner``.

    When ``section_spec`` and ``owner_maps`` are supplied, per-record resolution
    goes through :func:`resolve_section_owner` so a section declaring
    ``owner_join_field`` inherits the owner of the record it references (used by
    the learner-event derived ledger).  Otherwise it resolves each record's own
    owner field directly.
    """
    strategy = spec.owner.strategy
    owners: list[str] = []
    unresolved = 0
    for record in records:
        if section_spec is not None and owner_maps is not None:
            owner = resolve_section_owner(
                spec, section_spec, record, resolver, owner_scope, owner_maps
            )
        else:
            owner = _resolve_single_owner(spec, record, resolver, owner_scope)
        if owner is not None:
            owners.append(owner)
        else:
            unresolved += 1
    resolution = OwnerResolution(
        strategy=strategy,
        resolved_owner_ids=tuple(sorted(set(owners))),
        resolved_record_count=len(owners),
        unresolved_record_count=unresolved,
    )
    if strategy is OwnerStrategy.JOIN_GENERATION_TASK:
        return resolution.model_copy(
            update={"join_source": spec.owner.join_source, "join_key": spec.owner.field}
        )
    if strategy is OwnerStrategy.PATH_SCOPE:
        return resolution.model_copy(update={"note": "owner taken from workspace scope"})
    return resolution


def _analyze_sectioned_or_list(
    spec: SourceSpec, path: Path, resolver: OwnerResolver, owner_scope: str
) -> tuple[JsonSourceSummary, list[Anomaly]]:
    anomalies: list[Anomaly] = []
    exists = path.is_file()
    if not exists:
        summary = JsonSourceSummary(
            source_name=spec.name,
            relative_path=spec.relative_path,
            exists=False,
            byte_size=0,
            sha256="",
            mtime="",
            owner_resolution=OwnerResolution(strategy=spec.owner.strategy),
        )
        anomalies.append(
            Anomaly(
                code="missing_expected_source",
                severity=AnomalySeverity.WARNING,
                source_ref=spec.relative_path,
                message=f"expected source {spec.name!r} is absent on disk",
            )
        )
        return summary, anomalies

    payload, parse_error = _load_json(path)
    byte_size = path.stat().st_size
    sha256 = _sha256_file(path)
    mtime = _iso_mtime(path)

    if parse_error is not None or payload is None:
        summary = JsonSourceSummary(
            source_name=spec.name,
            relative_path=spec.relative_path,
            exists=True,
            byte_size=byte_size,
            sha256=sha256,
            mtime=mtime,
            owner_resolution=OwnerResolution(strategy=spec.owner.strategy),
            parse_error=parse_error,
        )
        anomalies.append(
            Anomaly(
                code="malformed_json",
                severity=AnomalySeverity.ERROR,
                source_ref=spec.relative_path,
                message=f"source {spec.name!r} could not be parsed: {parse_error}",
            )
        )
        return summary, anomalies

    # json_list: payload is the array of records directly.
    if spec.kind == "json_list":
        if not isinstance(payload, list):
            anomalies.append(
                Anomaly(
                    code="malformed_json",
                    severity=AnomalySeverity.ERROR,
                    source_ref=spec.relative_path,
                    message=f"source {spec.name!r} expected a JSON array",
                )
            )
            records: list[dict] = []
        else:
            records = [item for item in payload if isinstance(item, dict)]
        section_specs = spec.sections  # the single LIST_SECTION row
        payload_dict: dict[str, object] = {}
    else:
        # json_sectioned: payload must be a dict keyed by section name.
        if not isinstance(payload, dict):
            anomalies.append(
                Anomaly(
                    code="malformed_json",
                    severity=AnomalySeverity.ERROR,
                    source_ref=spec.relative_path,
                    message=f"source {spec.name!r} expected a JSON object",
                )
            )
            records = []
            section_specs = ()
            payload_dict = {}
        else:
            payload_dict = payload
            section_specs = spec.sections

    schema_version = payload.get("schema_version") if isinstance(payload, dict) else None

    section_summaries: list[JsonSourceSection] = []

    # First pass: collect each section's records (routing both kinds through an
    # ``object``-typed holder so sectioned list[Any] and json_list list[dict]
    # unify without tripping list invariance).
    collected: list[tuple[SectionSpec, list[dict], list[object]]] = []
    for section_spec in section_specs:
        raw_section: object = (
            records if spec.kind == "json_list" else payload_dict.get(section_spec.section)
        )
        section_records = raw_section if isinstance(raw_section, list) else []
        dict_records = [r for r in section_records if isinstance(r, dict)]
        collected.append((section_spec, dict_records, section_records))

    # Build the intra-source owner-inheritance map BEFORE resolving any section,
    # so sections declaring owner_join_field inherit the owner of the record they
    # reference (e.g. a derived-ledger row inherits its event's owner).
    owner_maps = build_section_owner_map(
        spec, [(s, rs) for s, rs, _ in collected], resolver, owner_scope
    )

    agg_owners: set[str] = set()
    agg_resolved = 0
    agg_unresolved = 0
    for section_spec, dict_records, section_records in collected:
        ids = _section_record_ids(section_records, section_spec.id_field)
        sample = tuple(sorted({i for i in ids if i is not None}))[:5]
        section_resolution = _resolve_record_owners(
            spec, dict_records, resolver, owner_scope, section_spec, owner_maps
        )
        agg_owners.update(section_resolution.resolved_owner_ids)
        agg_resolved += section_resolution.resolved_record_count
        agg_unresolved += section_resolution.unresolved_record_count
        section_summaries.append(
            JsonSourceSection(
                section_name=section_spec.section,
                target_table=section_spec.target_table,
                record_count=len(section_records),
                id_field=section_spec.id_field,
                duplicate_id_count=_count_duplicates(ids),
                sample_ids=sample,
                attributed_record_count=section_resolution.resolved_record_count,
                pending_record_count=section_resolution.unresolved_record_count,
            )
        )

    # Report any top-level keys present in the file but not in the registry,
    # so unmappable sections are visible rather than dropped.
    if spec.kind == "json_sectioned" and isinstance(payload, dict):
        mapped = {s.section for s in spec.sections}
        for key in payload:
            if isinstance(key, str) and key not in mapped and key != "schema_version":
                section_summaries.append(
                    JsonSourceSection(
                        section_name=key,
                        target_table=None,
                        record_count=len(payload[key]) if isinstance(payload[key], list) else 0,
                    )
                )

    owner_resolution = OwnerResolution(
        strategy=spec.owner.strategy,
        resolved_owner_ids=tuple(sorted(agg_owners)),
        resolved_record_count=agg_resolved,
        unresolved_record_count=agg_unresolved,
    )
    if spec.owner.strategy is OwnerStrategy.JOIN_GENERATION_TASK:
        owner_resolution = owner_resolution.model_copy(
            update={"join_source": spec.owner.join_source, "join_key": spec.owner.field}
        )
    elif spec.owner.strategy is OwnerStrategy.PATH_SCOPE:
        owner_resolution = owner_resolution.model_copy(
            update={"note": "owner taken from workspace scope"}
        )

    total_records = sum(s.record_count for s in section_summaries)
    is_empty = total_records == 0
    if is_empty:
        anomalies.append(
            Anomaly(
                code="empty_store",
                severity=AnomalySeverity.INFO,
                source_ref=spec.relative_path,
                message=f"source {spec.name!r} has no records",
            )
        )

    # Duplicate-id anomalies are surfaced per section with data.
    for sec in section_summaries:
        if sec.duplicate_id_count:
            anomalies.append(
                Anomaly(
                    code="duplicate_id_within_source",
                    severity=AnomalySeverity.WARNING,
                    source_ref=f"{spec.relative_path}::{sec.section_name}",
                    message=(
                        f"section {sec.section_name!r} has {sec.duplicate_id_count} duplicate id(s)"
                    ),
                    affected_record_count=sec.duplicate_id_count,
                )
            )

    if (
        owner_resolution.strategy is OwnerStrategy.JOIN_GENERATION_TASK
        and owner_resolution.unresolved_record_count
    ):
        anomalies.append(
            Anomaly(
                code="join_resolution_failure",
                severity=AnomalySeverity.WARNING,
                source_ref=spec.relative_path,
                message=(
                    f"{owner_resolution.unresolved_record_count} record(s) in "
                    f"{spec.name!r} could not resolve an owner via "
                    f"{spec.owner.join_source}"
                ),
                affected_record_count=owner_resolution.unresolved_record_count,
                detail={"join_key": spec.owner.field or ""},
            )
        )

    if (
        owner_resolution.strategy in (OwnerStrategy.DIRECT_OWNER_ID, OwnerStrategy.USER_ID_FIELD)
        and len(owner_resolution.resolved_owner_ids) > 1
    ):
        anomalies.append(
            Anomaly(
                code="owner_fragmentation",
                severity=AnomalySeverity.WARNING,
                source_ref=spec.relative_path,
                message=(
                    f"source {spec.name!r} spans "
                    f"{len(owner_resolution.resolved_owner_ids)} distinct owner ids"
                ),
                affected_record_count=owner_resolution.resolved_record_count,
                detail={"owner_ids": ",".join(owner_resolution.resolved_owner_ids)},
            )
        )

    summary = JsonSourceSummary(
        source_name=spec.name,
        relative_path=spec.relative_path,
        kind=spec.kind if spec.kind in ("json_sectioned", "json_list") else "json_sectioned",
        exists=True,
        byte_size=byte_size,
        sha256=sha256,
        mtime=mtime,
        schema_version=schema_version,
        sections=tuple(section_summaries),
        owner_resolution=owner_resolution,
        is_empty=is_empty,
    )
    return summary, anomalies


def _analyze_per_task(
    spec: SourceSpec,
    base: Path,
    resolver: OwnerResolver,
    owner_scope: str,
) -> tuple[PerTaskFileCollection, list[Anomaly]]:
    anomalies: list[Anomaly] = []
    files = sorted(base.glob(spec.relative_path))
    section_spec = spec.sections[0] if spec.sections else None
    id_field = section_spec.id_field if section_spec else None

    if not files:
        collection = PerTaskFileCollection(
            source_name=spec.name,
            relative_dir=spec.relative_path,
            exists=False,
            file_count=0,
            total_byte_size=0,
            aggregate_sha256="",
            id_field=id_field or "",
            owner_resolution=OwnerResolution(strategy=spec.owner.strategy),
        )
        anomalies.append(
            Anomaly(
                code="missing_expected_source",
                severity=AnomalySeverity.WARNING,
                source_ref=spec.relative_path,
                message=f"expected source {spec.name!r} has no task files",
            )
        )
        return collection, anomalies

    hash_pairs: list[str] = []
    total_size = 0
    all_ids: list[str | None] = []
    records: list[dict] = []  # real (non-residue) payloads eligible for migration
    residue_count = 0
    parse_error_files: list[str] = []
    for path in files:
        total_size += path.stat().st_size
        file_hash = _sha256_file(path)
        hash_pairs.append(f"{path.name}:{file_hash}")
        payload, parse_error = _load_json(path)
        if parse_error is not None or not isinstance(payload, dict):
            parse_error_files.append(path.name)
            continue
        if _is_residue(payload, spec.residue_if_empty_fields):
            # Never-produced artifacts: counted, reported, but neither migrated
            # nor counted as attribution-pending.  Their id is still hashed and
            # dup-checked so a residue file colliding with a real id surfaces.
            residue_count += 1
            if id_field is not None:
                value = payload.get(id_field)
                all_ids.append(str(value) if value is not None else None)
            continue
        records.append(payload)
        if id_field is not None:
            value = payload.get(id_field)
            all_ids.append(str(value) if value is not None else None)

    aggregate_input = "\n".join(sorted(hash_pairs)).encode("utf-8")
    aggregate_sha = hashlib.sha256(aggregate_input).hexdigest()
    duplicate_ids = _count_duplicates(all_ids)
    sample = tuple(sorted({i for i in all_ids if i is not None}))[:5]

    owner_resolution = _resolve_record_owners(spec, records, resolver, owner_scope)

    if parse_error_files:
        anomalies.append(
            Anomaly(
                code="malformed_json",
                severity=AnomalySeverity.ERROR,
                source_ref=spec.relative_path,
                message=(
                    f"{len(parse_error_files)} task file(s) in {spec.name!r} could not be parsed"
                ),
                affected_record_count=len(parse_error_files),
                detail={"files": ",".join(parse_error_files[:10])},
            )
        )

    if duplicate_ids:
        anomalies.append(
            Anomaly(
                code="duplicate_id_within_source",
                severity=AnomalySeverity.WARNING,
                source_ref=spec.relative_path,
                message=(f"{duplicate_ids} duplicate id(s) across {spec.name!r} task files"),
                affected_record_count=duplicate_ids,
            )
        )

    if (
        owner_resolution.strategy is OwnerStrategy.JOIN_GENERATION_TASK
        and owner_resolution.unresolved_record_count
    ):
        anomalies.append(
            Anomaly(
                code="join_resolution_failure",
                severity=AnomalySeverity.WARNING,
                source_ref=spec.relative_path,
                message=(
                    f"{owner_resolution.unresolved_record_count} record(s) in "
                    f"{spec.name!r} could not resolve an owner via "
                    f"{spec.owner.join_source}"
                ),
                affected_record_count=owner_resolution.unresolved_record_count,
                detail={"join_key": spec.owner.field or ""},
            )
        )

    if residue_count:
        anomalies.append(
            Anomaly(
                code="empty_residue_record",
                severity=AnomalySeverity.INFO,
                source_ref=spec.relative_path,
                message=(
                    f"{residue_count} file(s) in {spec.name!r} are never-produced "
                    f"residue ({'/'.join(spec.residue_if_empty_fields)} empty); "
                    f"Phase 5 quarantine/delete, do not migrate"
                ),
                affected_record_count=residue_count,
                detail={"residue_fields": ",".join(spec.residue_if_empty_fields)},
            )
        )

    collection = PerTaskFileCollection(
        source_name=spec.name,
        relative_dir=spec.relative_path,
        exists=True,
        file_count=len(files),
        total_byte_size=total_size,
        aggregate_sha256=aggregate_sha,
        id_field=id_field or "",
        duplicate_id_count=duplicate_ids,
        sample_ids=sample,
        owner_resolution=owner_resolution,
        parse_error_files=tuple(parse_error_files),
        residue_record_count=residue_count,
    )
    return collection, anomalies


def _analyze_sqlite(
    spec: SourceSpec, path: Path, owner_scope: str
) -> tuple[SqliteSourceSummary, list[Anomaly]]:
    anomalies: list[Anomaly] = []
    if not path.is_file():
        summary = SqliteSourceSummary(
            source_name=spec.name,
            relative_path=spec.relative_path,
            exists=False,
            byte_size=0,
            sha256="",
            integrity_check="missing",
            owner_resolution=OwnerResolution(strategy=spec.owner.strategy),
        )
        anomalies.append(
            Anomaly(
                code="missing_expected_source",
                severity=AnomalySeverity.WARNING,
                source_ref=spec.relative_path,
                message=f"expected source {spec.name!r} is absent on disk",
            )
        )
        return summary, anomalies

    byte_size = path.stat().st_size
    sha256 = _sha256_file(path)
    uri = f"file:{path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        anomalies.append(
            Anomaly(
                code="malformed_json",
                severity=AnomalySeverity.ERROR,
                source_ref=spec.relative_path,
                message=f"source {spec.name!r} could not be opened: {exc}",
            )
        )
        return (
            SqliteSourceSummary(
                source_name=spec.name,
                relative_path=spec.relative_path,
                exists=True,
                byte_size=byte_size,
                sha256=sha256,
                integrity_check=f"open error: {exc}",
                owner_resolution=OwnerResolution(strategy=spec.owner.strategy),
            ),
            anomalies,
        )

    table_summaries: list[SqliteTableSummary] = []
    distinct_owners: set[str] = set()
    integrity = "unknown"
    try:
        integrity_row = connection.execute("PRAGMA integrity_check").fetchone()
        integrity = str(integrity_row[0] if integrity_row else "unknown")
        if integrity != "ok":
            anomalies.append(
                Anomaly(
                    code="sqlite_integrity_failure",
                    severity=AnomalySeverity.ERROR,
                    source_ref=spec.relative_path,
                    message=f"source {spec.name!r} integrity_check: {integrity}",
                    detail={"integrity_check": integrity},
                )
            )

        for table in spec.sqlite_tables:
            try:
                row_count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                # Table may not exist in a fresh/legacy DB; report zero, no crash.
                row_count = 0
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            has_owner = (
                spec.sqlite_owner_column is not None
                and table == spec.sqlite_owner_table
                and spec.sqlite_owner_column in columns
            )
            owner_count: int | None = None
            if has_owner and spec.sqlite_owner_column is not None:
                try:
                    owner_rows = connection.execute(
                        f"SELECT DISTINCT {spec.sqlite_owner_column} FROM {table} "
                        f"WHERE {spec.sqlite_owner_column} IS NOT NULL"
                    ).fetchall()
                    distinct_owners.update(str(row[0]) for row in owner_rows if row[0] is not None)
                    owner_count = len(distinct_owners)
                except sqlite3.Error:
                    owner_count = None
            table_summaries.append(
                SqliteTableSummary(
                    table_name=table,
                    row_count=row_count,
                    has_owner_column=has_owner,
                    owner_column_name=spec.sqlite_owner_column if has_owner else None,
                    distinct_owner_count=owner_count,
                )
            )
    finally:
        connection.close()

    total_rows = sum(t.row_count for t in table_summaries)
    if spec.owner.strategy is OwnerStrategy.SQLITE_COLUMN:
        owner_resolution = OwnerResolution(
            strategy=spec.owner.strategy,
            resolved_owner_ids=tuple(sorted(distinct_owners)),
            resolved_record_count=total_rows if distinct_owners else 0,
            unresolved_record_count=0 if distinct_owners else total_rows,
        )
    elif spec.owner.strategy is OwnerStrategy.PATH_SCOPE:
        # Owner-less source whose owner is the workspace it lives in (e.g. the
        # knowledge graph).  Every row is attributed to the path scope; this is
        # kept strictly separate from evidence strength (graph evidence is never
        # BKT strong evidence).
        owner_resolution = OwnerResolution(
            strategy=spec.owner.strategy,
            resolved_owner_ids=(owner_scope,) if owner_scope else (),
            resolved_record_count=total_rows,
            unresolved_record_count=0,
            note="owner taken from workspace path scope",
        )
    else:
        owner_resolution = OwnerResolution(
            strategy=spec.owner.strategy,
            resolved_owner_ids=(),
            resolved_record_count=0,
            unresolved_record_count=total_rows,
        )

    summary = SqliteSourceSummary(
        source_name=spec.name,
        relative_path=spec.relative_path,
        exists=True,
        byte_size=byte_size,
        sha256=sha256,
        integrity_check=integrity,
        tables=tuple(table_summaries),
        owner_resolution=owner_resolution,
    )
    return summary, anomalies


def build_generation_task_resolver(
    base: Path,
) -> tuple[OwnerResolver, list[Anomaly]]:
    """Build the owner join map by unioning both generation-task stores.

    Two stores carry ``generation_id → owner_id`` and are unioned here so every
    join-based source resolves against the same map in a single pass:

    * the authoritative ``generation-tasks.sqlite`` at the system root
      (``data/system/generation-tasks.sqlite``; the live UPSERT target used by
      ``GenerationTaskManager._TaskStore``), and
    * the legacy per-workspace ``generation-tasks/*.json`` files.

    The SQLite store wins on conflict.  ``base`` is ``user_data_dir``; the
    system root is its sibling ``../system`` (``workspace_root/system``).
    """
    task_spec = next((s for s in SOURCE_SPECS if s.name == "generation_tasks"), None)
    anomalies: list[Anomaly] = []
    if task_spec is None:  # pragma: no cover - registry invariant
        return OwnerResolver(), anomalies

    task_files = sorted(base.glob(task_spec.relative_path))
    resolver = OwnerResolver.from_task_files(task_files)

    system_db = base.parent / "system" / "generation-tasks.sqlite"
    sqlite_resolver = OwnerResolver.from_task_sqlite(system_db)
    if system_db.is_file() and not sqlite_resolver.known_keys:
        anomalies.append(
            Anomaly(
                code="join_resolution_failure",
                severity=AnomalySeverity.WARNING,
                source_ref=str(system_db.relative_to(base.parent)),
                message=(
                    "generation-tasks.sqlite exists but yielded no usable "
                    "generation_id→owner_id mapping"
                ),
            )
        )
    # SQLite is authoritative — overlay it on the legacy JSON map.
    resolver = resolver.merge(sqlite_resolver)

    if not resolver.known_keys and task_files:
        anomalies.append(
            Anomaly(
                code="join_resolution_failure",
                severity=AnomalySeverity.WARNING,
                source_ref=task_spec.relative_path,
                message=(
                    "generation-task files exist but none yielded a usable "
                    "generation_id→owner_id mapping"
                ),
            )
        )
    return resolver, anomalies


def build_baseline_manifest(
    *,
    path_service: PathService | None = None,
    owner_scope: str = "default",
) -> BaselineManifest:
    """Walk every registered source and return a frozen :class:`BaselineManifest`.

    ``owner_scope`` is only a workspace label for PATH_SCOPE/UNRESOLVED sources;
    actual owner ids are always read from the records themselves.
    """
    service = path_service or get_path_service()
    base = service.user_data_dir

    resolver, resolver_anomalies = build_generation_task_resolver(base)

    json_sources: list[JsonSourceSummary] = []
    per_task_collections: list[PerTaskFileCollection] = []
    sqlite_sources: list[SqliteSourceSummary] = []
    anomalies: list[Anomaly] = list(resolver_anomalies)

    for spec in SOURCE_SPECS:
        if spec.kind in ("json_sectioned", "json_list"):
            path = base / spec.relative_path
            jsummary, spec_anomalies = _analyze_sectioned_or_list(spec, path, resolver, owner_scope)
            json_sources.append(jsummary)
            anomalies.extend(spec_anomalies)
        elif spec.kind == "json_per_task_file":
            tsummary, spec_anomalies = _analyze_per_task(spec, base, resolver, owner_scope)
            per_task_collections.append(tsummary)
            anomalies.extend(spec_anomalies)
        elif spec.kind == "sqlite":
            path = base / spec.relative_path
            ssummary, spec_anomalies = _analyze_sqlite(spec, path, owner_scope)
            sqlite_sources.append(ssummary)
            anomalies.extend(spec_anomalies)

    distinct_owners: set[str] = set()
    total_records = 0
    unresolved_owner_records = 0
    for jsource in json_sources:
        total_records += sum(s.record_count for s in jsource.sections)
        distinct_owners.update(jsource.owner_resolution.resolved_owner_ids)
        unresolved_owner_records += jsource.owner_resolution.unresolved_record_count
    for collection in per_task_collections:
        total_records += collection.file_count
        distinct_owners.update(collection.owner_resolution.resolved_owner_ids)
        unresolved_owner_records += collection.owner_resolution.unresolved_record_count
    for ssource in sqlite_sources:
        total_records += sum(t.row_count for t in ssource.tables)
        distinct_owners.update(ssource.owner_resolution.resolved_owner_ids)
        unresolved_owner_records += ssource.owner_resolution.unresolved_record_count

    error_count = sum(1 for a in anomalies if a.severity is AnomalySeverity.ERROR)
    warning_count = sum(1 for a in anomalies if a.severity is AnomalySeverity.WARNING)
    info_count = sum(1 for a in anomalies if a.severity is AnomalySeverity.INFO)

    total_sources = len(json_sources) + len(per_task_collections) + len(sqlite_sources)

    return BaselineManifest(
        generated_at=_utc_now(),
        data_root=str(base),
        owner_scope=owner_scope,
        plan_listed_sources=tuple(s.name for s in SOURCE_SPECS if s.listed_in_plan),
        json_sources=tuple(json_sources),
        per_task_collections=tuple(per_task_collections),
        sqlite_sources=tuple(sqlite_sources),
        anomalies=tuple(anomalies),
        summary=BaselineSummary(
            total_sources=total_sources,
            total_records=total_records,
            total_anomalies=len(anomalies),
            error_anomaly_count=error_count,
            warning_anomaly_count=warning_count,
            info_anomaly_count=info_count,
            unresolved_owner_record_count=unresolved_owner_records,
            distinct_owner_ids=tuple(sorted(distinct_owners)),
        ),
    )
