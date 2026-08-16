'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  CheckSquare2,
  Clock3,
  FileText,
  Layers3,
  Loader2,
  Plus,
  Route,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import {
  deleteLearningPack,
  deleteLearningPacks,
  LEARNING_PACKS_INVALIDATED_EVENT,
  listLearningPacks,
  type LearningPack,
} from '@/lib/traittutor-api'

export default function LearningPlansHome() {
  const { i18n } = useTranslation()
  const [packs, setPacks] = useState<LearningPack[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [pendingDeleteIds, setPendingDeleteIds] = useState<string[] | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [mutationMessage, setMutationMessage] = useState<{
    tone: 'success' | 'error'
    text: string
  } | null>(null)
  const zh = i18n.language.toLowerCase().startsWith('zh')

  useEffect(() => {
    let cancelled = false
    const load = () => {
      void listLearningPacks()
        .then(items => {
          if (!cancelled) setPacks(items)
        })
        .catch(() => {
          if (!cancelled) setError(true)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }
    load()
    // Deleting a chat session cascades server-side to its linked Packs; the
    // sidebar broadcasts this event so this list refetches instead of showing
    // packs that no longer exist.
    window.addEventListener(LEARNING_PACKS_INVALIDATED_EVENT, load)
    return () => {
      cancelled = true
      window.removeEventListener(LEARNING_PACKS_INVALIDATED_EVENT, load)
    }
  }, [])

  const completed = useMemo(
    () =>
      packs.filter(
        pack => pack.goal?.status === 'completed' || pack.goal?.round_status === 'completed'
      ),
    [packs]
  )
  const completedIds = useMemo(() => new Set(completed.map(pack => pack.pack_id)), [completed])
  const active = useMemo(
    () => packs.filter(pack => !completedIds.has(pack.pack_id)),
    [completedIds, packs]
  )
  const activeIds = useMemo(() => active.map(pack => pack.pack_id), [active])
  const allSelected = activeIds.length > 0 && activeIds.every(id => selectedIds.includes(id))
  const reviewCount = useMemo(
    () => packs.reduce((total, pack) => total + (pack.due_review_count ?? 0), 0),
    [packs]
  )
  const artifactCount = useMemo(
    () =>
      packs.reduce(
        (total, pack) =>
          total +
          Object.values(pack.artifacts ?? {}).reduce((sum, values) => sum + values.length, 0),
        0
      ),
    [packs]
  )

  const closeDeleteDialog = useCallback(() => {
    if (!deleting) setPendingDeleteIds(null)
  }, [deleting])

  function toggleSelected(packId: string) {
    setSelectedIds(current =>
      current.includes(packId) ? current.filter(id => id !== packId) : [...current, packId]
    )
  }

  function toggleSelectAll() {
    setSelectedIds(allSelected ? [] : activeIds)
  }

  async function confirmDelete() {
    const ids = pendingDeleteIds ?? []
    if (!ids.length || deleting) return
    setDeleting(true)
    setMutationMessage(null)
    try {
      const deletedIds =
        ids.length === 1
          ? [(await deleteLearningPack(ids[0])).deleted_id]
          : (await deleteLearningPacks(ids)).deleted_ids
      const deletedSet = new Set(deletedIds)
      setPacks(current => current.filter(pack => !deletedSet.has(pack.pack_id)))
      setSelectedIds(current => current.filter(id => !deletedSet.has(id)))
      setPendingDeleteIds(null)
      setMutationMessage({
        tone: 'success',
        text: zh
          ? `已删除 ${deletedIds.length} 条学习路径。`
          : `Deleted ${deletedIds.length} learning ${deletedIds.length === 1 ? 'path' : 'paths'}.`,
      })
    } catch (deleteError) {
      setMutationMessage({
        tone: 'error',
        text:
          deleteError instanceof Error
            ? deleteError.message
            : zh
              ? '删除失败，请稍后重试。'
              : 'Delete failed. Please try again.',
      })
    } finally {
      setDeleting(false)
    }
  }

  const pendingTitles = (pendingDeleteIds ?? [])
    .map(id => packs.find(pack => pack.pack_id === id))
    .filter((pack): pack is LearningPack => Boolean(pack))
    .map(pack => pack.goal?.text ?? pack.title)

  return (
    <main className="learning-canvas traittutor-scroll-area h-full overflow-y-auto px-4 py-6 sm:px-8 sm:py-8 lg:px-10 xl:px-12 2xl:px-16">
      <div className="w-full">
        <header className="flex flex-wrap items-end justify-between gap-5 border-b border-[var(--border)] pb-6">
          <div>
            <p className="learning-eyebrow">
              {zh ? 'TraitTutor · 学习路径' : 'TraitTutor · Learning paths'}
            </p>
            <h1 className="mt-3 font-serif text-4xl font-semibold">
              {zh ? '我的学习' : 'My learning'}
            </h1>
            <p className="learning-copy-muted mt-3 max-w-2xl text-sm leading-6">
              {zh
                ? '目标、材料、组件和学习证据都保留在同一个学习包中。'
                : 'Goals, sources, components, and learning evidence stay together in one learning pack.'}
            </p>
          </div>
          <Link href="/home" className="learning-button learning-button--primary px-4 py-3 text-sm">
            <Plus size={16} />
            {zh ? '新建学习目标' : 'New learning goal'}
          </Link>
        </header>

        <section className="mt-6 grid gap-3 sm:grid-cols-3">
          <Metric
            icon={Route}
            label={zh ? '进行中' : 'In progress'}
            value={active.length}
            emphasis="primary"
          />
          <Metric
            icon={Clock3}
            label={zh ? '今日复习' : 'Due today'}
            value={reviewCount}
            emphasis={reviewCount ? 'primary' : 'muted'}
          />
          <Metric
            icon={Layers3}
            label={zh ? '历史产物' : 'Learning artifacts'}
            value={artifactCount}
            emphasis="muted"
          />
        </section>

        <section className="mt-8">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="font-serif text-2xl">{zh ? '进行中的路径' : 'Active paths'}</h2>
              <span className="learning-meta">{zh ? '证据自适应' : 'Evidence-adaptive'}</span>
            </div>
            {!loading && !error && active.length ? (
              <div className="flex flex-wrap items-center justify-end gap-2">
                <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-lg border border-[var(--border)] px-3 text-xs hover:bg-[var(--muted)]">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleSelectAll}
                    className="h-4 w-4 accent-[var(--primary)]"
                  />
                  <CheckSquare2 size={14} />
                  {allSelected ? (zh ? '取消全选' : 'Clear all') : zh ? '全选' : 'Select all'}
                </label>
                {selectedIds.length ? (
                  <button
                    type="button"
                    onClick={() => setPendingDeleteIds(selectedIds)}
                    className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-[var(--destructive)]/40 px-3 text-xs font-medium text-[var(--destructive)] hover:bg-[var(--destructive)]/10"
                  >
                    <Trash2 size={14} />
                    {zh
                      ? `删除已选（${selectedIds.length}）`
                      : `Delete selected (${selectedIds.length})`}
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>

          {loading ? (
            <div className="learning-copy-muted mt-6 flex items-center gap-2 text-sm">
              <Loader2 size={16} className="animate-spin" />
              {zh ? '正在读取学习路径' : 'Loading paths'}
            </div>
          ) : null}
          {error ? (
            <p className="learning-alert--error mt-6 p-4 text-sm">
              {zh
                ? '学习路径暂不可用，请稍后刷新。'
                : 'Learning paths are temporarily unavailable.'}
            </p>
          ) : null}
          {mutationMessage?.tone === 'error' ? (
            <p
              role="alert"
              className="mt-5 rounded-lg border border-[var(--destructive)]/30 bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)]"
            >
              {mutationMessage.text}
            </p>
          ) : null}
          {mutationMessage?.tone === 'success' && active.length ? (
            <p
              role="status"
              aria-live="polite"
              aria-atomic="true"
              data-testid="learning-mutation-success"
              className="mt-5 inline-flex max-w-full items-center gap-2 rounded-xl border border-[var(--primary)]/35 bg-[var(--learning-panel)] px-3.5 py-2.5 text-xs text-[var(--foreground)] shadow-sm"
            >
              <CheckCircle2 size={15} className="shrink-0 text-[var(--primary)]" />
              <span>{mutationMessage.text}</span>
            </p>
          ) : null}
          {!loading && !error && !active.length ? (
            <div
              data-testid="learning-empty-state"
              className={`relative ${mutationMessage?.tone === 'success' ? 'mt-8 pt-5' : 'mt-6'}`}
            >
              {mutationMessage?.tone === 'success' ? (
                <p
                  role="status"
                  aria-live="polite"
                  aria-atomic="true"
                  data-testid="learning-mutation-success"
                  className="absolute left-4 top-0 z-10 inline-flex max-w-[calc(100%_-_2rem)] items-center gap-2 rounded-xl border border-[var(--primary)]/40 bg-[var(--learning-panel)] px-3.5 py-2.5 text-xs text-[var(--foreground)] shadow-sm sm:left-6"
                >
                  <CheckCircle2 size={15} className="shrink-0 text-[var(--primary)]" />
                  <span>{mutationMessage.text}</span>
                </p>
              ) : null}
              <div
                data-testid="learning-empty-card"
                className={`learning-card learning-card--large border-dashed p-10 text-center ${
                  mutationMessage?.tone === 'success' ? 'pt-14 sm:pt-12' : ''
                }`}
              >
                <Sparkles className="learning-accent mx-auto" />
                <p className="mt-4 font-serif text-xl">
                  {zh ? '还没有进行中的学习目标' : 'No active learning goal yet'}
                </p>
                <Link
                  href="/home"
                  className="learning-accent mt-4 inline-flex items-center gap-2 text-sm"
                >
                  {zh ? '告诉 TraitTutor 你想学什么' : 'Tell TraitTutor what you want to learn'}
                  <ArrowRight size={14} />
                </Link>
              </div>
            </div>
          ) : null}
          <div className="mt-5 grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {active.map(pack => (
              <LearningPathCard
                key={pack.pack_id}
                pack={pack}
                zh={zh}
                selected={selectedIds.includes(pack.pack_id)}
                disabled={deleting}
                onToggle={() => toggleSelected(pack.pack_id)}
                onDelete={() => setPendingDeleteIds([pack.pack_id])}
              />
            ))}
          </div>
        </section>

        {!loading && !error && completed.length ? (
          <section className="mt-10">
            <div>
              <h2 className="font-serif text-2xl">
                {zh ? '已走完的学习轮次' : 'Completed learning rounds'}
              </h2>
              <p className="learning-copy-muted mt-2 text-sm">
                {zh
                  ? '这里表示本轮组件已经完成，不等于系统已判定掌握；答案、解析和复习仍可回看。'
                  : 'A completed round means its components are finished, not that mastery is proven. Answers, explanations, and reviews remain available.'}
              </p>
            </div>
            <div className="mt-5 grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {completed.map(pack => (
                <LearningPathCard
                  key={pack.pack_id}
                  pack={pack}
                  zh={zh}
                  selected={false}
                  selectable={false}
                  disabled={deleting}
                  onToggle={() => undefined}
                  onDelete={() => setPendingDeleteIds([pack.pack_id])}
                />
              ))}
            </div>
          </section>
        ) : null}

        <section className="mt-8 grid gap-4 md:grid-cols-2">
          <ArchivePanel
            icon={FileText}
            title={zh ? '学习材料' : 'Learning sources'}
            note={
              zh
                ? '原文件、分析快照、概念和页码证据'
                : 'Original files, analysis snapshots, concepts, and page evidence'
            }
            count={packs.filter(pack => (pack.materials?.length ?? 0) > 0).length}
          />
          <ArchivePanel
            icon={BookOpen}
            title={zh ? '历史产物' : 'Artifact history'}
            note={
              zh
                ? '课件、闪卡、Quiz、图解和语音仍可回看与导出'
                : 'Lessons, flashcards, quizzes, diagrams, and audio remain available'
            }
            count={artifactCount}
          />
        </section>
      </div>

      <ConfirmDialog
        open={Boolean(pendingDeleteIds?.length)}
        title={
          pendingDeleteIds?.length === 1
            ? zh
              ? '删除这条学习路径？'
              : 'Delete this learning path?'
            : zh
              ? `删除 ${pendingDeleteIds?.length ?? 0} 条学习路径？`
              : `Delete ${pendingDeleteIds?.length ?? 0} learning paths?`
        }
        confirmLabel={zh ? '确认删除' : 'Delete'}
        cancelLabel={zh ? '取消' : 'Cancel'}
        busy={deleting}
        busyLabel={zh ? '正在删除…' : 'Deleting…'}
        tone="danger"
        onConfirm={() => void confirmDelete()}
        onCancel={closeDeleteDialog}
      >
        <div className="space-y-2">
          {pendingTitles.length === 1 ? (
            <p className="font-medium text-[var(--foreground)]">{pendingTitles[0]}</p>
          ) : null}
          <p>
            {zh
              ? '学习包中的目标、进度、练习和复习记录将从“我的学习”中删除。学习画像和长期记忆不会被删除。'
              : 'The goals, progress, practice, and review records in these learning packs will be removed from My learning. Your learning profile and long-term memory are not deleted.'}
          </p>
        </div>
      </ConfirmDialog>
    </main>
  )
}

function LearningPathCard({
  pack,
  zh,
  selected,
  selectable = true,
  disabled,
  onToggle,
  onDelete,
}: {
  pack: LearningPack
  zh: boolean
  selected: boolean
  selectable?: boolean
  disabled: boolean
  onToggle: () => void
  onDelete: () => void
}) {
  const plan = pack.component_plans?.find(item => item.plan_id === pack.active_plan_id)
  const movesDone = plan?.components.filter(item => item.status === 'completed').length ?? 0
  const movesTotal = plan?.components.length ?? 0
  const due = pack.due_review_count ?? 0
  const nextReview = nextReviewLabel(pack, zh)

  return (
    <article
      className={`learning-card learning-card--large transition ${
        selected
          ? 'border-[var(--primary)] ring-1 ring-[var(--primary)]/30'
          : 'hover:border-[var(--primary)]'
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <span className="learning-icon-badge">
          {due ? <Clock3 size={17} /> : <Route size={17} />}
        </span>
        <div className="flex items-center gap-1">
          {selectable ? (
            <label className="inline-flex min-h-9 cursor-pointer items-center gap-2 rounded-lg px-2 text-xs text-[var(--muted-foreground)] hover:bg-[var(--muted)]">
              <input
                type="checkbox"
                checked={selected}
                disabled={disabled}
                onChange={onToggle}
                className="h-4 w-4 accent-[var(--primary)]"
                aria-label={
                  zh
                    ? `选择 ${pack.goal?.text ?? pack.title}`
                    : `Select ${pack.goal?.text ?? pack.title}`
                }
              />
              {zh ? '选择' : 'Select'}
            </label>
          ) : null}
          <button
            type="button"
            disabled={disabled}
            onClick={onDelete}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-[var(--muted-foreground)] hover:bg-[var(--destructive)]/10 hover:text-[var(--destructive)] disabled:opacity-50"
            aria-label={
              zh
                ? `删除 ${pack.goal?.text ?? pack.title}`
                : `Delete ${pack.goal?.text ?? pack.title}`
            }
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      <Link
        href={`/learning/${pack.pack_id}`}
        className="group block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"
      >
        <h3 className="mt-6 font-serif text-xl">{pack.goal?.text ?? pack.title}</h3>
        <p className="learning-copy-muted mt-2 text-xs">
          {due
            ? zh
              ? `今日复习 ${due} 项 · 预计 ${due} 分钟`
              : `${due} due today · about ${due} min`
            : String(plan?.subject_ref?.label ?? nextReview)}
        </p>
        <div className="mt-6 h-1 overflow-hidden rounded-full bg-[var(--muted)]">
          <div
            className="h-full bg-[var(--primary)]"
            style={{ width: movesTotal ? `${Math.round((movesDone / movesTotal) * 100)}%` : '0%' }}
          />
        </div>
        <div className="learning-meta mt-2 flex justify-between">
          <span>
            {movesDone}/{movesTotal} {zh ? '学习动作' : 'moves'}
          </span>
          <span className="inline-flex items-center gap-1">
            {nextReview}
            <ArrowRight size={14} className="transition group-hover:translate-x-1" />
          </span>
        </div>
      </Link>
    </article>
  )
}

function Metric({
  icon: Icon,
  label,
  value,
  emphasis,
}: {
  icon: typeof Route
  label: string
  value: number
  emphasis: 'primary' | 'muted'
}) {
  const color = emphasis === 'primary' ? 'learning-accent' : 'learning-copy-muted'
  return (
    <div className="learning-card">
      <Icon size={16} className={color} />
      <p className="mt-5 font-serif text-3xl">{value}</p>
      <p className="learning-copy-muted mt-1 text-xs">{label}</p>
    </div>
  )
}

function ArchivePanel({
  icon: Icon,
  title,
  note,
  count,
}: {
  icon: typeof FileText
  title: string
  note: string
  count: number
}) {
  return (
    <div className="learning-card">
      <div className="flex items-center justify-between">
        <Icon size={17} className="learning-copy-muted" />
        <span className="learning-meta text-xs">{count}</span>
      </div>
      <h3 className="mt-5 font-serif text-lg">{title}</h3>
      <p className="learning-copy-muted mt-2 text-xs leading-5">{note}</p>
    </div>
  )
}

function nextReviewLabel(pack: LearningPack, zh: boolean): string {
  if ((pack.due_review_count ?? 0) > 0) return zh ? '现在复习' : 'Review now'
  const next = Date.parse(pack.next_review_at ?? '')
  if (!Number.isFinite(next)) {
    return zh ? '完成练习后安排复习' : 'Review scheduled after practice'
  }
  return new Intl.DateTimeFormat(zh ? 'zh-CN' : 'en', {
    month: 'short',
    day: 'numeric',
  }).format(next)
}
