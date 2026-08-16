"""Idempotent import of legacy JSON layouts that were split across directories.

Runtime stores never call this module.  It exists only for the offline storage
migration command, where reading legacy files is required to preserve existing
user data before those files are archived or removed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from traittutor.services.path_service import PathService, get_path_service

from .section_store import SectionedRecordStore


def legacy_aggregate_source_paths(path_service: PathService) -> tuple[Path, ...]:
    """Return every legacy business-state file consumed by this migrator."""
    workspace = path_service.get_workspace_dir()
    learner_root = path_service.get_memory_dir() / "learner"
    knowledge_root = path_service.get_knowledge_bases_root()
    paths = [
        path_service.get_settings_file("interface"),
        learner_root / "global.json",
        learner_root / "sessions.json",
        learner_root / "jobs.json",
        workspace / "skills" / ".tags.json",
        workspace / "skills" / ".hub-lock.json",
        knowledge_root / "kb_config.json",
        path_service.workspace_root / "system" / "auth" / "users.json",
        *sorted((path_service.workspace_root / "system" / "grants").glob("*.json")),
        *sorted((workspace / "traittutor" / "profiles").glob("*.json")),
        *sorted((workspace / "notebook").glob("*.json")),
        *sorted((learner_root / "subjects").glob("*.json")),
        *sorted((learner_root / "signals").glob("*.jsonl")),
        *sorted((workspace / "learning").glob("*.json")),
        *sorted((workspace / "traittutor" / "image-material-sources").glob("*.json")),
        *sorted((workspace / "traittutor" / "material-analyses").glob("*/*.json")),
        *sorted(workspace.glob("chat/chat/*/loaded_tools.json")),
        # Historical turn mirrors are no longer a runtime source. The canonical
        # session database owns replay; include mirrors in the verified archive
        # set so they can be retired without data loss.
        *sorted(workspace.glob("chat/*/*/events.jsonl")),
    ]
    if knowledge_root.exists():
        for directory in sorted(path for path in knowledge_root.iterdir() if path.is_dir()):
            paths.extend((directory / "metadata.json", directory / ".progress.json"))
    return tuple(dict.fromkeys(path for path in paths if path.is_file()))


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _objects(paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [value for path in paths if (value := _object(path))]


def _jsonl(paths: Iterable[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def _merge(
    adapter: SectionedRecordStore,
    section: str,
    records: Iterable[dict[str, Any]],
    *,
    key: str,
) -> int:
    inserted = 0
    with adapter.locked() as payload:
        existing = payload[section]
        known = {str(item.get(key)) for item in existing if item.get(key) is not None}
        for record in records:
            value = record.get(key)
            if value is None or str(value) in known:
                continue
            existing.append(record)
            known.add(str(value))
            inserted += 1
        if inserted:
            adapter.replace_all(payload)
    return inserted


def _replace_if_empty(
    adapter: SectionedRecordStore, section: str, records: list[dict[str, Any]]
) -> int:
    if not records:
        return 0
    with adapter.locked() as payload:
        if payload[section]:
            return 0
        payload[section] = records
        adapter.replace_all(payload)
        return len(records)


def migrate_legacy_aggregates(
    *, path_service: PathService | None = None, owner_id: str = "local-admin"
) -> dict[str, int]:
    """Import all discoverable split-file business records into unified SQLite."""
    ps = path_service or get_path_service()
    user = ps.get_user_root()
    workspace = ps.get_workspace_dir()
    memory = ps.get_memory_dir()
    counts: dict[str, int] = {}

    def adapter(name: str) -> SectionedRecordStore:
        return SectionedRecordStore(name, owner_id, schema_version=1, path_service=ps)

    system_root = ps.workspace_root / "system"
    accounts = _object(system_root / "auth" / "users.json")
    account_records = [
        (
            {"username": username, "hash": value}
            if isinstance(value, str)
            else {"username": username, **value}
        )
        for username, value in accounts.items()
        if isinstance(value, (str, dict))
    ]
    counts["user_accounts"] = _replace_if_empty(
        SectionedRecordStore(
            "user_accounts",
            "system",
            schema_version=1,
            db_path=system_root / "traittutor.sqlite3",
        ),
        "users",
        account_records,
    )
    grant_records = []
    for path in sorted((system_root / "grants").glob("*.json")):
        value = _object(path)
        if value:
            value.setdefault("user_id", path.stem)
            grant_records.append(value)
    counts["user_grants"] = _replace_if_empty(
        SectionedRecordStore(
            "user_grants",
            "system",
            schema_version=1,
            db_path=system_root / "traittutor.sqlite3",
        ),
        "grants",
        grant_records,
    )

    interface = _object(ps.get_settings_file("interface"))
    counts["interface_settings"] = _replace_if_empty(
        adapter("interface_settings"),
        "settings",
        (
            [{"settings_id": "interface", "owner_id": owner_id, "value": interface}]
            if interface
            else []
        ),
    )

    profiles = []
    for value in _objects(sorted((workspace / "traittutor" / "profiles").glob("*.json"))):
        profiles.append({**value, "owner_id": value.get("user_id") or owner_id})
    counts["trait_profiles"] = _merge(
        adapter("trait_profiles"), "profiles", profiles, key="profile_id"
    )

    notebook_dir = workspace / "notebook"
    notebooks = _objects(
        path for path in sorted(notebook_dir.glob("*.json")) if path.name != "notebooks_index.json"
    )
    counts["notebooks"] = _merge(adapter("notebooks"), "notebooks", notebooks, key="id")

    learner_root = memory / "learner"
    personalization = adapter("personalization_state")
    profile_records: list[dict[str, Any]] = []
    global_profile = _object(learner_root / "global.json")
    if global_profile:
        profile_records.append(
            {"storage_key": "global", "owner_id": owner_id, "profile": global_profile}
        )
    for path in sorted((learner_root / "subjects").glob("*.json")):
        value = _object(path)
        if value:
            profile_records.append(
                {"storage_key": f"subject:{path.stem}", "owner_id": owner_id, "profile": value}
            )
    counts["personalization_profiles"] = _merge(
        personalization, "profiles", profile_records, key="storage_key"
    )
    signals = _jsonl(sorted((learner_root / "signals").glob("*.jsonl")))
    for signal in signals:
        signal.setdefault("owner_id", owner_id)
    counts["personalization_signals"] = _merge(personalization, "signals", signals, key="signal_id")
    sessions = _object(learner_root / "sessions.json")
    session_records = [
        {"session_id": session_id, "owner_id": owner_id, **state}
        for session_id, state in sessions.items()
        if isinstance(state, dict)
    ]
    counts["personalization_sessions"] = _merge(
        personalization, "sessions", session_records, key="session_id"
    )
    jobs = _object(learner_root / "jobs.json")
    counts["personalization_jobs"] = _replace_if_empty(
        personalization,
        "jobs",
        ([{"job_id": "memory-reconcile", "owner_id": owner_id, **jobs}] if jobs else []),
    )

    learning_records = _objects(sorted((workspace / "learning").glob("*.json")))
    counts["learning_progress"] = _merge(
        adapter("learning_progress"), "progress", learning_records, key="book_id"
    )

    image_records = _objects(
        sorted((workspace / "traittutor" / "image-material-sources").glob("*.json"))
    )
    counts["image_material_sources"] = _merge(
        adapter("image_material_sources"), "sources", image_records, key="source_id"
    )
    analyses = _objects(sorted((workspace / "traittutor" / "material-analyses").glob("*/*.json")))
    counts["material_analyses"] = _merge(
        adapter("material_analyses"), "analyses", analyses, key="analysis_id"
    )

    skill_state = adapter("skill_state")
    tags = _object(workspace / "skills" / ".tags.json").get("tags")
    counts["skill_settings"] = _replace_if_empty(
        skill_state,
        "settings",
        (
            [{"settings_id": "skill-tags", "owner_id": owner_id, "tags": tags}]
            if isinstance(tags, list)
            else []
        ),
    )
    origins = _object(workspace / "skills" / ".hub-lock.json")
    origin_records = [
        {"name": name, "owner_id": owner_id, "origin": value}
        for name, value in origins.items()
        if isinstance(value, dict)
    ]
    counts["skill_origins"] = _merge(skill_state, "origins", origin_records, key="name")

    knowledge_root = ps.get_knowledge_bases_root()
    knowledge = adapter("knowledge_state")
    config = _object(knowledge_root / "kb_config.json")
    counts["knowledge_config"] = _replace_if_empty(
        knowledge,
        "config",
        ([{"config_id": "knowledge", "owner_id": owner_id, "value": config}] if config else []),
    )
    metadata_records = []
    progress_records = []
    if knowledge_root.exists():
        for directory in sorted(knowledge_root.iterdir()):
            if not directory.is_dir():
                continue
            metadata = _object(directory / "metadata.json")
            if metadata:
                metadata_records.append(
                    {"kb_name": directory.name, "owner_id": owner_id, "value": metadata}
                )
            progress = _object(directory / ".progress.json")
            if progress:
                progress_records.append(
                    {"kb_name": directory.name, "owner_id": owner_id, "value": progress}
                )
    counts["knowledge_metadata"] = _merge(knowledge, "metadata", metadata_records, key="kb_name")
    counts["knowledge_progress"] = _merge(knowledge, "progress", progress_records, key="kb_name")

    mcp_records = []
    for path in sorted(workspace.glob("chat/chat/*/loaded_tools.json")):
        value = _object(path)
        if value:
            mcp_records.append(
                {
                    "session_id": path.parent.name,
                    "owner_id": owner_id,
                    "loaded_tools": value.get("loaded_tools", []),
                }
            )
    counts["mcp_session_state"] = _merge(
        adapter("mcp_session_state"), "sessions", mcp_records, key="session_id"
    )

    # Keep this reference intentional: it documents that settings and workspace
    # roots were resolved from the same authenticated PathService.
    del user
    return counts


__all__ = ["legacy_aggregate_source_paths", "migrate_legacy_aggregates"]
