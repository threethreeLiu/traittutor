'use client'

import dynamic from 'next/dynamic'
import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useParams, usePathname, useRouter } from 'next/navigation'

import {
  Clapperboard,
  Code2,
  Compass,
  FileSearch,
  Globe,
  Image as ImageIcon,
  Lightbulb,
  PenLine,
  Sparkles,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { SelectedRecord } from '@/lib/notebook-selection-types'
import type { SelectedHistorySession } from '@/components/chat/HistorySessionPicker'
import type { SelectedQuestionEntry } from '@/components/chat/QuestionBankPicker'
import type { SelectedLearningArtifactReference } from '@/components/chat/LearningArtifactPicker'
import ChatComposer from '@/components/chat/home/ChatComposer'
import { ChatMessageList } from '@/components/chat/home/ChatMessages'
import SessionLoadingView from '@/components/chat/home/SessionLoadingView'
import { type TraitTutorIconName } from '@/components/brand/TraitTutorIcon'
import LearningHomeIntro, {
  MAX_LEARNING_HOME_FILES,
} from '@/components/chat/home/LearningHomeIntro'
import type { HomePendingAttachment } from '@/components/chat/home/HomeAttachmentTray'
import LearningPathLaunch, {
  type LearningPathState,
} from '@/components/chat/home/LearningPathLaunch'
import {
  LearnRouteDecisionCard,
  LearnWorkspaceStatus,
  MasteryPathPicker,
} from '@/components/learn-home/LearnHomeSurface'
// Imported eagerly so the drawer shell is always mounted off-screen —
// clicking a chip becomes a single CSS class flip, no chunk fetch + double
// render. The heavy renderers inside still load lazily.
import FilePreviewDrawer from '@/components/chat/preview/FilePreviewDrawer'
import Tooltip from '@/components/common/Tooltip'
import { QuizFollowupProvider } from '@/context/QuizFollowupContext'
import { Download } from 'lucide-react'
import { useUnifiedChat, type MessageAttachment } from '@/context/UnifiedChatContext'
import { useAppShell } from '@/context/AppShellContext'
import type { FilePreviewSource } from '@/components/chat/preview/previewerFor'
import type { LLMSelection } from '@/lib/unified-ws'
import { extractBase64FromDataUrl, readFileAsDataUrl } from '@/lib/file-attachments'
import { classifyFile, isSvgFilename } from '@/lib/doc-attachments'
import { useAttachmentLimits } from '@/lib/attachment-limits'
import { useChatAutoScroll } from '@/hooks/useChatAutoScroll'
import { useMeasuredHeight } from '@/hooks/useMeasuredHeight'
import { buildResearchWSConfig } from '@/lib/research-types'
import { listKnowledgeBases } from '@/lib/knowledge-api'
import { createLearningSession, deleteSession, SessionApiError } from '@/lib/session-api'
import { routeLearnIntent, type LearnIntentResult } from '@/lib/learning-intent-api'
import { fetchAllProgress, type ProgressSummary } from '@/lib/learning-api'
import { listLLMOptions, type LLMOption } from '@/lib/llm-options'
import { getEnabledOptionalTools, invalidateEnabledOptionalToolsCache } from '@/lib/tools-settings'
import { downloadChatMarkdown } from '@/lib/chat-export'
import type { ChatMemoryFile } from '@/lib/chat-memory-items'
import { selectedBooksToPayload, type SelectedBookReference } from '@/lib/book-references'
import {
  analyzeTraitTutorMaterial,
  createLearningComponentPlan,
  createLearningPackWithPlan,
  getLearningPackForSession,
  prepareTraitTutorMaterial,
  TraitTutorApiError,
  updateLearningPack,
  type LearningComponentPlan,
} from '@/lib/traittutor-api'
import { normalizeLearningGoal } from '@/lib/learning-goal'
import { getSuppressedHistoricalSessionId } from '@/lib/chat-navigation'
import { notify } from '@/lib/notifications'

const NotebookRecordPicker = dynamic(() => import('@/components/notebook/NotebookRecordPicker'), {
  ssr: false,
})
const HistorySessionPicker = dynamic(() => import('@/components/chat/HistorySessionPicker'), {
  ssr: false,
})
const QuestionBankPicker = dynamic(() => import('@/components/chat/QuestionBankPicker'), {
  ssr: false,
})
const MemoryPicker = dynamic(() => import('@/components/chat/MemoryPicker'), {
  ssr: false,
})
const LearningArtifactPicker = dynamic(() => import('@/components/chat/LearningArtifactPicker'), {
  ssr: false,
})
const BookReferencePicker = dynamic(() => import('@/components/chat/BookReferencePicker'), {
  ssr: false,
})
/* ------------------------------------------------------------------ */
/*  Type & data definitions                                           */
/* ------------------------------------------------------------------ */

type ToolName =
  | 'brainstorm'
  | 'geogebra_analysis'
  | 'web_search'
  | 'code_execution'
  | 'reason'
  | 'paper_search'
  | 'imagegen'
  | 'videogen'

interface ToolDef {
  name: ToolName
  label: string
  icon: LucideIcon
}

const ALL_TOOLS: ToolDef[] = [
  { name: 'brainstorm', label: 'Brainstorm', icon: Lightbulb },
  { name: 'geogebra_analysis', label: 'GeoGebra', icon: Compass },
  { name: 'web_search', label: 'Web Search', icon: Globe },
  { name: 'code_execution', label: 'Code', icon: Code2 },
  { name: 'reason', label: 'Reason', icon: Sparkles },
  { name: 'paper_search', label: 'Arxiv Search', icon: FileSearch },
  { name: 'imagegen', label: 'Image Gen', icon: ImageIcon },
  { name: 'videogen', label: 'Video Gen', icon: Clapperboard },
]

class LearningMaterialFileError extends Error {
  constructor(
    readonly filename: string,
    readonly originalError: unknown
  ) {
    super(`Learning material operation failed for ${filename}`)
    this.name = 'LearningMaterialFileError'
  }
}

class LearningMaterialBatchError extends Error {
  constructor(readonly errors: LearningMaterialFileError[]) {
    super(`Learning material operation failed for ${errors.length} files`)
    this.name = 'LearningMaterialBatchError'
  }
}

async function mapLearningMaterialFiles<T>(
  files: File[],
  operation: (file: File, index: number) => Promise<T>
): Promise<T[]> {
  const results = await Promise.allSettled(
    files.map(async (file, index) => {
      try {
        return await operation(file, index)
      } catch (error) {
        throw new LearningMaterialFileError(file.name, error)
      }
    })
  )
  const errors = results.flatMap(result =>
    result.status === 'rejected' && result.reason instanceof LearningMaterialFileError
      ? [result.reason]
      : []
  )
  if (errors.length === 1) throw errors[0]
  if (errors.length > 1) throw new LearningMaterialBatchError(errors)
  return results.map(result => (result as PromiseFulfilledResult<T>).value)
}

function fileErrorMessage(error: LearningMaterialFileError, detail: string, zh: boolean): string {
  return zh ? `“${error.filename}”：${detail}` : `“${error.filename}”: ${detail}`
}

function learningPathErrorMessage(error: unknown, zh: boolean): string {
  if (error instanceof LearningMaterialBatchError) {
    return error.errors.map(item => learningPathErrorMessage(item, zh)).join(zh ? '；' : '; ')
  }
  if (error instanceof LearningMaterialFileError) {
    return fileErrorMessage(error, learningPathErrorMessage(error.originalError, zh), zh)
  }
  if (!(error instanceof TraitTutorApiError) && !(error instanceof SessionApiError)) {
    return zh
      ? '学习路径暂未建立，请稍后重试。'
      : 'The learning path could not be created. Please try again.'
  }
  const pageLimit = error.message.match(/page_slices.*at most\s+(\d+)\s+pages/i)
  if (pageLimit) {
    return zh
      ? `材料最多支持 ${pageLimit[1]} 页，请拆分文件后重试。`
      : `Materials support up to ${pageLimit[1]} pages. Split the file and try again.`
  }
  if (error.status === 429) {
    return zh
      ? '材料分析请求过于频繁，请稍后重试。'
      : 'Material analysis is temporarily rate limited. Please try again shortly.'
  }
  if (error.status === 409) {
    // 409 is overloaded: the analyze endpoint uses it for
    // GenerationConfigurationError ("no model configured"), while the
    // with-plan/material endpoints use it for idempotency-key and revision
    // conflicts. Distinguish by the server detail instead of assuming.
    const detail = typeof error.detail === 'string' ? error.detail : ''
    if (/idempotency|revision|conflict/i.test(detail)) {
      return zh
        ? '该请求与此前已建立的学习路径冲突，请更换文件或稍后重试。'
        : 'This request conflicts with an earlier learning path. Try a different file or retry later.'
    }
    return zh
      ? '当前未配置可用的生成模型，请先在设置中完成配置。'
      : 'No generation model is configured. Configure one in Settings first.'
  }
  if (error.status === 422) {
    return zh
      ? '材料格式或内容未通过校验，请检查文件后重试。'
      : 'The material did not pass validation. Check the file and try again.'
  }
  if (error.status >= 500) {
    return zh
      ? '学习路径服务暂时不可用，请稍后重试。'
      : 'The learning path service is temporarily unavailable. Please try again later.'
  }
  return zh
    ? '学习路径暂未建立，请重试。'
    : 'The learning path could not be created. Please try again.'
}

function materialPreparationErrorMessage(error: unknown, zh: boolean): string {
  if (error instanceof LearningMaterialBatchError) {
    return error.errors
      .map(item => materialPreparationErrorMessage(item, zh))
      .join(zh ? '；' : '; ')
  }
  if (error instanceof LearningMaterialFileError) {
    return fileErrorMessage(error, materialPreparationErrorMessage(error.originalError, zh), zh)
  }
  if (!(error instanceof TraitTutorApiError)) {
    return zh
      ? '无法读取该材料，请换一份可解析的文件。'
      : 'The material could not be read. Try a supported, readable file.'
  }
  if (error.status === 422) {
    return zh
      ? '材料格式或内容无法解析，请检查文件后重试。'
      : 'The material format or content could not be parsed. Check the file and try again.'
  }
  if (error.status >= 500) {
    return zh
      ? '材料解析服务暂时不可用，请稍后重试。'
      : 'The material parsing service is temporarily unavailable. Please try again later.'
  }
  return learningPathErrorMessage(error, zh)
}

function fileReadErrorMessage(error: unknown, zh: boolean): string {
  if (error instanceof LearningMaterialBatchError) {
    return error.errors.map(item => fileReadErrorMessage(item, zh)).join(zh ? '；' : '; ')
  }
  if (error instanceof LearningMaterialFileError) {
    return zh
      ? `无法读取“${error.filename}”，请重新选择该文件。`
      : `“${error.filename}” could not be read. Select the file again.`
  }
  return zh
    ? '无法读取所选文件，请重新选择。'
    : 'The selected file could not be read. Select it again.'
}

function sessionCreationErrorMessage(error: unknown, zh: boolean): string {
  if (error instanceof SessionApiError && !error.message.startsWith('Request failed:')) {
    return zh
      ? `无法保存学习会话：${error.message}`
      : `The learning session could not be saved: ${error.message}`
  }
  return zh
    ? '无法保存学习会话，请稍后重试。'
    : 'The learning session could not be saved. Please try again.'
}

interface ReservedLearningSession {
  sessionId: string
  created: boolean
}

interface CapabilityDef {
  value: string
  label: string
  description: string
  icon: TraitTutorIconName
  allowedTools: ToolName[]
  defaultTools: ToolName[]
  // Loop-engine capabilities run on the chat agent loop rather
  // than a bespoke pipeline. They are collapsed into the "More" flyout in the
  // capability picker instead of listed directly. Driven by the loop-capability
  // registry on the backend; mirrored here as a static flag.
  loopEngine?: boolean
}

const CAPABILITIES: CapabilityDef[] = [
  {
    value: '',
    label: 'Chat',
    description: 'Flexible conversation with any tool',
    icon: 'chat',
    allowedTools: [
      'brainstorm',
      'geogebra_analysis',
      'web_search',
      'code_execution',
      'reason',
      'paper_search',
      'imagegen',
      'videogen',
    ],
    defaultTools: [],
  },
  {
    value: 'mastery_path',
    label: 'Mastery practice',
    description: 'Practise one confirmed learning path with evidence gates',
    icon: 'mastery',
    allowedTools: [],
    defaultTools: [],
    loopEngine: true,
  },
]

interface KnowledgeBase {
  name: string
  is_default?: boolean
  metadata?: {
    /** Connected-source kind, e.g. "obsidian". */
    type?: string
  }
}

type ChatGenerationKind = 'solve' | 'learning_exploration' | 'knowledge_diagram' | 'humanizer'

type PendingAttachment = HomePendingAttachment

type LearningPlanTarget = {
  goal: string
  packId: string
  plan: LearningComponentPlan
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

function getCapability(value: string | null): CapabilityDef {
  return CAPABILITIES.find(c => c.value === (value || '')) ?? CAPABILITIES[0]
}

function attachmentIdentity(
  attachment: Pick<PendingAttachment, 'filename' | 'size' | 'mimeType'>
): string {
  return `${attachment.filename}:${attachment.size ?? 0}:${attachment.mimeType ?? ''}`
}

function fileIdentity(file: File): string {
  return `${file.name}:${file.size}:${file.type}`
}

function mergeUniqueAttachments(
  current: PendingAttachment[],
  incoming: PendingAttachment[]
): PendingAttachment[] {
  const seen = new Set(current.map(attachmentIdentity))
  const uniqueIncoming = incoming.filter(attachment => {
    const key = attachmentIdentity(attachment)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
  return [...current, ...uniqueIncoming]
}

function learningMaterialSafetyExcerpt(
  materials: Array<Awaited<ReturnType<typeof prepareTraitTutorMaterial>>>
): string {
  return materials
    .flatMap(material => material.metadata.page_slices ?? [])
    .map(slice => slice.text)
    .join('\n')
    .slice(0, 240_000)
}

type PreparedLearningMaterial = Awaited<ReturnType<typeof prepareTraitTutorMaterial>>

/**
 * Preserve every source in a multi-file Learn request.  The primary material
 * remains compatible with existing generators, while `source_materials` keeps
 * the individual texts, page evidence, and analyses available to the Pack
 * rather than silently reducing a bundle to its first file.
 */
function buildLearningBundle(
  materials: PreparedLearningMaterial[],
  analyses: Awaited<ReturnType<typeof analyzeTraitTutorMaterial>>[],
  sessionId: string | null
): PreparedLearningMaterial {
  const primary = materials[0]
  if (!primary) throw new Error('learning material unavailable')
  const sourceMaterials = materials.map((item, index) => ({
    source_id: item.source_id ?? null,
    source_type: item.source_type,
    title: item.title,
    text: item.text,
    metadata: item.metadata,
    learner_analysis: analyses[index] ?? null,
  }))
  const allEvidence = analyses.flatMap(
    analysis => analysis.page_evidence ?? analysis.evidence ?? []
  )
  const allConcepts = analyses.flatMap(analysis => analysis.concept_candidates ?? [])
  // Generators accept one MaterialSource. Include an attributable excerpt from
  // every upload there; the full individual texts remain in source_materials.
  const textBudgetPerSource = Math.max(1, Math.floor(220_000 / materials.length))
  const combinedText = materials
    .map(item => `\n\n--- ${item.title} ---\n${item.text.slice(0, textBudgetPerSource)}`)
    .join('')
    .slice(0, 240_000)
  const primaryAnalysis = analyses[0] ?? null
  return {
    ...primary,
    title: materials.length === 1 ? primary.title : materials.map(item => item.title).join(' · '),
    text: combinedText || primary.text,
    metadata: {
      ...primary.metadata,
      learning_session_id: sessionId,
      source_materials: sourceMaterials,
      learner_analyses: analyses,
      learner_analysis: primaryAnalysis
        ? {
            ...primaryAnalysis,
            evidence: allEvidence,
            page_evidence: allEvidence,
            concept_candidates: allConcepts,
          }
        : primary.metadata.learner_analysis,
    },
  }
}

/* ------------------------------------------------------------------ */
/*  Chat page                                                         */
/* ------------------------------------------------------------------ */

export default function ChatPage() {
  const router = useRouter()
  const pathname = usePathname()
  const params = useParams<{ sessionId?: string[] }>()
  const { t } = useTranslation()
  const sessionIdParam = params.sessionId?.[0] ?? null
  const isAssistPage = pathname.startsWith('/assist')
  const chatRoot = isAssistPage ? '/assist' : '/home'
  const { setActiveSessionId, language: appLanguage } = useAppShell()

  const {
    state,
    setTools,
    setCapability,
    setKBs,
    setLLMSelection,
    sendMessage,
    cancelStreamingTurn,
    submitUserReply,
    regenerateLastMessage,
    deleteTurn,
    editMessage,
    switchBranch,
    newSession,
    loadSession,
    renameSessionTitle,
  } = useUnifiedChat()

  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [llmOptions, setLLMOptions] = useState<LLMOption[]>([])
  const [activeLLMDefault, setActiveLLMDefault] = useState<LLMSelection | null>(null)
  const [llmOptionsLoading, setLLMOptionsLoading] = useState(true)
  const [llmOptionsError, setLLMOptionsError] = useState(false)
  // User-toggleable tools the user has enabled in /settings/tools. This is
  // the single source of truth for which optional tools the chat agent may
  // use; the chat composer no longer exposes a picker.
  const [userEnabledTools, setUserEnabledTools] = useState<string[] | null>(null)
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const [chatGenerationKind, setChatGenerationKind] = useState<ChatGenerationKind | null>(null)
  const attachmentLimits = useAttachmentLimits()
  const [dragging, setDragging] = useState(false)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [previewSource, setPreviewSource] = useState<FilePreviewSource | null>(null)
  const attachmentErrorTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [capMenuOpen, setCapMenuOpen] = useState(false)
  const [showNotebookPicker, setShowNotebookPicker] = useState(false)
  const [showBookPicker, setShowBookPicker] = useState(false)
  const [showHistoryPicker, setShowHistoryPicker] = useState(false)
  const [showQuestionBankPicker, setShowQuestionBankPicker] = useState(false)
  const [showMemoryPicker, setShowMemoryPicker] = useState(false)
  const [showLearningArtifactPicker, setShowLearningArtifactPicker] = useState(false)
  const [learningPath, setLearningPath] = useState<LearningPathState | null>(null)
  const [learningPathError, setLearningPathError] = useState<string | null>(null)
  const [learnRouteDecision, setLearnRouteDecision] = useState<{
    content: string
    result: LearnIntentResult
  } | null>(null)
  // Browser File objects and prepared excerpts are held only for the current
  // Learn draft. They are never put into session state or sent to the intent
  // classifier as attachment instructions.
  const learningAttachmentFilesRef = useRef<File[]>([])
  const preparedLearningMaterialsRef = useRef<
    Awaited<ReturnType<typeof prepareTraitTutorMaterial>>[]
  >([])
  const activeLearningPlanRef = useRef<{ packId: string; planId: string } | null>(null)
  const activeLearningMaterialRef = useRef<Record<string, unknown> | null>(null)
  const activeLearningSessionRef = useRef<string | null>(null)
  const restoredLearningSessionRef = useRef<string | null>(null)
  const latestLearningPlanRef = useRef<LearningPlanTarget | null>(null)
  const pendingLearningPlanRef = useRef<Promise<LearningPlanTarget> | null>(null)
  const initialPackCreateRef = useRef<{ signature: string; key: string } | null>(null)
  const learningPlanEpochRef = useRef(0)
  const learningLaunchRef = useRef<Promise<void> | null>(null)
  const [learningLaunchPending, setLearningLaunchPending] = useState(false)
  const [attachmentReceipt, setAttachmentReceipt] = useState<{ filenames: string[] } | null>(null)
  const [referenceMenuOpen, setReferenceMenuOpen] = useState(false)

  const [assistantCapabilityError, setAssistantCapabilityError] = useState<string | null>(null)
  // The browser may select only an already-persisted path ID.  Subject/KC
  // attribution is deliberately absent here and is minted by the runtime.
  const [masteryPaths, setMasteryPaths] = useState<ProgressSummary[]>([])
  const [masteryPathsState, setMasteryPathsState] = useState<
    'idle' | 'loading' | 'ready' | 'error'
  >('idle')
  const [selectedMasteryPathId, setSelectedMasteryPathId] = useState('')
  const [selectedNotebookRecords, setSelectedNotebookRecords] = useState<SelectedRecord[]>([])
  const [selectedBookReferences, setSelectedBookReferences] = useState<SelectedBookReference[]>([])
  const [selectedHistorySessions, setSelectedHistorySessions] = useState<SelectedHistorySession[]>(
    []
  )
  const [selectedQuestionEntries, setSelectedQuestionEntries] = useState<SelectedQuestionEntry[]>(
    []
  )
  const [selectedMemoryFiles, setSelectedMemoryFiles] = useState<ChatMemoryFile[]>([])
  const [selectedLearningArtifacts, setSelectedLearningArtifacts] = useState<
    SelectedLearningArtifactReference[]
  >([])
  const dragCounter = useRef(0)
  const capMenuRef = useRef<HTMLDivElement>(null)
  const capBtnRef = useRef<HTMLButtonElement>(null)
  const referenceMenuRef = useRef<HTMLDivElement>(null)
  const referenceButtonRef = useRef<HTMLButtonElement>(null)
  const initialLoadRef = useRef(false)
  // Session-loading overlay: shown while navigating from chat-history →
  // session detail. Holds an AbortController so the user can cancel.
  const [sessionLoading, setSessionLoading] = useState(false)
  const loadAbortRef = useRef<AbortController | null>(null)
  // Bridge ref: ``ChatComposer`` writes a prefill function into this on
  // mount; ``ChatMessageList`` uses it so an ``AskUserOptions`` chip click can
  // drop text into the composer textarea.
  const prefillInputRef = useRef<((text: string) => void) | null>(null)

  const activeCap = useMemo(() => getCapability(state.activeCapability), [state.activeCapability])
  const isResearchMode = state.activeCapability === 'deep_research'
  const masteryPickerVisible = !isAssistPage && activeCap.value === 'mastery_path'
  const selectableMasteryPaths = useMemo(
    () => masteryPaths.filter(path => path.mastery_ready === true),
    [masteryPaths]
  )

  const hasMessages = state.messages.length > 0
  const isZh = (appLanguage || state.language || 'en').toLowerCase().startsWith('zh')
  const productMode = isAssistPage ? 'assist' : 'learn'
  useEffect(() => {
    if (!masteryPickerVisible) return
    let cancelled = false
    setMasteryPathsState('loading')
    void fetchAllProgress()
      .then(result => {
        if (cancelled) return
        const paths = result.summaries.filter(
          item => typeof item.book_id === 'string' && item.book_id.trim()
        )
        setMasteryPaths(paths)
        setSelectedMasteryPathId(current =>
          paths.some(item => item.book_id === current && item.mastery_ready)
            ? current
            : (paths.find(item => item.mastery_ready)?.book_id ?? '')
        )
        setMasteryPathsState('ready')
      })
      .catch(() => {
        if (cancelled) return
        setMasteryPaths([])
        setSelectedMasteryPathId('')
        setMasteryPathsState('error')
      })
    return () => {
      cancelled = true
    }
  }, [masteryPickerVisible])
  const firstUserTitle = useMemo(
    () =>
      state.messages
        .find(msg => msg.role === 'user')
        ?.content.trim()
        .replace(/\s+/g, ' ')
        .slice(0, 80) || '',
    [state.messages]
  )
  const persistedSessionTitle = state.sessionTitle.trim()
  // The backend's empty-session sentinel is English. Never surface it as a
  // user-facing title; use the locale label until a real title is available.
  const hasPlaceholderSessionTitle =
    persistedSessionTitle === 'New conversation' || persistedSessionTitle === 'New chat'
  const displaySessionTitle =
    (hasPlaceholderSessionTitle ? '' : persistedSessionTitle) || firstUserTitle || t('New chat')
  const canRenameSession = Boolean(state.sessionId)
  const titleInputRef = useRef<HTMLInputElement | null>(null)
  const skipTitleCommitRef = useRef(false)
  const [sessionTitleDraft, setSessionTitleDraft] = useState(displaySessionTitle)
  const [sessionTitleEditing, setSessionTitleEditing] = useState(false)
  const [sessionTitleSaving, setSessionTitleSaving] = useState(false)
  const [sessionTitleError, setSessionTitleError] = useState<string | null>(null)
  useEffect(() => {
    if (sessionTitleEditing) return
    setSessionTitleDraft(displaySessionTitle)
  }, [displaySessionTitle, sessionTitleEditing])
  useEffect(() => {
    if (!sessionTitleEditing) return
    window.requestAnimationFrame(() => {
      titleInputRef.current?.focus()
      titleInputRef.current?.select()
    })
  }, [sessionTitleEditing])
  const startSessionTitleEdit = useCallback(() => {
    if (!canRenameSession) return
    skipTitleCommitRef.current = false
    setSessionTitleError(null)
    setSessionTitleDraft(displaySessionTitle)
    setSessionTitleEditing(true)
  }, [canRenameSession, displaySessionTitle])
  const cancelSessionTitleEdit = useCallback(() => {
    skipTitleCommitRef.current = true
    setSessionTitleDraft(displaySessionTitle)
    setSessionTitleError(null)
    setSessionTitleEditing(false)
  }, [displaySessionTitle])
  const commitSessionTitleEdit = useCallback(async () => {
    if (skipTitleCommitRef.current) {
      skipTitleCommitRef.current = false
      return
    }
    const next = sessionTitleDraft.trim()
    if (!next) {
      setSessionTitleDraft(displaySessionTitle)
      setSessionTitleEditing(false)
      return
    }
    if (!canRenameSession || next === persistedSessionTitle) {
      setSessionTitleDraft(next || displaySessionTitle)
      setSessionTitleEditing(false)
      return
    }
    setSessionTitleSaving(true)
    setSessionTitleError(null)
    try {
      await renameSessionTitle(next)
      setSessionTitleEditing(false)
    } catch (error) {
      console.error('Failed to rename session:', error)
      setSessionTitleError(t('Rename failed'))
      titleInputRef.current?.focus()
    } finally {
      setSessionTitleSaving(false)
    }
  }, [
    canRenameSession,
    displaySessionTitle,
    persistedSessionTitle,
    renameSessionTitle,
    sessionTitleDraft,
    t,
  ])
  const handleSessionTitleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Enter') {
        event.preventDefault()
        void commitSessionTitleEdit()
      } else if (event.key === 'Escape') {
        event.preventDefault()
        cancelSessionTitleEdit()
      }
    },
    [cancelSessionTitleEdit, commitSessionTitleEdit]
  )
  const { ref: composerRef, height: composerHeight } = useMeasuredHeight<HTMLDivElement>()
  const notebookReferenceGroups = useMemo(() => {
    const groups = new Map<string, { notebookName: string; count: number }>()
    selectedNotebookRecords.forEach(record => {
      const existing = groups.get(record.notebookId)
      if (existing) {
        existing.count += 1
      } else {
        groups.set(record.notebookId, {
          notebookName: record.notebookName,
          count: 1,
        })
      }
    })
    return Array.from(groups.entries()).map(([notebookId, value]) => ({
      notebookId,
      ...value,
    }))
  }, [selectedNotebookRecords])
  const notebookReferencesPayload = useMemo(() => {
    const grouped = new Map<string, string[]>()
    selectedNotebookRecords.forEach(record => {
      const current = grouped.get(record.notebookId) || []
      current.push(record.id)
      grouped.set(record.notebookId, current)
    })
    return Array.from(grouped.entries()).map(([notebook_id, record_ids]) => ({
      notebook_id,
      record_ids,
    }))
  }, [selectedNotebookRecords])
  const bookReferencesPayload = useMemo(
    () => selectedBooksToPayload(selectedBookReferences),
    [selectedBookReferences]
  )
  const historyReferencesPayload = useMemo(
    () => selectedHistorySessions.map(session => session.sessionId),
    [selectedHistorySessions]
  )
  const questionNotebookReferencesPayload = useMemo(
    () => selectedQuestionEntries.map(entry => entry.id),
    [selectedQuestionEntries]
  )
  const memoryReferencesPayload = useMemo(() => [...selectedMemoryFiles], [selectedMemoryFiles])
  const lastMessage = state.messages[state.messages.length - 1]
  const {
    containerRef: messagesContainerRef,
    endRef: messagesEndRef,
    shouldAutoScrollRef,
    handleScroll: handleMessagesScroll,
  } = useChatAutoScroll({
    hasMessages,
    isStreaming: state.isStreaming,
    composerHeight,
    messageCount: state.messages.length,
    lastMessageContent: lastMessage?.content,
    lastEventCount: lastMessage?.events?.length,
  })
  const copyAssistantMessage = useCallback(async (content: string) => {
    if (!content.trim()) return
    try {
      await navigator.clipboard.writeText(content)
    } catch (error) {
      console.error('Failed to copy assistant message:', error)
    }
  }, [])
  /* ---- URL-driven session loading ---- */

  const navigateToHome = useCallback(() => {
    router.replace(chatRoot, { scroll: false })
  }, [chatRoot, router])

  /** Abort in-flight load + navigate home. */
  const cancelSessionLoad = useCallback(() => {
    loadAbortRef.current?.abort()
    loadAbortRef.current = null
    setSessionLoading(false)
    navigateToHome()
  }, [navigateToHome])

  /**
   * Shared helper: kick off a load. The user can cancel via the ✕ button;
   * otherwise the loading overlay stays until the API responds (no timeout).
   */
  const startSessionLoad = useCallback(
    (sid: string) => {
      loadAbortRef.current?.abort()
      const ctrl = new AbortController()
      loadAbortRef.current = ctrl
      setSessionLoading(true)

      void loadSession(sid, ctrl.signal)
        .then(() => {
          if (!ctrl.signal.aborted) {
            loadAbortRef.current = null
            setSessionLoading(false)
          }
        })
        .catch(() => {
          if (!ctrl.signal.aborted) {
            loadAbortRef.current = null
            setSessionLoading(false)
            navigateToHome()
          }
        })
    },
    [loadSession, navigateToHome]
  )

  // Initial mount — load the session from the URL.
  // Uses a ref-based flag so Strict Mode double-mount doesn't break the flow:
  // when React tears down + re-mounts in dev, we reset initialLoadRef in
  // cleanup so the second mount restarts the load cleanly. The abort is
  // deliberately OMITTED from cleanup — cancelSessionLoad handles
  // user-initiated cancellation.
  useEffect(() => {
    if (initialLoadRef.current) return
    initialLoadRef.current = true
    if (sessionIdParam) {
      startSessionLoad(sessionIdParam)
    } else {
      newSession()
    }
    return () => {
      initialLoadRef.current = false
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // When URL param changes (sidebar navigation), load the corresponding session
  const prevSessionIdParam = useRef(sessionIdParam)
  useEffect(() => {
    if (sessionIdParam === prevSessionIdParam.current) return
    prevSessionIdParam.current = sessionIdParam
    // Abort any in-flight session load from the previous param
    loadAbortRef.current?.abort()
    loadAbortRef.current = null
    if (sessionIdParam) {
      if (sessionIdParam === state.sessionId) {
        setSessionLoading(false)
        return
      }
      activeLearningPlanRef.current = null
      activeLearningMaterialRef.current = null
      activeLearningSessionRef.current = null
      latestLearningPlanRef.current = null
      pendingLearningPlanRef.current = null
      learningPlanEpochRef.current += 1
      setLearningPath(null)
      startSessionLoad(sessionIdParam)
    } else {
      activeLearningPlanRef.current = null
      activeLearningMaterialRef.current = null
      activeLearningSessionRef.current = null
      latestLearningPlanRef.current = null
      pendingLearningPlanRef.current = null
      learningPlanEpochRef.current += 1
      setLearningPath(null)
      newSession()
      setSessionLoading(false)
    }
  }, [sessionIdParam, startSessionLoad, newSession, state.sessionId])

  // When a new session_id is assigned by the server, update the URL
  useEffect(() => {
    if (!state.sessionId || sessionIdParam) return
    if (state.sessionId === getSuppressedHistoricalSessionId()) return
    if (state.sessionId && !sessionIdParam) {
      router.replace(`${chatRoot}/${state.sessionId}`, { scroll: false })
    }
  }, [chatRoot, state.sessionId, sessionIdParam, router])

  useEffect(() => {
    if (!isAssistPage) return
    activeLearningPlanRef.current = null
    activeLearningMaterialRef.current = null
    activeLearningSessionRef.current = null
    latestLearningPlanRef.current = null
    pendingLearningPlanRef.current = null
    learningPlanEpochRef.current += 1
    setLearningPath(null)
  }, [isAssistPage])

  useEffect(() => {
    setActiveSessionId(state.sessionId || sessionIdParam || null)
  }, [state.sessionId, sessionIdParam, setActiveSessionId])

  // A Learn conversation can be refreshed or reopened days later.  The Pack is
  // the durable source of its goal and active plan; hydrate the page state from
  // the explicit session link instead of relying on transient React state.
  useEffect(() => {
    if (isAssistPage || !state.sessionId || restoredLearningSessionRef.current === state.sessionId)
      return
    restoredLearningSessionRef.current = state.sessionId
    let cancelled = false
    void getLearningPackForSession(state.sessionId)
      .then(linked => {
        if (cancelled) return
        if (!linked) return
        const plan =
          linked.component_plans?.find(item => item.plan_id === linked.active_plan_id) ??
          linked.component_plans?.find(item => item.status === 'active') ??
          null
        const goal = linked.goal?.text?.trim() || linked.title
        activeLearningMaterialRef.current = linked.material
        activeLearningSessionRef.current = state.sessionId
        latestLearningPlanRef.current = plan ? { goal, packId: linked.pack_id, plan } : null
        activeLearningPlanRef.current = plan
          ? { packId: linked.pack_id, planId: plan.plan_id }
          : null
        setLearningPath({ goal, packId: linked.pack_id, plan, status: plan ? 'ready' : 'error' })
      })
      .catch(() => {
        // Keep Learn usable if a historical Pack has been removed.
      })
    return () => {
      cancelled = true
    }
  }, [isAssistPage, state.sessionId])

  const refreshKnowledgeBases = useCallback(async (options?: { force?: boolean }) => {
    try {
      const list = await listKnowledgeBases({ force: options?.force })
      setKnowledgeBases(list)
    } catch {
      setKnowledgeBases([])
    }
  }, [])

  /* Load KBs */
  useEffect(() => {
    void refreshKnowledgeBases({ force: true })
  }, [refreshKnowledgeBases])

  const refreshUserEnabledTools = useCallback(async (options?: { force?: boolean }) => {
    try {
      const list = await getEnabledOptionalTools({ force: options?.force })
      setUserEnabledTools(list)
    } catch {
      setUserEnabledTools([])
    }
  }, [])

  /* Load user tool prefs */
  useEffect(() => {
    void refreshUserEnabledTools({ force: true })
  }, [refreshUserEnabledTools])

  const refreshLLMOptions = useCallback(async () => {
    setLLMOptionsLoading(true)
    try {
      const payload = await listLLMOptions()
      setLLMOptions(payload.options)
      setActiveLLMDefault(payload.active)
      setLLMOptionsError(false)
    } catch {
      setLLMOptionsError(true)
      setLLMOptions([])
      setActiveLLMDefault(null)
    } finally {
      setLLMOptionsLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshLLMOptions()
  }, [refreshLLMOptions])

  useEffect(() => {
    if (state.llmSelection || !activeLLMDefault) return
    setLLMSelection(activeLLMDefault)
  }, [activeLLMDefault, setLLMSelection, state.llmSelection])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const refresh = () => {
      void refreshKnowledgeBases({ force: true })
      void refreshLLMOptions()
      // Picks up toggles the user changed in another tab (/settings/tools).
      invalidateEnabledOptionalToolsCache()
      void refreshUserEnabledTools({ force: true })
    }
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    window.addEventListener('focus', refresh)
    window.addEventListener('pageshow', refresh)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => {
      window.removeEventListener('focus', refresh)
      window.removeEventListener('pageshow', refresh)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [refreshKnowledgeBases, refreshLLMOptions, refreshUserEnabledTools])

  /* URL query params (capability, tool) */
  useEffect(() => {
    if (typeof window === 'undefined') return
    const p = new URLSearchParams(window.location.search)
    const qc = p.get('capability')
    const qt = p.getAll('tool')
    if (qc !== null) handleSelectCapability(qc || '')
    else if (qt.length) {
      const valid = qt.filter((t): t is ToolName => ALL_TOOLS.some(d => d.name === t))
      if (valid.length) setTools(Array.from(new Set(valid)))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (
        capMenuRef.current &&
        !capMenuRef.current.contains(t) &&
        capBtnRef.current &&
        !capBtnRef.current.contains(t)
      )
        setCapMenuOpen(false)
      if (
        referenceMenuRef.current &&
        !referenceMenuRef.current.contains(t) &&
        referenceButtonRef.current &&
        !referenceButtonRef.current.contains(t)
      )
        setReferenceMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // Keep state.enabledTools = (user's toggleable set) ∩ (capability's allowed
  // set). Re-runs when the user flips a toggle in /settings/tools or when
  // the active capability changes. The composer no longer owns this — the
  // /settings/tools page is the single switchboard.
  useEffect(() => {
    if (userEnabledTools === null) return
    const allowed = new Set(activeCap.allowedTools)
    const next = userEnabledTools.filter(tool => allowed.has(tool as ToolName))
    const current = state.enabledTools
    const same = current.length === next.length && current.every((tool, idx) => tool === next[idx])
    if (!same) setTools(next)
  }, [activeCap.allowedTools, setTools, state.enabledTools, userEnabledTools])

  /* ---- handlers ---- */

  const handleSelectCapability = useCallback(
    (value: string) => {
      const cap = CAPABILITIES.find(c => c.value === value) ?? CAPABILITIES[0]
      setCapability(cap.value || null)
      setChatGenerationKind(null)
      // Per-capability tool selection now derives from the user's saved
      // settings (/settings/tools) intersected with the capability's
      // allow-list. Playground-saved configs still override when the user
      // explicitly pinned tools in the playground for this capability.
      const baseline = userEnabledTools === null ? cap.allowedTools : userEnabledTools
      const enabledToolsForCap = baseline.filter(tool =>
        cap.allowedTools.includes(tool as ToolName)
      )
      setTools(enabledToolsForCap)
      setCapMenuOpen(false)
    },
    [setCapability, setTools, userEnabledTools]
  )

  const handleSelectGenerationShortcut = useCallback(
    (kind: ChatGenerationKind) => {
      setCapMenuOpen(false)
      setCapability(null)
      setChatGenerationKind(kind)
    },
    [setCapability]
  )

  const fileToAttachment = useCallback(
    (f: File): Promise<PendingAttachment> =>
      new Promise((resolve, reject) => {
        readFileAsDataUrl(f)
          .then(raw => {
            // SVG: treat as file (text extraction on server, vision models
            // reject SVG) but keep the data URL so the chip can render a
            // thumbnail via a raw <img> tag.
            const svg = isSvgFilename(f.name) || f.type === 'image/svg+xml'
            const isImage = !svg && f.type.startsWith('image/')
            const b64 = extractBase64FromDataUrl(raw)
            resolve({
              type: isImage ? 'image' : 'file',
              filename: f.name,
              base64: b64,
              previewUrl: isImage || svg ? raw : undefined,
              size: f.size,
              mimeType: f.type || undefined,
            })
          })
          .catch(reject)
      }),
    []
  )

  const showAttachmentError = useCallback((message: string) => {
    setAttachmentError(message)
    notify(message, { tone: 'error' })
    if (attachmentErrorTimer.current) {
      clearTimeout(attachmentErrorTimer.current)
    }
    attachmentErrorTimer.current = setTimeout(() => {
      setAttachmentError(null)
      attachmentErrorTimer.current = null
    }, 4000)
  }, [])

  const showLearningPathError = useCallback(
    (error: unknown, message?: string) => {
      const resolved = message ?? learningPathErrorMessage(error, isZh)
      setLearningPathError(resolved)
      showAttachmentError(resolved)
    },
    [isZh, showAttachmentError]
  )

  const filterAndReportFiles = useCallback(
    (files: File[], maxFiles = Number.POSITIVE_INFINITY): File[] => {
      let runningTotal = attachments.reduce((s, a) => s + (a.size ?? 0), 0)
      const accepted: File[] = []
      const seen = new Set(attachments.map(attachmentIdentity))
      const rejected: {
        name: string
        reason: 'unsupported' | 'too_large' | 'quota' | 'duplicate' | 'count'
      }[] = []
      for (const f of files) {
        const key = fileIdentity(f)
        if (seen.has(key)) {
          rejected.push({ name: f.name, reason: 'duplicate' })
          continue
        }
        const kind = classifyFile(f)
        if (!kind) {
          rejected.push({ name: f.name, reason: 'unsupported' })
          continue
        }
        if (f.size > attachmentLimits.maxFileBytes) {
          rejected.push({ name: f.name, reason: 'too_large' })
          continue
        }
        if (attachments.length + accepted.length >= maxFiles) {
          rejected.push({ name: f.name, reason: 'count' })
          continue
        }
        if (runningTotal + f.size > attachmentLimits.maxTotalBytes) {
          rejected.push({ name: f.name, reason: 'quota' })
          break
        }
        runningTotal += f.size
        seen.add(key)
        accepted.push(f)
      }
      if (rejected.length) {
        const first = rejected[0]
        let msg: string
        if (first.reason === 'too_large') {
          msg = t('File too large: {{name}}', { name: first.name })
        } else if (first.reason === 'quota') {
          msg = t('Too many files, skipped some')
        } else if (first.reason === 'duplicate') {
          msg = isZh ? `文件已添加：${first.name}` : `File already attached: ${first.name}`
        } else if (first.reason === 'count') {
          msg = isZh
            ? `每条学习路径最多添加 ${maxFiles} 个文件。`
            : `Add up to ${maxFiles} files to each learning path.`
        } else {
          msg = t('Unsupported file type: {{name}}', { name: first.name })
        }
        showAttachmentError(msg)
      }
      return accepted
    },
    [attachments, attachmentLimits, isZh, showAttachmentError, t]
  )

  const removeAttachment = useCallback(
    (index: number) => {
      setAttachments(prev => prev.filter((_, itemIndex) => itemIndex !== index))
      learningAttachmentFilesRef.current = learningAttachmentFilesRef.current.filter(
        (_, itemIndex) => itemIndex !== index
      )
      preparedLearningMaterialsRef.current = preparedLearningMaterialsRef.current.filter(
        (_, itemIndex) => itemIndex !== index
      )
      if (attachments.length === 1) {
        learningPlanEpochRef.current += 1
        pendingLearningPlanRef.current = null
        latestLearningPlanRef.current = null
        activeLearningPlanRef.current = null
        setAttachmentReceipt(null)
        setLearningPath(null)
        setLearningPathError(null)
      }
    },
    [attachments.length]
  )

  const handlePreviewPendingAttachment = useCallback(
    (index: number) => {
      const a = attachments[index]
      if (!a) return
      setPreviewSource({
        filename: a.filename,
        mimeType: a.mimeType,
        type: a.type,
        base64: a.base64,
        size: a.size,
      })
    },
    [attachments]
  )

  const handlePreviewMessageAttachment = useCallback(
    (a: MessageAttachment) => {
      setPreviewSource({
        filename: a.filename || t('Attachment'),
        mimeType: a.mime_type,
        type: a.type,
        base64: a.base64,
        url: a.url,
        extractedText: a.extracted_text,
        size: a.size_bytes,
        id: a.id,
      })
    },
    [t]
  )

  const handleClosePreview = useCallback(() => {
    setPreviewSource(null)
  }, [])

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current += 1
    if (e.dataTransfer.types.includes('Files')) setDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current -= 1
    if (dragCounter.current === 0) setDragging(false)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const createAttachmentLearningPlan = useCallback(
    async (
      files: File[],
      goal: string,
      preparedMaterials: Awaited<ReturnType<typeof prepareTraitTutorMaterial>>[] = [],
      learningSessionId = state.sessionId
    ): Promise<LearningPlanTarget> => {
      const first = files[0]
      if (!first) throw new Error('learning material unavailable')
      const prepared = preparedMaterials.length
        ? preparedMaterials
        : await mapLearningMaterialFiles(files, file => prepareTraitTutorMaterial(file))
      // Every uploaded source is independently analyzed.  This avoids using
      // only the first document as the Pack's evidence base when a learner
      // uploads a reading bundle.
      const analysisSessionId = learningSessionId || `home-${crypto.randomUUID()}`
      const analyses = await mapLearningMaterialFiles(files, (_file, index) => {
        const item = prepared[index]
        if (!item) throw new Error('prepared learning material unavailable')
        return analyzeTraitTutorMaterial({
          session_id: analysisSessionId,
          material: item,
        })
      })
      const material = buildLearningBundle(prepared, analyses, learningSessionId)
      activeLearningMaterialRef.current = material
      const signature = `${files.map(fileIdentity).join('|')}::${goal}`
      if (initialPackCreateRef.current?.signature !== signature) {
        initialPackCreateRef.current = {
          signature,
          key: `home-pack-${crypto.randomUUID()}`,
        }
      }
      const { pack, plan } = await createLearningPackWithPlan({
        idempotency_key: initialPackCreateRef.current.key,
        title: files.map(file => file.name).join(' · '),
        goal: { text: goal, status: 'active', origin: 'home_upload' },
        material,
        sources: prepared.map((item, index) => ({
          source_type: 'upload',
          source_id: item.source_id ?? null,
          title: files[index]?.name ?? item.title,
          role: 'material',
          analysis_id: analyses[index]?.analysis_id ?? null,
        })),
        plan: { instruction: goal },
      })
      return { goal, packId: pack.pack_id, plan }
    },
    [state.sessionId]
  )

  const ensureLearningSession = useCallback(
    async (title: string): Promise<ReservedLearningSession> => {
      if (state.sessionId) return { sessionId: state.sessionId, created: false }
      const session = await createLearningSession(title)
      const sessionId = session.session_id
      if (!sessionId) throw new Error('learning session unavailable')
      return { sessionId, created: true }
    },
    [state.sessionId]
  )

  const cleanupCreatedLearningSession = useCallback(
    async (reservation: ReservedLearningSession | null): Promise<boolean> => {
      if (!reservation?.created) return true
      try {
        const linkedPack = await getLearningPackForSession(reservation.sessionId)
        if (linkedPack) return true
      } catch {
        // An uncertain lookup must not delete a session that may already own
        // a committed Pack. Keep it recoverable and surface the cleanup issue.
        return false
      }
      try {
        await deleteSession(reservation.sessionId)
        if (activeLearningSessionRef.current === reservation.sessionId) {
          activeLearningSessionRef.current = null
        }
        return true
      } catch {
        return false
      }
    },
    []
  )

  const handleAddFiles = useCallback(
    async (files: File[]) => {
      const accepted = filterAndReportFiles(
        files,
        isAssistPage ? Number.POSITIVE_INFINITY : MAX_LEARNING_HOME_FILES
      )
      if (!accepted.length) return
      if (isAssistPage) {
        setAttachmentReceipt({ filenames: accepted.map(file => file.name) })
        let next: PendingAttachment[]
        try {
          next = await mapLearningMaterialFiles(accepted, file => fileToAttachment(file))
        } catch (error) {
          showAttachmentError(fileReadErrorMessage(error, isZh))
          return
        }
        setAttachments(prev => mergeUniqueAttachments(prev, next))
        return
      }
      setLearningPath(current => (current?.status === 'error' ? null : current))
      setLearningPathError(null)
      const first = accepted[0]
      const goal = isZh ? `学习并掌握 ${first.name}` : `Learn and understand ${first.name}`
      let preparedMaterials: Awaited<ReturnType<typeof prepareTraitTutorMaterial>>[]
      try {
        // Extract only so the server-side guard can inspect untrusted material.
        // The resulting text is not passed to the Gateway classifier.
        preparedMaterials = await mapLearningMaterialFiles(accepted, file =>
          prepareTraitTutorMaterial(file)
        )
      } catch (error) {
        showAttachmentError(materialPreparationErrorMessage(error, isZh))
        return
      }
      const attachmentText = learningMaterialSafetyExcerpt(preparedMaterials)
      let intent: LearnIntentResult
      if (!attachmentText.trim()) {
        // A visual-only or otherwise non-extractable source cannot be screened
        // as text. Keep it out of automatic path creation and require an
        // explicit learner choice instead.
        intent = {
          mode: 'conversation',
          confidence: 0,
          rationale: isZh
            ? '该材料无法提取可审查文本，请确认要如何继续。'
            : 'We could not extract reviewable text from this material. Confirm how to continue.',
          fallback_required: true,
          safety_action: 'confirm',
        }
      } else {
        try {
          intent = await routeLearnIntent(goal, state.sessionId ?? undefined, attachmentText)
        } catch {
          intent = {
            mode: 'conversation',
            confidence: 0,
            rationale: isZh
              ? '暂时无法安全确认材料用途，请选择下一步。'
              : "We could not safely confirm this material's use. Choose the next step.",
            fallback_required: true,
            safety_action: 'confirm',
          }
        }
      }
      if (intent.safety_action === 'block') {
        // Do not retain or analyze a blocked document, and never create a
        // Learning Pack from it. The neutral card asks for a clean rephrase.
        setLearnRouteDecision({ content: goal, result: intent })
        return
      }
      setAttachmentReceipt({ filenames: accepted.map(file => file.name) })
      let next: PendingAttachment[]
      try {
        next = await mapLearningMaterialFiles(accepted, file => fileToAttachment(file))
      } catch (error) {
        showAttachmentError(fileReadErrorMessage(error, isZh))
        return
      }
      setAttachments(prev => mergeUniqueAttachments(prev, next))
      learningAttachmentFilesRef.current = [...learningAttachmentFilesRef.current, ...accepted]
      preparedLearningMaterialsRef.current = [
        ...preparedLearningMaterialsRef.current,
        ...preparedMaterials,
      ]
      if (intent.fallback_required) {
        // A parsed, safe source is already an explicit learning context. Keep
        // it in the material-first flow instead of making the learner repeat
        // a system-routing decision before they can start.
        return
      }
      let learningSession: ReservedLearningSession
      try {
        learningSession = await ensureLearningSession(goal)
      } catch (error) {
        showLearningPathError(error, sessionCreationErrorMessage(error, isZh))
        setLearningPath({ goal, status: 'error', packId: null, plan: null })
        return
      }
      const learningSessionId = learningSession.sessionId
      activeLearningSessionRef.current = learningSessionId
      const planEpoch = ++learningPlanEpochRef.current
      setLearningPathError(null)
      setLearningPath({ goal, status: 'creating', packId: null, plan: null })
      const planTask = createAttachmentLearningPlan(
        accepted,
        goal,
        preparedMaterials,
        learningSessionId
      )
      pendingLearningPlanRef.current = planTask
      void planTask
        .then(target => {
          if (learningPlanEpochRef.current !== planEpoch) return
          latestLearningPlanRef.current = target
          activeLearningPlanRef.current = { packId: target.packId, planId: target.plan.plan_id }
          setLearningPath({
            goal: target.goal,
            packId: target.packId,
            plan: target.plan,
            status: 'ready',
          })
          if (!state.sessionId)
            router.replace(`${chatRoot}/${learningSessionId}`, { scroll: false })
        })
        .catch(async error => {
          const cleaned = await cleanupCreatedLearningSession(learningSession)
          if (learningPlanEpochRef.current !== planEpoch) return
          if (!cleaned) {
            notify(
              isZh
                ? '未完成的临时学习会话清理失败，可稍后在历史记录中删除。'
                : 'The unfinished temporary session could not be cleaned up. You can remove it from history later.',
              { tone: 'error' }
            )
          }
          showLearningPathError(
            error,
            error instanceof SessionApiError ? sessionCreationErrorMessage(error, isZh) : undefined
          )
          setLearningPath(current =>
            current?.goal === goal ? { ...current, status: 'error' } : current
          )
        })
        .finally(() => {
          if (
            learningPlanEpochRef.current === planEpoch &&
            pendingLearningPlanRef.current === planTask
          ) {
            pendingLearningPlanRef.current = null
          }
        })
    },
    [
      chatRoot,
      cleanupCreatedLearningSession,
      createAttachmentLearningPlan,
      ensureLearningSession,
      fileToAttachment,
      filterAndReportFiles,
      isAssistPage,
      isZh,
      router,
      showAttachmentError,
      showLearningPathError,
      state.sessionId,
    ]
  )

  const handlePaste = useCallback(
    async (event: React.ClipboardEvent) => {
      const items = Array.from(event.clipboardData.items)
      const files = items
        .filter(item => item.kind === 'file')
        .map(item => item.getAsFile())
        .filter((file): file is File => file !== null)
      if (!files.length) return
      event.preventDefault()
      await handleAddFiles(files)
    },
    [handleAddFiles]
  )

  const handleDrop = useCallback(
    async (event: React.DragEvent) => {
      event.preventDefault()
      event.stopPropagation()
      setDragging(false)
      dragCounter.current = 0
      await handleAddFiles(Array.from(event.dataTransfer.files))
    },
    [handleAddFiles]
  )

  const handleStartLearning = useCallback(
    async (input: string) => {
      if (isAssistPage || learningLaunchRef.current) return
      const requestedGoal = normalizeLearningGoal(input)
      const launch = (async () => {
        setLearningLaunchPending(true)
        setLearningPathError(null)
        let createdLearningSession: ReservedLearningSession | null = null
        let resolvedGoal =
          requestedGoal ||
          latestLearningPlanRef.current?.goal ||
          (isZh ? '学习当前材料' : 'Learn the current source')
        try {
          let target: LearningPlanTarget | null = null
          if (attachments.length) {
            const learningSession = activeLearningSessionRef.current
              ? { sessionId: activeLearningSessionRef.current, created: false }
              : await ensureLearningSession(resolvedGoal)
            createdLearningSession = learningSession.created ? learningSession : null
            const learningSessionId = learningSession.sessionId
            activeLearningSessionRef.current = learningSessionId
            const pending = pendingLearningPlanRef.current
            target = pending ? await pending : latestLearningPlanRef.current
            if (!target) {
              target = await createAttachmentLearningPlan(
                learningAttachmentFilesRef.current,
                resolvedGoal,
                preparedLearningMaterialsRef.current,
                learningSessionId
              )
            }
            resolvedGoal = requestedGoal || target.goal
            if (requestedGoal && requestedGoal !== target.goal) {
              await updateLearningPack(target.packId, {
                goal: { text: requestedGoal, status: 'active', origin: 'home_upload' },
              })
              const plan = await createLearningComponentPlan(target.packId, {
                instruction: requestedGoal,
              })
              target = { goal: requestedGoal, packId: target.packId, plan }
            }
          } else {
            if (!requestedGoal) return
            setLearningPath({
              goal: requestedGoal,
              status: 'creating',
              packId: null,
              plan: null,
            })
            // Keep the safety boundary for direct Learn goals, but do not
            // create a chat session or ask Assistant to classify the route.
            // Safe goals continue straight to Pack/Plan creation; only a
            // blocked safety result can interrupt this direct path.
            try {
              const intent = await routeLearnIntent(requestedGoal)
              if (intent.safety_action === 'block') {
                setLearningPath(null)
                setLearnRouteDecision({ content: requestedGoal, result: intent })
                return
              }
            } catch {
              // Path creation remains available when the optional Learn
              // safety service is unavailable; Assistant is never invoked.
            }
            const learningSessionId =
              activeLearningSessionRef.current ?? `learning-${crypto.randomUUID()}`
            activeLearningSessionRef.current = learningSessionId
            const goalMaterial = {
              source_type: 'paste',
              title: requestedGoal,
              text: requestedGoal,
              metadata: {
                source_kind: 'learning_goal',
                grounding_status: 'starter_plan',
                session_id: learningSessionId,
                learning_session_id: learningSessionId,
              },
            }
            activeLearningMaterialRef.current = goalMaterial
            const signature = `goal::${requestedGoal}`
            if (initialPackCreateRef.current?.signature !== signature) {
              initialPackCreateRef.current = {
                signature,
                key: `home-pack-${crypto.randomUUID()}`,
              }
            }
            const { pack, plan } = await createLearningPackWithPlan({
              idempotency_key: initialPackCreateRef.current.key,
              title: requestedGoal,
              goal: { text: requestedGoal, status: 'active', origin: 'home_goal' },
              material: goalMaterial,
              sources: [{ source_type: 'user_goal', title: requestedGoal, role: 'learning_goal' }],
              plan: { instruction: requestedGoal },
            })
            target = { goal: requestedGoal, packId: pack.pack_id, plan }
          }
          latestLearningPlanRef.current = target
          activeLearningPlanRef.current = { packId: target.packId, planId: target.plan.plan_id }
          setLearningPath({
            goal: target.goal,
            packId: target.packId,
            plan: target.plan,
            status: 'ready',
          })
          // Smart arrangement now completes on this Learn-only intermediate
          // surface. Navigation to the server-provided start URL is an
          // explicit learner action after the recommended components appear.
        } catch (error) {
          const cleaned = await cleanupCreatedLearningSession(createdLearningSession)
          if (!cleaned) {
            notify(
              isZh
                ? '未完成的临时学习会话清理失败，可稍后在历史记录中删除。'
                : 'The unfinished temporary session could not be cleaned up. You can remove it from history later.',
              { tone: 'error' }
            )
          }
          showLearningPathError(
            error,
            error instanceof SessionApiError ? sessionCreationErrorMessage(error, isZh) : undefined
          )
          setLearningPath(current =>
            current
              ? { ...current, status: 'error' }
              : { goal: resolvedGoal, status: 'error', packId: null, plan: null }
          )
        } finally {
          setLearningLaunchPending(false)
        }
      })()
      learningLaunchRef.current = launch
      try {
        await launch
      } finally {
        if (learningLaunchRef.current === launch) learningLaunchRef.current = null
      }
    },
    [
      attachments.length,
      cleanupCreatedLearningSession,
      createAttachmentLearningPlan,
      ensureLearningSession,
      isAssistPage,
      isZh,
      showLearningPathError,
    ]
  )

  const handleSend = useCallback(
    async (content: string) => {
      if (
        (!content &&
          !attachments.length &&
          !selectedBookReferences.length &&
          !selectedNotebookRecords.length &&
          !selectedHistorySessions.length &&
          !selectedQuestionEntries.length &&
          !selectedMemoryFiles.length &&
          !selectedLearningArtifacts.length) ||
        state.isStreaming
      )
        return false

      let extraAttachments = attachments.map(a => ({
        type: a.type,
        filename: a.filename,
        base64: a.base64,
        mime_type: a.mimeType,
      }))
      let config: Record<string, unknown> | undefined

      if (isResearchMode) {
        config = buildResearchWSConfig({ mode: 'notes', depth: 'standard' })
      }
      config = {
        ...(config ?? {}),
        product_mode: productMode,
        ...(chatGenerationKind ? { traittutor_mode: chatGenerationKind } : {}),
      }
      if (activeLearningPlanRef.current) {
        config = {
          ...config,
          learning_pack_id: activeLearningPlanRef.current.packId,
          learning_plan_id: activeLearningPlanRef.current.planId,
        }
      }
      if (activeCap.value === 'mastery_path') {
        const selected = selectableMasteryPaths.find(path => path.book_id === selectedMasteryPathId)
        if (!selected) {
          setAssistantCapabilityError(
            isZh
              ? '请先选择一条已确认主体和知识点的学习路径，再开始掌握练习。'
              : 'Select a learning path with a confirmed subject and knowledge graph before starting mastery practice.'
          )
          return false
        }
        // Do not derive, cache, or submit subject/KC in the browser. The
        // runtime receives this path ID and performs the trusted binding.
        config = { ...config, learning_path_id: selected.book_id }
      }

      const memoryPayload = [...memoryReferencesPayload]
      const messageContent =
        content ||
        (selectedNotebookRecords.length ||
        selectedBookReferences.length ||
        selectedHistorySessions.length ||
        selectedQuestionEntries.length ||
        selectedLearningArtifacts.length ||
        memoryPayload.length
          ? t('Please use the selected context to help with this request.')
          : '') ||
        (attachments.some(a => a.type === 'image')
          ? t('Please analyze the attached image(s).')
          : '')
      if (!messageContent) return false
      if (attachments.length) {
        setAttachmentReceipt({ filenames: attachments.map(attachment => attachment.filename) })
      } else {
        setAttachmentReceipt(null)
      }
      // Persona is NOT passed per-call here: it is a session-level
      // preference (state.personaSelection) that sendMessage resolves and
      // sends with every turn.
      sendMessage(
        messageContent,
        extraAttachments,
        config,
        notebookReferencesPayload,
        historyReferencesPayload,
        {
          bookReferences: bookReferencesPayload,
          learningArtifactReferences: selectedLearningArtifacts.map(item => ({
            pack_id: item.pack_id,
            artifact_type: item.artifact_type,
            artifact_index: item.artifact_index,
          })),
        },
        questionNotebookReferencesPayload,
        undefined,
        memoryPayload
      )
      shouldAutoScrollRef.current = true
      setAttachments([])
      setSelectedBookReferences([])
      setSelectedNotebookRecords([])
      setSelectedHistorySessions([])
      setSelectedQuestionEntries([])
      setSelectedMemoryFiles([])
      setSelectedLearningArtifacts([])
      setChatGenerationKind(null)
      return true
    },
    [
      attachments,
      activeCap.value,
      bookReferencesPayload,
      historyReferencesPayload,
      isResearchMode,
      isZh,
      memoryReferencesPayload,
      notebookReferencesPayload,
      questionNotebookReferencesPayload,
      productMode,
      selectedHistorySessions.length,
      selectedMemoryFiles.length,
      selectedLearningArtifacts,
      selectedBookReferences.length,
      selectedNotebookRecords.length,
      selectedQuestionEntries.length,
      sendMessage,
      shouldAutoScrollRef,
      state.isStreaming,
      t,
      chatGenerationKind,
      selectedMasteryPathId,
      selectableMasteryPaths,
    ]
  )

  const handleRegenerateMessage = useCallback(() => {
    regenerateLastMessage()
  }, [regenerateLastMessage])

  const handleStartMasteryChat = useCallback(() => {
    const selected = selectableMasteryPaths.find(path => path.book_id === selectedMasteryPathId)
    if (!selected || state.isStreaming) return
    // This is the only browser-authored Mastery config field. The generic
    // WebSocket runtime derives and validates owner/subject/KC binding anew.
    sendMessage(
      isZh
        ? '请开始基于已选学习路径的掌握练习。'
        : 'Start a mastery-guided practice session for my selected learning path.',
      [],
      { learning_path_id: selected.book_id }
    )
    shouldAutoScrollRef.current = true
  }, [
    isZh,
    selectedMasteryPathId,
    selectableMasteryPaths,
    sendMessage,
    shouldAutoScrollRef,
    state.isStreaming,
  ])

  const handleToggleKB = useCallback(
    (name: string) => {
      const current = state.knowledgeBases
      setKBs(current.includes(name) ? current.filter(kb => kb !== name) : [...current, name])
    },
    [setKBs, state.knowledgeBases]
  )

  const handleSelectNotebookPicker = useCallback(() => {
    setShowNotebookPicker(true)
  }, [])
  const handleSelectBookPicker = useCallback(() => {
    setShowBookPicker(true)
  }, [])
  const handleSelectHistoryPicker = useCallback(() => {
    setShowHistoryPicker(true)
  }, [])
  const handleSelectQuestionBankPicker = useCallback(() => {
    setShowQuestionBankPicker(true)
  }, [])
  const handleSelectMemoryPicker = useCallback(() => {
    setShowMemoryPicker(true)
  }, [])
  const handleSelectLearningArtifactPicker = useCallback(() => {
    setShowLearningArtifactPicker(true)
  }, [])
  const handleRemoveHistory = useCallback((sessionId: string) => {
    setSelectedHistorySessions(prev => prev.filter(item => item.sessionId !== sessionId))
  }, [])
  const handleRemoveNotebook = useCallback((notebookId: string) => {
    setSelectedNotebookRecords(prev => prev.filter(record => record.notebookId !== notebookId))
  }, [])
  const handleRemoveBookReference = useCallback((bookId: string) => {
    setSelectedBookReferences(prev => prev.filter(record => record.bookId !== bookId))
  }, [])
  const handleRemoveQuestion = useCallback((entryId: number) => {
    setSelectedQuestionEntries(prev => prev.filter(entry => entry.id !== entryId))
  }, [])
  const handleRemoveLearningArtifact = useCallback((key: string) => {
    setSelectedLearningArtifacts(prev =>
      prev.filter(
        item => `${item.pack_id}:${item.artifact_type}:${item.artifact_index ?? -1}` !== key
      )
    )
  }, [])

  const handleToggleMemoryFile = useCallback((file: ChatMemoryFile) => {
    setSelectedMemoryFiles(prev =>
      prev.includes(file) ? prev.filter(item => item !== file) : [...prev, file]
    )
  }, [])

  const handleCloseNotebookPicker = useCallback(() => {
    setShowNotebookPicker(false)
  }, [])
  const handleCloseBookPicker = useCallback(() => {
    setShowBookPicker(false)
  }, [])
  const handleApplyBookReferences = useCallback((references: SelectedBookReference[]) => {
    setSelectedBookReferences(references)
  }, [])
  const handleApplyNotebookRecords = useCallback((records: SelectedRecord[]) => {
    setSelectedNotebookRecords(records)
  }, [])
  const handleCloseHistoryPicker = useCallback(() => {
    setShowHistoryPicker(false)
  }, [])
  const handleApplyHistorySessions = useCallback((sessions: SelectedHistorySession[]) => {
    setSelectedHistorySessions(sessions)
  }, [])
  const handleCloseQuestionBankPicker = useCallback(() => {
    setShowQuestionBankPicker(false)
  }, [])
  const handleApplyQuestionEntries = useCallback((entries: SelectedQuestionEntry[]) => {
    setSelectedQuestionEntries(entries)
  }, [])
  const handleCloseMemoryPicker = useCallback(() => {
    setShowMemoryPicker(false)
  }, [])
  const handleApplyMemoryFiles = useCallback((files: ChatMemoryFile[]) => {
    setSelectedMemoryFiles(files)
  }, [])
  const handleCloseLearningArtifactPicker = useCallback(() => {
    setShowLearningArtifactPicker(false)
  }, [])
  const handleApplyLearningArtifacts = useCallback(
    (references: SelectedLearningArtifactReference[]) => {
      setSelectedLearningArtifacts(references)
    },
    []
  )

  const handleDownloadMarkdown = useCallback(() => {
    if (!state.messages.length) return
    const title =
      state.messages
        .find(msg => msg.role === 'user')
        ?.content.trim()
        .slice(0, 80) || 'Chat Session'
    downloadChatMarkdown(state.messages, { title })
  }, [state.messages])

  // One composer instance shared by the centered /assist empty state and the
  // bottom-of-thread position, so both places expose the exact same features
  // (attachments, + menu with manual routes, model/tutor selectors, voice,
  // streaming controls). `hasMessages` is the same page state either way; the
  // composer itself adapts width/focus to it.
  const composerElement = (
    <ChatComposer
      composerRef={composerRef}
      capMenuRef={capMenuRef}
      capBtnRef={capBtnRef}
      referenceMenuRef={referenceMenuRef}
      referenceButtonRef={referenceButtonRef}
      dragCounter={dragCounter}
      dragging={dragging}
      capMenuOpen={capMenuOpen}
      referenceMenuOpen={referenceMenuOpen}
      hasMessages={hasMessages}
      attachments={attachments}
      attachmentError={attachmentError}
      activeCap={activeCap}
      knowledgeBases={knowledgeBases}
      llmOptions={llmOptions}
      activeLLMDefault={activeLLMDefault}
      llmSelection={state.llmSelection}
      llmOptionsLoading={llmOptionsLoading}
      llmOptionsError={llmOptionsError}
      selectedBookReferences={selectedBookReferences}
      selectedNotebookRecords={selectedNotebookRecords}
      selectedHistorySessions={selectedHistorySessions}
      selectedQuestionEntries={selectedQuestionEntries}
      notebookReferenceGroups={notebookReferenceGroups}
      selectedPersona={null}
      selectedMemoryFiles={selectedMemoryFiles}
      selectedLearningArtifacts={selectedLearningArtifacts}
      selectedKnowledgeBases={state.knowledgeBases}
      isStreaming={state.isStreaming}
      isVisualizeMode={false}
      capabilities={CAPABILITIES}
      onSetCapMenuOpen={setCapMenuOpen}
      onSetReferenceMenuOpen={setReferenceMenuOpen}
      onToggleKB={handleToggleKB}
      onSelectLLM={setLLMSelection}
      onSelectNotebookPicker={handleSelectNotebookPicker}
      onSelectBookPicker={handleSelectBookPicker}
      onSelectHistoryPicker={handleSelectHistoryPicker}
      onSelectQuestionBankPicker={handleSelectQuestionBankPicker}
      onSelectMemoryPicker={handleSelectMemoryPicker}
      onSelectLearningArtifactPicker={handleSelectLearningArtifactPicker}
      onToggleMemoryFile={handleToggleMemoryFile}
      onSend={handleSend}
      onRemoveAttachment={removeAttachment}
      onPreviewAttachment={handlePreviewPendingAttachment}
      onRemoveHistory={handleRemoveHistory}
      onRemoveBookReference={handleRemoveBookReference}
      onRemoveNotebook={handleRemoveNotebook}
      onRemoveQuestion={handleRemoveQuestion}
      onRemoveLearningArtifact={handleRemoveLearningArtifact}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      onPaste={handlePaste}
      onAddFiles={handleAddFiles}
      onSelectCapability={handleSelectCapability}
      onSelectGenerationShortcut={handleSelectGenerationShortcut}
      generationShortcut={chatGenerationKind ?? null}
      onClearGenerationShortcut={() => setChatGenerationKind(null)}
      inputPlaceholder={
        chatGenerationKind === 'learning_exploration'
          ? t('Paste material or a topic to explore automatically.')
          : chatGenerationKind === 'humanizer'
            ? t('Paste text to humanize. Prefix with 检测： for review only.')
            : chatGenerationKind === 'solve'
              ? t('Paste or describe a problem to solve step by step.')
              : chatGenerationKind === 'knowledge_diagram'
                ? t('Paste material to turn into a knowledge diagram.')
                : !isAssistPage
                  ? isZh
                    ? '描述学习目标，或提出一次性问题…'
                    : 'Describe a learning goal or ask a one-off question…'
                  : undefined
      }
      onCancelStreaming={cancelStreamingTurn}
      prefillInputRef={prefillInputRef}
    />
  )

  return (
    <QuizFollowupProvider>
      <div
        // When the preview drawer is open AND the viewport is wide enough,
        // push the chat content to the left by the drawer's width so the two
        // panels live side-by-side (matches Claude desktop). On smaller
        // screens the drawer overlays — squeezing a phone-width chat into
        // the remaining ~30 px would be useless. The actual padding +
        // transition lives in `chat-preview-shell` (globals.css) so we can
        // hand-tune it without fighting Tailwind's arbitrary-value parser.
        data-preview-open={previewSource ? 'true' : 'false'}
        className="chat-preview-shell flex h-full flex-col overflow-hidden bg-[var(--background)]"
      >
        {hasMessages || sessionLoading ? (
          <div className="mx-auto flex w-full max-w-[960px] flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-6 pt-3 pb-0">
            <div className="group/title min-w-0 flex flex-1 items-center gap-2">
              {sessionTitleEditing ? (
                <input
                  ref={titleInputRef}
                  value={sessionTitleDraft}
                  onChange={event => setSessionTitleDraft(event.target.value)}
                  onBlur={() => void commitSessionTitleEdit()}
                  onKeyDown={handleSessionTitleKeyDown}
                  disabled={sessionTitleSaving}
                  aria-label={t('Session title')}
                  className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 font-serif text-[17px] font-semibold tracking-[-0.01em] text-[var(--foreground)] shadow-sm outline-none transition focus:border-[var(--ring)] focus:ring-2 focus:ring-[var(--ring)]/20 disabled:opacity-60"
                  maxLength={100}
                />
              ) : (
                <button
                  type="button"
                  onClick={startSessionTitleEdit}
                  disabled={!canRenameSession}
                  title={
                    canRenameSession
                      ? t('Click to rename session')
                      : t('Start a conversation to rename')
                  }
                  className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-xl px-2 py-1 text-left font-serif text-[17px] font-semibold tracking-[-0.01em] text-[var(--foreground)] transition hover:bg-[var(--muted)]/55 disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <span className="truncate">{displaySessionTitle}</span>
                  {canRenameSession ? (
                    <PenLine className="h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)] opacity-0 transition-opacity group-hover/title:opacity-100" />
                  ) : null}
                </button>
              )}
              {sessionTitleSaving ? (
                <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
                  {t('Saving...')}
                </span>
              ) : null}
              {sessionTitleError ? (
                <span className="shrink-0 text-xs text-[var(--destructive)]">
                  {sessionTitleError}
                </span>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <HeaderActionButton
                onClick={handleDownloadMarkdown}
                disabled={!state.messages.length}
                icon={Download}
                label={t('Download Markdown')}
                title={t('Download chat history as Markdown')}
              />
            </div>
          </div>
        ) : null}
        <div className="flex w-full flex-1 min-h-0 flex-col">
          {sessionLoading ? (
            <div className="flex w-full flex-1 min-h-0 justify-center px-6">
              <div className="h-full w-full max-w-[960px]">
                <SessionLoadingView onCancel={cancelSessionLoad} />
              </div>
            </div>
          ) : !hasMessages ? (
            <div
              className={`traittutor-scroll-area w-full flex-1 min-h-0 overflow-y-auto ${
                isAssistPage ? 'px-5 sm:px-6' : 'px-4 sm:px-8 lg:px-10 xl:px-12 2xl:px-16'
              }`}
            >
              <div
                className={`flex min-h-full w-full animate-fade-in ${
                  isAssistPage
                    ? 'items-center justify-center py-7 sm:py-10'
                    : 'items-start py-6 sm:py-8'
                }`}
              >
                {isAssistPage ? (
                  !state.isStreaming ? (
                    <div className="flex w-full max-w-[780px] flex-col items-center px-1 text-center">
                      <h1 className="max-w-2xl font-serif text-[clamp(1.35rem,3vw,1.85rem)] font-medium leading-tight tracking-[-0.025em] text-[var(--foreground)]">
                        {isZh ? '开始你的学习任务' : 'Start your learning task'}
                      </h1>
                      <p className="mt-2 text-sm text-[var(--muted-foreground)]">
                        {isZh
                          ? '输入问题、目标，或上传相关材料。'
                          : 'Enter a question, goal, or upload relevant material.'}
                      </p>
                      <div
                        className="mt-4 flex flex-wrap justify-center gap-2"
                        aria-label={isZh ? '任务示例' : 'Task examples'}
                      >
                        {(isZh
                          ? [
                              '帮我拆解这份课程 PPT，整理出重点和练习',
                              '请用提问的方式帮我理解概率论',
                              '根据这份材料生成一份复习计划',
                            ]
                          : [
                              'Break down this course deck into key ideas and practice',
                              'Help me understand probability through questions',
                              'Create a review plan from this material',
                            ]
                        ).map(example => (
                          <button
                            key={example}
                            type="button"
                            onClick={() => prefillInputRef.current?.(example)}
                            className="rounded-full border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-[11px] text-[var(--muted-foreground)] transition hover:border-[var(--primary)]/40 hover:bg-[var(--primary)]/[0.05] hover:text-[var(--foreground)]"
                          >
                            {example}
                          </button>
                        ))}
                      </div>
                      <div className="mt-7 w-full">{composerElement}</div>
                    </div>
                  ) : null
                ) : (
                  <div className="w-full space-y-4">
                    {learningPath?.status === 'ready' ? (
                      <LearningPathLaunch path={learningPath} zh={isZh} />
                    ) : (
                      <>
                        {learnRouteDecision ? (
                          <LearnRouteDecisionCard
                            decision={learnRouteDecision}
                            zh={isZh}
                            onBuildPath={() => {
                              const current = learnRouteDecision
                              setLearnRouteDecision(null)
                              void handleStartLearning(current.content)
                            }}
                          />
                        ) : null}
                        <LearningHomeIntro
                          zh={isZh}
                          // A plain Learn goal is already an explicit path
                          // request. Do not send it through the conversation
                          // runtime; create the owner-bound Pack/Plan and let
                          // handleStartLearning navigate to its goal map.
                          onBuildPath={goal => void handleStartLearning(goal)}
                          onFiles={files => void handleAddFiles(files)}
                          attachments={attachments}
                          attachmentError={attachmentError}
                          onRemoveAttachment={removeAttachment}
                          starting={learningLaunchPending}
                          pathStatus={learningPath?.status ?? null}
                          pathError={learningPathError}
                        />
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div
              ref={messagesContainerRef}
              data-chat-scroll-root="true"
              onScroll={handleMessagesScroll}
              // `both-edges` reserves the scrollbar gutter on both sides so
              // the inner mx-auto column centers on the same axis as the
              // header and composer (siblings outside this scrollport) on
              // classic-scrollbar platforms; plain `stable` would shift it
              // ~half a scrollbar-width left of them.
              className={`traittutor-scroll-area w-full flex-1 min-h-0 overflow-y-auto [scrollbar-gutter:stable_both-edges] ${hasMessages ? 'pt-6' : 'pt-2 pb-6'}`}
              style={
                hasMessages
                  ? (() => {
                      // The bottom 40 px of the messages area fades to
                      // transparent so content "dissolves" into the composer
                      // gutter. Without enough bottom padding, the fade
                      // overlaps the last assistant paragraph and looks like
                      // a stuck scroll — the user reaches scrollHeight but
                      // can still see only a faded sliver of text. paddingBottom
                      // is sized so the fade falls over empty space.
                      const maskImage =
                        'linear-gradient(to bottom, transparent 0px, #000 32px, #000 calc(100% - 40px), transparent 100%)'
                      return {
                        paddingBottom: '48px',
                        WebkitMaskImage: maskImage,
                        maskImage,
                      }
                    })()
                  : undefined
              }
            >
              <div className="mx-auto w-full max-w-[960px] space-y-9 px-6">
                {!isAssistPage ? (
                  <LearnWorkspaceStatus
                    zh={isZh}
                    learningPath={learningPath}
                    routeDecision={learnRouteDecision}
                    onBuildPath={content => {
                      setLearnRouteDecision(null)
                      void handleStartLearning(content)
                    }}
                  />
                ) : null}
                <ChatMessageList
                  messages={state.messages}
                  isStreaming={state.isStreaming}
                  sessionId={state.sessionId}
                  language={state.language}
                  onCopyAssistantMessage={copyAssistantMessage}
                  onRegenerateMessage={handleRegenerateMessage}
                  onPreviewAttachment={handlePreviewMessageAttachment}
                  onDeleteTurn={deleteTurn}
                  selectedBranches={state.selectedBranches}
                  onEditMessage={editMessage}
                  onSwitchBranch={switchBranch}
                  onSubmitUserReply={submitUserReply}
                />
                {attachmentReceipt ? (
                  <section
                    role="status"
                    className="rounded-2xl border border-sky-500/25 bg-sky-500/[0.06] px-4 py-3 text-[12px] leading-5 text-[var(--muted-foreground)]"
                  >
                    <span className="font-semibold text-[var(--foreground)]">
                      {isAssistPage
                        ? isZh
                          ? '附件已提交给 TraitTutor'
                          : 'Files submitted to TraitTutor'
                        : isZh
                          ? '材料已提交给学习教练'
                          : 'Source submitted to the learning coach'}
                    </span>
                    <span className="ml-2">
                      {attachmentReceipt.filenames.join('、')} ·{' '}
                      {isAssistPage
                        ? isZh
                          ? '正在读取内容并结合当前任务处理。'
                          : 'Reading the content and applying it to this task.'
                        : isZh
                          ? '正在读取内容，本轮回复会给出主题、难度、核心概念和下一步建议。'
                          : 'This turn will identify the topic, level, core concepts, and next action.'}
                    </span>
                  </section>
                ) : null}
                <div ref={messagesEndRef} className="h-px w-full shrink-0" />
              </div>
            </div>
          )}

          {assistantCapabilityError ? (
            <div className="mx-auto w-full max-w-[960px] px-6 pb-2" role="alert">
              <p className="rounded-xl border border-amber-500/35 bg-amber-500/[0.07] px-3 py-2 text-xs text-[var(--foreground)]">
                {assistantCapabilityError}
              </p>
            </div>
          ) : null}
          {masteryPickerVisible ? (
            <MasteryPathPicker
              zh={isZh}
              streaming={state.isStreaming}
              state={masteryPathsState}
              paths={masteryPaths}
              selectablePaths={selectableMasteryPaths}
              selectedPathId={selectedMasteryPathId}
              onSelectPathId={setSelectedMasteryPathId}
              onStart={handleStartMasteryChat}
            />
          ) : null}
          {isAssistPage && (hasMessages || state.isStreaming) ? composerElement : null}
          {hasMessages || state.isStreaming ? (
            <div
              aria-hidden="true"
              className="shrink-0 grow-0"
              style={{
                transition: 'flex-grow 650ms cubic-bezier(0.16, 1, 0.3, 1)',
              }}
            />
          ) : null}
        </div>
        <NotebookRecordPicker
          open={showNotebookPicker}
          onClose={handleCloseNotebookPicker}
          onApply={handleApplyNotebookRecords}
        />
        <BookReferencePicker
          open={showBookPicker}
          initialReferences={selectedBookReferences}
          onClose={handleCloseBookPicker}
          onApply={handleApplyBookReferences}
        />
        <HistorySessionPicker
          open={showHistoryPicker}
          onClose={handleCloseHistoryPicker}
          onApply={handleApplyHistorySessions}
        />
        <QuestionBankPicker
          open={showQuestionBankPicker}
          onClose={handleCloseQuestionBankPicker}
          onApply={handleApplyQuestionEntries}
        />
        <MemoryPicker
          open={showMemoryPicker}
          initialFiles={selectedMemoryFiles}
          onClose={handleCloseMemoryPicker}
          onApply={handleApplyMemoryFiles}
        />
        <LearningArtifactPicker
          open={showLearningArtifactPicker}
          initialReferences={selectedLearningArtifacts}
          onClose={handleCloseLearningArtifactPicker}
          onApply={handleApplyLearningArtifacts}
        />
        <FilePreviewDrawer
          open={previewSource !== null}
          source={previewSource}
          onClose={handleClosePreview}
        />
      </div>
    </QuizFollowupProvider>
  )
}

/**
 * Header action button that auto-collapses to icon-only when the chat
 * column gets squeezed on narrow viewports. The
 * label stays as the button's `title` so hovering an icon still reveals
 * what it does. Optional `active` flag paints the button with a primary tint.
 */
// Claude-style icon-only header action: bare 16px glyph, function revealed
// by an instant tooltip; active state gets a primary tint.
function HeaderActionButton({
  onClick,
  disabled,
  active,
  icon: Icon,
  label,
  title,
}: {
  onClick: () => void
  disabled?: boolean
  active?: boolean
  icon: LucideIcon
  label: string
  title?: string
}) {
  return (
    <Tooltip label={title ?? label} side="bottom">
      <button
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={active}
        className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-[background-color,color,transform] duration-150 active:scale-90 disabled:cursor-not-allowed disabled:opacity-40 ${
          active
            ? 'bg-[var(--primary)]/10 text-[var(--primary)]'
            : 'text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)] disabled:hover:bg-transparent disabled:hover:text-[var(--muted-foreground)]'
        }`}
      >
        <Icon size={16} strokeWidth={1.7} className="shrink-0" />
      </button>
    </Tooltip>
  )
}
