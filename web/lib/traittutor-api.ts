import { apiFetch, apiUrl } from "@/lib/api";
import {
  createNotebook,
  listNotebooks,
  upsertNotebookEntry,
  type NotebookSummary,
} from "@/lib/notebook-api";

export type TraitKey = "O" | "C" | "E" | "A" | "N";
export type GenerateKind = "courseware" | "flashcards" | "quiz";

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
    markdown?: string;
    sections?: Array<{ title: string; content: string[] }>;
    items?: Array<Record<string, unknown>>;
    save_target: "notebook" | "question_bank";
    evaluation?: { overall_score: number; verdict: "pass" | "revise" | "fail"; suggestions: string[] };
  };
  learner_profile: Record<string, unknown>;
}

export interface GenerationTaskAccepted {
  generation_id: string;
  status: "queued";
  events_url: string;
  result_url: string;
}

export interface GenerationProgressEvent {
  sequence: number;
  type: string;
  message: string;
  data: Record<string, unknown>;
}

export interface LearningPack {
  pack_id: string;
  title: string;
  material: Record<string, unknown>;
  profile_id?: string | null;
  persona?: string | null;
  artifacts: Record<GenerateKind, Array<Record<string, unknown>>>;
  flashcard_progress: Record<string, string>;
  quiz_attempts: Array<Record<string, unknown>>;
  created_at: string;
  updated_at: string;
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
  material: Record<string, unknown>;
  profile_id?: string;
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

export async function getTraitTutorGenerationTask(generationId: string): Promise<GenerateSuiteResult | { status: string; error?: string }> {
  const response = await apiFetch(apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}`), { cache: "no-store" });
  return expectJson<GenerateSuiteResult | { status: string; error?: string }>(response);
}

export function subscribeTraitTutorGeneration(
  task: GenerationTaskAccepted,
  onEvent: (event: GenerationProgressEvent) => void,
  onError: () => void,
): () => void {
  const stream = new EventSource(apiUrl(task.events_url));
  for (const type of ["accepted", "material_resolved", "profile_strategy_ready", "generation_started", "batch_validated", "evaluation_completed", "completed", "failed"]) {
    stream.addEventListener(type, (event) => {
      try { onEvent(JSON.parse((event as MessageEvent<string>).data) as GenerationProgressEvent); } catch { onError(); }
      if (type === "completed" || type === "failed") stream.close();
    });
  }
  stream.onerror = onError;
  return () => stream.close();
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
  const response = await apiFetch(apiUrl("/api/v1/notebook/add_record"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      notebook_ids: [notebook.id],
      record_type: "chat",
      title: result.result.title,
      summary: result.generation_type,
      user_query: "TraitTutor Generate Suite",
      output,
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
