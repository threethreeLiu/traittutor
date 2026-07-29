"""Tests for `traittutor models sync-cc-switch` and the build_models_yaml helper.

A self-contained SQLite fixture mirrors the CC Switch ``providers`` table; no
real ``~/.cc-switch/cc-switch.db`` is required.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import typer
from typer.testing import CliRunner


def _build_db(db_path: Path) -> None:
    """Build a fixture CC Switch DB with claude + codex + (skipped) gemini rows."""
    claude_cfg = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://a.example/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "sk-claude-1234567890",
            "ANTHROPIC_MODEL": "claude-x[1M]",
        }
    }
    # NOTE: ``model`` is a top-level string key, not a ``[model]`` table — the
    # verified _map_codex reads ``parsed.get("model")`` and expects a string.
    codex_toml = (
        'model = "gpt-5"\n'
        '[model_providers.p]\n'
        'name = "p"\n'
        'base_url = "https://b.example/v1"\n'
    )
    codex_cfg = {
        "auth": {"OPENAI_API_KEY": "sk-codex-abcdef123456"},
        "config": codex_toml,
    }
    gemini_cfg = {"auth": {}}  # OAuth-only, no usable key

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE providers "
        "(id TEXT, app_type TEXT, name TEXT, settings_config TEXT, is_current INTEGER)"
    )
    rows = [
        ("claude-a", "claude", "Claude A", json.dumps(claude_cfg), 1),
        ("codex-b", "codex", "Codex B", json.dumps(codex_cfg), 0),
        ("gemini-x", "gemini", "Gemini OAuth", json.dumps(gemini_cfg), 0),
    ]
    conn.executemany(
        "INSERT INTO providers (id, app_type, name, settings_config, is_current) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def _new_app() -> typer.Typer:
    """Fresh Typer app with the models commands registered (no root-app side effects)."""
    from traittutor_cli.models_cmd import register

    app = typer.Typer()
    register(app)
    return app


def test_build_models_yaml_maps_and_skips(tmp_path: Path) -> None:
    from traittutor.services.models.cc_switch import iter_model_records
    from traittutor_cli.models_cmd import build_models_yaml

    db = tmp_path / "cc-switch.db"
    _build_db(db)

    records = iter_model_records(db)
    data, active_id = build_models_yaml(records)

    models = data["models"]
    assert len(models) == 2
    by_id = {m["id"]: m for m in models}

    # gemini (OAuth-only) is absent.
    assert "gemini-x" not in by_id
    assert {m["id"] for m in models} == {"claude-a", "codex-b"}

    claude = by_id["claude-a"]
    assert claude["binding"] == "custom_anthropic"
    assert claude["model"] == "claude-x"  # [1M] suffix stripped
    assert claude["api_key"] == "sk-claude-1234567890"  # literal, not env(...)
    assert claude["base_url"] == "https://a.example/anthropic"

    codex = by_id["codex-b"]
    assert codex["binding"] == "custom"
    assert codex["model"] == "gpt-5"
    assert codex["api_key"] == "sk-codex-abcdef123456"

    # active follows the is_current row (claude).
    assert active_id == "claude-a"
    assert data["active"] == "claude-a"


def test_build_models_yaml_preserves_manual_entries(tmp_path: Path) -> None:
    from traittutor.services.models.cc_switch import iter_model_records
    from traittutor_cli.models_cmd import build_models_yaml

    db = tmp_path / "cc-switch.db"
    _build_db(db)

    existing = {
        "active": "manual-1",
        "models": [
            {
                "id": "manual-1",
                "name": "Manual",
                "binding": "custom",
                "base_url": "u",
                "api_key": "k",
                "model": "m",
            }
        ],
    }
    data, active_id = build_models_yaml(iter_model_records(db), existing=existing)

    ids = {m["id"] for m in data["models"]}
    assert {"claude-a", "codex-b", "manual-1"} == ids
    # The manual entry is preserved verbatim.
    manual = next(m for m in data["models"] if m["id"] == "manual-1")
    assert manual["name"] == "Manual"

    # active falls back to the is_current synced record.
    assert active_id == "claude-a"


def test_sync_then_reload_round_trip(tmp_path: Path, monkeypatch) -> None:
    from traittutor.services.models.local_catalog import load_local_llm

    db = tmp_path / "cc-switch.db"
    _build_db(db)
    out_path = tmp_path / "models.local.yaml"

    app = _new_app()
    runner = CliRunner()
    result = runner.invoke(
        app, ["sync-cc-switch", "--db", str(db), "--out", str(out_path)]
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists()

    # Point the loader at the file we just wrote, then reload.
    monkeypatch.setattr(
        "traittutor.services.models.local_catalog.local_models_path",
        lambda: out_path,
    )
    loaded = load_local_llm()
    assert loaded is not None
    profiles = loaded["profiles"]
    assert len(profiles) == 2
    assert {p["id"] for p in profiles} == {"claude-a", "codex-b"}
    assert loaded["active_profile_id"] == "claude-a"
    # Keys survive as literals (resolved by the loader).
    claude = next(p for p in profiles if p["id"] == "claude-a")
    assert claude["api_key"] == "sk-claude-1234567890"


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    db = tmp_path / "cc-switch.db"
    _build_db(db)
    out_path = tmp_path / "models.local.yaml"

    app = _new_app()
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["sync-cc-switch", "--db", str(db), "--out", str(out_path), "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert out_path.exists() is False
    assert "dry-run" in result.output.lower()


def test_missing_db_exits_nonzero(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.db"
    app = _new_app()
    runner = CliRunner()
    result = runner.invoke(app, ["sync-cc-switch", "--db", str(missing)])
    assert result.exit_code != 0
