"""Read-only dry-run migration planner (Phase 0 item 4).

Consumes a :class:`~traittutor.unified_storage.models.BaselineManifest` and
projects every source record onto its target table, splitting rows into
attributed vs. attribution-pending, flagging conflicts and unmappable sections,
and marking structurally-unattributable records as ``needs_rebuild``.

It writes nothing: it never opens ``traittutor.sqlite3`` and never inserts into
``storage_migration_runs``.  Its sole output is an in-memory
:class:`~traittutor.unified_storage.models.DryRunReport`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .mapping import SOURCE_SPECS, SourceSpec
from .models import (
    AttributionSummary,
    BaselineManifest,
    ConflictRecord,
    DryRunReport,
    OwnerFragmentationGroup,
    OwnerStrategy,
    TargetProjection,
    UnmappableRecord,
)

# Owner strategies whose attribution-pending records cannot be made attributable
# without manual evidence work (broken join or no owner column at all).  Per plan
# §5 Phase 3 item 5 these are flagged ``needs_rebuild`` rather than guessed.
_NEEDS_REBUILD_STRATEGIES = frozenset(
    {OwnerStrategy.JOIN_GENERATION_TASK, OwnerStrategy.UNRESOLVED}
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _strategy_for(source_name: str, specs_by_name: dict[str, SourceSpec]) -> OwnerStrategy:
    spec = specs_by_name.get(source_name)
    return spec.owner.strategy if spec is not None else OwnerStrategy.UNRESOLVED


def _build_projections_and_unmappable(
    manifest: BaselineManifest,
    specs_by_name: dict[str, SourceSpec],
) -> tuple[list[TargetProjection], list[UnmappableRecord]]:
    projections: list[TargetProjection] = []
    unmappable: list[UnmappableRecord] = []

    for source in manifest.json_sources:
        strategy = _strategy_for(source.source_name, specs_by_name)
        needs_rebuild = strategy in _NEEDS_REBUILD_STRATEGIES
        for section in source.sections:
            if section.target_table is None:
                unmappable.append(
                    UnmappableRecord(
                        source_ref=f"{source.relative_path}::{section.section_name}",
                        reason="section has no target-table mapping in the registry",
                    )
                )
                continue
            pending = section.pending_record_count
            projections.append(
                TargetProjection(
                    target_table=section.target_table,
                    source_name=source.source_name,
                    projected_row_count=section.record_count,
                    attributed_record_count=section.attributed_record_count,
                    attribution_pending_count=pending,
                    needs_rebuild_count=pending if needs_rebuild else 0,
                    conflict_count=section.duplicate_id_count,
                )
            )

    for collection in manifest.per_task_collections:
        strategy = _strategy_for(collection.source_name, specs_by_name)
        needs_rebuild = strategy in _NEEDS_REBUILD_STRATEGIES
        spec = specs_by_name.get(collection.source_name)
        target_table = spec.sections[0].target_table if spec and spec.sections else None
        if target_table is None:
            unmappable.append(
                UnmappableRecord(
                    source_ref=collection.relative_dir,
                    reason="per-task collection has no target-table mapping",
                )
            )
            continue
        pending = collection.owner_resolution.unresolved_record_count
        residue = collection.residue_record_count
        real_rows = collection.file_count - residue
        projections.append(
            TargetProjection(
                target_table=target_table,
                source_name=collection.source_name,
                projected_row_count=real_rows,
                attributed_record_count=collection.owner_resolution.resolved_record_count,
                attribution_pending_count=pending,
                needs_rebuild_count=pending if needs_rebuild else 0,
                conflict_count=collection.duplicate_id_count,
                residue_count=residue,
            )
        )

    for sqlite_source in manifest.sqlite_sources:
        strategy = sqlite_source.owner_resolution.strategy
        owners_resolved = bool(sqlite_source.owner_resolution.resolved_owner_ids)
        needs_rebuild = strategy in _NEEDS_REBUILD_STRATEGIES
        for table in sqlite_source.tables:
            target_table = f"sqlite_{table.table_name}"
            # Legacy SQLite tables are projected verbatim; Phase 2/3 decide their
            # true target.  Attribution is a source-level statement here because
            # only one table carries the owner column.
            attributed = table.row_count if owners_resolved else 0
            pending = table.row_count - attributed
            projections.append(
                TargetProjection(
                    target_table=target_table,
                    source_name=sqlite_source.source_name,
                    projected_row_count=table.row_count,
                    attributed_record_count=attributed,
                    attribution_pending_count=pending,
                    needs_rebuild_count=pending if needs_rebuild else 0,
                    conflict_count=0,
                )
            )

    return projections, unmappable


def _build_conflicts(manifest: BaselineManifest) -> list[ConflictRecord]:
    conflicts: list[ConflictRecord] = []
    for anomaly in manifest.anomalies:
        if anomaly.code == "duplicate_id_within_source":
            conflicts.append(
                ConflictRecord(
                    conflict_type="duplicate_id_within_source",
                    source_ref=anomaly.source_ref,
                    record_ids=(),
                    message=anomaly.message,
                )
            )
    return conflicts


def _build_fragmentation_groups(
    manifest: BaselineManifest,
) -> list[OwnerFragmentationGroup]:
    """Group sources whose direct/user-id owner ids may represent one user.

    The dry-run never merges owners; it only reports which sources contribute
    more than one id so a human chooses the canonical owner before migration.
    """
    groups: list[OwnerFragmentationGroup] = []
    for source in manifest.json_sources:
        ids = source.owner_resolution.resolved_owner_ids
        if len(ids) > 1 and source.owner_resolution.strategy in (
            OwnerStrategy.DIRECT_OWNER_ID,
            OwnerStrategy.USER_ID_FIELD,
        ):
            groups.append(OwnerFragmentationGroup(owner_ids=ids, sources=(source.source_name,)))
    # Cross-source fragmentation: if the workspace owner set spans more than one
    # id, surface it as one group so the global decision is visible.
    distinct = manifest.summary.distinct_owner_ids
    contributing = [
        s.source_name for s in manifest.json_sources if s.owner_resolution.resolved_owner_ids
    ]
    if len(distinct) > 1 and contributing:
        groups.append(OwnerFragmentationGroup(owner_ids=distinct, sources=tuple(contributing)))
    return groups


def _format_human_summary(
    manifest: BaselineManifest,
    projections: list[TargetProjection],
    conflicts: list[ConflictRecord],
    unmappable: list[UnmappableRecord],
    attribution: AttributionSummary,
) -> str:
    lines: list[str] = []
    lines.append("=== Unified storage dry-run migration plan ===")
    lines.append(f"manifest: {manifest.data_root}")
    lines.append(
        f"sources={manifest.summary.total_sources} "
        f"records={attribution.total_records} "
        f"attributed={attribution.fully_attributed} "
        f"attribution_pending={attribution.attribution_pending} "
        f"residue={sum(p.residue_count for p in projections)}"
    )
    lines.append(f"distinct owner ids: {', '.join(attribution.distinct_owner_ids) or '(none)'}")
    lines.append("")
    lines.append("target projections:")
    for proj in sorted(projections, key=lambda p: p.target_table):
        lines.append(
            f"  {proj.target_table:<34} rows={proj.projected_row_count:<5} "
            f"attributed={proj.attributed_record_count:<5} "
            f"pending={proj.attribution_pending_count:<5} "
            f"needs_rebuild={proj.needs_rebuild_count:<4} "
            f"conflicts={proj.conflict_count} "
            f"residue={proj.residue_count}"
        )
    if conflicts:
        lines.append("")
        lines.append(f"conflicts ({len(conflicts)}):")
        for conflict in conflicts:
            lines.append(f"  [{conflict.conflict_type}] {conflict.source_ref}: {conflict.message}")
    if unmappable:
        lines.append("")
        lines.append(f"unmappable sections ({len(unmappable)}):")
        for record in unmappable:
            lines.append(f"  {record.source_ref}: {record.reason}")
    if attribution.owner_fragmentation_groups:
        lines.append("")
        lines.append("owner fragmentation (human must choose canonical owner):")
        for group in attribution.owner_fragmentation_groups:
            lines.append(f"  ids={list(group.owner_ids)} sources={list(group.sources)}")
    return "\n".join(lines)


def plan_migration(manifest: BaselineManifest) -> DryRunReport:
    """Project ``manifest`` onto target tables and return a frozen dry-run report."""
    specs_by_name = {spec.name: spec for spec in SOURCE_SPECS}

    projections, unmappable = _build_projections_and_unmappable(manifest, specs_by_name)
    conflicts = _build_conflicts(manifest)
    fragmentation = _build_fragmentation_groups(manifest)

    total_records = sum(p.projected_row_count for p in projections)
    fully_attributed = sum(p.attributed_record_count for p in projections)
    attribution_pending = sum(p.attribution_pending_count for p in projections)

    attribution = AttributionSummary(
        total_records=total_records,
        fully_attributed=fully_attributed,
        attribution_pending=attribution_pending,
        distinct_owner_ids=manifest.summary.distinct_owner_ids,
        owner_fragmentation_groups=tuple(fragmentation),
    )

    human_summary = _format_human_summary(manifest, projections, conflicts, unmappable, attribution)

    return DryRunReport(
        generated_at=_utc_now(),
        manifest_path=manifest.data_root,
        target_projections=tuple(projections),
        conflicts=tuple(conflicts),
        unmappable_records=tuple(unmappable),
        attribution_summary=attribution,
        human_summary=human_summary,
    )
