import { apiFetch, apiUrl } from '@/lib/api'

export type GenerationRunStatus = 'succeeded' | 'degraded' | 'failed'
export type GenerationRunTraceAvailability = 'available' | 'unavailable'
export type GenerationRunTaskStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'degraded'

export interface GenerationRunSafeInputRef {
  kind: string
  ref_id: string
}

export interface GenerationRunTaskTrace {
  task_id: string
  task_type: string
  status: GenerationRunTaskStatus
  depends_on: string[]
  input_refs: GenerationRunSafeInputRef[]
  redacted_input_ref_count: number
  failure_code: string | null
  degradation_codes: string[]
}

export interface GenerationRunBudgetTrace {
  total_planned_budget_ms: number | null
  total_timeout_ms: number | null
  total_retry_limit: number | null
  elapsed_ms: number | null
  timing_status: GenerationRunTraceAvailability
}

export interface GenerationRunValidationTrace {
  status: 'passed' | 'repair' | 'degraded' | 'failed' | 'unavailable'
  finding_count: number
  category_codes: string[]
  offending_task_ids: string[]
}

export interface GenerationRunTrace {
  run_id: string
  generation_run_id: string
  graph_id: string
  status: GenerationRunStatus
  graph_status: GenerationRunTraceAvailability
  graph_version: string | null
  created_at: string | null
  page_schema_id: string
  nodes: GenerationRunTaskTrace[]
  budget: GenerationRunBudgetTrace
  validation: GenerationRunValidationTrace
  degradation_codes: string[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isFinite(value))
}

function isTaskTrace(value: unknown): value is GenerationRunTaskTrace {
  if (!isRecord(value)) return false
  return (
    typeof value.task_id === 'string' &&
    typeof value.task_type === 'string' &&
    ['pending', 'running', 'succeeded', 'failed', 'degraded'].includes(String(value.status)) &&
    isStringArray(value.depends_on) &&
    Array.isArray(value.input_refs) &&
    value.input_refs.every(
      ref => isRecord(ref) && typeof ref.kind === 'string' && typeof ref.ref_id === 'string'
    ) &&
    typeof value.redacted_input_ref_count === 'number' &&
    (value.failure_code === null || typeof value.failure_code === 'string') &&
    isStringArray(value.degradation_codes)
  )
}

function isGenerationRunTrace(value: unknown): value is GenerationRunTrace {
  if (!isRecord(value) || !isRecord(value.budget) || !isRecord(value.validation)) return false
  return (
    typeof value.run_id === 'string' &&
    typeof value.generation_run_id === 'string' &&
    typeof value.graph_id === 'string' &&
    ['succeeded', 'degraded', 'failed'].includes(String(value.status)) &&
    ['available', 'unavailable'].includes(String(value.graph_status)) &&
    (value.graph_version === null || typeof value.graph_version === 'string') &&
    (value.created_at === null || typeof value.created_at === 'string') &&
    typeof value.page_schema_id === 'string' &&
    Array.isArray(value.nodes) &&
    value.nodes.every(isTaskTrace) &&
    isNullableNumber(value.budget.total_planned_budget_ms) &&
    isNullableNumber(value.budget.total_timeout_ms) &&
    isNullableNumber(value.budget.total_retry_limit) &&
    isNullableNumber(value.budget.elapsed_ms) &&
    ['available', 'unavailable'].includes(String(value.budget.timing_status)) &&
    ['passed', 'repair', 'degraded', 'failed', 'unavailable'].includes(
      String(value.validation.status)
    ) &&
    typeof value.validation.finding_count === 'number' &&
    isStringArray(value.validation.category_codes) &&
    isStringArray(value.validation.offending_task_ids) &&
    isStringArray(value.degradation_codes)
  )
}

async function json(response: Response): Promise<GenerationRunTrace> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(payload.detail || `Request failed: ${response.status}`)
  }
  const payload: unknown = await response.json()
  if (!isGenerationRunTrace(payload)) throw new Error('Invalid generation run trace')
  return payload
}

export function getGenerationRunTrace(
  generationRunId: string,
  signal?: AbortSignal
): Promise<GenerationRunTrace> {
  return apiFetch(
    apiUrl(`/api/v1/generation-runs/${encodeURIComponent(generationRunId)}/trace`),
    { cache: 'no-store', signal }
  ).then(json)
}
