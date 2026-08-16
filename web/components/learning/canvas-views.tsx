'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
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
import {
  getDueLearningReviews,
  recordLearningReviewResult,
  revealLearningReviewAnswer,
  revealTraitTutorGenerationFlashcard,
  retryLearningRepair,
  type AssessmentAttemptView,
  type GenerateSuiteResult,
  type LearningComponent,
  type LearningComponentPlan,
  type ProgressCalibration,
  type RepairRecord,
  type ReviewState,
} from '@/lib/traittutor-api'
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import Modal from '@/components/common/Modal'
import { PageSchemaRenderer } from './PageSchemaRenderer'
import type {
  CalibrationResult,
  ComponentEvent,
  ComponentEventResult,
  ComponentOutput,
  LearningStageType,
  VisibleAction,
} from './canvas-shared'
import {
  actionLabel,
  calibrationLabel,
  executorKind,
  outputText,
  appendTranscript,
  progressDifficultyLabel,
  progressStrategyLabel,
  stageLabel,
  statusLabel,
  modalityLabel,
  componentReason,
  componentAction,
} from './canvas-labels'

export function VoiceAnswerButton({
  zh,
  disabled,
  onTranscript,
}: {
  zh: boolean
  disabled?: boolean
  onTranscript: (text: string) => void
}) {
  const recorder = useVoiceRecorder(onTranscript)
  const recording = recorder.state === 'recording'
  const transcribing = recorder.state === 'transcribing'
  const label = recording
    ? zh
      ? '停止录音并转写'
      : 'Stop recording and transcribe'
    : zh
      ? '用语音输入答案'
      : 'Answer with voice'
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <button
        type="button"
        onClick={recorder.toggle}
        disabled={disabled || transcribing}
        aria-label={label}
        title={recorder.error || label}
        className={`learning-button learning-button--secondary px-3 py-2 text-xs ${recording ? 'border-red-500/50 text-red-500' : ''}`}
      >
        {transcribing ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          <Mic size={14} className={recording ? 'animate-pulse' : ''} />
        )}
        {transcribing ? (zh ? '正在转写' : 'Transcribing') : label}
      </button>
      {recorder.error ? (
        <span role="alert" className="text-xs text-red-500">
          {recorder.error}
        </span>
      ) : null}
    </div>
  )
}


export function ComponentBody({
  component,
  output,
  packId,
  goal,
  zh,
  busy,
  onGenerate,
  onEvent,
  onConfirmReview,
  onRegenerateReview,
  onDiscardReview,
  blockedComponents,
  recentCalibration,
  progressCalibration,
  assessmentAttempts,
  onReviewsChanged,
  onOpenPrerequisite,
}: {
  component: LearningComponent
  output?: ComponentOutput
  packId: string
  goal: string
  zh: boolean
  busy: boolean
  onGenerate: () => void
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
  onConfirmReview: (result: GenerateSuiteResult) => void
  onRegenerateReview: (result: GenerateSuiteResult) => void
  onDiscardReview: (result: GenerateSuiteResult) => void
  blockedComponents: LearningComponent[]
  recentCalibration?: CalibrationResult
  progressCalibration?: ProgressCalibration | null
  assessmentAttempts: AssessmentAttemptView[]
  onReviewsChanged: () => Promise<void>
  onOpenPrerequisite: () => void
}) {
  if (blockedComponents.length)
    return (
      <section className="learning-card learning-card--large" role="status">
        <Lock className="learning-copy-muted" />
        <p className="learning-eyebrow mt-6">
          {zh ? '前置步骤尚未完成' : 'Prerequisite step incomplete'}
        </p>
        <h3 className="mt-3 font-serif text-2xl">
          {zh ? '先完成与此组件相关的练习' : 'Complete the practice linked to this component'}
        </h3>
        <p className="learning-copy-muted mt-3 max-w-xl text-sm leading-7">
          {zh
            ? `此组件需要 ${blockedComponents.map(item => item.label_zh).join('、')} 的核验结果。完成相关练习后会自动解锁。`
            : `This component needs the verified result from ${blockedComponents.map(item => item.label_en).join(', ')}. It unlocks after that practice is complete.`}
        </p>
        <button
          type="button"
          onClick={onOpenPrerequisite}
          className="learning-button learning-button--primary mt-7 px-4 py-3 text-sm"
        >
          <ArrowLeft size={15} />
          {zh ? '查看前置步骤' : 'Open prerequisite'}
        </button>
      </section>
    )
  if (component.component_type === 'calibration_checkpoint')
    return (
      <CalibrationCheckpoint
        component={component}
        calibration={recentCalibration}
        progressCalibration={progressCalibration ?? null}
        zh={zh}
        onEvent={onEvent}
      />
    )
  if (component.component_type === 'review_queue')
    return (
      <ReviewQueueView
        packId={packId}
        component={component}
        zh={zh}
        onEvent={onEvent}
        onReviewsChanged={onReviewsChanged}
      />
    )
  if (component.component_type === 'progress_checkpoint')
    return (
      <ProgressCheckpoint
        component={component}
        goal={goal}
        progressCalibration={progressCalibration ?? null}
        zh={zh}
        onEvent={onEvent}
      />
    )
  if (component.component_type === 'reflection_prompt')
    return <ReflectionPrompt component={component} goal={goal} zh={zh} onEvent={onEvent} />
  if (component.executor === 'deterministic') {
    return (
      <div className="learning-card learning-card--large">
        <Sparkles className="learning-accent" />
        <h3 className="mt-6 font-serif text-xl">{goal}</h3>
        <p className="learning-copy-muted mt-3 text-sm leading-7">
          {zh
            ? '完成标准：能够解释核心概念、在练习中使用它，并通过一次主动回忆或迁移检查。'
            : 'Completion means explaining the core idea, using it in practice, and passing an active-recall or transfer check.'}
        </p>
        <ActionBar
          component={component}
          zh={zh}
          busy={busy}
          onRegenerate={onGenerate}
          onEvent={onEvent}
        />
      </div>
    )
  }
  if (!output)
    return (
      <div className="learning-component-stage">
        <div className="learning-component-stage__intro">
          <span className="learning-icon-badge">
            <Play size={20} />
          </span>
          <p className="learning-meta mt-4 md:mt-7">
            {zh ? '当前步骤已就绪' : 'Ready for this step'}
          </p>
          <h3 className="mt-2 max-w-xl font-serif text-2xl font-semibold md:mt-3 md:text-3xl">
            {zh ? '生成当前学习组件' : 'Generate this learning component'}
          </h3>
          <p className="learning-copy-muted mt-2 max-w-xl text-sm leading-6 md:mt-3 md:leading-7">
            {zh
              ? '系统会复用当前材料、学科知识状态和支持动作，只生成这一阶段需要的内容。'
              : 'TraitTutor reuses the current source, subject knowledge state, and support actions to generate only what this step needs.'}
          </p>
        </div>
        <div className="learning-component-stage__action">
          <div>
            <p className="text-sm font-medium">{zh ? '准备好后开始' : 'Start when ready'}</p>
            <p className="learning-copy-muted mt-1 text-xs">
              {zh
                ? '生成失败只会降级当前组件，不影响其他组件。'
                : 'A failure degrades only this component, not the other components.'}
            </p>
          </div>
          <button
            disabled={busy}
            onClick={onGenerate}
            className="learning-button learning-button--primary px-5 py-3 text-sm"
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {busy ? (zh ? '正在生成' : 'Generating') : zh ? '开始学习' : 'Start learning'}
          </button>
        </div>
      </div>
    )
  if (!('audioUrl' in output) && output.status === 'needs_review')
    return (
      <ReviewRequiredView
        result={output}
        zh={zh}
        busy={busy}
        onConfirm={() => onConfirmReview(output)}
        onRegenerate={() => onRegenerateReview(output)}
        onDiscard={() => onDiscardReview(output)}
      />
    )
  if ('audioUrl' in output)
    return (
      <div className="learning-card learning-card--large">
        <Volume2 className="learning-accent" />
        <audio controls src={output.audioUrl} className="mt-5 w-full" />
        <p className="mt-5 whitespace-pre-wrap text-sm leading-7">{output.transcript}</p>
        <ActionBar
          component={component}
          zh={zh}
          busy={busy}
          onRegenerate={onGenerate}
          onEvent={onEvent}
        />
      </div>
    )
  // F-08 (WS-9B): when the backend PAGE_SCHEMA_WIRING flag is ON, courseware
  // content is projected into a whitelist PageSchema and rendered here instead
  // of the legacy LessonView. Assessment/retrieval steps keep their grading
  // views (answer submission feeds the event chain); the schema is absent when
  // the flag is OFF, so this branch never fires and behavior is unchanged.
  if (
    output.page_schema &&
    component.executor !== 'assessment' &&
    component.executor !== 'retrieval'
  ) {
    const orchestrationSucceeded = output.result.orchestration?.status === 'succeeded'
    return (
      <div className="space-y-4">
        {!orchestrationSucceeded ? (
          <section
            role="status"
            className="learning-card border-amber-500/40 bg-amber-500/5"
            aria-label={zh ? '生成未完成' : 'Generation incomplete'}
          >
            <p className="learning-eyebrow text-amber-700 dark:text-amber-300">
              {zh ? '生成未完成' : 'Generation incomplete'}
            </p>
            <p className="learning-copy-muted mt-1 text-sm leading-6">
              {zh
                ? '本次生成遇到临时问题，当前显示的是文字降级版。点「换一种解释」重新生成完整内容。'
                : 'This run hit a temporary issue, so a text fallback is shown. Regenerate for the full content.'}
            </p>
          </section>
        ) : null}
        <PageSchemaRenderer
          schema={output.page_schema}
          orchestration={output.result.orchestration}
          component={component}
          zh={zh}
          busy={busy}
          onRegenerate={onGenerate}
          onEvent={onEvent}
        />
      </div>
    )
  }
  if (component.executor === 'assessment')
    return (
      <AssessmentView
        component={component}
        result={output}
        attempts={assessmentAttempts}
        zh={zh}
        onEvent={onEvent}
      />
    )
  if (component.executor === 'retrieval')
    return <RetrievalView component={component} result={output} zh={zh} onEvent={onEvent} />
  return (
    <LessonView
      result={output}
      component={component}
      zh={zh}
      busy={busy}
      onRegenerate={onGenerate}
      onEvent={onEvent}
    />
  )
}

export function ProgressCheckpoint({
  component,
  goal,
  progressCalibration,
  zh,
  onEvent,
}: {
  component: LearningComponent
  goal: string
  progressCalibration: ProgressCalibration | null
  zh: boolean
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const complete = async () => {
    setSaving(true)
    setError(null)
    try {
      // The checkpoint is a passive review step: the earlier radio choices
      // only produced a ``progress_strategy`` feedback nothing consumed, so
      // completing it just records the event and replans nothing.
      await onEvent({ action: 'complete', replan: false })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  return (
    <section className="learning-card learning-card--large">
      <p className="learning-eyebrow">{zh ? '进度检查' : 'Progress checkpoint'}</p>
      <h3 className="mt-4 font-serif text-xl">{goal}</h3>
      <p className="learning-copy-muted mt-3 text-sm leading-7">
        {progressCalibration
          ? zh
            ? `最近的进度校准：${progressDifficultyLabel(progressCalibration.difficulty, true)}（${progressCalibration.verified_observations} 次可信作答，答对 ${progressCalibration.correct_count}）。`
            : `Latest progress calibration: ${progressDifficultyLabel(progressCalibration.difficulty, false)} (${progressCalibration.verified_observations} verified answers, ${progressCalibration.correct_count} correct).`
          : zh
            ? '当前还没有足够的可信作答用于进度判断。'
            : 'There is not enough verified evidence for a progress judgement yet.'}
      </p>
      {progressCalibration?.recommended_strategy ? (
        <p className="mt-3 rounded-md border border-[var(--primary)]/30 bg-[var(--primary)]/[0.06] p-3 text-sm leading-6">
          {progressStrategyLabel(progressCalibration.recommended_strategy, zh)}
        </p>
      ) : null}
      <button
        disabled={saving || component.status === 'completed'}
        onClick={() => void complete()}
        className="learning-button learning-button--primary mt-6 px-4 py-3 text-sm"
      >
        {saving ? <Loader2 size={16} className="animate-spin" /> : null}
        {zh ? '完成检查' : 'Complete checkpoint'}
      </button>
      {error ? <p role="alert" className="learning-alert--error mt-4">{error}</p> : null}
    </section>
  )
}

export function ReflectionPrompt({
  component,
  goal,
  zh,
  onEvent,
}: {
  component: LearningComponent
  goal: string
  zh: boolean
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const [reflection, setReflection] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async () => {
    const value = reflection.trim()
    if (!value) return
    setSaving(true)
    setError(null)
    try {
      await onEvent({
        action: 'complete',
        feedback: `reflection:${value}`.slice(0, 600),
        replan: false,
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  return (
    <section className="learning-card learning-card--large">
      <p className="learning-eyebrow">{zh ? '反思提示' : 'Reflection prompt'}</p>
      <h3 className="mt-4 font-serif text-xl">
        {zh
          ? `围绕“${goal}”，哪一种做法最帮助你理解？下一次准备怎样调整？`
          : `For “${goal}”, what helped you understand, and what will you adjust next time?`}
      </h3>
      <p className="learning-copy-muted mt-3 text-sm leading-7">
        {zh
          ? '反思只用于后续教学支持，不参与判分，也不会改变 BKT。'
          : 'Reflection informs later teaching support only; it is not graded and never changes BKT.'}
      </p>
      <textarea
        value={reflection}
        onChange={event => setReflection(event.target.value)}
        disabled={saving}
        maxLength={580}
        className="learning-input mt-6 min-h-32 w-full"
        placeholder={zh ? '写下一个具体发现和下一步动作' : 'Write one concrete insight and next action'}
      />
      <button
        disabled={saving || !reflection.trim()}
        onClick={() => void submit()}
        className="learning-button learning-button--primary mt-4 px-4 py-3 text-sm"
      >
        {saving ? <Loader2 size={16} className="animate-spin" /> : null}
        {zh ? '保存反思' : 'Save reflection'}
      </button>
      {error ? <p role="alert" className="learning-alert--error mt-4">{error}</p> : null}
    </section>
  )
}


export function CalibrationCheckpoint({
  component,
  calibration,
  progressCalibration,
  zh,
  onEvent,
}: {
  component: LearningComponent
  calibration?: CalibrationResult
  progressCalibration: ProgressCalibration | null
  zh: boolean
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const [summary, setSummary] = useState<ProgressCalibration | null>(progressCalibration)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    // Refresh/reconnect restores the latest persisted progress calibration;
    // the local summary must follow it.
    if (progressCalibration) setSummary(progressCalibration)
  }, [progressCalibration])
  const complete = async () => {
    setSaving(true)
    setError(null)
    try {
      // The server aggregates the round's accumulated verified evidence into
      // the difficulty evaluation; the learner picks no strategy in the UI.
      const result = await onEvent({ action: 'complete', replan: true })
      if (result.progress_calibration) setSummary(result.progress_calibration)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }
  const quadrant = calibration
    ? calibrationLabel(calibration.quadrant, zh)
    : zh
      ? '等待最近一次可信作答'
      : 'Awaiting the latest verified answer'
  return (
    <section className="learning-card learning-card--large">
      <p className="learning-eyebrow">
        {zh ? '进度校准 · 学习难度评价' : 'Progress calibration · difficulty evaluation'}
      </p>
      <h3 className="mt-4 font-serif text-xl">
        {zh ? '汇总本轮判分证据，校准下一步难度' : 'Aggregate this round’s evidence, then calibrate'}
      </h3>
      {summary ? (
        <div className="mt-5 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-md border border-[var(--border)] p-4">
              <p className="learning-meta">{zh ? '累计可信作答' : 'Verified answers'}</p>
              <p className="mt-2 font-serif text-2xl">
                {summary.verified_observations}
                <span className="ml-2 text-sm text-[var(--muted-foreground)]">
                  {zh ? `答对 ${summary.correct_count}` : `${summary.correct_count} correct`}
                </span>
              </p>
            </div>
            <div className="rounded-md border border-[var(--border)] p-4">
              <p className="learning-meta">{zh ? '学习难度评价' : 'Difficulty evaluation'}</p>
              <p className="mt-2 font-serif text-lg">
                {progressDifficultyLabel(summary.difficulty, zh)}
              </p>
            </div>
          </div>
          {summary.kc_summaries.length ? (
            <div className="rounded-md border border-[var(--border)] p-4">
              <p className="learning-meta">{zh ? '按知识点汇总' : 'Per-concept summary'}</p>
              <ul className="mt-2 space-y-1 text-sm">
                {summary.kc_summaries.slice(0, 6).map(item => (
                  <li key={item.kc_id} className="flex justify-between gap-4">
                    <span className="truncate">{item.kc_id}</span>
                    <span className="shrink-0 text-[var(--muted-foreground)]">
                      {zh
                        ? `对 ${item.correct} · 错 ${item.incorrect}`
                        : `${item.correct} correct · ${item.incorrect} incorrect`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="learning-copy-muted text-sm leading-7">{summary.difficulty_reason}</p>
          {summary.recommended_strategy ? (
            <p className="rounded-md border border-[var(--primary)]/30 bg-[var(--primary)]/[0.06] p-3 text-sm leading-6">
              {progressStrategyLabel(summary.recommended_strategy, zh)}
            </p>
          ) : null}
          <p className="learning-copy-muted text-xs leading-5">{summary.boundary}</p>
        </div>
      ) : (
        <>
          {calibration ? (
            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-[var(--border)] p-4">
                <p className="learning-meta">{zh ? '提交前把握度' : 'Predicted confidence'}</p>
                <p className="mt-2 font-serif text-2xl">
                  {Math.round(calibration.confidence * 100)}%
                </p>
              </div>
              <div className="rounded-md border border-[var(--border)] p-4">
                <p className="learning-meta">{zh ? '服务器核验' : 'Server verification'}</p>
                <p className="mt-2 font-serif text-lg">{quadrant}</p>
              </div>
            </div>
          ) : (
            <p className="learning-copy-muted mt-4 text-sm">{quadrant}</p>
          )}
          <p className="learning-copy-muted mt-3 max-w-2xl text-sm leading-7">
            {zh
              ? '完成后，系统会汇总本轮所有服务端判分证据，按知识点给出定性难度评价与下一步建议。自报把握度不会改变掌握度，难度评价只调整后续教学支持。'
              : 'Completing this step aggregates every server-graded answer of this round into a per-concept qualitative difficulty evaluation and a next-step suggestion. Self-reported confidence never changes mastery; the evaluation only adjusts upcoming support.'}
          </p>
        </>
      )}
      {component.status !== 'completed' ? (
        <button
          disabled={saving}
          onClick={() => void complete()}
          className="learning-button learning-button--primary mt-6 px-5 py-3 text-sm"
        >
          {saving ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
          {saving
            ? zh
              ? '正在汇总'
              : 'Aggregating'
            : zh
              ? '完成校准并生成评价'
              : 'Complete calibration'}
        </button>
      ) : null}
      {error ? (
        <p role="alert" className="learning-alert--error mt-4">
          {error}
        </p>
      ) : null}
    </section>
  )
}

export function ReviewRequiredView({
  result,
  zh,
  busy,
  onConfirm,
  onRegenerate,
  onDiscard,
}: {
  result: GenerateSuiteResult
  zh: boolean
  busy: boolean
  onConfirm: () => void
  onRegenerate: () => void
  onDiscard: () => void
}) {
  return (
    <div className="space-y-5">
      <section className="learning-card border-amber-500/40 bg-amber-500/5">
        <p className="learning-eyebrow text-amber-700 dark:text-amber-300">
          {zh ? '需要人工确认' : 'Human review required'}
        </p>
        <h3 className="mt-2 font-serif text-xl">
          {zh
            ? '生成内容可预览，但尚不能用于当前组件。'
            : 'Preview this output before it can be used by this component.'}
        </h3>
        <p className="learning-copy-muted mt-2 text-sm leading-6">
          {zh
            ? '质量评测发现需要由你确认的问题。确认后才会保存为可判分的学习材料。'
            : 'Quality evaluation found an issue that needs your confirmation. Only confirmed output can be saved or graded.'}
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <button
            disabled={busy}
            onClick={onConfirm}
            className="learning-button learning-button--primary px-4 py-2 text-sm"
          >
            <Check size={15} />
            {zh ? '确认保存' : 'Confirm and save'}
          </button>
          <button
            disabled={busy}
            onClick={onRegenerate}
            className="learning-button learning-button--secondary px-4 py-2 text-sm"
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : <RefreshCcw size={15} />}
            {busy ? (zh ? '正在重新生成' : 'Regenerating') : zh ? '重新生成' : 'Regenerate'}
          </button>
          <button
            disabled={busy}
            onClick={onDiscard}
            className="learning-button learning-button--secondary px-4 py-2 text-sm"
          >
            <X size={15} />
            {zh ? '放弃' : 'Discard'}
          </button>
        </div>
      </section>
      <GenerationPreview result={result} zh={zh} />
    </div>
  )
}

export function GenerationPreview({ result, zh }: { result: GenerateSuiteResult; zh: boolean }) {
  const items = result.result.items ?? []
  if (items.length)
    return (
      <section className="learning-card">
        <p className="learning-eyebrow">{zh ? '预览' : 'Preview'}</p>
        <div className="mt-4 space-y-3">
          {items.map((item, index) => (
            <div
              key={String(item.question_id ?? index)}
              className="rounded-md border border-[var(--border)] p-3 text-sm"
            >
              <p className="font-medium">
                {index + 1}. {String(item.question ?? item.front ?? '')}
              </p>
              {item.back ? (
                <MarkdownRenderer
                  content={String(item.back)}
                  className="learning-copy-muted mt-1"
                  variant="compact"
                />
              ) : null}
            </div>
          ))}
        </div>
      </section>
    )
  return (
    <section className="learning-card text-sm leading-7">
      <p className="learning-eyebrow mb-3">{zh ? '预览' : 'Preview'}</p>
      <MarkdownRenderer
        content={String(outputText(result))}
        variant="compact"
      />
    </section>
  )
}

export function LessonView({
  result,
  component,
  zh,
  busy,
  onRegenerate,
  onEvent,
}: {
  result: GenerateSuiteResult
  component: LearningComponent
  zh: boolean
  busy: boolean
  onRegenerate: () => void
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const sections = result.result.sections ?? []
  const images = [
    ...(result.result.images ?? []),
    ...sections.flatMap(section => section.images ?? []),
  ]
  return (
    <div className="space-y-5">
      {images.slice(0, 2).map(image => (
        <figure key={image.url} className="learning-card overflow-hidden p-0">
          <img src={image.url} alt={image.alt} className="h-auto w-full object-contain" />
          <figcaption className="learning-copy-muted px-4 py-2 text-xs">{image.alt}</figcaption>
        </figure>
      ))}
      {sections.length ? (
        sections.map((section, index) => (
          <article key={index} className="learning-card">
            <h3 className="font-serif text-lg">{section.title ?? section.section_title}</h3>
            <div className="mt-3 space-y-3 text-sm leading-7">
              {(section.content ?? [section.core_content])
                .filter(Boolean)
                .map((paragraph, item) => (
                  <MarkdownRenderer key={item} content={String(paragraph)} variant="compact" />
                ))}
            </div>
          </article>
        ))
      ) : (
        <article className="learning-card text-sm leading-7">
          <MarkdownRenderer
            content={String(outputText(result))}
            variant="compact"
          />
        </article>
      )}
      <ActionBar
        component={component}
        zh={zh}
        busy={busy}
        onRegenerate={onRegenerate}
        onEvent={onEvent}
      />
    </div>
  )
}

export function QuestionImages({ value }: { value: unknown }) {
  if (!Array.isArray(value)) return null
  const images = value.filter((item): item is { url: string; alt?: string } =>
    Boolean(item && typeof item === 'object' && typeof (item as { url?: unknown }).url === 'string')
  )
  if (!images.length) return null
  return (
    <div className="mt-4 grid gap-3 sm:grid-cols-2">
      {images.map(image => (
        <figure
          key={image.url}
          className="overflow-hidden rounded-xl border border-[var(--border)]"
        >
          <img
            src={image.url}
            alt={image.alt || 'Question illustration'}
            className="h-auto max-h-80 w-full object-contain"
          />
        </figure>
      ))}
    </div>
  )
}

export function AssessmentView({
  component,
  result,
  attempts,
  zh,
  onEvent,
}: {
  component: LearningComponent
  result: GenerateSuiteResult
  attempts: AssessmentAttemptView[]
  zh: boolean
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const items = useMemo(() => result.result.items ?? [], [result.result.items])
  const [answers, setAnswers] = useState<Record<number, string>>({})
  const [confidence, setConfidence] = useState<Record<number, number>>({})
  const [submitted, setSubmitted] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [outcomes, setOutcomes] = useState<Record<number, 'correct' | 'incorrect'>>({})
  const [feedback, setFeedback] = useState<Record<number, string>>({})
  const [restoredQuestionIds, setRestoredQuestionIds] = useState<Set<string>>(new Set())
  const attemptRef = useRef<{
    generationId: string
    submissions: Array<{ itemIndex: number; event: ComponentEvent }>
  } | null>(null)
  const attemptByQuestion = useMemo(
    () =>
      new Map(
        attempts
          .filter(
            attempt =>
              attempt.component_id === component.component_id &&
              attempt.generated_result_id === result.generation_id
          )
          .map(attempt => [attempt.question_id, attempt] as const)
      ),
    [attempts, component.component_id, result.generation_id]
  )

  useEffect(() => {
    const restored = new Set<string>()
    const restoredAnswers: Record<number, string> = {}
    const restoredConfidence: Record<number, number> = {}
    const restoredOutcomes: Record<number, 'correct' | 'incorrect'> = {}
    const restoredFeedback: Record<number, string> = {}
    items.forEach((item, index) => {
      const questionId = String(item.question_id ?? '')
      const attempt = attemptByQuestion.get(questionId)
      if (!attempt) return
      restored.add(questionId)
      restoredAnswers[index] = attempt.user_answer
      if (attempt.confidence !== null && attempt.confidence !== undefined)
        restoredConfidence[index] = attempt.confidence
      restoredOutcomes[index] = attempt.correct ? 'correct' : 'incorrect'
      restoredFeedback[index] =
        attempt.explanation ?? (zh ? '历史解析不可用。' : 'Historical explanation unavailable.')
    })
    setAnswers(restoredAnswers)
    setConfidence(restoredConfidence)
    setOutcomes(restoredOutcomes)
    setFeedback(restoredFeedback)
    setRestoredQuestionIds(restored)
    setSubmitted(items.length > 0 && restored.size === items.length)
  }, [attemptByQuestion, items, zh])
  const submit = async () => {
    setSubmitting(true)
    setSubmitError(null)
    const attempt =
      attemptRef.current?.generationId === result.generation_id
        ? attemptRef.current
        : (() => {
            const pendingItems = items
              .map((item, itemIndex) => ({ item, itemIndex }))
              .filter(({ item }) => !restoredQuestionIds.has(String(item.question_id ?? '')))
            return {
              generationId: result.generation_id,
              submissions: pendingItems.map(({ item, itemIndex }, submissionIndex) => {
                const questionId = String(item.question_id ?? '')
                return {
                  itemIndex,
                  event: {
                    // Component ids are unique per plan. Including one prevents a
                    // legitimate later attempt that reuses a durable quiz artifact
                    // from colliding in the owner-wide learner-evidence ledger.
                    event_id:
                      `${component.component_id}:${result.generation_id}:${questionId || itemIndex}`.slice(
                        0,
                        128
                      ),
                    action:
                      submissionIndex === pendingItems.length - 1
                        ? ('complete' as const)
                        : ('feedback' as const),
                    answer: answers[itemIndex] ?? '',
                    question_id: questionId,
                    output_ref: result.generation_id,
                    concept_id: String(
                      item.node_id ?? component.concept_refs[0] ?? component.component_id
                    ),
                    concept_label: String(item.node_name ?? item.question ?? 'Concept'),
                    confidence: confidence[itemIndex],
                    replan: false,
                  },
                }
              }),
            }
          })()
    attemptRef.current = attempt
    try {
      for (const submission of attempt.submissions) {
        const eventResult = await onEvent(submission.event)
        const verifiedObservation = eventResult.verified_observation
        if (verifiedObservation === 'correct' || verifiedObservation === 'incorrect') {
          setOutcomes(current => ({
            ...current,
            [submission.itemIndex]: verifiedObservation,
          }))
          if (eventResult.verified_feedback) {
            setFeedback(current => ({
              ...current,
              [submission.itemIndex]: eventResult.verified_feedback!,
            }))
          }
        }
      }
      setSubmitted(true)
      attemptRef.current = null
    } catch {
      setSubmitError(
        zh
          ? '答案暂未保存，请检查后重试。'
          : 'Your answers were not saved. Review them and try again.'
      )
    } finally {
      setSubmitting(false)
    }
  }
  const allAnswered = items.every(
    (_, index) => Boolean(answers[index]?.trim()) && confidence[index] !== undefined
  )
  const retrying = Boolean(submitError && attemptRef.current?.generationId === result.generation_id)
  return (
    <div className="space-y-4">
      {items.map((item, index) => {
        const options = Array.isArray(item.options)
          ? (item.options as Array<Record<string, unknown>>)
          : []
        const hasChoices = options.length > 0
        const questionId = String(item.question_id ?? '')
        const historicalAttempt = attemptByQuestion.get(questionId)
        return (
          <fieldset
            key={String(item.question_id ?? index)}
            disabled={submitted || restoredQuestionIds.has(questionId) || submitting || retrying}
            className="learning-card"
          >
            <legend className="px-2 font-serif text-lg">
              {index + 1}. {String(item.question ?? '')}
            </legend>
            <QuestionImages value={item.images} />
            {hasChoices ? (
              <div className="mt-3 grid gap-2">
                {options.map((option, optionIndex) => {
                  const value = String(option.key ?? option.id ?? option.text ?? optionIndex)
                  return (
                    <label
                      key={value}
                      className={`learning-choice ${answers[index] === value ? 'learning-choice--selected' : ''}`}
                    >
                      <input
                        type="radio"
                        name={`q-${index}`}
                        value={value}
                        checked={answers[index] === value}
                        onChange={() => setAnswers(current => ({ ...current, [index]: value }))}
                      />
                      {String(option.text ?? value)}
                    </label>
                  )
                })}
              </div>
            ) : (
              <div className="mt-3">
                <label
                  htmlFor={`assessment-answer-${index}`}
                  className="learning-meta block text-[10px]"
                >
                  {zh ? '写下你的答案与思路' : 'Write your answer and reasoning'}
                </label>
                <textarea
                  id={`assessment-answer-${index}`}
                  value={answers[index] ?? ''}
                  onChange={event =>
                    setAnswers(current => ({ ...current, [index]: event.target.value }))
                  }
                  placeholder={
                    zh
                      ? '用自己的话作答；提交后由服务端依据可信题目核验。'
                      : 'Answer in your own words; the server verifies it against the trusted question.'
                  }
                  className="mt-2 min-h-28 w-full rounded-md border border-[var(--border)] bg-[var(--background)] p-3 text-sm leading-6 outline-none focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/20"
                />
                <VoiceAnswerButton
                  zh={zh}
                  disabled={submitted || submitting || retrying}
                  onTranscript={text =>
                    setAnswers(current => ({
                      ...current,
                      [index]: appendTranscript(current[index] ?? '', text),
                    }))
                  }
                />
              </div>
            )}
            <div className="mt-5 border-t border-[var(--border)] pt-4">
              <p className="learning-meta text-[10px]">
                {zh ? '提交前：你对这题有多大把握？' : 'Before submitting: how confident are you?'}
              </p>
              <div className="mt-2 grid gap-2 sm:grid-cols-3">
                {(
                  [
                    { value: 0.35, zh: '把握不大', en: 'Not very' },
                    { value: 0.65, zh: '有些把握', en: 'Somewhat' },
                    { value: 0.9, zh: '很有把握', en: 'Very' },
                  ] as const
                ).map(level => (
                  <label
                    key={level.value}
                    className={`learning-choice ${confidence[index] === level.value ? 'learning-choice--selected' : ''}`}
                  >
                    <input
                      type="radio"
                      name={`confidence-${index}`}
                      checked={confidence[index] === level.value}
                      onChange={() =>
                        setConfidence(current => ({ ...current, [index]: level.value }))
                      }
                    />
                    {zh ? level.zh : level.en}
                  </label>
                ))}
              </div>
            </div>
            {outcomes[index] ? (
              <div
                className={`mt-3 rounded-md border px-3 py-2 text-xs leading-5 ${outcomes[index] === 'correct' ? 'border-emerald-500/35 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200' : 'border-amber-500/35 bg-amber-500/10 text-amber-900 dark:text-amber-100'}`}
                role="status"
              >
                <p className="font-medium">
                  {outcomes[index] === 'correct'
                    ? zh
                      ? '核验通过'
                      : 'Verified correct'
                    : zh
                      ? '这题还需要再看一遍'
                      : 'This needs another pass'}
                </p>
                {feedback[index] ? <p className="mt-1">{feedback[index]}</p> : null}
                {historicalAttempt?.reference_answer ? (
                  <p className="mt-2">
                    <span className="font-medium">{zh ? '参考答案：' : 'Reference answer: '}</span>
                    {historicalAttempt.reference_answer}
                  </p>
                ) : null}
                {historicalAttempt ? (
                  <p className="mt-2 text-[10px] opacity-75">
                    {zh ? '只读历史作答' : 'Read-only historical attempt'} ·{' '}
                    {historicalAttempt.submitted_at || (zh ? '提交时间未知' : 'Time unavailable')}
                  </p>
                ) : null}
              </div>
            ) : null}
            {submitted && !outcomes[index] ? (
              <p role="status" className="learning-success mt-3 text-xs">
                {zh
                  ? '答案已保存，并记录到当前学科的学习证据。'
                  : 'Your answer was saved as subject-scoped learning evidence.'}
              </p>
            ) : null}
          </fieldset>
        )
      })}
      <div className="flex flex-wrap items-center gap-3">
        <button
          disabled={!items.length || (!allAnswered && !retrying) || submitted || submitting}
          onClick={() => void submit()}
          className="learning-button learning-button--primary px-5 py-3 text-sm"
        >
          {submitting ? (
            <Loader2 size={16} className="animate-spin" />
          ) : retrying ? (
            <RefreshCcw size={16} />
          ) : null}
          {submitting
            ? zh
              ? '正在核验'
              : 'Verifying'
            : retrying
              ? zh
                ? '重试同一次提交'
                : 'Retry the same submission'
              : zh
                ? '提交答案'
                : 'Submit answers'}
        </button>
      </div>
      {submitError ? (
        <p role="alert" className="learning-alert--error">
          {submitError}
        </p>
      ) : null}
    </div>
  )
}

export function ReviewQueueView({
  packId,
  component,
  zh,
  onEvent,
  onReviewsChanged,
}: {
  packId: string
  component: LearningComponent
  zh: boolean
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
  onReviewsChanged: () => Promise<void>
}) {
  const [items, setItems] = useState<ReviewState[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [answer, setAnswer] = useState('')
  const [revealedAnswer, setRevealedAnswer] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const eventIds = useRef<Record<string, string>>({})
  const current = items[0]

  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    void getDueLearningReviews(packId)
      .then(result => {
        if (active) setItems(result.items)
      })
      .catch(reason => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [packId])

  useEffect(() => {
    setAnswer('')
    setRevealedAnswer(null)
    setMessage(null)
    setError(null)
  }, [current?.review_id])

  const stableEventId = (review: ReviewState) => {
    const key = `${review.review_id}:${review.due_at}`
    if (!eventIds.current[key]) eventIds.current[key] = `review-${crypto.randomUUID()}`
    return eventIds.current[key]
  }

  const finish = async (review: ReviewState, result: { answer?: string; rating?: 'known' | 'uncertain' | 'unknown' }) => {
    setSaving(true)
    setError(null)
    try {
      const saved = await recordLearningReviewResult(packId, review.review_id, {
        event_id: stableEventId(review),
        ...result,
      })
      setMessage(
        saved.verified
          ? saved.correct
            ? zh
              ? '回答正确，已安排下一次复习。'
              : 'Correct. The next review is scheduled.'
            : zh
              ? '答案已核验，并按当前结果重新安排复习。'
              : 'The answer was verified and the review was rescheduled.'
          : zh
            ? '回忆记录已保存，并已安排下一次复习。'
            : 'Your recall rating was saved and the next review is scheduled.'
      )
      const remaining = items.filter(item => item.review_id !== review.review_id)
      setItems(remaining)
      await onReviewsChanged()
      if (!remaining.length && !['completed', 'skipped'].includes(component.status)) {
        await onEvent({ action: 'complete', replan: false })
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  const reveal = async (review: ReviewState) => {
    setSaving(true)
    setError(null)
    try {
      const result = await revealLearningReviewAnswer(packId, review.review_id)
      setRevealedAnswer(result.answer)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setSaving(false)
    }
  }

  if (loading)
    return (
      <div className="learning-card learning-card--large" role="status">
        <Loader2 className="animate-spin" />
        <p className="learning-copy-muted mt-3 text-sm">
          {zh ? '正在载入到期复习…' : 'Loading due reviews…'}
        </p>
      </div>
    )

  if (!current)
    return (
      <div className="learning-card learning-card--large">
        <ClipboardCheck className="learning-accent" />
        <h3 className="mt-5 font-serif text-xl">{zh ? '当前没有到期复习' : 'Nothing is due'}</h3>
        <p className="learning-copy-muted mt-3 text-sm leading-7">
          {message ??
            (zh
              ? '真实复习队列已经检查完毕；这里只记录完成状态，不把阅读或自评当作掌握证据。'
              : 'The canonical review queue is clear. This only records completion; reading or self-rating never becomes mastery evidence.')}
        </p>
        {!['completed', 'skipped'].includes(component.status) ? (
          <button
            disabled={saving}
            onClick={() => void onEvent({ action: 'complete', replan: false })}
            className="learning-button learning-button--primary mt-6 px-4 py-3 text-sm"
          >
            {zh ? '完成检查并继续' : 'Complete check and continue'}
          </button>
        ) : null}
        {error ? <p role="alert" className="learning-alert--error mt-4">{error}</p> : null}
      </div>
    )

  const choiceOptions = current.options ?? []
  return (
    <div className="learning-card learning-card--large">
      <p className="learning-eyebrow">{zh ? '真实到期复习' : 'Canonical due review'}</p>
      <p className="learning-copy-muted mt-2 text-xs">
        {items.length} {zh ? '项待完成' : 'item(s) remaining'}
      </p>
      <h3 className="mt-6 font-serif text-xl leading-8">
        {current.prompt || current.concept_id}
      </h3>
      {current.source === 'retrieval' ? (
        revealedAnswer ? (
          <>
            <p className="learning-action-bar mt-6 border-t pt-5 text-sm leading-7">
              {revealedAnswer}
            </p>
            <div className="mt-6 grid gap-2 sm:grid-cols-3">
              {(['unknown', 'uncertain', 'known'] as const).map(rating => (
                <button
                  key={rating}
                  disabled={saving}
                  onClick={() => void finish(current, { rating })}
                  className="learning-button learning-button--secondary px-3 py-3 text-sm"
                >
                  {{
                    unknown: zh ? '还不熟' : 'Not yet',
                    uncertain: zh ? '有点模糊' : 'Uncertain',
                    known: zh ? '记得清楚' : 'Known',
                  }[rating]}
                </button>
              ))}
            </div>
          </>
        ) : (
          <button
            disabled={saving}
            onClick={() => void reveal(current)}
            className="learning-button learning-button--secondary mt-6 px-4 py-3 text-sm"
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : null}
            {zh ? '翻面核对' : 'Reveal answer'}
          </button>
        )
      ) : (
        <div className="mt-6 space-y-4">
          {choiceOptions.length ? (
            <fieldset className="grid gap-2" disabled={saving}>
              <legend className="sr-only">{zh ? '选择答案' : 'Choose an answer'}</legend>
              {choiceOptions.map((option, index) => {
                const value = String(option.key ?? option.id ?? option.text ?? index)
                return (
                  <label key={value} className={`learning-choice ${answer === value ? 'learning-choice--selected' : ''}`}>
                    <input type="radio" checked={answer === value} onChange={() => setAnswer(value)} />
                    {option.text ?? value}
                  </label>
                )
              })}
            </fieldset>
          ) : (
            <textarea
              value={answer}
              onChange={event => setAnswer(event.target.value)}
              disabled={saving}
              className="learning-input min-h-28 w-full"
              placeholder={zh ? '输入你的答案' : 'Enter your answer'}
            />
          )}
          <button
            disabled={saving || !answer.trim()}
            onClick={() => void finish(current, { answer })}
            className="learning-button learning-button--primary px-4 py-3 text-sm"
          >
            {saving ? <Loader2 size={16} className="animate-spin" /> : null}
            {zh ? '提交核验' : 'Submit for verification'}
          </button>
        </div>
      )}
      {error ? <p role="alert" className="learning-alert--error mt-4">{error}</p> : null}
    </div>
  )
}

export function RetrievalView({
  component,
  result,
  zh,
  onEvent,
}: {
  component: LearningComponent
  result: GenerateSuiteResult
  zh: boolean
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const items = useMemo(
    () => (result.result.items?.length ? result.result.items : [{}]),
    [result.result.items]
  )
  const [currentIndex, setCurrentIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [saving, setSaving] = useState(false)
  const [ratingError, setRatingError] = useState<string | null>(null)
  const [revealedAnswers, setRevealedAnswers] = useState<Record<string, string>>({})
  const [revealing, setRevealing] = useState(false)
  const item = items[currentIndex] ?? {}
  const isLast = currentIndex === items.length - 1
  const itemId = String(item.question_id ?? item.node_id ?? currentIndex)
  const revealedAnswer = revealedAnswers[itemId]

  useEffect(() => {
    setCurrentIndex(0)
    setFlipped(false)
    setSaving(false)
    setRatingError(null)
    setRevealedAnswers({})
    setRevealing(false)
  }, [result.generation_id])

  const reveal = async () => {
    setRevealing(true)
    setRatingError(null)
    try {
      const response = await revealTraitTutorGenerationFlashcard(result.generation_id, itemId)
      setRevealedAnswers(current => ({ ...current, [itemId]: response.answer }))
      setFlipped(true)
    } catch (error) {
      setRatingError(
        error instanceof Error
          ? error.message
          : zh
            ? '答案暂时无法载入，请重试。'
            : 'The answer could not be loaded. Try again.'
      )
    } finally {
      setRevealing(false)
    }
  }

  const rate = async (state: 'unknown' | 'uncertain' | 'known') => {
    setSaving(true)
    setRatingError(null)
    try {
      await onEvent({
        event_id:
          `retrieval:${component.component_id}:${result.generation_id}:${itemId}:${state}`.slice(
            0,
            128
          ),
        action: isLast ? 'complete' : 'feedback',
        observation: state,
        question_id: itemId,
        concept_id: String(item.node_id ?? component.concept_refs[0] ?? component.component_id),
        concept_label: String(item.node_name ?? item.front ?? 'Concept'),
        output_ref: result.generation_id,
        replan: false,
      })
      if (!isLast) {
        setCurrentIndex(index => index + 1)
        setFlipped(false)
      }
    } catch (error) {
      setRatingError(
        error instanceof Error
          ? error.message
          : zh
            ? '闪卡进度暂未保存，请重试。'
            : 'Flashcard progress was not saved. Try again.'
      )
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="learning-card learning-card--large text-center">
      <p className="learning-eyebrow">{zh ? '主动回忆' : 'Active recall'}</p>
      <p className="learning-copy-muted mt-3 text-xs">
        {currentIndex + 1} / {items.length}
      </p>
      <h3 className="mx-auto mt-12 max-w-2xl font-serif text-2xl leading-10">
        {String(item.front ?? item.question ?? result.result.title)}
      </h3>
      {flipped ? (
        <p className="learning-action-bar mx-auto mt-8 max-w-2xl border-t pt-6 text-sm leading-7">
          {revealedAnswer}
        </p>
      ) : (
        <button
          onClick={() => void reveal()}
          disabled={saving || revealing}
          className="learning-button learning-button--secondary mt-10 px-4 py-2 text-sm"
        >
          {revealing ? <Loader2 size={16} className="animate-spin" /> : null}
          {revealing ? (zh ? '正在载入' : 'Loading') : zh ? '翻面核对' : 'Reveal answer'}
        </button>
      )}
      {flipped ? (
        <>
          <p className="learning-copy-muted mx-auto mt-4 max-w-xl text-xs leading-5">
            {zh
              ? '这是你的自我回忆记录，不会单独改变掌握度或自动重规划。'
              : 'This self-recall is recorded as participation; it does not by itself change mastery or replan the path.'}
          </p>
          <div className="mt-6 grid gap-2 sm:grid-cols-3">
            {(['unknown', 'uncertain', 'known'] as const).map(state => (
              <button
                key={state}
                disabled={saving}
                onClick={() => void rate(state)}
                className="learning-button learning-button--secondary px-3 py-3 text-sm"
              >
                {
                  {
                    unknown: zh ? '还不熟' : 'Not yet',
                    uncertain: zh ? '有点模糊' : 'Uncertain',
                    known: zh ? '掌握了' : 'Known',
                  }[state]
                }
              </button>
            ))}
          </div>
        </>
      ) : null}
      {ratingError ? (
        <p role="alert" className="learning-alert--error mt-4">
          {ratingError}
        </p>
      ) : null}
    </div>
  )
}

export function ActionBar({
  component,
  zh,
  busy,
  onRegenerate,
  onEvent,
}: {
  component: LearningComponent
  zh: boolean
  busy: boolean
  onRegenerate: () => void
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const [pending, setPending] = useState<ComponentEvent['action'] | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const fire = async (event: ComponentEvent) => {
    if (pending) return
    setPending(event.action)
    setActionError(null)
    try {
      await onEvent({
        event_id: `${component.component_id}:${event.action}`.slice(0, 128),
        ...event,
      })
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setPending(null)
    }
  }
  // A completed/skipped step has no actions left: completing it again would
  // be a server 409 ("Cannot complete a completed component"), so do not
  // offer the buttons at all. This also covers re-selecting a finished step
  // from the sidebar route.
  if (['completed', 'skipped'].includes(component.status)) return null
  const hideComplete = component.component_type === 'concept_explanation'
  return (
    <div className="learning-action-bar mt-6 flex flex-wrap gap-2 border-t pt-4">
      {!hideComplete ? (
        <button
          disabled={pending !== null || busy}
          onClick={() => void fire({ action: 'complete', replan: false })}
          className="learning-button learning-button--primary"
        >
          <Check size={14} />
          {zh ? '标记完成' : 'Mark complete'}
        </button>
      ) : null}
      {!component.required ? (
        <button
          disabled={pending !== null || busy}
          onClick={() => void fire({ action: 'skip', replan: false })}
          className="learning-button learning-button--secondary"
        >
          <SkipForward size={14} />
          {zh ? '跳过' : 'Skip'}
        </button>
      ) : null}
      <button
        disabled={pending !== null || busy}
        onClick={onRegenerate}
        className="learning-button learning-button--secondary"
      >
        <RefreshCcw size={14} />
        {busy ? (zh ? '生成中…' : 'Generating…') : zh ? '换一种解释' : 'Explain differently'}
      </button>
      {actionError ? (
        <p role="alert" className="learning-copy-muted ml-auto self-center text-xs text-red-600 dark:text-red-400">
          {actionError}
        </p>
      ) : null}
    </div>
  )
}

export function RepairCard({
  packId,
  repair,
  zh,
  onComplete,
}: {
  packId: string
  repair: RepairRecord
  zh: boolean
  onComplete: (repair: RepairRecord) => Promise<void>
}) {
  const [answer, setAnswer] = useState('')
  const [checking, setChecking] = useState(false)
  const [result, setResult] = useState<boolean | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const attemptRef = useRef<{ repairId: string; eventId: string; answer: string } | null>(null)
  const submit = async () => {
    const normalizedAnswer = answer.trim()
    if (!normalizedAnswer) return
    setChecking(true)
    setSubmitError(null)
    const attempt =
      attemptRef.current?.repairId === repair.repair_id
        ? attemptRef.current
        : { repairId: repair.repair_id, eventId: crypto.randomUUID(), answer: normalizedAnswer }
    attemptRef.current = attempt
    try {
      const response = await retryLearningRepair(packId, repair.repair_id, {
        event_id: attempt.eventId,
        answer: attempt.answer,
      })
      setResult(response.verified_correct)
      if (response.verified_correct || response.recovery.deferred) {
        await onComplete(response.repair)
      }
      attemptRef.current = null
    } catch (error) {
      setSubmitError(
        error instanceof Error
          ? error.message
          : zh
            ? '提交失败，请重试同一次答案。'
            : 'Submission failed. Retry the same answer.'
      )
    } finally {
      setChecking(false)
    }
  }
  const options = repair.retry_options ?? []
  const retrying = Boolean(submitError && attemptRef.current?.repairId === repair.repair_id)
  return (
    <section className="learning-card learning-card--large">
      <p className="learning-eyebrow">
        {zh ? '错误修复 · 约 90 秒' : 'Error repair · about 90 seconds'}
      </p>
      <h3 className="mt-4 font-serif text-2xl">
        {zh ? '先修复这个理解缺口' : 'Repair this gap first'}
      </h3>
      <div className="mt-6 grid gap-4">
        <div className="rounded-md border border-[var(--border)] p-4">
          <p className="learning-meta">{zh ? '你刚才的答案' : 'Your answer'}</p>
          <p className="mt-2 text-sm">{repair.user_answer}</p>
        </div>
        <div className="rounded-md border border-[var(--primary)]/30 bg-[var(--primary)]/5 p-4">
          <p className="learning-meta">{zh ? '正确规则与关键对比' : 'Correct rule and contrast'}</p>
          <p className="mt-2 text-sm leading-7">{repair.correct_rule}</p>
          {repair.contrast ? (
            <p className="learning-copy-muted mt-2 text-xs">
              {zh ? '关键答案：' : 'Key answer: '}
              {repair.contrast}
            </p>
          ) : null}
        </div>
        <fieldset disabled={checking || retrying} className="block">
          <legend className="learning-meta">{zh ? '立即重试' : 'Immediate retry'}</legend>
          <span className="mt-2 block text-sm leading-6">
            {repair.retry_prompt ??
              (zh ? '用修正后的规则再回答一次。' : 'Answer again using the corrected rule.')}
          </span>
          {options.length ? (
            <div className="mt-3 grid gap-2">
              {options.map((option, index) => {
                const value = String(option.key ?? option.id ?? option.text ?? index)
                return (
                  <label
                    key={String(option.key ?? option.id ?? index)}
                    className={`learning-choice ${answer === value ? 'learning-choice--selected' : ''}`}
                  >
                    <input
                      type="radio"
                      name={`repair-${repair.repair_id}`}
                      checked={answer === value}
                      onChange={() => setAnswer(value)}
                    />
                    {String(option.text ?? option.key ?? option.id ?? '')}
                  </label>
                )
              })}
            </div>
          ) : (
            <div>
              <textarea
                value={answer}
                onChange={event => setAnswer(event.target.value)}
                className="mt-3 min-h-24 w-full rounded-md border border-[var(--border)] bg-[var(--background)] p-3 text-sm outline-none focus:border-[var(--primary)]"
              />
              <VoiceAnswerButton
                zh={zh}
                disabled={checking || retrying}
                onTranscript={text => setAnswer(current => appendTranscript(current, text))}
              />
            </div>
          )}
        </fieldset>
      </div>
      {result === false ? (
        <p role="status" className="learning-alert--error mt-4">
          {repair.status === 'deferred'
            ? zh
              ? '这个修复项已暂缓。先完成其他知识点的一次练习，再从修复队列回来。'
              : 'This repair is paused. Try another objective once, then return from the repair queue.'
            : zh
              ? '还没有通过核验。回看上面的关键对比，再试一次。'
              : 'Not verified yet. Review the contrast and try again.'}
        </p>
      ) : null}
      {submitError ? (
        <p role="alert" className="learning-alert--error mt-4">
          {submitError}
        </p>
      ) : null}
      {repair.retry_evidence_strength === 'weak' ? (
        <p role="status" className="learning-copy-muted mt-3 text-xs">
          {zh
            ? '当前没有可信的新题，这次仅作练习展示，不会再次写入强掌握证据。'
            : 'No trustworthy new item is available. This is display-only practice and will not add strong mastery evidence.'}
        </p>
      ) : null}
      <button
        disabled={!answer.trim() || checking || retrying}
        onClick={() => void submit()}
        className="learning-button learning-button--primary mt-6 px-5 py-3 text-sm"
      >
        {checking ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}
        {checking
          ? zh
            ? '正在核验'
            : 'Checking'
          : zh
            ? '提交重试并安排复习'
            : 'Submit retry and schedule review'}
      </button>
      {retrying ? (
        <button
          disabled={checking}
          onClick={() => void submit()}
          className="learning-button learning-button--secondary mt-3 px-5 py-3 text-sm"
        >
          <RefreshCcw size={16} />
          {zh ? '重试同一次提交' : 'Retry the same submission'}
        </button>
      ) : null}
    </section>
  )
}


export function ActionStatusIcon({
  status,
  active,
}: {
  status: VisibleAction['status']
  active: boolean
}) {
  if (status === 'completed') return <Check size={15} className="learning-accent" />
  if (active || status === 'ready') return <span className="learning-status-dot" />
  return <Circle size={14} className="text-[var(--border)]" />
}

export function FullState({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <main className="learning-full-state">
      <div className="learning-accent text-center">
        {icon}
        <p className="mt-4 font-serif text-xl text-[var(--foreground)]">{title}</p>
      </div>
    </main>
  )
}
