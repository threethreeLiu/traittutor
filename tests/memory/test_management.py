from __future__ import annotations

import json

import pytest

from traittutor.memory.api_models import (
    CandidateActivationRequest,
    CreateMemoryGrantRequest,
    MemoryMutationRequest,
    MemorySearchRequest,
)
from traittutor.memory.index_store import MemoryIndexStore, StaleMemoryIndexGenerationError
from traittutor.memory.management import MemoryManagementService
from traittutor.memory.store import MemoryAuthorizationError, MemoryStore


def _service(tmp_path, owner_id: str) -> MemoryManagementService:
    return MemoryManagementService(
        owner_id,
        store=MemoryStore(owner_id, path=tmp_path / "memory.json"),
        index_store=MemoryIndexStore(owner_id, path=tmp_path / "index.json"),
    )


def test_management_is_owner_bound_and_activation_is_idempotent(tmp_path) -> None:
    alice = _service(tmp_path, "alice")
    bob = _service(tmp_path, "bob")
    candidate = alice.store.propose_candidate(
        scope="subject",
        subject_id="math",
        key="preferred_example",
        value="geometry",
        provenance="inferred",
        confidence=0.8,
    )
    request = CandidateActivationRequest(operation_id="op-activate", confirmed=True)

    first = alice.activate_candidate(candidate.candidate_id, request)
    replay = alice.activate_candidate(candidate.candidate_id, request)

    assert replay.memory_id == first.memory_id
    assert len(alice.store.history("subject", "preferred_example", subject_id="math")) == 1
    assert bob.snapshot().items == ()
    with pytest.raises(KeyError):
        bob.activate_candidate(candidate.candidate_id, request)


def test_grant_is_server_owned_exact_and_revocable(tmp_path) -> None:
    service = _service(tmp_path, "alice")
    memory = service.store.add_explicit(scope="global", key="language", value="zh")
    grant_request = CreateMemoryGrantRequest(
        operation_id="grant-op",
        requesting_scope="subject",
        requesting_subject_id="math",
        target_scope="global",
        purpose="assemble_prompt",
    )
    grant = service.create_grant(grant_request)
    assert service.create_grant(grant_request).grant_id == grant.grant_id
    search = MemorySearchRequest(
        scope="global",
        requesting_scope="subject",
        requesting_subject_id="math",
        grant_id=grant.grant_id,
        snapshot_id="snapshot-1",
        purpose="assemble_prompt",
    )
    assert service.search(search) == [memory]
    assert service.list_access_records("snapshot-1")[0].key == memory.memory_id

    service.revoke_grant(grant.grant_id)
    with pytest.raises(MemoryAuthorizationError):
        service.search(search)


def test_delete_removes_recall_before_index_invalidation(tmp_path) -> None:
    service = _service(tmp_path, "alice")
    item = service.store.add_explicit(
        scope="subject", subject_id="math", key="note", value="forget me"
    )

    request = MemoryMutationRequest(operation_id="delete-1")
    result = service.delete(item.memory_id, request)
    replay = service.delete(item.memory_id, request)

    assert result.item.status == "deleted"
    assert service.store.search(keyword="forget me") == []
    assert result.invalidated_index_generation > 0
    assert replay.invalidated_index_generation == result.invalidated_index_generation


def test_management_rebuild_cannot_resurrect_deleted_memory(tmp_path) -> None:
    service = _service(tmp_path, "alice")
    item = service.store.add_explicit(scope="global", key="note", value="private")
    token = service.begin_index_rebuild()
    index = service.build_memory_index(token, entry_id="profile")

    service.delete(item.memory_id, MemoryMutationRequest(operation_id="delete-before-commit"))

    with pytest.raises(StaleMemoryIndexGenerationError):
        service.commit_index_rebuild(token, (index,))
    assert service.index_store.list_indexes() == []


def test_legacy_memory_json_remains_readable(tmp_path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidates": [],
                "items": [],
                "lifecycle": [],
                "access_records": [],
            }
        ),
        encoding="utf-8",
    )
    service = MemoryManagementService(
        "alice",
        store=MemoryStore("alice", path=path),
        index_store=MemoryIndexStore("alice", path=tmp_path / "index.json"),
    )

    assert service.snapshot().items == ()
    assert (
        service.create_grant(
            CreateMemoryGrantRequest(
                operation_id="old-json-grant",
                requesting_scope="subject",
                requesting_subject_id="math",
                target_scope="global",
                purpose="test",
            )
        ).owner_id
        == "alice"
    )


def test_legacy_layer_and_domain_fields_are_persistently_migrated(tmp_path) -> None:
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidates": [],
                "items": [
                    {
                        "memory_id": "mem_old",
                        "owner_id": "alice",
                        "layer": "L3",
                        "scope": "subject",
                        "domain_id": "math",
                        "kc_id": None,
                        "key": "goal",
                        "value": "learn algebra",
                        "provenance": "explicit",
                        "status": "active",
                        "confidence": 1.0,
                        "sensitivity": "personal",
                        "valid_from": "2026-08-09T08:00:00+00:00",
                        "valid_until": None,
                        "supersedes_id": None,
                        "evidence_refs": [],
                        "source_ref": None,
                        "created_at": "2026-08-09T08:00:00+00:00",
                        "updated_at": "2026-08-09T08:00:00+00:00",
                    }
                ],
                "lifecycle": [],
                "access_records": [],
            }
        ),
        encoding="utf-8",
    )
