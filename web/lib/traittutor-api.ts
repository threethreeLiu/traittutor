import { apiFetch, apiUrl } from "@/lib/api";
import {
  createNotebook,
  listNotebooks,
  upsertNotebookEntry,
  type NotebookSummary,
} from "@/lib/notebook-api";

export type TraitKey = "O" | "C" | "E" | "A" | "N";
export type GenerateKind = "courseware" | "flashcards" | "quiz";
export type LearningComponentType =
  | "goal_map" | "concept_explanation" | "worked_example" | "visual_map"
  | "audio_explanation" | "diagnostic_check" | "guided_practice"
  | "retrieval_card" | "progress_checkpoint" | "reflection_prompt"
  | "transfer_challenge" | "review_queue";

export interface TraitQuestion {
  id: number;
  text: string;
  trait: TraitKey;
  reverse: boolean;
}

export interface TraitQuestionsResponse {
  instrument: string;
  scale: { min: number; max: number; neutral: number };
  options: { value: number; label: string }[];
  questions: TraitQuestion[];
  traits: { key: TraitKey; label: string; subtitle: string }[];
  usage_boundary: string;
}

export interface TraitProfile {
  profile_id: string;
  scores: Record<TraitKey, number>;
  levels: Record<TraitKey, string>;
  dominant_traits: TraitKey[];
  summary: string;
  answers: Record<string, number>;
  created_at: string;
  metadata?: {
    slr_support?: SlrSupport;
    [key: string]: unknown;
  };
}

export interface SlrSupportDimension {
  label: string;
  detail: string;
  actions: string[];
  emphasis: "standard" | "strong";
  evidence_count: number;
}

export interface SlrSupport {
  version: string;
  source: "big_five_initial";
  status: "initial";
  dimensions: Record<"goal_planning" | "monitoring_regulation" | "reflection_transfer" | "motivation_emotion", SlrSupportDimension>;
  boundary: string;
}

export interface GenerateSuiteResult {
  generation_id: string;
  generation_type: GenerateKind;
  status: "completed" | "failed";
  events: Array<{
    type: string;
    message: string;
    created_at: string;
    data: Record<string, unknown>;
  }>;
  result: {
    kind: GenerateKind;
    title: string;
    artifact_type?: GenerateKind;
    artifact_url?: string;
    markdown?: string;
    sections?: Array<{ title?: string; section_title?: string; content?: string[]; core_content?: string; images?: GeneratedLearningImage[] }>;
    items?: Array<Record<string, unknown>>;
    images?: GeneratedLearningImage[];
    image_generation?: { status: "completed" | "failed" | "unavailable"; message?: string };
    save_target: "notebook" | "question_bank";
    evaluation?: { overall_score: number; verdict: "pass" | "revise" | "fail"; suggestions: string[] };
    external_sources?: ExternalLearningSource[];
    learning_targets?: LearningTargets;
    material_abstraction?: MaterialAbstraction;
  };
  material?: {
    analysis?: MaterialAnalysis;
    augmentation?: MaterialAugmentation;
    abstraction?: MaterialAbstraction;
    file_metadata?: Record<string, unknown>;
    [key: string]: unknown;
  };
  learner_profile: Record<string, unknown>;
  personalization_context_snapshot?: Record<string, unknown> | null;
  teaching_strategy_plan?: Record<string, unknown> | null;
  personalization_evidence_refs?: string[] | null;
}

export interface MaterialAbstraction {
  material_id: string;
  source_type: string;
  source_id: string;
  title: string;
  file_metadata: Record<string, unknown>;
  analysis?: MaterialAnalysis | null;
  subject_ref?: Record<string, unknown> | null;
  source_refs?: Array<Record<string, unknown>>;
  concept_candidates?: Array<Record<string, unknown>>;
  boundary?: string;
}

export interface LearningTargets {
  subject_ref?: Record<string, unknown> | null;
  material_id?: string;
  courseware_targets?: Array<Record<string, unknown>>;
  flashcard_targets?: Array<Record<string, unknown>>;
  quiz_targets?: Array<Record<string, unknown>>;
  visual_targets?: Array<Record<string, unknown>>;
  boundary?: string;
}

/** A deliberately small, learner-safe representation of a web source actually used in generation. */
export interface ExternalLearningSource {
  title: string;
  url: string;
  snippet?: string;
  retrieved_at?: string;
}

export interface MaterialAugmentation {
  used: boolean;
  reason?: string;
  sources?: ExternalLearningSource[];
}

export interface GeneratedLearningImage {
  url: string;
  alt: string;
  placement: "section" | "flashcards" | "quiz";
  provider: string;
  content_type: string;
}

export interface GenerationTaskAccepted {
  generation_id: string;
  status: "queued";
  events_url: string;
  result_url: string;
}

/** Reconstructs the stable task transport URLs when resuming a browser session. */
export function traitTutorGenerationTaskHandle(generationId: string): GenerationTaskAccepted {
  const encoded = encodeURIComponent(generationId);
  return {
    generation_id: generationId,
    status: "queued",
    events_url: `/api/v1/traittutor/generate/tasks/${encoded}/events`,
    result_url: `/api/v1/traittutor/generate/tasks/${encoded}`,
  };
}

export type GenerationTaskStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

/** A durable task snapshot returned while a generation has no final result yet. */
export interface GenerationTaskSnapshot {
  generation_id: string;
  status: Exclude<GenerationTaskStatus, "completed">;
  error?: string;
  error_code?: "model_configuration_required" | "model_routes_exhausted" | "generation_failed" | "generation_interrupted" | "generation_cancelled";
  retryable: boolean;
  created_at: string;
  updated_at: string;
}

/** @deprecated Use GenerationTaskSnapshot. */
export type GenerationTaskFailure = GenerationTaskSnapshot;

export interface GenerationProgressEvent {
  sequence: number;
  type: string;
  message: string;
  data: Record<string, unknown>;
}

export interface LearningPack {
  pack_id: string;
  title: string;
  goal?: {
    goal_id?: string;
    text: string;
    status?: "active" | "paused" | "completed";
    created_at?: string;
    [key: string]: unknown;
  } | null;
  sources?: Array<Record<string, unknown>>;
  material: Record<string, unknown>;
  profile_id?: string | null;
  persona?: string | null;
  artifacts: Record<GenerateKind, Array<Record<string, unknown>>>;
  flashcard_progress: Record<string, string>;
  quiz_attempts: Array<Record<string, unknown>>;
  component_plans?: LearningComponentPlan[];
  active_plan_id?: string | null;
  component_progress?: Record<string, Record<string, unknown>>;
  created_at: string;
  updated_at: string;
}

export interface LearningComponent {
  component_id: string;
  component_type: LearningComponentType;
  executor: "deterministic" | "lesson" | "retrieval" | "assessment" | "image" | "audio";
  label_zh: string;
  label_en: string;
  concept_refs: string[];
  support_dimensions: string[];
  bkt_stage: "unobserved" | "needs_support" | "developing" | "supported";
  modality: "text" | "interactive" | "visual" | "audio";
  dependencies: string[];
  required: boolean;
  reason: string;
  evidence_refs: string[];
  completion_event: string;
  status: "pending" | "active" | "completed" | "skipped" | "degraded";
  output_ref?: string | null;
  media_url?: string | null;
}

export interface LearningComponentPlan {
  plan_id: string;
  pack_id: string;
  version: number;
  goal: string;
  subject_ref?: { subject_id?: string; label?: string; [key: string]: unknown } | null;
  analysis_id?: string | null;
  support_state_snapshot: {
    subject_id?: string | null;
    source: "initial_profile" | "subject_evidence" | "default";
    dimensions: Record<string, Record<string, unknown>>;
    boundary: string;
  };
  components: LearningComponent[];
  status: "active" | "completed" | "superseded";
  supersedes_plan_id?: string | null;
  created_at: string;
  updated_at: string;
  start_url?: string;
}

async function expectJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const data = (await response.json()) as { detail?: string };
      detail = data.detail || detail;
    } catch {
      /* keep generic detail */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function createLearningPack(input: {
  title: string;
  material?: Record<string, unknown>;
  profile_id?: string;
  goal?: Record<string, unknown> | string;
  sources?: Array<Record<string, unknown>>;
}): Promise<LearningPack> {
  const response = await apiFetch(apiUrl("/api/v1/learning-packs"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
  return expectJson<LearningPack>(response);
}

export async function listLearningPacks(): Promise<LearningPack[]> {
  const response = await apiFetch(apiUrl("/api/v1/learning-packs"), { cache: "no-store" });
  const data = await expectJson<{ packs: LearningPack[] }>(response);
  return data.packs ?? [];
}

export async function getLearningPack(packId: string): Promise<LearningPack> {
  const response = await apiFetch(apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}`), { cache: "no-store" });
  return expectJson<LearningPack>(response);
}

export async function createLearningComponentPlan(
  packId: string,
  input: {
    instruction?: string;
    preferred_modalities?: Array<"text" | "visual" | "audio" | "interactive">;
    accessibility?: Record<string, unknown>;
    supersedes_plan_id?: string;
  } = {},
): Promise<LearningComponentPlan> {
  const response = await apiFetch(apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/plans`), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
  return expectJson<LearningComponentPlan>(response);
}

export async function getLearningComponentPlan(packId: string, planId: string): Promise<LearningComponentPlan> {
  const response = await apiFetch(apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/plans/${encodeURIComponent(planId)}`), { cache: "no-store" });
  return expectJson<LearningComponentPlan>(response);
}

export async function recordLearningComponentEvent(
  packId: string,
  planId: string,
  componentId: string,
  event: {
    event_id?: string;
    action: "start" | "complete" | "skip" | "retry" | "degrade" | "feedback";
    observation?: "correct" | "incorrect" | "known" | "uncertain" | "unknown";
    question_id?: string;
    answer?: string;
    concept_id?: string;
    concept_label?: string;
    output_ref?: string;
    media_url?: string;
    feedback?: string;
    replan?: boolean;
  },
): Promise<{ component: LearningComponent; learner_state_updated: boolean; replanned_plan?: LearningComponentPlan | null }> {
  const response = await apiFetch(apiUrl(
    `/api/v1/learning-packs/${encodeURIComponent(packId)}/plans/${encodeURIComponent(planId)}/components/${encodeURIComponent(componentId)}/events`,
  ), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(event),
  });
  return expectJson<{ component: LearningComponent; learner_state_updated: boolean; replanned_plan?: LearningComponentPlan | null }>(response);
}

export async function updateLearningPack(packId: string, patch: Record<string, unknown>): Promise<LearningPack> {
  const response = await apiFetch(apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}`), {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  });
  return expectJson<LearningPack>(response);
}

export async function fetchTraitQuestions(): Promise<TraitQuestionsResponse> {
  const response = await apiFetch(apiUrl("/api/v1/traittutor/profile/questions"), {
    cache: "no-store",
  });
  return expectJson<TraitQuestionsResponse>(response);
}

export async function createTraitProfile(
  answers: Record<string, number>,
): Promise<TraitProfile> {
  const response = await apiFetch(apiUrl("/api/v1/traittutor/profile/profiles"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers }),
  });
  return expectJson<TraitProfile>(response);
}

export async function listTraitProfiles(): Promise<TraitProfile[]> {
  const response = await apiFetch(apiUrl("/api/v1/traittutor/profile/profiles"), {
    cache: "no-store",
  });
  const data = await expectJson<{ profiles: TraitProfile[] }>(response);
  return data.profiles ?? [];
}

export async function deleteTraitProfile(profileId: string): Promise<void> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/profile/profiles/${encodeURIComponent(profileId)}`),
    { method: "DELETE" },
  );
  await expectJson<{ status: string }>(response);
}

export async function generateTraitTutorSuite(input: {
  generation_type: GenerateKind;
  material: {
    source_type: "knowledge" | "notebook" | "upload" | "paste";
    title: string;
    text: string;
    source_id?: string | null;
    metadata?: Record<string, unknown>;
  };
  learner_profile?: Partial<TraitProfile> | Record<string, unknown>;
}): Promise<GenerateSuiteResult> {
  const response = await apiFetch(apiUrl("/api/v1/traittutor/generate"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  return expectJson<GenerateSuiteResult>(response);
}

export type PreparedLearningMaterial = {
  source_type: "upload";
  source_id: string;
  title: string;
  text: string;
  metadata: Record<string, unknown> & {
    filename?: string;
    mime_type?: string;
    converted_to_pdf?: boolean;
    page_count?: number;
    page_slices?: Array<{ page: number; text: string }>;
  };
};

export type MaterialAnalysis = {
  analysis_id: string;
  session_id: string;
  source_id: string;
  version?: number;
  subject: string;
  sub_subject: string;
  chinese_grade: string;
  international_grade: string;
  grade_band?: { chinese?: string; international?: string };
  difficulty: string;
  confidence: number;
  evidence: Array<{ chunk_id: string; page?: number; excerpt: string }>;
  page_evidence?: Array<{ chunk_id: string; page?: number; excerpt: string; source_id?: string }>;
  concept_candidates?: Array<Record<string, unknown>>;
  augmentation_needed: boolean;
  augmentation_reason: string;
  augmentation_decision?: { needed?: boolean; reason?: string };
  component_affordances?: Record<string, { suitable?: boolean; confidence?: number; reasons?: string[] }>;
  created_at: string;
  trace: Record<string, unknown>;
};

/** Prepare an uploaded learning document and return page-scoped model material. */
export async function prepareTraitTutorMaterial(file: File): Promise<PreparedLearningMaterial> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
  const response = await apiFetch(apiUrl("/api/v1/traittutor/generate/materials/prepare"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, mime_type: file.type || "", base64: dataUrl.split(",")[1] || "" }),
  });
  return expectJson<PreparedLearningMaterial>(response);
}

export async function analyzeTraitTutorMaterial(input: {
  session_id: string;
  material: { source_type: "knowledge" | "notebook" | "upload" | "paste"; title: string; text: string; source_id?: string | null; metadata?: Record<string, unknown> };
}): Promise<MaterialAnalysis> {
  const response = await apiFetch(apiUrl("/api/v1/traittutor/generate/materials/analyze"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
  return expectJson<MaterialAnalysis>(response);
}

export async function createTraitTutorGenerationTask(input: {
  generation_type: GenerateKind;
  material: { source_type: "knowledge" | "notebook" | "upload" | "paste"; title: string; text: string; source_id?: string | null; metadata?: Record<string, unknown> };
  learner_profile?: Partial<TraitProfile> | Record<string, unknown>;
  options?: Record<string, unknown>;
}): Promise<GenerationTaskAccepted> {
  const response = await apiFetch(apiUrl("/api/v1/traittutor/generate/tasks"), {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  });
  return expectJson<GenerationTaskAccepted>(response);
}

export async function getTraitTutorGenerationTask(generationId: string): Promise<GenerateSuiteResult | GenerationTaskSnapshot> {
  const response = await apiFetch(apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}`), { cache: "no-store" });
  return expectJson<GenerateSuiteResult | GenerationTaskSnapshot>(response);
}

export async function cancelTraitTutorGenerationTask(generationId: string): Promise<Pick<GenerationTaskSnapshot, "generation_id" | "status">> {
  const response = await apiFetch(apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}`), { method: "DELETE" });
  return expectJson<Pick<GenerationTaskSnapshot, "generation_id" | "status">>(response);
}

export async function retryTraitTutorGenerationTask(generationId: string): Promise<GenerationTaskAccepted> {
  const response = await apiFetch(apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}/retry`), { method: "POST" });
  return expectJson<GenerationTaskAccepted>(response);
}

/** Never display provider payloads, quota codes, or credentials to learners. */
export function generationErrorMessage(error: unknown, zh = true): string {
  const message = error instanceof Error ? error.message : String(error || "");
  const lower = message.toLowerCase();
  if (lower.includes("model_configuration_required") || lower.includes("no generation model") || lower.includes("configure a generation model")) {
    return zh ? "尚未配置可用模型，请先在模型设置中完成配置。" : "No generation model is configured. Open Model settings to continue.";
  }
  if (lower.includes("model_routes_exhausted") || lower.includes("rate limit") || lower.includes("quota") || lower.includes("1308") || lower.includes("temporarily unavailable")) {
    return zh ? "当前模型额度或服务暂不可用，已自动尝试备用模型。请稍后重新生成。" : "Model capacity is temporarily unavailable. Backup models were tried; please retry later.";
  }
  return zh ? "生成未完成，请重试。系统会自动切换到可用模型。" : "Generation was not completed. Retry to automatically use another available model.";
}

export function subscribeTraitTutorGeneration(
  task: GenerationTaskAccepted,
  onEvent: (event: GenerationProgressEvent) => void,
  onError: () => void,
  options: { afterSequence?: number } = {},
): () => void {
  let closed = false;
  let stream: EventSource | null = null;
  let reconnectTimer: number | null = null;
  let lastSequence = options.afterSequence ?? 0;

  const connect = () => {
    if (closed) return;
    const separator = task.events_url.includes("?") ? "&" : "?";
    // EventSource cannot set Last-Event-ID explicitly. The durable server
    // contract accepts after_seq, so every reconnect gets an exact replay.
    stream = new EventSource(apiUrl(`${task.events_url}${separator}after_seq=${encodeURIComponent(String(lastSequence))}`));
    for (const type of ["accepted", "material_resolved", "profile_strategy_ready", "generation_started", "batch_validated", "evaluation_completed", "completed", "failed", "cancelled", "interrupted", "retry_queued"]) {
      stream.addEventListener(type, (event) => {
        try {
          const parsed = JSON.parse((event as MessageEvent<string>).data) as GenerationProgressEvent;
          lastSequence = Math.max(lastSequence, parsed.sequence || 0);
          onEvent(parsed);
        } catch { onError(); }
        if (["completed", "failed", "cancelled", "interrupted"].includes(type)) stream?.close();
      });
    }
    stream.onerror = () => {
      if (closed) return;
      stream?.close();
      onError();
      reconnectTimer = window.setTimeout(connect, 750);
    };
  };
  connect();
  return () => {
    closed = true;
    stream?.close();
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
  };
}

async function ensureTraitTutorNotebook(): Promise<NotebookSummary> {
  const notebooks = await listNotebooks();
  const existing = notebooks.find((notebook) => notebook.name === "TraitTutor");
  if (existing) return existing;
  return createNotebook({
    name: "TraitTutor",
    description: "TraitTutor generated courseware and flashcards",
    color: "#0F766E",
    icon: "brain",
  });
}

export async function saveGenerationResult(result: GenerateSuiteResult): Promise<string> {
  if (result.result.save_target === "question_bank") {
    const items = result.result.items ?? [];
    for (const item of items) {
      const optionsArray = Array.isArray(item.options) ? item.options : [];
      const options = Object.fromEntries(
        optionsArray.map((option, index) => [
          String.fromCharCode(65 + index),
          String((option as { text?: unknown }).text ?? ""),
        ]),
      );
      await upsertNotebookEntry({
        session_id: `traittutor-${result.generation_id}`,
        turn_id: result.generation_id,
        question_id: String(item.question_id ?? crypto.randomUUID()),
        question: String(item.question ?? ""),
        question_type: String(item.question_type ?? ""),
        difficulty: String(item.difficulty ?? ""),
        options,
        correct_answer: String(item.correct_answer ?? ""),
        explanation: String(item.explanation ?? ""),
      });
    }
    return "question_bank";
  }

  const notebook = await ensureTraitTutorNotebook();
  const output =
    result.result.markdown ||
    JSON.stringify(result.result.items ?? result.result.sections ?? [], null, 2);
  const visualMarkdown = (result.result.images ?? [])
    .filter((image) => typeof image.url === "string")
    .map((image) => `![${image.alt || "Learning illustration"}](${image.url})`)
    .join("\n\n");
  const response = await apiFetch(apiUrl("/api/v1/notebook/add_record"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      notebook_ids: [notebook.id],
      record_type: "chat",
      title: result.result.title,
      summary: result.generation_type,
      user_query: "TraitTutor Generate Suite",
      output: visualMarkdown ? `${output}\n\n${visualMarkdown}` : output,
      metadata: {
        source: "traittutor",
        generation_id: result.generation_id,
        generation_type: result.generation_type,
      },
    }),
  });
  await expectJson<{ success: boolean }>(response);
  return notebook.id;
}
