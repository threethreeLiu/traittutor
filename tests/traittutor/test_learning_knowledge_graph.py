from __future__ import annotations

import pytest

from traittutor.personalization.knowledge_graph import _validate_graph


def _graph(edges):
    return {
        "nodes": [
            {"concept_id": "physics.force", "label": "力", "module_id": "mechanics", "module_label": "力学", "evidence_chunk_ids": ["c1"], "confidence": .9},
            {"concept_id": "physics.newtons-second-law", "label": "牛顿第二定律", "module_id": "mechanics", "module_label": "力学", "evidence_chunk_ids": ["c2"], "confidence": .9},
        ],
        "edges": edges,
    }


def test_grounded_acyclic_prerequisite_graph_is_accepted():
    _validate_graph(_graph([{
        "source_concept_id": "physics.force", "target_concept_id": "physics.newtons-second-law",
        "relation": "prerequisite", "evidence_chunk_ids": ["c1", "c2"], "confidence": .8,
    }]), {"c1", "c2"})


def test_graph_rejects_ungrounded_edges_and_prerequisite_cycles():
    with pytest.raises(ValueError, match="grounded"):
        _validate_graph(_graph([{
            "source_concept_id": "physics.force", "target_concept_id": "physics.newtons-second-law",
            "relation": "prerequisite", "evidence_chunk_ids": ["missing"], "confidence": .8,
        }]), {"c1", "c2"})
    with pytest.raises(ValueError, match="cycle"):
        _validate_graph(_graph([
            {"source_concept_id": "physics.force", "target_concept_id": "physics.newtons-second-law", "relation": "prerequisite", "evidence_chunk_ids": ["c1"], "confidence": .8},
            {"source_concept_id": "physics.newtons-second-law", "target_concept_id": "physics.force", "relation": "prerequisite", "evidence_chunk_ids": ["c2"], "confidence": .8},
        ]), {"c1", "c2"})
