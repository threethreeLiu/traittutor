import logging

from traittutor.logging import ProcessLogEvent, bind_log_context, capture_process_logs


def test_capture_process_logs_emits_structured_event_for_matching_task():
    events: list[ProcessLogEvent] = []
    logger = logging.getLogger("traittutor.tests.process")

    with bind_log_context(task_id="task-1", capability="knowledge", stage="indexing"):
        with capture_process_logs(events.append, task_id="task-1"):
            logger.info("Embedding batches: %s/%s", 2, 8)

    assert len(events) == 1
    event = events[0].to_dict()
    assert event["type"] == "process_log"
    assert event["level"] == "INFO"
    assert event["message"] == "Embedding batches: 2/8"
    assert event["logger"] == "traittutor.tests.process"
    assert event["context"] == {
        "task_id": "task-1",
        "capability": "knowledge",
        "stage": "indexing",
    }


def test_capture_process_logs_filters_other_tasks():
    events: list[ProcessLogEvent] = []
    logger = logging.getLogger("traittutor.tests.process")

    with capture_process_logs(events.append, task_id="task-1"):
        with bind_log_context(task_id="task-2"):
            logger.warning("wrong task")

    assert events == []
