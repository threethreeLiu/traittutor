'use client'

import { Fragment, memo, useEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronRight, Layers, Paperclip } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { setPickerOrigin } from '@/lib/picker-origin'

type SelectableReferenceKey = 'attach' | 'learning_artifacts' | 'question_bank' | 'memory'

export interface ChatReferenceSelectionCounts {
  attachments: number
  learningArtifacts: number
  questionBank: number
  memory: number
}

interface ChatReferenceMenuProps {
  variant: 'toolbar' | 'mention'
  selectedCounts: ChatReferenceSelectionCounts
  onSelectItem: (key: SelectableReferenceKey) => void
  showLearningArtifacts?: boolean
  showAttach?: boolean
  showQuestionBank?: boolean
  showMemory?: boolean
  /** Optional GPT-style action rows rendered below references. */
  extraItems?: ReactNode
}

const ITEM_ORDER: SelectableReferenceKey[] = ['attach', 'learning_artifacts', 'question_bank', 'memory']

function countFor(key: SelectableReferenceKey, counts: ChatReferenceSelectionCounts): number {
  switch (key) {
    case 'attach':
      return counts.attachments
    case 'learning_artifacts':
      return counts.learningArtifacts
    case 'question_bank':
      return counts.questionBank
    case 'memory':
      return counts.memory
    default:
      return 0
  }
}

export default memo(function ChatReferenceMenu({
  variant,
  selectedCounts,
  onSelectItem,
  showLearningArtifacts = false,
  showAttach = true,
  showQuestionBank = true,
  showMemory = true,
  extraItems,
}: ChatReferenceMenuProps) {
  const { t } = useTranslation()
  const compact = variant === 'toolbar'
  const isMention = variant === 'mention'

  const items = ITEM_ORDER.filter(
    key =>
      (showAttach || key !== 'attach') &&
      (showLearningArtifacts || key !== 'learning_artifacts') &&
      (showQuestionBank || key !== 'question_bank') &&
      (showMemory || key !== 'memory')
  ).map(key => {
    if (key === 'attach') {
      return {
        key,
        label: t('Attach files'),
        description: t('Upload images, Office docs, code & text.'),
        icon: Paperclip,
      }
    }
    if (key === 'learning_artifacts') {
      return {
        key,
        label: t('学习产物'),
        description: t('引用已生成的课件、闪卡或 Quiz。'),
        icon: Layers,
      }
    }
    if (key === 'question_bank') {
      return {
        key,
        label: t('Practice history'),
        description: t('Reuse quiz questions and review incorrect answers.'),
        icon: ChevronRight,
      }
    }
    return {
      key,
      label: t('Learning memory'),
      description: t('Choose evidence from your learning model.'),
      icon: ChevronRight,
    }
  })

  // Active row index for keyboard navigation. Only meaningful in the
  // mention variant — the toolbar variant is mouse/click driven.
  const [activeIdx, setActiveIdx] = useState(0)
  // The keydown handler closes over `activeIdx`/`items`/`onSelectItem`;
  // stash them in refs so the document-level listener identity stays
  // stable and we don't re-attach it on every render. Refs are synced
  // in an effect (not during render) to satisfy `react-hooks/refs`.
  const activeIdxRef = useRef(activeIdx)
  const itemsRef = useRef(items)
  const onSelectItemRef = useRef(onSelectItem)
  useEffect(() => {
    activeIdxRef.current = activeIdx
  }, [activeIdx])
  useEffect(() => {
    itemsRef.current = items
  }, [items])
  useEffect(() => {
    onSelectItemRef.current = onSelectItem
  }, [onSelectItem])

  // Reset to the top whenever the menu first mounts (i.e. user typed `@`
  // and the popup appeared). The parent unmounts/remounts this component
  // on each open, so a fresh `useState(0)` initial value already gives us
  // the right behavior — no extra effect needed.

  // Attach a document-level keydown so Arrow/Enter while the textarea
  // still has focus drive the menu. The textarea's own handleKeyDown
  // continues to handle Escape and submit.
  useEffect(() => {
    if (!isMention) return
    const handler = (e: KeyboardEvent) => {
      const list = itemsRef.current
      if (list.length === 0) return
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveIdx(i => (i + 1) % list.length)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveIdx(i => (i - 1 + list.length) % list.length)
      } else if (e.key === 'Enter') {
        const item = list[activeIdxRef.current]
        if (!item) return
        e.preventDefault()
        onSelectItemRef.current(item.key as SelectableReferenceKey)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [isMention])

  return (
    <div
      role={isMention ? 'listbox' : undefined}
      aria-label={isMention ? t('Reference items') : undefined}
      className={`rounded-xl border border-[var(--border)] bg-[var(--popover)] shadow-lg backdrop-blur-md ${
        compact ? 'w-[280px] py-1.5' : 'w-64 p-2'
      }`}
    >
      <div
        // Cap the popup to the on-screen area above the trigger and let the
        // list scroll instead of pushing off-screen once reference rows plus
        // the extra function rows (manual routes, capabilities, generation
        // shortcuts) outgrow it. 60vh keeps it adaptive across viewports.
        className={`${compact ? '' : 'space-y-1'} max-h-[min(60vh,28rem)] overflow-y-auto overscroll-contain`}
      >
        {items.map(({ key, label, description, icon: Icon }, idx) => {
          const count = countFor(key as SelectableReferenceKey, selectedCounts)
          const isActive = isMention && idx === activeIdx
          // Two kinds of rows, Claude-style: "attach" is a direct action
          // (opens the file dialog), everything after it opens a
          // second-level picker — those get a trailing chevron, and a
          // divider separates the two groups.
          const opensPicker = key !== 'attach'
          return (
            <Fragment key={key}>
              {idx === 1 && items[0]?.key === 'attach' && (
                <div
                  className={`border-t border-[var(--border)]/60 ${
                    compact ? 'mx-3 my-1' : 'mx-1 my-1'
                  }`}
                />
              )}
              <button
                type="button"
                role={isMention ? 'option' : undefined}
                aria-selected={isMention ? isActive : undefined}
                onMouseEnter={isMention ? () => setActiveIdx(idx) : undefined}
                onClick={e => {
                  // Record the row's rect so the fullscreen picker can expand
                  // outward from exactly this clickable box.
                  setPickerOrigin(e.currentTarget.getBoundingClientRect())
                  onSelectItem(key as SelectableReferenceKey)
                }}
                className={`flex w-full items-center gap-2.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
                  isActive ? 'bg-[var(--muted)]/60' : 'hover:bg-[var(--muted)]/40'
                } ${compact ? 'px-3.5 py-2 text-[13px]' : 'rounded-xl px-3 py-2.5 text-[13px]'}`}
              >
                <Icon
                  size={15}
                  strokeWidth={1.7}
                  className="shrink-0 text-[var(--muted-foreground)]"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-[var(--foreground)]">
                    {t(label)}
                  </span>
                  {!compact && (
                    <span className="mt-0.5 block truncate text-[11px] text-[var(--muted-foreground)]">
                      {t(description)}
                    </span>
                  )}
                </span>
                {count > 0 && (
                  <span className="shrink-0 rounded-full bg-[var(--primary)]/10 px-1.5 py-px text-[9px] font-semibold text-[var(--primary)]">
                    {count}
                  </span>
                )}
                {opensPicker && (
                  <ChevronRight
                    size={14}
                    strokeWidth={1.8}
                    className="shrink-0 text-[var(--muted-foreground)]/55"
                  />
                )}
              </button>
            </Fragment>
          )
        })}
        {extraItems ? (
          <div className={items.length > 0 ? 'mt-1 border-t border-[var(--border)]/60 pt-1' : ''}>
            {extraItems}
          </div>
        ) : null}
      </div>
    </div>
  )
})
