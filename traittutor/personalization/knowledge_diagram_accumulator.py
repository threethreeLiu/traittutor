"""Accumulate inline TraitTutor learning artifacts into the learner graph.

Knowledge diagrams, learning explorations, and guided solves are generated
inside chat as auditable candidates.  They can enrich the source-grounded
knowledge graph, but they deliberately do not write BKT/mastery observations;
mastery only changes after gradable practice.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Any, Mapping

from traittutor.services.path_service import get_path_service

from .graph_repository import LearningKnowledgeGraphRepository
from .models import (
    KnowledgeGraphEdge,
    KnowledgeGraphNode,
    LearningKnowledgeGraph,
    SubjectRef,
)

_SUPPORTED_VERSIONS = {
    "traittutor.knowledge_diagram.v1",
    "traittutor.learning_exploration.v1",
    "traittutor.guided_solve.v1",
}
_FENCE_RE = re.compile(
    r"```[ \t]*(?:traittutor-knowledge-graph|traittutor-learning-exploration|traittutor-guided-solve)[^\n]*\n(?P<body>.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_EDGE_RELATION_MAP = {
    "prerequisite": "prerequisite",
    "part_of": "part_of",
    "causes": "related_to",
    "contrasts": "related_to",
    "applies_to": "related_to",
    "explains": "related_to",
    "related_to": "related_to",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(value: str, *, fallback: str = "concept") -> str:
    slug = re.sub(r"[^\w]+", "-", str(value or "").lower(), flags=re.UNICODE).strip("-")
    return slug or fallback


def _repository() -> LearningKnowledgeGraphRepository:
    return LearningKnowledgeGraphRepository(get_path_service().get_traittutor_database_path())


def _json_objects_from_message(content: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for match in _FENCE_RE.finditer(content or ""):
        try:
            value = json.loads(match.group("body").strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("version") in _SUPPORTED_VERSIONS:
            payloads.append(value)
    return payloads


def _subject_from_payload(payload: Mapping[str, Any]) -> SubjectRef:
    raw_subject = payload.get("subject")
    if isinstance(raw_subject, Mapping):
        label = str(raw_subject.get("label") or "").strip()
        grade = str(raw_subject.get("grade") or "").strip()
        confidence_value = raw_subject.get("confidence", 0.55)
        try:
            confidence = float(confidence_value)
        except (TypeError, ValueError):
            confidence = 0.55
    else:
        label = ""
        grade = ""
        confidence = 0.55
    title = str(payload.get("title") or "").strip()
    label = label or title or "General Knowledge"
    path = [label]
    if grade:
        path.append(grade)
    return SubjectRef(
        subject_id=_slug(label, fallback="general-knowledge"),
        label=label[:120],
        path=path[:6],
        confidence=max(0.0, min(1.0, confidence)),
        source="artifact",
        confirmed=False,
    )


def _concept_id(subject_id: str, raw_id: str, label: str) -> str:
    leaf = _slug(raw_id or label, fallback="concept")
    if leaf.startswith(f"{subject_id}."):
        return leaf[:160]
    return f"{subject_id}.{leaf}"[:160]


def _evidence(value: Any, fallback: str) -> list[str]:
    if isinstance(value, list):
        refs = [str(item).strip()[:120] for item in value if str(item or "").strip()]
        if refs:
            return refs[:4]
    return [fallback[:120]]


def _graph_from_payload(
    payload: Mapping[str, Any], *, source_ref: str
) -> LearningKnowledgeGraph | None:
    subject = _subject_from_payload(payload)
    fallback_evidence = f"chat:{source_ref}"
    raw_nodes = payload.get("nodes") or payload.get("concepts")
    if not isinstance(raw_nodes, list):
        return None

    id_map: dict[str, str] = {}
    nodes: list[KnowledgeGraphNode] = []
    for index, raw_node in enumerate(raw_nodes[:80]):
        if not isinstance(raw_node, Mapping):
            continue
        raw_id = str(raw_node.get("id") or f"node-{index + 1}").strip()
        label = str(raw_node.get("label") or raw_id).strip()
        if not label:
            continue
        concept_id = _concept_id(subject.subject_id, raw_id, label)
        id_map[raw_id] = concept_id
        module_label = str(
            raw_node.get("module")
            or payload.get("artifact_type")
            or raw_node.get("type")
            or "Learning Artifact"
        ).strip()
        module_id = _slug(module_label, fallback="knowledge-diagram")
        nodes.append(
            KnowledgeGraphNode(
                concept_id=concept_id,
                label=label[:160],
                module_id=module_id[:160],
                module_label=module_label[:160],
                evidence_chunk_ids=_evidence(raw_node.get("evidence"), fallback_evidence),
                confidence=0.55,
            )
        )
    if not nodes:
        return None

    raw_edges = payload.get("edges")
    edges: list[KnowledgeGraphEdge] = []
    if isinstance(raw_edges, list):
        for raw_edge in raw_edges[:180]:
            if not isinstance(raw_edge, Mapping):
                continue
            source = id_map.get(str(raw_edge.get("source") or "").strip())
            target = id_map.get(str(raw_edge.get("target") or "").strip())
            if not source or not target or source == target:
                continue
            relation = _EDGE_RELATION_MAP.get(
                str(raw_edge.get("relation") or "").strip(), "related_to"
            )
            edges.append(
                KnowledgeGraphEdge(
                    source_concept_id=source,
                    target_concept_id=target,
                    relation=relation,  # type: ignore[arg-type]
                    evidence_chunk_ids=_evidence(raw_edge.get("evidence"), fallback_evidence),
                    confidence=0.5,
                )
            )

    return LearningKnowledgeGraph(
        subject=subject,
        nodes=nodes,
        edges=edges,
        source_refs=[source_ref],
        updated_at=_now(),
    )


def accumulate_knowledge_diagram_message(
    content: str,
    *,
    session_id: str | None = None,
    message_id: int | str | None = None,
    repository: LearningKnowledgeGraphRepository | None = None,
) -> int:
    """Merge every valid inline TraitTutor artifact JSON fence into the KG.

    Returns the number of diagrams merged.  Invalid or incomplete fences are
    ignored so chat persistence remains the source of truth and the user never
    loses an answer because accumulation failed.
    """
    source_ref = f"chat:{session_id or 'unknown'}:{message_id or 'unsaved'}"
    repo = repository or _repository()
    merged = 0
    for payload in _json_objects_from_message(content):
        graph = _graph_from_payload(payload, source_ref=source_ref)
        if graph is None:
            continue
        repo.merge(graph, source_ref=source_ref)
        merged += 1
    return merged


__all__ = ["accumulate_knowledge_diagram_message"]
