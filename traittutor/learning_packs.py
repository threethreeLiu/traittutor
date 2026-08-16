"""Small, user-workspace learning-pack store for the consumer study tools."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from traittutor.multi_user.models import LOCAL_ADMIN_ID
from traittutor.services.path_service import get_path_service
from traittutor.unified_storage import SectionedRecordStore
from traittutor.unified_storage.mapping import LIST_SECTION

MAX_REVIEW_ATTEMPTS = 2048
PACK_MATERIAL_SOURCE_TYPES = frozenset({"knowledge", "notebook", "upload", "paste"})


class LearningPackStoreError(RuntimeError):
    """The durable learning-pack store cannot safely serve a request."""


class InvalidComponentTransition(ValueError):
    """A component event violates the active plan's state machine."""


class InvalidComponentPlanChain(ValueError):
    """A plan does not directly supersede the current active version."""


class InvalidPreAssessmentTransition(ValueError):
    """A pre-assessment request conflicts with the Pack's durable lifecycle."""


class InvalidLearningPathBinding(ValueError):
    """A Pack cannot safely bind to the requested learning path."""


class InvalidPackMaterialOperation(ValueError):
    """A requested Pack material mutation is not a valid revision operation."""


class MaterialRevisionConflict(RuntimeError):
    """The caller attempted to mutate a stale Pack material revision."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"Pack material revision changed from {expected_revision} to {actual_revision}"
        )


class MaterialIdempotencyConflict(RuntimeError):
    """One material idempotency key was reused for different client intent."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _request_fingerprint(payload: dict[str, Any]) -> str:
    """Fingerprint the immutable client intent behind an idempotent event."""
    canonical = {
        key: value
        for key, value in payload.items()
        if key not in {"occurred_at", "_idempotent_replay", "_request_fingerprint"}
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _material_content_hash(material: dict[str, Any]) -> str:
    """Hash source content independently from derived learner analysis.

    The hash is server-derived and gives later analysis code a safe reuse key.
    Browser-supplied hashes and mutable analysis output are deliberately
    ignored, so a client cannot claim that unrelated material was analyzed.
    """
    metadata = dict(material.get("metadata") or {})
    metadata.pop("learner_analysis", None)
    payload = {
        "source_type": str(material.get("source_type") or "paste").strip().lower(),
        "source_id": str(material.get("source_id") or "").strip(),
        "text": str(material.get("text") or ""),
        "metadata": metadata,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalize_material(material: dict[str, Any]) -> dict[str, Any]:
    """Return one deterministic material record without trusting client IDs."""
    normalized = deepcopy(material)
    normalized["source_type"] = str(normalized.get("source_type") or "paste").strip().lower()
    normalized["title"] = str(normalized.get("title") or "Learning source").strip()
    metadata = normalized.get("metadata")
    normalized["metadata"] = dict(metadata) if isinstance(metadata, dict) else {}
    content_hash = _material_content_hash(normalized)
    explicit_id = str(normalized.get("material_id") or "").strip()
    normalized["material_id"] = explicit_id or f"material-{content_hash[:24]}"
    normalized["content_hash"] = content_hash
    return normalized


def _material_revision_snapshot(
    *,
    revision: int,
    operation: str,
    materials: list[dict[str, Any]],
    created_at: str,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
) -> dict[str, Any]:
    return {
        "revision": revision,
        "operation": operation,
        "material_ids": [str(item["material_id"]) for item in materials],
        "materials": deepcopy(materials),
        "created_at": created_at,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
    }


def _material_mutation_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _path() -> Path:
    return get_path_service().get_workspace_dir() / "traittutor" / "learning-packs.json"


def _lock_path() -> Path:
    return _path().with_suffix(".lock")


def _migration_manifest_path() -> Path:
    return _path().with_suffix(".migration-v2.json")


_ACTIVE_STORE: ContextVar[tuple[SectionedRecordStore, dict[str, Any]] | None] = ContextVar(
    "learning_pack_active_store", default=None
)


def _adapter() -> SectionedRecordStore:
    return SectionedRecordStore(
        "learning_packs",
        LOCAL_ADMIN_ID,
        schema_version=2,
        path_service=get_path_service(),
    )


def _load() -> list[dict[str, Any]]:
    try:
        value = _adapter().snapshot().get(LIST_SECTION, [])
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise LearningPackStoreError("Learning-pack data has an invalid format")
        return [_normalize_pack(item) for item in value]
    except Exception as exc:
        if isinstance(exc, LearningPackStoreError):
            raise
        raise LearningPackStoreError("Unable to read learning packs") from exc


def _save(packs: list[dict[str, Any]]) -> None:
    active = _ACTIVE_STORE.get()
    if active is not None:
        adapter, payload = active
        payload[LIST_SECTION] = packs
        adapter.replace_all(payload)
        return
    adapter = _adapter()
    adapter.replace_all({"schema_version": 2, LIST_SECTION: packs})


@contextmanager
def _locked_packs():
    """Serialize read-modify-write updates across web workers and processes."""
    adapter = _adapter()
    with adapter.locked() as payload:
        packs = payload.get(LIST_SECTION, [])
        if not isinstance(packs, list) or any(not isinstance(item, dict) for item in packs):
            raise LearningPackStoreError("Learning-pack data has an invalid format")
        normalized = [_normalize_pack(item) for item in packs]
        token = _ACTIVE_STORE.set((adapter, payload))
        try:
            yield normalized
        finally:
            _ACTIVE_STORE.reset(token)


def _normalize_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the canonical Pack/Plan/material-revision shape."""
    retired_keys = {"material", "journeys", "active_journey_id", "unmatched_legacy_reviews"}
    present_retired = sorted(retired_keys.intersection(pack))
    if present_retired:
        raise LearningPackStoreError(
            "Learning Pack uses retired fields: " + ", ".join(present_retired)
        )
    if pack.get("schema_version") != 2:
        raise LearningPackStoreError("Learning Pack schema_version must be 2")
    required_lists = {
        "materials",
        "material_revisions",
        "component_plans",
        "learning_evidence",
        "calibrations",
        "repairs",
        "review_states",
        "review_attempts",
        "quiz_attempts",
        "learning_path_bindings",
    }
    invalid_lists = sorted(key for key in required_lists if not isinstance(pack.get(key), list))
    if invalid_lists:
        raise LearningPackStoreError(
            "Learning Pack is missing canonical list fields: " + ", ".join(invalid_lists)
        )
    if not isinstance(pack.get("component_progress"), dict):
        raise LearningPackStoreError("Learning Pack component_progress must be an object")
    normalized = dict(pack)
    normalized_plans: list[dict[str, Any]] = []
    for raw_plan in normalized["component_plans"]:
        if not isinstance(raw_plan, dict):
            raise LearningPackStoreError("Learning Pack component plans must contain objects")
        plan = dict(raw_plan)
        plan.setdefault("arrangement", "pending")
        plan.setdefault("arrangement_rationale", None)
        normalized_plans.append(plan)
    normalized["component_plans"] = normalized_plans
    raw_pre_assessment = normalized.get("pre_assessment")
    if raw_pre_assessment is not None and not isinstance(raw_pre_assessment, dict):
        raise LearningPackStoreError("Learning Pack pre_assessment must be an object or null")
    normalized.setdefault("pre_assessment", None)
    raw_materials = normalized["materials"]
    if any(not isinstance(item, dict) for item in raw_materials):
        raise LearningPackStoreError("Learning Pack materials must contain objects")
    materials = [_normalize_material(item) for item in raw_materials]
    normalized["materials"] = materials
    raw_revision = normalized.get("material_revision")
    if not isinstance(raw_revision, int) or isinstance(raw_revision, bool) or raw_revision < 0:
        raise LearningPackStoreError("Learning Pack material_revision is invalid")
    revisions = normalized["material_revisions"]
    if any(not isinstance(item, dict) for item in revisions):
        raise LearningPackStoreError("Learning Pack material revisions must contain objects")
    if materials and not revisions:
        raise LearningPackStoreError("Learning Pack materials require a revision snapshot")
    if revisions and revisions[-1].get("revision") != raw_revision:
        raise LearningPackStoreError("Learning Pack material revision does not match its snapshot")
    normalized["review_attempts"] = list(normalized["review_attempts"])[-MAX_REVIEW_ATTEMPTS:]
    return normalized


def list_packs() -> list[dict[str, Any]]:
    return sorted(_load(), key=lambda item: str(item.get("updated_at", "")), reverse=True)


def packs_referencing_learn_session(session_id: str) -> list[dict[str, Any]]:
    """Return the owner's Packs whose primary material references ``session_id``.

    The /home upload pipeline stores its durable Learn session id on the first
    material's metadata. This soft link is what keeps the sidebar Recents and
    the learning map deletable as one unit: deleting the session removes every
    Pack that still points at it (Assist conversations never appear here).
    """
    session_id = (session_id or "").strip()
    if not session_id:
        return []
    result: list[dict[str, Any]] = []
    for pack in _load():
        materials = pack.get("materials")
        if not isinstance(materials, list) or not materials:
            continue
        first = materials[0]
        if not isinstance(first, dict):
            continue
        metadata = first.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("learning_session_id") or "").strip() == session_id:
            result.append(pack)
    return result


def get_pack(pack_id: str) -> dict[str, Any] | None:
    return next((item for item in _load() if item.get("pack_id") == pack_id), None)


def delete_packs(pack_ids: list[str]) -> list[dict[str, Any]]:
    """Delete owner-scoped Packs in one locked, atomic store update.

    The current path service already resolves the authenticated user's
    workspace.  Returning the removed snapshots lets API callers report the
    exact result without a second, racy read.
    """
    requested = list(dict.fromkeys(pack_id.strip() for pack_id in pack_ids if pack_id.strip()))
    if not requested:
        return []
    requested_ids = set(requested)
    with _locked_packs() as packs:
        removed_by_id = {
            str(pack.get("pack_id")): _normalize_pack(pack)
            for pack in packs
            if str(pack.get("pack_id")) in requested_ids
        }
        if not removed_by_id:
            return []
        packs[:] = [pack for pack in packs if str(pack.get("pack_id")) not in requested_ids]
        _save(packs)
    return [removed_by_id[pack_id] for pack_id in requested if pack_id in removed_by_id]


def delete_pack(pack_id: str) -> dict[str, Any] | None:
    """Delete one Pack from the current owner's workspace."""
    removed = delete_packs([pack_id])
    return removed[0] if removed else None


def _normalize_goal(goal: dict[str, Any] | str | None) -> dict[str, Any] | None:
    if isinstance(goal, str):
        text = goal.strip()
        payload: dict[str, Any] = {"text": text}
    elif isinstance(goal, dict):
        text = str(goal.get("text") or goal.get("title") or "").strip()
        payload = dict(goal)
        payload["text"] = text
    else:
        return None
    if not text:
        return None
    payload.setdefault("goal_id", uuid4().hex)
    payload.setdefault("status", "active")
    payload.setdefault("created_at", _now())
    return payload


def _initial_sources(
    material: dict[str, Any], sources: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    normalized = [dict(item) for item in (sources or []) if isinstance(item, dict)]
    if material and not normalized:
        normalized.append(
            {
                "source_type": str(material.get("source_type") or "paste"),
                "source_id": material.get("source_id"),
                "title": str(material.get("title") or "Learning source"),
                "role": str((material.get("metadata") or {}).get("source_kind") or "material")
                if isinstance(material.get("metadata"), dict)
                else "material",
            }
        )
    return normalized


def _new_pack(
    *,
    title: str,
    material: dict[str, Any] | None = None,
    profile_id: str | None = None,
    goal: dict[str, Any] | str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one unsaved Pack payload shared by direct and routed creation."""
    material_payload = dict(material or {})
    initial_material = deepcopy(material_payload)
    initial_material.pop("material_id", None)
    initial_material.pop("content_hash", None)
    materials = [_normalize_material(initial_material)] if initial_material else []
    now = _now()
    pack: dict[str, Any] = {
        "schema_version": 2,
        "pack_id": uuid4().hex,
        "title": title.strip() or "未命名学习包",
        "goal": _normalize_goal(goal),
        "sources": _initial_sources(material_payload, sources),
        "materials": materials,
        "material_revision": 1 if materials else 0,
        "material_revisions": [],
        "profile_id": profile_id,
        "persona": None,
        # Learner's explicit Learn-intermediate-page choice about LLM-driven
        # component arrangement ("auto" | "basic" | None). Persisted so the
        # canvas can distinguish a deliberate opt-out from a still-pending
        # arrangement and suppress the "not yet arranged" notice.
        "arrangement_preference": None,
        "artifacts": {"courseware": [], "flashcards": [], "quiz": []},
        "flashcard_progress": {},
        "quiz_attempts": [],
        "component_plans": [],
        "active_plan_id": None,
        "component_progress": {},
        "pre_assessment": None,
        "learning_evidence": [],
        "calibrations": [],
        # Round-scoped progress-calibration results (one per calibration
        # checkpoint completion): aggregated verified evidence, a qualitative
        # difficulty evaluation, and a next-step strategy. Append-only.
        "progress_calibrations": [],
        "repairs": [],
        "review_states": [],
        "review_attempts": [],
        "learning_path_bindings": [],
        "active_learning_path_binding_revision": None,
        "created_at": now,
        "updated_at": now,
    }
    if materials:
        pack["material_revisions"].append(
            _material_revision_snapshot(
                revision=1,
                operation="initial",
                materials=materials,
                created_at=now,
            )
        )
    return pack


def create_pack(
    *,
    title: str,
    material: dict[str, Any] | None = None,
    profile_id: str | None = None,
    goal: dict[str, Any] | str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pack = _prepare_new_pack(
        title=title,
        material=material,
        profile_id=profile_id,
        goal=goal,
        sources=sources,
    )
    with _locked_packs() as packs:
        packs.append(pack)
        _save(packs)
    return pack


def _prepare_new_pack(
    *,
    title: str,
    material: dict[str, Any] | None = None,
    profile_id: str | None = None,
    goal: dict[str, Any] | str | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate and build a Pack without exposing a partially persisted record."""
    prepared_material = dict(material or {})
    metadata = prepared_material.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    mime_type = str(metadata_dict.get("mime_type") or "").split(";", 1)[0].strip().lower()
    if (
        str(prepared_material.get("source_type") or "").strip().lower() == "image"
        or metadata_dict.get("source_kind") == "image"
        or mime_type.startswith("image/")
    ):
        prepared_material = _validated_appended_material(prepared_material)
    return _new_pack(
        title=title,
        material=prepared_material,
        profile_id=profile_id,
        goal=goal,
        sources=sources,
    )


def create_pack_with_component_plan(
    *,
    title: str,
    plan_builder: Callable[[dict[str, Any]], dict[str, Any]],
    idempotency_key: str,
    material: dict[str, Any] | None = None,
    profile_id: str | None = None,
    goal: dict[str, Any] | str | None = None,
    sources: list[dict[str, Any]] | None = None,
    request_fingerprint_payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build and persist the initial Pack and Plan in one store update.

    Plan construction runs before the Pack becomes visible. A validation or
    planning failure therefore leaves no empty Pack for a retry to duplicate.
    """
    key = idempotency_key.strip()
    if not key or len(key) > 128:
        raise ValueError("Initial Pack idempotency contract is invalid")
    request_fingerprint = _material_mutation_fingerprint(
        {
            "title": title.strip() or "未命名学习包",
            "material": deepcopy(material or {}),
            "profile_id": profile_id,
            # Fingerprint caller intent, not the persisted goal projection:
            # _normalize_goal adds a new UUID/timestamp on every invocation.
            "goal": deepcopy(goal),
            "sources": _initial_sources(dict(material or {}), sources),
            "plan": deepcopy(request_fingerprint_payload or {}),
        }
    )
    with _locked_packs() as packs:
        replay = _find_initial_pack_replay(packs, key, request_fingerprint)
        if replay is not None:
            return replay
    pack = _prepare_new_pack(
        title=title,
        material=material,
        profile_id=profile_id,
        goal=goal,
        sources=sources,
    )
    plan = dict(plan_builder(deepcopy(pack)))
    plan_id = str(plan.get("plan_id") or "").strip()
    if not plan_id:
        raise InvalidComponentPlanChain("The initial learning plan requires a stable id")
    if plan.get("supersedes_plan_id"):
        raise InvalidComponentPlanChain("The first plan cannot supersede another plan")
    pack["component_plans"].append(plan)
    pack["active_plan_id"] = plan_id
    pack["component_progress"][plan_id] = {"events": [], "updated_at": _now()}
    pack["_initial_create_idempotency_key"] = key
    pack["_initial_create_request_fingerprint"] = request_fingerprint
    pack["_initial_create_plan_id"] = plan_id
    pack["updated_at"] = _now()
    with _locked_packs() as packs:
        replay = _find_initial_pack_replay(packs, key, request_fingerprint)
        if replay is not None:
            return replay
        packs.append(pack)
        _save(packs)
    return _normalize_pack(pack), deepcopy(plan)


def _find_initial_pack_replay(
    packs: list[dict[str, Any]], key: str, request_fingerprint: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for existing in packs:
        if existing.get("_initial_create_idempotency_key") != key:
            continue
        if existing.get("_initial_create_request_fingerprint") != request_fingerprint:
            raise InvalidComponentPlanChain(
                "An idempotency key cannot be reused for a different initial Pack request"
            )
        initial_plan_id = str(
            existing.get("_initial_create_plan_id") or existing.get("active_plan_id") or ""
        )
        initial_plan = next(
            (
                item
                for item in existing.get("component_plans") or []
                if isinstance(item, dict) and item.get("plan_id") == initial_plan_id
            ),
            None,
        )
        if initial_plan is None:
            raise LearningPackStoreError("Idempotent Pack replay has no initial plan")
        return _normalize_pack(existing), deepcopy(initial_plan)
    return None


def create_capability_routed_pack_or_replay(
    decision_id: str,
    *,
    learning_goal: str,
) -> tuple[dict[str, Any], bool]:
    """Create one goal-preserving Pack for a confirmed Learn decision."""
    normalized_decision_id = decision_id.strip()
    normalized_goal = learning_goal.strip()
    if not normalized_decision_id or len(normalized_decision_id) > 96:
        raise ValueError("capability decision id is invalid")
    if not normalized_goal:
        raise ValueError("learning goal must not be blank")
    with _locked_packs() as packs:
        for pack in packs:
            materials = pack.get("materials")
            material = materials[0] if isinstance(materials, list) and materials else None
            metadata = material.get("metadata") if isinstance(material, dict) else None
            if (
                isinstance(metadata, dict)
                and metadata.get("capability_decision_id") == normalized_decision_id
            ):
                return _normalize_pack(pack), True
        pack = _new_pack(
            title=normalized_goal[:180],
            goal={
                "text": normalized_goal,
                "origin": "assistant_confirmed_learn",
                "status": "active",
            },
            material={
                "source_type": "paste",
                "title": normalized_goal[:180],
                "text": normalized_goal,
                "metadata": {
                    "capability_decision_id": normalized_decision_id,
                    "source_kind": "learning_goal",
                },
            },
            sources=[
                {
                    "source_type": "user_goal",
                    "source_id": normalized_decision_id,
                    "title": normalized_goal[:180],
                    "role": "learning_goal",
                }
            ],
        )
        packs.append(pack)
        _save(packs)
        return pack, False


def update_pack(pack_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    if "material" in patch:
        raise InvalidPackMaterialOperation(
            "material updates require append/remove/reorder revision semantics"
        )
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            # Material writes use the revision APIs below.
            for key in ("title", "persona", "profile_id", "arrangement_preference"):
                if key in patch:
                    pack[key] = patch[key]
            if "goal" in patch:
                pack["goal"] = _normalize_goal(patch["goal"])
            if "sources" in patch and isinstance(patch["sources"], list):
                pack["sources"] = [
                    dict(item) for item in patch["sources"] if isinstance(item, dict)
                ]
            if "source" in patch and isinstance(patch["source"], dict):
                pack.setdefault("sources", []).append(dict(patch["source"]))
            if "artifact" in patch and isinstance(patch["artifact"], dict):
                artifact = patch["artifact"]
                kind = str(artifact.get("kind") or "")
                if kind in pack["artifacts"]:
                    generation_id = str(artifact.get("verified_generation_id") or "")
                    already_attached = generation_id and any(
                        isinstance(item, dict)
                        and str(item.get("verified_generation_id") or "") == generation_id
                        for item in pack["artifacts"][kind]
                    )
                    if not already_attached:
                        pack["artifacts"][kind].append(artifact)
            if "flashcard_progress" in patch and isinstance(patch["flashcard_progress"], dict):
                pack["flashcard_progress"].update(patch["flashcard_progress"])
            if "quiz_attempt" in patch and isinstance(patch["quiz_attempt"], dict):
                pack["quiz_attempts"].append(patch["quiz_attempt"])
            if "active_plan_id" in patch:
                pack["active_plan_id"] = patch["active_plan_id"]
            if "component_progress" in patch and isinstance(patch["component_progress"], dict):
                pack["component_progress"].update(patch["component_progress"])
            pack["updated_at"] = _now()
            _save(packs)
            return pack
    return None


def _validated_material_mutation(
    *, expected_revision: int, idempotency_key: str
) -> tuple[int, str]:
    if isinstance(expected_revision, bool) or expected_revision < 0:
        raise InvalidPackMaterialOperation("expected_revision must be a non-negative integer")
    key = idempotency_key.strip()
    if not key or len(key) > 128:
        raise InvalidPackMaterialOperation("idempotency_key must contain 1 to 128 characters")
    return expected_revision, key


def _validated_appended_material(material: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(material)
    # IDs and hashes are server-derived for new records.  They may be preserved
    # when loading an existing Pack, but never accepted as client authority.
    payload.pop("material_id", None)
    payload.pop("content_hash", None)
    metadata = payload.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    source_type = str(payload.get("source_type") or "").strip().lower()
    mime_type = str(metadata_dict.get("mime_type") or "").split(";", 1)[0].strip().lower()
    if (
        source_type == "image"
        or metadata_dict.get("source_kind") == "image"
        or mime_type.startswith("image/")
    ):
        from traittutor.generate.image_material import (
            LearningImageError,
            canonical_prepared_image_material,
        )
        from traittutor.multi_user.context import get_current_user

        try:
            payload = canonical_prepared_image_material(
                payload,
                owner_id=get_current_user().id,
            )
        except LearningImageError as exc:
            raise InvalidPackMaterialOperation(exc.code) from exc
        source_type = "upload"
    if source_type not in PACK_MATERIAL_SOURCE_TYPES:
        raise InvalidPackMaterialOperation("unsupported material source type")
    if source_type == "paste" and not str(payload.get("text") or "").strip():
        raise InvalidPackMaterialOperation("paste material requires non-empty text")
    if source_type != "paste" and not str(payload.get("source_id") or "").strip():
        raise InvalidPackMaterialOperation(f"{source_type} material requires source_id")
    return _normalize_material(payload)


def _material_revision_replay(
    pack: dict[str, Any], *, idempotency_key: str, request_fingerprint: str
) -> tuple[dict[str, Any], bool] | None:
    for revision in pack.get("material_revisions") or []:
        if not isinstance(revision, dict) or revision.get("idempotency_key") != idempotency_key:
            continue
        if revision.get("request_fingerprint") != request_fingerprint:
            raise MaterialIdempotencyConflict(
                "material idempotency key was already used for different input"
            )
        return deepcopy(revision), True
    return None


def _mutate_pack_materials(
    pack_id: str,
    *,
    operation: str,
    expected_revision: int,
    idempotency_key: str,
    material: dict[str, Any] | None = None,
    material_id: str | None = None,
    material_ids: list[str] | None = None,
) -> tuple[dict[str, Any], bool] | None:
    expected, key = _validated_material_mutation(
        expected_revision=expected_revision, idempotency_key=idempotency_key
    )
    appended = _validated_appended_material(material or {}) if operation == "append" else None
    normalized_material_id = str(material_id or "").strip()
    normalized_order = [str(value).strip() for value in (material_ids or [])]
    fingerprint = _material_mutation_fingerprint(
        {
            "operation": operation,
            "expected_revision": expected,
            "material": appended,
            "material_id": normalized_material_id,
            "material_ids": normalized_order,
        }
    )
    with _locked_packs() as packs:
        for index, raw_pack in enumerate(packs):
            if raw_pack.get("pack_id") != pack_id:
                continue
            pack = _normalize_pack(raw_pack)
            replay = _material_revision_replay(
                pack, idempotency_key=key, request_fingerprint=fingerprint
            )
            if replay is not None:
                return replay
            actual = int(pack.get("material_revision") or 0)
            if actual != expected:
                raise MaterialRevisionConflict(expected, actual)
            current = [deepcopy(item) for item in pack.get("materials") or []]
            current_ids = [str(item.get("material_id") or "") for item in current]
            if operation == "append":
                assert appended is not None
                if appended["material_id"] in current_ids:
                    raise InvalidPackMaterialOperation("material already belongs to this Pack")
                updated = [*current, appended]
            elif operation == "remove":
                if not normalized_material_id or normalized_material_id not in current_ids:
                    raise InvalidPackMaterialOperation("material does not belong to this Pack")
                updated = [
                    item
                    for item in current
                    if str(item.get("material_id") or "") != normalized_material_id
                ]
            elif operation == "reorder":
                if len(normalized_order) != len(set(normalized_order)):
                    raise InvalidPackMaterialOperation("material order contains duplicate IDs")
                if set(normalized_order) != set(current_ids) or len(normalized_order) != len(
                    current_ids
                ):
                    raise InvalidPackMaterialOperation(
                        "material order must contain every current material exactly once"
                    )
                by_id = {str(item["material_id"]): item for item in current}
                updated = [by_id[item_id] for item_id in normalized_order]
            else:
                raise InvalidPackMaterialOperation("unknown material operation")

            now = _now()
            revision = _material_revision_snapshot(
                revision=actual + 1,
                operation=operation,
                materials=updated,
                created_at=now,
                idempotency_key=key,
                request_fingerprint=fingerprint,
            )
            pack["materials"] = updated
            pack["material_revision"] = revision["revision"]
            pack.setdefault("material_revisions", []).append(revision)
            has_artifacts = any(
                isinstance(items, list) and bool(items)
                for items in (pack.get("artifacts") or {}).values()
            )
            if has_artifacts and operation == "remove":
                pack["material_dependency_state"] = {
                    "status": "needs_review",
                    "reason": "material_removed",
                    "revision": revision["revision"],
                }
            pack["updated_at"] = now
            packs[index] = pack
            _save(packs)
            return deepcopy(revision), False
    return None


def append_pack_material(
    pack_id: str,
    *,
    material: dict[str, Any],
    expected_revision: int,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool] | None:
    """Append one server-identified material under CAS and idempotency."""
    return _mutate_pack_materials(
        pack_id,
        operation="append",
        material=material,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
    )


def remove_pack_material(
    pack_id: str,
    *,
    material_id: str,
    expected_revision: int,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool] | None:
    """Remove one current material while retaining prior revision snapshots."""
    return _mutate_pack_materials(
        pack_id,
        operation="remove",
        material_id=material_id,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
    )


def reorder_pack_materials(
    pack_id: str,
    *,
    material_ids: list[str],
    expected_revision: int,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool] | None:
    """Replace only the active material order, never the material set."""
    return _mutate_pack_materials(
        pack_id,
        operation="reorder",
        material_ids=material_ids,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
    )


def get_pack_material_revision(pack_id: str, revision: int) -> dict[str, Any] | None:
    """Return one immutable material snapshot for owner-scoped recovery."""
    pack = get_pack(pack_id)
    if pack is None:
        return None
    return next(
        (
            deepcopy(item)
            for item in pack.get("material_revisions") or []
            if isinstance(item, dict) and item.get("revision") == revision
        ),
        None,
    )


def active_learning_path_binding(pack: dict[str, Any], *, owner_id: str) -> dict[str, Any] | None:
    """Return the current owner-held Pack-to-path binding, never an inference.

    The caller must provide the current authenticated owner.  A stale copied
    Pack payload or an old owner field therefore cannot turn a Pack ID into a
    cross-user LearningProgress target.
    """
    expected_owner = owner_id.strip()
    revision = pack.get("active_learning_path_binding_revision")
    if not expected_owner or not isinstance(revision, int) or revision < 1:
        return None
    for binding in pack.get("learning_path_bindings") or []:
        if not isinstance(binding, dict):
            continue
        if binding.get("revision") != revision or binding.get("status") != "active":
            continue
        if str(binding.get("owner_id") or "") != expected_owner:
            return None
        return dict(binding)
    return None


def create_learning_path_binding(
    pack_id: str,
    *,
    owner_id: str,
    learning_path_id: str,
    subject_id: str,
    allowed_kc_ids: list[str],
    graph_fingerprint: str,
    graph_version: int,
) -> tuple[dict[str, Any], bool] | None:
    """Append one server-authored Pack-to-LearningProgress graph snapshot.

    Repeating the exact active contract is idempotent.  A changed persisted
    graph or reduced allowed-KC set supersedes the prior snapshot instead of
    mutating it, so an audit can tell which contract was active at submission
    time.  Validation belongs here too because direct store callers must not
    manufacture a browser-owned link.
    """
    owner = owner_id.strip()
    path_id = learning_path_id.strip()
    subject = subject_id.strip()
    allowed = sorted({str(kc).strip() for kc in allowed_kc_ids if str(kc).strip()})
    fingerprint = graph_fingerprint.strip()
    if (
        not owner
        or not path_id
        or not subject
        or not allowed
        or not fingerprint
        or graph_version < 0
    ):
        raise InvalidLearningPathBinding("A complete persisted learning-path graph is required")
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            current = active_learning_path_binding(pack, owner_id=owner)
            contract = {
                "owner_id": owner,
                "learning_path_id": path_id,
                "subject_id": subject,
                "allowed_kc_ids": allowed,
                "graph_fingerprint": fingerprint,
                "graph_version": graph_version,
            }
            if current is not None and all(
                current.get(key) == value for key, value in contract.items()
            ):
                return current, True
            for binding in pack["learning_path_bindings"]:
                if isinstance(binding, dict) and binding.get("status") == "active":
                    binding["status"] = "superseded"
                    binding["superseded_at"] = _now()
            revisions = [
                int(binding.get("revision") or 0)
                for binding in pack["learning_path_bindings"]
                if isinstance(binding, dict)
            ]
            binding = {
                **contract,
                "binding_id": uuid4().hex,
                "revision": max(revisions, default=0) + 1,
                "status": "active",
                "linked_at": _now(),
            }
            pack["learning_path_bindings"].append(binding)
            pack["active_learning_path_binding_revision"] = binding["revision"]
            pack["updated_at"] = _now()
            _save(packs)
            return dict(binding), False
    return None


def list_component_plans(pack_id: str) -> list[dict[str, Any]]:
    pack = get_pack(pack_id)
    if pack is None:
        return []
    return list(pack.get("component_plans") or [])


def save_pre_assessment(
    pack_id: str, assessment: dict[str, Any]
) -> tuple[dict[str, Any], bool] | None:
    """Persist one server-owned probe set without overwriting an active attempt."""
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            existing = pack.get("pre_assessment")
            if isinstance(existing, dict):
                return deepcopy(existing), True
            payload = deepcopy(assessment)
            pack["pre_assessment"] = payload
            pack["updated_at"] = _now()
            _save(packs)
            return deepcopy(payload), False
    return None


def submit_pre_assessment(
    pack_id: str,
    assessment_id: str,
    *,
    answers: list[dict[str, Any]],
    event_id: str | None = None,
) -> tuple[dict[str, Any], bool] | None:
    """Grade probes inside the Pack lock; never call the learner-event chain."""
    fingerprint = _request_fingerprint({"answers": answers})
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            assessment = pack.get("pre_assessment")
            if not isinstance(assessment, dict) or assessment.get("assessment_id") != assessment_id:
                raise InvalidPreAssessmentTransition("Pre-assessment does not match this Pack")
            status = str(assessment.get("status") or "")
            if status == "answered":
                same_event = bool(event_id) and assessment.get("_submission_event_id") == event_id
                same_request = assessment.get("_submission_fingerprint") == fingerprint
                if same_request and (same_event or not event_id):
                    return deepcopy(assessment), True
                raise InvalidPreAssessmentTransition("Pre-assessment was already answered")
            if status != "pending":
                raise InvalidPreAssessmentTransition(
                    f"Cannot submit a pre-assessment in {status or 'unknown'} state"
                )
            probes = [item for item in assessment.get("probes") or [] if isinstance(item, dict)]
            by_question = {str(item.get("question_id") or ""): item for item in probes}
            answer_ids = [str(item.get("question_id") or "") for item in answers]
            if (
                len(answer_ids) != len(set(answer_ids))
                or set(answer_ids) != set(by_question)
                or len(answers) != len(probes)
            ):
                raise InvalidPreAssessmentTransition(
                    "Submit exactly one answer for every pre-assessment question"
                )
            responses: list[dict[str, Any]] = []
            for answer in answers:
                question_id = str(answer.get("question_id") or "")
                probe = by_question[question_id]
                selected_index = answer.get("selected_index")
                confidence = answer.get("confidence")
                options = probe.get("options") or []
                if (
                    not isinstance(selected_index, int)
                    or isinstance(selected_index, bool)
                    or not 0 <= selected_index < len(options)
                ):
                    raise InvalidPreAssessmentTransition(
                        "Pre-assessment selected_index is outside the option range"
                    )
                if confidence is not None and confidence not in {"低", "中", "高"}:
                    raise InvalidPreAssessmentTransition(
                        "Pre-assessment confidence must be 低, 中, or 高"
                    )
                responses.append(
                    {
                        "question_id": question_id,
                        "selected_index": selected_index,
                        "confidence": confidence,
                        "correct": selected_index == probe.get("correct_index"),
                    }
                )
            timestamp = _now()
            assessment["responses"] = responses
            assessment["status"] = "answered"
            assessment["updated_at"] = timestamp
            assessment["_submission_event_id"] = event_id
            assessment["_submission_fingerprint"] = fingerprint
            pack["updated_at"] = timestamp
            _save(packs)
            return deepcopy(assessment), False
    return None


def skip_pre_assessment(pack_id: str, assessment_id: str) -> dict[str, Any] | None:
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            assessment = pack.get("pre_assessment")
            if not isinstance(assessment, dict) or assessment.get("assessment_id") != assessment_id:
                raise InvalidPreAssessmentTransition("Pre-assessment does not match this Pack")
            status = str(assessment.get("status") or "")
            if status == "skipped":
                return deepcopy(assessment)
            if status != "pending":
                raise InvalidPreAssessmentTransition(
                    f"Cannot skip a pre-assessment in {status or 'unknown'} state"
                )
            timestamp = _now()
            assessment["status"] = "skipped"
            assessment["responses"] = []
            assessment["updated_at"] = timestamp
            pack["updated_at"] = timestamp
            _save(packs)
            return deepcopy(assessment)
    return None


def consume_pre_assessment(pack_id: str) -> dict[str, Any] | None:
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            assessment = pack.get("pre_assessment")
            if not isinstance(assessment, dict):
                return None
            if assessment.get("status") == "consumed":
                return deepcopy(assessment)
            if assessment.get("status") not in {"answered", "skipped", "not_needed"}:
                raise InvalidPreAssessmentTransition(
                    "Only a resolved pre-assessment can be consumed by arrangement"
                )
            timestamp = _now()
            assessment["status"] = "consumed"
            assessment["updated_at"] = timestamp
            pack["updated_at"] = timestamp
            _save(packs)
            return deepcopy(assessment)
    return None


def active_plan_has_started(pack: dict[str, Any]) -> bool:
    plan_id = str(pack.get("active_plan_id") or "")
    active = next(
        (
            item
            for item in pack.get("component_plans") or []
            if isinstance(item, dict) and str(item.get("plan_id") or "") == plan_id
        ),
        None,
    )
    if active is None:
        return False
    if any(
        isinstance(component, dict) and component.get("status", "pending") != "pending"
        for component in active.get("components") or []
    ):
        return True
    progress = (pack.get("component_progress") or {}).get(plan_id) or {}
    return bool(progress.get("events"))


def mark_active_plan_arrangement(
    pack_id: str,
    arrangement: str,
    *,
    rationale: str | None = None,
) -> dict[str, Any] | None:
    if arrangement not in {"pending", "llm", "deterministic_fallback"}:
        raise ValueError("Unknown learning-plan arrangement state")
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            active_id = str(pack.get("active_plan_id") or "")
            plan = next(
                (
                    item
                    for item in pack.get("component_plans") or []
                    if isinstance(item, dict) and str(item.get("plan_id") or "") == active_id
                ),
                None,
            )
            if plan is None:
                return None
            timestamp = _now()
            plan["arrangement"] = arrangement
            plan["arrangement_rationale"] = rationale
            plan["updated_at"] = timestamp
            pack["updated_at"] = timestamp
            _save(packs)
            return deepcopy(plan)
    return None


def get_component_plan(pack_id: str, plan_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in list_component_plans(pack_id) if item.get("plan_id") == plan_id),
        None,
    )


def create_component_plan(pack_id: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Persist one immutable plan version and make it active.

    Earlier plans remain available for audit and reconnect. Replanning marks
    only the previous active version as superseded; completed component output
    is copied by the selector rather than mutated here.
    """
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            plans = pack.setdefault("component_plans", [])
            plan_id = str(plan.get("plan_id") or "")
            if not plan_id:
                return None
            existing = next((item for item in plans if item.get("plan_id") == plan_id), None)
            if existing is not None:
                return dict(existing)
            previous_id = pack.get("active_plan_id")
            supersedes_plan_id = plan.get("supersedes_plan_id")
            active = next((item for item in plans if item.get("plan_id") == previous_id), None)
            if active is None and supersedes_plan_id:
                raise InvalidComponentPlanChain("The first plan cannot supersede another plan")
            if active is not None and supersedes_plan_id != previous_id:
                # A repeated initial-create request is safe to treat as an
                # idempotent read of the current plan. Any other stale plan
                # reference would fork the version chain and is rejected.
                if not supersedes_plan_id:
                    return dict(active)
                raise InvalidComponentPlanChain(
                    "A new plan must supersede the active learning plan"
                )
            if previous_id:
                for previous in plans:
                    if (
                        previous.get("plan_id") == previous_id
                        and previous.get("status") == "active"
                    ):
                        previous["status"] = "superseded"
                        previous["updated_at"] = _now()
            payload = dict(plan)
            plans.append(payload)
            pack["active_plan_id"] = plan_id
            # A new active plan means another learning round has started.  A
            # legacy goal-level completion is reopened, while the additive
            # round marker is always cleared.  Goal/mastery completion is not
            # inferred from merely visiting every component.
            goal = pack.get("goal")
            if isinstance(goal, dict):
                if goal.get("status") == "completed":
                    goal["status"] = "active"
                    goal.pop("completed_at", None)
                goal.pop("round_status", None)
                goal.pop("round_completed_at", None)
            pack["component_progress"].setdefault(plan_id, {"events": [], "updated_at": _now()})
            pack["updated_at"] = _now()
            _save(packs)
            return payload
    return None


def create_repair(
    pack_id: str,
    *,
    action_id: str,
    question_id: str,
    artifact_ref: str,
    concept_id: str,
    user_answer: str,
    correct_rule: str,
    error_type: str = "deviation",
    contrast: str = "",
    retry_prompt: str = "",
    retry_expected_answer: str = "",
    source_event_id: str = "",
    canonical_source_event_id: str = "",
    review_owner_id: str = "",
    review_subject_id: str = "",
    review_kc_id: str = "",
    retry_question_id: str = "",
) -> dict[str, Any] | None:
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            existing = next(
                (
                    item
                    for item in pack.setdefault("repairs", [])
                    if (source_event_id and item.get("source_event_id") == source_event_id)
                    or (
                        (
                            item.get("question_id") == question_id
                            and item.get("artifact_ref") == artifact_ref
                        )
                        and item.get("status") not in {"repaired", "scheduled"}
                    )
                ),
                None,
            )
            if existing:
                return existing
            repair: dict[str, Any] = {
                "repair_id": f"repair-{uuid4().hex}",
                "action_id": action_id,
                "question_id": question_id,
                "artifact_ref": artifact_ref,
                "concept_id": concept_id,
                "user_answer": user_answer,
                "correct_rule": correct_rule,
                "error_type": error_type,
                "contrast": contrast,
                "retry_prompt": retry_prompt,
                "retry_expected_answer": retry_expected_answer,
                # These fields are private, server-owned provenance for the
                # later review answer.  The client never receives them through
                # the learner Pack projection.  In particular, keep the
                # canonical event ID separate from the component request ID:
                # the latter is only an attempt token chosen at the boundary.
                "canonical_source_event_id": canonical_source_event_id or None,
                "review_owner_id": review_owner_id or None,
                "review_subject_id": review_subject_id or None,
                "review_kc_id": review_kc_id or None,
                "retry_question_id": retry_question_id or None,
                "source_event_id": source_event_id or None,
                "status": "identified",
                "retry_count": 0,
                "retry_event_receipts": {},
                "next_review_at": None,
                "created_at": _now(),
            }
            pack["repairs"].append(repair)
            refresh_repair_retry_assignments(pack, artifact_ref)
            pack["updated_at"] = _now()
            _save(packs)
            return repair
    return None


def refresh_repair_retry_assignments(pack: dict[str, Any], artifact_ref: str) -> None:
    """Keep retry items distinct from every error whose correction will be shown."""
    artifacts_value = pack.get("artifacts")
    artifacts = artifacts_value if isinstance(artifacts_value, dict) else {}
    artifact = next(
        (
            item
            for item in artifacts.get("quiz") or []
            if isinstance(item, dict)
            and str(item.get("verified_generation_id") or "") == artifact_ref
        ),
        None,
    )
    if artifact is None:
        return
    items = [item for item in artifact.get("items") or [] if isinstance(item, dict)]
    by_question = {str(item.get("question_id") or ""): item for item in items}
    repairs = [
        item
        for item in pack.get("repairs") or []
        if isinstance(item, dict)
        and item.get("artifact_ref") == artifact_ref
        and item.get("status") not in {"repaired", "scheduled"}
    ]
    error_ids = {str(item.get("question_id") or "") for item in repairs}
    available = [
        item
        for item in items
        if str(item.get("question_id") or "") not in error_ids and item.get("correct_answer")
    ]
    for repair in repairs:
        original = by_question.get(str(repair.get("question_id") or ""), {})
        concept_id = str(original.get("node_id") or "")
        retry = next(
            (
                item
                for item in available
                if concept_id and str(item.get("node_id") or "") == concept_id
            ),
            None,
        ) or (available[0] if available else None)
        if retry is not None:
            available.remove(retry)
            repair["correct_rule"] = str(
                original.get("explanation")
                or repair.get("correct_rule")
                or "Review the source-grounded rule."
            )
            repair["contrast"] = str(original.get("correct_answer") or "")
            repair["retry_prompt"] = str(
                retry.get("question") or "Apply the corrected rule to a related item."
            )
            repair["retry_expected_answer"] = str(retry.get("correct_answer") or "")
            repair["retry_question_id"] = str(retry.get("question_id") or "")
            repair["retry_question_type"] = str(retry.get("question_type") or "short")
            repair["retry_evidence_strength"] = "strong"
            repair["retry_options"] = [
                {key: option[key] for key in ("key", "id", "text") if key in option}
                for option in (retry.get("options") or [])
                if isinstance(option, dict)
            ]
            continue
        # When the artifact has no unused near item, retain a meaningful retry
        # without revealing the exact answer immediately above it.
        answer = str(original.get("correct_answer") or "").strip()
        explanation = str(
            original.get("explanation")
            or repair.get("correct_rule")
            or "Review the source-grounded rule."
        )
        if answer and explanation.casefold().startswith(answer.casefold()):
            explanation = (
                explanation[len(answer) :].lstrip(" .,:;，。；：-—")
                or "Review the source-grounded rule."
            )
        repair["correct_rule"] = explanation
        repair["contrast"] = ""
        repair["retry_prompt"] = str(
            original.get("question")
            or repair.get("retry_prompt")
            or "Apply the corrected rule again."
        )
        repair["retry_expected_answer"] = answer or str(repair.get("retry_expected_answer") or "")
        repair["retry_question_id"] = str(original.get("question_id") or "")
        repair["retry_question_type"] = str(original.get("question_type") or "short")
        repair["retry_evidence_strength"] = "weak"
        repair["retry_options"] = [
            {key: option[key] for key in ("key", "id", "text") if key in option}
            for option in (original.get("options") or [])
            if isinstance(option, dict)
        ]


def record_calibration(pack_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            records = pack.setdefault("calibrations", [])
            question_id = str(record.get("question_id") or "")
            artifact_ref = str(record.get("artifact_ref") or "")
            records[:] = [
                item
                for item in records
                if not (
                    str(item.get("question_id") or "") == question_id
                    and str(item.get("artifact_ref") or "") == artifact_ref
                )
            ]
            records.append(dict(record))
            pack["updated_at"] = _now()
            _save(packs)
            return dict(record)
    return None


def save_progress_calibration(pack_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
    """Append one round-scoped progress-calibration result.

    The result is a support projection over accumulated verified evidence; it
    never rewrites components or BKT and stays append-only so earlier rounds
    remain auditable.
    """
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            records = pack.setdefault("progress_calibrations", [])
            records.append(dict(record))
            pack["updated_at"] = _now()
            _save(packs)
            return dict(record)
    return None


def record_repair_retry(
    pack_id: str,
    repair_id: str,
    *,
    answer: str,
    event_id: str,
    before_mutation: Callable[[dict[str, Any], bool], None] | None = None,
) -> dict[str, Any] | None:
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            repair = next(
                (item for item in pack.get("repairs") or [] if item.get("repair_id") == repair_id),
                None,
            )
            if repair is None:
                return None
            attempts = repair.setdefault("retry_attempts", [])
            receipts = repair.setdefault("retry_event_receipts", {})
            if not isinstance(receipts, dict):
                raise LearningPackStoreError("Repair retry receipts have an invalid format")
            answer_fingerprint = hashlib.sha256(answer.encode("utf-8")).hexdigest()
            receipt = receipts.get(event_id)
            if isinstance(receipt, dict):
                if receipt.get("answer_fingerprint") != answer_fingerprint:
                    raise InvalidComponentTransition(
                        "A repair retry event ID cannot be reused with a different answer"
                    )
                if before_mutation is not None:
                    before_mutation(repair, bool(receipt.get("correct")))
                return repair
            duplicate = next((item for item in attempts if item.get("event_id") == event_id), None)
            if duplicate is not None:
                if str(duplicate.get("answer") or "") != answer:
                    raise InvalidComponentTransition(
                        "A repair retry event ID cannot be reused with a different answer"
                    )
                # A prior version or interrupted process may have committed
                # the legacy retry state without the independent canonical
                # event. Replay its event-first callback without incrementing
                # the retry count or re-scheduling the repair.
                if before_mutation is not None:
                    before_mutation(repair, bool(duplicate.get("correct")))
                receipts[event_id] = {
                    "answer_fingerprint": answer_fingerprint,
                    "correct": bool(duplicate.get("correct")),
                }
                pack["updated_at"] = _now()
                _save(packs)
                return repair
            if repair.get("status") in {"repaired", "scheduled"}:
                return repair
            if repair.get("status") == "deferred":
                raise InvalidComponentTransition(
                    "This repair is temporarily deferred; attempt a different learning "
                    "component before retrying it"
                )
            expected = str(repair.get("retry_expected_answer") or "").strip()
            from traittutor.learning.grading import grade_answer

            correct = bool(expected) and grade_answer(
                answer, expected, str(repair.get("retry_question_type") or "short")
            )
            # The durable answer verdict is server-derived from the private
            # repair artifact.  Write canonical evidence before touching the
            # legacy retry/schedule state; a callback failure leaves this
            # mutation unapplied and an identical request can safely replay.
            if before_mutation is not None:
                before_mutation(repair, correct)
            receipts[event_id] = {
                "answer_fingerprint": answer_fingerprint,
                "correct": correct,
            }
            repair["retry_count"] = int(repair.get("retry_count") or 0) + 1
            repair["last_retry_answer"] = answer
            repair["last_retry_correct"] = correct
            attempts.append(
                {
                    "event_id": event_id,
                    "answer": answer,
                    "correct": correct,
                    "occurred_at": _now(),
                }
            )
            if len(attempts) > 32:
                del attempts[:-32]
            repair["status"] = "repaired" if correct else "retrying"
            if not correct and int(repair.get("retry_count") or 0) >= 2:
                repair["status"] = "deferred"
                repair["deferred_at"] = _now()
                repair["suggested_next_component_id"] = _suggest_next_component_id(
                    pack,
                    str(repair.get("action_id") or ""),
                )
            if correct:
                from datetime import timedelta

                due_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
                repair["status"] = "scheduled"
                repair["next_review_at"] = due_at
                review_id = f"review-repair-{repair_id}"
                reviews = pack.setdefault("review_states", [])
                if not any(item.get("review_id") == review_id for item in reviews):
                    reviews.append(
                        {
                            "review_id": review_id,
                            "pack_id": pack_id,
                            "concept_id": repair.get("concept_id") or repair_id,
                            "knowledge_type": "concept",
                            "source": "repair",
                            "due_at": due_at,
                            "priority": 1,
                            "interval_index": 0,
                            "consecutive_correct": 0,
                            "consecutive_wrong": 0,
                            "last_result": True,
                        }
                    )
            pack["updated_at"] = _now()
            _save(packs)
            return repair
    return None


def _suggest_next_component_id(pack: dict[str, Any], current_component_id: str) -> str | None:
    plan_id = str(pack.get("active_plan_id") or "")
    plan = next(
        (
            item
            for item in pack.get("component_plans") or []
            if str(item.get("plan_id") or "") == plan_id
        ),
        None,
    )
    if not isinstance(plan, dict):
        return None
    components = [item for item in plan.get("components") or [] if isinstance(item, dict)]
    alternatives = [
        item for item in components if str(item.get("component_id") or "") != current_component_id
    ]
    candidate = next(
        (item for item in alternatives if item.get("status") not in {"completed", "skipped"}),
        None,
    ) or (alternatives[0] if alternatives else None)
    if candidate is None:
        return None
    return str(candidate.get("component_id") or "") or None


def update_review_result(
    pack_id: str,
    review_id: str,
    *,
    correct: bool,
    event_id: str,
    before_schedule: Callable[[], None] | None = None,
) -> dict[str, Any] | None:
    """Idempotently update one currently due server-owned review."""
    intervals = (1, 3, 7, 14, 30, 60)
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            review = next(
                (
                    item
                    for item in pack.get("review_states") or []
                    if item.get("review_id") == review_id
                ),
                None,
            )
            if review is None:
                return None
            attempts = pack.setdefault("review_attempts", [])
            duplicate = next((item for item in attempts if item.get("event_id") == event_id), None)
            if duplicate is not None:
                if duplicate.get("review_id") != review_id:
                    raise InvalidComponentTransition(
                        "A review event ID cannot be reused for another review"
                    )
                if bool(duplicate.get("correct")) != correct:
                    raise InvalidComponentTransition(
                        "A review event ID cannot be replayed with a different result"
                    )
                # A prior process may have committed the review schedule but
                # died before its independent canonical event write.  Replay
                # the event-first callback so its own stable attempt identity
                # can repair that gap without re-scheduling the review.
                if before_schedule is not None:
                    before_schedule()
                return review
            try:
                due_at = datetime.fromisoformat(
                    str(review.get("due_at") or "").replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise InvalidComponentTransition("The review has an invalid due date") from exc
            due_at = due_at.replace(tzinfo=UTC) if due_at.tzinfo is None else due_at.astimezone(UTC)
            if due_at > datetime.now(UTC):
                raise InvalidComponentTransition("The review is not due yet")
            # The source event must exist before the derived review schedule.
            # A callback failure leaves this store untouched; a later replay
            # retains the same event_id and therefore cannot double-count BKT.
            if before_schedule is not None:
                before_schedule()
            index = int(review.get("interval_index") or 0)
            if correct:
                review["consecutive_correct"] = int(review.get("consecutive_correct") or 0) + 1
                review["consecutive_wrong"] = 0
                index = min(
                    len(intervals) - 1, index + (2 if review["consecutive_correct"] >= 2 else 1)
                )
            else:
                review["consecutive_wrong"] = int(review.get("consecutive_wrong") or 0) + 1
                review["consecutive_correct"] = 0
                index = max(0, index - 1)
            review["interval_index"] = index
            review["last_result"] = correct
            attempts.append(
                {
                    "event_id": event_id,
                    "review_id": review_id,
                    "correct": correct,
                    "occurred_at": _now(),
                }
            )
            if len(attempts) > MAX_REVIEW_ATTEMPTS:
                del attempts[:-MAX_REVIEW_ATTEMPTS]
            from datetime import timedelta

            review["due_at"] = (datetime.now(UTC) + timedelta(days=intervals[index])).isoformat()
            pack["updated_at"] = _now()
            _save(packs)
            return review
    return None


def validate_component_event(
    pack_id: str,
    plan_id: str,
    component_id: str,
    event: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Validate one event without changing Pack state.

    The HTTP layer uses this preflight before writing the immutable canonical
    learner event.  That keeps invalid/stale component transitions from
    producing evidence while preserving event-first ordering for valid ones.
    """
    pack = get_pack(pack_id)
    if pack is None:
        return None
    plan = next(
        (item for item in pack.get("component_plans", []) if item.get("plan_id") == plan_id),
        None,
    )
    if plan is None:
        return None
    component = next(
        (item for item in plan.get("components", []) if item.get("component_id") == component_id),
        None,
    )
    if component is None:
        return None
    progress = pack.setdefault("component_progress", {}).setdefault(plan_id, {"events": []})
    events = progress.setdefault("events", [])
    event_id = str(event.get("event_id") or "")
    duplicate = next(
        (item for item in events if event_id and item.get("event_id") == event_id), None
    )
    if duplicate is not None:
        if (
            duplicate.get("plan_id") not in {None, plan_id}
            or duplicate.get("component_id") not in {None, component_id}
            or duplicate.get("_request_fingerprint") not in {None, _request_fingerprint(event)}
        ):
            raise InvalidComponentTransition(
                "A component event ID cannot be reused with a different request"
            )
        return pack, component
    if pack.get("active_plan_id") != plan_id or plan.get("status") != "active":
        raise InvalidComponentTransition("Events can only update the active learning plan")
    action = str(event.get("action") or "")
    dependencies = set(component.get("dependencies") or [])
    completed = {
        str(item.get("component_id"))
        for item in plan.get("components", [])
        if item.get("status") in {"completed", "skipped"}
    }
    if action in {"start", "complete", "feedback"} and not dependencies.issubset(completed):
        raise InvalidComponentTransition("Complete prerequisite components before this step")
    current_status = str(component.get("status") or "pending")
    allowed = (
        (action == "start" and current_status == "pending")
        or (action == "complete" and current_status in {"pending", "active", "degraded"})
        or (
            action == "skip"
            and not component.get("required", True)
            and current_status in {"pending", "active", "degraded"}
        )
        or (action == "degrade" and current_status in {"pending", "active"})
        or (action == "retry" and current_status == "degraded")
        or (action == "feedback" and current_status in {"pending", "active"})
    )
    if not allowed:
        raise InvalidComponentTransition(f"Cannot {action} a {current_status} component")
    return pack, component


def record_component_event(
    pack_id: str,
    plan_id: str,
    component_id: str,
    event: dict[str, Any],
    *,
    before_mutation: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Append an idempotent interaction and update component progress.

    ``before_mutation`` runs only after the authoritative locked transition
    check. It is the event-first boundary used by the HTTP route: a trusted
    canonical learner event is appended before this Pack projection changes,
    without exposing a stale preflight/check time-of-check race.
    """
    with _locked_packs() as packs:
        for pack in packs:
            if pack.get("pack_id") != pack_id:
                continue
            plan = next(
                (
                    item
                    for item in pack.get("component_plans", [])
                    if item.get("plan_id") == plan_id
                ),
                None,
            )
            if plan is None:
                return None
            component = next(
                (
                    item
                    for item in plan.get("components", [])
                    if item.get("component_id") == component_id
                ),
                None,
            )
            if component is None:
                return None
            progress = pack.setdefault("component_progress", {}).setdefault(plan_id, {"events": []})
            events = progress.setdefault("events", [])
            event_id = str(event.get("event_id") or "")
            duplicate = next(
                (item for item in events if event_id and item.get("event_id") == event_id), None
            )
            if duplicate is not None:
                if (
                    duplicate.get("plan_id") not in {None, plan_id}
                    or duplicate.get("component_id") not in {None, component_id}
                    or duplicate.get("_request_fingerprint")
                    not in {None, _request_fingerprint(event)}
                ):
                    raise InvalidComponentTransition(
                        "A component event ID cannot be reused with a different request"
                    )
                # This ephemeral marker is read by the HTTP route after the
                # atomic store call; it is not persisted with the event.
                if before_mutation is not None:
                    # An interrupted first attempt may have committed the
                    # canonical event but not this Pack projection. Replaying
                    # the same stable ID lets the canonical chain repair its
                    # derived projections without duplicating evidence.
                    before_mutation(pack, plan, component)
                event["_idempotent_replay"] = True
                return pack, component
            if pack.get("active_plan_id") != plan_id or plan.get("status") != "active":
                raise InvalidComponentTransition("Events can only update the active learning plan")
            payload = {
                **event,
                "plan_id": plan_id,
                "component_id": component_id,
                "_request_fingerprint": _request_fingerprint(event),
                "occurred_at": str(event.get("occurred_at") or _now()),
            }
            action = str(payload.get("action") or "")
            dependencies = set(component.get("dependencies") or [])
            completed = {
                str(item.get("component_id"))
                for item in plan.get("components", [])
                if item.get("status") in {"completed", "skipped"}
            }
            if action in {"start", "complete", "feedback"} and not dependencies.issubset(completed):
                raise InvalidComponentTransition(
                    "Complete prerequisite components before this step"
                )
            current_status = str(component.get("status") or "pending")
            allowed = (
                (action == "start" and current_status == "pending")
                or (action == "complete" and current_status in {"pending", "active", "degraded"})
                or (
                    action == "skip"
                    and not component.get("required", True)
                    and current_status in {"pending", "active", "degraded"}
                )
                or (action == "degrade" and current_status in {"pending", "active"})
                or (action == "retry" and current_status == "degraded")
                or (action == "feedback" and current_status in {"pending", "active"})
            )
            if not allowed:
                raise InvalidComponentTransition(f"Cannot {action} a {current_status} component")
            if before_mutation is not None:
                before_mutation(pack, plan, component)
            if action == "start":
                component["status"] = "active"
            elif action == "complete":
                component["status"] = "completed"
            elif action == "skip":
                component["status"] = "skipped"
            elif action == "degrade":
                component["status"] = "degraded"
            elif action == "retry":
                component["status"] = "active"
            elif action == "feedback":
                if current_status == "pending":
                    component["status"] = "active"
            events.append(payload)
            if payload.get("observation") in {"correct", "incorrect"}:
                attempted_concept = str(payload.get("concept_id") or "")
                for repair in pack.get("repairs") or []:
                    if not isinstance(repair, dict) or repair.get("status") != "deferred":
                        continue
                    same_component = str(repair.get("action_id") or "") == component_id
                    same_concept = (
                        bool(attempted_concept)
                        and str(repair.get("concept_id") or "") == attempted_concept
                    )
                    if same_component or same_concept:
                        continue
                    repair["status"] = "retrying"
                    repair["reopened_at"] = _now()
                    repair["suggested_next_component_id"] = None
            if payload.get("output_ref"):
                component["output_ref"] = str(payload["output_ref"])
            if payload.get("media_url"):
                component["media_url"] = str(payload["media_url"])
            timestamp = _now()
            progress[component_id] = {
                "status": component.get("status"),
                "last_action": action,
                "updated_at": timestamp,
                "output_ref": component.get("output_ref"),
            }
            progress["updated_at"] = timestamp
            plan["updated_at"] = timestamp
            required = [item for item in plan.get("components", []) if item.get("required", True)]
            if required and all(item.get("status") == "completed" for item in required):
                plan["status"] = "completed"
                # Completing the component sequence closes this learning
                # round; it is not evidence that the underlying goal/KCs are
                # mastered.  BKT/qualitative gates remain the only authority
                # for mastery and may still report insufficient evidence.
                #
                # Contract: ``round_status`` is the plan-level completion
                # signal (consumed by the frontend); ``goal.status`` is a
                # legacy field that only legacy data ever sets to "completed"
                # and is reopened by the next plan — it is deliberately NOT
                # written here, so a goal never claims mastery completion
                # from merely visiting every component.
                goal = pack.get("goal")
                if isinstance(goal, dict):
                    goal["round_status"] = "completed"
                    goal["round_completed_at"] = timestamp
            pack["updated_at"] = timestamp
            _save(packs)
            return pack, component
    return None
