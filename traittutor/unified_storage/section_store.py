"""Canonical SQLite backing for sectioned and list-shaped domain stores.

The adapter reconstructs the established dict-of-sections in-memory shape so
domain logic can use SQLite transactions without reopening a JSON read/write
path. Business rules, idempotency, and evidence gates remain unchanged.

One ``BEGIN IMMEDIATE`` transaction per locked section replaces one flock
section — the same process-wide mutual exclusion, with stronger atomicity
(read + mutate + write commit as one unit, or roll back).

Design rules:

* **Workspace-faithful read/write** — every row for a section is read back,
  matching the legacy "one file = all records" shape.  Owner filtering stays in
  the business module, exactly as before; the migration did not change that.
* **Owner never re-resolved differently from the migration** — each written
  row's ``owner_id`` comes from the *same* :func:`resolve_section_owner` the
  migrator used, so a live write and a migrated read never disagree on owner.
* **Type-agnostic** — a section may hold non-dict markers (notably the learner
  ``derived_applied`` set, a ``list[str]``).  These are stored verbatim as
  ``payload_json`` and restored by ``json.loads``, so no marker is silently
  dropped.  (The historical migration skipped non-dict rows; live writes do
  not, which only *adds* rows going forward and never loses data.)
* **Reuses** :func:`migrator._record_id` / :func:`migrator._source_sha` /
  :func:`migrator.path_scoped_suffix` so record ids and section tags match the
  migration byte-for-byte (idempotent ``INSERT OR REPLACE`` lands on the same
  row, never a duplicate).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from traittutor.services.path_service import PathService

from .inventory import OwnerResolver, build_section_owner_map, resolve_section_owner
from .mapping import SectionSpec, SourceSpec, get_source
from .migrator import _record_id, _source_sha, path_scoped_suffix
from .schema import create_business_tables
from .store import UnifiedStore, _utc_now


def _scalar_record_id(value: Any) -> str:
    """Deterministic content-hash id for a non-dict section marker.

    The migration never stored non-dict rows, so there is no historical
    record id to match — a stable synthetic id is free to choose.  It reuses
    the migrator's ``synthetic:<sha24>`` shape for visual consistency.
    """
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"synthetic:{digest[:24]}"


def _scalar_sha(value: Any) -> str:
    """``source_sha256`` for a non-dict value, mirroring :func:`_source_sha`."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


class SectionedRecordStore:
    """Unified-DB persistence for one sectioned/list legacy source.

    Construct one per store instance, bound to the server-resolved workspace
    ``owner_id`` (used for PATH_SCOPE rows and as the UnifiedStore scope; it
    never selects the database path).  ``source_name`` must be a registered
    source in :data:`mapping.SOURCE_SPECS`.
    """

    def __init__(
        self,
        source_name: str,
        owner_id: str,
        *,
        schema_version: int,
        path_service: PathService | None = None,
        db_path: Any | None = None,
        legacy_path: str | Path | None = None,
    ) -> None:
        self._spec: SourceSpec = get_source(source_name)
        self.owner_id = owner_id
        self._schema_version = schema_version
        self._path_service = path_service
        # Transitional: a legacy per-file ``path`` (the old JSON filename) is
        # accepted so switched stores keep their existing ``path=`` constructors
        # without a sweeping rename.  It only selects an *isolated* database
        # location for tests/legacy callers — it is never read or written as a
        # file.  Co-located stores in one workspace (same parent dir) land on the
        # same ``traittutor.sqlite3``, matching the one-DB-per-workspace model.
        if db_path is None and path_service is None and legacy_path is not None:
            db_path = Path(legacy_path).parent / "traittutor.sqlite3"
        self._db_path = db_path
        # Stack of currently-held transaction connections on this instance, so
        # ``snapshot``/``replace_all`` called *inside* a ``locked`` block reuse
        # the active connection instead of opening a nested one (which would
        # self-deadlock on SQLite's write lock).
        self._active: ContextVar[tuple[sqlite3.Connection, ...]] = ContextVar(
            f"section_store_active_{id(self)}", default=()
        )

    @property
    def spec(self) -> SourceSpec:
        return self._spec

    def _store(self) -> UnifiedStore:
        return UnifiedStore(self.owner_id, path_service=self._path_service, db_path=self._db_path)

    def _target_tables(self) -> list[str]:
        return [section.target_table for section in self._spec.sections]

    def _ensure_tables(self) -> None:
        """Create this source's tables in a short, self-committing transaction.

        :func:`create_business_tables` runs ``executescript``, which issues an
        implicit ``COMMIT`` before its script — that would close the read/write
        transaction and turn every subsequent statement into an autocommit,
        defeating rollback.  Running table creation in its own transaction first
        keeps the yielded transaction's boundaries intact (a real ``BEGIN
        IMMEDIATE`` with no mid-block commit), so an exception rolls back the
        whole read/mutate/write unit.
        """
        with self._store().transaction() as connection:
            create_business_tables(connection, self._target_tables())

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """One immediate transaction with this source's tables ensured.

        Tables are created idempotently (in a prior short transaction) so a
        fresh database (no migration run yet) behaves like an empty store
        rather than erroring.  The connection is pushed onto ``_active`` so
        read/write helpers within the block share it.  Rolled back on any
        exception by :meth:`UnifiedStore.transaction`.
        """
        store = self._store()
        parent = store.active_transaction_connection()
        if parent is not None:
            # A sibling domain store already owns the same SQLite transaction.
            # Its tables were initialized before BEGIN IMMEDIATE; running the
            # schema executescript here would implicitly commit that parent and
            # break the event-before-projection atomic boundary.
            active = self._active.get()
            token = self._active.set((*active, parent))
            try:
                yield parent
            finally:
                self._active.reset(token)
            return
        self._ensure_tables()
        with store.transaction() as connection:
            active = self._active.get()
            token = self._active.set((*active, connection))
            try:
                yield connection
            finally:
                self._active.reset(token)

    def _section_key(self, section_spec: SectionSpec) -> str:
        return f"{self._spec.name}/{section_spec.section}{path_scoped_suffix(self._spec)}"

    def read_via(self, connection: sqlite3.Connection) -> dict[str, Any]:
        """Reconstruct the legacy dict-of-sections payload from the database.

        Rows are read in ``rowid`` order so append order (file order) is
        preserved — conversations, memory lifecycle and the derived ledger all
        depend on stable ordering.  ``schema_version`` is injected to match the
        legacy file shape the modules validate against.
        """
        payload: dict[str, Any] = {"schema_version": self._schema_version}
        for section_spec in self._spec.sections:
            section_key = self._section_key(section_spec)
            rows = connection.execute(
                f"SELECT payload_json FROM {section_spec.target_table} "  # noqa: S608 - target_table is identifier-validated at table-create time
                "WHERE source_section=? ORDER BY rowid",
                (section_key,),
            ).fetchall()
            payload[section_spec.section] = [json.loads(row["payload_json"]) for row in rows]
        return payload

    def _write_record(
        self,
        connection: sqlite3.Connection,
        section_spec: SectionSpec,
        record: Any,
        owner_maps: Any,
        now: str,
    ) -> None:
        """Upsert one record row (shared by full replace and pure append)."""
        if isinstance(record, dict):
            owner = resolve_section_owner(
                self._spec,
                section_spec,
                record,
                OwnerResolver(),
                self.owner_id,
                owner_maps,
                path_scope_owner=self.owner_id,
            )
            if owner is None:
                owner = self.owner_id
            record_id = _record_id(record, section_spec.id_field)
            source_sha = _source_sha(record)
        else:
            # Non-dict marker (e.g. a derived_applied key string).
            # Stored verbatim; workspace-scoped.
            owner = self.owner_id
            record_id = _scalar_record_id(record)
            source_sha = _scalar_sha(record)
        connection.execute(
            f"INSERT OR REPLACE INTO {section_spec.target_table} ("  # noqa: S608
            "record_id, owner_id, source_section, payload_json, "
            "source_sha256, migrated_at) VALUES (?,?,?,?,?,?)",
            (
                record_id,
                owner,
                self._section_key(section_spec),
                json.dumps(record, ensure_ascii=False, sort_keys=True),
                source_sha,
                now,
            ),
        )

    def write_via(self, connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        """Replace every section's rows with ``payload`` inside one transaction.

        For each section: delete all rows tagged with this ``source_section``,
        then upsert each record by its natural id (``INSERT OR REPLACE``).  The
        delete is scoped to ``source_section`` only — never owner — so the
        workspace-wide "one file = all records" round-trip is faithful.
        ``owner_id`` is resolved per record via the *same* owner resolution as
        the migration; a genuinely unresolvable record falls back to the
        workspace scope rather than being dropped.
        """
        section_payloads = [
            (section, [r for r in payload.get(section.section, []) if isinstance(r, dict)])
            for section in self._spec.sections
        ]
        owner_maps = build_section_owner_map(
            self._spec, section_payloads, OwnerResolver(), self.owner_id
        )
        now = _utc_now()
        for section_spec in self._spec.sections:
            section_key = self._section_key(section_spec)
            connection.execute(
                f"DELETE FROM {section_spec.target_table} WHERE source_section=?",  # noqa: S608
                (section_key,),
            )
            records = payload.get(section_spec.section, [])
            if not isinstance(records, list):
                continue
            for record in records:
                self._write_record(connection, section_spec, record, owner_maps, now)

    def append_records(
        self,
        section: str,
        records: list[Any],
        *,
        keep_newest: int | None = None,
    ) -> None:
        """Append records to one section without rewriting the others.

        Audit-style growth paths (memory access records) must not pay the
        full-replace cost of :meth:`replace_all` — a delete + re-insert of
        every section on each append turns cumulative IO quadratic as the
        audit trail grows. Appending preserves :meth:`read_via` order (new
        rows land after existing ones by ``rowid``) and keeps
        ``INSERT OR REPLACE`` idempotency for content-derived ids.

        Owner resolution follows the source spec exactly as in
        :meth:`write_via`; for the sections that grow by append
        (``DIRECT_OWNER_ID``), each record carries its own owner so no
        cross-record map is needed.

        ``keep_newest`` bounds the section's growth: after appending, the
        oldest rows beyond the newest ``keep_newest`` are deleted (by
        ``rowid``, i.e. insertion order).
        """
        section_spec = self._section_spec(section)
        if not isinstance(records, list) or not records:
            return
        owner_maps = build_section_owner_map(
            self._spec, [(section_spec, records)], OwnerResolver(), self.owner_id
        )
        now = _utc_now()
        with self.transaction() as connection:
            for record in records:
                self._write_record(connection, section_spec, record, owner_maps, now)
            if keep_newest is not None and keep_newest > 0:
                connection.execute(
                    f"DELETE FROM {section_spec.target_table} WHERE source_section=? "  # noqa: S608
                    "AND rowid IN (SELECT rowid FROM "
                    f"{section_spec.target_table} WHERE source_section=? "  # noqa: S608
                    "ORDER BY rowid DESC LIMIT -1 OFFSET ?)",
                    (
                        self._section_key(section_spec),
                        self._section_key(section_spec),
                        int(keep_newest),
                    ),
                )

    @contextmanager
    def locked(self) -> Iterator[dict[str, Any]]:
        """Lock + load, yielding the payload for in-place mutation.

        The caller mutates the yielded dict and calls :meth:`replace_all` to
        persist (mirroring the legacy ``_locked()`` + explicit ``_save()``).
        If the caller does not persist, the transaction commits empty — a
        faithful match for the legacy "lock, read, maybe write" pattern.
        """
        with self.transaction() as connection:
            yield self.read_via(connection)

    def _section_spec(self, section: str) -> SectionSpec:
        matches = [spec for spec in self._spec.sections if spec.section == section]
        if not matches:
            raise KeyError(f"unknown section for source {self._spec.name}: {section}")
        return matches[0]

    def read_section(self, section: str) -> list[Any]:
        """Read one section's records in insertion (``rowid``) order.

        A section-scoped mutation should not pay the load-every-section cost
        of :meth:`snapshot` when it only needs its own records.
        """
        section_spec = self._section_spec(section)
        section_key = self._section_key(section_spec)
        active = self._active.get()
        if active:
            rows = (
                active[-1]
                .execute(
                    f"SELECT payload_json FROM {section_spec.target_table} "  # noqa: S608 - identifier-validated at table-create time
                    "WHERE source_section=? ORDER BY rowid",
                    (section_key,),
                )
                .fetchall()
            )
        else:
            with self.transaction() as connection:
                rows = connection.execute(
                    f"SELECT payload_json FROM {section_spec.target_table} "  # noqa: S608
                    "WHERE source_section=? ORDER BY rowid",
                    (section_key,),
                ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def replace_section(self, section: str, records: list[Any]) -> None:
        """Atomically rewrite ONE section, leaving sibling sections untouched.

        ``replace_all`` deletes and re-inserts every section of the source —
        correct for whole-payload round-trips, quadratic when a caller only
        mutates one section on every write (personalization profiles,
        sessions, signals). This rewrites just the named section inside one
        transaction, reusing the same per-record id/owner/sha logic.
        """
        section_spec = self._section_spec(section)
        if not isinstance(records, list):
            raise TypeError(f"section records must be a list, got {type(records).__name__}")
        owner_maps = build_section_owner_map(
            self._spec, [(section_spec, records)], OwnerResolver(), self.owner_id
        )
        now = _utc_now()
        with self.transaction() as connection:
            connection.execute(
                f"DELETE FROM {section_spec.target_table} WHERE source_section=?",  # noqa: S608
                (self._section_key(section_spec),),
            )
            for record in records:
                self._write_record(connection, section_spec, record, owner_maps, now)

    def snapshot(self) -> dict[str, Any]:
        """Read the current payload in its own short transaction.

        Reuses an already-active transaction on this instance if one is held,
        so a ``_load()`` invoked inside a ``_locked()`` block does not open a
        nested connection (which would deadlock on the write lock).
        """
        active = self._active.get()
        if active:
            return self.read_via(active[-1])
        with self.transaction() as connection:
            return self.read_via(connection)

    def replace_all(self, payload: dict[str, Any]) -> None:
        """Persist ``payload``, reusing the active transaction if one is held."""
        active = self._active.get()
        if active:
            self.write_via(active[-1], payload)
            return
        with self.transaction() as connection:
            self.write_via(connection, payload)


__all__ = ["SectionedRecordStore"]
