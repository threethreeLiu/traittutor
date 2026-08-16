"""Higher-level multi-query retrieval helpers built on top of RAGService."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Dict, List, Optional

from traittutor.gateway import GatewayMessage, GatewayRequest, get_gateway

SearchFunc = Callable[..., Awaitable[Dict[str, Any]]]


class SmartRetriever:
    """Generate query variants, retrieve passages, and aggregate them."""

    def __init__(self, search: SearchFunc):
        self._search = search

    async def retrieve(
        self,
        context: str,
        kb_name: str,
        query_hints: Optional[List[str]] = None,
        max_queries: int = 3,
    ) -> Dict[str, Any]:
        queries = query_hints if query_hints else await self._generate_queries(context, max_queries)
        results = await asyncio.gather(
            *(self._search(query=q, kb_name=kb_name) for q in queries),
            return_exceptions=True,
        )

        passages: list[str] = []
        all_sources: list[dict] = []
        for result in results:
            if isinstance(result, BaseException):
                continue
            content = result.get("content") or result.get("answer") or ""
            if content:
                passages.append(content)
                all_sources.append(
                    {"query": result.get("query", ""), "provider": result.get("provider", "")}
                )

        if not passages:
            return {"answer": "", "sources": []}

        aggregated = await self._aggregate(context, passages)
        return {"answer": aggregated, "sources": all_sources}

    async def _generate_queries(self, context: str, n: int) -> list[str]:
        try:
            prompt = (
                f"Generate {n} diverse search queries to retrieve information relevant "
                f"to the following context. Return ONLY the queries, one per line.\n\n"
                f"Context:\n{context[:2000]}"
            )
            raw = await self._complete_gateway(
                prompt,
                system_prompt="You are a search query generator.",
                purpose="rag:query-generation",
            )
            lines = [
                line.strip().lstrip("0123456789.-) ")
                for line in raw.strip().split("\n")
                if line.strip()
            ]
            return lines[:n] if lines else [context[:200]]
        except Exception:
            return [context[:200]]

    async def _aggregate(self, context: str, passages: list[str]) -> str:
        try:
            combined = "\n---\n".join(passages)
            prompt = (
                "Synthesise the following retrieved passages into a concise, "
                "relevant summary for the given context.\n\n"
                f"Context:\n{context[:1000]}\n\n"
                f"Passages:\n{combined[:6000]}"
            )
            return await self._complete_gateway(
                prompt,
                system_prompt="You are a knowledge synthesiser.",
                purpose="rag:passage-synthesis",
            )
        except Exception:
            return "\n\n".join(passages)

    @staticmethod
    async def _complete_gateway(prompt: str, *, system_prompt: str, purpose: str) -> str:
        response = await get_gateway().complete(
            GatewayRequest(
                prompt=prompt,
                system_prompt=system_prompt,
                purpose=purpose,
                messages=(
                    GatewayMessage(role="system", content=system_prompt),
                    GatewayMessage(role="user", content=prompt),
                ),
            )
        )
        return response.content
