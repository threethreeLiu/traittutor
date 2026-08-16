import { apiFetch, apiUrl, parseApiError } from '@/lib/api'
import { appPath } from '@/lib/base-path'
import { invalidateClientCache, withClientCache } from '@/lib/client-cache'
import type { LLMSelection, StreamEvent } from '@/lib/unified-ws'

export interface SessionMessage {
  id: number
  session_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  capability?: string
  events: StreamEvent[]
  attachments: Array<{
    type: string
    filename?: string
    base64?: string
    url?: string
    mime_type?: string
    id?: string
    extracted_text?: string
    generated?: boolean
    size_bytes?: number
  }>
  metadata?: Record<string, unknown>
  created_at: number
  /** Edit-branching: id of the message this row continues. `null` for the
   *  first message in a session. Siblings share the same parent. */
  parent_message_id?: number | null
}

export interface SessionSummary {
  id: string
  session_id: string
  title: string
  created_at: number
  updated_at: number
  message_count: number
  last_message: string
  mode?: 'learn' | 'assist'
  status?: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled' | 'rejected'
  active_turn_id?: string
  preferences?: {
    capability?: string
    tools?: string[]
    knowledge_bases?: string[]
    language?: string
    llm_selection?: LLMSelection | null
    /** Session-level persona preference; "" / absent = Default (no persona). */
    persona?: string
    /** Edit-branching: maps a parent_message_id → the child id currently
     *  shown at that branch point. Missing keys default to the latest
     *  sibling (most recently created child). */
    selected_branches?: Record<string, number>
  }
}

export interface ActiveTurnSummary {
  id: string
  turn_id: string
  session_id: string
  capability: string
  status: 'running' | 'completed' | 'failed' | 'cancelled' | 'rejected'
  error: string
  created_at: number
  updated_at: number
  mode?: 'learn' | 'assist'
  finished_at?: number | null
  last_seq: number
}

export interface SessionDetail {
  id: string
  session_id: string
  title: string
  created_at: number
  updated_at: number
  status?: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled' | 'rejected'
  active_turn_id?: string
  compressed_summary?: string
  summary_up_to_msg_id?: number
  preferences?: {
    capability?: string
    tools?: string[]
    knowledge_bases?: string[]
    language?: string
    llm_selection?: LLMSelection | null
    /** Session-level persona preference; "" / absent = Default (no persona). */
    persona?: string
    /** Edit-branching: maps a parent_message_id → the child id currently
     *  shown at that branch point. Missing keys default to the latest
     *  sibling (most recently created child). */
    selected_branches?: Record<string, number>
  }
  messages: SessionMessage[]
  active_turns?: ActiveTurnSummary[]
}

export interface QuizResultItem {
  question_id: string
  answer: string
  attempt_id: string
}

export interface ServerQuizGradeResult {
  question_id: string
  attempt_id: string
  correct: boolean
  explanation: string
  entry_id: number | null
}

export type ServerQuizAnswerImage = {
  id?: string
  base64?: string | null
  url?: string | null
  filename: string
  mime_type: string
}

export class SessionApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly detail?: unknown
  ) {
    super(message)
    this.name = 'SessionApiError'
  }
}

async function expectJson<T>(response: Response): Promise<T> {
  if (response.status === 401 && typeof window !== 'undefined') {
    const next = encodeURIComponent(window.location.pathname)
    window.location.href = appPath(`/login?next=${next}`)
    return new Promise(() => {})
  }
  if (!response.ok) {
    const error = await parseApiError(response)
    throw new SessionApiError(error.message, response.status, error.code, error.detail)
  }
  return response.json() as Promise<T>
}

export async function listSessions(
  limit = 50,
  offset = 0,
  options?: { force?: boolean; mode?: 'learn' | 'assist' }
): Promise<SessionSummary[]> {
  const qs = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  if (options?.mode) qs.set('mode', options.mode)
  return withClientCache<SessionSummary[]>(
    `sessions:${limit}:${offset}:${options?.mode ?? 'all'}`,
    async () => {
      const response = await apiFetch(apiUrl(`/api/v1/sessions?${qs.toString()}`), {
        cache: 'no-store',
      })
      const data = await expectJson<{ sessions: SessionSummary[] }>(response)
      return data.sessions ?? []
    },
    {
      force: options?.force,
      ttlMs: 15_000,
    }
  )
}

export async function getSession(sessionId: string, signal?: AbortSignal): Promise<SessionDetail> {
  const response = await apiFetch(apiUrl(`/api/v1/sessions/${sessionId}`), {
    cache: 'no-store',
    signal,
  })
  return expectJson<SessionDetail>(response)
}

/** Reserve a durable Learn session before creating a path from a goal/upload. */
export async function createLearningSession(title: string): Promise<SessionDetail> {
  const response = await apiFetch(apiUrl('/api/v1/sessions'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title.trim().slice(0, 100) || 'My learning path' }),
  })
  const data = await expectJson<{ session: SessionDetail }>(response)
  invalidateClientCache('sessions:')
  return data.session
}

export async function updateSessionTitle(sessionId: string, title: string): Promise<SessionDetail> {
  const response = await apiFetch(apiUrl(`/api/v1/sessions/${sessionId}`), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  const data = await expectJson<{ session: SessionDetail }>(response)
  invalidateClientCache('sessions:')
  return data.session
}

export interface SessionDeleteResult {
  deleted: boolean
  session_id?: string
  /** Packs whose primary material linked to this Learn session and were
   *  removed by the same delete (server-side cascade). */
  deleted_pack_ids?: string[]
}

export async function deleteSession(sessionId: string): Promise<SessionDeleteResult> {
  const response = await apiFetch(apiUrl(`/api/v1/sessions/${sessionId}`), {
    method: 'DELETE',
  })
  const result = await expectJson<SessionDeleteResult>(response)
  invalidateClientCache('sessions:')
  return result
}

export async function recordQuizResults(
  sessionId: string,
  answers: QuizResultItem[],
  turnId?: string | null
): Promise<void> {
  const response = await apiFetch(apiUrl(`/api/v1/sessions/${sessionId}/quiz-results`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answers, turn_id: turnId || '' }),
  })
  await expectJson<{ recorded: boolean }>(response)
}

/**
 * Grade one deep-question attempt from its server-held definition.  The
 * browser sends only identity + learner answer, never answer keys or a
 * browser-computed verdict.
 */
export async function gradeServerQuizAnswer(
  sessionId: string,
  turnId: string,
  submission: QuizResultItem & {
    user_answer_images?: ServerQuizAnswerImage[]
  }
): Promise<ServerQuizGradeResult> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/quiz/grade?turn_id=${encodeURIComponent(turnId)}`
    ),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(submission),
    }
  )
  return expectJson<ServerQuizGradeResult>(response)
}

export async function deleteMessage(sessionId: string, messageId: number): Promise<void> {
  const response = await apiFetch(apiUrl(`/api/v1/sessions/${sessionId}/messages/${messageId}`), {
    method: 'DELETE',
  })
  await expectJson<{ deleted: boolean }>(response)
}

export async function updateBranchSelection(
  sessionId: string,
  selectedBranches: Record<string, number>
): Promise<void> {
  const response = await apiFetch(apiUrl(`/api/v1/sessions/${sessionId}/branch-selection`), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ selected_branches: selectedBranches }),
  })
  await expectJson<{ selected_branches: Record<string, number> }>(response)
}
