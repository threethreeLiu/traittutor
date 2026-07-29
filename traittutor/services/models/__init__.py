"""Code-defined model sources: CC Switch import + local YAML catalog."""

from .cc_switch import ModelRecord, map_provider, read_providers, iter_model_records
from .local_catalog import load_local_llm, local_models_path

__all__ = [
    "ModelRecord",
    "map_provider",
    "read_providers",
    "iter_model_records",
    "load_local_llm",
    "local_models_path",
]
