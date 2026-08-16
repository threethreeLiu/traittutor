"""Regression: audited reads append instead of rewriting the whole store.

The old ``search(snapshot_id=...)`` path took the exclusive lock, loaded
every section, appended one envelope per result item, and rewrote the
entire payload — quadratic cumulative IO as the access audit grew, plus
unbounded growth. The new path is a pure section append with a bounded
tail.
"""

from __future__ import annotations

from pathlib import Path

from traittutor.memory.store import MAX_ACCESS_RECORDS, MemoryStore


def _seed(store: MemoryStore, count: int = 3) -> None:
    for index in range(count):
        store.add_explicit(
            scope="global",
            key=f"preference:style-{index}",
            value=f"prefers style {index}",
            source="test_seeding",
        )


def test_audited_search_appends_without_rewriting_other_sections(
    tmp_path: Path,
) -> None:
    store = MemoryStore("user-a", path=tmp_path / "memory.json")
    _seed(store)
    items_before = len(store._adapter.snapshot()["items"])

    store.search(purpose="chat_memory", scope="global", snapshot_id="snap-a")
    store.search(purpose="chat_memory", scope="global", snapshot_id="snap-b")

    payload = store._adapter.snapshot()
    # Other sections untouched by the audit append.
    assert len(payload["items"]) == items_before
    # Both audited searches left envelopes, in append order.
    snapshots = [env["record"]["snapshot_id"] for env in payload["access_records"]]
    assert snapshots.count("snap-a") >= 1
    assert snapshots.count("snap-b") >= 1
    assert snapshots[: snapshots.index("snap-b")].count("snap-a") >= 1


def test_append_records_trims_oldest_beyond_cap(tmp_path: Path) -> None:
    store = MemoryStore("user-a", path=tmp_path / "memory.json")
    store.add_explicit(scope="global", key="preference:style", value="v", source="test_seeding")
    found = store.search(purpose="chat_memory", scope="global", snapshot_id="snap")
    assert found

    # Append a burst with a small cap: only the newest rows survive.
    from uuid import uuid4

    from traittutor.context_assembler.access import MemoryAccessRecord
    from traittutor.memory.store import _now

    envelopes = [
        {
            "owner_id": "user-a",
            "record": MemoryAccessRecord(
                record_id=f"mar_{uuid4().hex[:16]}",
                snapshot_id=f"burst-{index}",
                created_at=_now(),
                scope="global:*:*:*",
                key=f"burst-{index}",
                version_read=f"burst-{index}",
                purpose="test",
                user_authorized=True,
            ).model_dump(mode="json"),
        }
        for index in range(8)
    ]
    store._adapter.append_records("access_records", envelopes, keep_newest=5)

    remaining = [
        env["record"]["snapshot_id"] for env in store._adapter.snapshot()["access_records"]
    ]
    # The pre-burst records were pushed out; exactly the newest 5 bursts remain.
    assert len(remaining) == 5
    assert remaining == [f"burst-{index}" for index in range(3, 8)]


def test_max_access_records_bound_is_sane() -> None:
    # The production cap must stay bounded (it is a growth guard, not a
    # wishlist); keep the assertion loose so tuning stays possible.
    assert 1_000 <= MAX_ACCESS_RECORDS <= 100_000
