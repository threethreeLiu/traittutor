"""Source-grounded learner knowledge graphs, separate from BKT state."""
from __future__ import annotations

from datetime import UTC, datetime
import asyncio
import logging
from pathlib import Path
from typing import Any, Mapping

from traittutor.services.path_service import get_path_service
from .models import KnowledgeGraphEdge, KnowledgeGraphNode, LearningKnowledgeGraph, SubjectRef
from .graph_repository import LearningKnowledgeGraphRepository

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _repository() -> LearningKnowledgeGraphRepository:
    return LearningKnowledgeGraphRepository(get_path_service().get_workspace_dir() / "learner" / "knowledge-graph.sqlite3")


def _legacy_graph_path(subject_id: str) -> Path:
    if not subject_id or subject_id in {".", ".."} or not subject_id.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise ValueError("invalid subject id")
    return get_path_service().get_workspace_dir() / "traittutor" / "learner-knowledge-graphs" / f"{subject_id}.json"


def _validate_graph(payload: Mapping[str, Any], chunk_ids: set[str]) -> None:
    nodes = [KnowledgeGraphNode.model_validate(value) for value in payload.get("nodes", [])]
    node_ids = {node.concept_id for node in nodes}
    if len(nodes) != len(node_ids):
        raise ValueError("duplicate concept ids")
    for node in nodes:
        if not set(node.evidence_chunk_ids).issubset(chunk_ids):
            raise ValueError("node evidence is not grounded in material")
    for raw_edge in payload.get("edges", []):
        edge = KnowledgeGraphEdge.model_validate(raw_edge)
        if edge.source_concept_id not in node_ids or edge.target_concept_id not in node_ids:
            raise ValueError("edge references an unknown concept")
        if edge.source_concept_id == edge.target_concept_id:
            raise ValueError("self edge")
        if not set(edge.evidence_chunk_ids).issubset(chunk_ids):
            raise ValueError("edge evidence is not grounded in material")
    prerequisites = [(item["source_concept_id"], item["target_concept_id"])
                     for item in payload.get("edges", []) if item.get("relation") == "prerequisite"]
    adjacency: dict[str, list[str]] = {}
    for source, target in prerequisites:
        adjacency.setdefault(source, []).append(target)
    visiting: set[str] = set(); visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting: raise ValueError("prerequisite cycle")
        if node_id in visited: return
        visiting.add(node_id)
        for child in adjacency.get(node_id, []): visit(child)
        visiting.remove(node_id); visited.add(node_id)
    for node_id in adjacency: visit(node_id)


async def extract_learning_knowledge_graph(*, subject: SubjectRef, chunks: list[Mapping[str, Any]], source_ref: str) -> LearningKnowledgeGraph | None:
    """Extract a bounded candidate graph through the gateway; failures are non-blocking."""
    compact_chunks = [{"chunk_id": str(item.get("chunk_id") or ""), "text": str(item.get("text") or "")[:1800]} for item in chunks[:16]]
    chunk_ids = {item["chunk_id"] for item in compact_chunks if item["chunk_id"]}
    if not chunk_ids:
        return None
    # Import at execution time to avoid making the learner domain depend on
    # the generation package's module initialisation order.
    from traittutor.generate.catalog import load_prompt
    from traittutor.generate.runner import (
        GenerationConfigurationError,
        GenerationModelExhaustedError,
        run_structured_prompt,
    )
    prompt = load_prompt("analysis/learning-knowledge-graph.md", {
        "subject_json": subject.model_dump(), "material_chunks_json": compact_chunks,
    })
    try:
        payload, _ = await run_structured_prompt(
            prompt, validate=lambda value: _validate_graph(value, chunk_ids), reasoning_effort="medium",
        )
    except (GenerationConfigurationError, GenerationModelExhaustedError, ValueError):
        return None
    graph = LearningKnowledgeGraph(
        subject=subject,
        nodes=[KnowledgeGraphNode.model_validate(item) for item in payload.get("nodes", [])],
        edges=[KnowledgeGraphEdge.model_validate(item) for item in payload.get("edges", [])],
        source_refs=[source_ref], updated_at=_now(),
    )
    return _repository().merge(graph, source_ref=source_ref)


def resolve_graph_concept(subject_id: str, source_node_id: str) -> tuple[str, str, str | None] | None:
    """Return the canonical BKT concept for a source chunk or graph node."""
    return _repository().concept_for_source_node(subject_id, source_node_id)


def schedule_learning_knowledge_graph(*, subject: SubjectRef, chunks: list[Mapping[str, Any]], source_ref: str) -> None:
    """Build graph evidence after the response path; failures never affect generation."""
    async def _build() -> None:
        try:
            graph = await extract_learning_knowledge_graph(subject=subject, chunks=chunks, source_ref=source_ref)
            if graph is not None:
                from .service import get_personalization_service
                get_personalization_service().reconcile_graph_concepts(subject, [node.model_dump() for node in graph.nodes])
        except Exception:
            logger.exception("background learner knowledge graph build failed")

    asyncio.create_task(_build(), name=f"traittutor-knowledge-graph-{subject.subject_id}")


def load_learning_knowledge_graph(subject_id: str) -> LearningKnowledgeGraph | None:
    repository = _repository()
    graph = repository.load(subject_id)
    if graph is not None:
        return graph
    # One-time, non-destructive migration from the short-lived JSON prototype.
    # Keep the file intact for audit/recovery; future reads use SQLite only.
    legacy = _legacy_graph_path(subject_id)
    if not legacy.exists():
        return None
    try:
        imported = LearningKnowledgeGraph.model_validate_json(legacy.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return repository.merge(imported, source_ref="legacy-json-migration")
