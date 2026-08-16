"""Resolve a knowledge base's bound RAG provider.

TraitTutor owns the knowledge-base lifecycle and stores the authoritative
provider binding in ``kb_config.json``. Older KBs may only have
``metadata.json``, so metadata remains a legacy fallback. Retrieval and
incremental indexing should use this helper instead of hand-reading either file
directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from traittutor.knowledge.state_store import KnowledgeStateStore
from traittutor.services.rag.factory import DEFAULT_PROVIDER, normalize_provider_name


def load_kb_config_entry(kb_base_dir: str | Path, kb_name: str) -> dict[str, Any]:
    """Return the raw ``kb_config.json`` entry for ``kb_name``, if present."""
    entry = (
        KnowledgeStateStore(Path(kb_base_dir))
        .load_config()
        .get("knowledge_bases", {})
        .get(kb_name, {})
    )
    return entry if isinstance(entry, dict) else {}


def load_metadata_provider(kb_base_dir: str | Path, kb_name: str) -> str | None:
    """Return the provider stored in legacy ``metadata.json``, if present."""
    provider = KnowledgeStateStore(Path(kb_base_dir)).load_metadata(kb_name).get("rag_provider")
    return normalize_provider_name(provider) if provider else None


def resolve_bound_provider(kb_base_dir: str | Path, kb_name: str | None) -> str:
    """Resolve the provider TraitTutor has bound to a KB.

    Order:
    1. ``kb_config.json``: authoritative runtime state.
    2. ``metadata.json``: legacy/import fallback.
    3. default provider.
    """
    if kb_name:
        entry = load_kb_config_entry(kb_base_dir, kb_name)
        provider = entry.get("rag_provider")
        if provider:
            return normalize_provider_name(provider)

        metadata_provider = load_metadata_provider(kb_base_dir, kb_name)
        if metadata_provider:
            return metadata_provider

    return DEFAULT_PROVIDER


__all__ = [
    "load_kb_config_entry",
    "load_metadata_provider",
    "resolve_bound_provider",
]
