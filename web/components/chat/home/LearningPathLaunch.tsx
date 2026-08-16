'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  CircleDot,
  Loader2,
  Route,
  ShieldCheck,
} from 'lucide-react'

import ArrangementProgress from '@/components/learning/ArrangementProgress'
import PersonalizationChoiceCard from '@/components/learning/PersonalizationChoiceCard'
import PreAssessmentCard from '@/components/learning/PreAssessmentCard'
import {
  arrangeLearningComponentPlan,
  generationErrorMessage,
  judgeAndGeneratePreAssessment,
  skipPreAssessment,
  submitPreAssessment,
  TraitTutorApiError,
  updateLearningPack,
  type LearningComponentPlan,
  type PreAssessmentDecision,
  type PreAssessmentResult,
} from '@/lib/traittutor-api'

const TRAIT_LOOP_STAGE = 'Trait Loop · 01'

type ArrangementPhase =
  | 'idle'
  | 'choose'
  | 'judging'
  | 'question'
  | 'answered'
  | 'arranging'
  | 'complete'
  | 'error'
type RequiredPreAssessment = Extract<PreAssessmentDecision, { needed: true }>

export type LearningPathState = {
  goal: string
  packId?: string | null
  plan?: LearningComponentPlan | null
  status: 'creating' | 'ready' | 'error'
}

function arrangementFailureMessage(
  error: unknown,
  zh: boolean,
  phase: 'judge' | 'submit' | 'arrange'
): string {
  const code = error instanceof TraitTutorApiError ? error.code : undefined
  if (code === 'model_configuration_required') return generationErrorMessage(code, zh)
  if (code === 'pre_assessment_failed') {
    return zh
      ? '前置提问暂时无法生成，请重试。'
      : 'The pre-assessment could not be generated. Please retry.'
  }
  if (phase === 'arrange') {
    return zh
      ? '学习路径排列暂时失败，已保留基础路径。'
      : 'Path arrangement failed; the basic path is still available.'
  }
  if (phase === 'submit') {
    return zh ? '答案暂时无法提交，请重试。' : 'The answers could not be submitted. Please retry.'
  }
  return zh
    ? '无法判断是否需要前置提问，请重试。'
    : 'Could not decide whether a pre-assessment is needed. Please retry.'
}

function safeStartUrl(path: LearningPathState, plan: LearningComponentPlan | null): string {
  const candidate = plan?.start_url ?? (path.packId ? `/learning/${path.packId}` : '/learning')
  return candidate.startsWith('/') && !candidate.startsWith('//') ? candidate : '/learning'
}

export default function LearningPathLaunch({ path, zh }: { path: LearningPathState; zh: boolean }) {
  const router = useRouter()
  const [plan, setPlan] = useState<LearningComponentPlan | null>(path.plan ?? null)
  const [phase, setPhase] = useState<ArrangementPhase>('idle')
  const [preAssessment, setPreAssessment] = useState<RequiredPreAssessment | null>(null)
  const [preAssessmentResult, setPreAssessmentResult] = useState<PreAssessmentResult | null>(null)
  const [arrangementError, setArrangementError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [basicPathAccepted, setBasicPathAccepted] = useState(false)
  const startedFlowKey = useRef<string | null>(null)
  const packId = path.packId ?? null
  // One submit event id per pre-assessment evaluation (see submitStartingPoint).
  const preAssessmentEventIdRef = useRef<string | null>(null)

  const arrangePath = useCallback(async () => {
    if (!packId) return
    setPhase('arranging')
    setArrangementError(null)
    try {
      const arranged = await arrangeLearningComponentPlan(packId)
      setPlan(arranged)
      if (arranged.arrangement === 'llm') {
        setBasicPathAccepted(false)
        setPhase('complete')
        return
      }
      setArrangementError(
        zh
          ? '自动排列暂时不可用，已保留基础路径。你可以重试，或使用基础路径继续。'
          : 'Smart arrangement is unavailable. Retry, or continue with the basic path.'
      )
      setPhase('error')
    } catch (error) {
      setArrangementError(arrangementFailureMessage(error, zh, 'arrange'))
      setPhase('error')
    }
  }, [packId, zh])

  const judgePreAssessment = useCallback(async () => {
    if (!packId) return
    setPhase('judging')
    setArrangementError(null)
    try {
      const decision = await judgeAndGeneratePreAssessment(packId)
      if (!decision.needed) {
        await arrangePath()
        return
      }
      if (decision.status !== 'pending') {
        await arrangePath()
        return
      }
      setPreAssessment(decision)
      setPhase('question')
    } catch (error) {
      setArrangementError(arrangementFailureMessage(error, zh, 'judge'))
      setPhase('error')
    }
  }, [arrangePath, packId, zh])

  /**
   * Persist the learner's arrangement choice (best effort — the in-session
   * flow never depends on it; the canvas uses it to suppress the pending
   * notice after a deliberate opt-out).
   */
  const persistArrangementChoice = useCallback(
    (preference: 'auto' | 'basic') => {
      if (!packId) return
      updateLearningPack(packId, { arrangement_preference: preference }).catch(() => undefined)
    },
    [packId]
  )

  /** The learner lets the LLM auto-select components: run judge → arrange. */
  const chooseAutoArrange = useCallback(() => {
    persistArrangementChoice('auto')
    void judgePreAssessment()
  }, [judgePreAssessment, persistArrangementChoice])

  /** The learner opts out of the LLM pipeline: start from the basic path. */
  const chooseBasicPath = useCallback(() => {
    persistArrangementChoice('basic')
    setBasicPathAccepted(true)
    setArrangementError(null)
    setPhase('complete')
  }, [persistArrangementChoice])

  useEffect(() => {
    if (path.status !== 'ready' || !path.plan || !packId) {
      setPlan(null)
      setPreAssessment(null)
      setPreAssessmentResult(null)
      setArrangementError(null)
      setBasicPathAccepted(false)
      setPhase('idle')
      return
    }
    if (path.plan.arrangement === 'llm') {
      setPlan(path.plan)
      setPhase('complete')
      return
    }
    if (path.plan.arrangement === 'deterministic_fallback') {
      setPlan(path.plan)
      setArrangementError(
        zh
          ? '自动排列暂时不可用，已保留基础路径。你可以重试，或使用基础路径继续。'
          : 'Smart arrangement is unavailable. Retry, or continue with the basic path.'
      )
      setPhase('error')
      return
    }

    const flowKey = `${packId}:${path.plan.plan_id}`
    if (startedFlowKey.current === flowKey) {
      // Same Pack + plan arriving again (idempotent replay of the same upload)
      // must not reset an arrangement that is already in flight or complete.
      // Preserve the current intermediate-page state instead of wiping it.
      return
    }
    startedFlowKey.current = flowKey
    setPlan(path.plan)
    setPreAssessment(null)
    preAssessmentEventIdRef.current = null
    setPreAssessmentResult(null)
    setArrangementError(null)
    setBasicPathAccepted(false)
    // Ask first whether the LLM may auto-select this path's components. The
    // judge → arrange pipeline only starts after an explicit learner choice.
    setPhase('choose')
  }, [judgePreAssessment, packId, path.plan, path.status, zh])

  // A re-judged assessment is a new evaluation: its answers need a fresh
  // event id (the previous one belongs to the old assessment's submission).
  useEffect(() => {
    preAssessmentEventIdRef.current = null
  }, [preAssessment?.assessment_id])

  const submitStartingPoint = useCallback(
    async (
      answers: Array<{
        question_id: string
        selected_index: number
      }>
    ) => {
      if (!packId || !preAssessment?.assessment_id) return
      setSubmitting(true)
      setArrangementError(null)
      try {
        // One event id per pre-assessment evaluation, reused across retries:
        // the server dedups a replay only when the same event_id comes back
        // with the same answers. A fresh uuid per submit would 409 on every
        // network-lost retry (and make skip 409 too), dead-locking the
        // intermediate page until a full refresh.
        const eventId = (preAssessmentEventIdRef.current ??= `pre-${crypto.randomUUID()}`)
        const result = await submitPreAssessment(
          packId,
          preAssessment.assessment_id,
          answers,
          eventId
        )
        setPreAssessmentResult(result)
        setPhase('answered')
      } catch (error) {
        setArrangementError(arrangementFailureMessage(error, zh, 'submit'))
      } finally {
        setSubmitting(false)
      }
    },
    [packId, preAssessment, zh]
  )

  const skipStartingPoint = useCallback(async () => {
    if (!packId || !preAssessment?.assessment_id) return
    setSubmitting(true)
    setArrangementError(null)
    try {
      await skipPreAssessment(packId, preAssessment.assessment_id)
      await arrangePath()
    } catch (error) {
      setArrangementError(arrangementFailureMessage(error, zh, 'submit'))
    } finally {
      setSubmitting(false)
    }
  }, [arrangePath, packId, preAssessment, zh])

  const retryArrangement = useCallback(() => {
    setBasicPathAccepted(false)
    if (
      plan?.arrangement === 'deterministic_fallback' ||
      preAssessmentResult ||
      (preAssessment && preAssessment.status !== 'pending')
    ) {
      void arrangePath()
      return
    }
    void judgePreAssessment()
  }, [arrangePath, judgePreAssessment, plan?.arrangement, preAssessment, preAssessmentResult])

  const finalPlanReady = plan?.arrangement === 'llm'
  const fallbackVisible =
    phase === 'error' || basicPathAccepted || plan?.arrangement === 'deterministic_fallback'
  const showSkeleton =
    path.status === 'creating' ||
    ['idle', 'judging', 'question', 'arranging'].includes(phase)
  const canStart =
    path.status === 'ready' && Boolean(packId) && (finalPlanReady || basicPathAccepted)
  const startUrl = safeStartUrl(path, plan)

  return (
    <section
      className="learning-home-card overflow-hidden rounded-[24px] border bg-[var(--card)]"
      data-testid="learning-path-launch"
    >
      <div className="grid lg:grid-cols-[0.68fr_1.32fr]">
        <div className="border-b border-[var(--border)] bg-[var(--primary)]/[0.055] p-5 lg:border-r lg:border-b-0">
          <div className="flex items-center justify-between">
            <span className="grid h-9 w-9 place-items-center rounded-full border border-[var(--primary)]/30 text-[var(--primary)]">
              {path.status === 'creating' || showSkeleton ? (
                <Loader2 size={17} className="animate-spin" />
              ) : (
                <CheckCircle2 size={17} />
              )}
            </span>
            <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-[var(--muted-foreground)]">
              {plan ? '路径已生成' : zh ? '正在编排' : 'planning'}
            </span>
          </div>
          <p className="mt-5 text-[9px] font-semibold uppercase tracking-[0.2em] text-[var(--primary)]">
            {zh ? '已建立学习目标' : 'Learning goal created'}
          </p>
          <h2 className="mt-2 font-serif text-[19px] font-semibold leading-7">{path.goal}</h2>
          <p className="mt-5 border-t border-dashed border-[var(--primary)]/25 pt-4 text-[10px] leading-4 text-[var(--muted-foreground)]">
            {path.status === 'creating'
              ? zh
                ? '正在创建学习包与基础计划。'
                : 'Creating the learning pack and basic plan.'
              : path.status === 'error'
                ? zh
                  ? '目标已保留，但学习路径暂未建立。进入我的学习可重试。'
                  : 'The goal is saved, but the path could not be created yet. Retry in My Learning.'
                : zh
                  ? '智能排列只调整本次教学支持，不形成能力诊断。'
                  : 'Smart arrangement shapes this teaching path; it is not an ability diagnosis.'}
          </p>
        </div>

        <div className="p-5">
          {phase === 'choose' ? (
            <div className="mt-5">
              <PersonalizationChoiceCard
                zh={zh}
                onAutoArrange={chooseAutoArrange}
                onUseBasicPath={chooseBasicPath}
              />
            </div>
          ) : null}

          {/* The component area only appears while the path is actually being
              arranged (skeleton), after the LLM recommendation is ready, or
              as the deterministic fallback. It stays hidden while judging
              whether probes are needed and while the learner answers them:
              those stages must not suggest that components are ready yet. */}
          {phase === 'arranging' || phase === 'complete' || fallbackVisible ? (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-[9px] font-semibold uppercase tracking-[0.2em] text-[var(--muted-foreground)]">
                    {TRAIT_LOOP_STAGE}
                  </p>
                  <p className="mt-1 text-[12px] font-medium">
                    {finalPlanReady
                      ? zh
                        ? '为你推荐的学习组件'
                        : 'Recommended learning components'
                      : fallbackVisible
                        ? zh
                          ? '可用的基础学习组件'
                          : 'Available basic components'
                        : zh
                          ? '正在准备组件推荐'
                          : 'Preparing component recommendations'}
                  </p>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] px-2.5 py-1 text-[9.5px] text-[var(--muted-foreground)]">
                  <CircleDot size={11} />
                  {plan?.subject_ref?.label ?? (zh ? '等待学科证据' : 'Awaiting subject evidence')}
                </span>
              </div>

              <ComponentPreview
                plan={plan}
                zh={zh}
                skeleton={showSkeleton}
                fallback={fallbackVisible}
              />
            </>
          ) : null}

          {phase === 'question' && preAssessment ? (
            <div className="mt-5">
              <PreAssessmentCard
                assessment={preAssessment}
                result={preAssessmentResult}
                zh={zh}
                busy={submitting}
                onSubmit={answers => void submitStartingPoint(answers)}
                onSkip={() => void skipStartingPoint()}
                onContinue={() => void arrangePath()}
              />
              {arrangementError ? (
                <p role="alert" className="learning-alert--error mt-3">
                  {arrangementError}
                </p>
              ) : null}
            </div>
          ) : null}

          {phase === 'answered' && preAssessment ? (
            <div className="mt-5">
              <PreAssessmentCard
                assessment={preAssessment}
                result={preAssessmentResult}
                zh={zh}
                busy={submitting}
                onSubmit={answers => void submitStartingPoint(answers)}
                onSkip={() => void skipStartingPoint()}
                onContinue={() => void arrangePath()}
              />
            </div>
          ) : null}

          {phase === 'judging' || phase === 'arranging' || phase === 'error' ? (
            <div className="mt-5">
              <ArrangementProgress
                phase={phase}
                zh={zh}
                error={arrangementError}
                onRetry={phase === 'error' ? retryArrangement : undefined}
                onUseBasicPath={
                  phase === 'error'
                    ? () => {
                        setBasicPathAccepted(true)
                        setArrangementError(null)
                        setPhase('complete')
                      }
                    : undefined
                }
              />
            </div>
          ) : null}

          {fallbackVisible ? (
            <div
              className="mt-4 flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-3 text-xs leading-5"
              role="status"
              data-testid="arrangement-fallback"
            >
              <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />
              <span>
                {zh
                  ? '当前展示的是确定性基础计划，不是 LLM 推荐结果。'
                  : 'These are deterministic fallback components, not the LLM recommendation.'}
              </span>
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] pt-3">
            <p className="inline-flex items-center gap-1.5 text-[9.5px] leading-4 text-[var(--muted-foreground)]">
              <ShieldCheck size={12} />
              {zh
                ? '阅读不算掌握；只有服务端判分的有效作答形成知识证据。'
                : 'Reading is not mastery; only valid server-graded answers create knowledge evidence.'}
            </p>
            {canStart ? (
              <button
                type="button"
                onClick={() => router.push(startUrl)}
                className="inline-flex items-center gap-2 rounded-xl bg-[var(--primary)] px-4 py-2.5 text-[11px] font-semibold text-[var(--primary-foreground)] transition hover:opacity-90"
              >
                <Route size={14} />
                {zh ? '开始学习' : 'Start learning'}
                <ArrowUpRight size={13} />
              </button>
            ) : path.status === 'error' ? (
              <Link
                href="/learning"
                className="inline-flex items-center gap-2 rounded-xl border border-[var(--border)] px-4 py-2.5 text-[11px] font-semibold"
              >
                {zh ? '进入我的学习' : 'Open My Learning'}
                <ArrowUpRight size={13} />
              </Link>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  )
}

function ComponentPreview({
  plan,
  zh,
  skeleton,
  fallback,
}: {
  plan: LearningComponentPlan | null
  zh: boolean
  skeleton: boolean
  fallback: boolean
}) {
  if (skeleton) {
    return (
      <ol
        className="mt-4 grid gap-2 sm:grid-cols-2"
        aria-busy="true"
        aria-label={zh ? '正在生成组件推荐' : 'Generating component recommendations'}
        data-testid="learning-path-components-skeleton"
      >
        {Array.from({ length: 4 }, (_, index) => (
          <li
            key={index}
            className="flex min-h-14 animate-pulse items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--background)]/65 px-3 py-2.5"
          >
            <span className="h-3 w-5 rounded bg-[var(--muted)]" />
            <span className="h-3 flex-1 rounded bg-[var(--muted)]" />
          </li>
        ))}
      </ol>
    )
  }

  const components = plan?.components ?? []
  return (
    <div
      className="mt-4"
      data-testid={fallback ? 'learning-path-components-fallback' : 'learning-path-components-llm'}
    >
      {!fallback && plan?.arrangement_rationale ? (
        <p className="mb-3 rounded-xl border border-[var(--primary)]/20 bg-[var(--primary)]/[0.045] px-3 py-2.5 text-[10.5px] leading-5 text-[var(--muted-foreground)]">
          <strong className="text-[var(--foreground)]">
            {zh ? '推荐思路：' : 'Recommendation rationale: '}
          </strong>
          {plan.arrangement_rationale}
        </p>
      ) : null}
      <ol className="grid gap-2 sm:grid-cols-2">
        {components.slice(0, 12).map((component, index) => (
          <li
            key={component.component_id}
            className="flex min-h-16 items-start gap-3 rounded-xl border border-[var(--border)] bg-[var(--background)]/65 px-3 py-2.5"
          >
            <span className="pt-0.5 font-mono text-[9px] text-[var(--primary)]">
              {String(index + 1).padStart(2, '0')}
            </span>
            <span className="min-w-0">
              <span className="block text-[11px] font-semibold">
                {zh ? component.label_zh : component.label_en}
              </span>
              {component.reason ? (
                <span className="mt-1 block text-[9.5px] leading-4 text-[var(--muted-foreground)]">
                  {component.reason}
                </span>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}
