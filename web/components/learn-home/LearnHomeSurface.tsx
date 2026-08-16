'use client'

import LearningPathLaunch, {
  type LearningPathState,
} from '@/components/chat/home/LearningPathLaunch'
import type { LearnIntentResult } from '@/lib/learning-intent-api'
import type { ProgressSummary } from '@/lib/learning-api'

/** Server routing decision card shown on the Learn home surface. Purely
 *  presentational: building the path itself stays with the page so the
 *  Learn entry keeps its direct Pack/Plan creation path (invariant 11). */
export function LearnRouteDecisionCard({
  decision,
  zh,
  onBuildPath,
}: {
  decision: { content: string; result: LearnIntentResult }
  zh: boolean
  onBuildPath: () => void
}) {
  const blocked = decision.result.safety_action === 'block'
  const description = blocked
    ? zh
      ? '请移除会改变系统行为的指令后，再描述学习目标或重新上传材料。'
      : 'Remove instructions that try to change system behavior, then describe your learning goal or upload the source again.'
    : zh
      ? '请确认后继续建立学习路径。'
      : 'Confirm to continue building the learning path.'
  return (
    <section
      role={blocked ? 'alert' : 'status'}
      className="rounded-2xl border border-[var(--primary)]/30 bg-[var(--card)] p-5 shadow-sm"
    >
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--primary)]">
        {blocked
          ? zh
            ? '需要重新描述'
            : 'Please rephrase'
          : zh
            ? '选择继续方式'
            : 'Choose how to continue'}
      </p>
      <p className="mt-2 text-sm leading-6 text-[var(--muted-foreground)]">{description}</p>
      {blocked ? null : (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            onClick={onBuildPath}
            className="rounded-xl bg-[var(--primary)] px-4 py-2 text-xs font-semibold text-[var(--primary-foreground)] transition hover:opacity-90"
          >
            {zh ? '建立学习路径' : 'Build a learning path'}
          </button>
        </div>
      )}
    </section>
  )
}

/** The Learn-only workspace status card: current goal, my-learning link,
 *  route decision, and learning-path launch. Never rendered on /assist. */
export function LearnWorkspaceStatus({
  zh,
  learningPath,
  routeDecision,
  onBuildPath,
}: {
  zh: boolean
  learningPath: LearningPathState | null
  routeDecision: { content: string; result: LearnIntentResult } | null
  onBuildPath: (content: string) => void
}) {
  return (
    <section
      aria-label={zh ? '学习工作区状态' : 'Learning workspace status'}
      className="learning-home-card overflow-hidden rounded-2xl border p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-[0.2em] text-[var(--primary)]">
            {zh ? '学习工作区' : 'Learning workspace'}
          </p>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {learningPath?.goal
              ? zh
                ? `当前目标：${learningPath.goal}`
                : `Current goal: ${learningPath.goal}`
              : zh
                ? '答疑不会自动算作掌握；建立路径后才会记录学习证据。'
                : 'A chat answer is not mastery; a path records learning evidence.'}
          </p>
        </div>
        <a
          href={learningPath?.packId ? `/learning/${learningPath.packId}` : '/learning'}
          className="rounded-lg border border-[var(--primary)]/30 bg-[var(--card)] px-3 py-1.5 text-[10px] font-semibold text-[var(--primary)] transition hover:border-[var(--primary)]"
        >
          {zh ? '我的学习' : 'My learning'}
        </a>
      </div>
      <div className="mt-3 space-y-3">
        {routeDecision ? (
          <LearnRouteDecisionCard
            decision={routeDecision}
            zh={zh}
            onBuildPath={() => onBuildPath(routeDecision.content)}
          />
        ) : null}
        {learningPath ? <LearningPathLaunch path={learningPath} zh={zh} /> : null}
      </div>
    </section>
  )
}

/** The Learn-only mastery practice picker. The browser may select only an
 *  already-persisted path ID; subject/KC attribution stays server-side. */
export function MasteryPathPicker({
  zh,
  streaming,
  state,
  paths,
  selectablePaths,
  selectedPathId,
  onSelectPathId,
  onStart,
}: {
  zh: boolean
  streaming: boolean
  state: 'idle' | 'loading' | 'ready' | 'error'
  paths: ProgressSummary[]
  selectablePaths: ProgressSummary[]
  selectedPathId: string
  onSelectPathId: (id: string) => void
  onStart: () => void
}) {
  return (
    <section
      aria-label={zh ? '选择掌握练习路径' : 'Choose mastery practice path'}
      className="mx-auto w-full max-w-[960px] px-6 pb-2"
    >
      <div className="rounded-xl border border-[var(--border)] bg-[var(--card)] px-4 py-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="font-medium text-[var(--foreground)]">
              {zh ? '掌握练习路径' : 'Mastery practice path'}
            </p>
            <p className="mt-0.5 text-xs leading-5 text-[var(--muted-foreground)]">
              {zh
                ? '仅可选择当前账户中已确认主体与知识点图谱的学习路径；浏览器不会提交主体或知识点。'
                : 'Choose a current-account path with a confirmed subject and knowledge graph; the browser never submits subject or KC data.'}
            </p>
          </div>
          <button
            type="button"
            onClick={onStart}
            disabled={streaming || state !== 'ready' || !selectedPathId}
            className="inline-flex h-9 items-center rounded-lg bg-[var(--primary)] px-3 text-xs font-semibold text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {zh ? '开始掌握练习' : 'Start mastery practice'}
          </button>
        </div>
        <label className="mt-3 block max-w-xl space-y-1.5">
          <span className="text-xs font-medium text-[var(--foreground)]">
            {zh ? '学习路径' : 'Learning path'}
          </span>
          <select
            aria-label={zh ? '学习路径' : 'Learning path'}
            value={selectedPathId}
            onChange={event => onSelectPathId(event.target.value)}
            disabled={streaming || state !== 'ready' || !selectablePaths.length}
            className="h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 text-sm text-[var(--foreground)] outline-none focus:border-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-55"
          >
            <option value="">
              {state === 'loading'
                ? zh
                  ? '正在加载可用路径…'
                  : 'Loading available paths…'
                : zh
                  ? '选择一条学习路径'
                  : 'Choose a learning path'}
            </option>
            {selectablePaths.map(path => (
              <option key={path.book_id} value={path.book_id}>
                {path.name}
              </option>
            ))}
          </select>
        </label>
        {state === 'error' ? (
          <p role="alert" className="mt-2 text-xs text-[var(--destructive)]">
            {zh
              ? '无法读取你的学习路径，尚未开始掌握练习。请稍后重试。'
              : 'Your learning paths could not be loaded; mastery practice has not started. Try again later.'}
          </p>
        ) : state === 'ready' && !paths.length ? (
          <p className="mt-2 text-xs text-[var(--muted-foreground)]">
            {zh
              ? '当前账户还没有学习路径。请先建立并确认一条学习路径。'
              : 'This account has no learning paths yet. Create and confirm a path first.'}
          </p>
        ) : state === 'ready' && !selectablePaths.length ? (
          <p className="mt-2 text-xs text-[var(--muted-foreground)]">
            {zh
              ? '现有学习路径尚未具备可验证的主体与知识点图谱，因此不会开始掌握练习。'
              : 'Existing paths do not yet have a verifiable subject and knowledge graph, so mastery practice cannot start.'}
          </p>
        ) : null}
      </div>
    </section>
  )
}
