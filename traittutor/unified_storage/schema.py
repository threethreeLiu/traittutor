"""Business-table schema for the unified owner-bound database.

Phase 2 introduces one table per target declared by the source registry
(:data:`traittutor.unified_storage.mapping.SOURCE_SPECS`).  Every table shares
a uniform, lossless shape so the migration is fully reconcilable and
reversible:

* ``owner_id`` — the server-resolved owner (never client-controlled), indexed
  so the Store layer can filter every query by owner without a caller hint.
* ``record_id`` — the source primary key verbatim when the section has one, or
  a deterministic content hash when it does not.  Primary key of the table.
* ``source_section`` — which source/section the row came from (audit /
  rollback key).
* ``payload_json`` — the source record serialized verbatim.  This preserves the
  original field for reconciliation and later typed extraction; the plan frames
  Phase 2-4 as *consolidation* into one owner-bound DB, not a relational
  redesign, so no source field is dropped or guessed.
* ``source_sha256`` / ``migrated_at`` — provenance for idempotent re-runs.

No table is created until its source is migrated, so an interrupted rollout
never exposes a half-migrated aggregate (plan §6).
"""

from __future__ import annotations

import sqlite3

# Every business table is born with this shape.  A dedicated table per target
# (rather than one polymorphic ``records`` table) keeps per-domain indexes and
# constraints honest and matches the source→target table map the dry-run
# projects against.
_BUSINESS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    record_id        TEXT    NOT NULL,
    owner_id         TEXT    NOT NULL,
    source_section   TEXT    NOT NULL,
    payload_json     TEXT    NOT NULL,
    source_sha256    TEXT    NOT NULL,
    migrated_at      TEXT    NOT NULL,
    PRIMARY KEY (record_id)
)
"""

_BUSINESS_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner_id)"


def _identifier_ok(name: str) -> bool:
    """Reject anything that is not a bare identifier (defends the format call)."""
    return name.isidentifier() and all(c.isalnum() or c == "_" for c in name)


def create_business_tables(connection: sqlite3.Connection, table_names: list[str]) -> None:
    """Create the uniform business tables for ``table_names`` idempotently.

    Called inside an existing transaction by the migrator.  ``table_names`` must
    be a subset of the registry's target tables; each is validated as a bare
    identifier before being interpolated into DDL.
    """
    for table in table_names:
        if not _identifier_ok(table):
            raise ValueError(f"refusing to create table with unsafe name: {table!r}")
        # ``executescript`` performs an implicit COMMIT, which breaks an outer
        # domain transaction when another SectionedRecordStore is entered from
        # an event-first callback.  Individual DDL statements participate in
        # the caller's transaction and therefore roll back atomically with it.
        connection.execute(_BUSINESS_TABLE_DDL.format(table=table))
        connection.execute(_BUSINESS_INDEX_DDL.format(table=table))


def business_table_exists(connection: sqlite3.Connection, table: str) -> bool:
    """True when ``table`` has already been created in this database."""
    if not _identifier_ok(table):
        raise ValueError(f"refusing to query table with unsafe name: {table!r}")
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None
