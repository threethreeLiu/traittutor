"""Branch-isolated, cumulative source inventory for the chat capability.

The chat pipeline shows the LLM an "Attached Sources" manifest each turn so
it can decide whether to call ``read_source(id)`` for full text. Historically
this manifest only listed sources the user attached in the *current* turn —
so the model forgot anything uploaded in earlier turns unless re-attached.

This module materialises the manifest as a **session-cumulative inventory**:

* Sources attached in the current turn (the "fresh" set) get a full preview
  in the manifest, just like before.
* Sources attached in *prior* turns on the active branch's ancestor chain
  (the "historical" set) get a compact one-line row: id, name, kind, size,
  and the turn ordinal where they first appeared. The LLM can call
  ``read_source(id)`` to load full text when the question warrants it.

Both sets dedupe by source id; fresh always wins on collision. Branch
isolation is enforced by walking ``parent_message_id`` from the active
branch's leaf, so sibling branches never leak sources into each other.

The output is decoupled from the rest of ``turn_runtime``:

    inventory = await build_inventory(store, ..., fresh_*=...)
    manifest_text, source_index = render_manifest(inventory)

``source_index`` is the per-turn ``{source_id: full_text}`` map handed to
``ReadSourceTool`` via tool-call kwargs injection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Sequence

from traittutor.services.session.protocol import SessionStoreProtocol

logger = logging.getLogger(__name__)

# Per-source text-preview caps. Fresh sources get a meaningful preview so
# the model can answer simple "is this the right one?" questions without
# read_source. Historical sources surface only their identity — the model
# pays the read_source cost only when it actually needs them.
MANIFEST_PREVIEW_CHARS_FRESH = 2000
# Image attachments flow through the multimodal block path; never list them.
_IMAGE_MIME_PREFIX = "image/"


@dataclass(frozen=True)
class SourceEntry:
    """One row in the per-turn Attached Sources manifest."""

    sid: str
    kind: str  # "notebook" | "book" | "history" | "question" | "attachment"
    name: str
    full_text: str
    fresh: bool
    # 1-indexed ordinal of the user turn this source first appeared in,
    # within the active branch's lineage. Fresh sources use the **current**
    # turn's ordinal so the manifest can label them consistently.
    first_seen_turn: int

    @property
    def char_count(self) -> int:
        return len(self.full_text)


@dataclass
class SourceInventory:
    """Ordered set of ``SourceEntry`` keyed by ``sid``.

    ``add`` is the only mutator. On duplicate ``sid`` the existing entry is
    upgraded to ``fresh=True`` if the incoming entry is fresh — so a source
    attached in the current turn always renders with a preview even if it
    was also attached in a prior turn.
    """

    entries: list[SourceEntry] = field(default_factory=list)
    _index: dict[str, int] = field(default_factory=dict, repr=False)

    def add(self, entry: SourceEntry) -> None:
        if not entry.sid:
            return
        if not entry.full_text.strip():
            return
        existing_pos = self._index.get(entry.sid)
        if existing_pos is None:
            self._index[entry.sid] = len(self.entries)
            self.entries.append(entry)
            return
        existing = self.entries[existing_pos]
        # Fresh always wins; otherwise keep the earlier registration.
        if entry.fresh and not existing.fresh:
            self.entries[existing_pos] = entry

    def is_empty(self) -> bool:
        return not self.entries

    def __contains__(self, sid: str) -> bool:
        return sid in self._index


# ---------------------------------------------------------------------------
# Public API: build + render
# ---------------------------------------------------------------------------


async def build_inventory(
    store: SessionStoreProtocol,
    *,
    session_id: str,
    leaf_message_id: int | None,
    current_turn_ordinal: int,
    fresh_attachment_records: Sequence[dict[str, Any]],
    fresh_notebook_records: Sequence[dict[str, Any]],
    fresh_history_session_ids: Sequence[Any],
    fresh_question_entry_ids: Sequence[Any],
    fresh_learning_artifact_references: Sequence[Any] = (),
    language: str = "en",
) -> SourceInventory:
    """Compose the session-cumulative inventory for one chat turn.

    Fresh refs are added first (so they shadow historical entries on the
    same sid); historical refs are then collected from the active branch's
    ancestor messages. The caller passes already-resolved notebook records
    and the already-rendered book context — keeping side-effects (LLM
    summarisation, file I/O) under the caller's control rather than buried
    inside this module.
    """
    inv = SourceInventory()
    _add_fresh(
        inv,
        current_turn_ordinal=current_turn_ordinal,
        attachment_records=fresh_attachment_records,
        notebook_records=fresh_notebook_records,
    )
    # History + question entries are async (per-id store fetches), keep them
    # in a separate phase so the sync fresh additions don't block.
    await _add_fresh_history(
        inv,
        store=store,
        history_session_ids=fresh_history_session_ids,
        current_turn_ordinal=current_turn_ordinal,
        language=language,
    )
    await _add_fresh_questions(
        inv,
        store=store,
        question_entry_ids=fresh_question_entry_ids,
        current_turn_ordinal=current_turn_ordinal,
    )
    _add_fresh_learning_artifacts(
        inv,
        learning_artifact_references=fresh_learning_artifact_references,
        current_turn_ordinal=current_turn_ordinal,
    )
    await _add_historical(
        inv,
        store=store,
        session_id=session_id,
        leaf_message_id=leaf_message_id,
        language=language,
    )
    return inv


def render_manifest(inv: SourceInventory) -> tuple[str, dict[str, str]]:
    """Render the inventory into (manifest_text, source_index).

    ``manifest_text`` is the human/LLM-readable block injected at the tail
    of the chat system prompt. ``source_index`` maps each source id to its
    full extracted text and is handed to ``ReadSourceTool`` so the LLM can
    read on demand.
    """
    if inv.is_empty():
        return "", {}

    source_index: dict[str, str] = {sid: e.full_text for sid, e in _iter_sid_entries(inv)}
    rendered_rows: list[str] = []
    for entry in inv.entries:
        rendered_rows.append(_render_row(entry))

    header = (
        "[Attached Sources]\n"
        "An index of the sources the user has attached in this conversation. "
        "Rows with a `preview` field were attached **this turn**; rows marked "
        "`previously attached (turn N)` were uploaded in earlier turns and show "
        "only their identity. Their full text can be loaded on demand when a "
        "source is relevant. Refer to sources by name; never invent source ids."
    )
    return header + "\n\n" + "\n\n".join(rendered_rows), source_index


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_sid_entries(inv: SourceInventory):
    seen: set[str] = set()
    for e in inv.entries:
        if e.sid in seen:
            continue
        seen.add(e.sid)
        yield e.sid, e


def _clip_preview(text: str, limit: int = MANIFEST_PREVIEW_CHARS_FRESH) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def _format_size(char_count: int) -> str:
    """Compact size hint for historical rows ('~3 KB', '~120 chars')."""
    if char_count >= 1024:
        return f"~{round(char_count / 1024)} KB"
    return f"~{char_count} chars"


def _render_row(entry: SourceEntry) -> str:
    if entry.fresh:
        preview = _clip_preview(entry.full_text)
        return f"- id={entry.sid}  type={entry.kind}  name={entry.name!r}\n  preview: {preview!r}"
    return (
        f"- id={entry.sid}  type={entry.kind}  name={entry.name!r}"
        f"  size={_format_size(entry.char_count)}  "
        f"source: previously attached (turn {entry.first_seen_turn})"
    )


# ----- Fresh source addition (current-turn payload) -----------------------


def _add_fresh(
    inv: SourceInventory,
    *,
    current_turn_ordinal: int,
    attachment_records: Sequence[dict[str, Any]],
    notebook_records: Sequence[dict[str, Any]],
) -> None:
    """Add the synchronously-available fresh sources (notebook records and attachments)."""
    for rec in notebook_records:
        rid = str(rec.get("id", "") or "").strip()
        full = str(rec.get("output", "") or "")
        if not rid or not full.strip():
            continue
        inv.add(
            SourceEntry(
                sid=f"nb-{rid}",
                kind="notebook",
                name=str(rec.get("title") or rec.get("name") or "Untitled record"),
                full_text=full,
                fresh=True,
                first_seen_turn=current_turn_ordinal,
            )
        )

    for record in attachment_records:
        if str(record.get("type", "")).lower() == "image":
            continue
        mime = str(record.get("mime_type", "")).lower()
        if mime.startswith(_IMAGE_MIME_PREFIX):
            continue
        text = str(record.get("extracted_text", "") or "")
        att_id = str(record.get("id", "") or "").strip()
        if not text.strip() or not att_id:
            continue
        inv.add(
            SourceEntry(
                sid=f"at-{att_id}",
                kind="attachment",
                name=str(record.get("filename") or "Untitled file"),
                full_text=text,
                fresh=True,
                first_seen_turn=current_turn_ordinal,
            )
        )


async def _add_fresh_history(
    inv: SourceInventory,
    *,
    store: SessionStoreProtocol,
    history_session_ids: Sequence[Any],
    current_turn_ordinal: int,
    language: str = "en",
) -> None:
    for raw in history_session_ids:
        hs_id = str(raw or "").strip()
        if not hs_id:
            continue
        text, name = await _load_history_session(store, hs_id, language=language)
        if not text:
            continue
        inv.add(
            SourceEntry(
                sid=f"hs-{hs_id}",
                kind="history",
                name=name,
                full_text=text,
                fresh=True,
                first_seen_turn=current_turn_ordinal,
            )
        )


async def _add_fresh_questions(
    inv: SourceInventory,
    *,
    store: SessionStoreProtocol,
    question_entry_ids: Sequence[Any],
    current_turn_ordinal: int,
) -> None:
    get_entry = getattr(store, "get_notebook_entry", None)
    if not callable(get_entry):
        return
    for raw in question_entry_ids:
        try:
            eid = int(raw)
        except (TypeError, ValueError):
            continue
        block, stem = await _load_question_entry(store, eid)
        if not block:
            continue
        inv.add(
            SourceEntry(
                sid=f"qb-{eid}",
                kind="question",
                name=stem,
                full_text=block,
                fresh=True,
                first_seen_turn=current_turn_ordinal,
            )
        )


# ----- Historical source collection ---------------------------------------


async def _add_historical(
    inv: SourceInventory,
    *,
    store: SessionStoreProtocol,
    session_id: str,
    leaf_message_id: int | None,
    language: str = "en",
) -> None:
    """Walk the active branch's ancestor user messages and pull in
    references they carried. Sources already in ``inv`` (i.e. fresh
    duplicates) are skipped — fresh entries always win."""
    lineage = await _load_lineage(store, session_id, leaf_message_id)
    user_turn_ordinal = 0
    for msg in lineage:
        if msg.get("role") != "user":
            continue
        user_turn_ordinal += 1
        await _collect_from_user_message(
            inv, store=store, msg=msg, turn_ordinal=user_turn_ordinal, language=language
        )


async def _collect_from_user_message(
    inv: SourceInventory,
    *,
    store: SessionStoreProtocol,
    msg: dict[str, Any],
    turn_ordinal: int,
    language: str = "en",
) -> None:
    """Drain one prior user message into the inventory as historical
    entries. Attachments are pulled from the persisted ``attachments``
    JSON; space refs are pulled from ``metadata.request_snapshot`` and
    re-resolved through their respective services so the historical
    full text always reflects the current state of the referenced object.
    """
    # Attachments — extracted_text was persisted at upload time, no
    # external lookup needed.
    for att in msg.get("attachments") or []:
        att_id = str(att.get("id", "") or "").strip()
        if not att_id:
            continue
        sid = f"at-{att_id}"
        if sid in inv:
            continue
        mime = str(att.get("mime_type", "")).lower()
        if mime.startswith(_IMAGE_MIME_PREFIX):
            continue
        text = str(att.get("extracted_text") or "")
        if not text.strip():
            continue
        inv.add(
            SourceEntry(
                sid=sid,
                kind="attachment",
                name=str(att.get("filename") or "Untitled file"),
                full_text=text,
                fresh=False,
                first_seen_turn=turn_ordinal,
            )
        )

    snap = (msg.get("metadata") or {}).get("request_snapshot") or {}
    if not isinstance(snap, dict):
        return

    # Notebook records — re-resolve through the notebook service.
    notebook_refs = snap.get("notebookReferences") or []
    if notebook_refs:
        from traittutor.services.notebook import get_notebook_manager

        try:
            records = get_notebook_manager().get_records_by_references(list(notebook_refs))
        except Exception:
            records = []
        for rec in records:
            rid = str(rec.get("id", "") or "").strip()
            if not rid:
                continue
            sid = f"nb-{rid}"
            if sid in inv:
                continue
            full = str(rec.get("output", "") or "")
            if not full.strip():
                continue
            inv.add(
                SourceEntry(
                    sid=sid,
                    kind="notebook",
                    name=str(rec.get("title") or rec.get("name") or "Untitled record"),
                    full_text=full,
                    fresh=False,
                    first_seen_turn=turn_ordinal,
                )
            )

    # History sessions — async, one store fetch per id.
    for raw in snap.get("historyReferences") or []:
        hs_id = str(raw or "").strip()
        if not hs_id:
            continue
        sid = f"hs-{hs_id}"
        if sid in inv:
            continue
        text, name = await _load_history_session(store, hs_id, language=language)
        if not text:
            continue
        inv.add(
            SourceEntry(
                sid=sid,
                kind="history",
                name=name,
                full_text=text,
                fresh=False,
                first_seen_turn=turn_ordinal,
            )
        )

    # Question-bank entries.
    for raw in snap.get("questionNotebookReferences") or []:
        try:
            eid = int(raw)
        except (TypeError, ValueError):
            continue
        sid = f"qb-{eid}"
        if sid in inv:
            continue
        block, stem = await _load_question_entry(store, eid)
        if not block:
            continue
        inv.add(
            SourceEntry(
                sid=sid,
                kind="question",
                name=stem,
                full_text=block,
                fresh=False,
                first_seen_turn=turn_ordinal,
            )
        )

    # TraitTutor learning artifacts generated in the Learning Space.
    for ref in snap.get("learningArtifactReferences") or []:
        entry = _resolve_learning_artifact(ref, fresh=False, first_seen_turn=turn_ordinal)
        if entry is not None:
            inv.add(entry)


# ----- Lineage walker (branch-safe, store-protocol-compatible) ------------


async def _load_lineage(
    store: SessionStoreProtocol,
    session_id: str,
    leaf_message_id: int | None,
) -> list[dict[str, Any]]:
    """Return the active branch's ancestor user/assistant messages in
    chronological order. When ``leaf_message_id`` is ``None`` (legacy
    linear append), returns every message in the session. When it's set
    (branched edit), walks the ``parent_message_id`` chain up from that
    leaf so sibling branches are excluded. Uses ``get_messages`` (which
    every store implements) plus a Python-side parent walk, avoiding a
    sqlite-only ``get_message_path``.
    """
    all_msgs = await store.get_messages(session_id)
    if leaf_message_id is None:
        return all_msgs
    by_id: dict[int, dict[str, Any]] = {}
    for m in all_msgs:
        mid = m.get("id")
        if mid is not None:
            by_id[int(mid)] = m
    chain: list[dict[str, Any]] = []
    current: int | None = int(leaf_message_id)
    safety = 10_000
    while current is not None and safety > 0:
        m = by_id.get(int(current))
        if m is None:
            break
        chain.append(m)
        parent = m.get("parent_message_id")
        current = int(parent) if parent is not None else None
        safety -= 1
    chain.reverse()
    return chain


def _add_fresh_learning_artifacts(
    inv: SourceInventory,
    *,
    learning_artifact_references: Sequence[Any],
    current_turn_ordinal: int,
) -> None:
    for ref in learning_artifact_references:
        entry = _resolve_learning_artifact(ref, fresh=True, first_seen_turn=current_turn_ordinal)
        if entry is not None:
            inv.add(entry)


def _resolve_learning_artifact(
    reference: Any,
    *,
    fresh: bool,
    first_seen_turn: int,
) -> SourceEntry | None:
    if not isinstance(reference, dict):
        return None
    pack_id = str(reference.get("pack_id") or "").strip()
    artifact_type = str(reference.get("artifact_type") or "").strip()
    if not pack_id or artifact_type not in {"courseware", "flashcards", "quiz"}:
        return None
    try:
        raw_index = reference.get("artifact_index")
        requested_index = int(raw_index) if raw_index is not None else -1
    except (TypeError, ValueError):
        requested_index = -1

    try:
        from traittutor import learning_packs

        pack = learning_packs.get_pack(pack_id)
    except Exception:
        logger.debug("Failed to resolve learning artifact pack %r", pack_id, exc_info=True)
        return None
    if not isinstance(pack, dict):
        return None
    artifacts = pack.get("artifacts") if isinstance(pack.get("artifacts"), dict) else {}
    candidates = artifacts.get(artifact_type) if isinstance(artifacts, dict) else []
    if not isinstance(candidates, list) or not candidates:
        return None
    index = requested_index if 0 <= requested_index < len(candidates) else len(candidates) - 1
    artifact = candidates[index]
    if not isinstance(artifact, dict):
        return None
    full_text = _serialize_learning_artifact(pack, artifact_type, artifact)
    if not full_text.strip():
        return None
    title = str(artifact.get("title") or pack.get("title") or "Learning artifact")
    return SourceEntry(
        sid=f"lp-{pack_id}-{artifact_type}-{index}",
        kind="learning_artifact",
        name=f"{title} ({artifact_type})",
        full_text=full_text,
        fresh=fresh,
        first_seen_turn=first_seen_turn,
    )


def _serialize_learning_artifact(
    pack: dict[str, Any],
    artifact_type: str,
    artifact: dict[str, Any],
) -> str:
    material = pack.get("material") if isinstance(pack.get("material"), dict) else {}
    lines = [
        f"# TraitTutor Learning Artifact: {artifact.get('title') or pack.get('title') or 'Untitled'}",
        f"- pack_id: {pack.get('pack_id')}",
        f"- artifact_type: {artifact_type}",
        f"- material_title: {material.get('title') or pack.get('title') or 'Untitled material'}",
        "",
    ]
    if artifact_type == "courseware":
        markdown = str(artifact.get("markdown") or "").strip()
        if markdown:
            lines.extend(["## Courseware Markdown", markdown])
        sections = artifact.get("sections")
        if isinstance(sections, list):
            rendered = []
            for idx, section in enumerate(sections, start=1):
                if not isinstance(section, dict):
                    continue
                title = str(section.get("title") or section.get("section_title") or f"Section {idx}")
                content = section.get("content")
                if isinstance(content, list):
                    body = "\n".join(str(item) for item in content if str(item).strip())
                else:
                    body = str(section.get("core_content") or content or "")
                if body.strip():
                    rendered.append(f"### {title}\n{body}")
            if rendered:
                lines.extend(["## Courseware Sections", "\n\n".join(rendered)])
    elif artifact_type == "flashcards":
        items = artifact.get("items")
        if isinstance(items, list):
            rendered = []
            for idx, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                front = str(item.get("front") or item.get("question") or "").strip()
                back = str(item.get("back") or item.get("answer") or "").strip()
                if front or back:
                    rendered.append(f"{idx}. Front: {front}\n   Back: {back}")
            if rendered:
                lines.extend(["## Flashcards", "\n\n".join(rendered)])
    elif artifact_type == "quiz":
        items = artifact.get("items")
        if isinstance(items, list):
            rendered = []
            for idx, item in enumerate(items, start=1):
                if not isinstance(item, dict):
                    continue
                question = str(item.get("question") or "").strip()
                answer = str(item.get("correct_answer") or "").strip()
                explanation = str(item.get("explanation") or "").strip()
                options = item.get("options")
                option_text = ""
                if isinstance(options, list):
                    option_text = "\n".join(
                        f"   - {opt.get('text') if isinstance(opt, dict) else opt}"
                        for opt in options
                    )
                rendered.append(
                    f"{idx}. Question: {question}\n"
                    f"{option_text}\n"
                    f"   Correct answer: {answer}\n"
                    f"   Explanation: {explanation}".strip()
                )
            if rendered:
                lines.extend(["## Quiz", "\n\n".join(rendered)])
    return "\n".join(lines).strip()


# Human labels for the external agents a session can be imported from. The
# import source is recorded at import time in ``preferences["import"]["source"]``
# (see ``traittutor/api/routers/imports.py``).
_EXTERNAL_AGENT_LABELS: dict[str, str] = {
    "claude_code": "Claude Code",
    "codex": "Codex",
}


def _imported_agent_label(meta: dict[str, Any], lang: str) -> str | None:
    """Return a human label for the external agent a session was imported from,
    or ``None`` when the session is *not* an imported external-agent transcript.

    A referenced session is "imported" when its id carries the ``imported_``
    prefix or its preferences hold the ``import`` block written at import time.
    A referenced *partner* session (resolved by :func:`_load_partner_session`)
    carries ``source == "partner"`` and is framed with the partner's own name.
    """
    prefs = meta.get("preferences") if isinstance(meta, dict) else None
    import_meta = prefs.get("import") if isinstance(prefs, dict) else None
    source = str((import_meta or {}).get("source") or "").strip().lower()
    sid = str(meta.get("session_id") or meta.get("id") or "")
    if source == "partner":
        name = str(meta.get("partner_name") or "").strip()
        return name or ("伙伴" if lang == "zh" else "a partner")
    if not source and not sid.startswith("imported_"):
        return None
    if source in _EXTERNAL_AGENT_LABELS:
        return _EXTERNAL_AGENT_LABELS[source]
    return "外部 AI 助手" if lang == "zh" else "an external AI assistant"


def serialize_referenced_transcript(
    meta: dict[str, Any],
    messages: Sequence[dict[str, Any]],
    *,
    language: str = "en",
) -> str:
    """Serialize a *referenced* conversation into a clearly-framed transcript.

    A referenced session is material the user attached for the assistant to
    read and discuss — it is **not** the current conversation. Two failure
    modes motivated this framing:

    * An imported session is a transcript of the user talking to a *different*
      AI agent. Rendered as bare ``## Assistant`` turns it reads exactly like
      the model's own past replies, so the model adopts that agent's first
      person voice and even claims its actions as its own.
    * Even a referenced *native* session is a separate conversation, not the
      current one.

    The fix is structural: prepend an explicit boundary header and name the
    other party (the external agent for imports) so the role label can never
    be confused with the model's own ``assistant`` role. Returns ``""`` when
    there is no content to serialize.
    """
    lang = "zh" if str(language or "en").lower().startswith("zh") else "en"
    agent = _imported_agent_label(meta, lang)
    if agent is not None:
        assistant_label = agent
        header = (
            f"〔以下是用户与外部 AI 助手「{agent}」的历史对话记录，由用户附带进来供你参考和讨论。"
            "这不是你与用户的对话——你没有参与其中，也没有执行其中的任何动作。"
            "请把它当作第三方材料客观对待：复述时用第三人称，不要沿用其口吻，"
            "也不要把其中助手做过的事说成是你做的。〕"
            if lang == "zh"
            else (
                f"[The following is a transcript of a past conversation between the user and an "
                f"external AI assistant ({agent}), attached by the user for your reference and "
                "discussion. This is NOT your conversation with the user — you did not take part "
                "in it and performed none of its actions. Treat it as third-party material: "
                "describe it in the third person, do not adopt its voice, and never claim its "
                "assistant's actions as your own.]"
            )
        )
    else:
        assistant_label = "Assistant"
        header = (
            "〔以下是另一段历史对话记录，由用户附带进来供你参考。它不是当前对话的一部分。〕"
            if lang == "zh"
            else (
                "[The following is a transcript of a separate past conversation, attached by the "
                "user for reference. It is not part of the current conversation.]"
            )
        )
    user_label = "用户" if lang == "zh" else "User"
    lines: list[str] = []
    for message in messages:
        content = str(message.get("content", "") or "").strip()
        if not content:
            continue
        role = str(message.get("role", "")).strip().lower()
        if role == "user":
            label = user_label
        elif role == "assistant":
            label = assistant_label
        else:
            label = role.title() or "Message"
        lines.append(f"## {label}\n{content}")
    if not lines:
        return ""
    return header + "\n\n" + "\n\n".join(lines)


# A referenced *partner* session: ``partner:{partner_id}:{session_key}``. The
# session_key itself may contain ``:`` (e.g. ``web:abc``), so only the partner
# id is split off — partner ids are colon-free slugs.
_PARTNER_REF_PREFIX = "partner:"


def _load_partner_session(ref: str, *, language: str = "en") -> tuple[str, str]:
    """Resolve a ``partner:{pid}:{session_key}`` reference into transcript + title.

    Partner conversations live in the admin-scoped ``PartnerSessionStore`` (one
    JSONL per session under ``data/partners/<id>/sessions/``), not the main
    session store — so they resolve here through the partner manager rather than
    ``store.get_session``. Gated to admins: partner data is admin-scoped (the
    whole partners API is admin-gated), and this read runs inside the user's
    turn, so a non-admin must not be able to pull a partner's transcript by
    hand-crafting a reference id. Returns ``("", "")`` on any miss.
    """
    rest = ref[len(_PARTNER_REF_PREFIX) :]
    pid, _, session_key = rest.partition(":")
    pid, session_key = pid.strip(), session_key.strip()
    if not pid or not session_key:
        return "", ""

    from traittutor.multi_user.context import get_current_user

    if not get_current_user().is_admin:
        return "", ""
    try:
        from traittutor.services.partners import get_partner_manager

        manager = get_partner_manager()
        if not manager.partner_exists(pid):
            return "", ""
        messages = manager.get_history(pid, session_key=session_key, limit=400)
        config = manager.load_config(pid)
    except Exception:
        logger.debug("Failed to resolve partner session %r", ref, exc_info=True)
        return "", ""

    partner_name = (getattr(config, "name", "") or pid).strip()
    meta = {"preferences": {"import": {"source": "partner"}}, "partner_name": partner_name}
    transcript = serialize_referenced_transcript(meta, messages, language=language)
    if not transcript:
        return "", ""
    opener = next((m for m in messages if str(m.get("role")) == "user"), None)
    first_line = str((opener or {}).get("content", "") or "").strip().splitlines()
    title = (first_line[0][:60].strip() if first_line else "") or partner_name
    return transcript, title


async def _load_history_session(
    store: SessionStoreProtocol,
    history_session_id: str,
    *,
    language: str = "en",
) -> tuple[str, str]:
    """Fetch and serialize a referenced history session into transcript +
    title. Returns ``("", "")`` when the session is empty or missing.

    A ``partner:`` reference resolves through the partner store instead of the
    main session store (see :func:`_load_partner_session`).
    """
    if history_session_id.startswith(_PARTNER_REF_PREFIX):
        return _load_partner_session(history_session_id, language=language)
    try:
        meta = await store.get_session(history_session_id)
    except Exception:
        meta = None
    if not meta:
        return "", ""
    try:
        messages_in_hs = await store.get_messages_for_context(history_session_id)
    except Exception:
        messages_in_hs = []
    transcript = serialize_referenced_transcript(meta, messages_in_hs, language=language)
    if not transcript:
        return "", ""
    name = str(meta.get("title", "") or "Untitled session")
    return transcript, name


async def _load_question_entry(store: SessionStoreProtocol, entry_id: int) -> tuple[str, str]:
    """Fetch and render one question-bank entry into a markdown block +
    short stem (used as the manifest name). Returns ``("", "")`` on
    missing entry. Imports the renderer lazily to avoid pulling
    ``turn_runtime``'s import surface into modules that consume this one."""
    get_entry = getattr(store, "get_notebook_entry", None)
    if not callable(get_entry):
        return "", ""
    try:
        entry = await get_entry(entry_id)
    except Exception:
        entry = None
    if not entry:
        return "", ""
    # Use the existing turn_runtime helper for consistency with fresh-path
    # formatting. Imported here to keep this module's static deps minimal.
    from traittutor.services.session.turn_runtime import _format_question_bank_entry

    block = _format_question_bank_entry(entry)
    if not block.strip():
        return "", ""
    stem_source = str(entry.get("question", "") or "Untitled question")
    stem = stem_source[:60].rstrip() or "Untitled question"
    return block, stem


__all__ = [
    "MANIFEST_PREVIEW_CHARS_FRESH",
    "SourceEntry",
    "SourceInventory",
    "build_inventory",
    "render_manifest",
    "serialize_referenced_transcript",
]
