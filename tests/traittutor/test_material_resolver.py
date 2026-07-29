from __future__ import annotations

from pathlib import Path

import pytest

from traittutor.generate.materials import (
    MaterialReference,
    MaterialResolutionError,
    MaterialResolver,
)
from traittutor.knowledge.manager import KnowledgeBaseManager
from traittutor.services.notebook.service import NotebookManager


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
