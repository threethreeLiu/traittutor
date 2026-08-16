from __future__ import annotations

import sqlite3

from traittutor.generate.tasks import _TaskStore
from traittutor.personalization.graph_repository import LearningKnowledgeGraphRepository
from traittutor.services.config.runtime_settings import RuntimeSettingsService
from traittutor.services.session.sqlite_store import SQLiteSessionStore


def test_runtime_data_planes_share_one_sqlite_database(tmp_path) -> None:
    settings_dir = tmp_path / "user" / "settings"
    settings_dir.mkdir(parents=True)
    canonical_db = tmp_path / "user" / "workspace" / "traittutor" / "traittutor.sqlite3"

    settings = RuntimeSettingsService(settings_dir, process_env={})
    settings.load_system(include_process_overrides=False)
    SQLiteSessionStore(canonical_db)
    _TaskStore(canonical_db)
    LearningKnowledgeGraphRepository(canonical_db).list_subject_ids()

    with sqlite3.connect(canonical_db) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert {
        "runtime_documents",
        "sessions",
        "generation_task_queue",
        "learning_graph_subjects",
    }.issubset(tables)
