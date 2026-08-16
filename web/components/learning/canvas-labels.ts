import {
  getTraitTutorGenerationTask,
  type GenerateKind,
  type GenerateSuiteResult,
  type LearningComponent,
  type LearningComponentType,
  type ProgressCalibration,
} from '@/lib/traittutor-api'
import type { ComponentOutput, LearningStageType, VisibleAction } from './canvas-shared'

export function appendTranscript(current: string, transcript: string): string {
  const existing = current.trimEnd()
  return existing ? `${existing} ${transcript}` : transcript
}

export const TEACHING_COMPONENT_TYPES = new Set([
  'concept_explanation',
  'worked_example',
  'visual_map',
  'video_explanation',
  'audio_explanation',
])

export function learningAssistantExcerpt(output?: ComponentOutput): string {
  if (!output) return ''
  if ('audioUrl' in output) return output.transcript.slice(0, 24_000)

  const parts: string[] = []
  if (output.page_schema) {
    for (const region of output.page_schema.regions) {
      if (region.heading) parts.push(region.heading)
      if (region.component) collectVisibleStrings(region.component.props, parts)
    }
  } else {
    if (output.result.title) parts.push(output.result.title)
    if (output.result.markdown) parts.push(output.result.markdown)
    for (const section of output.result.sections ?? []) {
      if (section.title || section.section_title)
        parts.push(section.title ?? section.section_title ?? '')
      parts.push(...(section.content ?? []))
      if (section.core_content) parts.push(section.core_content)
    }
    for (const item of (output.result.items ?? []).slice(0, 20)) {
      // Only the question face is sent to the assistant. The answer face
      // (back/answer) and any prompt that may embed a hint or solution are
      // server-held and must not leak into the chat context before reveal.
      for (const key of ['question', 'front']) {
        const value = item[key]
        if (typeof value === 'string') parts.push(value)
      }
    }
  }
  return parts.filter(Boolean).join('\n\n').slice(0, 24_000)
}

export function collectVisibleStrings(value: unknown, target: string[]): void {
  if (typeof value === 'string') {
    target.push(value)
    return
  }
  if (Array.isArray(value)) {
    value.forEach(item => collectVisibleStrings(item, target))
    return
  }
  if (!value || typeof value !== 'object') return
  for (const [key, item] of Object.entries(value)) {
    if (/url|href|id|answer|rubric|hint/i.test(key)) continue
    collectVisibleStrings(item, target)
  }
}


export function groupVisibleActions(components: LearningComponent[]): VisibleAction[] {
  const completedIds = new Set(
    components
      .filter(item => ['completed', 'skipped'].includes(item.status))
      .map(item => item.component_id)
  )
  return components.map(component => {
    const actionId = component.component_id
    const actionType = componentAction(component.component_type)
    const items = [component]
    if (['completed', 'skipped'].includes(component.status))
      return { actionId, actionType, components: items, status: 'completed' as const }
    if (!component.dependencies.every(dependency => completedIds.has(dependency)))
      return { actionId, actionType, components: items, status: 'locked' as const }
    return {
      actionId,
      actionType,
      components: items,
      status: component.status === 'active' ? ('active' as const) : ('ready' as const),
    }
  })
}
export function componentAction(type: LearningComponentType): LearningStageType {
  if (['goal_map'].includes(type)) return 'mission'
  if (
    [
      'concept_explanation',
      'worked_example',
      'visual_map',
      'video_explanation',
      'audio_explanation',
    ].includes(type)
  )
    return 'learn'
  if (['diagnostic_check', 'guided_practice', 'transfer_challenge'].includes(type)) return 'try'
  if (['calibration_checkpoint', 'progress_checkpoint', 'reflection_prompt'].includes(type))
    return 'decide'
  return 'remember'
}
export function actionLabel(action: LearningStageType, zh: boolean): string {
  return (
    {
      mission: zh ? '本轮任务' : 'Mission',
      learn: zh ? '理解' : 'Learn',
      try: zh ? '尝试' : 'Try',
      fix: zh ? '修复缺口' : 'Fix the gap',
      decide: zh ? '校准与下一步' : 'Decide',
      remember: zh ? '今日复习' : 'Remember',
    } as Record<LearningStageType, string>
  )[action]
}
export function calibrationLabel(quadrant: string, zh: boolean): string {
  return (
    (
      {
        confident_correct: zh ? '高把握且答对' : 'Confident and correct',
        uncertain_correct: zh ? '低把握但答对' : 'Uncertain but correct',
        confident_incorrect: zh ? '高把握但答错' : 'Confident but incorrect',
        uncertain_incorrect: zh ? '低把握且答错' : 'Uncertain and incorrect',
      } as Record<string, string>
    )[quadrant] ?? quadrant.replaceAll('_', ' ')
  )
}

export function stageLabel(stage: string, zh: boolean): string {
  if (!zh) return stage.replaceAll('_', ' ')
  return (
    (
      {
        unobserved: '尚未观察',
        emerging: '正在形成',
        developing: '逐步理解',
        proficient: '基本掌握',
        mastered: '稳定掌握',
        needs_support: '需要支持',
      } as Record<string, string>
    )[stage] ?? stage.replaceAll('_', ' ')
  )
}
export function modalityLabel(modality: string, zh: boolean): string {
  if (!zh) return modality.replaceAll('_', ' ')
  return (
    (
      {
        interactive: '互动',
        visual: '图解',
        video: '视频',
        audio: '语音',
        text: '阅读',
        assessment: '诊断',
        retrieval: '回忆',
      } as Record<string, string>
    )[modality] ?? modality.replaceAll('_', ' ')
  )
}
export function statusLabel(status: string, zh: boolean): string {
  if (!zh) return status
  return (
    (
      {
        pending: '待开始',
        active: '进行中',
        completed: '已完成',
        skipped: '已跳过',
        degraded: '已降级',
      } as Record<string, string>
    )[status] ?? status
  )
}
export function componentReason(component: LearningComponent, zh: boolean): string {
  if (!zh) return component.reason
  return (
    (
      {
        goal_map: '先明确目标、阶段与完成标准，让后续学习有清晰方向。',
        diagnostic_check: '可选的一次性起点判断；结果不写入 BKT，也不会触发新计划。',
        concept_explanation: '结合当前知识状态补足核心概念，再进入练习。',
        worked_example: '通过分步例题把概念连接到可执行的方法。',
        visual_map: '用关系图呈现重点概念和它们之间的联系。',
        video_explanation: '用短视频动态呈现课件中的核心概念。',
        audio_explanation: '把当前课件转成来源受限的播客脚本，并由导师语音讲解。',
        guided_practice: '在提示和即时反馈下完成练习，形成可判分证据。',
        calibration_checkpoint: '将作答前的把握度与核验结果对照，选定下一次更有效的学习策略。',
        retrieval_card: '用主动回忆检验保持程度，并安排后续复习。',
        progress_checkpoint: '回看当前证据，确认下一阶段最值得投入的内容。',
        reflection_prompt: '用简短反思整理本轮学习策略，不把自评当作掌握证据。',
        transfer_challenge: '把已学知识迁移到新情境，检验能否灵活运用。',
        review_queue: '优先复习已到期或仍需支持的概念。',
      } as Record<string, string>
    )[component.component_type] ?? component.reason
  )
}

export function progressDifficultyLabel(
  difficulty: ProgressCalibration['difficulty'],
  zh: boolean
): string {
  if (!difficulty) return zh ? '证据不足' : 'Insufficient evidence'
  return (
    {
      smooth: zh ? '顺畅' : 'Smooth',
      can_continue: zh ? '可继续' : 'Can continue',
      needs_support: zh ? '需要支持' : 'Needs support',
      blocked: zh ? '明显受阻' : 'Blocked',
    } as const
  )[difficulty]
}

export function progressStrategyLabel(
  strategy: ProgressCalibration['recommended_strategy'],
  zh: boolean
): string {
  const labels = {
    transfer_or_schedule_review: zh
      ? '建议：尝试迁移到新情境，或把错题安排进复习队列。'
      : 'Suggest a transfer challenge, or schedule missed concepts for review.',
    self_explain_then_retrieve: zh
      ? '建议：先用自己的话解释关键步骤，再主动回忆。'
      : 'Self-explain the key steps, then retrieve them from memory.',
    worked_example_then_guided_retry: zh
      ? '建议：先回看分步例题，再做一次带提示的引导练习。'
      : 'Review a worked example, then retry guided practice with hints.',
    repair_with_contrast: zh
      ? '建议：用“正确解法对照”修复错题。'
      : 'Repair the mistakes with a side-by-side contrast.',
  } as const
  return strategy ? labels[strategy] : (zh ? '暂无建议' : 'No suggestion yet')
}


export function executorKind(executor: LearningComponent['executor']): GenerateKind {
  return executor === 'assessment' ? 'quiz' : executor === 'retrieval' ? 'flashcards' : 'courseware'
}
// Components whose output is teachable lesson text an assessment may be
// grounded in. Support components (goal map, calibration, reflection, review,
// retrieval, progress) carry no material prose; using them as "prior lesson"
// would feed the quiz generator an empty string.

export function outputText(result: GenerateSuiteResult): string {
  // ``||`` (not ``??``): every field is a string, and an empty markdown or
  // empty section list must fall through to the next source instead of
  // short-circuiting the whole chain to "" (a goal-map run has markdown "",
  // and "" as material text fails downstream quiz generation instantly).
  const props = result.result.component?.props
  const componentText =
    typeof props?.body_markdown === 'string' && props.body_markdown.trim()
      ? props.body_markdown
      : typeof props?.prompt === 'string' && props.prompt.trim()
        ? props.prompt
        : Array.isArray(props?.milestones)
          ? props.milestones
              .filter((item): item is string => typeof item === 'string' && item.trim() !== '')
              .join('\n')
          : ''
  return (
    result.result.podcast_script ||
    result.result.markdown ||
    result.result.sections
      ?.flatMap(section => section.content ?? [section.core_content ?? ''])
      .filter(Boolean)
      .join('\n') ||
    componentText ||
    result.result.title
  )
}
export async function waitForGeneration(generationId: string): Promise<GenerateSuiteResult> {
  // The backend owns provider retries, fallback routes, and their deadlines.
  // Do not invent a shorter browser timeout: a valid task can take several
  // minutes and remains recoverable from the durable task store.
  for (;;) {
    const result = await getTraitTutorGenerationTask(generationId)
    if ('result' in result) return result
    if (['failed', 'cancelled', 'interrupted'].includes(result.status))
      throw new Error(result.error_code ?? result.error ?? 'generation_failed')
    await new Promise(resolve => window.setTimeout(resolve, 1000))
  }
}
