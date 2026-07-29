"""Notebook service regression tests."""

from __future__ import annotations

import json

from traittutor.services.notebook.service import NotebookManager, RecordType


def test_add_record_accepts_enum_record_type(tmp_path) -> None:
    manager = NotebookManager(base_dir=str(tmp_path))
    notebook = manager.create_notebook("CLI test notebook")

    result = manager.add_record(
        notebook_ids=[notebook["id"]],
        record_type=RecordType.CHAT,
        title="Sample",
        user_query="Sample",
        output="# Sample",
    )

    assert result["record"]["type"] == RecordType.CHAT

    stored = manager.get_notebook(notebook["id"])
    assert stored is not None
    assert stored["records"][0]["type"] == "chat"


def test_add_record_strips_thinking_tags_from_summary(tmp_path) -> None:
    manager = NotebookManager(base_dir=str(tmp_path))
    notebook = manager.create_notebook("Sanitized notebook")

    result = manager.add_record(
        notebook_ids=[notebook["id"]],
        record_type="chat",
        title="Sample",
        summary="<think>private reasoning</think>\nReusable summary.",
        user_query="Sample",
        output="# Sample",
    )

    assert result["record"]["summary"] == "Reusable summary."

    stored = manager.get_notebook(notebook["id"])
    assert stored is not None
    assert stored["records"][0]["summary"] == "Reusable summary."


def test_get_notebook_repairs_existing_thinking_tags_in_summary(tmp_path) -> None:
    manager = NotebookManager(base_dir=str(tmp_path))
    notebook = manager.create_notebook("Legacy notebook")
    manager.add_record(
        notebook_ids=[notebook["id"]],
        record_type="chat",
        title="Sample",
        summary="Reusable summary.",
        user_query="Sample",
        output="# Sample",
    )

    path = manager._get_notebook_file(notebook["id"])
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["records"][0]["summary"] = "<think>old reasoning</think>\nReusable summary."
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    repaired = manager.get_notebook(notebook["id"])
    assert repaired is not None
    assert repaired["records"][0]["summary"] == "Reusable summary."
    assert "old reasoning" not in path.read_text(encoding="utf-8")
