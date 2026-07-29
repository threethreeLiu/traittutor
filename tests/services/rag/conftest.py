"""Local conftest for RAG integration tests.

The ``--pipeline`` option must be registered from a conftest (or a real
plugin), not from a test module — otherwise pytest raises
``ValueError: pytest_addoption is only supported from a plugin or conftest``.
"""

from __future__ import annotations


def pytest_addoption(parser):
    """Register the ``--pipeline`` CLI option used by the integration tests."""
    parser.addoption(
        "--pipeline",
        action="store",
        default="llamaindex",
        help="Pipeline to test (e.g. llamaindex, all)",
    )
