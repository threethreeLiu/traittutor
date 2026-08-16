from __future__ import annotations

from traittutor.unified_storage import SQLiteDocumentStore


def test_document_store_round_trip_is_owner_and_namespace_scoped(tmp_path) -> None:
    db_path = tmp_path / "traittutor.sqlite3"
    first = SQLiteDocumentStore("owner-a", namespace="settings", db_path=db_path)
    other_owner = SQLiteDocumentStore("owner-b", namespace="settings", db_path=db_path)
    other_namespace = SQLiteDocumentStore("owner-a", namespace="tour", db_path=db_path)

    first.save("system", {"version": 1, "port": 8001})

    assert first.load("system") == {"version": 1, "port": 8001}
    assert other_owner.load("system", {}) == {}
    assert other_namespace.load("system", {}) == {}


def test_document_store_delete_reports_presence(tmp_path) -> None:
    store = SQLiteDocumentStore(
        "owner-a", namespace="credentials", db_path=tmp_path / "traittutor.sqlite3"
    )
    store.save("token", {"value": "secret"})

    assert store.delete("token") is True
    assert store.delete("token") is False
    assert store.load("token") is None
