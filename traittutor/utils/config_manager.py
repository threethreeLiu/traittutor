from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional


class ConfigManager:
    """
    Minimal runtime configuration manager backed by canonical SQLite.

    The long-lived configuration model is now:
    - model catalog documents for providers and credentials
    - runtime configuration documents for application behavior
    """

    _instance: Optional["ConfigManager"] = None
    _lock = RLock()

    def __new__(cls, project_root: Optional[Path] = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ConfigManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, project_root: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return

        self.project_root = project_root or Path(__file__).parent.parent.parent
        # ConfigManager is also used by offline tooling and tests.  It must be
        # rooted in the project supplied by its caller, rather than borrowing
        # the process-wide request runtime directory.
        self.config_path = self.project_root / "data" / "user" / "settings" / "main.yaml"
        self._config_cache: Dict[str, Any] = {}
        self._initialized = True

    def _deep_update(self, target: Dict[str, Any], source: Dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_update(target[key], value)
            else:
                target[key] = value

    def load_config(self, force_reload: bool = False) -> Dict[str, Any]:
        with self._lock:
            from traittutor.services.config.loader import load_runtime_document

            self._config_cache = load_runtime_document("main", self.project_root)
            return {**self._config_cache}

    def save_config(self, config: Dict[str, Any]) -> bool:
        with self._lock:
            current = self.load_config(force_reload=True)
            self._deep_update(current, config)

            from traittutor.services.config.loader import save_runtime_document

            self._config_cache = save_runtime_document("main", current, self.project_root)
            return True

    def get_env_info(self) -> Dict[str, str]:
        return {"model": self._runtime_key_values().get("LLM_MODEL", "")}

    def validate_required_env(self, keys: List[str]) -> Dict[str, List[str]]:
        values = self._runtime_key_values()
        missing = [key for key in keys if not values.get(key)]
        return {"missing": missing}

    def _runtime_key_values(self) -> Dict[str, str]:
        from traittutor.services.config.model_catalog import ModelCatalogService
        from traittutor.services.config.runtime_settings import RuntimeSettingsService

        # A ConfigManager instantiated for a project must inspect that
        # project's catalog, not the request-local runtime directory of the
        # process hosting it.  The latter made diagnostics report whichever
        # model happened to be active globally.
        settings_dir = self.config_path.parent
        catalog_service = ModelCatalogService(settings_dir / "model_catalog.json")
        catalog = catalog_service.load(include_local_overlay=False)
        llm_profile = catalog_service.get_active_profile(catalog, "llm") or {}
        llm_model = catalog_service.get_active_model(catalog, "llm") or {}
        system = RuntimeSettingsService.get_instance(settings_dir).load_system()
        return {
            "BACKEND_PORT": str(system["backend_port"]),
            "FRONTEND_PORT": str(system["frontend_port"]),
            "LLM_BINDING": str(llm_profile.get("binding") or ""),
            "LLM_MODEL": str(llm_model.get("model") or ""),
            "LLM_API_KEY": str(llm_profile.get("api_key") or ""),
            "LLM_HOST": str(llm_profile.get("base_url") or ""),
        }

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._instance = None
