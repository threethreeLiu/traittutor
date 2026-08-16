'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft,
  Check,
  ChevronRight,
  Circle,
  CircleHelp,
  ClipboardCheck,
  Loader2,
  Lock,
  Mic,
  MessageCircle,
  Play,
  RefreshCcw,
  Route,
  SkipForward,
  Sparkles,
  Volume2,
  X,
} from 'lucide-react'
import { apiFetch, apiUrl } from '@/lib/api'
import { useAppShell } from '@/context/AppShellContext'
import { readStoredSidebarCollapsed } from '@/context/app-shell-storage'
import {
  arrangeLearningComponentPlan,
  createLearningComponentPlan,
  createTraitTutorGenerationTask,
  confirmTraitTutorGenerationReview,
  discardTraitTutorGenerationReview,
  generationErrorMessage,
  getDueLearningReviews,
  getLearningPack,
  getLearningRepair,
  getTraitTutorGenerationTask,
  listLearningAssessmentAttempts,
  recordLearningComponentEvent,
  recordLearningReviewResult,
  revealLearningReviewAnswer,
  revealTraitTutorGenerationFlashcard,
  retryLearningRepair,
  retryTraitTutorGenerationTask,
  TraitTutorApiError,
  updateLearningPack,
  type GenerateKind,
  type GenerateSuiteResult,
  type AssessmentAttemptView,
  type LearningComponent,
  type LearningComponentType,
  type LearningComponentPlan,
  type LearningPack,
  type ProgressCalibration,
  type RepairRecord,
  type ReviewState,
} from '@/lib/traittutor-api'
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import Modal from '@/components/common/Modal'
import LearningAssistant from './LearningAssistant'
import { PageSchemaRenderer } from './PageSchemaRenderer'


// Split modules: pure label/helper functions and presentational views
// moved out of this file; the canvas keeps state, data flow, and layout.
import type {
  CalibrationResult,
  ComponentEvent,
  ComponentEventResult,
  ComponentOutput,
  Locale,
} from './canvas-shared'
import {
  TEACHING_COMPONENT_TYPES,
  actionLabel,
  appendTranscript,
  collectVisibleStrings,
  componentReason,
  executorKind,
  groupVisibleActions,
  learningAssistantExcerpt,
  modalityLabel,
  outputText,
  progressDifficultyLabel,
  progressStrategyLabel,
  stageLabel,
  statusLabel,
  waitForGeneration,
} from './canvas-labels'
import {
  ActionBar,
  AssessmentView,
  CalibrationCheckpoint,
  ComponentBody,
  FullState,
  GenerationPreview,
  LessonView,
  ProgressCheckpoint,
  ReflectionPrompt,
  RepairCard,
  RetrievalView,
  ReviewQueueView,
  ReviewRequiredView,
  ActionStatusIcon,
  VoiceAnswerButton,
} from './canvas-views'

export default function LearningCanvas({ packId, locale }: { packId: string; locale: Locale }) {
  const zh = locale === 'zh'
  const { setSidebarCollapsed } = useAppShell()
  const [pack, setPack] = useState<LearningPack | null>(null)
  const [plan, setPlan] = useState<LearningComponentPlan | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedMove, setSelectedMove] = useState<string | null>(null)
  const [outputs, setOutputs] = useState<Record<string, ComponentOutput>>({})
  const [calibrations, setCalibrations] = useState<CalibrationResult[]>([])
  const [progressCalibration, setProgressCalibration] = useState<ProgressCalibration | null>(null)
  const [assessmentAttempts, setAssessmentAttempts] = useState<AssessmentAttemptView[]>([])
  const [activeRepair, setActiveRepair] = useState<RepairRecord | null>(null)
  const [repairLoading, setRepairLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [arrangementRetrying, setArrangementRetrying] = useState(false)
  const [arrangementRetryError, setArrangementRetryError] = useState<string | null>(null)
  const [assistantDrawerOpen, setAssistantDrawerOpen] = useState(false)
  const [assistantMinimized, setAssistantMinimized] = useState(false)
  // Clicking "Open review component" only re-selects the review component.
  // When that is already the selection the click would look dead — surface a
  // passive hint instead of failing silently. When nothing is due yet, the
  // hint alone is too easy to miss, so it is shown as a modal instead.
  const [reviewNotice, setReviewNotice] = useState<'already-open' | null>(null)
  const [reviewEmptyModalOpen, setReviewEmptyModalOpen] = useState(false)
  const reviewNoticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const drawerRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const wasCollapsed = readStoredSidebarCollapsed()
    setSidebarCollapsed(true)
    return () => {
      // Restore the entry state only when the sidebar is still collapsed.
      // If the learner expanded it manually, that newer preference wins.
      if (readStoredSidebarCollapsed()) setSidebarCollapsed(wasCollapsed)
    }
  }, [setSidebarCollapsed])

  useEffect(() => {
    let active = true
    setArrangementRetrying(false)
    setArrangementRetryError(null)
    void (async () => {
      try {
        const loaded = await getLearningPack(packId)
        let current =
          loaded.component_plans?.find(item => item.plan_id === loaded.active_plan_id) ?? null
        if (!current)
          current = await createLearningComponentPlan(packId, {
            instruction: loaded.goal?.text ?? loaded.title,
          })
        if (!active) return
        setPack(loaded)
        setPlan(current)
        setCalibrations((loaded.calibrations ?? []) as CalibrationResult[])
        setProgressCalibration(
          (loaded.progress_calibrations?.at(-1) as ProgressCalibration | undefined) ?? null
        )
        const restoredAttempts = await listLearningAssessmentAttempts(packId, current.plan_id, {
          limit: 200,
        }).catch(() => ({ items: [] as AssessmentAttemptView[] }))
        if (!active) return
        setAssessmentAttempts(restoredAttempts.items)
        // Component output is durable through its generation id. Rehydrate it
        // before rendering so a refresh never turns completed work into a new
        // billable generation request.
        const restored = await Promise.all(
          current.components.map(async component => {
            if (!component.output_ref) return null
            try {
              const output = await getTraitTutorGenerationTask(component.output_ref)
              if (!('result' in output)) return null
              if (component.executor === 'audio' && component.media_url) {
                return [
                  component.component_id,
                  { audioUrl: component.media_url, transcript: outputText(output) },
                ] as const
              }
              return [component.component_id, output] as const
            } catch {
              return null
            }
          })
        )
        if (!active) return
        setOutputs(
          Object.fromEntries(
            restored.filter(
              (entry): entry is readonly [string, GenerateSuiteResult] => entry !== null
            )
          )
        )
        setSelectedId(
          current.components.find(item => !['completed', 'skipped'].includes(item.status))
            ?.component_id ??
            current.components[0]?.component_id ??
            null
        )
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (active) setLoading(false)
      }
    })()
    return () => {
      active = false
    }
  }, [packId])

  const retryArrangementFromCanvas = useCallback(async () => {
    setArrangementRetrying(true)
    setArrangementRetryError(null)
    try {
      const arranged = await arrangeLearningComponentPlan(packId)
      const refreshed = await getLearningPack(packId).catch(() => null)
      setPlan(arranged)
      if (refreshed) setPack(refreshed)
      setSelectedMove(null)
      setSelectedId(
        arranged.components.find(item => !['completed', 'skipped'].includes(item.status))
          ?.component_id ??
          arranged.components[0]?.component_id ??
          null
      )
    } catch {
      setArrangementRetryError(
        zh
          ? '智能排列重试未完成，基础路径仍可继续使用。'
          : 'Smart arrangement retry did not complete. The basic path remains available.'
      )
    } finally {
      setArrangementRetrying(false)
    }
  }, [packId, zh])

  useEffect(() => {
    if (!assistantDrawerOpen) return
    const previousBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const previouslyFocused =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const drawer = drawerRef.current
    const focusable = () =>
      Array.from(
        drawer?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
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

  const selected = useMemo(
    () =>
      (plan?.components ?? []).find(item => item.component_id === selectedId) ??
      plan?.components?.[0] ??
      null,
    [plan, selectedId]
  )
  const visibleActions = useMemo(() => groupVisibleActions(plan?.components ?? []), [plan])
  const selectedAction = useMemo(
    () =>
      visibleActions.find(action => action.actionId === selectedMove) ??
      visibleActions.find(action =>
        action.components.some(item => item.component_id === selectedId)
      ) ??
      visibleActions[0] ??
      null,
    [selectedId, selectedMove, visibleActions]
  )
  const activeRepairSummary = useMemo(
    () =>
      (pack?.repairs ?? []).find(
        item =>
          item.action_id === selected?.component_id &&
          !['deferred', 'repaired', 'scheduled'].includes(item.status)
      ) ?? null,
    [pack, selected]
  )
  useEffect(() => {
    let active = true
    setActiveRepair(null)
    if (!activeRepairSummary) {
      setRepairLoading(false)
      return () => {
        active = false
      }
    }
    setRepairLoading(true)
    void getLearningRepair(packId, activeRepairSummary.repair_id)
      .then(repair => {
        if (active) setActiveRepair(repair)
      })
      .catch(reason => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => {
        if (active) setRepairLoading(false)
      })
    return () => {
      active = false
    }
  }, [activeRepairSummary, packId])
  const recoveryRepairs = useMemo(
    () =>
      (pack?.repairs ?? []).filter(item =>
        ['identified', 'explained', 'retrying', 'deferred'].includes(item.status)
      ),
    [pack]
  )
  const completedIds = useMemo(
    // The server treats an explicitly skipped optional step as satisfied; the
    // canvas must use the same rule or it visually keeps later steps locked.
    () =>
      new Set(
        plan?.components
          .filter(item => item.status === 'completed' || item.status === 'skipped')
          .map(item => item.component_id) ?? []
      ),
    [plan]
  )
  const blockedBy = useMemo(
    () => selected?.dependencies.filter(dependency => !completedIds.has(dependency)) ?? [],
    [completedIds, selected]
  )
  const blockedComponents = useMemo(
    () => plan?.components.filter(item => blockedBy.includes(item.component_id)) ?? [],
    [blockedBy, plan]
  )
  const reviewComponent = useMemo(
    () =>
      plan?.components.find(
        item => item.component_type === 'review_queue' && item.status !== 'completed'
      ) ??
      plan?.components.find(item => item.executor === 'retrieval' && item.status !== 'completed') ??
      null,
    [plan]
  )
  const reviewCount = pack?.due_review_count ?? 0

  // The review entry only re-selects the review component. When it is already
  // selected the click changes nothing on screen, and when nothing is due the
  // queue itself is empty — explain what unlocks review in a modal instead of
  // staying silent. Reviews are scheduled only after a server-graded practice
  // miss is recovered through the repair queue (due the next day).
  const openReviewComponent = useCallback(() => {
    if (!reviewComponent) return
    const alreadyOpen = selectedId === reviewComponent.component_id
    setSelectedId(reviewComponent.component_id)
    if (!reviewCount) {
      setReviewNotice(null)
      setReviewEmptyModalOpen(true)
      if (reviewNoticeTimer.current) clearTimeout(reviewNoticeTimer.current)
      return
    }
    const notice = alreadyOpen ? 'already-open' : null
    setReviewNotice(notice)
    if (reviewNoticeTimer.current) clearTimeout(reviewNoticeTimer.current)
    reviewNoticeTimer.current = notice ? setTimeout(() => setReviewNotice(null), 8000) : null
  }, [reviewComponent, reviewCount, selectedId])
  useEffect(
    () => () => {
      if (reviewNoticeTimer.current) clearTimeout(reviewNoticeTimer.current)
    },
    []
  )
  // Navigating to any other step dismisses the notice; selecting the review
  // component itself (including via openReviewComponent) must keep it. The
  // empty-queue modal closes too when the learner moves elsewhere.
  useEffect(() => {
    if (selectedId !== reviewComponent?.component_id) {
      setReviewNotice(null)
      setReviewEmptyModalOpen(false)
    }
  }, [selectedId, reviewComponent])

  const applyComponentEvent = useCallback(
    async (
      component: LearningComponent,
      event: ComponentEvent,
      options: { advance?: boolean } = {}
    ): Promise<ComponentEventResult> => {
      if (!plan) throw new Error('Learning plan is unavailable')
      // Completing/skipping a component that is already completed/skipped is a
      // no-op, not an error: the sidebar keeps completed steps selectable, and
      // re-selecting one must never surface a server 409 as a crash.
      if (
        (event.action === 'complete' || event.action === 'skip') &&
        ['completed', 'skipped'].includes(component.status)
      ) {
        return { component, learner_state_updated: false }
      }
      let result: ComponentEventResult
      try {
        result = await recordLearningComponentEvent(
          packId,
          plan.plan_id,
          component.component_id,
          event
        )
      } catch (reason) {
        // Defense in depth against stale local plan state: if the server says
        // the transition already happened (e.g. a refresh landed between the
        // artifact persistence and the complete event), sync the authoritative
        // plan and treat it as applied instead of crashing the page.
        if (
          reason instanceof TraitTutorApiError &&
          reason.status === 409 &&
          (event.action === 'complete' || event.action === 'skip') &&
          /Cannot (complete|skip) a (completed|skipped) component/.test(reason.message)
        ) {
          const refreshed = await getLearningPack(packId).catch(() => null)
          if (refreshed) {
            const refreshedPlan =
              refreshed.component_plans?.find(item => item.plan_id === plan.plan_id) ?? null
            if (refreshedPlan) setPlan(refreshedPlan)
          }
          return { component, learner_state_updated: false }
        }
        throw reason
      }
      if (result.calibration)
        setCalibrations(current => [
          ...current.filter(
            item =>
              !(
                item.question_id === result.calibration!.question_id &&
                item.artifact_ref === result.calibration!.artifact_ref
              )
          ),
          result.calibration!,
        ])
      if (result.progress_calibration) setProgressCalibration(result.progress_calibration)
      if (result.replanned_plan) {
        setPlan(result.replanned_plan)
        setSelectedMove(null)
        // A calibration completion produces the progress evaluation and the
        // follow-up plan together: stay on the calibration so the learner can
        // read the evaluation instead of being yanked to the inserted support.
        if (component.component_type !== 'calibration_checkpoint') {
          setSelectedId(
            result.replanned_plan.components.find(
              item => !['completed', 'skipped'].includes(item.status)
            )?.component_id ?? null
          )
        }
        return result
      }
      if (component.executor === 'assessment' && event.action === 'complete') {
        // The event is already recorded and server-graded at this point; the
        // pack/attempt-history refresh is best-effort and must NEVER surface
        // as a "answer not saved" failure, so each refresh swallows its own
        // error and the handler keeps the successful save result.
        const [refreshedPack, refreshedAttempts] = await Promise.all([
          getLearningPack(packId).catch(() => null),
          listLearningAssessmentAttempts(packId, plan.plan_id, { limit: 200 })
            .then(result => result.items)
            .catch(() => []),
        ])
        if (refreshedPack) setPack(refreshedPack)
        setAssessmentAttempts(refreshedAttempts)
      } else if (result.created_repair_id) {
        const refreshedPack = await getLearningPack(packId).catch(() => null)
        if (refreshedPack) setPack(refreshedPack)
      }
      setPlan(current =>
        current
          ? {
              ...current,
              components: current.components.map(item =>
                item.component_id === component.component_id ? result.component : item
              ),
            }
          : current
      )
      if (event.action === 'complete' || event.action === 'skip') {
        const satisfied = new Set(
          plan.components
            .filter(item => ['completed', 'skipped'].includes(item.status))
            .map(item => item.component_id)
        )
        satisfied.add(component.component_id)
        const index = plan.components.findIndex(item => item.component_id === component.component_id)
        const ordered = [
          ...plan.components.slice(index + 1),
          ...plan.components.slice(0, Math.max(0, index)),
        ]
        const next = ordered.find(
          item =>
            !['completed', 'skipped'].includes(item.status) &&
            item.dependencies.every(dependency => satisfied.has(dependency))
        )
        if (next && options.advance !== false) {
          setSelectedMove(null)
          setSelectedId(next.component_id)
        }
      }
      return result
    },
    [packId, plan]
  )

  // goal_map and concept_explanation have no "complete" button — they complete
  // on open (see generate()), so the following components unlock. Re-entry must
  // reach the same state: if the generated content is already available but the
  // component is still pending/active (e.g. a refresh landed between artifact
  // persistence and the complete event), finish it here instead of dead-locking
  // the path behind an invisible button. The once-per-component ref keeps the
  // heal idempotent even if the server response leaves the status unchanged
  // (e.g. a test double), so the effect can never loop.
  const autoCompleteAttemptedRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (!plan || !selected || busy === selected.component_id) return
    if (!['goal_map', 'concept_explanation'].includes(selected.component_type)) return
    if (['completed', 'skipped'].includes(selected.status)) return
    const output = outputs[selected.component_id]
    if (!output || !('result' in output)) return
    // Guard the heal exactly like the explicit auto-complete in generate():
    // only a genuinely usable completed run unlocks the path. A needs_review
    // or degraded/failed output must stay active so the learner still has the
    // ReviewRequiredView confirm/regenerate/discard entry points — completing
    // it here would swallow the regeneration path (a completed component
    // cannot retry). Missing orchestration metadata is tolerated for outputs
    // restored from before the field existed.
    if (output.status !== 'completed') return
    const orchestration = output.result.orchestration
    if (orchestration !== undefined && orchestration.status !== 'succeeded') return
    if (autoCompleteAttemptedRef.current.has(selected.component_id)) return
    autoCompleteAttemptedRef.current.add(selected.component_id)
    // advance: false — the heal mirrors the explicit auto-complete in
    // generate(): it unlocks the path but must not yank the learner away from
    // the goal map / concept explanation they just opened.
    void applyComponentEvent(
      selected,
      { action: 'complete', replan: false },
      { advance: false }
    ).catch(() => undefined)
  }, [plan, selected, outputs, busy, applyComponentEvent])

  const persistGenerationOutput = useCallback(
    async (
      component: LearningComponent,
      result: GenerateSuiteResult,
      output: ComponentOutput,
      mediaDegraded = false
    ) => {
      // Persist a generation id immediately, including a review-required
      // result. This makes refresh/reconnect recover the same artifact instead
      // of creating another billable task before the learner can interact.
      if (result.status === 'needs_review') {
        await applyComponentEvent(component, {
          action: 'feedback',
          output_ref: result.generation_id,
          feedback: 'quality_review_required',
          replan: false,
        })
        return
      }
      await updateLearningPack(packId, { generation_id: result.generation_id })
      await applyComponentEvent(component, {
        // Persisting a generated artifact is not completion evidence. Keep the
        // component active so the learner sees the PageSchema and explicitly
        // chooses Complete and continue; assessment/retrieval remain ungraded.
        action: mediaDegraded ? 'degrade' : 'feedback',
        output_ref: result.generation_id,
        media_url: 'audioUrl' in output ? output.audioUrl : undefined,
        replan: false,
      })
    },
    [applyComponentEvent, packId]
  )

  const generate = useCallback(
    async (component: LearningComponent) => {
      if (!pack) return
      if (component.component_type === 'calibration_checkpoint') {
        // Calibration is a deterministic decision point: completing it
        // aggregates the round's verified evidence into a difficulty
        // evaluation and follow-up plan. It has no model output or generation
        // task to retry.
        setError(
          zh
            ? '校准复盘不需要重新生成，完成校准即可汇总本轮证据并生成难度评价。'
            : 'Calibration is not regenerated; complete it to aggregate this round’s evidence into a difficulty evaluation.',
        )
        return
      }
      setBusy(component.component_id)
      setError(null)
      try {
        // A discarded/reloaded output leaves the component active on purpose:
        // it already represents an in-progress learner step. Starting it again
        // would violate the server transition contract and mask the real task.
        if (component.status === 'pending') {
          await applyComponentEvent(component, { action: 'start', replan: false })
        } else if (component.status === 'degraded') {
          await applyComponentEvent(component, { action: 'retry', replan: false })
        }
        const generationType = executorKind(component.executor)
        const material = pack.materials?.[0] as {
          source_type?: 'knowledge' | 'notebook' | 'upload' | 'paste'
          title?: string
          text?: string
          source_id?: string | null
          metadata?: Record<string, unknown>
        }
        const componentIndex = plan?.components.findIndex(
          item => item.component_id === component.component_id
        )
        // An assessment is grounded in the lesson it follows — but only in a
        // real teaching component (explanation/example/media), never in
        // support steps like a goal map, which carries no teachable material
        // prose. When no prior lesson exists (e.g. the LLM arrangement puts a
        // diagnostic right after the goal map), fall back to the pack's own
        // material instead of feeding the generator an empty or title-only
        // text (which makes the quiz task fail instantly).
        const priorLessonComponent =
          component.executor === 'assessment' && componentIndex !== undefined && componentIndex > 0
            ? plan?.components
                .slice(0, componentIndex)
                .reverse()
                .find(
                  item =>
                    TEACHING_COMPONENT_TYPES.has(item.component_type) &&
                    Boolean(outputs[item.component_id] && 'result' in outputs[item.component_id])
                )
            : undefined
        const priorLessonOutput = priorLessonComponent
          ? outputs[priorLessonComponent.component_id]
          : undefined
        const priorLesson =
          priorLessonOutput && 'result' in priorLessonOutput ? priorLessonOutput : undefined
        const priorLessonText = priorLesson ? outputText(priorLesson) : ''
        const generationMaterial =
          priorLesson && priorLessonText
            ? {
                source_type: 'paste' as const,
                title: `${pack.title} · ${zh ? '已学讲解' : 'Learned explanation'}`,
                text: priorLessonText,
                metadata: {
                  source_kind: 'generated_lesson',
                  derived_generation_id: priorLesson.generation_id,
                },
              }
            : {
                source_type: material.source_type ?? ('paste' as const),
                title: material.title ?? pack.title,
                // Whitespace-only text would still resolve to empty grounding
                // chunks and fail the generation instantly — treat it as
                // missing and fall back to the goal/title.
                text: material.text?.trim() ? material.text : (plan?.goal ?? pack.title),
                source_id: material.source_id,
                metadata: material.metadata,
              }
        // B+C: the upload pipeline already persisted a grounded material
        // analysis in the pack material metadata. Hand its server-side record
        // id back so per-component generation reuses it instead of re-running
        // the content-analysis LLM stage (which can exceed the instruction
        // executor budget and fail the whole component).
        const learnerAnalyses = Array.isArray(
          (material.metadata as Record<string, unknown> | undefined)?.learner_analyses
        )
          ? ((material.metadata as Record<string, unknown>).learner_analyses as Array<{
              analysis_id?: string | null
            }>)
          : []
        const persistedAnalysisId = learnerAnalyses[0]?.analysis_id ?? undefined
        const persistedSessionId =
          typeof (material.metadata as Record<string, unknown> | undefined)?.learning_session_id ===
          'string'
            ? ((material.metadata as Record<string, unknown>).learning_session_id as string)
            : undefined
        const accepted = await createTraitTutorGenerationTask({
          generation_type: generationType,
          material: generationMaterial,
          options: {
            learning_component: {
              component_id: component.component_id,
              component_type: component.component_type,
              reason: component.reason,
              concept_refs: component.concept_refs,
            },
            ...(persistedAnalysisId && persistedSessionId && !priorLesson
              ? { analysis_id: persistedAnalysisId, session_id: persistedSessionId }
              : {}),
          },
        })
        const result = await waitForGeneration(accepted.generation_id)
        let output: ComponentOutput = result
        const expectedMediaType =
          component.executor === 'image'
            ? 'visual_map'
            : component.executor === 'video'
              ? 'video_explanation'
              : null
        const pageContainsRequestedMedia = result.page_schema?.regions.some(
          region => region.component?.component_type === expectedMediaType
        )
        let mediaDegraded =
          expectedMediaType !== null &&
          (result.page_schema
            ? !pageContainsRequestedMedia
            : component.executor === 'image'
              ? result.result.image_generation?.status !== 'completed'
              : result.result.video_generation?.status !== 'completed')
        if (component.executor === 'audio' && result.status === 'completed') {
          const transcript = outputText(result) || plan?.goal || pack.title
          // The backend synthesizes a two-host podcast during generation when a
          // TTS provider is configured. If the audio URL is already present,
          // use it directly instead of making a separate single-segment TTS call.
          const precomputedAudioUrl = result.result.podcast_generation?.audio_url
          if (precomputedAudioUrl) {
            output = { audioUrl: precomputedAudioUrl, transcript }
          } else {
            try {
              const response = await apiFetch(apiUrl('/api/v1/voice/tts'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  text: transcript.slice(0, 4000),
                  generation_id: result.generation_id,
                }),
              })
              if (!response.ok) throw new Error('tts unavailable')
              const audioUrl = response.headers.get('X-TraitTutor-Audio-Url')
              if (!audioUrl) throw new Error('tts output was not persisted')
              output = { audioUrl, transcript }
            } catch {
              mediaDegraded = true
              output = result
            }
          }
        }
        await persistGenerationOutput(component, result, output, mediaDegraded)
        setOutputs(current => ({ ...current, [component.component_id]: output }))
        // A goal map has no complete/continue button: it is the mission that
        // opens the path, and reading it is the whole point. The same applies
        // to the core concept explanation — it is content to read, not a task
        // to check off — so both are marked complete as soon as they render
        // and the following components unlock without a meaningless extra
        // click. A degraded run (orchestration_failed text fallback) must NOT
        // auto-complete: the learner would be stuck on a placeholder with no
        // way to regenerate (a completed component cannot retry). Keep it
        // active so the action bar still offers regeneration.
        const orchestrationSucceeded = result.result.orchestration?.status === 'succeeded'
        if (
          orchestrationSucceeded &&
          (component.component_type === 'goal_map' ||
            component.component_type === 'concept_explanation')
        ) {
          // advance: false — the goal map / concept explanation completes on
          // open, but the learner must stay on it to read the content; the
          // sidebar remains the way to move on.
          await applyComponentEvent(
            component,
            { action: 'complete', replan: false },
            { advance: false }
          ).catch(() => undefined)
        }
      } catch (reason) {
        setError(generationErrorMessage(reason, zh))
        await applyComponentEvent(component, { action: 'degrade', replan: false }).catch(
          () => undefined
        )
      } finally {
        setBusy(null)
      }
    },
    [applyComponentEvent, outputs, pack, plan, persistGenerationOutput, zh]
  )

  const confirmReview = useCallback(
    async (component: LearningComponent, result: GenerateSuiteResult) => {
      setBusy(component.component_id)
      setError(null)
      try {
        const confirmed = await confirmTraitTutorGenerationReview(result.generation_id)
        let output: ComponentOutput = confirmed
        if (component.executor === 'audio') {
          const transcript = outputText(confirmed) || plan?.goal || pack?.title || ''
          const response = await apiFetch(apiUrl('/api/v1/voice/tts'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              text: transcript.slice(0, 4000),
              generation_id: confirmed.generation_id,
            }),
          })
          if (response.ok && response.headers.get('X-TraitTutor-Audio-Url')) {
            output = { audioUrl: response.headers.get('X-TraitTutor-Audio-Url')!, transcript }
          }
        }
        await persistGenerationOutput(component, confirmed, output)
        setOutputs(current => ({ ...current, [component.component_id]: output }))
      } catch (reason) {
        setError(generationErrorMessage(reason, zh))
      } finally {
        setBusy(null)
      }
    },
    [pack?.title, persistGenerationOutput, plan?.goal, zh]
  )

  const regenerateReview = useCallback(
    async (component: LearningComponent, result: GenerateSuiteResult) => {
      setBusy(component.component_id)
      setError(null)
      try {
        const accepted = await retryTraitTutorGenerationTask(result.generation_id)
        const regenerated = await waitForGeneration(accepted.generation_id)
        await persistGenerationOutput(component, regenerated, regenerated)
        setOutputs(current => ({ ...current, [component.component_id]: regenerated }))
      } catch (reason) {
        setError(generationErrorMessage(reason, zh))
      } finally {
        setBusy(null)
      }
    },
    [persistGenerationOutput, zh]
  )

  const discardReview = useCallback(
    async (component: LearningComponent, result: GenerateSuiteResult) => {
      setBusy(component.component_id)
      setError(null)
      try {
        await discardTraitTutorGenerationReview(result.generation_id)
        await applyComponentEvent(component, {
          action: 'feedback',
          feedback: 'quality_review_discarded',
          replan: false,
        })
        setOutputs(current => {
          const next = { ...current }
          delete next[component.component_id]
          return next
        })
      } catch (reason) {
        setError(generationErrorMessage(reason, zh))
      } finally {
        setBusy(null)
      }
    },
    [applyComponentEvent, zh]
  )

  if (loading)
    return (
      <FullState
        icon={<Loader2 className="animate-spin" />}
        title={zh ? '正在恢复学习组件' : 'Restoring learning components'}
      />
    )
  if (!pack || !plan || !selected)
    return (
      <FullState
        icon={<CircleHelp />}
        title={error ?? (zh ? '学习组件暂不可用' : 'Learning components unavailable')}
      />
    )

  return (
    <main className="learning-canvas">
      <header className="learning-canvas__header">
        <div className="learning-canvas__toolbar">
          <div className="flex min-w-0 items-center gap-3">
            <Link href="/learning" className="learning-icon-button">
              <ArrowLeft size={16} />
            </Link>
            <div className="min-w-0">
              <p className="learning-eyebrow">{zh ? '学习组件' : 'Learning components'}</p>
              <h1 className="truncate font-serif text-lg font-semibold md:text-xl">{plan.goal}</h1>
            </div>
          </div>
          <button
            onClick={() => setAssistantDrawerOpen(true)}
            aria-label={zh ? '问助手' : 'Ask assistant'}
            className="learning-button learning-button--secondary shrink-0 px-3 py-2 xl:hidden"
          >
            <MessageCircle size={15} />
            <span className="hidden min-[400px]:inline">{zh ? '问助手' : 'Ask assistant'}</span>
          </button>
        </div>
      </header>

      {plan.arrangement === 'deterministic_fallback' || plan.arrangement === 'pending' ? (
        // A deliberate opt-out on the Learn intermediate page
        // (arrangement_preference === "basic") is not a failure: the learner
        // chose the basic path, so no "not yet arranged" banner or retry
        // affordance may appear.
        pack.arrangement_preference === 'basic' ? null : (
          <div
            className="mx-4 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm md:mx-6"
            role="status"
            data-testid="arrangement-canvas-notice"
          >
            <span>
              {arrangementRetryError ??
                (plan.arrangement === 'pending'
                  ? zh
                    ? '学习路径尚未完成智能排列，已使用基础路径。'
                    : 'Smart arrangement is still pending. The basic path remains available.'
                  : zh
                    ? '自动排列暂时不可用，已使用基础学习路径。你可以稍后重试排列。'
                    : 'Smart arrangement is temporarily unavailable. The basic path is ready, and you can retry later.')}
            </span>
            <button
              type="button"
              disabled={arrangementRetrying}
              onClick={() => void retryArrangementFromCanvas()}
              className="learning-button learning-button--secondary px-3 py-2 text-xs"
            >
              <RefreshCcw size={14} className={arrangementRetrying ? 'animate-spin' : ''} />
              {arrangementRetrying
                ? zh
                  ? '正在重试'
                  : 'Retrying'
                : zh
                  ? '重试排列'
                  : 'Retry arrangement'}
            </button>
          </div>
        )
      ) : null}

      <div
        className={`learning-canvas__layout ${assistantMinimized ? 'learning-canvas__layout--assistant-closed' : ''}`}
      >
        <aside className="learning-canvas__path-panel" data-testid="learning-path-panel">
          <div className="mb-3 flex items-end justify-between lg:mb-5">
            <div>
              <p className="learning-meta">{zh ? '可用组件' : 'Available components'}</p>
              <p className="learning-copy-muted mt-1 text-xs">
                {visibleActions.filter(item => item.status === 'completed').length} /{' '}
                {visibleActions.length} {zh ? '个组件已完成' : 'components completed'}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {reviewComponent ? (
                <button
                  type="button"
                  onClick={openReviewComponent}
                  aria-label={
                    zh
                      ? `打开复习组件，${reviewCount} 项待巩固`
                      : `Open review component, ${reviewCount} due`
                  }
                  className="learning-icon-button lg:hidden"
                >
                  <ClipboardCheck size={15} />
                </button>
              ) : reviewCount ? (
                <span
                  className="learning-status-pill lg:hidden"
                  aria-label={zh ? `${reviewCount} 项待巩固` : `${reviewCount} items to review`}
                >
                  {reviewCount}
                </span>
              ) : null}
              <Route size={18} className="learning-accent" />
            </div>
          </div>
          {reviewNotice ? (
            <p
              role="status"
              className="learning-copy-muted mb-2 rounded-md border border-[var(--border)] p-2 text-xs leading-5 lg:hidden"
            >
              {zh
                ? '复习组件已经在主区域打开，直接在那里翻卡自评即可。'
                : 'The review component is already open in the main area — flip and rate the cards there.'}
            </p>
          ) : null}
          <ol className="flex gap-2 overflow-x-auto pb-2 lg:block lg:space-y-1 lg:overflow-visible">
            {visibleActions.map((action, index) => {
              const target =
                action.components.find(
                  item =>
                    !['completed', 'skipped'].includes(item.status) &&
                    item.dependencies.every(dependency => completedIds.has(dependency))
                ) ??
                action.components.find(item => !['completed', 'skipped'].includes(item.status)) ??
                action.components[0]
              const locked = action.status === 'locked'
              return (
                <li key={action.actionId} className="min-w-[156px] sm:min-w-[180px] lg:min-w-0">
                  <button
                    disabled={locked}
                    onClick={() => {
                      setSelectedMove(action.actionId)
                      if (target) setSelectedId(target.component_id)
                    }}
                    aria-describedby={locked ? `dependency-${action.actionId}` : undefined}
                    className={`learning-step ${selectedAction?.actionId === action.actionId ? 'learning-step--active' : ''} ${locked ? 'opacity-65' : ''}`}
                  >
                    {locked ? (
                      <Lock size={14} className="text-[var(--muted-foreground)]" aria-hidden />
                    ) : (
                      <ActionStatusIcon
                        status={action.status}
                        active={selectedAction?.actionId === action.actionId}
                      />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="learning-meta block text-[8px]">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <span className="block truncate text-xs font-medium">
                        {target
                          ? zh
                            ? target.label_zh
                            : target.label_en
                          : actionLabel(action.actionType, zh)}
                      </span>
                    </span>
                    <ChevronRight size={13} className="opacity-40" />
                  </button>
                  {locked ? (
                    <span id={`dependency-${action.actionId}`} className="sr-only">
                      {zh ? '此组件需要先完成相关练习' : 'Complete the related practice first'}
                    </span>
                  ) : null}
                </li>
              )
            })}
          </ol>
          <section
            className="mt-5 hidden border-t border-[var(--border)] pt-5 lg:block"
            aria-label={zh ? '待复习' : 'Review queue'}
          >
            <div className="flex items-start gap-2">
              <ClipboardCheck size={16} className="learning-accent mt-0.5" />
              <div className="min-w-0">
                <p className="learning-meta">{zh ? '待复习' : 'Review queue'}</p>
                <p className="learning-copy-muted mt-1 text-xs leading-5">
                  {reviewCount
                    ? zh
                      ? `${reviewCount} 项回忆记录等待巩固`
                      : `${reviewCount} recall records need reinforcement`
                    : zh
                      ? '完成可判分练习后，系统会把需要巩固的概念安排到这里。'
                      : 'Concepts that need reinforcement appear here after graded practice.'}
                </p>
              </div>
            </div>
            {reviewComponent ? (
              <button
                type="button"
                onClick={openReviewComponent}
                className="learning-button learning-button--secondary mt-2 w-full justify-center px-3 py-2 text-xs lg:mt-3"
              >
                <ClipboardCheck size={14} />
                {zh ? '打开复习组件' : 'Open review component'}
              </button>
            ) : null}
            {reviewNotice ? (
              <p
                role="status"
                className="learning-copy-muted mt-2 rounded-md border border-[var(--border)] p-2 text-xs leading-5"
              >
                {zh
                  ? '复习组件已经在主区域打开，直接在那里翻卡自评即可。'
                  : 'The review component is already open in the main area — flip and rate the cards there.'}
              </p>
            ) : null}
          </section>
          {recoveryRepairs.length ? (
            <section
              className="mt-5 hidden border-t border-[var(--border)] pt-5 lg:block"
              aria-label={zh ? '非阻塞修复队列' : 'Non-blocking repair queue'}
            >
              <p className="learning-meta">{zh ? '修复队列' : 'Repair queue'}</p>
              <div className="mt-2 grid gap-2">
                {recoveryRepairs.map(repair => (
                  <button
                    key={repair.repair_id}
                    type="button"
                    disabled={repair.status === 'deferred'}
                    onClick={() => setSelectedId(repair.action_id)}
                    className="learning-button learning-button--secondary w-full justify-center px-3 py-2 text-xs"
                  >
                    {repair.status === 'deferred'
                      ? zh
                        ? '已暂缓，先完成其他练习'
                        : 'Paused — try another objective'
                      : zh
                        ? '进入对应修复项'
                        : 'Open repair item'}
                  </button>
                ))}
              </div>
            </section>
          ) : null}
        </aside>

        <section className="learning-canvas__content">
          <div className="flex min-h-0 w-full flex-1 flex-col">
            <div className="learning-canvas__section-heading mb-4 flex items-start justify-between gap-3 border-b pb-4 md:mb-5 md:gap-4 md:pb-5">
              <div className="min-w-0">
                <p className="learning-eyebrow">
                  {selectedAction
                    ? actionLabel(selectedAction.actionType, zh)
                    : stageLabel(selected.bkt_stage, zh)}{' '}
                  · {modalityLabel(selected.modality, zh)}
                </p>
                <h2 className="mt-1 font-serif text-2xl font-semibold md:mt-2 md:text-3xl">
                  {zh ? selected.label_zh : selected.label_en}
                </h2>
                <p className="learning-copy-muted mt-1 max-w-2xl text-sm leading-5 md:mt-2 md:leading-6">
                  {componentReason(selected, zh)}
                </p>
              </div>
              <span className="learning-status-pill">{statusLabel(selected.status, zh)}</span>
            </div>

            {activeRepairSummary ? (
              activeRepair ? (
                <RepairCard
                  packId={packId}
                  repair={activeRepair}
                  zh={zh}
                  onComplete={async updatedRepair => {
                    setActiveRepair(updatedRepair)
                    const refreshedPack = await getLearningPack(packId)
                    setPack(refreshedPack)
                    if (
                      updatedRepair.status === 'deferred' &&
                      updatedRepair.suggested_next_component_id
                    ) {
                      setSelectedMove(null)
                      setSelectedId(updatedRepair.suggested_next_component_id)
                    }
                  }}
                />
              ) : (
                <div className="learning-empty" aria-live="polite">
                  {repairLoading
                    ? zh
                      ? '正在载入修复内容…'
                      : 'Loading repair…'
                    : zh
                      ? '修复内容暂时无法载入。'
                      : 'The repair could not be loaded.'}
                </div>
              )
            ) : (
              <ComponentBody
                component={selected}
                output={outputs[selected.component_id]}
                packId={packId}
                goal={plan.goal}
                zh={zh}
                busy={busy === selected.component_id}
                onGenerate={() => void generate(selected)}
                onEvent={event => applyComponentEvent(selected, event)}
                onConfirmReview={result => void confirmReview(selected, result)}
                onRegenerateReview={result => void regenerateReview(selected, result)}
                onDiscardReview={result => void discardReview(selected, result)}
                blockedComponents={blockedComponents}
                recentCalibration={calibrations.at(-1)}
                progressCalibration={progressCalibration}
                assessmentAttempts={assessmentAttempts}
                onReviewsChanged={async () => {
                  const refreshed = await getLearningPack(packId).catch(() => null)
                  if (refreshed) setPack(refreshed)
                }}
                onOpenPrerequisite={() =>
                  setSelectedId(blockedComponents[0]?.component_id ?? selected.component_id)
                }
              />
            )}
            {error ? (
              <p role="alert" className="learning-alert--error">
                {error}
              </p>
            ) : null}
          </div>
        </section>

        <aside
          ref={drawerRef}
          className={`learning-canvas__assistant-panel ${assistantDrawerOpen ? 'learning-canvas__assistant-panel--mobile-open' : ''} ${assistantMinimized ? 'learning-canvas__assistant-panel--minimized' : ''}`}
          role={assistantDrawerOpen ? 'dialog' : undefined}
          aria-modal={assistantDrawerOpen ? true : undefined}
          aria-label={
            assistantDrawerOpen ? (zh ? '学习问答助手' : 'Learning Q&A assistant') : undefined
          }
          aria-hidden={assistantMinimized ? true : undefined}
        >
          <LearningAssistant
            pack={pack}
            plan={plan}
            component={selected}
            currentContent={learningAssistantExcerpt(outputs[selected.component_id])}
            zh={zh}
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
          aria-label={zh ? '关闭学习问答助手' : 'Close learning Q&A assistant'}
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
          aria-label={zh ? '打开学习问答助手' : 'Open learning Q&A assistant'}
        >
          <MessageCircle size={21} />
          <span className="learning-assistant-bubble__pulse" />
        </button>
      ) : null}

      <Modal
        isOpen={reviewEmptyModalOpen}
        onClose={() => setReviewEmptyModalOpen(false)}
        title={zh ? '复习队列还没有到期内容' : 'Nothing is due for review yet'}
        titleIcon={<ClipboardCheck size={16} className="learning-accent" />}
        width="md"
        footer={
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setReviewEmptyModalOpen(false)}
              className="learning-button learning-button--primary px-4 py-2 text-sm"
            >
              {zh ? '知道了' : 'Got it'}
            </button>
          </div>
        }
      >
        <div className="space-y-3 p-5">
          <p className="text-sm leading-7">
            {zh
              ? '先完成一次可判分练习（引导练习或迁移挑战）：答错的概念会进入修复队列，修复题答对后，第二天才会安排到这里复习。'
              : 'Finish a graded practice first (guided practice or transfer challenge): a missed concept enters the repair queue, and a correct repair retry is scheduled here for the next day.'}
          </p>
        </div>
      </Modal>
    </main>
  )
}

