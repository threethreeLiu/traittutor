"""Owner-scoped SQLite persistence and migration tooling for business records.

Runtime stores use the canonical database. Inventory, dry-run, backup,
migration, and reconciliation helpers exist only for explicit one-way imports;
they are not an online fallback or dual-write path.
"""

from .backup import create_source_backup
from .document_store import SQLiteDocumentStore
from .dry_run import plan_migration
from .inventory import build_baseline_manifest
from .mapping import SOURCE_SPECS, OwnerResolver, SourceSpec
from .migrator import (
    PHASE_2_SOURCE_NAMES,
    PHASE_3_SOURCE_NAMES,
    PHASE_4_SOURCE_NAMES,
    MigrationReport,
    TableMigrationResult,
    migrate_sources,
)
from .models import (
    ANOMALY_CODES,
    Anomaly,
    AnomalySeverity,
    AttributionSummary,
    BackupFileEntry,
    BackupManifest,
    BaselineManifest,
    BaselineSummary,
    ConflictRecord,
    DryRunReport,
    JsonSourceSection,
    JsonSourceSummary,
    OwnerFragmentationGroup,
    OwnerResolution,
    OwnerStrategy,
    PerTaskFileCollection,
    SqliteSourceSummary,
    SqliteTableSummary,
    TargetProjection,
    UnmappableRecord,
)
from .reconcile import (
    DEFAULT_SOURCE_NAMES,
    ReconciliationReport,
    SectionReconciliation,
    SourceIntactCheck,
    reconcile_sources,
)
from .schema import business_table_exists, create_business_tables
from .section_store import SectionedRecordStore
from .store import (
    CURRENT_SCHEMA_VERSION,
    UnifiedStore,
    UnifiedStoreError,
    create_backup,
    initialize_database,
    restore_backup,
)

__all__ = [
    # Foundation (Phase 1)
    "CURRENT_SCHEMA_VERSION",
    "UnifiedStore",
    "UnifiedStoreError",
    "create_backup",
    "initialize_database",
    "restore_backup",
    # Mapping registry (Phase 0)
    "SOURCE_SPECS",
    "SourceSpec",
    "OwnerResolver",
    # Inventory + dry-run + backup (Phase 0)
    "build_baseline_manifest",
    "plan_migration",
    "create_source_backup",
    # Migration (Phase 2+)
    "PHASE_2_SOURCE_NAMES",
    "PHASE_3_SOURCE_NAMES",
    "PHASE_4_SOURCE_NAMES",
    "MigrationReport",
    "TableMigrationResult",
    "migrate_sources",
    "DEFAULT_SOURCE_NAMES",
    "ReconciliationReport",
    "SectionReconciliation",
    "SourceIntactCheck",
    "reconcile_sources",
    "business_table_exists",
    "create_business_tables",
    "SectionedRecordStore",
    "SQLiteDocumentStore",
    # Models
    "ANOMALY_CODES",
    "Anomaly",
    "AnomalySeverity",
    "AttributionSummary",
    "BackupFileEntry",
    "BackupManifest",
    "BaselineManifest",
    "BaselineSummary",
    "ConflictRecord",
    "DryRunReport",
    "JsonSourceSection",
    "JsonSourceSummary",
    "OwnerFragmentationGroup",
    "OwnerResolution",
    "OwnerStrategy",
    "PerTaskFileCollection",
    "SqliteSourceSummary",
    "SqliteTableSummary",
    "TargetProjection",
    "UnmappableRecord",
]
