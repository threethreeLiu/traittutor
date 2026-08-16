import { apiFetch, apiUrl, parseApiError } from '@/lib/api'
import {
  createNotebook,
  listNotebooks,
  upsertNotebookEntry,
  type NotebookSummary,
} from '@/lib/notebook-api'

// Deleting a Learn chat session cascades server-side to the Packs whose
// primary material links to that session (DELETE /api/v1/sessions/{id}
// returns deleted_pack_ids). Pack-list surfaces ("Continue learning" on /home,
// the /learning page) fetch once on mount, so the sidebar broadcasts this
// window event after a delete removed packs; listeners refetch instead of
// showing entries that no longer exist until a manual reload.
export const LEARNING_PACKS_INVALIDATED_EVENT = 'learning-packs-invalidated'

export function dispatchLearningPacksInvalidated(packIds: string[]): void {
  window.dispatchEvent(new CustomEvent(LEARNING_PACKS_INVALIDATED_EVENT, { detail: { packIds } }))
}

export type TraitKey = 'O' | 'C' | 'E' | 'A' | 'N'
export type GenerateKind = 'courseware' | 'flashcards' | 'quiz'
export type LearningComponentType =
  | 'goal_map'
  | 'concept_explanation'
  | 'worked_example'
  | 'visual_map'
  | 'video_explanation'
  | 'audio_explanation'
  | 'diagnostic_check'
  | 'guided_practice'
  | 'calibration_checkpoint'
  | 'retrieval_card'
  | 'progress_checkpoint'
  | 'reflection_prompt'
  | 'transfer_challenge'
  | 'review_queue'

// ---- F-08 PageSchema (client mirror of traittutor.components.PageSchema) ----
// Answers/rubrics/unactivated prompts are SERVER-HELD and never appear here
// (invariant #5). The frontend renders only registered component types from
// this schema; anything unknown is text-downgraded (invariant #8).
export interface PageSchemaComponentInstance {
  instance_id: string
  component_type: string
  version: string
  props: Record<string, unknown>
  modality_hint?: string | null
}

export interface PageSchemaRegion {
  region_id: string
  component?: PageSchemaComponentInstance | null
  heading?: string | null
}

export interface PageSchema {
  page_schema_id: string
  generation_run_id: string
  version: string
  regions: PageSchemaRegion[]
  supersedes_page_id?: string | null
  published: boolean
  created_at: string
}

export interface TraitQuestion {
  id: number
  text: string
  trait: TraitKey
  reverse: boolean
}

export interface TraitQuestionsResponse {
  instrument: string
  scale: { min: number; max: number; neutral: number }
  options: { value: number; label: string }[]
  questions: TraitQuestion[]
  traits: { key: TraitKey; label: string; subtitle: string }[]
  usage_boundary: string
}

export interface TraitProfile {
  profile_id: string
  scores: Record<TraitKey, number>
  levels: Record<TraitKey, string>
  dominant_traits: TraitKey[]
  summary: string
  answers: Record<string, number>
  created_at: string
  metadata?: {
    slr_support?: SlrSupport
    [key: string]: unknown
  }
}

export interface SlrSupportDimension {
  label: string
  detail: string
  actions: string[]
  emphasis: 'light' | 'standard' | 'strong'
  evidence_count: number
  source?: 'initial_profile' | 'subject_evidence' | 'learner_choice'
  confidence?: number
}

export interface SlrSupport {
  version: string
  source: 'big_five_initial' | 'generation_support_action_catalog'
  status: 'initial'
  dimensions: Record<
    'goal_planning' | 'monitoring_regulation' | 'reflection_transfer' | 'motivation_emotion',
    SlrSupportDimension
  >
  boundary: string
}

export interface GenerateSuiteResult {
  generation_id: string
  generation_type: GenerateKind
  status: 'completed' | 'needs_review' | 'failed'
  // F-08: present when the canonical page-schema renderer is available.
  page_schema?: PageSchema
  page_schema_id?: string
  events: Array<{
    type: string
    message: string
    created_at: string
    data: Record<string, unknown>
  }>
  result: {
    kind: GenerateKind
    title: string
    artifact_type?: GenerateKind
    artifact_url?: string
    markdown?: string
    podcast_title?: string
    podcast_script?: string
    podcast_generation?: {
      status: 'completed' | 'degraded'
      message?: string
      audio_url?: string
    }
    sections?: Array<{
      title?: string
      section_title?: string
      content?: string[]
      core_content?: string
      images?: GeneratedLearningImage[]
    }>
    items?: Array<Record<string, unknown>>
    // Single-component courseware runs project the generated component here
    // (the only place its props/body live in the public result).
    component?: {
      component_type?: string
      props?: Record<string, unknown>
    } | null
    images?: GeneratedLearningImage[]
    image_generation?: { status: 'completed' | 'failed' | 'unavailable'; message?: string }
    question_image_generation?: {
      status: 'completed' | 'degraded' | 'skipped'
      reason: string
      requested?: number
      completed?: number
      questions: Array<{
        question_id: string
        difficulty: 'hard'
        status: string
        message?: string
      }>
    }
    video_generation?: {
      status: 'completed' | 'failed' | 'unavailable' | 'skipped'
      message?: string
    }
    save_target: 'notebook' | 'question_bank'
    evaluation?: {
      overall_score: number
      verdict: 'pass' | 'revise' | 'fail'
      suggestions: string[]
    }
    external_sources?: ExternalLearningSource[]
    learning_targets?: LearningTargets
    material_abstraction?: MaterialAbstraction
    orchestration?: CoursewareOrchestrationSummary
  }
  material?: {
    analysis?: MaterialAnalysis
    augmentation?: MaterialAugmentation
    abstraction?: MaterialAbstraction
    file_metadata?: Record<string, unknown>
    [key: string]: unknown
  }
  learner_profile: Record<string, unknown>
  personalization_context_snapshot?: Record<string, unknown> | null
  teaching_strategy_plan?: Record<string, unknown> | null
  personalization_evidence_refs?: string[] | null
}

export interface CoursewareOrchestrationSummary {
  run_id: string
  status: 'succeeded' | 'degraded' | 'failed'
  agents: Array<{
    task_id: string
    status: 'succeeded' | 'degraded' | 'failed'
    component_count: number
  }>
}

export interface MaterialAbstraction {
  material_id: string
  source_type: string
  source_id: string
  title: string
  file_metadata: Record<string, unknown>
  analysis?: MaterialAnalysis | null
  subject_ref?: Record<string, unknown> | null
  source_refs?: Array<Record<string, unknown>>
  concept_candidates?: Array<Record<string, unknown>>
  boundary?: string
}

export interface LearningTargets {
  subject_ref?: Record<string, unknown> | null
  material_id?: string
  courseware_targets?: Array<Record<string, unknown>>
  flashcard_targets?: Array<Record<string, unknown>>
  quiz_targets?: Array<Record<string, unknown>>
  visual_targets?: Array<Record<string, unknown>>
  boundary?: string
}

/** A deliberately small, learner-safe representation of a web source actually used in generation. */
export interface ExternalLearningSource {
  title: string
  url: string
  snippet?: string
  retrieved_at?: string
}

export interface MaterialAugmentation {
  used: boolean
  reason?: string
  sources?: ExternalLearningSource[]
}

export interface GeneratedLearningImage {
  url: string
  alt: string
  placement: 'section' | 'flashcards' | 'quiz'
  provider: string
  content_type: string
}

export interface GenerationTaskAccepted {
  generation_id: string
  status: 'queued'
  events_url: string
  result_url: string
}

/** Reconstructs the stable task transport URLs when resuming a browser session. */
export function traitTutorGenerationTaskHandle(generationId: string): GenerationTaskAccepted {
  const encoded = encodeURIComponent(generationId)
  return {
    generation_id: generationId,
    status: 'queued',
    events_url: `/api/v1/traittutor/generate/tasks/${encoded}/events`,
    result_url: `/api/v1/traittutor/generate/tasks/${encoded}`,
  }
}

export type GenerationTaskStatus =
  | 'queued'
  | 'running'
  | 'needs_review'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'interrupted'
  | 'discarded'

/** A durable task snapshot returned while a generation has no final result yet. */
export interface GenerationTaskSnapshot {
  generation_id: string
  status: Exclude<GenerationTaskStatus, 'completed'>
  error?: string
  error_code?:
    | 'model_configuration_required'
    | 'structured_output_invalid'
    | 'model_routes_exhausted'
    | 'generation_failed'
    | 'generation_interrupted'
    | 'generation_cancelled'
  retryable: boolean
  created_at: string
  updated_at: string
}

export interface GenerationProgressEvent {
  sequence: number
  type: string
  message: string
  data: Record<string, unknown>
}

export interface ProgressCalibration {
  plan_id: string
  created_at: string
  verified_observations: number
  correct_count: number
  kc_summaries: Array<{
    subject_id: string
    kc_id: string
    correct: number
    incorrect: number
  }>
  difficulty: 'smooth' | 'can_continue' | 'needs_support' | 'blocked' | null
  difficulty_reason: string
  recommended_strategy:
    | 'transfer_or_schedule_review'
    | 'self_explain_then_retrieve'
    | 'worked_example_then_guided_retry'
    | 'repair_with_contrast'
    | null
  boundary: string
}

export interface LearningPack {
  schema_version?: number
  pack_id: string
  title: string
  goal?: {
    goal_id?: string
    text: string
    status?: 'active' | 'paused' | 'completed'
    created_at?: string
    round_status?: 'completed'
    round_completed_at?: string
    [key: string]: unknown
  } | null
  sources?: Array<Record<string, unknown>>
  material: Record<string, unknown>
  materials?: LearningPackMaterial[]
  material_revision?: number
  material_dependency_state?: {
    status: 'ready' | 'needs_review'
    reason?: string
    revision?: number
  }
  profile_id?: string | null
  persona?: string | null
  /** Learn intermediate-page choice: "basic" means the learner deliberately
   *  opted out of LLM component arrangement (suppresses pending notices). */
  arrangement_preference?: 'auto' | 'basic' | null
  artifacts: Record<GenerateKind, Array<Record<string, unknown>>>
  flashcard_progress: Record<string, string>
  quiz_attempts: Array<Record<string, unknown>>
  component_plans?: LearningComponentPlan[]
  active_plan_id?: string | null
  pre_assessment?: PreAssessmentState | null
  component_progress?: Record<string, Record<string, unknown>>
  learning_evidence?: Array<Record<string, unknown>>
  calibrations?: Array<{
    question_id: string
    artifact_ref?: string
    confidence: number
    correctness: boolean
    quadrant: string
    recommended_strategy: string
  }>
  progress_calibrations?: ProgressCalibration[]
  repairs?: RepairRecord[]
  review_states?: ReviewState[]
  due_review_count?: number
  next_review_at?: string | null
  created_at: string
  updated_at: string
}

export interface LearningPackMaterial {
  material_id: string
  content_hash: string
  source_type: 'knowledge' | 'notebook' | 'upload' | 'paste'
  source_id?: string | null
  title: string
  text: string
  metadata?: Record<string, unknown>
  [key: string]: unknown
}

export interface LearningPackMaterialCapabilities {
  source_types: Array<'knowledge' | 'notebook' | 'upload' | 'paste'>
  operations: Array<'append' | 'remove' | 'reorder'>
  image_ocr: {
    available: boolean
    error_code: 'image_ocr_unavailable' | string
  }
}

export interface LearningPackMaterialRevision {
  pack_id: string
  revision: number
  operation: 'initial' | 'append' | 'replace' | 'remove' | 'reorder' | string
  material_ids: string[]
  materials: LearningPackMaterial[]
  created_at: string
  idempotent_replay: boolean
}

export interface RepairRecord {
  repair_id: string
  action_id: string
  question_id: string
  concept_id: string
  artifact_ref?: string
  user_answer?: string
  correct_rule?: string
  error_type: string
  contrast?: string
  status: 'identified' | 'explained' | 'retrying' | 'deferred' | 'repaired' | 'scheduled'
  retry_count: number
  next_review_at?: string | null
  created_at: string
  retry_prompt?: string
  retry_question_type?: string
  retry_evidence_strength?: 'strong' | 'weak'
  retry_options?: Array<{ key?: string; id?: string; text?: string }>
  last_retry_correct?: boolean
  deferred_at?: string
  reopened_at?: string
  suggested_next_component_id?: string | null
}

export interface AssessmentAttemptView {
  attempt_id: string
  component_id: string
  question_id: string
  generated_result_id: string
  user_answer: string
  confidence?: number | null
  correct: boolean
  reference_answer?: string | null
  explanation?: string | null
  submitted_at: string
  read_only: true
  historical_explanation_available: boolean
}

export interface ReviewState {
  review_id: string
  pack_id: string
  concept_id: string
  knowledge_type: 'memory' | 'concept' | 'procedure' | 'design'
  source: 'retrieval' | 'quiz' | 'repair'
  due_at: string
  priority: number
  interval_index: number
  consecutive_correct: number
  consecutive_wrong: number
  last_result?: boolean | null
  prompt?: string | null
  question_type?: string
  options?: Array<{ key?: string; id?: string; text?: string }>
}

export interface LearningComponent {
  component_id: string
  component_type: LearningComponentType
  executor: 'deterministic' | 'lesson' | 'retrieval' | 'assessment' | 'image' | 'video' | 'audio'
  label_zh: string
  label_en: string
  concept_refs: string[]
  support_dimensions: string[]
  bkt_stage: 'unobserved' | 'needs_support' | 'developing' | 'supported'
  modality: 'text' | 'interactive' | 'visual' | 'video' | 'audio'
  dependencies: string[]
  required: boolean
  reason: string
  evidence_refs: string[]
  completion_event: string
  status: 'pending' | 'active' | 'completed' | 'skipped' | 'degraded'
  output_ref?: string | null
  media_url?: string | null
  reattempt_of_component_id?: string | null
}

export interface LearningComponentPlan {
  plan_id: string
  pack_id: string
  version: number
  goal: string
  subject_ref?: { subject_id?: string; label?: string; [key: string]: unknown } | null
  analysis_id?: string | null
  support_state_snapshot: {
    subject_id?: string | null
    source: 'initial_profile' | 'subject_evidence' | 'default'
    dimensions: Record<string, Record<string, unknown>>
    boundary: string
  }
  components: LearningComponent[]
  status: 'active' | 'completed' | 'superseded'
  supersedes_plan_id?: string | null
  reattempt_of_component_id?: string | null
  reattempt_component_id?: string | null
  arrangement: 'pending' | 'llm' | 'deterministic_fallback'
  arrangement_rationale?: string | null
  created_at: string
  updated_at: string
  start_url?: string
}

export interface PreAssessmentQuestion {
  question_id: string
  concept_id: string
  concept_label: string
  question: string
  options: string[]
  /** Deprecated: the confidence picker was removed from the starting-point check. */
  confidence_scale?: Array<'低' | '中' | '高'>
}

export interface PreAssessmentState {
  assessment_id?: string | null
  status: 'pending' | 'answered' | 'skipped' | 'not_needed' | 'consumed'
  created_at?: string
  updated_at?: string
  questions?: Omit<PreAssessmentQuestion, 'confidence_scale'>[]
}

export type PreAssessmentDecision =
  | { needed: false }
  | {
      needed: true
      assessment_id: string
      questions: PreAssessmentQuestion[]
      status: Exclude<PreAssessmentState['status'], 'not_needed'>
    }

export interface PreAssessmentResult {
  assessment_id: string
  results: Array<{
    question_id: string
    correct: boolean
    confidence: '低' | '中' | '高' | null
    rationale: string
  }>
  idempotent_replay: boolean
}

export type ArrangedLearningComponentPlan = LearningComponentPlan & {
  fallback: boolean
  fallback_message?: string
  idempotent_replay: boolean
}

export class TraitTutorApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly detail?: unknown
  ) {
    super(message)
    this.name = 'TraitTutorApiError'
  }
}

async function expectJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const error = await parseApiError(response)
    throw new TraitTutorApiError(error.message, response.status, error.code, error.detail)
  }
  return (await response.json()) as T
}

export interface LearningPackCreateInput {
  title: string
  material?: Record<string, unknown>
  profile_id?: string
  goal?: Record<string, unknown> | string
  sources?: Array<Record<string, unknown>>
}

// The plain POST /api/v1/learning-packs create endpoint is legacy: Learn must
// go through the atomic create-with-plan path (the e2e suite treats the plain
// endpoint as a must-not-use sentinel). createLearningPack was removed because
// it had no consumers; the legacy endpoint itself remains server-side for
// other clients.
export async function createLearningPackWithPlan(
  input: LearningPackCreateInput & {
    idempotency_key: string
    plan?: Parameters<typeof createLearningComponentPlan>[1]
  }
): Promise<{ pack: LearningPack; plan: LearningComponentPlan }> {
  const response = await apiFetch(apiUrl('/api/v1/learning-packs/with-plan'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return expectJson(response)
}

export async function listLearningPacks(): Promise<LearningPack[]> {
  const response = await apiFetch(apiUrl('/api/v1/learning-packs'), { cache: 'no-store' })
  const data = await expectJson<{ packs: LearningPack[] }>(response)
  const uniquePacks = new Map<string, LearningPack>()
  for (const pack of data.packs ?? []) {
    const existing = uniquePacks.get(pack.pack_id)
    // The API normally returns newest-first. Keep the newer record as a
    // defensive boundary in case a migrated store contains duplicate IDs.
    if (!existing || pack.updated_at > existing.updated_at) {
      uniquePacks.set(pack.pack_id, pack)
    }
  }
  return [...uniquePacks.values()]
}

export async function getLearningPack(packId: string): Promise<LearningPack> {
  const response = await apiFetch(apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}`), {
    cache: 'no-store',
  })
  return expectJson<LearningPack>(response)
}

export async function getLearningPackMaterialCapabilities(): Promise<LearningPackMaterialCapabilities> {
  const response = await apiFetch(apiUrl('/api/v1/learning-packs/materials/capabilities'), {
    cache: 'no-store',
  })
  const data = await expectJson<Partial<LearningPackMaterialCapabilities>>(response)
  if (
    !Array.isArray(data.source_types) ||
    !Array.isArray(data.operations) ||
    !data.image_ocr ||
    typeof data.image_ocr.available !== 'boolean' ||
    typeof data.image_ocr.error_code !== 'string'
  ) {
    throw new Error('Material capability response is invalid')
  }
  return data as LearningPackMaterialCapabilities
}

export async function appendLearningPackMaterial(
  packId: string,
  input: {
    expected_revision: number
    idempotency_key: string
    material: Omit<LearningPackMaterial, 'material_id' | 'content_hash'>
  }
): Promise<LearningPackMaterialRevision> {
  const response = await apiFetch(
    apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/materials`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }
  )
  return expectJson<LearningPackMaterialRevision>(response)
}

export async function removeLearningPackMaterial(
  packId: string,
  materialId: string,
  input: { expected_revision: number; idempotency_key: string }
): Promise<LearningPackMaterialRevision> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/materials/${encodeURIComponent(materialId)}`
    ),
    {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }
  )
  return expectJson<LearningPackMaterialRevision>(response)
}

export async function reorderLearningPackMaterials(
  packId: string,
  input: { expected_revision: number; idempotency_key: string; material_ids: string[] }
): Promise<LearningPackMaterialRevision> {
  const response = await apiFetch(
    apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/materials/reorder`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }
  )
  return expectJson<LearningPackMaterialRevision>(response)
}

export async function getLearningPackMaterialRevision(
  packId: string,
  revision: number
): Promise<LearningPackMaterialRevision> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/materials/revisions/${encodeURIComponent(String(revision))}`
    ),
    { cache: 'no-store' }
  )
  return expectJson<LearningPackMaterialRevision>(response)
}

export async function deleteLearningPack(packId: string): Promise<{ deleted_id: string }> {
  const response = await apiFetch(apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}`), {
    method: 'DELETE',
  })
  return expectJson(response)
}

export async function deleteLearningPacks(packIds: string[]): Promise<{
  deleted_ids: string[]
  missing_ids: string[]
  deleted_count: number
}> {
  const response = await apiFetch(apiUrl('/api/v1/learning-packs'), {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pack_ids: packIds }),
  })
  return expectJson(response)
}

/** Look up the Pack linked to a durable Learn chat session without pagination. */
export async function getLearningPackForSession(sessionId: string): Promise<LearningPack | null> {
  const response = await apiFetch(
    apiUrl(`/api/v1/learning-packs/by-session/${encodeURIComponent(sessionId)}`),
    { cache: 'no-store' }
  )
  if (response.status === 404) return null
  return expectJson<LearningPack>(response)
}

export async function createLearningComponentPlan(
  packId: string,
  input: {
    instruction?: string
    preferred_modalities?: Array<'text' | 'visual' | 'audio' | 'interactive'>
    accessibility?: Record<string, unknown>
    supersedes_plan_id?: string
  } = {}
): Promise<LearningComponentPlan> {
  const response = await apiFetch(
    apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/plans`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }
  )
  return expectJson<LearningComponentPlan>(response)
}

export async function getLearningComponentPlan(
  packId: string,
  planId: string
): Promise<LearningComponentPlan> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/plans/${encodeURIComponent(planId)}`
    ),
    { cache: 'no-store' }
  )
  return expectJson<LearningComponentPlan>(response)
}

export async function judgeAndGeneratePreAssessment(
  packId: string
): Promise<PreAssessmentDecision> {
  const response = await apiFetch(
    apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/pre-assessment`),
    { method: 'POST' }
  )
  return expectJson<PreAssessmentDecision>(response)
}

export async function submitPreAssessment(
  packId: string,
  assessmentId: string,
  answers: Array<{
    question_id: string
    selected_index: number
  }>,
  eventId?: string
): Promise<PreAssessmentResult> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/pre-assessment/${encodeURIComponent(assessmentId)}/submit`
    ),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers, event_id: eventId }),
    }
  )
  return expectJson<PreAssessmentResult>(response)
}

export async function skipPreAssessment(
  packId: string,
  assessmentId: string
): Promise<{ assessment_id: string; status: 'skipped' }> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/pre-assessment/${encodeURIComponent(assessmentId)}/skip`
    ),
    { method: 'POST' }
  )
  return expectJson(response)
}

export async function arrangeLearningComponentPlan(
  packId: string
): Promise<ArrangedLearningComponentPlan> {
  const response = await apiFetch(
    apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/plans/arrange`),
    { method: 'POST' }
  )
  return expectJson<ArrangedLearningComponentPlan>(response)
}

export async function listLearningAssessmentAttempts(
  packId: string,
  planId: string,
  input: { limit?: number; offset?: number } = {}
): Promise<{ items: AssessmentAttemptView[]; total: number; limit: number; offset: number }> {
  const params = new URLSearchParams()
  if (input.limit !== undefined) params.set('limit', String(input.limit))
  if (input.offset !== undefined) params.set('offset', String(input.offset))
  const query = params.toString()
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/plans/${encodeURIComponent(planId)}/attempts${query ? `?${query}` : ''}`
    ),
    { cache: 'no-store' }
  )
  return expectJson(response)
}

export async function getDueLearningReviews(
  packId: string
): Promise<{ items: ReviewState[]; total: number; estimated_minutes: number }> {
  const response = await apiFetch(
    apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}/reviews/due`),
    { cache: 'no-store' }
  )
  return expectJson(response)
}

export async function revealLearningReviewAnswer(
  packId: string,
  reviewId: string
): Promise<{ review_id: string; answer: string }> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/reviews/${encodeURIComponent(reviewId)}/reveal`
    ),
    { method: 'POST' }
  )
  return expectJson(response)
}

export async function getLearningRepair(packId: string, repairId: string): Promise<RepairRecord> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/repairs/${encodeURIComponent(repairId)}`
    ),
    { cache: 'no-store' }
  )
  return expectJson(response)
}

export async function recordLearningReviewResult(
  packId: string,
  reviewId: string,
  result: { event_id: string; answer?: string; rating?: 'known' | 'uncertain' | 'unknown' }
): Promise<{
  accepted: boolean
  verified: boolean
  correct?: boolean | null
  review: ReviewState
  next_review_at?: string
}> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/reviews/${encodeURIComponent(reviewId)}/result`
    ),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(result),
    }
  )
  return expectJson(response)
}

export async function retryLearningRepair(
  packId: string,
  repairId: string,
  input: { event_id: string; answer: string }
): Promise<{
  accepted: boolean
  verified_correct: boolean
  repair: RepairRecord
  next_review_at?: string
  recovery: {
    deferred: boolean
    suggested_next_component_id?: string | null
  }
  evidence_strength: 'strong' | 'weak'
}> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/repairs/${encodeURIComponent(repairId)}/retry`
    ),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }
  )
  return expectJson(response)
}

export async function recordLearningComponentEvent(
  packId: string,
  planId: string,
  componentId: string,
  event: {
    event_id?: string
    action: 'start' | 'complete' | 'skip' | 'retry' | 'degrade' | 'feedback'
    observation?: 'correct' | 'incorrect' | 'known' | 'uncertain' | 'unknown'
    confidence?: number
    question_id?: string
    answer?: string
    concept_id?: string
    concept_label?: string
    output_ref?: string
    media_url?: string
    feedback?: string
    replan?: boolean
  }
): Promise<{
  component: LearningComponent
  learner_state_updated: boolean
  replanned_plan?: LearningComponentPlan | null
  verified_observation?: 'correct' | 'incorrect' | null
  verified_feedback?: string | null
  calibration?: {
    question_id: string
    artifact_ref: string
    confidence: number
    correctness: boolean
    quadrant: string
    recommended_strategy: string
  } | null
  progress_calibration?: ProgressCalibration | null
  created_repair_id?: string | null
}> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/learning-packs/${encodeURIComponent(packId)}/plans/${encodeURIComponent(planId)}/components/${encodeURIComponent(componentId)}/events`
    ),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(event),
    }
  )
  return expectJson(response)
}

export async function updateLearningPack(
  packId: string,
  patch: Record<string, unknown>
): Promise<LearningPack> {
  const response = await apiFetch(apiUrl(`/api/v1/learning-packs/${encodeURIComponent(packId)}`), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return expectJson<LearningPack>(response)
}

export async function fetchTraitQuestions(): Promise<TraitQuestionsResponse> {
  const response = await apiFetch(apiUrl('/api/v1/traittutor/profile/questions'), {
    cache: 'no-store',
  })
  return expectJson<TraitQuestionsResponse>(response)
}

export async function createTraitProfile(answers: Record<string, number>): Promise<TraitProfile> {
  const response = await apiFetch(apiUrl('/api/v1/traittutor/profile/profiles'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers }),
  })
  return expectJson<TraitProfile>(response)
}

export async function listTraitProfiles(): Promise<TraitProfile[]> {
  const response = await apiFetch(apiUrl('/api/v1/traittutor/profile/profiles'), {
    cache: 'no-store',
  })
  const data = await expectJson<{ profiles: TraitProfile[] }>(response)
  return data.profiles ?? []
}

export async function deleteTraitProfile(profileId: string): Promise<void> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/profile/profiles/${encodeURIComponent(profileId)}`),
    { method: 'DELETE' }
  )
  await expectJson<{ status: string }>(response)
}

export type PreparedLearningMaterial = {
  source_type: 'upload'
  source_id: string
  title: string
  text: string
  metadata: Record<string, unknown> & {
    filename?: string
    mime_type?: string
    converted_to_pdf?: boolean
    page_count?: number
    page_slices?: Array<{ page: number; text: string }>
  }
}

export type MaterialAnalysis = {
  analysis_id: string
  session_id: string
  owner_id: string
  source_id: string
  version: number
  subject: string
  sub_subject: string
  chinese_grade: string
  international_grade: string
  difficulty: string
  confidence: number
  evidence: Array<{ chunk_id: string; page?: number; excerpt: string; source_id: string }>
  /** Retired duplicate field. Canonical responses must never populate it. */
  page_evidence?: never
  concept_candidates: Array<Record<string, unknown>>
  augmentation_needed: boolean
  augmentation_reason: string
  component_affordances: Record<
    string,
    { suitable?: boolean; confidence?: number; reasons?: string[] }
  >
  language: string | null
  language_confidence: number | null
  created_at: string
  trace: Record<string, unknown>
}

/** Prepare an uploaded learning document and return page-scoped model material. */
export async function prepareTraitTutorMaterial(file: File): Promise<PreparedLearningMaterial> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
  const response = await apiFetch(apiUrl('/api/v1/traittutor/generate/materials/prepare'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      filename: file.name,
      mime_type: file.type || '',
      base64: dataUrl.split(',')[1] || '',
    }),
  })
  return expectJson<PreparedLearningMaterial>(response)
}

export async function analyzeTraitTutorMaterial(input: {
  session_id: string
  material: {
    source_type: 'knowledge' | 'notebook' | 'upload' | 'paste'
    title: string
    text: string
    source_id?: string | null
    metadata?: Record<string, unknown>
  }
}): Promise<MaterialAnalysis> {
  const response = await apiFetch(apiUrl('/api/v1/traittutor/generate/materials/analyze'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return expectJson<MaterialAnalysis>(response)
}

export async function createTraitTutorGenerationTask(input: {
  generation_type: GenerateKind
  material: {
    source_type: 'knowledge' | 'notebook' | 'upload' | 'paste'
    title: string
    text: string
    source_id?: string | null
    metadata?: Record<string, unknown>
  }
  learner_profile?: Partial<TraitProfile> | Record<string, unknown>
  options?: Record<string, unknown>
}): Promise<GenerationTaskAccepted> {
  const response = await apiFetch(apiUrl('/api/v1/traittutor/generate/tasks'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  return expectJson<GenerationTaskAccepted>(response)
}

export async function getTraitTutorGenerationTask(
  generationId: string
): Promise<GenerateSuiteResult | GenerationTaskSnapshot> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}`),
    { cache: 'no-store' }
  )
  return expectJson<GenerateSuiteResult | GenerationTaskSnapshot>(response)
}

/** Grade a standalone Quiz answer without returning the server-held answer key. */
export async function gradeTraitTutorGenerationQuizAnswer(
  generationId: string,
  input: { question_id: string; answer: string; attempt_id: string }
): Promise<{ question_id: string; correct: boolean; explanation: string; attempt_id?: string }> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}/quiz/grade`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }
  )
  return expectJson<{
    question_id: string
    correct: boolean
    explanation: string
    attempt_id?: string
  }>(response)
}

/** Reveal one released flashcard answer after the learner explicitly flips it. */
export async function revealTraitTutorGenerationFlashcard(
  generationId: string,
  cardId: string
): Promise<{ card_id: string; answer: string }> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}/flashcards/${encodeURIComponent(cardId)}/reveal`
    ),
    { method: 'POST' }
  )
  return expectJson(response)
}

export async function cancelTraitTutorGenerationTask(
  generationId: string
): Promise<Pick<GenerationTaskSnapshot, 'generation_id' | 'status'>> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}`),
    { method: 'DELETE' }
  )
  return expectJson<Pick<GenerationTaskSnapshot, 'generation_id' | 'status'>>(response)
}

export async function retryTraitTutorGenerationTask(
  generationId: string
): Promise<GenerationTaskAccepted> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}/retry`),
    { method: 'POST' }
  )
  return expectJson<GenerationTaskAccepted>(response)
}

/** Confirm a quality-gated artifact before it is attached to a pack or notebook. */
export async function confirmTraitTutorGenerationReview(
  generationId: string
): Promise<GenerateSuiteResult> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}/review/confirm`),
    { method: 'POST' }
  )
  return expectJson<GenerateSuiteResult>(response)
}

/** Discard a quality-gated artifact while retaining server-side audit history. */
export async function discardTraitTutorGenerationReview(
  generationId: string
): Promise<Pick<GenerationTaskSnapshot, 'generation_id' | 'status'>> {
  const response = await apiFetch(
    apiUrl(`/api/v1/traittutor/generate/tasks/${encodeURIComponent(generationId)}/review/discard`),
    { method: 'POST' }
  )
  return expectJson<Pick<GenerationTaskSnapshot, 'generation_id' | 'status'>>(response)
}

/** Never display provider payloads, quota codes, or credentials to learners. */
export function generationErrorMessage(error: unknown, zh = true): string {
  const message = error instanceof Error ? error.message : String(error || '')
  const lower = message.toLowerCase()
  if (lower.includes('structured_output_invalid')) {
    return zh
      ? '生成内容未通过质量校验，已自动重试；仍失败请再试一次。'
      : 'The generated content did not pass quality validation after an automatic retry. Please try again.'
  }
  if (
    lower.includes('model_configuration_required') ||
    lower.includes('no generation model') ||
    lower.includes('configure a generation model')
  ) {
    return zh
      ? '尚未配置可用模型，请先在模型设置中完成配置。'
      : 'No generation model is configured. Open Model settings to continue.'
  }
  if (
    lower.includes('model_routes_exhausted') ||
    lower.includes('rate limit') ||
    lower.includes('quota') ||
    lower.includes('1308') ||
    lower.includes('temporarily unavailable')
  ) {
    return zh
      ? '当前模型额度或服务暂不可用，已自动尝试备用模型。请稍后重新生成。'
      : 'Model capacity is temporarily unavailable. Backup models were tried; please retry later.'
  }
  return zh
    ? '生成未完成，请重试。系统会自动切换到可用模型。'
    : 'Generation was not completed. Retry to automatically use another available model.'
}

export function subscribeTraitTutorGeneration(
  task: GenerationTaskAccepted,
  onEvent: (event: GenerationProgressEvent) => void,
  onError: () => void,
  options: { afterSequence?: number } = {}
): () => void {
  let closed = false
  let stream: EventSource | null = null
  let reconnectTimer: number | null = null
  let lastSequence = options.afterSequence ?? 0

  const connect = () => {
    if (closed) return
    const separator = task.events_url.includes('?') ? '&' : '?'
    // EventSource cannot set Last-Event-ID explicitly. The durable server
    // contract accepts after_seq, so every reconnect gets an exact replay.
    stream = new EventSource(
      apiUrl(`${task.events_url}${separator}after_seq=${encodeURIComponent(String(lastSequence))}`)
    )
    for (const type of [
      'accepted',
      'material_resolved',
      'profile_strategy_ready',
      'generation_started',
      'batch_validated',
      'evaluation_completed',
      'needs_review',
      'completed',
      'failed',
      'cancelled',
      'interrupted',
      'retry_queued',
    ]) {
      stream.addEventListener(type, event => {
        try {
          const parsed = JSON.parse((event as MessageEvent<string>).data) as GenerationProgressEvent
          lastSequence = Math.max(lastSequence, parsed.sequence || 0)
          onEvent(parsed)
        } catch {
          onError()
        }
        if (['needs_review', 'completed', 'failed', 'cancelled', 'interrupted'].includes(type))
          stream?.close()
      })
    }
    stream.onerror = () => {
      if (closed) return
      stream?.close()
      onError()
      reconnectTimer = window.setTimeout(connect, 750)
    }
  }
  connect()
  return () => {
    closed = true
    stream?.close()
    if (reconnectTimer) window.clearTimeout(reconnectTimer)
  }
}

async function ensureTraitTutorNotebook(): Promise<NotebookSummary> {
  const notebooks = await listNotebooks()
  const existing = notebooks.find(notebook => notebook.name === 'TraitTutor')
  if (existing) return existing
  return createNotebook({
    name: 'TraitTutor',
    description: 'TraitTutor generated courseware and flashcards',
    color: '#0F766E',
    icon: 'brain',
  })
}

export async function saveGenerationResult(result: GenerateSuiteResult): Promise<string> {
  if (result.result.save_target === 'question_bank') {
    const items = result.result.items ?? []
    for (const item of items) {
      const optionsArray = Array.isArray(item.options) ? item.options : []
      const options = Object.fromEntries(
        optionsArray.map((option, index) => [
          String.fromCharCode(65 + index),
          String((option as { text?: unknown }).text ?? ''),
        ])
      )
      await upsertNotebookEntry({
        session_id: `traittutor-${result.generation_id}`,
        turn_id: result.generation_id,
        question_id: String(item.question_id ?? crypto.randomUUID()),
        question: String(item.question ?? ''),
        question_type: String(item.question_type ?? ''),
        difficulty: String(item.difficulty ?? ''),
        options,
        correct_answer: String(item.correct_answer ?? ''),
        explanation: String(item.explanation ?? ''),
      })
    }
    return 'question_bank'
  }

  const notebook = await ensureTraitTutorNotebook()
  const output =
    result.result.markdown ||
    JSON.stringify(result.result.items ?? result.result.sections ?? [], null, 2)
  const visualMarkdown = (result.result.images ?? [])
    .filter(image => typeof image.url === 'string')
    .map(image => `![${image.alt || 'Learning illustration'}](${image.url})`)
    .join('\n\n')
  const response = await apiFetch(apiUrl('/api/v1/notebook/add_record'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      notebook_ids: [notebook.id],
      record_type: 'chat',
      title: result.result.title,
      summary: result.generation_type,
      user_query: 'TraitTutor Generate Suite',
      output: visualMarkdown ? `${output}\n\n${visualMarkdown}` : output,
      metadata: {
        source: 'traittutor',
        generation_id: result.generation_id,
        generation_type: result.generation_type,
      },
    }),
  })
  await expectJson<{ success: boolean }>(response)
  return notebook.id
}
