"""Bridge optional loguru records into stdlib logging."""

from __future__ import annotations

import logging
from typing import Any


def install_loguru_bridge(level: int = logging.DEBUG) -> bool:
    """Forward loguru logs to stdlib if loguru is installed."""
    try:
        from loguru import logger as loguru_logger
    except Exception:
        return False

    def sink(message: Any) -> None:
        record = message.record
        std_logger = logging.getLogger(record["name"])
        std_logger.log(
            record["level"].no,
            record["message"],
            exc_info=record["exception"],
            extra={"loguru": True},
        )

    loguru_logger.remove()
    loguru_logger.add(sink, level=level, enqueue=False, backtrace=False, diagnose=False)
    return True
