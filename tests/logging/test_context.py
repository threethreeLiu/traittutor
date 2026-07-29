from traittutor.logging import bind_log_context, current_log_context


def test_bind_log_context_is_scoped_and_nested():
    assert current_log_context() == {}

    with bind_log_context(request_id="req-1", task_id="task-1"):
        assert current_log_context() == {"request_id": "req-1", "task_id": "task-1"}
        with bind_log_context(stage="indexing", task_id="task-2"):
            assert current_log_context() == {
                "request_id": "req-1",
                "task_id": "task-2",
                "stage": "indexing",
            }
        assert current_log_context() == {"request_id": "req-1", "task_id": "task-1"}

    assert current_log_context() == {}
