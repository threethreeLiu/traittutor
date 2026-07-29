from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
from unittest import mock


def _load_cli_kit():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "_cli_kit.py"
    spec = importlib.util.spec_from_file_location("cli_kit_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_log_helpers_do_not_raise_on_legacy_windows_code_page() -> None:
    buffer = io.BytesIO()
    stdout = io.TextIOWrapper(buffer, encoding="cp936", errors="strict")

    with mock.patch("sys.stdout", stdout):
        cli_kit = _load_cli_kit()

        assert sys.stdout.errors == "replace"
        cli_kit.banner("TraitTutor", ["Backend http://localhost:8001"])
        cli_kit.log_success("TraitTutor started")
        cli_kit.log_error("TraitTutor failed")
        stdout.flush()

    assert buffer.getvalue()
