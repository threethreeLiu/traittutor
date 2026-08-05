from __future__ import annotations

import pytest

from traittutor import learning_packs


def test_learning_pack_can_start_from_goal_without_uploaded_material(tmp_path, monkeypatch):
    from traittutor.services import path_service

    service = path_service.get_path_service()
    monkeypatch.setattr(service, "get_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)

    pack = learning_packs.create_pack(
        title="7 天个人理财入门",
        goal={"text": "我想用 7 天入门个人理财", "origin": "home_chat"},
        sources=[{"source_type": "user_goal", "title": "个人理财", "role": "learning_goal"}],
    )

    assert pack["material"] == {}
    assert pack["goal"]["text"] == "我想用 7 天入门个人理财"
    assert pack["goal"]["status"] == "active"
    assert pack["goal"]["goal_id"]
    assert pack["sources"] == [
        {"source_type": "user_goal", "title": "个人理财", "role": "learning_goal"}
    ]
    assert pack["artifacts"] == {"courseware": [], "flashcards": [], "quiz": []}


def test_goal_pack_can_add_material_without_replacing_goal(tmp_path, monkeypatch):
    from traittutor.services import path_service

    service = path_service.get_path_service()
    monkeypatch.setattr(service, "get_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)

    pack = learning_packs.create_pack(title="English", goal="I want to learn English")
    updated = learning_packs.update_pack(
        pack["pack_id"],
        {
            "material": {"source_type": "upload", "title": "lesson.pdf", "text": "Lesson one"},
            "source": {"source_type": "upload", "title": "lesson.pdf", "role": "material"},
        },
    )

    assert updated is not None
    assert updated["goal"]["text"] == "I want to learn English"
    assert updated["material"]["title"] == "lesson.pdf"
    assert updated["sources"][-1]["title"] == "lesson.pdf"


@pytest.mark.parametrize("payload", ['{}', '[{"pack_id": "valid"}, "invalid"]'])
def test_learning_pack_store_rejects_invalid_root_without_hiding_data(tmp_path, monkeypatch, payload):
    path = tmp_path / "learning-packs.json"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(learning_packs, "_path", lambda: path)

    with pytest.raises(learning_packs.LearningPackStoreError, match="invalid format"):
        learning_packs.list_packs()


def test_component_plan_events_are_idempotent_and_do_not_change_artifacts(tmp_path, monkeypatch):
    from traittutor.services import path_service

    service = path_service.get_path_service()
    monkeypatch.setattr(service, "get_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Math", goal="Learn slope")
    plan = {
        "plan_id": "plan-1",
        "status": "active",
        "components": [
            {
                "component_id": "cmp-1",
                "component_type": "diagnostic_check",
                "status": "pending",
                "required": True,
            }
        ],
    }

    assert learning_packs.create_component_plan(pack["pack_id"], plan) == plan
    event = {"event_id": "evt-1", "action": "complete", "observation": "incorrect"}
    assert learning_packs.record_component_event(pack["pack_id"], "plan-1", "cmp-1", event)
    assert learning_packs.record_component_event(pack["pack_id"], "plan-1", "cmp-1", event)

    updated = learning_packs.get_pack(pack["pack_id"])
    assert updated is not None
    assert updated["component_plans"][0]["components"][0]["status"] == "completed"
    assert updated["component_plans"][0]["status"] == "completed"
    assert len(updated["component_progress"]["plan-1"]["events"]) == 1
    assert updated["artifacts"] == {"courseware": [], "flashcards": [], "quiz": []}


def test_component_events_require_active_plan_and_completed_dependencies(tmp_path, monkeypatch):
    from traittutor.services import path_service

    service = path_service.get_path_service()
    monkeypatch.setattr(service, "get_workspace_dir", lambda: tmp_path / "workspace")
    monkeypatch.setattr(learning_packs, "get_path_service", lambda: service)
    pack = learning_packs.create_pack(title="Math", goal="Learn slope")
    plan = {
        "plan_id": "plan-guarded",
        "status": "active",
        "components": [
            {"component_id": "first", "status": "pending", "required": True},
            {"component_id": "second", "status": "pending", "required": True, "dependencies": ["first"]},
        ],
    }
    learning_packs.create_component_plan(pack["pack_id"], plan)

    with pytest.raises(learning_packs.InvalidComponentTransition, match="prerequisite"):
        learning_packs.record_component_event(pack["pack_id"], "plan-guarded", "second", {"action": "complete"})

    learning_packs.record_component_event(pack["pack_id"], "plan-guarded", "first", {"action": "complete"})
    learning_packs.record_component_event(pack["pack_id"], "plan-guarded", "second", {"action": "complete"})
    replacement = {"plan_id": "plan-2", "status": "active", "components": [{"component_id": "next", "status": "pending"}]}
    learning_packs.create_component_plan(pack["pack_id"], replacement)

    with pytest.raises(learning_packs.InvalidComponentTransition, match="active learning plan"):
        learning_packs.record_component_event(pack["pack_id"], "plan-guarded", "first", {"action": "retry"})
