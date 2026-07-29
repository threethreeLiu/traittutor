"""Tests for the code-defined LLM model access layer.

Every user now selects from the full code-defined model list; per-user LLM
grants no longer filter the catalog. These tests isolate ``model_access`` from
the (separately-built) YAML overlay by monkeypatching ``admin_catalog`` to a
fixture and stubbing ``get_current_user``.
"""

import pytest

from traittutor.multi_user import model_access


class _StubUser:
    def __init__(self, *, is_admin=False, uid="u1"):
        self.is_admin = is_admin
        self.id = uid


def _catalog():
    return {
        "services": {
            "llm": {
                "active_profile_id": "p1",
                "active_model_id": "m1",
                "profiles": [
                    {
                        "id": "p1",
                        "name": "Prof1",
                        "binding": "custom_anthropic",
                        "base_url": "u1",
                        "api_key": "k1",
                        "models": [{"id": "m1", "name": "M1", "model": "glm-5.2"}],
                    },
                    {
                        "id": "p2",
                        "name": "Prof2",
                        "binding": "custom",
                        "base_url": "u2",
                        "api_key": "k2",
                        "models": [{"id": "m2", "name": "M2", "model": "gpt-4o"}],
                    },
                ],
            }
        }
    }


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(model_access, "admin_catalog", lambda: _catalog())
    monkeypatch.setattr(model_access, "get_current_user", lambda: _StubUser(is_admin=False, uid="u1"))
    return monkeypatch


def test_allowed_llm_options_returns_all_models_for_non_admin(patched):
    # Non-admin sees BOTH models — i.e. NOT grant-filtered.
    options = model_access.allowed_llm_options().get("options", [])
    assert len(options) == 2
    ids = {(opt["profile_id"], opt["model_id"]) for opt in options}
    assert ids == {("p1", "m1"), ("p2", "m2")}


def test_redacted_model_access_all_available_code_sourced(patched):
    items = model_access.redacted_model_access("u1")["llm"]
    assert len(items) == 2
    assert all(item["available"] is True for item in items)
    assert all(item["source"] == "code" for item in items)


def test_apply_allowed_llm_selection_accepts_real_model(patched):
    selection = {"profile_id": "p2", "model_id": "m2"}
    assert model_access.apply_allowed_llm_selection(selection) == selection


def test_apply_allowed_llm_selection_rejects_unknown_model(patched):
    with pytest.raises(PermissionError):
        model_access.apply_allowed_llm_selection({"profile_id": "p2", "model_id": "BOGUS"})


def test_has_capability_access_llm_true_for_non_admin(patched):
    assert model_access.has_capability_access("llm") is True


def test_has_capability_access_false_when_llm_catalog_empty(patched):
    patched.setattr(model_access, "admin_catalog", lambda: {"services": {"llm": {"profiles": []}}})
    assert model_access.has_capability_access("llm") is False


def test_has_capability_access_true_for_admin_regardless(patched):
    patched.setattr(model_access, "get_current_user", lambda: _StubUser(is_admin=True, uid="admin"))
    # Even with an empty llm catalog, admins are never gated.
    patched.setattr(model_access, "admin_catalog", lambda: {"services": {"llm": {"profiles": []}}})
    assert model_access.has_capability_access("llm") is True


def test_apply_allowed_llm_selection_none_passthrough(patched):
    assert model_access.apply_allowed_llm_selection(None) is None
