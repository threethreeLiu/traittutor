'use client'

import Link from 'next/link'
import { type ChangeEvent, type DragEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Clock3,
  FileText,
  FileUp,
  Layers3,
  Loader2,
  Route,
  Sparkles,
  Upload,
  type LucideIcon,
} from 'lucide-react'
import { ATTACHMENT_ACCEPT } from '@/lib/doc-attachments'
import {
  LEARNING_PACKS_INVALIDATED_EVENT,
  listLearningPacks,
  type LearningPack,
} from '@/lib/traittutor-api'
import HomeAttachmentTray, { type HomePendingAttachment } from './HomeAttachmentTray'

export const MAX_LEARNING_HOME_FILES = 5

export default function LearningHomeIntro({
  zh,
  onBuildPath,
  onFiles,
  attachments,
  attachmentError,
  onRemoveAttachment,
  starting,
  pathStatus,
  pathError,
}: {
  zh: boolean
  onBuildPath: (goal: string) => void
  onFiles: (files: File[]) => void
  attachments: HomePendingAttachment[]
  attachmentError: string | null
  onRemoveAttachment: (index: number) => void
  starting: boolean
  pathStatus?: 'creating' | 'ready' | 'error' | null
  pathError?: string | null
}) {
  const [packs, setPacks] = useState<LearningPack[]>([])
  const [packsLoading, setPacksLoading] = useState(true)
  const [packsError, setPacksError] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const hasMaterial = attachments.length > 0

  useEffect(() => {
    let cancelled = false
    const load = () => {
      void listLearningPacks()
        .then(items => {
          if (!cancelled) setPacks(items)
        })
        .catch(() => {
          if (!cancelled) setPacksError(true)
        })
        .finally(() => {
          if (!cancelled) setPacksLoading(false)
        })
    }
    load()
    // Deleting a chat session cascades server-side to its linked Packs; the
    // sidebar broadcasts this event so "Continue learning" refetches instead
    // of showing packs that no longer exist.
    window.addEventListener(LEARNING_PACKS_INVALIDATED_EVENT, load)
    return () => {
      cancelled = true
      window.removeEventListener(LEARNING_PACKS_INVALIDATED_EVENT, load)
    }
  }, [])

  const activePacks = useMemo(
    () => packs.filter(pack => pack.goal?.status !== 'completed').slice(0, 2),
    [packs]
  )
  const duePack = useMemo(
    () => packs.find(pack => (pack.due_review_count ?? 0) > 0) ?? null,
    [packs]
  )
  const dueCount = useMemo(
    () => packs.reduce((total, pack) => total + (pack.due_review_count ?? 0), 0),
    [packs]
  )
  const artifactCount = useMemo(
    () =>
      packs.reduce(
        (total, pack) =>
          total + Object.values(pack.artifacts ?? {}).reduce((sum, items) => sum + items.length, 0),
        0
      ),
    [packs]
  )

  const addFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length) onFiles(files)
    event.target.value = ''
  }

  const addDroppedFiles = (event: DragEvent<HTMLFormElement>) => {
    event.preventDefault()
    const files = Array.from(event.dataTransfer.files)
    if (files.length) onFiles(files)
  }

  const greeting = getGreeting(zh)

  return (
    <section className="w-full animate-fade-in px-1 pb-8">
      <header className="mb-7 px-1 sm:mb-8">
        <p className="learning-home-kicker">
          {zh ? 'TraitTutor · 学习工作台' : 'TraitTutor · Learning workspace'}
        </p>
        <h1 className="mt-3 font-serif text-[clamp(1.85rem,4vw,2.55rem)] font-semibold tracking-[-0.035em] text-[var(--foreground)]">
          {greeting}
        </h1>
        <p className="mt-2 text-[14px] text-[var(--muted-foreground)] sm:text-[15px]">
          {packs.length
            ? zh
              ? '从上次的位置继续，或上传新的课程材料。'
              : 'Continue where you left off, or add a new source to learn from.'
            : zh
              ? '上传一份课程材料，TraitTutor 会从材料开始建立学习路径。'
              : 'Upload a course source and TraitTutor will build a learning path from it.'}
        </p>
      </header>

      <form
        onSubmit={event => {
          event.preventDefault()
          if (hasMaterial) onBuildPath('')
        }}
        onDragOver={event => event.preventDefault()}
        onDrop={addDroppedFiles}
        className="learning-home-composer rounded-[24px] border p-3 transition sm:p-4"
      >
        <div className="flex min-h-[124px] flex-col justify-between rounded-[18px] border border-dashed border-[var(--border)] bg-[var(--background)]/60 p-4 sm:p-5">
          <div className="flex items-start gap-3">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--primary)]/[0.1] text-[var(--primary)]">
              <Upload size={17} />
            </span>
            <div>
              <p className="text-[14px] font-semibold text-[var(--foreground)]">
                {zh ? '添加学习材料' : 'Add learning material'}
              </p>
              <p className="mt-1 text-[12px] leading-5 text-[var(--muted-foreground)]">
                {zh
                  ? `支持 PDF、PPT、Word、Markdown 和图片，最多 ${MAX_LEARNING_HOME_FILES} 个文件。上传后会直接进入学习路径。`
                  : `PDF, PPT, Word, Markdown, and images, up to ${MAX_LEARNING_HOME_FILES} files. Uploading goes directly into a learning path.`}
              </p>
            </div>
          </div>
          <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
            <label className="learning-home-upload-button inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-xl border px-3.5 text-[12px] font-semibold transition">
              <FileUp size={15} />
              {zh ? '选择材料' : 'Choose source'}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                disabled={starting}
                accept={ATTACHMENT_ACCEPT}
                className="sr-only"
                onChange={addFiles}
              />
            </label>
            <p className="text-[11px] text-[var(--muted-foreground)]">
              {zh ? '也可以直接拖放到这里' : 'You can also drop files here'}
            </p>
          </div>
        </div>

        <HomeAttachmentTray
          attachments={attachments}
          error={attachmentError}
          onRemove={onRemoveAttachment}
          zh={zh}
        />
        {hasMaterial ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 px-1">
            <p className="text-[11px] text-[var(--muted-foreground)]">
              {zh
                ? '材料已就绪，准备建立学习路径。'
                : 'Source ready. Build your learning path when ready.'}
            </p>
            <button
              type="submit"
              disabled={starting}
              className="learning-home-submit inline-flex min-h-10 items-center gap-2 rounded-xl px-4 text-[12px] font-semibold transition disabled:cursor-not-allowed disabled:opacity-50"
            >
              {starting ? <Loader2 size={15} className="animate-spin" /> : <Route size={15} />}
              {starting
                ? zh
                  ? '正在建立…'
                  : 'Building…'
                : zh
                  ? '建立学习路径'
                  : 'Build learning path'}
              {!starting ? <ArrowRight size={14} /> : null}
            </button>
          </div>
        ) : null}
        {pathStatus === 'error' ? (
          <p role="alert" className="mt-3 px-1 text-[11px] text-[var(--destructive)]">
            {pathError ??
              (zh
                ? '学习路径暂未建立，请重新提交材料。'
                : 'The learning path could not be created. Please submit the source again.')}
          </p>
        ) : null}
      </form>

      {!packsLoading && !packs.length ? (
        <section className="mt-8">
          <SectionHeading icon={Sparkles} title={zh ? '从这里开始' : 'Start here'} />
          {packsError ? <InlineError zh={zh} /> : null}
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            <QuickSourceCard
              icon={BookOpen}
              title={zh ? '上传课程材料' : 'Upload course material'}
              note={zh ? '从一份 PDF、PPT 或讲义开始。' : 'Start with a PDF, deck, or handout.'}
              onClick={() => fileInputRef.current?.click()}
            />
            <QuickSourceCard
              icon={FileText}
              title={zh ? '上传课堂笔记' : 'Upload class notes'}
              note={
                zh ? '把零散笔记整理成清晰的学习路径。' : 'Turn scattered notes into a clear path.'
              }
              onClick={() => fileInputRef.current?.click()}
            />
            <QuickSourceCard
              icon={Layers3}
              title={zh ? '查看我的学习' : 'Open my learning'}
              note={zh ? '管理已有的学习包和学习记录。' : 'Manage learning packs and records.'}
              href="/learning"
            />
          </div>
        </section>
      ) : null}

      {packs.length ? (
        <>
          <section className="mt-9">
            <div className="flex items-center justify-between gap-3">
              <SectionHeading icon={BookOpen} title={zh ? '继续学习' : 'Continue learning'} />
              <Link href="/learning" className="learning-home-text-link">
                {zh ? '查看全部' : 'View all'} <ArrowRight size={13} />
              </Link>
            </div>
            {packsError ? <InlineError zh={zh} /> : null}
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {activePacks.map(pack => (
                <ContinueCard key={pack.pack_id} pack={pack} zh={zh} />
              ))}
            </div>
          </section>

          <section className="mt-8 grid gap-3 lg:grid-cols-[1.35fr_0.65fr]">
            <TodayCard pack={duePack} dueCount={dueCount} zh={zh} />
            <SnapshotCard packs={packs} dueCount={dueCount} artifactCount={artifactCount} zh={zh} />
          </section>
        </>
      ) : null}
    </section>
  )
}

function getGreeting(zh: boolean): string {
  const hour = new Date().getHours()
  if (zh) return hour < 12 ? '上午好' : hour < 18 ? '下午好' : '晚上好'
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

function SectionHeading({ icon: Icon, title }: { icon: LucideIcon; title: string }) {
  return (
    <h2 className="flex items-center gap-2 font-serif text-[19px] font-semibold tracking-[-0.02em]">
      <Icon size={17} className="text-[var(--primary)]" />
      {title}
    </h2>
  )
}

function QuickSourceCard({
  icon: Icon,
  title,
  note,
  href,
  onClick,
}: {
  icon: LucideIcon
  title: string
  note: string
  href?: string
  onClick?: () => void
}) {
  const className =
    'learning-home-quick-card group flex min-h-[112px] flex-col justify-between rounded-2xl border bg-[var(--card)] p-4 text-left transition'
  const content = (
    <>
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-[var(--primary)]/[0.09] text-[var(--primary)]">
        <Icon size={16} />
      </span>
      <span>
        <span className="mt-3 block text-[12px] font-semibold">{title}</span>
        <span className="mt-1 block text-[11px] leading-4 text-[var(--muted-foreground)]">
          {note}
        </span>
      </span>
    </>
  )
  return href ? (
    <Link href={href} className={className}>
      {content}
    </Link>
  ) : (
    <button type="button" onClick={onClick} className={className}>
      {content}
    </button>
  )
}

function ContinueCard({ pack, zh }: { pack: LearningPack; zh: boolean }) {
  const title = pack.title || pack.goal?.text || (zh ? '未命名学习路径' : 'Untitled learning path')
  const plan = pack.component_plans?.find(item => item.plan_id === pack.active_plan_id)
  const completed = plan?.components.filter(item => item.status === 'completed').length ?? 0
  const total = plan?.components.length ?? 0
  const next = plan?.components.find(item => item.status === 'active' || item.status === 'pending')
  return (
    <Link
      href={`/learning/${pack.pack_id}`}
      className="learning-home-continue-card group rounded-2xl border bg-[var(--card)] p-4 transition"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="truncate text-[13px] font-semibold">{title}</p>
          <p className="mt-1 truncate text-[11px] text-[var(--muted-foreground)]">
            {next ? (zh ? next.label_zh : next.label_en) : pack.goal?.text}
          </p>
        </div>
        <ArrowRight
          size={15}
          className="shrink-0 text-[var(--muted-foreground)] transition group-hover:translate-x-0.5 group-hover:text-[var(--primary)]"
        />
      </div>
      <div className="mt-5 flex items-center gap-3">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--muted)]">
          <div
            className="h-full rounded-full bg-[var(--primary)] transition-all"
            style={{ width: `${total ? Math.round((completed / total) * 100) : 0}%` }}
          />
        </div>
        <span className="text-[10px] tabular-nums text-[var(--muted-foreground)]">
          {total ? `${completed}/${total}` : zh ? '准备中' : 'Preparing'}
        </span>
      </div>
      <p className="mt-3 text-[10.5px] text-[var(--muted-foreground)]">
        {next
          ? `${zh ? '下一步：' : 'Next: '}${zh ? next.label_zh : next.label_en}`
          : zh
            ? '进入学习路径查看详情'
            : 'Open the learning path for details'}
      </p>
    </Link>
  )
}

function TodayCard({
  pack,
  dueCount,
  zh,
}: {
  pack: LearningPack | null
  dueCount: number
  zh: boolean
}) {
  return (
    <section className="rounded-2xl border border-[var(--primary)]/20 bg-[var(--primary)]/[0.045] p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="learning-home-kicker">{zh ? '今天建议' : 'Today for you'}</p>
          <h2 className="mt-2 font-serif text-[19px] font-semibold">
            {pack
              ? zh
                ? '先复习一个熟悉的知识点'
                : 'Start with a focused review'
              : zh
                ? '准备下一步学习'
                : 'Prepare your next study step'}
          </h2>
        </div>
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-[var(--card)] text-[var(--primary)]">
          <Clock3 size={17} />
        </span>
      </div>
      <p className="mt-3 max-w-xl text-[12px] leading-5 text-[var(--muted-foreground)]">
        {pack
          ? zh
            ? `当前有 ${dueCount} 个复习内容等待处理，从「${pack.title}」继续可以保持学习节奏。`
            : `${dueCount} review item${dueCount === 1 ? '' : 's'} are waiting. Continue with “${pack.title}” to keep your rhythm.`
          : zh
            ? '上传材料后，系统会根据你的学习记录安排下一步。'
            : 'Once you upload a source, your next step will be shaped by your learning record.'}
      </p>
      {pack ? (
        <Link
          href={`/learning/${pack.pack_id}`}
          className="mt-4 inline-flex items-center gap-2 rounded-xl bg-[var(--primary)] px-3.5 py-2.5 text-[11px] font-semibold text-[var(--primary-foreground)] transition hover:opacity-90"
        >
          {zh ? '开始复习' : 'Start review'}
          <ArrowRight size={14} />
        </Link>
      ) : null}
    </section>
  )
}

function SnapshotCard({
  packs,
  dueCount,
  artifactCount,
  zh,
}: {
  packs: LearningPack[]
  dueCount: number
  artifactCount: number
  zh: boolean
}) {
  return (
    <section className="rounded-2xl border bg-[var(--card)] p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-serif text-[19px] font-semibold">
          {zh ? '学习状态' : 'Learning snapshot'}
        </h2>
        <CheckCircle2 size={17} className="text-[var(--primary)]" />
      </div>
      <div className="mt-5 grid grid-cols-3 gap-2">
        <SnapshotMetric
          label={zh ? '进行中' : 'Active'}
          value={packs.filter(pack => pack.goal?.status !== 'completed').length}
        />
        <SnapshotMetric label={zh ? '待复习' : 'Due'} value={dueCount} />
        <SnapshotMetric label={zh ? '学习产物' : 'Artifacts'} value={artifactCount} />
      </div>
      <Link href="/settings/learning-model" className="learning-home-text-link mt-5 inline-flex">
        {zh ? '查看学习画像' : 'View learning profile'}
        <ArrowRight size={13} />
      </Link>
    </section>
  )
}

function SnapshotMetric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <p className="text-xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-[10px] text-[var(--muted-foreground)]">{label}</p>
    </div>
  )
}
function InlineError({ zh }: { zh: boolean }) {
  return (
    <p role="status" className="mt-3 text-[11px] text-[var(--muted-foreground)]">
      {zh ? '部分学习状态暂时无法读取。' : 'Some learning status is temporarily unavailable.'}
    </p>
  )
}
