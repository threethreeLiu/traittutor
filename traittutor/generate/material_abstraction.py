"""Unified material abstraction for generation, KG, and learner-model joins.

This layer deliberately reuses ``MaterialAnalysis`` as the only subject/grade
classifier.  It does not re-classify material.  Its job is to keep the source
metadata, analysis result, chunks, and source refs in one compact contract that
the generation graph can pass around without leaking full uploaded files into
prompts or BKT.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
from typing import Any, Mapping

from traittutor.personalization.models import SubjectRef


_FILE_METADATA_KEYS = (
    "filename",
    "converted_to_pdf",
    "page_count",
    "mime_type",
    "checksum",
    "attachment_id",
    "session_id",
    "url",
)


@dataclass(frozen=True)
class MaterialAbstraction:
    material_id: str
    source_type: str
    source_id: str
    title: str
    file_metadata: dict[str, Any]
    analysis: dict[str, Any] | None
    subject_ref: dict[str, Any] | None
    chunks: list[dict[str, Any]]
    source_refs: list[dict[str, Any]]
    concept_candidates: list[dict[str, Any]] = field(default_factory=list)
    boundary: str = (
        "Material abstraction preserves file/source metadata and concept "
        "evidence for generation and KG; file upload alone never updates BKT."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> dict[str, Any]:
        """Return a compact record suitable for persisted generation JSON."""
        return {
            "material_id": self.material_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "title": self.title,
            "file_metadata": self.file_metadata,
            "analysis": self.analysis,
            "subject_ref": self.subject_ref,
            "source_refs": self.source_refs[:24],
            "concept_candidates": self.concept_candidates[:24],
            "boundary": self.boundary,
        }


def _stable_material_id(source_type: str, source_id: str, title: str, chunks: list[dict[str, Any]]) -> str:
    seed = "|".join(
        [
            source_type,
            source_id,
            title,
            *[str(chunk.get("chunk_id") or "") for chunk in chunks[:12]],
        ]
    )
    return f"mat_{sha256(seed.encode('utf-8')).hexdigest()[:20]}"


def _metadata_summary(metadata: Mapping[str, Any] | None, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    raw = dict(metadata or {})
    summary = {key: raw[key] for key in _FILE_METADATA_KEYS if key in raw}
    page_slices = raw.get("page_slices")
    if isinstance(page_slices, list):
        summary["page_slice_count"] = len(page_slices)
        summary.setdefault("page_count", raw.get("page_count") or len(page_slices))
    if chunks:
        pages = []
        for chunk in chunks:
            citation = chunk.get("citation") if isinstance(chunk.get("citation"), Mapping) else {}
            locator = citation.get("locator") if isinstance(citation.get("locator"), Mapping) else {}
            page = locator.get("page")
            if isinstance(page, int) and page not in pages:
                pages.append(page)
        if pages:
            summary["page_range"] = [min(pages), max(pages)]
    return summary


def subject_ref_from_analysis(analysis: Mapping[str, Any] | None) -> SubjectRef | None:
    """Build the canonical subject ref from existing MaterialAnalysis output."""
    data = dict(analysis or {})
    raw_subject = str(data.get("subject") or "").strip()
    if not raw_subject:
        return None
    sub_subject = str(data.get("sub_subject") or "").strip()
    confidence = float(data.get("confidence") or 0)
    path = [raw_subject, *([sub_subject] if sub_subject else [])]
    return SubjectRef(
        subject_id=raw_subject,
        label=raw_subject,
        path=path,
        confidence=max(0.0, min(1.0, confidence)),
        source="material_analysis",
        confirmed=False,
    )


def build_material_abstraction(
    *,
    resolved: Any,
    analysis: Mapping[str, Any] | None,
    original_metadata: Mapping[str, Any] | None = None,
) -> MaterialAbstraction:
    """Create one task-local abstraction without repeating classification."""
    chunks = [chunk.to_dict() if hasattr(chunk, "to_dict") else dict(chunk) for chunk in resolved.chunks]
    source_refs = []
    for chunk in chunks:
        citation = chunk.get("citation") if isinstance(chunk.get("citation"), Mapping) else {}
        locator = citation.get("locator") if isinstance(citation.get("locator"), Mapping) else {}
        source_refs.append(
            {
                "source_id": str(chunk.get("source_id") or resolved.source_id),
                "chunk_id": str(chunk.get("chunk_id") or ""),
                "source_type": str(chunk.get("source_type") or resolved.source_type),
                "title": str(chunk.get("title") or resolved.title),
                "locator": dict(locator),
            }
        )
    subject = subject_ref_from_analysis(analysis)
    concept_candidates = []
    if subject is not None:
        # The real concept graph is extracted/merged by KG.  These candidates
        # are temporary, chunk-grounded handles so generation/BKT can reference
        # material safely before the background graph finishes.
        for chunk in chunks[:12]:
            concept_candidates.append(
                {
                    "concept_id": str(chunk.get("chunk_id") or ""),
                    "label": str(chunk.get("excerpt") or chunk.get("title") or "")[:120],
                    "subject_id": subject.subject_id,
                    "source_refs": [str(chunk.get("chunk_id") or "")],
                    "confidence": min(subject.confidence, 0.55),
                    "temporary": True,
                }
            )
    return MaterialAbstraction(
        material_id=_stable_material_id(str(resolved.source_type), str(resolved.source_id), str(resolved.title), chunks),
        source_type=str(resolved.source_type),
        source_id=str(resolved.source_id),
        title=str(resolved.title),
        file_metadata=_metadata_summary(original_metadata, chunks),
        analysis=dict(analysis) if analysis else None,
        subject_ref=subject.model_dump() if subject else None,
        chunks=chunks,
        source_refs=source_refs,
        concept_candidates=concept_candidates,
    )


def build_learning_targets(
    *,
    generation_type: str,
    abstraction: MaterialAbstraction,
    personalization_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Select a small task-local target set shared by the three generators."""
    context = dict(personalization_context or {})
    signals = [item for item in context.get("relevant_concept_signals") or [] if isinstance(item, Mapping)]
    targets = []
    for signal in signals[:8]:
        support = str(signal.get("support_level") or "")
        targets.append(
            {
                "concept_id": str(signal.get("concept_id") or ""),
                "label": str(signal.get("label") or ""),
                "source": "learner_model",
                "priority": "review" if support == "needs_support" else "reinforce",
                "mastery_probability": signal.get("mastery_probability"),
                "evidence_refs": list(signal.get("evidence_refs") or [])[:6],
            }
        )
    if not targets:
        for candidate in abstraction.concept_candidates[:6]:
            targets.append(
                {
                    "concept_id": candidate["concept_id"],
                    "label": candidate["label"],
                    "source": "material_chunk",
                    "priority": "introduce",
                    "evidence_refs": candidate["source_refs"],
                    "temporary": True,
                }
            )
    buckets = {
        "courseware_targets": targets[:6] if generation_type == "courseware" else targets[:3],
        "flashcard_targets": targets[:8] if generation_type == "flashcards" else targets[:4],
        "quiz_targets": targets[:8] if generation_type == "quiz" else targets[:4],
        "visual_targets": targets[:2],
    }
    return {
        "subject_ref": abstraction.subject_ref,
        "material_id": abstraction.material_id,
        **buckets,
        "boundary": "Learning targets guide generation only; BKT changes require later learner events.",
    }


__all__ = [
    "MaterialAbstraction",
    "build_learning_targets",
    "build_material_abstraction",
    "subject_ref_from_analysis",
]
