'use client'

import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowUp,
  BookOpen,
  Brain,
  Check,
  ClipboardList,
  GraduationCap,
  Loader2,
  MessageSquare,
  Mic,
  Paperclip,
  Plus,
  Square,
  UserRound,
  X,
} from 'lucide-react'
import { ATTACHMENT_ACCEPT, docIconFor, formatBytes, isSvgFilename } from '@/lib/doc-attachments'
import { useTranslation } from 'react-i18next'
import type { SelectedHistorySession } from '@/components/chat/HistorySessionPicker'
import type { SelectedQuestionEntry } from '@/components/chat/QuestionBankPicker'
import type { SelectedRecord } from '@/lib/notebook-selection-types'
import type { LLMSelection } from '@/lib/unified-ws'
import type { LLMOption } from '@/lib/llm-options'
import ChatReferenceMenu from '@/components/chat/ChatReferenceMenu'
import type { ChatMemoryFile } from '@/lib/chat-memory-items'
import type { SelectedBookReference } from '@/lib/book-references'
import type { SelectedLearningArtifactReference } from '@/components/chat/LearningArtifactPicker'
import ModelSelector from './ModelSelector'
import TutorProfileButton from './TutorProfileButton'
import { TraitTutorIcon, type TraitTutorIconName } from '@/components/brand/TraitTutorIcon'

type ReferenceSelectionCounts = {
  attachments: number
  knowledge: number
  chatHistory: number
  books: number
  notebooks: number
  questionBank: number
  persona: number
  memory: number
  learningArtifacts: number
}
import ContextReferenceTree, { type ContextTreeItem } from './ContextReferenceTree'
import { ComposerInput, type ComposerInputHandle } from './ComposerInput'
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder'

interface PendingAttachment {
  type: string
  filename: string
  base64?: string
  previewUrl?: string
  size?: number
  mimeType?: string
}

interface KnowledgeBase {
  name: string
}

interface CapabilityDef {
  value: string
  label: string
  description: string
  icon: TraitTutorIconName
  allowedTools: string[]
  // Loop-engine capabilities run on the chat agent loop and
  // are collapsed into the "More" flyout instead of listed directly.
  loopEngine?: boolean
}

type GenerationShortcut =
  'solve' | 'learning_exploration' | 'knowledge_diagram' | 'humanizer'

const GENERATION_SHORTCUTS: Array<{
  kind: GenerationShortcut
  label: string
  description: string
  icon: TraitTutorIconName
}> = [
  { kind: 'solve', label: 'Solver', description: '调用学科解题提示词分步求解', icon: 'solve' },
  {
    kind: 'learning_exploration',
    label: '学习探索',
    description: '自动补足来源、概念和下一步',
    icon: 'explore',
  },
  {
    kind: 'knowledge_diagram',
    label: '知识图解',
    description: '在聊天中生成可积累的概念图',
    icon: 'visualize',
  },
  {
    kind: 'humanizer',
    label: 'Humanizer',
    description: '自然改写文本，保留原意',
    icon: 'motivation',
  },
]

function GenerationMenuItem({
  shortcut,
  selected,
  onSelect,
}: {
  shortcut: (typeof GENERATION_SHORTCUTS)[number]
  selected: boolean
  onSelect: (kind: GenerationShortcut) => void
}) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      onPointerDown={event => {
        // Select before the document-level outside-click listener can close
        // the menu, including on touch devices.
        event.preventDefault()
        onSelect(shortcut.kind)
      }}
      className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
        selected ? 'bg-[var(--primary)]/[0.06]' : 'hover:bg-[var(--muted)]/45'
      }`}
    >
      <TraitTutorIcon
        name={shortcut.icon}
        size={16}
        strokeWidth={1.7}
        className={`shrink-0 ${selected ? 'text-[var(--primary)]' : 'text-[var(--muted-foreground)]'}`}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px] font-medium leading-snug text-[var(--foreground)]">
          {t(shortcut.label)}
        </div>
        <div className="truncate text-[11px] leading-snug text-[var(--muted-foreground)]">
          {t(shortcut.description)}
        </div>
      </div>
      {selected ? (
        <Check size={14} strokeWidth={2} className="shrink-0 text-[var(--primary)]" />
      ) : null}
    </button>
  )
}

/** What the "+" trigger morphs into after a menu selection: a generation
 *  shortcut or capability armed for the next send. */
type ArmedCapsule = {
  label: string
  icon: TraitTutorIconName
  clear: () => void
}

/** One row in the capability picker — shared by the built-in list and the
 *  "More" flyout so both render identically. */
function CapMenuItem({
  cap,
  selected,
  onSelect,
}: {
  cap: CapabilityDef
  selected: boolean
  onSelect: (value: string) => void
}) {
  const { t } = useTranslation()
  return (
    <button
      type="button"
      onClick={() => onSelect(cap.value)}
      className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
        selected ? 'bg-[var(--primary)]/[0.06]' : 'hover:bg-[var(--muted)]/45'
      }`}
    >
      <TraitTutorIcon
        name={cap.icon}
        size={16}
        strokeWidth={1.65}
        className={`shrink-0 ${selected ? 'text-[var(--primary)]' : 'text-[var(--muted-foreground)]'}`}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px] font-medium leading-snug text-[var(--foreground)]">
          {t(cap.label)}
        </div>
        <div className="truncate text-[11px] leading-snug text-[var(--muted-foreground)]">
          {t(cap.description)}
        </div>
      </div>
      {selected && <Check size={14} strokeWidth={2} className="shrink-0 text-[var(--primary)]" />}
    </button>
  )
}

export default memo(function ChatComposer({
  composerRef,
  referenceMenuRef,
  referenceButtonRef,
  dragCounter,
  dragging,
  referenceMenuOpen,
  hasMessages,
  attachments,
  attachmentError,
  activeCap,
  llmOptions,
  activeLLMDefault,
  llmSelection,
  llmOptionsLoading,
  llmOptionsError,
  selectedNotebookRecords,
  selectedBookReferences,
  selectedHistorySessions,
  selectedQuestionEntries,
  notebookReferenceGroups,
  selectedPersona,
  selectedMemoryFiles,
  selectedLearningArtifacts = [],
  selectedKnowledgeBases,
  isStreaming,
  isVisualizeMode,
  capabilities,
  onSetReferenceMenuOpen,
  onSelectLLM,
  onSelectQuestionBankPicker,
  onSelectMemoryPicker,
  onSelectLearningArtifactPicker,
  onClearPersona,
  onToggleMemoryFile,
  onSend,
  onRemoveAttachment,
  onPreviewAttachment,
  onRemoveHistory,
  onRemoveBookReference,
  onRemoveNotebook,
  onRemoveQuestion,
  onRemoveLearningArtifact,
  onDragEnter,
  onDragLeave,
  onDragOver,
  onDrop,
  onPaste,
  onAddFiles,
  onSelectCapability,
  onSelectGenerationShortcut,
  generationShortcut,
  onClearGenerationShortcut,
  onCancelStreaming,
  prefillInputRef,
  inputPlaceholder,
}: {
  composerRef: RefObject<HTMLDivElement | null>
  capMenuRef: RefObject<HTMLDivElement | null>
  capBtnRef: RefObject<HTMLButtonElement | null>
  referenceMenuRef: RefObject<HTMLDivElement | null>
  referenceButtonRef: RefObject<HTMLButtonElement | null>
  dragCounter: RefObject<number>
  dragging: boolean
  capMenuOpen: boolean
  referenceMenuOpen: boolean
  hasMessages: boolean
  attachments: PendingAttachment[]
  attachmentError: string | null
  activeCap: CapabilityDef
  knowledgeBases: KnowledgeBase[]
  llmOptions: LLMOption[]
  activeLLMDefault: LLMSelection | null
  llmSelection: LLMSelection | null
  llmOptionsLoading: boolean
  llmOptionsError: boolean
  selectedNotebookRecords: SelectedRecord[]
  selectedBookReferences: SelectedBookReference[]
  selectedHistorySessions: SelectedHistorySession[]
  selectedQuestionEntries: SelectedQuestionEntry[]
  notebookReferenceGroups: Array<{
    notebookId: string
    notebookName: string
    count: number
  }>
  selectedPersona?: string | null
  selectedMemoryFiles: ChatMemoryFile[]
  selectedLearningArtifacts?: SelectedLearningArtifactReference[]
  selectedKnowledgeBases: string[]
  isStreaming: boolean
  isVisualizeMode: boolean
  capabilities: CapabilityDef[]
  onSetCapMenuOpen: (open: boolean | ((prev: boolean) => boolean)) => void
  onSetReferenceMenuOpen: (open: boolean | ((prev: boolean) => boolean)) => void
  onToggleKB: (name: string) => void
  onSelectLLM: (selection: LLMSelection | null) => void
  onSelectNotebookPicker: () => void
  onSelectBookPicker: () => void
  onSelectHistoryPicker: () => void
  onSelectQuestionBankPicker: () => void
  onSelectMemoryPicker: () => void
  onSelectLearningArtifactPicker?: () => void
  onClearPersona?: () => void
  onToggleMemoryFile: (file: ChatMemoryFile) => void
  onSend: (content: string) => boolean | void | Promise<boolean | void>
  onRemoveAttachment: (index: number) => void
  onPreviewAttachment?: (index: number) => void
  onRemoveHistory: (sessionId: string) => void
  onRemoveBookReference: (bookId: string) => void
  onRemoveNotebook: (notebookId: string) => void
  onRemoveQuestion: (entryId: number) => void
  onRemoveLearningArtifact?: (key: string) => void
  onDragEnter: (event: React.DragEvent) => void
  onDragLeave: (event: React.DragEvent) => void
  onDragOver: (event: React.DragEvent) => void
  onDrop: (event: React.DragEvent) => void
  onPaste: (event: React.ClipboardEvent) => void
  onAddFiles: (files: File[]) => void
  onSelectCapability: (value: string) => void
  /** Activates a compact TraitTutor action within this same composer. */
  onSelectGenerationShortcut?: (kind: GenerationShortcut) => void
  /** Generation is a mode of this composer, never a second input surface. */
  generationShortcut?: GenerationShortcut | null
  onClearGenerationShortcut?: () => void
  onCancelStreaming: () => void
  /**
   * Optional ref the composer writes its ``prefillInput`` function into
   * once mounted, so the message-list side (specifically
   * ``AskUserOptions`` chips) can drop a string into the textarea
   * without owning the composer's imperative handle directly.
   */
  prefillInputRef?: React.MutableRefObject<((text: string) => void) | null>
  /** Override the composer placeholder (e.g. quiz follow-up). */
  inputPlaceholder?: string
}) {
  const { t } = useTranslation()
  const [hasContent, setHasContent] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const inputHandleRef = useRef<ComposerInputHandle>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!prefillInputRef) return
    prefillInputRef.current = (text: string) => {
      inputHandleRef.current?.setValue(text)
    }
    return () => {
      if (prefillInputRef) prefillInputRef.current = null
    }
  }, [prefillInputRef])

  // Microphone → speech-to-text. Appends the transcript to whatever is already
  // in the composer so a dictated phrase can be combined with typed text.
  const handleTranscript = useCallback((text: string) => {
    const current = inputHandleRef.current?.getValue() || ''
    const next = current.trim() ? `${current.trimEnd()} ${text}` : text
    inputHandleRef.current?.setValue(next)
  }, [])
  const recorder = useVoiceRecorder(handleTranscript)

  const handlePickFiles = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const picked = Array.from(event.target.files ?? [])
      if (picked.length) onAddFiles(picked)
      // Reset so picking the same file twice still triggers `change`.
      event.target.value = ''
    },
    [onAddFiles]
  )

  useEffect(() => {
    if (!hasMessages) textareaRef.current?.focus()
  }, [hasMessages])

  const handleSelectCapability = useCallback(
    (value: string) => {
      onClearGenerationShortcut?.()
      onSelectCapability(value)
    },
    [onClearGenerationShortcut, onSelectCapability]
  )

  const handleSelectCapabilityFromReferenceMenu = useCallback(
    (value: string) => {
      onSetReferenceMenuOpen(false)
      handleSelectCapability(value)
    },
    [handleSelectCapability, onSetReferenceMenuOpen]
  )

  const handleSelectGenerationFromReferenceMenu = useCallback(
    (kind: GenerationShortcut) => {
      onSetReferenceMenuOpen(false)
      onSelectGenerationShortcut?.(kind)
    },
    [onSelectGenerationShortcut, onSetReferenceMenuOpen]
  )

  // Functional-update form keeps `handleInputChange` identity stable across
  // every keystroke (no `hasContent` in deps), so the memoized ComposerInput
  // doesn't get re-rendered just because we observed a content-empty toggle.
  const handleInputChange = useCallback((val: string) => {
    const next = !!val.trim()
    setHasContent(prev => (prev === next ? prev : next))
  }, [])

  const doSend = useCallback(
    async (content: string) => {
      const submitted = await onSend(content)
      // A transport failure deliberately keeps the draft available for retry.
      // A routing receipt is a completed submission even when it stops before
      // a downstream owner creates work.
      if (submitted !== false) {
        setHasContent(false)
        inputHandleRef.current?.clear()
      }
      return submitted
    },
    [onSend]
  )

  const hasReferences =
    !!attachments.length ||
    !!selectedBookReferences.length ||
    !!selectedNotebookRecords.length ||
    !!selectedHistorySessions.length ||
    !!selectedQuestionEntries.length ||
    !!selectedPersona ||
    !!selectedMemoryFiles.length ||
    !!selectedLearningArtifacts.length

  const canSend = (hasContent || hasReferences) && !isStreaming

  const referenceSelectionCounts: ReferenceSelectionCounts = {
    attachments: attachments.length,
    knowledge: selectedKnowledgeBases.length,
    chatHistory: selectedHistorySessions.length,
    books: selectedBookReferences.reduce((total, ref) => total + ref.pages.length, 0),
    notebooks: selectedNotebookRecords.length,
    questionBank: selectedQuestionEntries.length,
    persona: selectedPersona ? 1 : 0,
    memory: selectedMemoryFiles.length,
    learningArtifacts: selectedLearningArtifacts.length,
  }
  // Badge on the "+" button = how many things are selected through the
  // "+" menu. Knowledge is excluded: it no longer lives in this menu —
  // it has its own toolbar chip (KnowledgeSelector) with its own active
  // state, so counting it here would double-signal.
  const contextSelectionCount = Object.entries(referenceSelectionCounts).reduce(
    (total, [key, count]) => (key === 'knowledge' || key === 'attachments' ? total : total + count),
    0
  )

  // The "+" trigger reflects whatever was last armed from its menu.
  // Generation shortcut > non-default capability: selecting either clears
  // the other in page.tsx, so one capsule with a single X is enough. The
  // default capability (value === '') stays a plain "+".
  const armedCapsule: ArmedCapsule | null = (() => {
    if (generationShortcut && onClearGenerationShortcut) {
      const shortcut = GENERATION_SHORTCUTS.find(s => s.kind === generationShortcut)
      if (shortcut) {
        return {
          label: t(shortcut.label),
          icon: shortcut.icon,
          clear: onClearGenerationShortcut,
        }
      }
    }
    if (activeCap.value !== '') {
      return {
        label: t(activeCap.label),
        icon: activeCap.icon,
        clear: () => onSelectCapability(''),
      }
    }
    return null
  })()

  // Unified reference tree above the textarea: source references, persona
  // and memory render as quiet monochrome rows, collapsed behind a count
  // by default. File attachments intentionally stay OUT of the tree —
  // they keep their preview cards below the textarea.
  // Knowledge bases are intentionally NOT in this tree: they are a
  // session-level retrieval SCOPE (sticky, persisted), not a one-shot
  // reference like the rows below. That sticky state lives in the
  // toolbar KnowledgeSelector chip instead — same lifecycle class as
  // the persona selector.
  const contextTreeItems: ContextTreeItem[] = [
    ...selectedBookReferences.map((book): ContextTreeItem => ({
      key: `book-${book.bookId}`,
      icon: BookOpen,
      kind: t('Book'),
      label: `${book.bookTitle} (${book.pages.length})`,
      onRemove: () => onRemoveBookReference(book.bookId),
    })),
    ...notebookReferenceGroups.map((group): ContextTreeItem => ({
      key: `nb-${group.notebookId}`,
      icon: BookOpen,
      kind: t('Notebook'),
      label: `${group.notebookName} (${group.count})`,
      onRemove: () => onRemoveNotebook(group.notebookId),
    })),
    ...selectedHistorySessions.map((session): ContextTreeItem => ({
      key: `hist-${session.sessionId}`,
      icon: MessageSquare,
      kind: t('Chat History'),
      label: session.title,
      onRemove: () => onRemoveHistory(session.sessionId),
    })),
    ...selectedQuestionEntries.map((entry): ContextTreeItem => ({
      key: `q-${entry.id}`,
      icon: ClipboardList,
      kind: t('Question Bank'),
      label: entry.question,
      onRemove: () => onRemoveQuestion(entry.id),
    })),
    ...selectedLearningArtifacts.map((artifact): ContextTreeItem => ({
      key: `learning-artifact-${artifact.pack_id}-${artifact.artifact_type}-${artifact.artifact_index ?? -1}`,
      icon: GraduationCap,
      kind: t('学习产物'),
      label: `${artifact.title} · ${artifact.pack_title}`,
      onRemove: () =>
        onRemoveLearningArtifact?.(
          `${artifact.pack_id}:${artifact.artifact_type}:${artifact.artifact_index ?? -1}`
        ),
    })),
    ...(selectedPersona
      ? [
          {
            key: 'persona',
            icon: UserRound,
            kind: t('Persona'),
            label: selectedPersona,
            onRemove: () => onClearPersona?.(),
          } satisfies ContextTreeItem,
        ]
      : []),
    ...selectedMemoryFiles.map((file): ContextTreeItem => ({
      key: `mem-${file}`,
      icon: Brain,
      kind: t('Memory'),
      label: file === 'summary' ? t('Summary') : t('Profile'),
      onRemove: () => onToggleMemoryFile(file),
    })),
  ]

  const handleManualSend = useCallback(() => {
    if (!canSend) return
    const content = inputHandleRef.current?.getValue() || ''
    void doSend(content)
  }, [canSend, doSend])

  return (
    <div
      ref={composerRef}
      className={`relative z-20 mx-auto w-full shrink-0 px-4 pb-4 sm:px-6 sm:pb-5 ${hasMessages ? 'pt-1 max-w-[960px]' : 'max-w-[768px] pt-3'}`}
      style={{
        transition: 'max-width 650ms cubic-bezier(0.16, 1, 0.3, 1)',
      }}
    >
      {hasMessages && (
        <div className="pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-transparent to-[var(--background)]/72" />
      )}

      <div className="relative">
        <div
          data-testid="chat-composer-card"
          className={`relative rounded-[26px] border bg-[var(--card)] shadow-[0_1px_2px_rgba(0,0,0,0.025),0_10px_28px_-10px_rgba(0,0,0,0.08)] transition-colors ${
            dragging
              ? 'border-[var(--primary)] bg-[var(--primary)]/[0.03]'
              : 'border-[var(--border)]/55'
          }`}
          onDragEnter={onDragEnter}
          onDragLeave={onDragLeave}
          onDragOver={onDragOver}
          onDrop={onDrop}
          data-drag-counter={dragCounter.current}
        >
          {dragging && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-[26px] border-2 border-dashed border-[var(--primary)]/50 bg-[var(--primary)]/[0.04]">
              <div className="flex flex-col items-center gap-1 text-[var(--primary)]">
                <Paperclip size={22} strokeWidth={1.6} />
                <span className="text-[13px] font-medium">{t('Drop files here')}</span>
                <span className="text-[11px] text-[var(--primary)]/70">
                  {t('Images, Office docs, code & text')}
                </span>
              </div>
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ATTACHMENT_ACCEPT}
            onChange={handleFileInputChange}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
          />

          {contextTreeItems.length > 0 && (
            // The reference zone reads as its own layer: a faint muted band
            // with a hairline against the input area, following the card's
            // top radius.
            <div className="rounded-t-[26px] border-b border-[var(--border)]/30 bg-[var(--muted)]/30 px-4 pb-2 pt-2.5">
              {/* Narrower than the composer on purpose — long titles
                  truncate early so the tree reads as an annotation, not a
                  content row. */}
              <div className="max-w-[min(560px,85%)]">
                <ContextReferenceTree
                  items={contextTreeItems}
                  direction="up"
                  summaryNoun={t('references')}
                />
              </div>
            </div>
          )}
          <ComposerInput
            ref={inputHandleRef}
            textareaRef={textareaRef}
            isVisualizeMode={isVisualizeMode}
            canSendEmpty={hasReferences}
            onSend={doSend}
            onInputChange={handleInputChange}
            onPaste={onPaste}
            selectedCounts={referenceSelectionCounts}
            onSelectAttach={handlePickFiles}
            onSelectQuestionBankPicker={onSelectQuestionBankPicker}
            onSelectLearningArtifactPicker={onSelectLearningArtifactPicker}
            onSelectMemoryPicker={onSelectMemoryPicker}
            placeholder={inputPlaceholder}
            minHeight={hasMessages ? 28 : 64}
          />

          {!!attachments.length && (
            <div className="flex flex-wrap gap-2 px-4 pb-2">
              {attachments.map((a, i) => {
                const previewLabel = t('Preview')
                const removeLabel = t('Remove attachment')
                if ((a.type === 'image' || isSvgFilename(a.filename)) && a.previewUrl) {
                  return (
                    <div
                      key={`${a.filename}-${i}`}
                      className="group relative"
                      title={a.filename || previewLabel}
                    >
                      <button
                        type="button"
                        onClick={() => onPreviewAttachment?.(i)}
                        aria-label={previewLabel}
                        className="relative block h-16 w-16 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--card)] transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]/40"
                      >
                        {/* Native <img> is safe for SVG: scripts inside an
                            SVG don't execute under <img> context. Next.js
                            <Image> rejects SVG by default. */}
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={a.previewUrl}
                          alt={a.filename || t('Attachment preview')}
                          className={`h-full w-full ${isSvgFilename(a.filename) ? 'object-contain p-1' : 'object-cover'}`}
                        />
                      </button>
                      <button
                        type="button"
                        onClick={e => {
                          e.stopPropagation()
                          onRemoveAttachment(i)
                        }}
                        aria-label={removeLabel}
                        className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--foreground)] text-[var(--background)] opacity-0 shadow-sm transition-opacity group-hover:opacity-100"
                      >
                        <X size={10} />
                      </button>
                    </div>
                  )
                }
                const spec = docIconFor(a.filename)
                const Icon = spec.Icon
                const sizeLabel = a.size ? formatBytes(a.size) : ''
                return (
                  <div key={`${a.filename}-${i}`} className="group relative" title={a.filename}>
                    <button
                      type="button"
                      onClick={() => onPreviewAttachment?.(i)}
                      aria-label={previewLabel}
                      className="flex h-16 w-[160px] items-center gap-2.5 rounded-lg border border-[var(--border)] bg-[var(--card)] px-2.5 text-left transition-colors hover:border-[var(--primary)]/40 hover:bg-[var(--muted)]/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]/40"
                    >
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-[var(--muted)]/60">
                        <Icon size={22} strokeWidth={1.5} className={spec.tint} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[12px] font-medium text-[var(--foreground)]">
                          {a.filename}
                        </div>
                        <div className="truncate text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
                          {sizeLabel ? `${spec.label} · ${sizeLabel}` : spec.label}
                        </div>
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={e => {
                        e.stopPropagation()
                        onRemoveAttachment(i)
                      }}
                      aria-label={removeLabel}
                      className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--foreground)] text-[var(--background)] opacity-0 shadow-sm transition-opacity group-hover:opacity-100"
                    >
                      <X size={10} />
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {attachmentError && (
            <div className="px-4 pb-2 text-[11px] text-red-600">{attachmentError}</div>
          )}

          {/* Claude-style chrome-free toolbar: no divider against the input
              area, no pill borders — quiet text/icon buttons that surface
              on hover. */}
          <div className="px-3 pb-2 pt-0.5">
            <div className="flex items-center gap-1">
              <div className="relative flex min-w-0 flex-1 items-center">
                {armedCapsule ? (
                  // Arming a manual route, generation shortcut, or non-default
                  // capability turns the "+" trigger into a cancellable
                  // capsule: the destination of the next send is visible right
                  // at the button (icon + name + X) instead of a separate chip
                  // above the input. Clicking the body re-opens the menu to
                  // swap; X clears back to "+".
                  <button
                    ref={referenceButtonRef}
                    type="button"
                    onClick={() => onSetReferenceMenuOpen(v => !v)}
                    title={t('Add files & context')}
                    aria-label={`${t('Add files & context')} — ${armedCapsule.label}`}
                    className={`relative flex h-8 shrink-0 items-center gap-1.5 rounded-lg border border-[var(--primary)]/40 bg-[var(--primary)]/10 pl-2 pr-1.5 text-[var(--primary)] transition-[background-color,color,transform] duration-150 active:scale-95 ${
                      referenceMenuOpen
                        ? 'bg-[var(--primary)]/[0.16]'
                        : 'hover:bg-[var(--primary)]/[0.16]'
                    }`}
                  >
                    <TraitTutorIcon name={armedCapsule.icon} size={16} strokeWidth={1.9} />
                    <span className="text-[12px] font-medium">{armedCapsule.label}</span>
                    <span
                      role="button"
                      tabIndex={-1}
                      aria-label={t('Use automatic routing')}
                      onClick={event => {
                        // Don't let the X click toggle the menu — it only clears.
                        event.stopPropagation()
                        armedCapsule.clear()
                      }}
                      className="flex h-5 w-5 items-center justify-center rounded-md text-[var(--primary)]/80 transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
                    >
                      <X size={12} strokeWidth={2} />
                    </span>
                  </button>
                ) : (
                  <button
                    ref={referenceButtonRef}
                    type="button"
                    onClick={() => onSetReferenceMenuOpen(v => !v)}
                    title={t('Add files & context')}
                    aria-label={t('Add files & context')}
                    className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-[background-color,color,transform] duration-150 active:scale-90 ${
                      referenceMenuOpen
                        ? 'bg-[var(--muted)] text-[var(--foreground)]'
                        : 'text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]'
                    }`}
                  >
                    <Plus size={20} strokeWidth={1.8} />
                    {contextSelectionCount > 0 && (
                      <span className="absolute -right-0.5 -top-0.5 flex h-[13px] min-w-[13px] items-center justify-center rounded-full bg-[var(--primary)] px-[3px] text-[8px] font-semibold leading-none text-[var(--primary-foreground)] ring-[1.5px] ring-[var(--card)]">
                        {contextSelectionCount}
                      </span>
                    )}
                  </button>
                )}
                <AnimatePresence>
                  {referenceMenuOpen && (
                    <motion.div
                      ref={referenceMenuRef}
                      className="absolute bottom-full left-0 z-50 mb-1.5"
                      style={{ transformOrigin: 'bottom left' }}
                      initial={{ opacity: 0, y: 6, scale: 0.96 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: 4, scale: 0.97 }}
                      transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
                    >
                      <ChatReferenceMenu
                        variant="toolbar"
                        selectedCounts={referenceSelectionCounts}
                        // Reference pickers (学习产物 / 错题 / 学习画像) live on
                        // the toolbar as inline chips now — keep them out of the
                        // "+" menu so it only shows capabilities/shortcuts.
                        showAttach={false}
                        showLearningArtifacts={false}
                        showQuestionBank={false}
                        showMemory={false}
                        extraItems={
                          <>
                            {capabilities.map(cap => (
                              <CapMenuItem
                                key={cap.value}
                                cap={cap}
                                selected={!generationShortcut && activeCap.value === cap.value}
                                onSelect={handleSelectCapabilityFromReferenceMenu}
                              />
                            ))}
                            {onSelectGenerationShortcut
                              ? GENERATION_SHORTCUTS.map(shortcut => (
                                  <GenerationMenuItem
                                    key={shortcut.kind}
                                    shortcut={shortcut}
                                    selected={generationShortcut === shortcut.kind}
                                    onSelect={handleSelectGenerationFromReferenceMenu}
                                  />
                                ))
                              : null}
                          </>
                        }
                        onSelectItem={key => {
                          onSetReferenceMenuOpen(false)
                          if (key === 'attach') handlePickFiles()
                          else if (key === 'learning_artifacts') onSelectLearningArtifactPicker?.()
                          else if (key === 'question_bank') onSelectQuestionBankPicker()
                          else if (key === 'memory') onSelectMemoryPicker()
                        }}
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              <div className="ml-auto flex shrink-0 items-center gap-1.5">
                <TutorProfileButton />
                <ModelSelector
                  options={llmOptions}
                  activeDefault={activeLLMDefault}
                  value={llmSelection}
                  loading={llmOptionsLoading}
                  error={llmOptionsError}
                  onChange={onSelectLLM}
                />

                <button
                  type="button"
                  onClick={recorder.toggle}
                  disabled={recorder.state === 'transcribing' || isStreaming}
                  className={`group relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] transition-[background-color,color,transform] duration-150 active:scale-90 disabled:opacity-40 ${
                    recorder.state === 'recording'
                      ? 'bg-red-500/15 text-red-500'
                      : 'text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]'
                  }`}
                  aria-label={
                    recorder.state === 'recording' ? t('Stop recording') : t('Record voice')
                  }
                  title={
                    recorder.error ||
                    (recorder.state === 'recording' ? t('Stop recording') : t('Record voice'))
                  }
                >
                  {recorder.state === 'recording' && (
                    <span className="pointer-events-none absolute inset-0 rounded-[10px] border border-red-500/40 animate-pulse" />
                  )}
                  {recorder.state === 'transcribing' ? (
                    <Loader2 size={16} strokeWidth={1.9} className="animate-spin" />
                  ) : (
                    <Mic size={16} strokeWidth={1.9} />
                  )}
                </button>

                {isStreaming ? (
                  <button
                    type="button"
                    onClick={onCancelStreaming}
                    className="group relative ml-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-[var(--primary)] text-[var(--primary-foreground)] transition-[background-color,transform] duration-150 hover:bg-[var(--primary)]/90 active:scale-95"
                    aria-label={t('Stop generating')}
                    title={t('Stop generating')}
                  >
                    {/* A faint ring slowly rotates inside while streaming,
                        signalling "still working — click to cancel". Kept
                        circular (inset within the rounded square) so the
                        rotation reads as a spinner, not a tumbling box. */}
                    <span className="pointer-events-none absolute inset-[3px] rounded-full border-[1.5px] border-white/25 border-t-white/85 animate-spin opacity-90 transition-opacity group-hover:opacity-40" />
                    <Square size={10} strokeWidth={2.6} className="relative z-10 fill-current" />
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={handlePickFiles}
                      disabled={isStreaming}
                      className="relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] text-[var(--muted-foreground)] transition-[background-color,color,transform] duration-150 hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)] active:scale-90 disabled:opacity-40"
                      aria-label={t('Attach files')}
                      title={t('Attach files')}
                    >
                      <Paperclip size={16} strokeWidth={1.9} />
                      {attachments.length > 0 && (
                        <span className="absolute -right-0.5 -top-0.5 flex h-[13px] min-w-[13px] items-center justify-center rounded-full bg-[var(--primary)] px-[3px] text-[8px] font-semibold leading-none text-[var(--primary-foreground)] ring-[1.5px] ring-[var(--card)]">
                          {attachments.length}
                        </span>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={handleManualSend}
                      disabled={!(hasContent || hasReferences) || isStreaming}
                      aria-disabled={!canSend}
                      className="ml-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-[var(--primary)] text-[var(--primary-foreground)] transition-[background-color,transform,opacity] duration-150 hover:bg-[var(--primary)]/90 active:scale-95 disabled:opacity-25"
                      aria-label={t('Send')}
                    >
                      <ArrowUp size={16} strokeWidth={2.5} />
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
})
