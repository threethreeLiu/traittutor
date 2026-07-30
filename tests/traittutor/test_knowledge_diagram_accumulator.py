from __future__ import annotations

from traittutor.personalization.graph_repository import LearningKnowledgeGraphRepository
from traittutor.personalization.knowledge_diagram_accumulator import (
    accumulate_knowledge_diagram_message,
)


def test_inline_knowledge_diagram_merges_as_graph_candidate(tmp_path):
    repository = LearningKnowledgeGraphRepository(
        tmp_path / "learner" / "knowledge-graph.sqlite3"
    )
    content = """
这是图解总览。

```traittutor-knowledge-graph
{
  "version": "traittutor.knowledge_diagram.v1",
  "title": "牛顿第二定律知识图解",
  "subject": {"label": "物理", "grade": "八年级", "confidence": 0.82},
  "nodes": [
    {"id": "force", "label": "力", "type": "concept", "module": "力学", "evidence": ["F 表示力"]},
    {"id": "newtons-second-law", "label": "牛顿第二定律", "type": "principle", "module": "力学", "evidence": ["F=ma"]}
  ],
  "edges": [
    {"source": "force", "target": "newtons-second-law", "relation": "prerequisite", "evidence": ["先理解力"]}
  ],
  "accumulation": {
    "knowledge_graph": "candidate",
    "bkt": "no_mastery_update",
    "memory": "chat_history_evidence"
  }
}
```
"""

    merged = accumulate_knowledge_diagram_message(
        content,
        session_id="s1",
        message_id=12,
        repository=repository,
    )

    assert merged == 1
    graph = repository.load("物理")
    assert graph is not None
    assert graph.subject.source == "artifact"
    assert graph.subject.confirmed is False
    assert {node.concept_id for node in graph.nodes} == {
        "物理.force",
        "物理.newtons-second-law",
    }
    assert graph.edges[0].relation == "prerequisite"
    assert "chat:s1:12" in graph.source_refs


def test_inline_knowledge_diagram_ignores_invalid_or_old_payloads(tmp_path):
    repository = LearningKnowledgeGraphRepository(
        tmp_path / "learner" / "knowledge-graph.sqlite3"
    )

    merged = accumulate_knowledge_diagram_message(
        """
```traittutor-knowledge-graph
{"version": "old.visualize.v1", "title": "旧图", "nodes": [], "edges": []}
```
""",
        repository=repository,
    )

    assert merged == 0
    assert repository.load("旧图") is None


def test_learning_exploration_and_guided_solve_artifacts_accumulate(tmp_path):
    repository = LearningKnowledgeGraphRepository(
        tmp_path / "learner" / "knowledge-graph.sqlite3"
    )
    content = """
```traittutor-learning-exploration
{
  "version": "traittutor.learning_exploration.v1",
  "artifact_type": "learning_exploration",
  "title": "函数学习探索",
  "subject": {"label": "数学", "grade": "七年级", "confidence": 0.8},
  "nodes": [
    {"id": "function", "label": "函数", "module": "函数基础", "evidence": ["函数描述变量关系"]},
    {"id": "domain", "label": "定义域", "module": "函数基础", "evidence": ["自变量可取范围"]}
  ],
  "edges": [
    {"source": "domain", "target": "function", "relation": "part_of", "evidence": ["定义域是函数组成部分"]}
  ],
  "accumulation": {"knowledge_graph": "candidate", "bkt": "no_mastery_update", "memory": "chat_history_evidence"}
}
```

```traittutor-guided-solve
{
  "version": "traittutor.guided_solve.v1",
  "artifact_type": "guided_solve",
  "title": "一次函数求斜率",
  "subject": {"label": "数学", "grade": "七年级", "confidence": 0.8},
  "nodes": [
    {"id": "slope", "label": "斜率", "module": "一次函数", "evidence": ["斜率表示变化率"]}
  ],
  "edges": [
    {"source": "function", "target": "slope", "relation": "related_to", "evidence": ["一次函数中出现斜率"]}
  ],
  "answer": "k = 2",
  "accumulation": {"knowledge_graph": "candidate", "bkt": "no_mastery_update", "memory": "chat_history_evidence"}
}
```
"""

    merged = accumulate_knowledge_diagram_message(
        content,
        session_id="s2",
        message_id=20,
        repository=repository,
    )

    assert merged == 2
    graph = repository.load("数学")
    assert graph is not None
    assert {"数学.function", "数学.domain", "数学.slope"}.issubset(
        {node.concept_id for node in graph.nodes}
    )
    assert "chat:s2:20" in graph.source_refs
