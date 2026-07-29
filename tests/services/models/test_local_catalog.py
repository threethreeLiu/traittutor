"""Tests for the code-defined LLM catalog loader and its ModelCatalogService overlay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from traittutor.services.config.model_catalog import ModelCatalogService
from traittutor.services.models.local_catalog import (
    _resolve_secret,
    load_local_llm,
)


def _point_at(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Redirect ``local_models_path()`` used inside ``load_local_llm`` to *path*."""
    monkeypatch.setattr(
        "traittutor.services.models.local_catalog.local_models_path",
        lambda: path,
    )


# --- _resolve_secret --------------------------------------------------------


def test_resolve_secret_env_var_and_literal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MY_VAR", "secret-from-env")

    assert _resolve_secret("env(MY_VAR)") == "secret-from-env"
    # whitespace inside env(...) is tolerated by the regex
    assert _resolve_secret("env( MY_VAR )") == "secret-from-env"
    # literal keys pass straight through
    assert _resolve_secret("sk-literal") == "sk-literal"
    assert _resolve_secret(None) == ""
    # an unknown env var resolves to empty string, not the literal text
    monkeypatch.delenv("MISSING_VAR", raising=False)
    assert _resolve_secret("env(MISSING_VAR)") == ""


def test_load_local_llm_resolves_env_and_passes_literal_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("MY_VAR", "env-value")
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "models:\n"
        "  - id: a\n"
        "    model: model-a\n"
        "    api_key: env(MY_VAR)\n"
        "  - id: b\n"
        "    model: model-b\n"
        "    api_key: sk-literal\n",
        encoding="utf-8",
    )
    _point_at(monkeypatch, yaml_path)

    result = load_local_llm()
    assert result is not None
    keys = {p["id"]: p["api_key"] for p in result["profiles"]}
    assert keys["a"] == "env-value"
    assert keys["b"] == "sk-literal"


# --- one-profile-per-entry shape -------------------------------------------


def test_entry_to_profile_shape_and_binding_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "models:\n"
        "  - id: zhipu-glm\n"
        "    name: Zhipu GLM\n"
        "    binding: custom_anthropic\n"
        "    base_url: https://open.bigmodel.cn/api/anthropic\n"
        "    model: glm-5.2\n"
        "    extra_headers:\n"
        "      X-Custom: 'yes'\n"
        "  - id: plain\n"
        "    model: m-plain\n"
        "    base_url: https://x.example\n",
        encoding="utf-8",
    )
    _point_at(monkeypatch, yaml_path)

    result = load_local_llm()
    assert result is not None
    by_id = {p["id"]: p for p in result["profiles"]}

    zhipu = by_id["zhipu-glm"]
    assert zhipu["name"] == "Zhipu GLM"
    assert zhipu["binding"] == "custom_anthropic"
    assert zhipu["base_url"] == "https://open.bigmodel.cn/api/anthropic"
    assert zhipu["api_version"] == ""
    assert zhipu["extra_headers"] == {"X-Custom": "yes"}
    # one model per entry; model id mirrors the entry id
    assert len(zhipu["models"]) == 1
    assert zhipu["models"][0]["model"] == "glm-5.2"
    assert zhipu["models"][0]["id"] == "zhipu-glm"

    # binding omitted -> default "custom"
    plain = by_id["plain"]
    assert plain["binding"] == "custom"
    assert plain["models"][0]["model"] == "m-plain"


def test_name_falls_back_to_id_when_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "models:\n  - id: no-name\n    model: m\n", encoding="utf-8"
    )
    _point_at(monkeypatch, yaml_path)

    result = load_local_llm()
    assert result is not None
    profile = result["profiles"][0]
    assert profile["id"] == "no-name"
    assert profile["name"] == "no-name"
    # model name falls back to the model value when no name is given
    assert profile["models"][0]["name"] == "m"


# --- active id resolution ---------------------------------------------------


def test_active_honored_when_it_matches_an_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "active: b\n"
        "models:\n"
        "  - id: a\n    model: ma\n"
        "  - id: b\n    model: mb\n",
        encoding="utf-8",
    )
    _point_at(monkeypatch, yaml_path)

    result = load_local_llm()
    assert result is not None
    assert result["active_profile_id"] == "b"
    assert result["active_model_id"] == "b"


def test_active_defaults_to_first_when_omitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "models:\n"
        "  - id: first\n    model: m1\n"
        "  - id: second\n    model: m2\n",
        encoding="utf-8",
    )
    _point_at(monkeypatch, yaml_path)

    result = load_local_llm()
    assert result is not None
    assert result["active_profile_id"] == "first"
    assert result["active_model_id"] == "first"


def test_active_defaults_to_first_when_bogus(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "active: does-not-exist\n"
        "models:\n"
        "  - id: first\n    model: m1\n",
        encoding="utf-8",
    )
    _point_at(monkeypatch, yaml_path)

    result = load_local_llm()
    assert result is not None
    assert result["active_profile_id"] == "first"


# --- None cases ------------------------------------------------------------


def test_missing_file_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _point_at(monkeypatch, tmp_path / "absent.yaml")
    assert load_local_llm() is None


def test_empty_models_list_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text("models: []\n", encoding="utf-8")
    _point_at(monkeypatch, yaml_path)
    assert load_local_llm() is None


def test_top_level_not_dict_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    _point_at(monkeypatch, yaml_path)
    assert load_local_llm() is None


def test_entry_missing_model_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "models:\n"
        "  - id: no-model\n"  # missing model -> skipped
        "  - id: survivor\n    model: m\n",
        encoding="utf-8",
    )
    _point_at(monkeypatch, yaml_path)

    result = load_local_llm()
    assert result is not None
    assert [p["id"] for p in result["profiles"]] == ["survivor"]


def test_all_entries_skipped_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "models:\n"
        "  - id: a\n"  # missing model
        "  - name: no-id\n    model: m\n",  # missing id
        encoding="utf-8",
    )
    _point_at(monkeypatch, yaml_path)
    assert load_local_llm() is None


def test_invalid_yaml_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text("model: [unterminated\n", encoding="utf-8")
    _point_at(monkeypatch, yaml_path)
    assert load_local_llm() is None


# --- ModelCatalogService integration (the key overlay + strip test) --------


def test_model_catalog_service_overlays_local_llm_and_strips_on_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    yaml_path = tmp_path / "models.local.yaml"
    yaml_path.write_text(
        "active: alpha\n"
        "models:\n"
        "  - id: alpha\n"
        "    name: Alpha\n"
        "    binding: custom_anthropic\n"
        "    base_url: https://a.example\n"
        "    api_key: env(ALPHA_KEY)\n"
        "    model: model-alpha\n"
        "  - id: beta\n"
        "    name: Beta\n"
        "    binding: custom\n"
        "    base_url: https://b.example\n"
        "    api_key: sk-beta-literal\n"
        "    model: model-beta\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALPHA_KEY", "alpha-secret")
    _point_at(monkeypatch, yaml_path)

    catalog_path = tmp_path / "catalog.json"
    catalog = ModelCatalogService(path=catalog_path).load()

    # Overlay applied to the returned (in-memory) catalog.
    llm = catalog["services"]["llm"]
    assert [p["id"] for p in llm["profiles"]] == ["alpha", "beta"]
    assert llm["active_profile_id"] == "alpha"
    assert llm["active_model_id"] == "alpha"
    alpha = next(p for p in llm["profiles"] if p["id"] == "alpha")
    assert alpha["api_key"] == "alpha-secret"  # env() resolved at overlay time
    beta = next(p for p in llm["profiles"] if p["id"] == "beta")
    assert beta["api_key"] == "sk-beta-literal"

    # Non-llm services are untouched by the overlay.
    assert "embedding" in catalog["services"]
    assert "search" in catalog["services"]
    assert catalog["services"]["embedding"]["profiles"] == []

    # Persisted JSON on disk must NOT contain the overlaid keys/profiles.
    saved = json.loads(catalog_path.read_text(encoding="utf-8"))
    saved_llm = saved["services"]["llm"]
    assert saved_llm["active_profile_id"] is None
    assert saved_llm["active_model_id"] is None
    assert saved_llm["profiles"] == []
    # ...and other services did persist normally.
    assert "embedding" in saved["services"]
    assert "search" in saved["services"]


def test_model_catalog_service_unchanged_when_local_yaml_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # No models.local.yaml at the redirected path -> overlay is a no-op.
    _point_at(monkeypatch, tmp_path / "absent.yaml")

    catalog_path = tmp_path / "catalog.json"
    catalog = ModelCatalogService(path=catalog_path).load()

    assert catalog["services"]["llm"]["profiles"] == []
    assert catalog["services"]["llm"]["active_profile_id"] is None
