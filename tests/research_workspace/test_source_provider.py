from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from traittutor.multi_user.models import KnowledgeResource
from traittutor.research_workspace.executor import ResearchExecutionTask, ResearchSourceDraft
from traittutor.research_workspace.models import ResearchBrief, ResearchKnowledgeBaseBinding
from traittutor.research_workspace.source_provider import (
    KnowledgeBaseValidatedSourceProvider,
    ResearchPolicySourceProvider,
    WebSearchValidatedSourceProvider,
)

T0 = "2026-08-10T00:00:00+00:00"


def _task(
    source_policy: Literal["web", "knowledge_base", "mixed"] = "web",
) -> ResearchExecutionTask:
    return ResearchExecutionTask(
        workspace_id="workspace",
        run_id="run",
        task_id="research_report",
        input_hash="a" * 64,
        fencing_epoch=1,
        claim_token="claim",
        brief=ResearchBrief(
            brief_id="brief",
            workspace_id="workspace",
            owner_id="owner",
            version=1,
            question="What evidence is available?",
            source_policy=source_policy,
            knowledge_base=(
                ResearchKnowledgeBaseBinding(
                    resource_id="user:kb:study-notes",
                    display_name="study-notes",
                    source="user",
                    authorized_owner_id="owner",
                )
                if source_policy != "web"
                else None
            ),
            content_hash="b" * 64,
            created_at=T0,
        ),
    )


def test_web_provider_uses_only_valid_individual_results_not_consolidated_answer() -> None:
    calls: list[tuple[str, int]] = []

    def search(query: str, *, max_results: int) -> dict[str, Any]:
        calls.append((query, max_results))
        return {
            "answer": "Invented synthesis citing https://invented.example/answer",
            "response": {"content": "Must not become evidence"},
            "search_results": [
                {
                    "url": "https://evidence.example/source",
                    "title": "Validated result",
                    "snippet": "A bounded result excerpt",
                },
                {
                    "url": "ftp://invalid.example/source",
                    "title": "Invalid protocol",
                    "snippet": "Must be dropped",
                },
                {"url": "https://evidence.example/no-title", "title": ""},
            ],
        }

    provider = WebSearchValidatedSourceProvider(search, max_results=4)

    sources = provider.sources_for(_task())

    assert calls == [("What evidence is available?", 4)]
    assert len(sources) == 1
    assert str(sources[0].url) == "https://evidence.example/source"
    assert sources[0].title == "Validated result"
    assert "invented" not in sources[0].model_dump_json()


def test_web_provider_uses_citations_when_raw_results_are_absent() -> None:
    provider = WebSearchValidatedSourceProvider(
        lambda query, **kwargs: {
            "query": query,
            "answer": "ignored",
            "search_results": [],
            "citations": [
                {
                    "url": "https://evidence.example/citation",
                    "title": "Validated citation",
                    "snippet": "Citation excerpt",
                }
            ],
        }
    )

    sources = provider.sources_for(_task("mixed"))

    assert [source.title for source in sources] == ["Validated citation"]


def test_web_provider_returns_no_sources_for_an_empty_search_result() -> None:
    provider = WebSearchValidatedSourceProvider(
        lambda query, **kwargs: {
            "query": query,
            "answer": "No sources were found.",
            "search_results": [],
            "citations": [],
        }
    )

    assert provider.sources_for(_task()) == ()


def test_web_provider_degrades_search_failure_to_no_validated_sources() -> None:
    def unavailable_search(query: str, **kwargs: Any) -> dict[str, Any]:
        del query, kwargs
        raise TimeoutError("upstream search timed out")

    provider = WebSearchValidatedSourceProvider(unavailable_search)

    assert provider.sources_for(_task()) == ()


def test_knowledge_base_policy_does_not_fall_through_to_web_search() -> None:
    calls = 0

    def search(query: str, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        del query, kwargs
        calls += 1
        return {}

    provider = WebSearchValidatedSourceProvider(search)

    assert provider.sources_for(_task("knowledge_base")) == ()
    assert calls == 0


def _knowledge_resource(name: str = "study-notes") -> KnowledgeResource:
    return KnowledgeResource(
        id=f"user:kb:{name}",
        name=name,
        # This path is intentionally private.  The KB provider must use it for
        # retrieval but never emit it in a ResearchSourceDraft.
        base_dir=Path("/private/research-owner/knowledge_bases"),
        source="user",
    )


@pytest.mark.asyncio
async def test_kb_provider_accepts_only_row_provenance_and_redacts_file_paths() -> None:
    calls: list[tuple[str, str]] = []

    async def search(resource: KnowledgeResource, question: str) -> dict[str, Any]:
        calls.append((resource.id, question))
        return {
            "answer": "Synthesized answer from /private/never-expose.txt",
            "content": "Likewise never evidence",
            "sources": [
                {
                    "source": "/private/research-owner/raw/week-1/lecture.pdf",
                    "chunk_id": "chunk-42",
                    "page": "8",
                    "content": "Passage is grounded, but /private/hidden.txt is not public.",
                }
            ],
        }

    provider = KnowledgeBaseValidatedSourceProvider(
        lambda resource_id: _knowledge_resource() if resource_id == "user:kb:study-notes" else None,
        search=search,
    )

    sources = await provider.sources_for(_task("knowledge_base"))

    assert calls == [("user:kb:study-notes", "What evidence is available?")]
    assert len(sources) == 1
    assert sources[0].title == "lecture.pdf"
    assert str(sources[0].url).startswith("https://research-source.traittutor.invalid/kb/")
    assert "lecture.pdf" in sources[0].model_dump_json()
    assert "/private/" not in sources[0].model_dump_json()
    assert "Synthesized answer" not in sources[0].model_dump_json()


@pytest.mark.asyncio
async def test_kb_provider_fails_closed_for_revoked_or_provenance_free_results() -> None:
    calls = 0

    async def search(resource: KnowledgeResource, question: str) -> dict[str, Any]:
        nonlocal calls
        del resource, question
        calls += 1
        return {
            "answer": "A synthesis is not a source.",
            "sources": [{"content": "A passage with no file, URL, or chunk id."}],
        }

    revoked = KnowledgeBaseValidatedSourceProvider(lambda resource_id: None, search=search)
    no_provenance = KnowledgeBaseValidatedSourceProvider(
        lambda resource_id: _knowledge_resource(),
        search=search,
    )

    assert await revoked.sources_for(_task("knowledge_base")) == ()
    assert calls == 0
    assert await no_provenance.sources_for(_task("knowledge_base")) == ()
    assert calls == 1


@pytest.mark.asyncio
async def test_mixed_policy_is_a_bounded_kb_and_web_merge() -> None:
    class _FixedProvider:
        def __init__(self, key: str) -> None:
            self._key = key

        def sources_for(self, task: ResearchExecutionTask):
            del task
            return (
                ResearchSourceDraft(
                    source_key=f"{self._key}-1",
                    url=f"https://evidence.example/{self._key}-1",
                    title=f"{self._key} 1",
                ),
                ResearchSourceDraft(
                    source_key=f"{self._key}-2",
                    url=f"https://evidence.example/{self._key}-2",
                    title=f"{self._key} 2",
                ),
            )

    provider = ResearchPolicySourceProvider(
        web=_FixedProvider("web"),
        knowledge_base=_FixedProvider("kb"),
        mixed_max_results=3,
    )

    sources = await provider.sources_for(_task("mixed"))

    assert [source.source_key for source in sources] == ["kb-1", "kb-2", "web-1"]
