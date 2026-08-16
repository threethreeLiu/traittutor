'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowLeft, MessageCircle, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import ResearchBriefEditor from '@/components/research/ResearchBriefEditor'
import ResearchAssistant from '@/components/research/ResearchAssistant'
import ResearchEvidencePanel from '@/components/research/ResearchEvidencePanel'
import ResearchRunPanel from '@/components/research/ResearchRunPanel'
import {
  ResearchApiError,
  applyResearchRunAction,
  createResearchNote,
  continueResearchRun,
  getResearchWorkspace,
  invalidateResearchSource,
  saveResearchBrief,
  startResearchRun,
  type ResearchRun,
  type ResearchReport,
  type ResearchRunAction,
  type ResearchSource,
  type ResearchWorkspaceDetail,
  type SaveResearchBriefInput,
} from '@/lib/research-workspace-api'

type Copy = { zh: string; en: string }

const POLLABLE_STATES = new Set(['queued', 'running', 'pausing', 'cancelling'])

export default function ResearchWorkspaceApp({ workspaceId }: { workspaceId: string }) {
  const { i18n } = useTranslation()
  const zh = i18n.language.toLowerCase().startsWith('zh')
  const tr = useCallback((copy: Copy) => (zh ? copy.zh : copy.en), [zh])
  const [detail, setDetail] = useState<ResearchWorkspaceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [contractBlocked, setContractBlocked] = useState(false)
  const [error, setError] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [assistantDrawerOpen, setAssistantDrawerOpen] = useState(false)
  const [assistantMinimized, setAssistantMinimized] = useState(false)
  const mutationAlertRef = useRef<HTMLDivElement>(null)
  const assistantDrawerRef = useRef<HTMLElement>(null)

  const load = useCallback(
    async (signal?: AbortSignal, silent = false) => {
      if (!silent) setLoading(true)
      try {
        const next = await getResearchWorkspace(workspaceId, signal)
        if (signal?.aborted) return
        setDetail(next)
        setError('')
        setContractBlocked(false)
      } catch (cause) {
        if (signal?.aborted || (cause instanceof DOMException && cause.name === 'AbortError'))
          return
        setError(messageFor(cause, tr))
        if (cause instanceof ResearchApiError && cause.kind === 'contract') {
          setContractBlocked(true)
        }
      } finally {
        if (!signal?.aborted && !silent) setLoading(false)
      }
    },
    [tr, workspaceId]
  )

  useEffect(() => {
    const controller = new AbortController()
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  useEffect(() => {
    if (mutationError) mutationAlertRef.current?.focus()
  }, [mutationError])

  useEffect(() => {
    if (!assistantDrawerOpen) return
    const previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const drawer = assistantDrawerRef.current
    const focusable = () =>
      Array.from(
        drawer?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ) ?? []
      )
    focusable()[0]?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setAssistantDrawerOpen(false)
        return
      }
      if (event.key !== 'Tab') return
      const targets = focusable()
      if (!targets.length) {
        event.preventDefault()
        return
      }
      const first = targets[0]
      const last = targets[targets.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousBodyOverflow
      previouslyFocused?.focus()
    }
  }, [assistantDrawerOpen])

  const shouldPoll =
    !contractBlocked && (detail?.runs.some(run => POLLABLE_STATES.has(run.status)) ?? false)
  useEffect(() => {
    if (!shouldPoll) return
    const controller = new AbortController()
    let timer: number | undefined
    const poll = async () => {
      await load(controller.signal, true)
      if (!controller.signal.aborted) timer = window.setTimeout(() => void poll(), 2500)
    }
    timer = window.setTimeout(() => void poll(), 2500)
    return () => {
      controller.abort()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [load, shouldPoll])

  async function mutate(work: () => Promise<unknown>) {
    setBusy(true)
    setMutationError('')
    try {
      await work()
      await load(undefined, true)
    } catch (cause) {
      const message =
        cause instanceof ResearchApiError && cause.status === 409
          ? tr({
              zh: '这项内容已在其他窗口更新。页面已保留现状，请刷新后再提交。',
              en: 'This item changed in another window. Refresh before submitting again.',
            })
          : messageFor(cause, tr)
      setMutationError(message)
    } finally {
      setBusy(false)
    }
  }

  async function saveBrief(input: SaveResearchBriefInput) {
    await mutate(() => saveResearchBrief(workspaceId, input, detail?.brief?.brief_id))
  }

  async function startRun() {
    if (!detail?.brief) return
    const { brief_id, version: brief_version } = detail.brief
    await mutate(() =>
      startResearchRun(workspaceId, {
        brief_id,
        brief_version,
        idempotency_key: crypto.randomUUID(),
      })
    )
  }

  async function runAction(run: ResearchRun, action: ResearchRunAction) {
    await mutate(() =>
      applyResearchRunAction(workspaceId, run.run_id, {
        action,
        expected_revision: run.revision,
        expected_status: run.status,
        idempotency_key: crypto.randomUUID(),
      })
    )
  }

  async function addNote(body: string, sourceIds: string[]) {
    await mutate(() =>
      createResearchNote(workspaceId, {
        body,
        source_ids: sourceIds,
        idempotency_key: crypto.randomUUID(),
      })
    )
  }

  async function invalidateSource(source: ResearchSource) {
    if (source.status !== 'active') return
    await mutate(() =>
      invalidateResearchSource(workspaceId, source.source_id, {
        expected_revision: source.revision,
        expected_status: 'active',
        idempotency_key: crypto.randomUUID(),
      })
    )
  }

  async function followUp(report: ResearchReport, question: string) {
    const brief = detail?.brief
    if (!detail || !brief) return
    await mutate(() =>
      continueResearchRun(workspaceId, report.run_id, {
        question,
        objectives: [],
        constraints: [],
        source_policy: brief.source_policy,
        ...(brief.knowledge_base ? { knowledge_base_ref: brief.knowledge_base.resource_id } : {}),
        expected_workspace_revision: detail.workspace.revision,
        idempotency_key: crypto.randomUUID(),
        parent_report_revision: report.revision,
      })
    )
  }

  const assistantPanelVisibility = assistantMinimized
    ? 'hidden'
    : assistantDrawerOpen
      ? 'fixed inset-x-0 bottom-0 z-[60] block h-[78vh] max-h-[calc(100dvh-4rem)] rounded-t-2xl border-t shadow-2xl xl:static xl:h-auto xl:max-h-none xl:rounded-none xl:border-t-0 xl:shadow-none'
      : 'hidden xl:block'

  if (loading && !detail) return <WorkspaceLoading tr={tr} />

  if (!detail) {
    return (
      <main className="traittutor-scroll-area h-full overflow-y-auto px-4 py-8 sm:px-8 lg:px-10 xl:px-12 2xl:px-16">
        <div
          className="mx-auto max-w-3xl rounded-xl border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-5"
          role="alert"
        >
          <h1 className="text-lg font-semibold">
            {tr({ zh: '研究工作区无法打开', en: 'Research workspace could not be opened' })}
          </h1>
          <p className="mt-2 text-sm leading-relaxed">{error}</p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={() => void load()} className={secondaryButtonClass}>
              {tr({ zh: '重试', en: 'Try again' })}
            </button>
            <Link href="/research" className={secondaryButtonClass}>
              {tr({ zh: '返回工作区列表', en: 'Back to workspaces' })}
            </Link>
          </div>
        </div>
      </main>
    )
  }

  return (
    <main className="relative h-full min-h-0 overflow-hidden">
      <div
        className={`grid h-full min-h-0 w-full ${assistantMinimized ? 'xl:grid-cols-1' : 'xl:grid-cols-[minmax(0,1fr)_320px]'}`}
      >
        <div className="traittutor-scroll-area min-h-0 overflow-y-auto px-4 py-6 pb-16 sm:px-8 sm:py-8 lg:px-10 xl:px-12 2xl:px-16">
          <header className="border-b border-[var(--border)] pb-6">
            <Link
              href="/research"
              className="mb-4 inline-flex min-h-8 items-center gap-1.5 rounded text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
            >
              <ArrowLeft aria-hidden="true" className="h-4 w-4" />
              {tr({ zh: '全部研究工作区', en: 'All research workspaces' })}
            </Link>
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--primary)]">
                  {tr({ zh: '研究工作区', en: 'Research workspace' })}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-3">
                  <h1 className="break-words font-serif text-3xl font-semibold tracking-tight sm:text-4xl">
                    {detail.workspace.title}
                  </h1>
                  <span className="rounded-full bg-[var(--muted)] px-2.5 py-1 text-xs font-medium text-[var(--muted-foreground)]">
                    {workspaceStatusLabel(detail.workspace.status, tr)}
                  </span>
                </div>
                <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">
                  {tr({
                    zh: '以版本化简报启动可恢复研究，并让每条研究主张保持来源可追溯。',
                    en: 'Run recoverable research from a versioned brief and keep every research claim traceable to its sources.',
                  })}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={() => setAssistantDrawerOpen(true)}
                  className={`${secondaryButtonClass} xl:hidden`}
                >
                  <MessageCircle aria-hidden="true" className="h-4 w-4" />
                  {tr({ zh: '研究追问', en: 'Research follow-up' })}
                </button>
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => void load()}
                  className={secondaryButtonClass}
                >
                  <RefreshCw
                    aria-hidden="true"
                    className={loading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'}
                  />
                  {tr({ zh: '刷新', en: 'Refresh' })}
                </button>
              </div>
            </div>
          </header>

          {error ? (
            <div
              role="status"
              className="mt-5 rounded-lg border border-amber-500/35 bg-amber-500/10 px-4 py-3 text-sm text-amber-800 dark:text-amber-200"
            >
              {tr({
                zh: `自动刷新暂时失败：${error}。当前内容仍可查看。`,
                en: `Automatic refresh failed: ${error}. The current content remains available.`,
              })}
            </div>
          ) : null}
          {mutationError ? (
            <div
              ref={mutationAlertRef}
              tabIndex={-1}
              role="alert"
              className="mt-5 rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)] focus:outline-none"
            >
              {mutationError}
            </div>
          ) : null}
          {detail.workspace.status !== 'active' ? (
            <div
              role="status"
              className="mt-5 rounded-lg border border-[var(--border)] bg-[var(--muted)]/35 px-4 py-3 text-sm text-[var(--muted-foreground)]"
            >
              {tr({
                zh: '这个工作区当前为只读状态。已有简报、来源、主张与笔记仍可查看。',
                en: 'This workspace is read-only. Its existing brief, sources, claims, and notes remain available.',
              })}
            </div>
          ) : null}

          <div className="mt-6 space-y-5">
            <ResearchBriefEditor
              key={detail.brief ? `${detail.brief.brief_id}:${detail.brief.version}` : 'new'}
              brief={detail.brief}
              workspaceRevision={detail.workspace.revision}
              disabled={busy || contractBlocked || detail.workspace.status !== 'active'}
              tr={tr}
              onSave={saveBrief}
            />
            <ResearchRunPanel
              brief={detail.brief}
              runs={detail.runs}
              busy={busy || contractBlocked || detail.workspace.status !== 'active'}
              tr={tr}
              onStart={startRun}
              onAction={runAction}
            />
            <ResearchEvidencePanel
              sources={detail.sources}
              claims={detail.claims}
              notes={detail.notes}
              reports={detail.reports}
              busy={busy || contractBlocked || detail.workspace.status !== 'active'}
              tr={tr}
              onCreateNote={addNote}
              onInvalidateSource={invalidateSource}
            />
          </div>
        </div>

        <aside
          ref={assistantDrawerRef}
          className={`min-h-0 overflow-hidden border-l border-[var(--border)] bg-[var(--learning-panel-subtle)] ${assistantPanelVisibility}`}
          role={assistantDrawerOpen ? 'dialog' : undefined}
          aria-modal={assistantDrawerOpen ? true : undefined}
          aria-label={assistantDrawerOpen ? tr({ zh: '研究追问助手', en: 'Research follow-up assistant' }) : undefined}
          aria-hidden={assistantMinimized ? true : undefined}
        >
          <ResearchAssistant
            workspaceTitle={detail.workspace.title}
            brief={detail.brief}
            reports={detail.reports}
            runs={detail.runs}
            sourceCount={detail.sources.filter(source => source.status === 'active').length}
            busy={busy}
            disabled={contractBlocked || detail.workspace.status !== 'active'}
            zh={zh}
            onFollowUp={followUp}
            onMinimize={() => {
              setAssistantDrawerOpen(false)
              setAssistantMinimized(true)
            }}
          />
        </aside>
      </div>

      {assistantDrawerOpen ? (
        <button
          type="button"
          className="learning-assistant-backdrop xl:hidden"
          onClick={() => setAssistantDrawerOpen(false)}
          aria-label={tr({ zh: '关闭研究追问助手', en: 'Close research follow-up assistant' })}
        />
      ) : null}

      {assistantMinimized ? (
        <button
          type="button"
          className="learning-assistant-bubble"
          onClick={() => {
            setAssistantMinimized(false)
            if (!window.matchMedia('(min-width: 1280px)').matches) setAssistantDrawerOpen(true)
          }}
          aria-label={tr({ zh: '打开研究追问助手', en: 'Open research follow-up assistant' })}
        >
          <MessageCircle size={21} />
          <span className="learning-assistant-bubble__pulse" />
        </button>
      ) : null}
    </main>
  )
}

function WorkspaceLoading({ tr }: { tr: (copy: Copy) => string }) {
  return (
    <main
      className="traittutor-scroll-area h-full overflow-y-auto px-4 py-8 sm:px-8 lg:px-10 xl:px-12 2xl:px-16"
      aria-busy="true"
      aria-label={tr({ zh: '正在加载研究工作区', en: 'Loading research workspace' })}
    >
      <div className="w-full space-y-5">
        <div className="h-28 animate-pulse rounded-xl bg-[var(--muted)]/55" />
        {[0, 1, 2].map(item => (
          <div key={item} className="h-52 animate-pulse rounded-xl bg-[var(--muted)]/55" />
        ))}
      </div>
    </main>
  )
}

function messageFor(cause: unknown, tr: (copy: Copy) => string): string {
  if (cause instanceof ResearchApiError) {
    if (cause.status === 404)
      return tr({
        zh: '这个工作区不存在，或你没有访问权限。',
        en: 'This workspace does not exist or is not available to you.',
      })
    if (cause.kind === 'contract')
      return tr({
        zh: '服务返回了当前版本无法识别的研究状态。为避免误操作，页面已停止更新。',
        en: 'The service returned an unrecognized research state. Updates stopped to prevent an unsafe action.',
      })
    return cause.message
  }
  return cause instanceof Error
    ? cause.message
    : tr({
        zh: '操作未完成，请稍后重试。',
        en: 'The action could not be completed. Try again shortly.',
      })
}

function workspaceStatusLabel(
  status: ResearchWorkspaceDetail['workspace']['status'],
  tr: (copy: Copy) => string
): string {
  if (status === 'active') return tr({ zh: '进行中', en: 'Active' })
  if (status === 'archived') return tr({ zh: '已归档', en: 'Archived' })
  return tr({ zh: '已删除', en: 'Deleted' })
}

const secondaryButtonClass =
  'inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm font-medium hover:border-[var(--primary)]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-50'
