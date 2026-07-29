from __future__ import annotations

from pathlib import Path

from traittutor_cli.kb import _collect_documents


def test_collect_documents_from_directory_matches_uppercase_extensions(tmp_path: Path) -> None:
    docs_dir = tmp_path / "资料"
    docs_dir.mkdir()
    upper_pdf = docs_dir / "报告.PDF"
    upper_pdf.write_bytes(b"%PDF-1.4")
    nested = docs_dir / "nested"
    nested.mkdir()
    upper_md = nested / "README.MD"
    upper_md.write_text("hello", encoding="utf-8")

    collected = [Path(path).name for path in _collect_documents([], str(docs_dir))]

    assert collected == ["README.MD", "报告.PDF"]
