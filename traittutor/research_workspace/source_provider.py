"""Validated source adapters for Gateway-backed Research Workspace execution.

The provider layer is deliberately narrower than a general RAG response.  It
may promote only individual retrieval rows with explicit provenance into the
research evidence ledger; a RAG engine's ``answer`` is a synthesis, not an
evidence record.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import sha256
import inspect
import json
import logging
from pathlib import PurePath
import re
from typing import Any, Literal, Protocol, cast

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, model_validator

from traittutor.multi_user.models import KnowledgeResource
from traittutor.services.rag.service import RAGService
from traittutor.services.search import web_search

from .executor import ResearchExecutionTask, ResearchSourceDraft

SearchCallable = Callable[..., dict[str, Any]]
KnowledgeBaseResolver = Callable[[str], KnowledgeResource | None]
KnowledgeBaseSearch = Callable[[KnowledgeResource, str], Awaitable[dict[str, Any]]]

_ABSOLUTE_PATH = re.compile(r"(?<![:\w])(?:/[\w .@+=,-]{1,255}){2,}|[A-Za-z]:\\[^\s<>]{1,512}")
logger = logging.getLogger(__name__)


class _SourceProvider(Protocol):
    def sources_for(
        self, task: ResearchExecutionTask
    ) -> tuple[ResearchSourceDraft, ...] | Awaitable[tuple[ResearchSourceDraft, ...]]: ...


class KnowledgeBaseSourceLocator(BaseModel):
    """Internal, path-free provenance extracted from one RAG source row.

    RAG providers disagree about source shape.  This normalized locator keeps
    only a public URL, a basename, a chunk id and a page label; it never retains
    the provider's filesystem ``source`` value.  The opaque URL is stable for
    a row but deliberately carries no owner, KB name or filesystem path.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["url", "file", "chunk"]
    url: AnyHttpUrl | None = None
    file_name: str | None = Field(default=None, max_length=255)
    chunk_id: str | None = Field(default=None, max_length=256)
    page: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _require_matching_provenance(self) -> "KnowledgeBaseSourceLocator":
        if self.kind == "url" and self.url is None:
            raise ValueError("URL provenance requires a URL")
        if self.kind == "file" and self.file_name is None:
            raise ValueError("file provenance requires a file name")
        if self.kind == "chunk" and self.chunk_id is None:
            raise ValueError("chunk provenance requires a chunk id")
        return self

    def public_url(self, *, resource_id: str) -> AnyHttpUrl:
        """Return a non-path URL suitable for the existing evidence ledger."""

        if self.url is not None:
            return self.url
        fingerprint = sha256(
            json.dumps(
                {
                    "resource": resource_id,
                    "file_name": self.file_name,
                    "chunk_id": self.chunk_id,
                    "page": self.page,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return cast(
            AnyHttpUrl,
            f"https://research-source.traittutor.invalid/kb/{fingerprint}",
        )


class WebSearchValidatedSourceProvider:
    """Convert raw web-search rows into a bounded, URL-validated source bundle.

    The search service may also return a consolidated answer. This adapter
    intentionally ignores it: only individual result/citation metadata can
    become durable evidence supplied to the Gateway executor.
    """

    def __init__(
        self,
        search: SearchCallable = web_search,
        *,
        max_results: int = 6,
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        self._search = search
        self._max_results = max_results

    def sources_for(self, task: ResearchExecutionTask) -> tuple[ResearchSourceDraft, ...]:
        if task.brief.source_policy == "knowledge_base":
            return ()
        try:
            payload = self._search(
                task.brief.question[:2_000],
                max_results=self._max_results,
            )
        except Exception:  # noqa: BLE001 - provider failures are an expected degradation
            # Search is an external evidence dependency.  A transient engine
            # timeout must not be persisted as an opaque executor failure:
            # returning no validated sources makes the executor produce its
            # explicit managed-review result without inventing evidence.  Do
            # not log the query or provider exception because either may
            # contain owner input or credential-bearing transport details.
            logger.warning("Research web search unavailable; returning no validated sources")
            return ()
        rows = payload.get("search_results")
        if not isinstance(rows, list) or not rows:
            rows = payload.get("citations")
        if not isinstance(rows, list):
            return ()

        sources: list[ResearchSourceDraft] = []
        seen_urls: set[str] = set()
        for row in rows:
            if len(sources) >= self._max_results:
                break
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            title = row.get("title")
            if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
                continue
            try:
                candidate = ResearchSourceDraft(
                    source_key=f"web_{sha256(url.strip().encode()).hexdigest()[:20]}",
                    url=AnyHttpUrl(url.strip()),
                    title=title.strip()[:500],
                    excerpt=_excerpt(row.get("snippet")),
                )
            except ValidationError:
                continue
            normalized_url = str(candidate.url)
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            sources.append(candidate)
        return tuple(sources)


class KnowledgeBaseValidatedSourceProvider:
    """Retrieve only provenance-bearing rows from an owner-authorized KB.

    ``resolver`` is invoked immediately before each worker retrieval.  It must
    resolve the frozen logical id in that worker's owner context and return the
    exact same resource, preventing a stale grant or a changed KB name from
    becoming evidence.  The RAG service receives the private base dir only
    after this check; it is never copied into a draft or persisted source.
    """

    def __init__(
        self,
        resolver: KnowledgeBaseResolver,
        *,
        search: KnowledgeBaseSearch | None = None,
        max_results: int = 6,
    ) -> None:
        if not 1 <= max_results <= 20:
            raise ValueError("max_results must be between 1 and 20")
        self._resolver = resolver
        self._search = search or _rag_search
        self._max_results = max_results

    async def sources_for(self, task: ResearchExecutionTask) -> tuple[ResearchSourceDraft, ...]:
        binding = task.brief.knowledge_base
        if binding is None:
            return ()
        try:
            resource = self._resolver(binding.resource_id)
        except Exception:
            # Lost authorization is not an execution error containing user or
            # provider data.  The executor turns an empty source bundle into a
            # managed review state without leaking why access changed.
            return ()
        if (
            resource is None
            or resource.id != binding.resource_id
            or resource.name != binding.display_name
            or resource.source != binding.source
        ):
            return ()
        try:
            payload = await self._search(resource, task.brief.question[:2_000])
        except Exception:
            return ()
        return _knowledge_base_sources(
            payload,
            resource_id=resource.id,
            max_results=self._max_results,
        )


class ResearchPolicySourceProvider:
    """Select policy-bounded evidence without allowing cross-policy fallback."""

    def __init__(
        self,
        *,
        web: _SourceProvider,
        knowledge_base: _SourceProvider,
        mixed_max_results: int = 8,
    ) -> None:
        if not 1 <= mixed_max_results <= 40:
            raise ValueError("mixed_max_results must be between 1 and 40")
        self._web = web
        self._knowledge_base = knowledge_base
        self._mixed_max_results = mixed_max_results

    async def sources_for(self, task: ResearchExecutionTask) -> tuple[ResearchSourceDraft, ...]:
        if task.brief.source_policy == "web":
            return await _provider_sources(self._web, task)
        if task.brief.source_policy == "knowledge_base":
            return await _provider_sources(self._knowledge_base, task)
        # Mixed is intentionally a bounded merge, not a fall-through.  Both
        # boundaries are independently consulted and the ledger gets no more
        # than the fixed product budget.
        knowledge_sources, web_sources = await _gather_two(
            _provider_sources(self._knowledge_base, task),
            _provider_sources(self._web, task),
        )
        merged: list[ResearchSourceDraft] = []
        seen_urls: set[str] = set()
        for source in (*knowledge_sources, *web_sources):
            if len(merged) >= self._mixed_max_results:
                break
            normalized_url = str(source.url)
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            merged.append(source)
        return tuple(merged)


async def _gather_two(
    first: Awaitable[tuple[ResearchSourceDraft, ...]],
    second: Awaitable[tuple[ResearchSourceDraft, ...]],
) -> tuple[tuple[ResearchSourceDraft, ...], tuple[ResearchSourceDraft, ...]]:
    # Kept without an asyncio import at module import time to make this adapter
    # cheap in test-only and worker-only processes.
    import asyncio

    first_result, second_result = await asyncio.gather(first, second)
    return first_result, second_result


async def _provider_sources(
    provider: _SourceProvider,
    task: ResearchExecutionTask,
) -> tuple[ResearchSourceDraft, ...]:
    result = provider.sources_for(task)
    if inspect.isawaitable(result):
        return await result
    return result


async def _rag_search(resource: KnowledgeResource, question: str) -> dict[str, Any]:
    """Run retrieval through the KB's server-owned, provider-bound service."""

    service = RAGService(kb_base_dir=str(resource.base_dir))
    return await service.search(question, resource.name, top_k=6)


def _knowledge_base_sources(
    payload: object,
    *,
    resource_id: str,
    max_results: int,
) -> tuple[ResearchSourceDraft, ...]:
    """Promote explicit RAG rows only; synthesized answer/content is ignored."""

    if not isinstance(payload, dict):
        return ()
    rows = payload.get("sources")
    if not isinstance(rows, list):
        return ()
    sources: list[ResearchSourceDraft] = []
    seen_keys: set[str] = set()
    for row in rows:
        if len(sources) >= max_results or not isinstance(row, dict):
            continue
        locator = _locator_from_row(row)
        if locator is None:
            # A chunk of model/RAG answer text without an individual source,
            # file or chunk marker is synthesis and cannot become evidence.
            continue
        title = _safe_title(row, locator)
        excerpt = _excerpt_from_rag_row(row)
        source_key = (
            f"kb_{sha256(_source_fingerprint(resource_id, locator).encode()).hexdigest()[:20]}"
        )
        if source_key in seen_keys:
            continue
        seen_keys.add(source_key)
        sources.append(
            ResearchSourceDraft(
                source_key=source_key,
                url=locator.public_url(resource_id=resource_id),
                title=title,
                excerpt=excerpt,
            )
        )
    return tuple(sources)


def _locator_from_row(row: dict[str, Any]) -> KnowledgeBaseSourceLocator | None:
    raw_url = row.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        try:
            return KnowledgeBaseSourceLocator(kind="url", url=AnyHttpUrl(raw_url.strip()))
        except ValidationError:
            pass
    file_name = _safe_file_name(
        row.get("file_name") or row.get("filename") or row.get("source") or row.get("file")
    )
    chunk_id = _bounded_text(row.get("chunk_id") or row.get("node_id") or row.get("id"), 256)
    page = _bounded_text(row.get("page") or row.get("page_label"), 128)
    if file_name is not None:
        return KnowledgeBaseSourceLocator(
            kind="file", file_name=file_name, chunk_id=chunk_id, page=page
        )
    if chunk_id is not None:
        return KnowledgeBaseSourceLocator(kind="chunk", chunk_id=chunk_id, page=page)
    return None


def _safe_file_name(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("\\", "/")
    name = PurePath(normalized).name.strip()
    if not name or name in {".", ".."}:
        return None
    return name[:255]


def _safe_title(row: dict[str, Any], locator: KnowledgeBaseSourceLocator) -> str:
    candidate = row.get("title")
    if isinstance(candidate, str) and candidate.strip():
        # A provider sometimes labels a passage with its file path.  Keep a
        # human-recognizable basename in that case rather than expose a path.
        candidate = candidate.strip()
        if "/" in candidate or "\\" in candidate:
            candidate = _safe_file_name(candidate) or "Knowledge-base passage"
        return _redact_absolute_paths(candidate)[:500] or "Knowledge-base passage"
    if locator.file_name is not None:
        return locator.file_name
    if locator.chunk_id is not None:
        return f"Knowledge-base passage {locator.chunk_id}"[:500]
    return "Knowledge-base passage"


def _excerpt_from_rag_row(row: dict[str, Any]) -> str | None:
    # Do not inspect `answer`: top-level and per-provider answer strings are
    # synthesis.  The source row's own passage/content is the only eligible
    # excerpt, and paths in it are redacted before it reaches a public ledger.
    for key in ("excerpt", "content", "text", "snippet"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return _redact_absolute_paths(value.strip())[:8_000] or None
    return None


def _source_fingerprint(resource_id: str, locator: KnowledgeBaseSourceLocator) -> str:
    return json.dumps(
        {"resource_id": resource_id, **locator.model_dump(mode="json")},
        ensure_ascii=False,
        sort_keys=True,
    )


def _redact_absolute_paths(value: str) -> str:
    return _ABSOLUTE_PATH.sub("[redacted path]", value)


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _redact_absolute_paths(value.strip())[:limit] or None


def _excerpt(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:8_000]


__all__ = [
    "KnowledgeBaseResolver",
    "KnowledgeBaseSourceLocator",
    "KnowledgeBaseValidatedSourceProvider",
    "KnowledgeBaseSearch",
    "ResearchPolicySourceProvider",
    "SearchCallable",
    "WebSearchValidatedSourceProvider",
]
