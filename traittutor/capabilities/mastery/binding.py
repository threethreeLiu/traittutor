"""Server-derived identity binding for canonical Mastery Path chat.

The browser may choose an existing learning path, but it never supplies the
subject partition used for learning evidence.  A binding is minted only after
the current owner's ``LearningProgress`` document has been loaded and its
already-persisted subject/KC graph has been checked.  The same binding is
rechecked before every mastery tool call so a stale, cross-owner, or
model-invented path cannot reach canonical BKT.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from traittutor.learning.models import LearningProgress
from traittutor.learning.storage import LearningStore
from traittutor.multi_user.context import get_current_user


class MasteryPathBinding(BaseModel):
    """Durable server-authored link from one owner/session to one path subject.

    ``owner_id`` is deliberately carried even though ``LearningStore`` is
    workspace-scoped: it lets a copied session preference fail closed before
    it can be interpreted as a target in another authenticated workspace.
    ``graph_fingerprint`` fences later model-driven graph replacement; a new
    explicit selection is required after the authoritative graph changes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: str = Field(min_length=1, max_length=128)
    learning_path_id: str = Field(min_length=1, max_length=160)
    subject_id: str = Field(min_length=1, max_length=128)
    graph_fingerprint: str = Field(min_length=64, max_length=64)
    path_version_at_binding: int = Field(ge=0)


def learning_graph_fingerprint(progress: LearningProgress) -> str:
    """Hash the persisted module/KC identity graph, not model display text."""
    graph = [
        {
            "module_id": module.id,
            "knowledge_points": [
                {"kc_id": kp.id, "type": kp.type.value} for kp in module.knowledge_points
            ],
        }
        for module in progress.modules
    ]
    encoded = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _has_authoritative_graph(progress: LearningProgress) -> bool:
    """Require a nonempty, unambiguous persisted KC graph before binding."""
    ids = [kp.id.strip() for module in progress.modules for kp in module.knowledge_points]
    return bool(ids) and all(ids) and len(ids) == len(set(ids))


def create_mastery_path_binding(learning_path_id: str) -> MasteryPathBinding | None:
    """Mint a binding for the current owner from one existing path only.

    This intentionally does not call ``get_or_create``.  A chat session ID,
    pack title, book reference, or model output may never create a progress
    target or imply its subject partition.
    """
    path_id = str(learning_path_id or "").strip()
    if not path_id:
        return None
    try:
        progress = LearningStore().load(path_id)
    except (OSError, ValueError):
        return None
    if progress is None or progress.book_id != path_id:
        return None
    subject_id = progress.subject_id.strip()
    if not subject_id or not _has_authoritative_graph(progress):
        return None
    owner_id = get_current_user().id.strip()
    if not owner_id:
        return None
    return MasteryPathBinding(
        owner_id=owner_id,
        learning_path_id=path_id,
        subject_id=subject_id,
        graph_fingerprint=learning_graph_fingerprint(progress),
        path_version_at_binding=progress.version,
    )


def load_bound_mastery_progress(
    raw_binding: MasteryPathBinding | dict[str, Any] | None,
) -> tuple[MasteryPathBinding, LearningProgress] | None:
    """Load a binding only when its owner, subject, and KC graph still match."""
    try:
        binding = (
            raw_binding
            if isinstance(raw_binding, MasteryPathBinding)
            else MasteryPathBinding.model_validate(raw_binding)
        )
    except (TypeError, ValueError):
        return None
    if binding.owner_id != get_current_user().id:
        return None
    try:
        progress = LearningStore().load(binding.learning_path_id)
    except (OSError, ValueError):
        return None
    if (
        progress is None
        or progress.book_id != binding.learning_path_id
        or progress.subject_id.strip() != binding.subject_id
        or not _has_authoritative_graph(progress)
        or learning_graph_fingerprint(progress) != binding.graph_fingerprint
    ):
        return None
    return binding, progress


def resolve_mastery_path_binding(
    *,
    requested_path_id: str | None,
    persisted_binding: MasteryPathBinding | dict[str, Any] | None,
) -> MasteryPathBinding | None:
    """Resolve an explicit selection or an already server-authored binding.

    An explicitly supplied invalid ID never falls back to a prior selection:
    that would silently train against a different subject than the one the
    caller asked for.  With no selection, a surviving persisted binding is
    safe to reuse only after the same live validation.
    """
    requested = str(requested_path_id or "").strip()
    if requested:
        return create_mastery_path_binding(requested)
    loaded = load_bound_mastery_progress(persisted_binding)
    return loaded[0] if loaded is not None else None


__all__ = [
    "MasteryPathBinding",
    "create_mastery_path_binding",
    "learning_graph_fingerprint",
    "load_bound_mastery_progress",
    "resolve_mastery_path_binding",
]
