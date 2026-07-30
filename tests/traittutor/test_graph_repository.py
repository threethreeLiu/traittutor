from __future__ import annotations

from traittutor.personalization.graph_repository import LearningKnowledgeGraphRepository
from traittutor.personalization.models import KnowledgeGraphEdge, KnowledgeGraphNode, LearningKnowledgeGraph, SubjectRef


def _graph(label: str, *, include_edge: bool = False) -> LearningKnowledgeGraph:
    subject = SubjectRef(subject_id="physics", label="物理", path=["科学", "物理"], confidence=1, source="user", confirmed=True)
    nodes = [KnowledgeGraphNode(concept_id="physics.force", label="力", module_id="mechanics", module_label="力学", evidence_chunk_ids=["c1"], confidence=.8)]
    if include_edge:
        nodes.append(KnowledgeGraphNode(concept_id="physics.newtons-second-law", label=label, module_id="mechanics", module_label="力学", evidence_chunk_ids=["c2"], confidence=.9))
    edges = [KnowledgeGraphEdge(source_concept_id="physics.force", target_concept_id="physics.newtons-second-law", relation="prerequisite", evidence_chunk_ids=["c1", "c2"], confidence=.85)] if include_edge else []
    return LearningKnowledgeGraph(subject=subject, nodes=nodes, edges=edges, source_refs=[], updated_at="2026-07-29T00:00:00+00:00")


def test_repository_merges_sources_and_preserves_evidence(tmp_path):
    repository = LearningKnowledgeGraphRepository(tmp_path / "learner" / "knowledge-graph.sqlite3")
    repository.merge(_graph("牛顿第二定律"), source_ref="analysis:a")
    graph = repository.merge(_graph("牛顿第二定律", include_edge=True), source_ref="analysis:b")
    assert {node.concept_id for node in graph.nodes} == {"physics.force", "physics.newtons-second-law"}
    assert graph.edges[0].relation == "prerequisite"
    assert set(graph.source_refs) == {"analysis:a", "analysis:b"}
    assert repository.load("physics") is not None


def test_repository_resolves_material_chunk_to_canonical_concept(tmp_path):
    repository = LearningKnowledgeGraphRepository(tmp_path / "learner" / "knowledge-graph.sqlite3")
    repository.merge(_graph("牛顿第二定律", include_edge=True), source_ref="analysis:a")

    assert repository.concept_for_source_node("physics", "c2") == (
        "physics.newtons-second-law",
        "牛顿第二定律",
        "mechanics",
    )
    assert repository.concept_for_source_node("physics", "physics.force") == (
        "physics.force",
        "力",
        "mechanics",
    )
