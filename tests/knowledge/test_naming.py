from __future__ import annotations

import pytest

from traittutor.knowledge.naming import validate_knowledge_base_name


def test_validate_knowledge_base_name_allows_unicode_and_spaces() -> None:
    assert validate_knowledge_base_name("  高等数学 KB  ") == "高等数学 KB"


@pytest.mark.parametrize("name", ["bad/name", "bad\\name", "bad?name", "bad#name", "bad%name"])
def test_validate_knowledge_base_name_rejects_path_and_url_separators(name: str) -> None:
    with pytest.raises(ValueError, match="reserved characters"):
        validate_knowledge_base_name(name)
