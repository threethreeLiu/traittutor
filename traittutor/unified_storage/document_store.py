"""Small JSON-document records stored inside the canonical SQLite database.

This store is for structured singleton configuration/state that does not need a
full domain table.  Files and generated artefacts remain on the filesystem;
their mutable metadata belongs here.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from traittutor.services.path_service import PathService

from .store import UnifiedStore, _utc_now


class SQLiteDocumentStore:
    """Owner-bound key/value JSON documents in the unified database."""

    def __init__(
        self,
        owner_id: str,
        *,
        namespace: str,
        path_service: PathService | None = None,
        db_path: Path | None = None,
    ) -> None:
        normalized_namespace = namespace.strip()
        if not normalized_namespace:
            raise ValueError("namespace is required")
        self.owner_id = owner_id
        self.namespace = normalized_namespace
        self._store = UnifiedStore(owner_id, path_service=path_service, db_path=db_path)

    @property
    def db_path(self) -> Path:
        return self._store.db_path

    @staticmethod
    def _ensure_table(connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_documents (
                owner_id TEXT NOT NULL,
                namespace TEXT NOT NULL,
                document_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (owner_id, namespace, document_key)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_documents_namespace "
            "ON runtime_documents(owner_id, namespace)"
        )

    def load(self, key: str, default: Any = None) -> Any:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("document key is required")
        with self._store.transaction() as connection:
            self._ensure_table(connection)
            row = connection.execute(
                "SELECT payload_json FROM runtime_documents "
                "WHERE owner_id=? AND namespace=? AND document_key=?",
                (self.owner_id, self.namespace, normalized_key),
            ).fetchone()
        if row is None:
            return deepcopy(default)
        return json.loads(str(row["payload_json"]))

    def save(self, key: str, payload: Any) -> Any:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("document key is required")
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._store.transaction() as connection:
            self._ensure_table(connection)
            connection.execute(
                """
                INSERT INTO runtime_documents(
                    owner_id, namespace, document_key, payload_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, namespace, document_key) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (self.owner_id, self.namespace, normalized_key, serialized, _utc_now()),
            )
        return deepcopy(payload)

    def delete(self, key: str) -> bool:
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError("document key is required")
        with self._store.transaction() as connection:
            self._ensure_table(connection)
            cursor = connection.execute(
                "DELETE FROM runtime_documents WHERE owner_id=? AND namespace=? AND document_key=?",
                (self.owner_id, self.namespace, normalized_key),
            )
            return cursor.rowcount > 0


__all__ = ["SQLiteDocumentStore"]
