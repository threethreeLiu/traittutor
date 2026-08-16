'use client'

import Link from 'next/link'
import {
  AlertTriangle,
  ArrowUpRight,
  BookOpenCheck,
  CalendarClock,
  CheckCircle2,
  CircleHelp,
  Clock3,
  ListChecks,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react'
import type {
  LearningModelPendingSubjectsSection,
  LearningModelSectionMeta,
  LearningModelSectionStatus,
  LearningModelSubjectsSection,
  LearningModelSupportSummary,
  LearningModelTask,
  LearningModelTaskQueue,
  LearningModelTodaySummary,
} from '@/lib/learning-model-read-api'

type Copy = { zh: string; en: string }
export type OverviewTr = (copy: Copy) => string

export function OverviewSectionSkeleton({ label }: { label: string }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label={label}>
      <div className="h-5 w-36 animate-pulse rounded bg-[var(--muted)]" />
      <div className="h-20 animate-pulse rounded-lg bg-[var(--muted)]/70" />
      <div className="h-20 animate-pulse rounded-lg bg-[var(--muted)]/50" />
    </div>
  )
}

export function TodaySummarySection({ section, tr }: { section: LearningModelTodaySummary; tr: OverviewTr }) {
  return (
    <SectionFrame
      id="today-summary"
      eyebrow="TODAY"
      title={tr({ zh: '今日学习摘要', en: "Today's learning summary" })}
      description={tr({ zh: '根据当前可执行事项安排下一步，不把不同学科折算成总分。', en: 'Choose the next action from current work without combining subjects into one score.' })}
      icon={<CalendarClock className="h-4 w-4" />}
      meta={section.meta}
      tr={tr}
    >
      {section.meta.status === 'empty' ? (
        <EmptyState
          title={tr({ zh: '今天没有待处理任务', en: 'Nothing is due today' })}
          detail={tr({ zh: '开始一个学习目标后，复习、错题修复和最近活动会集中显示在这里。', en: 'Start a learning goal to see reviews, error repairs, and recent activity here.' })}
          actionLabel={tr({ zh: '开始学习', en: 'Start learning' })}
          actionHref="/learning"
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <SummaryMetric label={tr({ zh: '活跃学科', en: 'Active subjects' })} value={section.active_subject_count} />
            <SummaryMetric label={tr({ zh: '到期复习', en: 'Reviews due' })} value={section.due_review_count} />
            <SummaryMetric label={tr({ zh: '待修复错题', en: 'Open errors' })} value={section.open_error_count} />
            <SummaryMetric label={tr({ zh: '待归因', en: 'Needs attribution' })} value={section.attribution_pending_count} />
          </div>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-[var(--muted-foreground)]">
              {section.latest_activity_at
                ? tr({ zh: `最近活动 ${formatDate(section.latest_activity_at, tr)}`, en: `Last activity ${formatDate(section.latest_activity_at, tr)}` })
                : tr({ zh: '暂无近期活动', en: 'No recent activity' })}
            </p>
            <div className="flex flex-wrap gap-2">
              <SummaryAction href="/learning" label={tr({ zh: '继续学习', en: 'Continue learning' })} primary={!section.due_review_count && !section.open_error_count} />
              {section.due_review_count ? <SummaryAction href="/settings/learning-model#task-queue" label={tr({ zh: '开始复习', en: 'Start review' })} primary /> : null}
              {section.open_error_count ? <SummaryAction href="/settings/learning-model#task-queue" label={tr({ zh: '修复错题', en: 'Repair errors' })} primary={!section.due_review_count} /> : null}
            </div>
          </div>
        </>
      )}
    </SectionFrame>
  )
}

export function SubjectsSection({ section, tr }: { section: LearningModelSubjectsSection; tr: OverviewTr }) {
  return (
    <SectionFrame
      id="subjects"
      eyebrow="SUBJECTS"
      title={tr({ zh: '我的学科', en: 'My subjects' })}
      description={tr({ zh: '每个 canonical 学科只出现一次；事实计数由服务端读模型提供。', en: 'Each canonical subject appears once; factual counts come from the server read model.' })}
      icon={<BookOpenCheck className="h-4 w-4" />}
      meta={section.meta}
      tr={tr}
    >
      {!section.items.length ? (
        <EmptyState
          title={tr({ zh: '学习画像会随活动形成', en: 'Your learning profile grows with activity' })}
          detail={tr({ zh: '创建学习目标、发布课件或完成一次可判分练习后，相关学科会出现在这里。', en: 'Create a goal, publish courseware, or complete graded practice to add a subject.' })}
          actionLabel={tr({ zh: '创建学习目标', en: 'Create a learning goal' })}
          actionHref="/learning"
        />
      ) : (
        <>
          <p className="mb-3 rounded-lg bg-[var(--muted)]/30 px-3 py-2 text-xs text-[var(--muted-foreground)]">
            {tr({
              zh: '不同学科不计算综合掌握率；具体掌握状态按 KC 查看，证据不足时不显示百分比。',
              en: 'Subjects are not combined into an overall mastery score. View mastery by KC; insufficient evidence is never shown as a percentage.',
            })}
          </p>
          <div className="grid gap-3 xl:grid-cols-2">
            {section.items.map(subject => {
              const detailHref = `/settings/learning-model/${encodeURIComponent(subject.subject_id)}`
            return (
              <article key={subject.subject_id} className="rounded-lg border border-[var(--border)] bg-[var(--background)]/35 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate font-medium">{subject.label}</h3>
                    <p className="mt-1 truncate text-xs text-[var(--muted-foreground)]">
                      {subject.last_activity_at
                        ? tr({ zh: `最近活动 ${formatDate(subject.last_activity_at, tr)}`, en: `Last activity ${formatDate(subject.last_activity_at, tr)}` })
                        : tr({ zh: '暂无近期活动', en: 'No recent activity' })}
                    </p>
                  </div>
                  <StatusPill status={section.meta.status} tr={tr} />
                </div>
                <dl className="mt-4 grid grid-cols-2 gap-x-3 gap-y-3 text-sm sm:grid-cols-4">
                  <Count label={tr({ zh: '覆盖 KC', en: 'KCs covered' })} value={subject.covered_kc_count} />
                  <Count label={tr({ zh: '强证据', en: 'Strong evidence' })} value={subject.strong_evidence_count} />
                  <Count label={tr({ zh: '开放错题', en: 'Open errors' })} value={subject.open_error_count} />
                  <Count label={tr({ zh: '到期复习', en: 'Reviews due' })} value={subject.due_review_count} />
                </dl>
                <div className="mt-4 flex justify-end border-t border-[var(--border)] pt-3">
                  <Link href={detailHref} className="inline-flex h-9 items-center gap-1 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90">
                    {tr({ zh: '查看学科', en: 'View subject' })}
                    <ArrowUpRight className="h-3.5 w-3.5" />
                  </Link>
                </div>
              </article>
            )
            })}
          </div>
        </>
      )}
    </SectionFrame>
  )
}

export function PendingSubjectsSection({ section, tr }: { section: LearningModelPendingSubjectsSection; tr: OverviewTr }) {
  return (
    <SectionFrame
      id="pending-subjects"
      eyebrow="CONFIRMATION"
      title={tr({ zh: '待确认学科', en: 'Subjects to confirm' })}
      description={tr({ zh: '这些记录不会进入正式学科统计、BKT 汇总或默认教学上下文。', en: 'These records stay out of confirmed statistics, BKT summaries, and default teaching context.' })}
      icon={<CircleHelp className="h-4 w-4" />}
      meta={section.meta}
      tr={tr}
    >
      {!section.items.length ? (
        <EmptyState title={tr({ zh: '没有待确认学科', en: 'No subjects need confirmation' })} detail={tr({ zh: '当前学科归属均已确认；新出现的模糊归属会单独列在这里。', en: 'Current subject assignments are confirmed. New ambiguous records will appear here.' })} />
      ) : (
        <div className="space-y-3">
          {section.items.map(subject => (
            <article key={subject.subject_id} className="rounded-lg border border-amber-500/25 bg-amber-500/5 p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-medium">{subject.label}</h3>
                    <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">{tr({ zh: '待确认', en: 'Pending' })}</span>
                  </div>
                  <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                    {tr({ zh: '来源', en: 'Sources' })}: {subject.source_refs.length || tr({ zh: '未知', en: 'Unknown' })}
                    {subject.created_at ? ` · ${formatDate(subject.created_at, tr)}` : ''}
                  </p>
                  {subject.possible_duplicate_subject_ids.length ? (
                    <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                      {tr({ zh: `发现 ${subject.possible_duplicate_subject_ids.length} 个可能重复的学科 ID；合并前需要确认。`, en: `${subject.possible_duplicate_subject_ids.length} possible duplicate subject IDs found; confirmation is required before merging.` })}
                    </p>
                  ) : null}
                </div>
                <Link href={`/settings/learning-model/${encodeURIComponent(subject.subject_id)}?tab=governance`} className="inline-flex h-9 shrink-0 items-center gap-1 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-foreground)]">
                  {tr({ zh: '确认或更正', en: 'Confirm or correct' })}
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}
    </SectionFrame>
  )
}

export function TaskQueueSection({ section, subjectLabels, tr }: { section: LearningModelTaskQueue; subjectLabels: ReadonlyMap<string, string>; tr: OverviewTr }) {
  return (
    <SectionFrame
      id="task-queue"
      eyebrow="NEXT ACTIONS"
      title={tr({ zh: '学习任务队列', en: 'Learning task queue' })}
      description={tr({ zh: '只展示服务端确认的可执行事项；同一来源不会由浏览器重复计数。', en: 'Only server-confirmed actions are shown; the browser does not recount shared sources.' })}
      icon={<ListChecks className="h-4 w-4" />}
      meta={section.meta}
      tr={tr}
    >
      {!section.items.length ? (
        <EmptyState title={tr({ zh: '任务队列已清空', en: 'Your task queue is clear' })} detail={tr({ zh: '没有到期复习、开放错题或待归因事项。你可以继续当前学习目标。', en: 'No reviews, open errors, or attribution tasks are due. Continue your current goal.' })} actionLabel={tr({ zh: '继续学习', en: 'Continue learning' })} actionHref="/learning" />
      ) : (
        <ol className="divide-y divide-[var(--border)]">
          {section.items.map(task => <TaskRow key={task.task_id} task={task} subjectLabel={subjectLabels.get(task.subject_id)} tr={tr} />)}
        </ol>
      )}
    </SectionFrame>
  )
}

export function GovernanceSection({ section, tr, updatingInference, onToggleInference }: { section: LearningModelSupportSummary; tr: OverviewTr; updatingInference: boolean; onToggleInference: () => void }) {
  return (
    <SectionFrame
      id="governance"
      eyebrow="TEACHING SUPPORT"
      title={tr({ zh: '画像治理', en: 'Profile governance' })}
      description={tr({ zh: '这些信息只影响“怎么教”，不参与判分，也不证明你学会了什么。', en: 'These signals affect how teaching is delivered; they do not grade or prove mastery.' })}
      icon={<ShieldCheck className="h-4 w-4" />}
      meta={section.meta}
      tr={tr}
    >
      {section.meta.status === 'empty' ? (
        <EmptyState title={tr({ zh: '尚未形成教学支持画像', en: 'No teaching support profile yet' })} detail={tr({ zh: '明确告诉 TraitTutor 你希望怎样讲解，或完成一次学习反思后，这里会显示可治理摘要。', en: 'Share an explanation preference or complete a reflection to create a manageable summary.' })} actionLabel={tr({ zh: '管理记忆', en: 'Manage memory' })} actionHref="/settings/memory" />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <SummaryMetric label={tr({ zh: '明确偏好', en: 'Preferences' })} value={section.confirmed_preference_count} />
            <SummaryMetric label={tr({ zh: '已确认反思', en: 'Confirmed reflections' })} value={section.confirmed_reflection_count} />
            <SummaryMetric label={tr({ zh: 'Compass 信号', en: 'Compass signals' })} value={section.compass_signal_count} />
          </div>
          <div className="mt-4 flex flex-col gap-3 border-t border-[var(--border)] pt-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium">
                {section.inference_enabled === null || section.inference_enabled === undefined
                  ? tr({ zh: '推断状态暂不可用', en: 'Inference status unavailable' })
                  : section.inference_enabled
                    ? tr({ zh: '行为推断已开启', en: 'Behavioral inference is on' })
                    : tr({ zh: '行为推断已关闭', en: 'Behavioral inference is off' })}
              </p>
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">{tr({ zh: '明确偏好始终优先，推断可随时关闭。', en: 'Explicit preferences always take priority; inference can be disabled anytime.' })}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {typeof section.inference_enabled === 'boolean' ? (
                <button type="button" disabled={updatingInference} aria-pressed={section.inference_enabled} onClick={onToggleInference} className="inline-flex h-9 items-center justify-center rounded-md border border-[var(--border)] px-3 text-sm font-medium hover:border-[var(--primary)]/45 disabled:cursor-not-allowed disabled:opacity-50">
                  {updatingInference ? tr({ zh: '正在更新', en: 'Updating' }) : section.inference_enabled ? tr({ zh: '关闭推断', en: 'Turn inference off' }) : tr({ zh: '开启推断', en: 'Turn inference on' })}
                </button>
              ) : null}
              <Link href="/settings/memory" className="inline-flex h-9 items-center gap-1 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-foreground)]">
                {tr({ zh: '查看依据与更正', en: 'Review and correct' })}
                <ArrowUpRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </div>
        </>
      )}
    </SectionFrame>
  )
}

function SectionFrame({ id, eyebrow, title, description, icon, meta, tr, children }: { id: string; eyebrow: string; title: string; description: string; icon: React.ReactNode; meta: LearningModelSectionMeta; tr: OverviewTr; children: React.ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5">
      <div className="flex flex-col gap-2 border-b border-[var(--border)] pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="flex items-center gap-2 text-[11px] font-medium tracking-[0.16em] text-[var(--muted-foreground)]"><span className="text-[var(--primary)]">{icon}</span>{eyebrow}</p>
          <h2 id={`${id}-heading`} className="mt-2 text-lg font-semibold">{title}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-[var(--muted-foreground)]">{description}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2"><StatusPill status={meta.status} tr={tr} />{meta.updated_at ? <time className="text-[11px] text-[var(--muted-foreground)]">{formatDate(meta.updated_at, tr)}</time> : null}</div>
      </div>
      {meta.status === 'stale' || meta.status === 'rebuilding' ? <SectionNotice meta={meta} tr={tr} /> : null}
      <div className="mt-4">{meta.status === 'unavailable' ? <UnavailableState meta={meta} tr={tr} /> : children}</div>
    </section>
  )
}

function SectionNotice({ meta, tr }: { meta: LearningModelSectionMeta; tr: OverviewTr }) {
  const rebuilding = meta.status === 'rebuilding'
  return (
    <div role="status" className="mt-4 flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2.5 text-xs text-amber-800 dark:text-amber-200">
      {rebuilding ? <RefreshCw className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin" /> : <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
      <span>{rebuilding ? tr({ zh: '模型正在重建，先展示仍可用的数据；完成后会自动更新。', en: 'The model is rebuilding. Available data remains visible and will update when complete.' }) : tr({ zh: '本区数据可能不是最新状态，其他区块仍可正常使用。', en: 'This section may be out of date; other sections remain available.' })}</span>
    </div>
  )
}

function UnavailableState({ meta, tr }: { meta: LearningModelSectionMeta; tr: OverviewTr }) {
  return (
    <div role="status" className="rounded-lg border border-dashed border-[var(--border)] px-4 py-7 text-center">
      <AlertTriangle className="mx-auto h-5 w-5 text-amber-600 dark:text-amber-300" />
      <h3 className="mt-2 text-sm font-medium">{tr({ zh: '本区数据暂不可用', en: 'This section is temporarily unavailable' })}</h3>
      <p className="mx-auto mt-1 max-w-lg text-sm text-[var(--muted-foreground)]">
        {meta.unavailable_sources.length
          ? tr({ zh: `${meta.unavailable_sources.length} 个数据源暂不可用；其他区域不受影响。`, en: `${meta.unavailable_sources.length} data sources are unavailable; other sections are unaffected.` })
          : tr({ zh: '其他区域不受影响，可稍后刷新重试。', en: 'Other sections are unaffected. Refresh again shortly.' })}
      </p>
    </div>
  )
}

function EmptyState({ title, detail, actionLabel, actionHref }: { title: string; detail: string; actionLabel?: string; actionHref?: string }) {
  return (
    <div className="rounded-lg border border-dashed border-[var(--border)] px-4 py-7 text-center">
      <CheckCircle2 className="mx-auto h-5 w-5 text-[var(--primary)]" />
      <h3 className="mt-2 text-sm font-medium">{title}</h3>
      <p className="mx-auto mt-1 max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">{detail}</p>
      {actionLabel && actionHref ? <Link href={actionHref} className="mt-3 inline-flex h-9 items-center gap-1 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-foreground)]">{actionLabel}<ArrowUpRight className="h-3.5 w-3.5" /></Link> : null}
    </div>
  )
}

function SummaryMetric({ label, value }: { label: string; value: number }) {
  return <div className="rounded-lg bg-[var(--muted)]/35 p-3"><p className="text-xs text-[var(--muted-foreground)]">{label}</p><p className="mt-2 text-2xl font-semibold tabular-nums">{value}</p></div>
}

function Count({ label, value }: { label: string; value: number }) {
  return <div><dt className="text-[11px] text-[var(--muted-foreground)]">{label}</dt><dd className="mt-0.5 font-medium tabular-nums">{value}</dd></div>
}

function SummaryAction({ href, label, primary = false }: { href: string; label: string; primary?: boolean }) {
  return (
    <Link
      href={href}
      className={primary
        ? 'inline-flex h-9 items-center justify-center gap-1 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-foreground)] hover:opacity-90'
        : 'inline-flex h-9 items-center justify-center gap-1 rounded-md border border-[var(--border)] px-3 text-sm font-medium hover:border-[var(--primary)]/45'}
    >
      {label}
      <ArrowUpRight className="h-3.5 w-3.5" />
    </Link>
  )
}

function TaskRow({ task, subjectLabel, tr }: { task: LearningModelTask; subjectLabel?: string; tr: OverviewTr }) {
  const href = `/settings/learning-model/${encodeURIComponent(task.subject_id)}?tab=${taskTab(task.kind)}`
  const labels = {
    review: tr({ zh: '完成到期复习', en: 'Complete due review' }),
    error_repair: tr({ zh: '修复开放错题', en: 'Repair open error' }),
    attribution: tr({ zh: '补充学科归因', en: 'Resolve subject attribution' }),
  }
  const actionLabels = {
    review: tr({ zh: '开始复习', en: 'Start review' }),
    error_repair: tr({ zh: '修复错题', en: 'Repair error' }),
    attribution: tr({ zh: '补充归因', en: 'Resolve attribution' }),
  }
  return (
    <li className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-md bg-[var(--muted)] text-[var(--primary)]">{task.kind === 'review' ? <CalendarClock className="h-4 w-4" /> : task.kind === 'attribution' ? <CircleHelp className="h-4 w-4" /> : <ListChecks className="h-4 w-4" />}</span>
        <div className="min-w-0">
          <h3 className="text-sm font-medium">{labels[task.kind]}</h3>
          <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">{[subjectLabel || task.subject_id, task.due_at ? formatDate(task.due_at, tr) : null, task.source_refs.length ? tr({ zh: `${task.source_refs.length} 个来源`, en: `${task.source_refs.length} sources` }) : null].filter(Boolean).join(' · ')}</p>
        </div>
      </div>
      <Link href={href} className="inline-flex h-9 items-center justify-center gap-1 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-foreground)]">{actionLabels[task.kind]}<ArrowUpRight className="h-3.5 w-3.5" /></Link>
    </li>
  )
}

function StatusPill({ status, tr }: { status: LearningModelSectionStatus; tr: OverviewTr }) {
  const labels: Record<LearningModelSectionStatus, Copy> = {
    ready: { zh: '已更新', en: 'Ready' },
    empty: { zh: '暂无数据', en: 'Empty' },
    unavailable: { zh: '不可用', en: 'Unavailable' },
    stale: { zh: '可能过期', en: 'Stale' },
    rebuilding: { zh: '重建中', en: 'Rebuilding' },
  }
  const tone = status === 'ready' ? 'bg-[var(--primary)]/10 text-[var(--primary)]' : status === 'unavailable' ? 'bg-[var(--destructive)]/10 text-[var(--destructive)]' : status === 'stale' || status === 'rebuilding' ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300' : 'bg-[var(--muted)] text-[var(--muted-foreground)]'
  return <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] ${tone}`}>{tr(labels[status])}</span>
}

function taskTab(kind: LearningModelTask['kind']) {
  if (kind === 'review') return 'reviews'
  if (kind === 'error_repair') return 'errors'
  return 'governance'
}

function formatDate(value: string, tr: OverviewTr) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return tr({ zh: '时间未知', en: 'Unknown time' })
  return new Intl.DateTimeFormat(tr({ zh: 'zh-CN', en: 'en-US' }), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}
