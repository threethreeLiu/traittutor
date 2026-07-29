import logging

import pytest

from traittutor.logging import bind_log_context, capture_process_logs
from traittutor.logging.loguru_bridge import install_loguru_bridge


def test_loguru_bridge_forwards_to_stdlib_process_capture():
    loguru = pytest.importorskip("loguru")
    events = []

    assert install_loguru_bridge(logging.DEBUG) is True
    with bind_log_context(task_id="loguru-task"):
        with capture_process_logs(events.append, task_id="loguru-task"):
            loguru.logger.info("hello from loguru")

    assert [event.message for event in events] == ["hello from loguru"]
    assert events[0].context["task_id"] == "loguru-task"
