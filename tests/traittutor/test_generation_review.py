from dataclasses import replace

from traittutor.api.routers.traittutor_generate import (
    QuizAnswerRequest,
    _learner_safe_task_result,
    grade_generation_quiz_answer,
)
from traittutor.generate.service import GenerationRequest, GenerationResult, MaterialSource
from traittutor.generate.tasks import GenerationTask, GenerationTaskManager
from traittutor.multi_user.context import reset_current_user, set_current_user
from traittutor.multi_user.models import CurrentUser
from traittutor.multi_user.paths import scope_for_user


def _review_task() -> GenerationTask:
    request = GenerationRequest(
        generation_type="quiz",
        material=MaterialSource(source_type="paste", text="A small source", title="Source"),
    )
    result = GenerationResult(
        generation_id="review-task",
        generation_type="quiz",
        status="needs_review",
        events=[],
        result={"kind": "quiz", "title": "Quiz", "save_target": "question_bank"},
        created_at="2026-01-01T00:00:00+00:00",
        prompt_asset="quiz.md",
        material={},
        learner_profile={},
    )
    return GenerationTask(
        generation_id="review-task",
        owner_id="reviewer",
        owner_username="reviewer",
        owner_role="user",
        request=request,
        status="needs_review",
        result=result,
    )


def _reviewer():
    return CurrentUser(
        id="reviewer",
        username="reviewer",
        role="user",
        scope=scope_for_user("reviewer", is_admin=False),
    )


def test_confirm_review_changes_artifact_to_completed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "traittutor.generate.tasks.save_generation", lambda result: tmp_path / result.generation_id
    )
    manager = GenerationTaskManager(storage_root=tmp_path)
    task = _review_task()
    manager._persist(task)
    token = set_current_user(_reviewer())
    try:
        confirmed = manager.confirm_review(task.generation_id)
    finally:
        reset_current_user(token)
    assert confirmed is not None
    assert confirmed.status == "completed"
    assert confirmed.result is not None
    assert confirmed.result.status == "completed"
    assert confirmed.events[-1]["type"] == "review_confirmed"


def test_discard_review_keeps_result_ineligible(tmp_path):
    manager = GenerationTaskManager(storage_root=tmp_path)
    task = _review_task()
    manager._persist(task)
    token = set_current_user(_reviewer())
    try:
        discarded = manager.discard_review(task.generation_id)
    finally:
        reset_current_user(token)
    assert discarded is not None
    assert discarded.status == "discarded"
    assert discarded.result is not None
    assert discarded.result.status == "needs_review"
    assert discarded.events[-1]["type"] == "review_discarded"


def test_retry_review_hides_old_result_but_keeps_audit_evidence(monkeypatch, tmp_path):
    """Polling a regenerated task must not return the previous review result."""
    manager = GenerationTaskManager(storage_root=tmp_path)
    monkeypatch.setattr(manager, "_schedule", lambda: None)
    task = _review_task()
    manager._persist(task)
    token = set_current_user(_reviewer())
    try:
        retried = manager.retry(task.generation_id)
        persisted = manager.get(task.generation_id)
    finally:
        reset_current_user(token)

    assert retried is not None
    assert retried.status == "queued"
    assert retried.result is None
    assert retried.review_history[0]["reason"] == "regenerate_requested"
    assert retried.review_history[0]["result"]["status"] == "needs_review"
    assert persisted is not None
    assert persisted.status == "queued"
    assert persisted.result is None


def test_admin_confirmation_saves_in_task_owner_workspace(monkeypatch, tmp_path):
    manager = GenerationTaskManager(storage_root=tmp_path)
    task = _review_task()
    manager._persist(task)
    captured = {}

    def capture_save(_result):
        from traittutor.multi_user.context import get_current_user

        captured["owner"] = get_current_user().id
        return tmp_path / "saved"

    monkeypatch.setattr("traittutor.generate.tasks.save_generation", capture_save)
    admin = CurrentUser("admin", "admin", "admin", scope_for_user("admin", is_admin=True))
    token = set_current_user(admin)
    try:
        confirmed = manager.confirm_review(task.generation_id)
    finally:
        reset_current_user(token)
    assert captured["owner"] == "reviewer"
    assert confirmed.events[-1]["data"]["actor_id"] == "admin"
    assert confirmed.events[-1]["data"]["artifact_owner_id"] == "reviewer"


def test_learner_task_view_hides_quiz_answer_keys():
    task = _review_task()
    task.result.result["items"] = [
        {
            "question_id": "q",
            "correct_answer": "B",
            "is_correct": True,
            "explanation": "B is correct",
            "options": [{"key": "A", "is_correct": False}, {"key": "B", "is_correct": True}],
        }
    ]
    safe = _learner_safe_task_result(task.result)
    item = safe["result"]["items"][0]
    assert "correct_answer" not in item and "is_correct" not in item
    assert "explanation" not in item
    assert all("is_correct" not in option for option in item["options"])


def test_standalone_quiz_grades_server_side_without_returning_answer_key(monkeypatch):
    task = _review_task()
    task.status = "completed"
    task.result = replace(task.result, status="completed")
    task.result.result["items"] = [
        {
            "question_id": "q",
            "question_type": "choice",
            "correct_answer": "B",
            "explanation": "Because B.",
        }
    ]
    monkeypatch.setattr(
        "traittutor.api.routers.traittutor_generate.get_generation_task_manager",
        lambda: type("Manager", (), {"get": lambda _self, _id: task})(),
    )
    graded = __import__("asyncio").run(
        grade_generation_quiz_answer("review-task", QuizAnswerRequest(question_id="q", answer="B"))
    )
    assert graded["question_id"] == "q"
    assert graded["correct"] is True
    assert graded["explanation"] == "Because B."
    assert graded["attempt_id"].startswith("attempt_")
