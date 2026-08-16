'use client'

import { useId, useMemo, useState } from 'react'
import { ExternalLink, FileText, Link2, NotebookPen } from 'lucide-react'
import RichMarkdownRenderer from '@/components/common/RichMarkdownRenderer'
import type {
  ResearchClaim,
  ResearchNote,
  ResearchReport,
  ResearchSource,
} from '@/lib/research-workspace-api'

type Copy = { zh: string; en: string }
type Tr = (copy: Copy) => string

interface Props {
  sources: ResearchSource[]
  claims: ResearchClaim[]
  notes: ResearchNote[]
  reports: ResearchReport[]
  busy: boolean
  tr: Tr
  onCreateNote: (body: string, sourceIds: string[]) => Promise<void>
  onInvalidateSource: (source: ResearchSource) => Promise<void>
}

export default function ResearchEvidencePanel({
  sources,
  claims,
  notes,
  reports,
  busy,
  tr,
  onCreateNote,
  onInvalidateSource,
}: Props) {
  const noteId = useId()
  const [note, setNote] = useState('')
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const sourceById = useMemo(
    () => new Map(sources.map(source => [source.source_id, source])),
    [sources]
  )

  async function submitNote(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!note.trim()) return
    await onCreateNote(note.trim(), selectedSourceIds)
    setNote('')
    setSelectedSourceIds([])
  }

  function toggleSource(sourceId: string) {
    setSelectedSourceIds(current =>
      current.includes(sourceId) ? current.filter(id => id !== sourceId) : [...current, sourceId]
    )
  }

  const latestReport = [...reports].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]

  return (
    <section
      className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5"
      aria-labelledby="research-evidence-heading"
    >
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--primary)]">
          {tr({ zh: '证据工作区', en: 'Evidence workspace' })}
        </p>
        <h2 id="research-evidence-heading" className="mt-1 text-lg font-semibold">
          {tr({ zh: '来源、主张与笔记', en: 'Sources, claims, and notes' })}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-[var(--muted-foreground)]">
          {tr({
            zh: '检索来源与模型推断明确分层；无来源的推断不会伪装成检索事实。',
            en: 'Retrieved evidence and model inferences remain visibly separate; an unsourced inference is never presented as a retrieved fact.',
          })}
        </p>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <EvidenceSection
          icon={<Link2 aria-hidden="true" className="h-4 w-4" />}
          title={tr({ zh: '来源', en: 'Sources' })}
          count={sources.length}
        >
          {sources.length ? (
            <ol className="space-y-3">
              {sources.map(source => (
                <SourceItem
                  key={source.source_id}
                  source={source}
                  busy={busy}
                  tr={tr}
                  onInvalidate={onInvalidateSource}
                />
              ))}
            </ol>
          ) : (
            <Empty
              text={tr({
                zh: '运行完成并验证来源后，这里会显示可追溯证据。',
                en: 'Traceable evidence will appear after a run retrieves and validates sources.',
              })}
            />
          )}
        </EvidenceSection>

        <EvidenceSection
          icon={<FileText aria-hidden="true" className="h-4 w-4" />}
          title={tr({ zh: '主张', en: 'Claims' })}
          count={claims.length}
        >
          {claims.length ? (
            <ol className="space-y-3">
              {claims.map(claim => (
                <li key={claim.claim_id} className="rounded-lg border border-[var(--border)] p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={claim.kind === 'grounded' ? groundedClass : inferenceClass}>
                      {claim.kind === 'grounded'
                        ? tr({ zh: '有来源', en: 'Grounded' })
                        : tr({ zh: '推断', en: 'Inference' })}
                    </span>
                    {claim.evidence_status === 'needs_review' ? (
                      <span className={reviewClass}>
                        {tr({ zh: '证据待复核', en: 'Evidence review required' })}
                      </span>
                    ) : null}
                    {claim.source_ids.length ? (
                      <span className="text-xs text-[var(--muted-foreground)]">
                        {tr({
                          zh: `${claim.source_ids.length} 个来源`,
                          en: `${claim.source_ids.length} sources`,
                        })}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-2 text-sm leading-relaxed">{claim.text}</p>
                  {claim.source_ids.length ? (
                    <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
                      {claim.source_ids.map(sourceId => {
                        const source = sourceById.get(sourceId)
                        const href = source ? safeHttpUrl(source.url) : null
                        return (
                          <li key={sourceId}>
                            {source && href ? (
                              <a
                                href={href}
                                target="_blank"
                                rel="noreferrer noopener"
                                className="inline-flex min-h-6 items-center gap-1 text-[var(--primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
                              >
                                {source.title}
                                <ExternalLink aria-hidden="true" className="h-3 w-3" />
                              </a>
                            ) : (
                              <span className="text-[var(--muted-foreground)]">
                                {tr({ zh: '来源不可用', en: 'Source unavailable' })}
                              </span>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : (
            <Empty
              text={tr({
                zh: '尚未形成经过验证的研究主张。',
                en: 'No validated research claims yet.',
              })}
            />
          )}
        </EvidenceSection>
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <EvidenceSection
          icon={<FileText aria-hidden="true" className="h-4 w-4" />}
          title={tr({ zh: '最新报告', en: 'Latest report' })}
          count={reports.length}
        >
          {latestReport ? (
            <article>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-semibold">{tr({ zh: '研究报告', en: 'Research report' })}</h3>
                {latestReport.evidence_status === 'needs_review' ? (
                  <span className={reviewClass}>
                    {tr({ zh: '证据待复核', en: 'Evidence review required' })}
                  </span>
                ) : null}
              </div>
              {latestReport.evidence_status === 'needs_review' ? (
                <p className="mt-2 text-xs leading-relaxed text-amber-800 dark:text-amber-200">
                  {tr({
                    zh: '相关来源已失效。报告正文为审计保留，不能再作为已验证结论使用。',
                    en: 'A linked source was invalidated. This report body is retained for audit, not as currently verified evidence.',
                  })}
                </p>
              ) : null}
              <RichMarkdownRenderer
                content={latestReport.body}
                className="mt-3 text-sm"
                variant="compact"
              />
            </article>
          ) : (
            <Empty
              text={tr({
                zh: '研究完成后，报告会出现在这里。',
                en: 'The report will appear here after research completes.',
              })}
            />
          )}
        </EvidenceSection>

        <EvidenceSection
          icon={<NotebookPen aria-hidden="true" className="h-4 w-4" />}
          title={tr({ zh: '我的笔记', en: 'My notes' })}
          count={notes.length}
        >
          <form onSubmit={event => void submitNote(event)}>
            <label htmlFor={noteId} className="block text-sm font-medium">
              {tr({ zh: '新增笔记', en: 'Add a note' })}
            </label>
            <textarea
              id={noteId}
              value={note}
              onChange={event => setNote(event.target.value)}
              rows={3}
              disabled={busy}
              className="mt-1.5 min-h-11 w-full resize-y rounded-md border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:opacity-60"
            />
            {sources.some(source => source.status === 'active') ? (
              <fieldset className="mt-3">
                <legend className="text-xs font-medium text-[var(--muted-foreground)]">
                  {tr({ zh: '关联来源（可选）', en: 'Link sources (optional)' })}
                </legend>
                <div className="mt-2 max-h-32 space-y-1 overflow-y-auto rounded-md border border-[var(--border)] p-2">
                  {sources
                    .filter(source => source.status === 'active')
                    .map(source => (
                      <label
                        key={source.source_id}
                        className="flex min-h-8 cursor-pointer items-start gap-2 rounded px-1 py-1 text-xs hover:bg-[var(--muted)]"
                      >
                        <input
                          type="checkbox"
                          checked={selectedSourceIds.includes(source.source_id)}
                          onChange={() => toggleSource(source.source_id)}
                          className="mt-0.5 h-4 w-4"
                        />{' '}
                        <span>{source.title}</span>
                      </label>
                    ))}
                </div>
              </fieldset>
            ) : null}
            <button
              type="submit"
              disabled={busy || !note.trim()}
              className="mt-3 inline-flex min-h-10 items-center justify-center rounded-md border border-[var(--border)] px-3 text-sm font-medium hover:border-[var(--primary)]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {tr({ zh: '保存笔记', en: 'Save note' })}
            </button>
          </form>
          {notes.length ? (
            <ol className="mt-4 space-y-2 border-t border-[var(--border)] pt-4">
              {[...notes]
                .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
                .map(item => (
                  <li key={item.note_id} className="rounded-lg bg-[var(--muted)]/35 p-3">
                    <p className="whitespace-pre-wrap text-sm leading-relaxed">{item.body}</p>
                    <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                      {tr({
                        zh: `${item.source_ids.length} 个关联来源`,
                        en: `${item.source_ids.length} linked sources`,
                      })}
                    </p>
                  </li>
                ))}
            </ol>
          ) : null}
        </EvidenceSection>
      </div>
    </section>
  )
}

function EvidenceSection({
  icon,
  title,
  count,
  children,
}: {
  icon: React.ReactNode
  title: string
  count: number
  children: React.ReactNode
}) {
  return (
    <section className="min-w-0 rounded-lg border border-[var(--border)] bg-[var(--muted)]/15 p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="flex items-center gap-2 font-semibold">
          <span className="text-[var(--primary)]">{icon}</span>
          {title}
        </h3>
        <span className="text-xs tabular-nums text-[var(--muted-foreground)]">{count}</span>
      </div>
      {children}
    </section>
  )
}

function SourceItem({
  source,
  busy,
  tr,
  onInvalidate,
}: {
  source: ResearchSource
  busy: boolean
  tr: Tr
  onInvalidate: (source: ResearchSource) => Promise<void>
}) {
  const href = safeHttpUrl(source.url)
  return (
    <li className="rounded-lg border border-[var(--border)] p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="text-sm font-medium">{source.title}</h4>
          {source.status === 'invalidated' ? (
            <span className={reviewClass}>
              {tr({ zh: '来源已失效', en: 'Source invalidated' })}
            </span>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              aria-label={tr({
                zh: `打开来源：${source.title}`,
                en: `Open source: ${source.title}`,
              })}
              className="grid h-8 w-8 place-items-center rounded text-[var(--primary)] hover:bg-[var(--muted)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
            >
              <ExternalLink aria-hidden="true" className="h-4 w-4" />
            </a>
          ) : null}
          {source.status === 'active' ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void onInvalidate(source)}
              className="min-h-8 rounded border border-amber-500/45 px-2 text-xs font-medium text-amber-800 hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {tr({ zh: '标记失效', en: 'Invalidate' })}
            </button>
          ) : null}
        </div>
      </div>
      {source.excerpt ? (
        <p className="mt-2 text-xs leading-relaxed text-[var(--muted-foreground)]">
          {source.excerpt}
        </p>
      ) : null}
      {source.invalidation_reason ? (
        <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">
          {source.invalidation_reason}
        </p>
      ) : null}
    </li>
  )
}

function Empty({ text }: { text: string }) {
  return (
    <p className="rounded-lg border border-dashed border-[var(--border)] px-3 py-5 text-sm leading-relaxed text-[var(--muted-foreground)]">
      {text}
    </p>
  )
}

function safeHttpUrl(value: string): string | null {
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.toString() : null
  } catch {
    return null
  }
}

const groundedClass =
  'rounded-full bg-emerald-500/10 px-2 py-0.5 text-xs font-medium text-emerald-600 dark:text-emerald-300'
const inferenceClass =
  'rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-300'
const reviewClass =
  'rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-800 dark:text-amber-200'
