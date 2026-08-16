"""TraitTutor generation suite."""

from .materials import (
    MaterialChunk,
    MaterialReference,
    MaterialResolutionError,
    MaterialResolver,
    ResolvedMaterial,
)
from .service import (
    GenerationRequest,
    GenerationResult,
    load_generation,
    save_generation,
)

__all__ = [
    "GenerationRequest",
    "GenerationResult",
    "MaterialChunk",
    "MaterialReference",
    "MaterialResolutionError",
    "MaterialResolver",
    "ResolvedMaterial",
    "load_generation",
    "save_generation",
]
