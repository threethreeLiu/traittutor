from pathlib import Path

from traittutor.learning.storage import LearningStore
from traittutor.services.path_service import PathService


def test_scoped_learning_store_reuses_canonical_workspace_database(tmp_path: Path) -> None:
    path_service = PathService(workspace_root=tmp_path / "owner-root")

    store = LearningStore(
        path_service.get_workspace_dir() / "learning",
        path_service=path_service,
        owner_id="owner-a",
    )

    assert store.root == path_service.get_workspace_dir() / "learning"
    assert store._adapter._store().db_path == path_service.get_traittutor_database_path()
    assert not (store.root / "traittutor.sqlite3").exists()


def test_explicit_legacy_root_remains_isolated_for_tests(tmp_path: Path) -> None:
    store = LearningStore(tmp_path / "learning")

    assert store._adapter._store().db_path == tmp_path / "learning" / "traittutor.sqlite3"


def test_workspace_initialization_creates_only_shared_roots(tmp_path: Path) -> None:
    path_service = PathService(workspace_root=tmp_path / "data")

    path_service.ensure_all_directories()

    assert path_service.get_settings_dir().is_dir()
    assert path_service.get_workspace_dir().is_dir()
    assert sorted(
        path.relative_to(path_service.user_data_dir)
        for path in path_service.user_data_dir.rglob("*")
    ) == [Path("settings"), Path("workspace")]
