from traittutor.services import llm


def test_llm_module_exports_cache_helpers():
    assert callable(llm.clear_llm_config_cache)
    assert callable(llm.reload_config)
