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
    generate_traittutor_content,
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
    "generate_traittutor_content",
    "load_generation",
    "save_generation",
]
