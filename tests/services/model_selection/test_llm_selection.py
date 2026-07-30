from traittutor.services.model_selection import (
    LLMSelection,
    apply_llm_selection_to_catalog,
    list_llm_options,
)


def _catalog():
    return {
        "version": 1,
        "services": {
            "llm": {
                "active_profile_id": "p1",
                "active_model_id": "m1",
                "profiles": [
                    {
                        "id": "p1",
                        "name": "OpenRouter",
                        "binding": "openrouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key": "secret",
                        "api_version": "",
                        "extra_headers": {"x-secret": "nope"},
                        "models": [
                            {
                                "id": "m1",
                                "name": "Gemini Flash",
                                "model": "google/gemini-3-flash-preview",
                                "context_window": "1000000",
                            },
                            {
                                "id": "m2",
                                "name": "GPT Mini",
                                "model": "openai/gpt-4o-mini",
                            },
                        ],
                    },
                    {
                        "id": "p2",
                        "name": "Local",
                        "binding": "ollama",
                        "base_url": "http://localhost:11434/v1",
                        "api_key": "",
                        "api_version": "",
                        "extra_headers": {},
                        "models": [{"id": "m3", "name": "Llama", "model": "llama3.2"}],
                    },
                ],
            },
            "embedding": {"active_profile_id": None, "active_model_id": None, "profiles": []},
            "search": {"active_profile_id": None, "profiles": []},
        },
    }


def test_list_llm_options_is_redacted_and_marks_active_default():
    payload = list_llm_options(_catalog())
    assert payload["active"] == {"profile_id": "p1", "model_id": "m1"}
    assert [o["model_id"] for o in payload["options"]] == ["m1", "m2", "m3"]
    assert payload["options"][0]["is_active_default"] is True
    assert payload["options"][0]["context_window"] == 1000000
    assert "api_key" not in payload["options"][0]
    assert "base_url" not in payload["options"][0]
    assert "extra_headers" not in payload["options"][0]


def test_list_llm_options_uses_model_vendor_for_custom_protocol_icons():
    catalog = {
        "services": {
            "llm": {
                "active_profile_id": "minimax",
                "active_model_id": "minimax",
                "profiles": [{
                    "id": "minimax",
                    "name": "MiniMax M3",
                    "binding": "custom_anthropic",
                    "models": [{"id": "minimax", "model": "MiniMax-M3"}],
                }],
            }
        }
    }
    option = list_llm_options(catalog)["options"][0]
    assert option["provider"] == "minimax"
    assert option["provider_label"] == "MiniMax"


def test_apply_llm_selection_to_catalog_returns_copy_with_selected_active_ids():
    selected = apply_llm_selection_to_catalog(
        _catalog(), LLMSelection(profile_id="p2", model_id="m3")
    )
    assert selected["services"]["llm"]["active_profile_id"] == "p2"
    assert selected["services"]["llm"]["active_model_id"] == "m3"


def test_apply_llm_selection_does_not_mutate_source_catalog():
    catalog = _catalog()
    apply_llm_selection_to_catalog(catalog, LLMSelection(profile_id="p2", model_id="m3"))
    assert catalog["services"]["llm"]["active_profile_id"] == "p1"
    assert catalog["services"]["llm"]["active_model_id"] == "m1"


def test_apply_llm_selection_rejects_model_not_in_profile():
    try:
        apply_llm_selection_to_catalog(_catalog(), LLMSelection(profile_id="p2", model_id="m1"))
    except ValueError as exc:
        assert "Invalid LLM selection" in str(exc)
    else:
        raise AssertionError("expected invalid selection to fail")
