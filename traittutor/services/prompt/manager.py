#!/usr/bin/env python
"""
Unified Prompt Manager - Single source of truth for all prompt loading.
Supports multi-language, caching, and language fallbacks.
"""

import logging
from pathlib import Path
from typing import Any, Iterable

from traittutor.runtime.home import PACKAGE_ROOT
from traittutor.services.config import parse_language
from traittutor.services.prompt.markdown import PromptLoadError, load_markdown_prompt

logger = logging.getLogger(__name__)


class PromptManager:
    """Unified prompt manager with singleton pattern and global caching."""

    _instance: "PromptManager | None" = None
    _cache: dict[str, dict[str, Any]] = {}

    # Language fallback chain: if primary language not found, try alternatives
    LANGUAGE_FALLBACKS = {
        "zh": ["zh", "cn", "en"],
        "en": ["en", "zh", "cn"],
    }

    # Supported modules
    MODULES = [
        "research",
        "solve",
        "question",
        "math_animator",
        "notebook",
        "visualize",
        "chat",
    ]

    # All checked-in prompt assets live below ``traittutor/prompts``.  Keeping
    # this mapping here gives callers a stable logical module name while the
    # physical layout remains organized by function.
    MODULE_ROOTS: dict[str, str] = {
        "research": "agents/research",
        "solve": "capabilities/solve",
        "question": "agents/question",
        "math_animator": "agents/math_animator",
        "notebook": "agents/notebook",
        "visualize": "agents/visualize",
        "vision_solver": "agents/vision_solver",
        "chat": "agents/chat",
    }

    def __new__(cls) -> "PromptManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load_prompts(
        self,
        module_name: str,
        agent_name: str,
        language: str = "zh",
        subdirectory: str | None = None,
        *,
        required_sections: Iterable[str] = (),
    ) -> dict[str, Any]:
        """
        Load prompts for an agent.

        Args:
            module_name: Module name (research, solve, question)
            agent_name: Agent name (filename without .md)
            language: Language code ('zh' or 'en')
            subdirectory: Optional subdirectory (e.g., 'solve_loop' for solve module)
            required_sections: Dot-paths which must resolve to non-empty prompt
                values. Missing values raise :class:`PromptLoadError`.

        Returns:
            Loaded prompt configuration dictionary
        """
        lang_code = parse_language(language)
        cache_key = self._build_cache_key(module_name, agent_name, lang_code, subdirectory)

        if cache_key in self._cache:
            prompts = self._cache[cache_key]
        else:
            prompts = self._load_with_fallback(module_name, agent_name, lang_code, subdirectory)
            self._cache[cache_key] = prompts
        self._validate_required_sections(
            prompts,
            required_sections,
            module_name=module_name,
            agent_name=agent_name,
            language=lang_code,
        )
        return prompts

    def _build_cache_key(
        self,
        module_name: str,
        agent_name: str,
        lang_code: str,
        subdirectory: str | None,
    ) -> str:
        """Build unique cache key."""
        subdir_part = f"_{subdirectory}" if subdirectory else ""
        return f"{module_name}_{agent_name}_{lang_code}{subdir_part}"

    def _load_with_fallback(
        self,
        module_name: str,
        agent_name: str,
        lang_code: str,
        subdirectory: str | None,
    ) -> dict[str, Any]:
        """Load prompt file with language fallback."""
        prompt_dirs = self._candidate_prompt_dirs(module_name)
        fallback_chain = self.LANGUAGE_FALLBACKS.get(lang_code, ["en"])

        failures: list[str] = []
        seen_paths: set[Path] = set()
        for prompts_dir in prompt_dirs:
            for lang in fallback_chain:
                prompt_file = self._resolve_prompt_path(prompts_dir, lang, agent_name, subdirectory)
                if not prompt_file or not prompt_file.exists() or prompt_file in seen_paths:
                    continue
                seen_paths.add(prompt_file)
                try:
                    return load_markdown_prompt(prompt_file)
                except (OSError, PromptLoadError, ValueError) as exc:
                    failures.append(f"{prompt_file}: {exc}")
                    logger.warning(
                        "prompt_load_candidate_failed module=%s agent=%s language=%s path=%s error=%s",
                        module_name,
                        agent_name,
                        lang,
                        prompt_file,
                        exc,
                    )

        target = f"{module_name}/{agent_name} (language={lang_code})"
        if failures:
            raise PromptLoadError(
                f"Unable to load prompt {target}; all candidate assets failed: "
                + "; ".join(failures)
            )
        roots = ", ".join(str(path) for path in prompt_dirs)
        raise PromptLoadError(
            f"Prompt asset not found for {target}; searched prompt roots: {roots}"
        )

    @staticmethod
    def _validate_required_sections(
        prompts: dict[str, Any],
        required_sections: Iterable[str],
        *,
        module_name: str,
        agent_name: str,
        language: str,
    ) -> None:
        """Reject a loaded bundle when a caller's required prompt is absent."""
        missing: list[str] = []
        for path in required_sections:
            value: Any = prompts
            for key in (part for part in path.split(".") if part):
                if not isinstance(value, dict) or key not in value:
                    value = None
                    break
                value = value[key]
            if not isinstance(value, str) or not value.strip():
                missing.append(path)
        if missing:
            raise PromptLoadError(
                f"Prompt {module_name}/{agent_name} (language={language}) is missing "
                f"required prompt sections: {', '.join(missing)}"
            )

    def _candidate_prompt_dirs(self, module_name: str) -> list[Path]:
        """Return the canonical prompt directory."""
        canonical_root = PACKAGE_ROOT / "traittutor" / "prompts"
        relative_root = self.MODULE_ROOTS.get(module_name)
        if relative_root is None:
            # ``capabilities`` is a namespace used by a few older callers;
            # those callers pass the concrete capability as ``agent_name``.
            relative_root = f"capabilities/{module_name}"
        canonical = canonical_root / relative_root

        return [canonical]

    def _resolve_prompt_path(
        self,
        prompts_dir: Path,
        lang: str,
        agent_name: str,
        subdirectory: str | None,
    ) -> Path | None:
        """Resolve prompt file path, supporting subdirectory and recursive search."""
        lang_dir = prompts_dir / lang

        if not lang_dir.exists():
            return None

        # If subdirectory specified, look there first
        if subdirectory:
            direct_path = lang_dir / subdirectory / f"{agent_name}.md"
            if direct_path.exists():
                return direct_path

        # Try direct path
        direct_path = lang_dir / f"{agent_name}.md"
        if direct_path.exists():
            return direct_path

        # Recursive search in subdirectories
        found = list(lang_dir.rglob(f"{agent_name}.md"))
        if found:
            return found[0]

        return None

    def get_prompt(
        self,
        prompts: dict[str, Any],
        section: str,
        field: str | None = None,
        fallback: str = "",
    ) -> str:
        """
        Safely get prompt from loaded configuration.

        Args:
            prompts: Loaded prompt dictionary
            section: Top-level section name
            field: Optional nested field name
            fallback: Default value if not found

        Returns:
            Prompt string or fallback
        """
        if section not in prompts:
            return fallback

        value = prompts[section]

        if field is None:
            return value if isinstance(value, str) else fallback

        if isinstance(value, dict) and field in value:
            result = value[field]
            return result if isinstance(result, str) else fallback

        return fallback

    def clear_cache(self, module_name: str | None = None) -> None:
        """
        Clear cached prompts.

        Args:
            module_name: If provided, only clear cache for this module
        """
        if module_name:
            keys_to_remove = [k for k in self._cache if k.startswith(f"{module_name}_")]
            for key in keys_to_remove:
                del self._cache[key]
        else:
            self._cache.clear()

    def reload_prompts(
        self,
        module_name: str,
        agent_name: str,
        language: str = "zh",
        subdirectory: str | None = None,
    ) -> dict[str, Any]:
        """Force reload prompts, bypassing cache."""
        lang_code = parse_language(language)
        cache_key = self._build_cache_key(module_name, agent_name, lang_code, subdirectory)

        if cache_key in self._cache:
            del self._cache[cache_key]

        return self.load_prompts(module_name, agent_name, language, subdirectory)


# Global singleton instance
_prompt_manager: PromptManager | None = None


def get_prompt_manager() -> PromptManager:
    """Get the global PromptManager instance."""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = PromptManager()
    return _prompt_manager


__all__ = ["PromptManager", "get_prompt_manager"]
