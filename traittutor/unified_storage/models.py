"""Pydantic models for the Phase 0 baseline manifest and dry-run report.

These types are the auditable contract between the read-only inventory, the
dry-run migration planner, and any human reviewing the output.  They are
intentionally ``frozen`` so a produced manifest/report cannot be silently
mutated after construction — every change must produce a new artifact.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

manifest_model_config = ConfigDict(extra="forbid", frozen=True)


class OwnerStrategy(str, Enum):
    """How the owner of a record collection was, or would be, determined.

    Each value maps to a concrete resolution path in
    :mod:`traittutor.unified_storage.mapping`.
    """

    DIRECT_OWNER_ID = "direct_owner_id"
    USER_ID_FIELD = "user_id_field"
    JOIN_GENERATION_TASK = "join_generation_task"
    SQLITE_COLUMN = "sqlite_column"
    PATH_SCOPE = "path_scope"
    UNRESOLVED = "unresolved"


class AnomalySeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ── Owner resolution ──────────────────────────────────────────────────────────


class OwnerResolution(BaseModel):
    """How the owner of one record collection was determined at baseline."""

    model_config = manifest_model_config

    strategy: OwnerStrategy
    resolved_owner_ids: tuple[str, ...] = ()
    join_source: str | None = None
    join_key: str | None = None
    resolved_record_count: int = 0
    unresolved_record_count: int = 0
    note: str | None = None


# ── Anomalies ─────────────────────────────────────────────────────────────────


class Anomaly(BaseModel):
    """A surfaced irregularity.  Nothing is silently skipped.

    ``code`` is a stable machine-readable key (see ``ANOMALY_CODES``); ``detail``
    carries structured primitives so reports stay JSON-serializable and
    diffable across runs.
    """

    model_config = manifest_model_config

    code: str
    severity: AnomalySeverity
    source_ref: str
    message: str
    affected_record_count: int = 0
    detail: dict[str, str | int | bool] = Field(default_factory=dict)


# Stable anomaly codes.  Tests and consumers filter on these, never on prose.
ANOMALY_CODES = (
    "missing_expected_source",
    "empty_store",
    "malformed_json",
    "duplicate_id_within_source",
    "duplicate_id_across_sources",
    "join_resolution_failure",
    "owner_fragmentation",
    "fk_orphan",
    "owner_mismatch",
    "plan_source_not_found",
    "discovered_source_not_in_plan",
    "unmappable_section",
    "sqlite_integrity_failure",
    # A per-task file whose payload marks it as never-produced residue (e.g. a
    # generation result with an empty ``result``).  Residue is reported, never
    # silently dropped, but it is not a rebuild candidate — Phase 5 quarantines
    # or deletes it rather than migrating it as business data.
    "empty_residue_record",
)


# ── JSON source summaries ─────────────────────────────────────────────────────


class JsonSourceSection(BaseModel):
    """One top-level list section inside a JSON source file."""

    model_config = manifest_model_config

    section_name: str
    target_table: str | None
    record_count: int
    id_field: str | None = None
    duplicate_id_count: int = 0
    sample_ids: tuple[str, ...] = ()
    # Per-section owner attribution so the dry-run can project attributed vs.
    # attribution-pending rows per target table without inventing precision.
    attributed_record_count: int = 0
    pending_record_count: int = 0


class JsonSourceSummary(BaseModel):
    model_config = manifest_model_config

    source_name: str
    relative_path: str
    kind: Literal["json_sectioned", "json_list", "json_per_task_file"] = "json_sectioned"
    exists: bool
    byte_size: int
    sha256: str
    mtime: str
    schema_version: int | str | None = None
    sections: tuple[JsonSourceSection, ...] = ()
    owner_resolution: OwnerResolution
    is_empty: bool = False
    parse_error: str | None = None


class PerTaskFileCollection(BaseModel):
    """Summary of a directory of per-task JSON files (e.g. ``generations/``).

    The generation-results directory is summarized by an aggregate hash over
    sorted ``(filename, file-hash)`` pairs rather than one entry per file:
    this keeps the manifest compact while still detecting any single-file
    change or duplicate id across files.
    """

    model_config = manifest_model_config

    source_name: str
    relative_dir: str
    exists: bool
    file_count: int
    total_byte_size: int
    aggregate_sha256: str
    id_field: str
    duplicate_id_count: int = 0
    sample_ids: tuple[str, ...] = ()
    owner_resolution: OwnerResolution
    parse_error_files: tuple[str, ...] = ()
    # Files whose payload marks them as never-produced residue (see
    # ``SourceSpec.residue_if_empty_fields``).  They are excluded from owner
    # attribution counts: residue is neither attributed nor attribution-pending
    # — it is a Phase 5 deletion/quarantine candidate, reported but not migrated.
    residue_record_count: int = 0


class SqliteTableSummary(BaseModel):
    model_config = manifest_model_config

    table_name: str
    row_count: int
    has_owner_column: bool = False
    owner_column_name: str | None = None
    distinct_owner_count: int | None = None


class SqliteSourceSummary(BaseModel):
    model_config = manifest_model_config

    source_name: str
    relative_path: str
    exists: bool
    byte_size: int
    sha256: str
    integrity_check: str
    tables: tuple[SqliteTableSummary, ...] = ()
    owner_resolution: OwnerResolution


# ── Top-level manifest ────────────────────────────────────────────────────────


class BaselineSummary(BaseModel):
    model_config = manifest_model_config

    total_sources: int
    total_records: int
    total_anomalies: int
    error_anomaly_count: int
    warning_anomaly_count: int
    info_anomaly_count: int
    unresolved_owner_record_count: int
    distinct_owner_ids: tuple[str, ...] = ()


class BaselineManifest(BaseModel):
    """Machine-readable snapshot of all source stores, consumed by dry_run."""

    model_config = manifest_model_config

    manifest_version: int = 1
    generated_at: str
    data_root: str
    owner_scope: str
    plan_listed_sources: tuple[str, ...] = ()
    json_sources: tuple[JsonSourceSummary, ...] = ()
    per_task_collections: tuple[PerTaskFileCollection, ...] = ()
    sqlite_sources: tuple[SqliteSourceSummary, ...] = ()
    anomalies: tuple[Anomaly, ...] = ()
    summary: BaselineSummary


# ── Dry-run report ────────────────────────────────────────────────────────────


class TargetProjection(BaseModel):
    """Projected row counts for one target table."""

    model_config = manifest_model_config

    target_table: str
    source_name: str
    projected_row_count: int
    attributed_record_count: int = 0
    attribution_pending_count: int = 0
    needs_rebuild_count: int = 0
    conflict_count: int = 0
    # Rows projected to be excluded from migration as never-produced residue
    # (Phase 5 quarantine/deletion candidates), reported separately so they do
    # not inflate ``needs_rebuild_count`` or ``attribution_pending_count``.
    residue_count: int = 0


class ConflictRecord(BaseModel):
    model_config = manifest_model_config

    conflict_type: Literal[
        "duplicate_id_within_source",
        "duplicate_id_across_sources",
        "fk_orphan",
        "owner_mismatch",
        "owner_fragmentation",
    ]
    source_ref: str
    record_ids: tuple[str, ...]
    message: str


class UnmappableRecord(BaseModel):
    model_config = manifest_model_config

    source_ref: str
    record_id: str | None = None
    reason: str


class OwnerFragmentationGroup(BaseModel):
    """Records that may be the same logical user under different ids."""

    model_config = manifest_model_config

    owner_ids: tuple[str, ...]
    sources: tuple[str, ...]


class AttributionSummary(BaseModel):
    model_config = manifest_model_config

    total_records: int
    fully_attributed: int
    attribution_pending: int
    distinct_owner_ids: tuple[str, ...] = ()
    owner_fragmentation_groups: tuple[OwnerFragmentationGroup, ...] = ()


class DryRunReport(BaseModel):
    """Read-only migration plan.  Never writes to ``traittutor.sqlite3``."""

    model_config = manifest_model_config

    report_version: int = 1
    generated_at: str
    manifest_path: str
    target_projections: tuple[TargetProjection, ...] = ()
    conflicts: tuple[ConflictRecord, ...] = ()
    unmappable_records: tuple[UnmappableRecord, ...] = ()
    attribution_summary: AttributionSummary
    human_summary: str


# ── Source backup manifest ────────────────────────────────────────────────────


class BackupFileEntry(BaseModel):
    """One file captured by a source backup."""

    model_config = manifest_model_config

    source_name: str
    relative_path: str  # relative to user_data_dir
    sha256: str
    byte_size: int


class BackupManifest(BaseModel):
    """Manifest for a read-only pre-migration source backup (Phase 0 item 4).

    Backups never overwrite an existing backup directory; this manifest is the
    verifiable record of what was snapshotted, written alongside the copies.
    """

    model_config = manifest_model_config

    manifest_version: int = 1
    created_at: str
    owner_scope: str
    source_root: str
    backup_root: str
    files: tuple[BackupFileEntry, ...] = ()
    file_count: int = 0
    total_byte_size: int = 0
