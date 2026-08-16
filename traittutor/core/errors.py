"""
Base exception classes for consistent error handling across the application.
Provides a standardized way to distinguish between bugs, recoverable errors,
and configuration issues.
"""

from typing import Any, Dict, Optional


class TraitTutorError(Exception):
    """Base class for all application errors in TraitTutor."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} (details: {self.details})"
        return self.message


class ConfigurationError(TraitTutorError):
    """Raised when there's a configuration-related error."""

    pass


class ValidationError(TraitTutorError):
    """Raised when input validation fails."""

    pass


class ServiceError(TraitTutorError):
    """Base class for service layer errors."""

    pass


class LLMServiceError(ServiceError):
    """Base class for LLM service-related errors."""

    pass


class LLMContextError(LLMServiceError):
    """Raised when prompt exceeds model context window."""

    pass


class EnvironmentConfigError(ConfigurationError):
    """Raised when there's an environment-related configuration error."""

    pass
