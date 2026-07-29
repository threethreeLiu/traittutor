"""Tests for the CC Switch provider -> ModelRecord mapper."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from traittutor.services.models.cc_switch import (
    ModelRecord,
    iter_model_records,
    map_provider,
    read_providers,
)


# --- claude mapping ---------------------------------------------------------

def test_claude_mapping_strips_context_suffix_and_populates_fields():
    settings = {
        "model": "deepseek-v4-pro[1M]",
        "env": {
            "ANTHROPIC_AUTH_TOKEN": "sk-deepseek",
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "ANTHROPIC_MODEL": "deepseek-v4-pro[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "deepseek-v4-pro",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
        },
    }

    record = map_provider("claude", "DeepSeek", settings)

    assert record is not None
    assert isinstance(record, ModelRecord)
    assert record.binding == "custom_anthropic"
    assert record.base_url == "https://api.deepseek.com/anthropic"
    assert record.api_key == "sk-deepseek"
    # top-level model wins and the [1M] suffix is stripped
    assert record.model == "deepseek-v4-pro"
    assert record.id == "deepseek"
    assert record.name == "DeepSeek"
    assert record.extra_headers == {}
    assert record.api_version == ""


def test_claude_resolves_model_from_env_when_no_top_level_model():
    # Zhipu-style: no top-level model, only env.ANTHROPIC_MODEL.
    settings = {
        "env": {
            "ANTHROPIC_AUTH_TOKEN": "sk-zhipu",
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_MODEL": "glm-5.2",
        }
    }

    record = map_provider("claude", "Zhipu GLM", settings)

    assert record is not None
    assert record.binding == "custom_anthropic"
    assert record.base_url == "https://open.bigmodel.cn/api/anthropic"
    assert record.api_key == "sk-zhipu"
    assert record.model == "glm-5.2"
    assert record.id == "zhipu-glm"


def test_claude_accepts_anthropic_api_key_alias():
    settings = {
        "env": {
            "ANTHROPIC_API_KEY": "sk-apikey-alias",
            "ANTHROPIC_BASE_URL": "https://example.com/anthropic",
            "ANTHROPIC_MODEL": "claude-test",
        }
    }

    record = map_provider("claude", "Alias", settings)

    assert record is not None
    assert record.api_key == "sk-apikey-alias"


def test_claude_without_key_or_base_url_returns_none():
    assert map_provider("claude", "Empty", {"env": {}}) is None
    assert (
        map_provider(
            "claude",
            "OnlyKey",
            {"env": {"ANTHROPIC_AUTH_TOKEN": "sk", "ANTHROPIC_BASE_URL": ""}},
        )
        is None
    )
    assert (
        map_provider(
            "claude",
            "OnlyUrl",
            {"env": {"ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_BASE_URL": "https://x"}},
        )
        is None
    )


# --- codex mapping ----------------------------------------------------------

_CODEX_CONFIG = (
    'model_provider = "custom"\n'
    'model = "MiniMax-M3"\n'
    "disable_response_storage = true\n"
    "\n"
    "[model_providers]\n"
    "[model_providers.custom]\n"
    'name = "minimax"\n'
    'base_url = "https://api.minimaxi.com/v1"\n'
    'wire_api = "responses"\n'
    "requires_openai_auth = true\n"
)


def test_codex_mapping_parses_toml_and_auth():
    settings = {"auth": {"OPENAI_API_KEY": "sk-minimax"}, "config": _CODEX_CONFIG}

    record = map_provider("codex", "MiniMax", settings)

    assert record is not None
    assert record.binding == "custom"
    assert record.api_key == "sk-minimax"
    assert record.base_url == "https://api.minimaxi.com/v1"
    assert record.model == "MiniMax-M3"
    assert record.id == "minimax"


def test_codex_without_auth_key_returns_none():
    assert (
        map_provider("codex", "NoKey", {"auth": {}, "config": _CODEX_CONFIG}) is None
    )


def test_codex_without_base_url_returns_none():
    # No model_providers -> no base_url -> None even with a key.
    settings = {"auth": {"OPENAI_API_KEY": "sk"}, "config": 'model = "m"\n'}
    assert map_provider("codex", "NoUrl", settings) is None


def test_codex_invalid_toml_returns_none():
    settings = {"auth": {"OPENAI_API_KEY": "sk"}, "config": "not = valid = toml"}
    assert map_provider("codex", "BadToml", settings) is None


# --- gemini / unknown -------------------------------------------------------

def test_gemini_maps_to_none():
    # Gemini is OAuth-only; no static key.
    assert map_provider("gemini", "Gemini", {"env": {}}) is None


def test_unknown_app_type_maps_to_none():
    assert map_provider("hermes", "Hermes", {"env": {"KEY": "x"}}) is None


# --- sqlite reader + iter_model_records -------------------------------------

def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE providers (
                id TEXT,
                app_type TEXT,
                name TEXT,
                settings_config TEXT,
                is_current BOOLEAN,
                category TEXT,
                provider_type TEXT,
                sort_index INTEGER
            )
            """
        )
        rows = [
            (
                "550e8400-e29b-41d4-a716-446655440000",
                "claude",
                "Zhipu GLM",
                json.dumps(
                    {
                        "env": {
                            "ANTHROPIC_AUTH_TOKEN": "sk-zhipu",
                            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                            "ANTHROPIC_MODEL": "glm-5.2",
                        }
                    }
                ),
                1,
                "anthropic",
                "api",
                0,
            ),
            (
                "minimax",
                "codex",
                "MiniMax",
                json.dumps({"auth": {"OPENAI_API_KEY": "sk-minimax"}, "config": _CODEX_CONFIG}),
                0,
                "openai",
                "api",
                1,
            ),
            (
                "gem-oauth",
                "gemini",
                "Gemini OAuth",
                json.dumps({"oauth": {"refresh_token": "rt"}}),
                0,
                "google",
                "oauth",
                2,
            ),
        ]
        conn.executemany(
            "INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        conn.commit()
    finally:
        conn.close()


def test_read_providers_parses_rows(tmp_path: Path):
    db = tmp_path / "cc-switch.db"
    _make_db(db)

    rows = read_providers(db)

    assert len(rows) == 3
    by_app = {r["app_type"]: r for r in rows}

    zhipu = by_app["claude"]
    assert zhipu["id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert zhipu["name"] == "Zhipu GLM"
    assert isinstance(zhipu["settings_config"], dict)
    assert zhipu["settings_config"]["env"]["ANTHROPIC_MODEL"] == "glm-5.2"
    assert zhipu["is_current"] is True

    minimax = by_app["codex"]
    assert minimax["settings_config"]["auth"]["OPENAI_API_KEY"] == "sk-minimax"
    assert minimax["is_current"] is False


def test_read_providers_handles_bad_json(tmp_path: Path):
    db = tmp_path / "cc-switch.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, "
        "settings_config TEXT, is_current BOOLEAN)"
    )
    conn.execute(
        "INSERT INTO providers VALUES ('x', 'claude', 'X', 'not-json', 0)"
    )
    conn.commit()
    conn.close()

    rows = read_providers(db)
    assert len(rows) == 1
    assert rows[0]["settings_config"] == {}


def test_read_providers_missing_table_returns_empty(tmp_path: Path):
    db = tmp_path / "absent.db"  # file is created by connect, but no table
    assert read_providers(db) == []


def test_iter_model_records_maps_and_resolves_ids(tmp_path: Path):
    db = tmp_path / "cc-switch.db"
    _make_db(db)

    pairs = iter_model_records(db)

    # gemini is skipped, so only claude + codex remain.
    assert len(pairs) == 2
    by_id = {record.id: (record, is_current) for record, is_current in pairs}

    # claude DB id is a UUID -> falls back to slugified name.
    zhipu, zhipu_current = by_id["zhipu-glm"]
    assert zhipu.binding == "custom_anthropic"
    assert zhipu.model == "glm-5.2"
    assert zhipu_current is True

    # codex DB id is a clean slug -> kept as-is.
    minimax, minimax_current = by_id["minimax"]
    assert minimax.binding == "custom"
    assert minimax.base_url == "https://api.minimaxi.com/v1"
    assert minimax_current is False

    # gemini must not appear.
    assert "gem-oauth" not in by_id


def test_codex_model_table_does_not_crash():
    # A codex config whose top-level ``model`` is a TOML [model] table (parsed
    # as a dict) must not raise; it maps with an empty model rather than
    # aborting the whole import.
    settings = {
        "auth": {"OPENAI_API_KEY": "sk-x"},
        "config": (
            'model_provider = "p"\n'
            "[model]\n"
            'name = "gpt-5"\n'
            "[model_providers.p]\n"
            'base_url = "https://api.example.com/v1"\n'
        ),
    }
    record = map_provider("codex", "Weird", settings)
    assert record is not None
    assert record.binding == "custom"
    assert record.base_url == "https://api.example.com/v1"
    assert record.model == ""
