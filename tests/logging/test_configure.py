import importlib
import json
import logging
from pathlib import Path

import pytest

from traittutor.logging import LoggingConfig, bind_log_context


@pytest.fixture(autouse=True)
def _clean_logging_handlers():
    configure_module = importlib.import_module("traittutor.logging.configure")
    configure_module._remove_managed_handlers(logging.getLogger())
    yield
    configure_module._remove_managed_handlers(logging.getLogger())


def _flush_root_handlers() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def test_configure_logging_writes_jsonl_and_respects_level(monkeypatch, tmp_path: Path):
    configure_module = importlib.import_module("traittutor.logging.configure")
    monkeypatch.setattr(
        configure_module,
        "load_logging_config",
        lambda: LoggingConfig(
            level="WARNING",
            console_output=False,
            file_output=True,
            log_dir=str(tmp_path),
            max_bytes=1024 * 1024,
            backup_count=1,
        ),
    )

    configure_module.configure_logging(force=True)
    logger = logging.getLogger("traittutor.tests.config")
    with bind_log_context(request_id="req-1", task_id="task-1"):
        logger.info("filtered")
        logger.warning("written")
    _flush_root_handlers()

    lines = (tmp_path / "traittutor.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "traittutor.tests.config"
    assert entry["message"] == "written"
    assert entry["context"] == {"request_id": "req-1", "task_id": "task-1"}


def test_configure_logging_uses_rotation_settings(monkeypatch, tmp_path: Path):
    configure_module = importlib.import_module("traittutor.logging.configure")
    monkeypatch.setattr(
        configure_module,
        "load_logging_config",
        lambda: LoggingConfig(
            level="INFO",
            console_output=False,
            file_output=True,
            log_dir=str(tmp_path),
            max_bytes=220,
            backup_count=1,
        ),
    )

    configure_module.configure_logging(force=True)
    logger = logging.getLogger("traittutor.tests.rotation")
    for index in range(20):
        logger.info("rotation line %02d %s", index, "x" * 40)
    _flush_root_handlers()

    assert (tmp_path / "traittutor.jsonl").exists()
    assert (tmp_path / "traittutor.jsonl.1").exists()
