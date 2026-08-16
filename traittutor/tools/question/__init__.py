"""
Question Tools - Question generation system toolset

Tools for PDF parsing and question extraction.
"""

# MinerU parsing now lives in the shared parse layer
# (traittutor/services/parsing/engines/mineru); re-exported here for the question
# toolset's canonical parsing API.
from traittutor.services.parsing.engines.mineru.backend import parse_pdf_to_workdir
from traittutor.services.parsing.engines.mineru.config import (
    MinerUConfig,
    MinerUError,
    resolve_mineru_config,
)
from traittutor.services.parsing.engines.mineru.local import parse_pdf_with_mineru

from .question_extractor import extract_questions_from_paper

__all__ = [
    "MinerUConfig",
    "MinerUError",
    "parse_pdf_to_workdir",
    "parse_pdf_with_mineru",
    "resolve_mineru_config",
    "extract_questions_from_paper",
]
