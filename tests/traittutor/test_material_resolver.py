from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from traittutor.generate.materials import (
    MaterialReference,
    MaterialResolutionError,
    MaterialResolver,
)
from traittutor.knowledge.manager import KnowledgeBaseManager
from traittutor.services.notebook.service import NotebookManager
from traittutor.api.routers.traittutor_generate import AnalyzeMaterialRequest
from traittutor.generate import material_analysis
from traittutor.generate.material_analysis import (
    ANALYSIS_MAX_PAGE_SLICES,
    ANALYSIS_MAX_TEXT_CHARS,
    ANALYSIS_QUOTA_MAX_REQUESTS,
    MaterialAnalysis,
    MaterialAnalysisRateLimitError,
    consume_material_analysis_quota,
    load_material_analysis,
    save_material_analysis,
    search_learning_sources,
)


def test_paste_material_has_stable_chunk_ids_and_traceable_citation() -> None:
    reference = MaterialReference(
        source_type="paste",
        title="Marketing notes",
        text="First principle explains the goal.\n\nSecond principle explains the constraint.",
    )
    resolver = MaterialResolver(chunk_chars=128)

    first = resolver.resolve(reference)
    second = resolver.resolve(reference)

    assert first.source_id.startswith("paste-")
    assert [chunk.chunk_id for chunk in first.chunks] == [chunk.chunk_id for chunk in second.chunks]
    chunk = first.chunks[0]
    assert chunk.source_type == "paste"
    assert chunk.title == "Marketing notes"
    assert chunk.excerpt == chunk.text
    assert chunk.citation.source_id == first.source_id
    assert chunk.citation.locator["kind"] == "pasted_text"


def test_knowledge_material_uses_existing_manager_and_raw_file(tmp_path: Path) -> None:
    manager = KnowledgeBaseManager(base_dir=str(tmp_path / "knowledge"))
    raw_dir = manager.base_dir / "learning" / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "lesson.md").write_text("# Topic\n\nGrounded knowledge text.", encoding="utf-8")
    resolver = MaterialResolver(knowledge_manager=manager, chunk_chars=128)

    result = resolver.resolve(
        MaterialReference(
            source_type="knowledge",
            source_id="learning",
            title="Selected knowledge",
            metadata={"file_path": "lesson.md"},
        )
    )

    assert result.source_type == "knowledge"
    assert result.source_id == "learning"
    assert result.chunks[0].source_id == "learning:lesson.md"
    assert result.chunks[0].citation.locator["path"] == "lesson.md"
    assert "Grounded knowledge text" in result.chunks[0].text


def test_knowledge_material_rejects_path_traversal(tmp_path: Path) -> None:
    manager = KnowledgeBaseManager(base_dir=str(tmp_path / "knowledge"))
    raw_dir = manager.base_dir / "learning" / "raw"
    raw_dir.mkdir(parents=True)
    (tmp_path / "outside.md").write_text("not available", encoding="utf-8")
    resolver = MaterialResolver(knowledge_manager=manager)

    with pytest.raises(MaterialResolutionError, match="stay inside"):
        resolver.resolve(
            MaterialReference(
                source_type="knowledge",
                source_id="learning",
                metadata={"file_path": "../../outside.md"},
            )
        )


def test_notebook_material_uses_existing_notebook_records(tmp_path: Path) -> None:
    manager = NotebookManager(base_dir=str(tmp_path / "notebooks"))
    notebook = manager.create_notebook("Learning notebook")
    record = manager.add_record(
        notebook_ids=[notebook["id"]],
        record_type="chat",
        title="Cell biology",
        user_query="Explain cells",
        output="Cells contain membranes and organelles.",
    )["record"]
    resolver = MaterialResolver(notebook_manager=manager, chunk_chars=128)

    result = resolver.resolve(
        MaterialReference(
            source_type="notebook",
            source_id=notebook["id"],
            metadata={"record_id": record["id"]},
        )
    )

    assert result.title == "Learning notebook"
    assert result.chunks[0].source_id == f"{notebook['id']}:{record['id']}"
    assert result.chunks[0].title == "Cell biology"
    assert result.chunks[0].citation.locator["record_id"] == record["id"]


class _AttachmentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[dict[str, str]] = []

    def resolve_path(self, *, session_id: str, attachment_id: str, filename: str) -> Path:
        self.calls.append(
            {
                "session_id": session_id,
                "attachment_id": attachment_id,
                "filename": filename,
            }
        )
        return self.path


def test_upload_material_resolves_via_attachment_store(tmp_path: Path) -> None:
    path = tmp_path / "uploaded.txt"
    path.write_text("An uploaded source is resolved through the attachment store.", encoding="utf-8")
    store = _AttachmentStore(path)
    resolver = MaterialResolver(attachment_store=store, chunk_chars=128)

    result = resolver.resolve(
        MaterialReference(
            source_type="upload",
            source_id="attachment-1",
            metadata={"session_id": "session-1", "filename": "uploaded.txt"},
        )
    )

    assert store.calls == [
        {"session_id": "session-1", "attachment_id": "attachment-1", "filename": "uploaded.txt"}
    ]
    assert result.chunks[0].source_id == "attachment-1"
    assert result.chunks[0].citation.locator["session_id"] == "session-1"
    assert "attachment store" in result.chunks[0].text


def test_upload_material_requires_session_for_store_lookup() -> None:
    resolver = MaterialResolver(attachment_store=object())

    with pytest.raises(MaterialResolutionError, match="metadata.session_id"):
        resolver.resolve(MaterialReference(source_type="upload", source_id="attachment-1"))


def test_upload_pdf_page_slices_keep_page_citations() -> None:
    resolver = MaterialResolver(chunk_chars=128)

    result = resolver.resolve(
        MaterialReference(
            source_type="upload",
            source_id="material-1",
            title="Lesson.pdf",
            metadata={
                "filename": "Lesson.pdf",
                "converted_to_pdf": True,
                "page_slices": [
                    {"page": 1, "text": "First PDF page explains the foundation."},
                    {"page": 2, "text": "Second PDF page adds an example."},
                ],
            },
        )
    )

    assert len(result.chunks) == 2
    assert result.chunks[0].citation.locator["page"] == 1
    assert result.chunks[1].citation.locator["page"] == 2
    assert result.chunks[0].citation.locator["converted_to_pdf"] is True


def test_material_analysis_persists_by_session_and_cannot_cross_load(tmp_path: Path) -> None:
    analysis = MaterialAnalysis(
        analysis_id="analysis-1", session_id="session-a", owner_id="test-owner", source_id="material-1",
        subject="mathematics", sub_subject="Algebra", chinese_grade="junior_1",
        international_grade="grade_7", difficulty="standard", confidence=0.8,
        evidence=[], augmentation_needed=False, augmentation_reason="Material is sufficient.",
        created_at="2026-01-01T00:00:00Z", trace={"reasoning_effort": "medium"},
    )
    save_material_analysis(analysis, root=tmp_path)
    loaded = load_material_analysis("analysis-1", "session-a", root=tmp_path)
    assert loaded["subject"] == "mathematics"
    assert loaded["version"] == 1
    assert loaded["grade_band"] == {"chinese": "junior_1", "international": "grade_7"}
    assert loaded["concept_candidates"] == []
    assert loaded["page_evidence"] == []
    assert loaded["augmentation_decision"] == {"needed": False, "reason": "Material is sufficient."}
    with pytest.raises(FileNotFoundError):
        load_material_analysis("analysis-1", "session-b", root=tmp_path)


def test_search_tool_skips_sufficient_material_without_network() -> None:
    trace = asyncio.run(search_learning_sources({"augmentation_needed": False}))
    assert trace["used"] is False
    assert trace["reason"] == "material_sufficient"


def test_search_tool_uses_thread_and_returns_a_failure_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    async def fake_to_thread(func, /, *args, **kwargs):
        calls.append((func, args, kwargs))
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(material_analysis.asyncio, "to_thread", fake_to_thread)
    trace = asyncio.run(search_learning_sources({"augmentation_needed": True, "sub_subject": "Algebra"}))

    assert calls and calls[0][0] is material_analysis.web_search
    assert trace["used"] is False
    assert trace["reason"] == "search_failed"
    assert trace["error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_material_analysis_queues_knowledge_graph_without_touching_bkt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from traittutor.generate.service import MaterialSource

    queued: list[dict[str, object]] = []

    class _User:
        id = "learner-a"

    async def fake_prompt(_prompt, *, validate, reasoning_effort=None):
        payload = {
            "subject": "mathematics",
            "sub_subject": "linear functions",
            "chinese_grade": "junior_2",
            "international_grade": "grade_8",
            "difficulty": "standard",
            "confidence": 0.9,
            "evidence": [{"chunk_id": "material-1:chunk-1"}],
            "concept_candidates": [
                {
                    "concept_id": "linear-functions.slope",
                    "label": "斜率",
                    "module_id": "linear-functions",
                    "module_label": "一次函数",
                    "confidence": 0.8,
                    "evidence_chunk_ids": ["material-1:chunk-1"],
                }
            ],
            "augmentation_needed": False,
            "augmentation_reason": "Material is sufficient.",
        }
        validate(payload)
        return payload, SimpleNamespace(model="test-model", provider="test-provider", reasoning_effort=reasoning_effort or "medium")

    def fake_schedule(**kwargs):
        queued.append(kwargs)

    monkeypatch.setattr(material_analysis, "get_current_user", lambda: _User())
    monkeypatch.setattr(material_analysis, "run_structured_prompt", fake_prompt)
    monkeypatch.setattr(material_analysis, "schedule_learning_knowledge_graph", fake_schedule)
    monkeypatch.setattr(material_analysis, "_analysis_dir", lambda root=None: tmp_path / "analyses")
    monkeypatch.setattr(material_analysis, "_analysis_request_times", {})

    analysis = await material_analysis.analyze_material(
        MaterialSource(
            source_type="paste",
            source_id="material-1",
            title="函数",
            text="一次函数的斜率表示变化率。",
        ),
        session_id="session-a",
    )

    assert analysis.subject == "mathematics"
    assert analysis.grade_band == {"chinese": "junior_2", "international": "grade_8"}
    assert analysis.concept_candidates and analysis.concept_candidates[0]["status"] == "confirmed"
    assert analysis.page_evidence and analysis.page_evidence[0]["source_id"] == "material-1"
    assert queued
    assert queued[0]["source_ref"] == f"material-analysis:{analysis.analysis_id}"
    assert queued[0]["subject"].subject_id == "mathematics"


@pytest.mark.asyncio
async def test_material_analysis_tolerates_non_numeric_concept_confidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from traittutor.generate.service import MaterialSource

    class _User:
        id = "learner-a"

    async def fake_prompt(_prompt, *, validate, reasoning_effort=None):
        payload = {
            "subject": "other",
            "sub_subject": "commerce operations",
            "chinese_grade": "adult",
            "international_grade": "adult",
            "difficulty": "standard",
            "confidence": 0.7,
            "evidence": [{"chunk_id": "material-1:chunk-1"}],
            "concept_candidates": [
                {
                    "concept_id": "commerce.kol",
                    "label": "KOL 营销",
                    "module_id": "commerce",
                    "module_label": "Commerce",
                    "confidence": "high",
                    "evidence_chunk_ids": ["material-1:chunk-1"],
                }
            ],
            "augmentation_needed": False,
            "augmentation_reason": "Material is sufficient.",
        }
        validate(payload)
        return payload, SimpleNamespace(model="test-model", provider="test-provider", reasoning_effort=reasoning_effort or "medium")

    monkeypatch.setattr(material_analysis, "get_current_user", lambda: _User())
    monkeypatch.setattr(material_analysis, "run_structured_prompt", fake_prompt)
    monkeypatch.setattr(material_analysis, "schedule_learning_knowledge_graph", lambda **_kwargs: None)
    monkeypatch.setattr(material_analysis, "_analysis_dir", lambda root=None: tmp_path / "analyses")
    monkeypatch.setattr(material_analysis, "_analysis_request_times", {})

    analysis = await material_analysis.analyze_material(
        MaterialSource(
            source_type="paste",
            source_id="material-1",
            title="运营材料",
            text="跨境电商直播运营需要同步 KOL 营销和供应链节奏。",
        ),
        session_id="session-a",
    )

    assert analysis.concept_candidates
    assert analysis.concept_candidates[0]["confidence"] == 0.35
    assert analysis.concept_candidates[0]["status"] == "candidate"


def test_search_tool_rejects_malformed_provider_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_to_thread(_func, /, *_args, **_kwargs):
        return {"search_results": "not-a-list"}

    monkeypatch.setattr(material_analysis.asyncio, "to_thread", fake_to_thread)
    trace = asyncio.run(search_learning_sources({"augmentation_needed": True, "sub_subject": "Algebra"}))

    assert trace["used"] is False
    assert trace["reason"] == "search_invalid_response"


def test_analysis_request_rejects_oversized_text_and_page_slices() -> None:
    with pytest.raises(ValueError, match="material.text"):
        AnalyzeMaterialRequest(session_id="session-1", material={"text": "x" * (ANALYSIS_MAX_TEXT_CHARS + 1)})
    with pytest.raises(ValueError, match="page_slices"):
        AnalyzeMaterialRequest(
            session_id="session-1",
            material={"metadata": {"page_slices": [{}] * (ANALYSIS_MAX_PAGE_SLICES + 1)}},
        )


def test_analysis_quota_rejects_burst_for_one_user() -> None:
    owner_id = "quota-test-owner"
    material_analysis._analysis_request_times.pop(owner_id, None)
    for offset in range(ANALYSIS_QUOTA_MAX_REQUESTS):
        consume_material_analysis_quota(owner_id, now=float(offset))
    with pytest.raises(MaterialAnalysisRateLimitError, match="Too many"):
        consume_material_analysis_quota(owner_id, now=float(ANALYSIS_QUOTA_MAX_REQUESTS))
