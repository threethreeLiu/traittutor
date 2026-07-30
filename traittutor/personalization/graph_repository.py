"""Storage boundary for the learner knowledge graph.

The product currently uses SQLite because it is local-first and supports
atomic merges.  Callers depend only on this repository API, so a hosted SQL or
graph-database adapter can replace it without changing graph extraction/BKT.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Iterator

from .models import LearningKnowledgeGraph


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LearningKnowledgeGraphRepository:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 10000")
            self._migrate(connection)
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS graph_subjects (
            subject_id TEXT PRIMARY KEY, subject_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS graph_versions (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_id TEXT NOT NULL,
            source_ref TEXT NOT NULL, graph_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
            FOREIGN KEY(subject_id) REFERENCES graph_subjects(subject_id)
        );
        CREATE TABLE IF NOT EXISTS graph_modules (
            subject_id TEXT NOT NULL, module_id TEXT NOT NULL, label TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(subject_id, module_id),
            FOREIGN KEY(subject_id) REFERENCES graph_subjects(subject_id)
        );
        CREATE TABLE IF NOT EXISTS graph_concepts (
            subject_id TEXT NOT NULL, concept_id TEXT NOT NULL, label TEXT NOT NULL,
            module_id TEXT NOT NULL, confidence REAL NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(subject_id, concept_id),
            FOREIGN KEY(subject_id) REFERENCES graph_subjects(subject_id)
        );
        CREATE TABLE IF NOT EXISTS graph_edges (
            subject_id TEXT NOT NULL, source_concept_id TEXT NOT NULL, target_concept_id TEXT NOT NULL,
            relation TEXT NOT NULL, confidence REAL NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(subject_id, source_concept_id, target_concept_id, relation),
            FOREIGN KEY(subject_id) REFERENCES graph_subjects(subject_id)
        );
        CREATE TABLE IF NOT EXISTS graph_evidence (
            subject_id TEXT NOT NULL, entity_kind TEXT NOT NULL, entity_key TEXT NOT NULL,
            source_ref TEXT NOT NULL, chunk_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(subject_id, entity_kind, entity_key, source_ref, chunk_id),
            FOREIGN KEY(subject_id) REFERENCES graph_subjects(subject_id)
        );
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(graph_versions)")}
        if "graph_json" not in columns:
            connection.execute("ALTER TABLE graph_versions ADD COLUMN graph_json TEXT NOT NULL DEFAULT '{}'")

    def merge(self, graph: LearningKnowledgeGraph, *, source_ref: str) -> LearningKnowledgeGraph:
        """Atomically add evidence-backed nodes/edges and return the merged tree."""
        subject_id = graph.subject.subject_id
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO graph_subjects(subject_id, subject_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(subject_id) DO UPDATE SET subject_json=excluded.subject_json, updated_at=excluded.updated_at",
                (subject_id, graph.subject.model_dump_json(), timestamp),
            )
            connection.execute(
                "INSERT INTO graph_versions(subject_id, source_ref, graph_json, created_at) VALUES (?, ?, ?, ?)",
                (subject_id, source_ref, graph.model_dump_json(), timestamp),
            )
            for node in graph.nodes:
                connection.execute(
                    "INSERT INTO graph_modules(subject_id, module_id, label, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(subject_id, module_id) DO UPDATE SET label=excluded.label, updated_at=excluded.updated_at",
                    (subject_id, node.module_id, node.module_label, timestamp),
                )
                connection.execute(
                    "INSERT INTO graph_concepts(subject_id, concept_id, label, module_id, confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(subject_id, concept_id) DO UPDATE SET label=excluded.label, module_id=excluded.module_id, confidence=MAX(graph_concepts.confidence, excluded.confidence), updated_at=excluded.updated_at",
                    (subject_id, node.concept_id, node.label, node.module_id, node.confidence, timestamp),
                )
                for chunk_id in node.evidence_chunk_ids:
                    connection.execute("INSERT OR IGNORE INTO graph_evidence VALUES (?, 'node', ?, ?, ?, ?)", (subject_id, node.concept_id, source_ref, chunk_id, timestamp))
            for edge in graph.edges:
                key = f"{edge.source_concept_id}|{edge.target_concept_id}|{edge.relation}"
                connection.execute(
                    "INSERT INTO graph_edges(subject_id, source_concept_id, target_concept_id, relation, confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(subject_id, source_concept_id, target_concept_id, relation) DO UPDATE SET confidence=MAX(graph_edges.confidence, excluded.confidence), updated_at=excluded.updated_at",
                    (subject_id, edge.source_concept_id, edge.target_concept_id, edge.relation, edge.confidence, timestamp),
                )
                for chunk_id in edge.evidence_chunk_ids:
                    connection.execute("INSERT OR IGNORE INTO graph_evidence VALUES (?, 'edge', ?, ?, ?, ?)", (subject_id, key, source_ref, chunk_id, timestamp))
        return self.load(subject_id) or graph

    def load(self, subject_id: str) -> LearningKnowledgeGraph | None:
        with self._connect() as connection:
            subject_row = connection.execute("SELECT subject_json, updated_at FROM graph_subjects WHERE subject_id = ?", (subject_id,)).fetchone()
            if subject_row is None:
                return None
            from .models import KnowledgeGraphEdge, KnowledgeGraphNode, SubjectRef
            subject = SubjectRef.model_validate_json(subject_row["subject_json"])
            modules = {row["module_id"]: row["label"] for row in connection.execute("SELECT module_id, label FROM graph_modules WHERE subject_id = ?", (subject_id,))}
            evidence_rows = connection.execute("SELECT entity_kind, entity_key, source_ref, chunk_id FROM graph_evidence WHERE subject_id = ?", (subject_id,)).fetchall()
            evidence: dict[tuple[str, str], list[str]] = {}; source_refs: list[str] = []
            for row in evidence_rows:
                evidence.setdefault((row["entity_kind"], row["entity_key"]), []).append(row["chunk_id"])
                source_refs.append(row["source_ref"])
            nodes = [KnowledgeGraphNode(concept_id=row["concept_id"], label=row["label"], module_id=row["module_id"], module_label=modules.get(row["module_id"], row["module_id"]), evidence_chunk_ids=evidence.get(("node", row["concept_id"]), ["legacy"]), confidence=row["confidence"]) for row in connection.execute("SELECT concept_id, label, module_id, confidence FROM graph_concepts WHERE subject_id = ? ORDER BY module_id, label", (subject_id,))]
            edges = [KnowledgeGraphEdge(source_concept_id=row["source_concept_id"], target_concept_id=row["target_concept_id"], relation=row["relation"], evidence_chunk_ids=evidence.get(("edge", f"{row['source_concept_id']}|{row['target_concept_id']}|{row['relation']}"), ["legacy"]), confidence=row["confidence"]) for row in connection.execute("SELECT source_concept_id, target_concept_id, relation, confidence FROM graph_edges WHERE subject_id = ?", (subject_id,))]
            return LearningKnowledgeGraph(subject=subject, nodes=nodes, edges=edges, source_refs=list(dict.fromkeys(source_refs)), updated_at=subject_row["updated_at"])

    def concept_for_source_node(self, subject_id: str, source_node_id: str) -> tuple[str, str, str | None] | None:
        """Resolve a generator's chunk/node id to the canonical graph concept."""
        with self._connect() as connection:
            direct = connection.execute(
                "SELECT concept_id, label, module_id FROM graph_concepts WHERE subject_id = ? AND concept_id = ?",
                (subject_id, source_node_id),
            ).fetchone()
            if direct is None:
                direct = connection.execute(
                    "SELECT concept_id, label, module_id FROM graph_concepts WHERE subject_id = ? "
                    "AND concept_id IN (SELECT entity_key FROM graph_evidence WHERE subject_id = ? AND entity_kind = 'node' AND chunk_id = ?) "
                    "ORDER BY confidence DESC, concept_id LIMIT 1",
                    (subject_id, subject_id, source_node_id),
                ).fetchone()
            if direct is None:
                return None
            return str(direct["concept_id"]), str(direct["label"]), str(direct["module_id"] or "") or None
