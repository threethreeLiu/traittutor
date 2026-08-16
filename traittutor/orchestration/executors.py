"""Read-only adapters over TraitTutor's existing generation bodies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any

from traittutor.components import ComponentInstance, ComponentRegistry
from traittutor.generate.courseware import CoursewareArtifact, generate_courseware
from traittutor.generate.flashcards import validate_flashcard_payload
from traittutor.generate.quiz import validate_quiz_payload
from traittutor.generate.videos import generate_learning_video
from traittutor.generate.visuals import generate_learning_visual

from .courseware_orchestrator import AgentExecutor, AgentTaskResult
from .evaluator import CoursewareEvaluator
from .prompt_bundle import CoursewarePromptBundle
from .task_graph import AgentTask, AgentTaskType

PayloadProvider = Callable[[AgentTask, CoursewarePromptBundle], Mapping[str, Any]]
ArtifactProvider = Callable[[], Awaitable[CoursewareArtifact]]
PodcastBody = Callable[..., Awaitable[dict[str, Any]]]
#: Synthesize and persist a two-host podcast dialogue; returns a trace with
#: ``status`` and, on success, ``audio_url``.  Mirrors visuals/videos persistence.
PodcastAudioBody = Callable[..., Awaitable[dict[str, Any]]]


def _public_text(value: Any) -> str:
    """Return a learner-visible string without serializing structured fields."""
    return value.strip() if isinstance(value, str) else ""


_FIGURE_TYPES = {"concept_map", "flow", "timeline", "compare"}


def _public_figure(value: Any) -> dict[str, Any] | None:
    """Strictly validate the optional section figure for the public schema.

    A figure is decorative presentation, never grading content: any structural
    problem drops it entirely (``None``) instead of degrading the section.
    Strings are trimmed and capped so a runaway model cannot bloat the page.
    """
    if not isinstance(value, Mapping):
        return None
    figure_type = _public_text(value.get("type"))
    if figure_type not in _FIGURE_TYPES:
        return None
    title = _public_text(value.get("title"))
    if not title:
        return None
    result: dict[str, Any] = {"type": figure_type, "title": title}

    def _texts(raw: Any, limit: int) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [_public_text(item) for item in raw if _public_text(item)][:limit]

    if figure_type == "concept_map":
        nodes_raw = value.get("nodes")
        nodes: list[dict[str, str]] = []
        if isinstance(nodes_raw, list):
            for node in nodes_raw:
                if not isinstance(node, Mapping):
                    continue
                node_id = _public_text(node.get("id"))
                label = _public_text(node.get("label"))
                if not node_id or not label:
                    continue
                entry: dict[str, str] = {"id": node_id, "label": label[:80]}
                detail = _public_text(node.get("detail"))
                if detail:
                    entry["detail"] = detail[:160]
                nodes.append(entry)
        if not nodes:
            return None
        result["nodes"] = nodes[:8]
        edges_raw = value.get("edges")
        edges: list[dict[str, str]] = []
        if isinstance(edges_raw, list):
            for edge in edges_raw:
                if not isinstance(edge, Mapping):
                    continue
                source = _public_text(edge.get("from"))
                target = _public_text(edge.get("to"))
                if not source or not target:
                    continue
                edge_entry: dict[str, str] = {"from": source, "to": target}
                edge_label = _public_text(edge.get("label"))
                if edge_label:
                    edge_entry["label"] = edge_label[:60]
                edges.append(edge_entry)
        if edges:
            result["edges"] = edges[:8]
        return result
    if figure_type == "flow":
        steps = _texts(value.get("steps"), 8)
        if not steps:
            return None
        result["steps"] = steps
        return result
    if figure_type == "timeline":
        points = _texts(value.get("points"), 8)
        if not points:
            return None
        result["points"] = points
        return result
    if figure_type == "compare":
        items_raw = value.get("items")
        items: list[dict[str, str]] = []
        if isinstance(items_raw, list):
            for item in items_raw:
                if not isinstance(item, Mapping):
                    continue
                label = _public_text(item.get("label"))
                if not label:
                    continue
                compare_entry: dict[str, str] = {"label": label[:80]}
                detail = _public_text(item.get("detail"))
                if detail:
                    compare_entry["detail"] = detail[:160]
                items.append(compare_entry)
        if len(items) < 2:
            return None
        result["items"] = items[:6]
        return result
    return None


def _goal_map_milestones(artifact: CoursewareArtifact) -> list[str]:
    """Project only material-grounded goals and concepts into the goal map."""
    lesson = artifact.lesson
    sections = [section for section in lesson.get("sections", []) if isinstance(section, Mapping)]
    milestones = [
        *(
            _public_text(item)
            for item in lesson.get("final_takeaways", [])
            if isinstance(item, str)
        ),
        *(_public_text(section.get("goal")) for section in sections),
    ]
    for concept in artifact.content_analysis.get("core_concepts", []):
        label = concept.get("label") if isinstance(concept, Mapping) else concept
        milestones.append(_public_text(label))

    visible = list(dict.fromkeys(item for item in milestones if item))
    if visible:
        return visible
    return list(
        dict.fromkeys(
            title for section in sections if (title := _public_text(section.get("section_title")))
        )
    )


def _worked_example_steps(section: Mapping[str, Any]) -> list[str]:
    """Project source-grounded section prose into a bounded worked sequence."""
    explicit = section.get("worked_example_steps")
    if isinstance(explicit, list):
        steps = [_public_text(item) for item in explicit if _public_text(item)]
        if steps:
            return steps[:8]
    figure = section.get("figure")
    if isinstance(figure, Mapping) and _public_text(figure.get("type")) == "flow":
        flow_steps = figure.get("steps")
        if isinstance(flow_steps, list):
            steps = [_public_text(item) for item in flow_steps if _public_text(item)]
            if steps:
                return steps[:8]
    body = _public_text(section.get("core_content"))
    if not body:
        return []
    lines = [line.strip().lstrip("-*•0123456789. ") for line in body.splitlines()]
    visible = [line for line in lines if line]
    return (visible or [body])[:8]


def _courseware_concept_refs(
    section: Mapping[str, Any],
    chunks: Sequence[Mapping[str, Any]],
) -> list[str | dict[str, str]]:
    """Project Web-backed claims into the evaluator's public claim contract."""
    external_urls = {
        str(chunk.get("chunk_id") or ""): str(chunk.get("source_url") or "")
        for chunk in chunks
        if str(chunk.get("source_url") or "").startswith(("http://", "https://"))
    }
    external_ids = set(external_urls)
    references = [
        str(reference)
        for reference in section.get("references", [])
        if isinstance(reference, str) and reference.strip()
    ]
    referenced_external_ids = external_ids.intersection(references)
    public_refs: list[str | dict[str, str]] = [
        reference for reference in references if reference not in external_ids
    ]
    raw_claims = section.get("external_claims", [])
    if not isinstance(raw_claims, list):
        raise ValueError("courseware external_claims must be a list")
    claimed_external_ids: set[str] = set()
    for record in raw_claims:
        if not isinstance(record, Mapping):
            raise ValueError("courseware external claim must be an object")
        source_chunk_id = str(record.get("source_chunk_id") or "")
        source_url = external_urls.get(source_chunk_id)
        claim = str(record.get("claim") or "").strip()
        if source_url is None or source_chunk_id not in referenced_external_ids:
            raise ValueError("courseware external claim is not backed by a cited Web chunk")
        if not claim or len(claim) > 2_000:
            raise ValueError("courseware external claim text is invalid")
        claimed_external_ids.add(source_chunk_id)
        public_refs.append(
            {
                "claim": claim,
                "source_url": source_url,
            }
        )
    if referenced_external_ids != claimed_external_ids:
        raise ValueError("every cited Web chunk requires an external claim record")
    return public_refs


def _instance(
    task: AgentTask,
    registry: ComponentRegistry,
    component_type: str,
    props: Mapping[str, Any],
    *,
    suffix: str = "1",
) -> ComponentInstance:
    if component_type not in task.produces_component_types:
        raise ValueError(f"task {task.task_id} may not produce {component_type}")
    spec = registry.require(component_type)
    safe_props = {key: value for key, value in props.items() if spec.allows_prop(key)}
    return ComponentInstance(
        instance_id=f"{task.task_id}-{suffix}",
        component_type=component_type,
        version=spec.version,
        props=safe_props,
        modality_hint=spec.modality,
    )


@dataclass(frozen=True)
class MaterialExecutor:
    """Validate the bounded grounding slice before any billable generation.

    The Material Agent is deliberately deterministic here: material resolution,
    authorization, and optional research source validation already happened in
    server-owned services. This boundary confirms that the specialist tasks get
    a non-empty, uniquely-addressable slice without inventing source facts.
    """

    payload_provider: PayloadProvider

    def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        payload = dict(self.payload_provider(task, bundle))
        del registry
        chunks = [chunk for chunk in payload.get("chunks", []) if isinstance(chunk, Mapping)]
        if not chunks:
            raise ValueError("material agent requires at least one grounded chunk")
        chunk_ids = [str(chunk.get("chunk_id") or "").strip() for chunk in chunks]
        if any(not chunk_id for chunk_id in chunk_ids):
            raise ValueError("every grounded chunk requires a chunk_id")
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("grounded chunk ids must be unique")
        if any(not str(chunk.get("text") or "").strip() for chunk in chunks):
            raise ValueError("every grounded chunk requires text")
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded",
            produced_component_instances=(),
            notes=f"validated {len(chunks)} grounded chunk(s)",
        )


@dataclass(frozen=True)
class CoursewareExecutor:
    """Call ``generate_courseware`` and adapt its lesson into public components."""

    payload_provider: PayloadProvider
    body: Callable[..., Awaitable[CoursewareArtifact]] = generate_courseware
    podcast_body: PodcastBody | None = None
    # When set, the dialogue is synthesized into a single audio file whose URL
    # populates the component ``media_url``.  Podcast audio is an optional
    # presentation layer: a synthesis failure leaves ``media_url`` empty so the
    # frontend can fall back to its single-segment TTS path.
    podcast_audio_body: PodcastAudioBody | None = None
    generation_id: str = ""

    async def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        payload = dict(self.payload_provider(task, bundle))
        chunks = [chunk for chunk in payload.get("chunks", []) if isinstance(chunk, Mapping)]
        artifact = await self.body(
            chunks=chunks,
            learner_strategy=dict(payload.get("learner_strategy", {})),
            slr_support=payload.get("slr_support"),
            language=bundle.material_language,
        )
        components = []
        lesson_sections = [
            section
            for section in artifact.lesson.get("sections", [])
            if isinstance(section, Mapping)
        ]
        for index, section in enumerate(lesson_sections, start=1):
            common_props: dict[str, Any] = {
                "title": section.get("section_title", artifact.lesson.get("title", "")),
                "body_markdown": section.get("core_content", ""),
                "concept_refs": _courseware_concept_refs(section, chunks),
            }
            if "concept_explanation" in task.produces_component_types:
                explanation_props = dict(common_props)
                figure = _public_figure(section.get("figure"))
                if figure is not None:
                    explanation_props["figure"] = figure
                components.append(
                    _instance(
                        task,
                        registry,
                        "concept_explanation",
                        explanation_props,
                        suffix=str(index),
                    )
                )
            if "worked_example" in task.produces_component_types:
                steps = _worked_example_steps(section)
                if steps:
                    components.append(
                        _instance(
                            task,
                            registry,
                            "worked_example",
                            {**common_props, "steps": steps},
                            suffix=f"worked-{index}",
                        )
                    )
        if "audio_explanation" in task.produces_component_types and self.podcast_body is not None:
            podcast = await self.podcast_body(
                lesson=artifact.lesson,
                language=bundle.material_language,
            )
            podcast_props: dict[str, Any] = {
                "title": podcast.get("title", artifact.lesson.get("title", "")),
                "body_markdown": podcast.get("script", ""),
                "a11y_label": podcast.get(
                    "title", artifact.lesson.get("title", "Podcast explanation")
                ),
                # Ground the audio transcript in the same chunks the lesson
                # cites; without chunk-id references the evaluation gate
                # reports missing_citations and sends every audio run to
                # manual review.
                "concept_refs": list(
                    dict.fromkeys(
                        str(reference)
                        for section in lesson_sections
                        for reference in section.get("references", [])
                        if isinstance(reference, str) and reference.strip()
                    )
                ),
            }
            # Synthesize the two-host dialogue into a single playable audio file.
            # A failure here is non-fatal: the transcript in body_markdown and
            # the frontend single-segment TTS fallback keep the component useful.
            if self.podcast_audio_body is not None and self.generation_id:
                dialogue = podcast.get("dialogue")
                if isinstance(dialogue, list) and dialogue:
                    audio_trace = await self.podcast_audio_body(
                        dialogue,
                        generation_id=self.generation_id,
                    )
                    if audio_trace.get("status") == "completed" and audio_trace.get("audio_url"):
                        podcast_props["media_url"] = audio_trace["audio_url"]
            components.append(
                _instance(
                    task,
                    registry,
                    "audio_explanation",
                    podcast_props,
                    suffix="podcast",
                )
            )
        return AgentTaskResult(
            task_id=task.task_id,
            # When the requested whitelist contains no instruction-family
            # component (e.g. only ``goal_map``, which the SRL task projects),
            # this executor still runs to supply the shared lesson artifact to
            # dependent tasks. Zero requested outputs is the expected outcome
            # then, not a degradation — only a missing section for an actually
            # requested component type is a failure.
            status="succeeded" if components or not task.produces_component_types else "degraded",
            produced_component_instances=tuple(components),
            notes=(
                "existing courseware generator returned no publishable section"
                if task.produces_component_types and not components
                else (
                    "instruction artifact produced for dependent tasks"
                    if not task.produces_component_types
                    else ""
                )
            ),
        )


@dataclass(frozen=True)
class PracticeExecutor:
    """Project lesson checkpoints into answer-free practice components."""

    artifact_provider: ArtifactProvider
    payload_provider: PayloadProvider

    async def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        artifact = await self.artifact_provider()
        payload = dict(self.payload_provider(task, bundle))
        chunks = [chunk for chunk in payload.get("chunks", []) if isinstance(chunk, Mapping)]
        components: list[ComponentInstance] = []
        preferred = next(
            (
                component_type
                for component_type in (
                    "guided_practice",
                    "diagnostic_check",
                    "calibration_checkpoint",
                    "transfer_challenge",
                    "retrieval_card",
                )
                if component_type in task.produces_component_types
            ),
            None,
        )
        if preferred is not None:
            for index, raw_section in enumerate(artifact.lesson.get("sections", []), start=1):
                if not isinstance(raw_section, Mapping):
                    continue
                checkpoint = raw_section.get("checkpoint")
                if not isinstance(checkpoint, Mapping):
                    continue
                prompt = str(checkpoint.get("question") or "").strip()
                if not prompt:
                    continue
                props: dict[str, Any] = {
                    "title": raw_section.get("section_title", artifact.lesson.get("title", "")),
                    "concept_refs": _courseware_concept_refs(raw_section, chunks),
                }
                if preferred == "retrieval_card":
                    props["front"] = prompt
                    props["hint"] = checkpoint.get("feedback_if_confused", "")
                else:
                    props["prompt"] = prompt
                    if preferred in {"guided_practice", "transfer_challenge"}:
                        props["hint"] = checkpoint.get("feedback_if_confused", "")
                components.append(_instance(task, registry, preferred, props, suffix=str(index)))
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded",
            produced_component_instances=tuple(components),
            notes=(
                "lesson contained no publishable checkpoint"
                if preferred is not None and not components
                else ""
            ),
        )


@dataclass(frozen=True)
class SRLSupportExecutor:
    """Project bounded lesson goals/reflections into non-diagnostic SRL support."""

    artifact_provider: ArtifactProvider
    arrangement_context: Mapping[str, Any] | None = None
    payload_provider: PayloadProvider | None = None

    async def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        artifact = await self.artifact_provider()
        sections = [
            section
            for section in artifact.lesson.get("sections", [])
            if isinstance(section, Mapping)
        ]
        chunks: list[Mapping[str, Any]] = []
        if self.payload_provider is not None:
            payload = dict(self.payload_provider(task, bundle))
            chunks = [chunk for chunk in payload.get("chunks", []) if isinstance(chunk, Mapping)]
        components: list[ComponentInstance] = []
        if "goal_map" in task.produces_component_types:
            props: dict[str, Any] = {
                "title": _public_text(artifact.lesson.get("lesson_goal"))
                or _public_text(artifact.lesson.get("title")),
                "milestones": _goal_map_milestones(artifact),
            }
            # Carry the lesson's chunk/claim references so the published page
            # keeps its citation linkage: without them, the result's
            # ``external_sources`` loses web-augmentation records even when the
            # analysis requested augmentation.
            seen_refs: set[str] = set()
            concept_refs: list[str | dict[str, str]] = []
            for section in sections:
                for reference in _courseware_concept_refs(section, chunks):
                    key = (
                        json.dumps(reference, ensure_ascii=False, sort_keys=True)
                        if isinstance(reference, Mapping)
                        else str(reference)
                    )
                    if key not in seen_refs:
                        seen_refs.add(key)
                        concept_refs.append(reference)
            if concept_refs:
                props["concept_refs"] = concept_refs
            components.append(_instance(task, registry, "goal_map", props))
        if "reflection_prompt" in task.produces_component_types:
            for index, section in enumerate(sections, start=1):
                prompt = str(section.get("reflection_prompt") or "").strip()
                if not prompt:
                    continue
                components.append(
                    _instance(
                        task,
                        registry,
                        "reflection_prompt",
                        {
                            "title": section.get("section_title", artifact.lesson.get("title", "")),
                            "prompt": prompt,
                        },
                        suffix=str(index),
                    )
                )
        if "progress_checkpoint" in task.produces_component_types:
            guidance = str(artifact.lesson.get("next_step_guidance") or "").strip()
            components.append(
                _instance(
                    task,
                    registry,
                    "progress_checkpoint",
                    {
                        "title": "Next step",
                        "body_markdown": guidance,
                    },
                )
            )
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded",
            produced_component_instances=tuple(components),
            notes="lesson contained no requested SRL support" if not components else "",
        )


@dataclass(frozen=True)
class UIComposerExecutor:
    """Validate the exact component collection before PageSchema assembly."""

    def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        del bundle
        components = tuple(getattr(task, "produced_component_instances", ()))
        if not components:
            raise ValueError("UI composer requires at least one validated component")
        instance_ids = [component.instance_id for component in components]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("UI composer received duplicate component instance ids")
        for component in components:
            spec = registry.require(component.component_type)
            if component.version != spec.version:
                raise ValueError("UI composer received a component version mismatch")
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded",
            produced_component_instances=(),
            notes=f"composed {len(components)} component region(s)",
        )


@dataclass(frozen=True)
class EvaluatorExecutor:
    """Run the deterministic release evaluator as the DAG's final Agent gate."""

    evaluator: CoursewareEvaluator = CoursewareEvaluator()

    def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        components = tuple(getattr(task, "produced_component_instances", ()))
        verdict = self.evaluator.evaluate(components, bundle=bundle, registry=registry)
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded" if verdict.status == "passed" else "failed",
            produced_component_instances=(),
            notes="; ".join(verdict.findings),
        )


@dataclass(frozen=True)
class FlashcardExecutor:
    """Use the existing strict flashcard validator; keep card backs server-side."""

    payload_provider: PayloadProvider

    def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        payload = dict(self.payload_provider(task, bundle))
        validated = validate_flashcard_payload(
            payload.get("payload", payload), payload.get("chunks", [])
        )
        components = tuple(
            _instance(
                task,
                registry,
                "retrieval_card",
                {
                    "title": item.node_name,
                    "front": item.front,
                    "concept_refs": [
                        reference.model_dump(mode="json") for reference in item.references
                    ],
                },
                suffix=str(index),
            )
            for index, item in enumerate(validated.items, start=1)
        )
        return AgentTaskResult(
            task_id=task.task_id, status="succeeded", produced_component_instances=components
        )


@dataclass(frozen=True)
class QuizExecutor:
    """Use the strict quiz validator and publish stems without answers/rubrics."""

    payload_provider: PayloadProvider

    def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        payload = dict(self.payload_provider(task, bundle))
        validated = validate_quiz_payload(
            payload.get("payload", payload), payload.get("chunks", [])
        )
        components = tuple(
            _instance(
                task,
                registry,
                "diagnostic_check",
                {
                    "title": item.node_name,
                    "prompt": item.question,
                    "concept_refs": [
                        reference.model_dump(mode="json") for reference in item.references
                    ],
                },
                suffix=str(item.question_id),
            )
            for item in validated.items
        )
        return AgentTaskResult(
            task_id=task.task_id, status="succeeded", produced_component_instances=components
        )


@dataclass(frozen=True)
class VisualExecutor:
    """Generate only the explicitly selected image or video support."""

    payload_provider: PayloadProvider
    body: Callable[..., Awaitable[dict[str, Any]]] = generate_learning_visual
    video_body: Callable[..., Awaitable[dict[str, Any]]] = generate_learning_video

    async def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        payload = dict(self.payload_provider(task, bundle))
        selected = str(payload.get("component_type") or "")
        component_type = (
            selected
            if selected in {"visual_map", "video_explanation"}
            else "visual_map"
            if "visual_map" in task.produces_component_types
            else "video_explanation"
        )
        if component_type not in task.produces_component_types:
            return AgentTaskResult(
                task_id=task.task_id,
                status="degraded",
                produced_component_instances=(),
                notes=f"{component_type} not in task contract",
            )
        generation_body = self.video_body if component_type == "video_explanation" else self.body
        result = await generation_body(payload, generation_id=bundle.prompt_bundle_id)
        asset = result.get("asset") if result.get("status") == "completed" else None
        if not isinstance(asset, Mapping):
            return AgentTaskResult(
                task_id=task.task_id,
                status="degraded",
                produced_component_instances=(),
                notes=str(result.get("message", f"{component_type} unavailable")),
            )
        component = _instance(
            task,
            registry,
            component_type,
            {
                "title": payload.get(
                    "title",
                    "Video explanation" if component_type == "video_explanation" else "Visual",
                ),
                "media_url": asset.get("url"),
                "a11y_label": asset.get("alt"),
                # Ground the visual in the same chunk ids the material seed
                # carries; without them the evaluation gate reports
                # missing_citations and sends every visual/video run to manual
                # review.
                **(
                    {"concept_refs": chunk_ids}
                    if (
                        chunk_ids := [
                            str(item)
                            for item in (payload.get("chunk_ids") or [])
                            if str(item).strip()
                        ]
                    )
                    else {}
                ),
            },
        )
        return AgentTaskResult(
            task_id=task.task_id, status="succeeded", produced_component_instances=(component,)
        )


@dataclass(frozen=True)
class AgentBodyExecutor:
    """Adapter for relevant ``agents/*`` bodies supplied by the composition root."""

    body: Callable[[Mapping[str, Any]], Awaitable[Sequence[ComponentInstance]]]
    payload_provider: PayloadProvider

    async def __call__(
        self, task: AgentTask, bundle: CoursewarePromptBundle, registry: ComponentRegistry
    ) -> AgentTaskResult:
        components = tuple(await self.body(self.payload_provider(task, bundle)))
        for component in components:
            if component.component_type not in task.produces_component_types:
                raise ValueError("agent emitted a component outside its task contract")
            registry.require(component.component_type)
        return AgentTaskResult(
            task_id=task.task_id, status="succeeded", produced_component_instances=components
        )


def build_executor_map(
    *,
    material: AgentExecutor,
    courseware: CoursewareExecutor,
    practice: AgentExecutor,
    srl: AgentExecutor,
    visual: AgentExecutor,
    ui_composer: AgentExecutor,
    evaluator: AgentExecutor,
) -> dict[AgentTaskType, AgentExecutor]:
    """Require a real executor for every task in the planned courseware DAG.

    Keeping this constructor total makes a missing production adapter fail at
    composition time instead of silently degrading a paid generation run.
    """
    return {
        "material": material,
        "instruction": courseware,
        "practice": practice,
        "srl": srl,
        "visual": visual,
        "ui_composer": ui_composer,
        "evaluator": evaluator,
    }


__all__ = [
    "AgentBodyExecutor",
    "CoursewareExecutor",
    "EvaluatorExecutor",
    "FlashcardExecutor",
    "MaterialExecutor",
    "PracticeExecutor",
    "QuizExecutor",
    "SRLSupportExecutor",
    "UIComposerExecutor",
    "VisualExecutor",
    "build_executor_map",
]
