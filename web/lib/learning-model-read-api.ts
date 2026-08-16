import { apiFetch, apiUrl } from '@/lib/api'

export type LearningModelSectionStatus =
  | 'ready'
  | 'empty'
  | 'unavailable'
  | 'stale'
  | 'rebuilding'

export interface LearningModelSectionMeta {
  status: LearningModelSectionStatus
  updated_at?: string | null
  source_refs: string[]
  unavailable_sources: string[]
}

export interface LearningModelTodaySummary {
  meta: LearningModelSectionMeta
  active_subject_count: number
  due_review_count: number
  open_error_count: number
  attribution_pending_count: number
  latest_activity_at?: string | null
}

export interface LearningModelSubjectSummary {
  subject_id: string
  label: string
  last_activity_at?: string | null
  covered_kc_count: number
  strong_evidence_count: number
  open_error_count: number
  due_review_count: number
  source_refs: string[]
}

export interface LearningModelSubjectsSection {
  meta: LearningModelSectionMeta
  items: LearningModelSubjectSummary[]
}

export interface LearningModelPendingSubject {
  subject_id: string
  label: string
  created_at?: string | null
  source_refs: string[]
  possible_duplicate_subject_ids: string[]
}

export interface LearningModelPendingSubjectsSection {
  meta: LearningModelSectionMeta
  items: LearningModelPendingSubject[]
}

export type LearningModelTaskKind = 'review' | 'error_repair' | 'attribution'

export interface LearningModelTask {
  task_id: string
  subject_id: string
  kind: LearningModelTaskKind
  due_at?: string | null
  source_refs: string[]
}

export interface LearningModelTaskQueue {
  meta: LearningModelSectionMeta
  items: LearningModelTask[]
}

export interface LearningModelSupportSummary {
  meta: LearningModelSectionMeta
  inference_enabled?: boolean | null
  confirmed_preference_count: number
  confirmed_reflection_count: number
  compass_signal_count: number
}

export interface LearningModelOverview {
  generated_at: string
  today: LearningModelTodaySummary
  confirmed_subjects: LearningModelSubjectsSection
  pending_subjects: LearningModelPendingSubjectsSection
  task_queue: LearningModelTaskQueue
  support: LearningModelSupportSummary
}

export interface LearningModelSubjectDetail {
  generated_at: string
  header: {
    subject_id: string
    label: string
    confirmed: boolean
    updated_at?: string | null
    data_status: LearningModelSectionStatus
  }
  tabs: Record<
    'overview' | 'knowledge' | 'errors' | 'reviews' | 'misconceptions' | 'support' | 'governance',
    {
      meta: LearningModelSectionMeta
      item_count: number
      actionable_count: number
      mastery_items?: Array<{
        kc_id: string
        evidence_state: 'insufficient_evidence' | 'needs_support' | 'developing' | 'supported'
        change_signal: 'none' | 'needs_review' | 'repaired' | 'due_for_review'
        verified_observation_count: number
        model_version?: string | null
        stage_policy_version: string
      }>
      model_version?: string | null
      mapping_version?: string | null
    }
  >
  allowed_actions: Array<
    | 'continue_learning'
    | 'start_review'
    | 'repair_error'
    | 'confirm_subject'
    | 'correct_subject'
    | 'view_evidence'
  >
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(payload.detail || `Request failed: ${response.status}`)
  }
  return response.json() as Promise<T>
}

export function getLearningModelOverview(): Promise<LearningModelOverview> {
  return apiFetch(apiUrl('/api/v1/learning-model/overview'), { cache: 'no-store' }).then(
    json<LearningModelOverview>
  )
}

export function getLearningModelSubjectDetail(subjectId: string): Promise<LearningModelSubjectDetail> {
  return apiFetch(
    apiUrl(`/api/v1/learning-model/subjects/${encodeURIComponent(subjectId)}`),
    { cache: 'no-store' }
  ).then(json<LearningModelSubjectDetail>)
}
