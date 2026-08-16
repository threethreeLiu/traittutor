import type {
  GenerateSuiteResult,
  LearningComponent,
  recordLearningComponentEvent,
} from '@/lib/traittutor-api'

export type Locale = 'zh' | 'en'
export type ComponentOutput = GenerateSuiteResult | { audioUrl: string; transcript: string }
export type ComponentEvent = Parameters<typeof recordLearningComponentEvent>[3]
export type ComponentEventResult = Awaited<ReturnType<typeof recordLearningComponentEvent>>
export type CalibrationResult = NonNullable<ComponentEventResult['calibration']>
export type LearningStageType = 'mission' | 'learn' | 'try' | 'fix' | 'decide' | 'remember'

export type VisibleAction = {
  actionId: string
  actionType: LearningStageType
  components: LearningComponent[]
  status: 'locked' | 'ready' | 'active' | 'completed'
}
