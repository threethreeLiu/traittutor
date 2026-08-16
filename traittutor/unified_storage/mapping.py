"""Declarative source registry + owner resolver for the unified storage baseline.

This is the single source of truth for legacy-source inventory and the
source-to-target baseline.  Both the read-only inventory
(:mod:`traittutor.unified_storage.inventory`) and the dry-run planner
(:mod:`traittutor.unified_storage.dry_run`) consume :data:`SOURCE_SPECS`, so
adding a domain means appending one :class:`SourceSpec` — no scattered edits.

Every row records, in one place:

* where the source lives on disk (``relative_path``, relative to ``user_data_dir``),
* which store owns it (``store_module`` — documentation / cross-check only),
* how its records are structured (``kind`` + ``sections``),
* how the owner is resolved (``owner``), and
* whether the source belongs to the declared migration baseline (``listed_in_plan``).

The inventory walks the *actual* tree and cross-references this table, surfacing
every divergence (missing expected source, discovered-but-unmapped section,
broken owner join) as an :class:`~traittutor.unified_storage.models.Anomaly`
rather than silently skipping it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
from typing import Literal

from .models import OwnerStrategy

SourceKind = Literal["json_sectioned", "json_list", "json_per_task_file", "sqlite"]

# Sentinel section names used when the source is not a sectioned dict.
LIST_SECTION = "<list>"  # json_list: the whole file is one array of records.
FILE_SECTION = "<file>"  # json_per_task_file: each file is one record.


@dataclass(frozen=True)
class OwnerSpec:
    """How to resolve the owner for every record in a source.

    ``field`` is overloaded by strategy on purpose:

    * ``DIRECT_OWNER_ID`` / ``USER_ID_FIELD`` → the record field holding the
      owner id (``owner_id`` or ``user_id``).
    * ``JOIN_GENERATION_TASK`` → the record field holding the join key
      (``generation_run_id`` / ``generation_id``); the actual owner is looked up
      via :class:`OwnerResolver`.
    * ``SQLITE_COLUMN`` → the column holding the owner (on ``sqlite_owner_table``).
    * ``PATH_SCOPE`` / ``UNRESOLVED`` → ignored; owner is the workspace scope or
      not resolvable.
    """

    strategy: OwnerStrategy
    field: str | None = None
    join_source: str | None = None
    join_lookup_key: str | None = None
    join_owner_field: str | None = None


@dataclass(frozen=True)
class SectionSpec:
    """One record collection within a source and its target table.

    ``section`` is the actual top-level key for ``json_sectioned`` sources, or a
    sentinel (:data:`LIST_SECTION` / :data:`FILE_SECTION`) for the other kinds.

    ``owner_join_field`` lets a section inherit its owner from another section of
    the same source via a join field, when the record carries no owner of its
    own.  Used by the learner-event derived ledger (amendments / derived_applied
    / derived_queue): each entry references ``event_id`` but has no ``user_id``,
    so its owner is the referenced event's owner — a deterministic intra-source
    join, not a guess.  The join target is the section whose ``id_field`` equals
    this join field (e.g. the ``events`` section, keyed by ``event_id``).
    """

    section: str
    target_table: str
    id_field: str | None = None
    owner_join_field: str | None = None


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: SourceKind
    relative_path: str  # relative to user_data_dir; glob for json_per_task_file
    store_module: str
    owner: OwnerSpec
    sections: tuple[SectionSpec, ...] = field(default_factory=tuple)
    sqlite_tables: tuple[str, ...] = field(default_factory=tuple)
    sqlite_owner_table: str | None = None
    sqlite_owner_column: str | None = None
    listed_in_plan: bool = True
    # Per-task files whose payload has every listed field empty/null are flagged
    # as residue (never-produced artifacts).  Empty means ``None``, ``{}``,
    # ``[]``, ``""`` or ``{"items": []}`` — the shape a generation result takes
    # when the run produced nothing.  Left empty for sources without a residue
    # signal.
    residue_if_empty_fields: tuple[str, ...] = ()


# ── Join definition shared by the page-schema / orchestrator-run / generation
#    result sources.  Their owner is resolved by joining the record's
#    ``generation_run_id`` (or ``generation_id``) to the generation-task file of
#    the same id, then reading that task's ``owner_id``.  On the real workspace
#    this join is *broken* (0/7 page-schemas, 0/3 runs, 2/354 results resolve) —
#    a fact the baseline must surface, not flatten.
_GENERATION_TASK_JOIN = OwnerSpec(
    strategy=OwnerStrategy.JOIN_GENERATION_TASK,
    field="generation_run_id",
    join_source="generation_tasks",
    join_lookup_key="generation_id",
    join_owner_field="owner_id",
)

# ── The registry.  Order groups related sources; it is not load-order-critical
#    because the inventory resolves join-based owners in a second pass after the
#    generation-task join map is built.
SOURCE_SPECS: tuple[SourceSpec, ...] = (
    # ── Low-coupling business records (Phase 2) ──────────────────────────────
    SourceSpec(
        name="capability_decisions",
        kind="json_sectioned",
        relative_path="workspace/traittutor/capability_decisions.json",
        store_module="traittutor.assistant_routing.store",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            SectionSpec("decisions", "capability_decisions", "decision_id"),
            SectionSpec("idempotency", "capability_idempotency", "decision_id"),
        ),
    ),
    SourceSpec(
        name="page_schemas",
        kind="json_sectioned",
        relative_path="workspace/traittutor/page-schemas.json",
        store_module="traittutor.components.page_store",
        owner=_GENERATION_TASK_JOIN,
        sections=(SectionSpec("pages", "page_schemas", "page_schema_id"),),
    ),
    SourceSpec(
        name="tutor_personas",
        kind="json_sectioned",
        relative_path="workspace/traittutor/tutor_personas.json",
        store_module="traittutor.tutor_persona.store",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            # Profile revisions reuse persona_id and append immutable versions.
            SectionSpec("profiles", "tutor_personas", None),
            SectionSpec("idempotency", "tutor_persona_versions", "persona_id"),
        ),
    ),
    SourceSpec(
        name="tutor_reminders",
        kind="json_sectioned",
        relative_path="workspace/traittutor/persona-reminders.json",
        store_module="traittutor.tutor_persona.reminder_outbox",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            SectionSpec("reminders", "tutor_reminders", "reminder_id"),
            SectionSpec("audit", "tutor_reminder_audit", None),
        ),
    ),
    SourceSpec(
        name="orchestrator_runs",
        kind="json_sectioned",
        relative_path="workspace/traittutor/orchestrator-runs.json",
        store_module="traittutor.orchestration.run_store",
        owner=_GENERATION_TASK_JOIN,
        sections=(
            SectionSpec("runs", "generation_runs", "run_id"),
            SectionSpec("plans", "generation_agentic_plans", "request_key"),
            SectionSpec(
                "checkpoints",
                "generation_agentic_checkpoints",
                "checkpoint_id",
            ),
            SectionSpec(
                "budget_reservations",
                "generation_agentic_budget_reservations",
                "reservation_id",
            ),
        ),
    ),
    SourceSpec(
        name="conversations",
        kind="json_sectioned",
        relative_path="workspace/traittutor/conversations.json",
        store_module="traittutor.conversation.store",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            SectionSpec("threads", "conversation_threads", "thread_id"),
            SectionSpec("turns", "conversation_turns", "turn_id"),
            # Episode revisions intentionally retain the same episode_id and
            # append summary_version rows. A content-derived storage key keeps
            # every immutable version instead of replacing history.
            SectionSpec("episodes", "conversation_episodes", None),
            SectionSpec("working_states", "conversation_states", None),
            SectionSpec("open_loops", "conversation_open_loops", None),
            SectionSpec("session_bindings", "conversation_session_bindings", None),
        ),
    ),
    # ── Learning domain (Phase 3) ────────────────────────────────────────────
    SourceSpec(
        name="learning_packs",
        kind="json_list",
        relative_path="workspace/traittutor/learning-packs.json",
        store_module="traittutor.learning_packs",
        # Packs carry owner_id, but the file-level store is path-scoped and not
        # owner-bound, so missing owner_id falls back to the workspace scope.
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec(LIST_SECTION, "learning_packs", "pack_id"),),
    ),
    SourceSpec(
        name="learning_progress",
        kind="json_sectioned",
        relative_path="workspace/learning/learning-progress.json",
        store_module="traittutor.learning.storage",
        owner=OwnerSpec(OwnerStrategy.PATH_SCOPE),
        sections=(SectionSpec("progress", "learning_progress", "book_id"),),
    ),
    SourceSpec(
        name="notebooks",
        kind="json_sectioned",
        relative_path="workspace/notebook/notebooks.json",
        store_module="traittutor.services.notebook.service",
        owner=OwnerSpec(OwnerStrategy.PATH_SCOPE),
        sections=(SectionSpec("notebooks", "notebooks", "id"),),
    ),
    SourceSpec(
        name="personalization_state",
        kind="json_sectioned",
        relative_path="memory/learner/personalization.json",
        store_module="traittutor.personalization.service",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            SectionSpec("profiles", "learner_profiles", "storage_key"),
            SectionSpec("signals", "learner_signals", "signal_id"),
            SectionSpec("sessions", "learner_sessions", "session_id"),
            SectionSpec("jobs", "learner_jobs", "job_id"),
        ),
    ),
    SourceSpec(
        name="legacy_agent_sessions",
        kind="json_sectioned",
        relative_path="sessions/agent-sessions.json",
        store_module="traittutor.services.session.base_session_manager",
        owner=OwnerSpec(OwnerStrategy.PATH_SCOPE),
        sections=(SectionSpec("sessions", "agent_sessions", "storage_id"),),
    ),
    SourceSpec(
        name="trait_profiles",
        kind="json_sectioned",
        relative_path="workspace/traittutor/trait-profiles.json",
        store_module="traittutor.assessment.big_five",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("profiles", "trait_profiles", "profile_id"),),
    ),
    SourceSpec(
        name="interface_settings",
        kind="json_sectioned",
        relative_path="settings/interface-state.json",
        store_module="traittutor.api.routers.settings",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("settings", "interface_settings", "settings_id"),),
    ),
    SourceSpec(
        name="user_grants",
        kind="json_sectioned",
        relative_path="system/grants-state.json",
        store_module="traittutor.multi_user.grants",
        owner=OwnerSpec(OwnerStrategy.USER_ID_FIELD, field="user_id"),
        sections=(SectionSpec("grants", "user_grants", "user_id"),),
    ),
    SourceSpec(
        name="user_accounts",
        kind="json_sectioned",
        relative_path="system/auth/accounts-state.json",
        store_module="traittutor.multi_user.identity",
        owner=OwnerSpec(OwnerStrategy.USER_ID_FIELD, field="id"),
        sections=(SectionSpec("users", "user_accounts", "id"),),
    ),
    SourceSpec(
        name="image_material_sources",
        kind="json_sectioned",
        relative_path="workspace/traittutor/image-material-state.json",
        store_module="traittutor.generate.image_material",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("sources", "image_material_sources", "source_id"),),
    ),
    SourceSpec(
        name="material_analyses",
        kind="json_sectioned",
        relative_path="workspace/traittutor/material-analysis-state.json",
        store_module="traittutor.generate.material_analysis",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("analyses", "material_analyses", "analysis_id"),),
    ),
    SourceSpec(
        name="evolution_trails",
        kind="json_sectioned",
        relative_path="memory/trail/trail-state.json",
        store_module="traittutor.services.evolution.store",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("events", "evolution_trails", "trail_id"),),
    ),
    SourceSpec(
        name="knowledge_state",
        kind="json_sectioned",
        relative_path="workspace/traittutor/knowledge-state.json",
        store_module="traittutor.knowledge.state_store",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            SectionSpec("config", "knowledge_config", "config_id"),
            SectionSpec("metadata", "knowledge_metadata", "kb_name"),
            SectionSpec("progress", "knowledge_progress", "kb_name"),
        ),
    ),
    SourceSpec(
        name="mcp_session_state",
        kind="json_sectioned",
        relative_path="workspace/traittutor/mcp-session-state.json",
        store_module="traittutor.services.mcp.session_state",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("sessions", "mcp_session_state", "session_id"),),
    ),
    SourceSpec(
        name="gateway_route_health",
        kind="json_sectioned",
        relative_path="workspace/traittutor/gateway-route-health.json",
        store_module="traittutor.gateway.route_health",
        owner=OwnerSpec(OwnerStrategy.PATH_SCOPE),
        sections=(SectionSpec("routes", "gateway_route_health", "route_key"),),
    ),
    SourceSpec(
        name="skill_state",
        kind="json_sectioned",
        relative_path="workspace/traittutor/skill-state.json",
        store_module="traittutor.services.skill.service",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            SectionSpec("settings", "skill_settings", "settings_id"),
            SectionSpec("origins", "skill_origins", "name"),
        ),
    ),
    SourceSpec(
        name="legacy_research_queues",
        kind="json_sectioned",
        relative_path="workspace/traittutor/research-queue-state.json",
        store_module="traittutor.agents.research.data_structures",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("queues", "legacy_research_queues", "research_id"),),
    ),
    SourceSpec(
        name="learner_events",
        kind="json_sectioned",
        relative_path="workspace/learning_model/learner_events.json",
        store_module="traittutor.learning_model.events",
        # Iron law #2: events carry user_id (the learning owner), not owner_id.
        owner=OwnerSpec(OwnerStrategy.USER_ID_FIELD, field="user_id"),
        sections=(
            SectionSpec("events", "learner_events", "event_id"),
            # The derived ledger carries no user_id; each row inherits the owner
            # of the event it references (a deterministic event_id → user_id
            # join, never a guess).  Iron law #2: this inherits the owner only —
            # it does NOT promote the event's evidence strength.
            SectionSpec(
                "amendments",
                "learner_event_amendments",
                "amendment_id",
                owner_join_field="event_id",
            ),
            SectionSpec(
                "derived_applied",
                "learner_event_derived_applied",
                None,
                owner_join_field="event_id",
            ),
            SectionSpec(
                "derived_queue",
                "learner_event_derived_queue",
                None,
                owner_join_field="event_id",
            ),
        ),
    ),
    SourceSpec(
        name="misconceptions",
        kind="json_sectioned",
        relative_path="workspace/learning_model/misconceptions.json",
        store_module="traittutor.learning_model.misconception",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec("items", "misconceptions", "hypothesis_id"),),
    ),
    # ── Memory (Phase 4) — expected but currently absent on disk ─────────────
    SourceSpec(
        name="memory_v2",
        kind="json_sectioned",
        relative_path="workspace/traittutor/memory-v2.json",
        store_module="traittutor.memory.store",
        # Every persisted section carries owner_id: candidates/items/lifecycle/
        # grants/mutation_receipts store it on the record; access_records wrap
        # their record in an {"owner_id": ..., "record": ...} envelope.
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            # Active durable memory facts (UserMemoryItem).
            SectionSpec("items", "memories", "memory_id"),
            # Proposed memories awaiting activation (MemoryCandidate).
            SectionSpec("candidates", "memory_candidates", "candidate_id"),
            # Append-only lifecycle provenance (MemoryLifecycleRecord).
            SectionSpec("lifecycle", "memory_lifecycle", "record_id"),
            # Cross-scope read audit envelopes (id is nested in record.record_id;
            # use a synthetic content-hash id rather than reaching into the
            # envelope).
            SectionSpec("access_records", "memory_access_records", None),
            # Cross-scope access grants (MemoryGrant).
            SectionSpec("grants", "memory_grants", "grant_id"),
            # Idempotent mutation receipts (operation_id is the stable key).
            SectionSpec("mutation_receipts", "memory_mutation_receipts", "operation_id"),
        ),
    ),
    SourceSpec(
        name="memory_index",
        kind="json_sectioned",
        relative_path="workspace/traittutor/memory-index-v1.json",
        store_module="traittutor.memory.index_store",
        # The index file is owner-less by design (one per-owner file under the
        # workspace); its owner is the workspace it lives in, resolved by path
        # scope — the same path-based ownership as the knowledge graph.  The
        # generation/invalidation ledger is preserved verbatim so fail-closed
        # index fencing survives the migration unchanged.
        owner=OwnerSpec(OwnerStrategy.PATH_SCOPE),
        sections=(
            SectionSpec("states", "memory_index_states", None),
            SectionSpec("indexes", "memory_index_entries", None),
            SectionSpec("invalidations", "memory_index_invalidations", None),
        ),
    ),
    SourceSpec(
        name="research_workspaces",
        kind="json_sectioned",
        relative_path="workspace/traittutor/research_workspaces.json",
        store_module="traittutor.research_workspace.store",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(
            SectionSpec("workspaces", "research_workspaces", "workspace_id"),
            SectionSpec("briefs", "research_briefs", "brief_id"),
            SectionSpec("runs", "research_runs", "run_id"),
            SectionSpec("receipts", "research_task_receipts", "receipt_id"),
            SectionSpec("sources", "research_sources", "source_id"),
            SectionSpec("notes", "research_notes", "note_id"),
            SectionSpec("claims", "research_claims", "claim_id"),
            SectionSpec("reports", "research_reports", "report_id"),
            SectionSpec("operations", "research_operations", None),
        ),
    ),
    # ── Generation run data ──────────────────────────────────────────────────
    SourceSpec(
        name="generation_tasks",
        kind="json_per_task_file",
        relative_path="workspace/traittutor/generation-tasks/*.json",
        store_module="traittutor.generate.service",
        owner=OwnerSpec(OwnerStrategy.DIRECT_OWNER_ID, field="owner_id"),
        sections=(SectionSpec(FILE_SECTION, "generation_tasks", "generation_id"),),
    ),
    SourceSpec(
        name="generation_results",
        kind="json_per_task_file",
        relative_path="workspace/traittutor/generations/*.json",
        store_module="traittutor.generate.service",
        # Results carry generation_id but no owner; join to the task file.
        owner=OwnerSpec(
            strategy=OwnerStrategy.JOIN_GENERATION_TASK,
            field="generation_id",
            join_source="generation_tasks",
            join_lookup_key="generation_id",
            join_owner_field="owner_id",
        ),
        sections=(SectionSpec(FILE_SECTION, "generation_results", "generation_id"),),
        # A generation-result file whose ``result`` is empty/null/``{"items": []}``
        # never produced anything — it is residue (a Phase 5 deletion candidate),
        # not business data to migrate or an owner to rebuild.
        residue_if_empty_fields=("result",),
    ),
    # ── SQLite stores ────────────────────────────────────────────────────────
    SourceSpec(
        name="chat_history",
        kind="sqlite",
        relative_path="chat_history.db",
        store_module="traittutor.services.session.sqlite_store",
        owner=OwnerSpec(OwnerStrategy.SQLITE_COLUMN, field="owner_id"),
        sqlite_owner_table="sessions",
        sqlite_owner_column="owner_id",
        sqlite_tables=(
            "sessions",
            "messages",
            "turns",
            "turn_events",
            "notebook_items",
            "notebook_sections",
            "server_quiz_items",
        ),
    ),
    SourceSpec(
        name="knowledge_graph",
        kind="sqlite",
        relative_path="workspace/learner/knowledge-graph.sqlite3",
        store_module="traittutor.personalization.graph_repository",
        # The graph store has no owner column at all — its owner is the workspace
        # it lives in (path scope), resolved server-side.  This is a Phase-3
        # decision: the file's location inside a per-user workspace is the owner
        # evidence, not a guess from row data.  Path-scope attribution is kept
        # strictly separate from evidence strength: graph evidence is structural
        # and is NEVER treated as BKT strong evidence (PRD §5.2, iron law #2);
        # no KC attribution is backfilled from the graph onto learner events.
        owner=OwnerSpec(OwnerStrategy.PATH_SCOPE),
        sqlite_tables=(
            "graph_subjects",
            "graph_versions",
            "graph_modules",
            "graph_concepts",
            "graph_edges",
            "graph_evidence",
        ),
    ),
)

_SOURCE_BY_NAME = {spec.name: spec for spec in SOURCE_SPECS}


def get_source(name: str) -> SourceSpec:
    """Look up a source spec by name.  Raises ``KeyError`` if unknown."""
    try:
        return _SOURCE_BY_NAME[name]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"unknown source: {name!r}") from exc


def plan_listed_source_names() -> tuple[str, ...]:
    """Names of sources included in the declared migration baseline."""
    return tuple(spec.name for spec in SOURCE_SPECS if spec.listed_in_plan)


class OwnerResolver:
    """Resolves owners for join-based records via the generation-task map.

    The map is built once from two unioned sources during the inventory's first
    pass:

    * the authoritative ``generation-tasks.sqlite`` at the system root
      (``GenerationTaskManager._TaskStore``; ``generation_id`` PRIMARY KEY,
      ``owner_id NOT NULL``), and
    * the legacy per-workspace ``generation-tasks/*.json`` files.

    The SQLite store wins on conflict — it is the live UPSERT target.  Every
    join-based source (page-schemas, orchestrator-runs, generation-results) then
    asks this resolver for an owner given a join key.  A ``None`` return means
    the run genuinely has no owner record anywhere; the caller counts it as
    ``attribution_pending`` (or residue, if the payload is empty).
    """

    def __init__(self, generation_task_owners: dict[str, str] | None = None) -> None:
        self._map: dict[str, str] = dict(generation_task_owners or {})

    @classmethod
    def from_task_files(cls, task_files: list[Path]) -> "OwnerResolver":
        """Build the join map by reading each generation-task JSON file.

        Files that fail to parse are skipped silently here; the inventory
        reports those as anomalies when it inventories the same directory.
        """
        mapping: dict[str, str] = {}
        for path in task_files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            generation_id = payload.get("generation_id")
            owner_id = payload.get("owner_id")
            if isinstance(generation_id, str) and isinstance(owner_id, str):
                mapping[generation_id] = owner_id
        return cls(mapping)

    @classmethod
    def from_task_sqlite(cls, db_path: Path) -> "OwnerResolver":
        """Build the join map from the authoritative ``generation-tasks.sqlite``.

        Opened read-only via a ``mode=ro`` URI so the live store is never
        touched.  A missing or unreadable database yields an empty map (the
        inventory reports the missing file separately when relevant).
        """
        if not db_path.is_file():
            return cls()

        mapping: dict[str, str] = {}
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = con.execute(
                    "SELECT generation_id, owner_id FROM generation_tasks "
                    "WHERE generation_id IS NOT NULL AND owner_id IS NOT NULL"
                ).fetchall()
            finally:
                con.close()
        except sqlite3.DatabaseError:
            return cls()
        for generation_id, owner_id in rows:
            if isinstance(generation_id, str) and isinstance(owner_id, str):
                mapping[generation_id] = owner_id
        return cls(mapping)

    def merge(self, other: "OwnerResolver") -> "OwnerResolver":
        """Return a resolver whose map is ``other`` overlaid on ``self``.

        ``other`` wins on key conflict — used to let the authoritative SQLite
        task store override stale legacy JSON task files.
        """
        combined = dict(self._map)
        combined.update(other._map)
        return OwnerResolver(combined)

    def resolve_join(self, join_value: str | None) -> str | None:
        """Return the owner id for a generation join key, or ``None``."""
        if not join_value:
            return None
        return self._map.get(join_value)

    @property
    def known_keys(self) -> set[str]:
        return set(self._map)
