'use client'

import Link from 'next/link'
import {
  ArrowUp,
  Bot,
  Loader2,
  MessageCircle,
  Mic,
  Minimize2,
  Settings2,
  Sparkles,
  UserRound,
} from 'lucide-react'
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import { useUnifiedChat } from '@/context/UnifiedChatContext'
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder'
import { getTutorPersona, type TutorPersonaProfile } from '@/lib/tutor-persona-api'
import type { LearningArtifactReferencePayload, MessageItem } from '@/context/UnifiedChatContext'
import type {
  GenerateKind,
  LearningComponent,
  LearningComponentPlan,
  LearningPack,
} from '@/lib/traittutor-api'

const SUGGESTIONS = {
  zh: ['用更简单的话解释', '给我一个具体例子', '这一步和目标有什么关系？'],
  en: [
    'Explain this more simply',
    'Give me a concrete example',
    'How does this connect to my goal?',
  ],
}

export default function LearningAssistant({
  pack,
  plan,
  component,
  currentContent,
  zh,
  onMinimize,
}: {
  pack: LearningPack
  plan: LearningComponentPlan
  component: LearningComponent
  currentContent: string
  zh: boolean
  onMinimize: () => void
}) {
  const { state, sendMessage, cancelStreamingTurn, newSession, setCapability, setTools } =
    useUnifiedChat()
  const [input, setInput] = useState('')
  const [tutorEnabled, setTutorEnabled] = useState(false)
  const [tutor, setTutor] = useState<TutorPersonaProfile | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const recorder = useVoiceRecorder(
    useCallback((transcript: string) => {
      setInput(current => (current.trim() ? `${current.trimEnd()} ${transcript}` : transcript))
    }, [])
  )

  useEffect(() => {
    newSession()
    setCapability('chat')
    setTools([])
  }, [newSession, pack.pack_id, setCapability, setTools])

  useEffect(() => {
    const controller = new AbortController()
    void getTutorPersona(controller.signal)
      .then(profile => {
        if (!controller.signal.aborted) setTutor(profile)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const root = scrollRef.current
    if (root) root.scrollTop = root.scrollHeight
  }, [state.messages, state.isStreaming])

  const artifactReferences = useMemo<LearningArtifactReferencePayload[]>(
    () =>
      (['courseware', 'flashcards', 'quiz'] as GenerateKind[]).flatMap(artifactType => {
        const artifacts = pack.artifacts?.[artifactType] ?? []
        if (!artifacts.length) return []
        return [
          {
            pack_id: pack.pack_id,
            artifact_type: artifactType,
            artifact_index: artifacts.length - 1,
          },
        ]
      }),
    [pack.artifacts, pack.pack_id]
  )

  const submit = useCallback(
    (value = input) => {
      const question = value.trim()
      if (!question || state.isStreaming) return
      sendMessage(
        question,
        undefined,
        {
          product_mode: 'assist',
          learning_pack_id: pack.pack_id,
          learning_plan_id: plan.plan_id,
          learning_support: true,
          learning_canvas_excerpt: currentContent,
          use_tutor_persona: tutorEnabled,
        },
        undefined,
        undefined,
        { learningArtifactReferences: artifactReferences }
      )
      setInput('')
    },
    [
      artifactReferences,
      currentContent,
      input,
      pack.pack_id,
      plan.plan_id,
      sendMessage,
      state.isStreaming,
      tutorEnabled,
    ]
  )

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    submit()
  }

  return (
    <section
      className="learning-assistant"
      aria-label={zh ? '学习问答助手' : 'Learning Q&A assistant'}
    >
      <header className="learning-assistant__header">
        <div className="flex min-w-0 items-center gap-3">
          <span className="learning-assistant__mark" aria-hidden>
            <Sparkles size={15} />
          </span>
          <div className="min-w-0">
            <p className="learning-eyebrow">{zh ? '随学问答' : 'Ask as you learn'}</p>
            <h2 className="mt-1 truncate font-serif text-lg">
              {zh ? '问学习内容' : 'Learning assistant'}
            </h2>
          </div>
        </div>
        <button
          type="button"
          onClick={onMinimize}
          className="learning-icon-button"
          aria-label={zh ? '最小化学习助手' : 'Minimize learning assistant'}
          title={zh ? '最小化' : 'Minimize'}
        >
          <Minimize2 size={15} />
        </button>
      </header>

      <div
        className="learning-assistant__context"
        title={zh ? component.label_zh : component.label_en}
      >
        <span className="learning-assistant__context-dot" />
        <span className="truncate">
          {zh ? '正在学习：' : 'Learning now: '}
          {zh ? component.label_zh : component.label_en}
        </span>
      </div>

      <div ref={scrollRef} className="learning-assistant__messages" aria-live="polite">
        {state.messages.length ? (
          state.messages.map((message, index) => {
            // While streaming, the placeholder assistant bubble has no content
            // yet — the thinking animation below stands in for it until the
            // first token arrives.
            if (state.isStreaming && message.role === 'assistant' && !message.content.trim()) {
              return null
            }
            return (
              <AssistantMessage
                key={message.id ?? `${message.role}-${index}`}
                message={message}
                zh={zh}
              />
            )
          })
        ) : (
          <div className="learning-assistant__welcome">
            <span className="learning-assistant__avatar" aria-hidden>
              <Bot size={18} />
            </span>
            <div>
              <p className="text-sm leading-6">
                {zh
                  ? '哪里不清楚，随时问我。我会优先依据当前学习内容回答。'
                  : 'Ask whenever something is unclear. I will ground answers in the current learning content.'}
              </p>
              <p className="learning-copy-muted mt-2 text-[11px] leading-5">
                {artifactReferences.length
                  ? zh
                    ? `已连接 ${artifactReferences.length} 份当前学习产物`
                    : `${artifactReferences.length} current learning artifacts connected`
                  : zh
                    ? '当前尚无已发布产物，我会依据学习目标与当前步骤回答。'
                    : 'No published artifact yet; I will use the learning goal and current step.'}
              </p>
            </div>
          </div>
        )}

        {!state.messages.length ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {SUGGESTIONS[zh ? 'zh' : 'en'].map(suggestion => (
              <button
                key={suggestion}
                type="button"
                onClick={() => submit(suggestion)}
                className="learning-assistant__suggestion"
              >
                {suggestion}
              </button>
            ))}
          </div>
        ) : null}

        {state.isStreaming &&
        !state.messages.some(message => message.role === 'assistant' && message.content.trim()) ? (
          <div className="learning-assistant__thinking" role="status">
            <span className="learning-assistant__thinking-dots" aria-hidden>
              <span className="learning-assistant__thinking-dot" />
              <span className="learning-assistant__thinking-dot" />
              <span className="learning-assistant__thinking-dot" />
            </span>
            {zh ? '正在结合学习内容思考…' : 'Thinking with your learning content…'}
          </div>
        ) : null}
      </div>

      <form className="learning-assistant__composer" onSubmit={onSubmit}>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={event => setInput(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          rows={2}
          placeholder={zh ? '问问当前内容…' : 'Ask about this content…'}
          aria-label={zh ? '输入关于当前学习内容的问题' : 'Ask about the current learning content'}
        />
        {recorder.error ? (
          <p className="px-1 text-[10px] text-red-500" role="alert">
            {recorder.error}
          </p>
        ) : null}
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setTutorEnabled(value => !value)}
              className={`learning-assistant__tool ${tutorEnabled ? 'learning-assistant__tool--active' : ''}`}
              aria-pressed={tutorEnabled}
              title={
                tutorEnabled
                  ? zh
                    ? '移除导师表达风格'
                    : 'Remove tutor style'
                  : zh
                    ? '添加我的导师'
                    : 'Add my tutor'
              }
            >
              <UserRound size={14} />
              <span className="truncate">
                {tutorEnabled
                  ? tutor?.settings?.name || (zh ? '导师已加入' : 'Tutor added')
                  : zh
                    ? '添加导师'
                    : 'Add tutor'}
              </span>
            </button>
            {tutorEnabled ? (
              <Link
                href="/settings/tutor"
                className="learning-assistant__icon-tool"
                aria-label={zh ? '管理我的导师' : 'Manage my tutor'}
                title={zh ? '管理导师' : 'Manage tutor'}
              >
                <Settings2 size={14} />
              </Link>
            ) : null}
            <button
              type="button"
              onClick={recorder.toggle}
              disabled={recorder.state === 'transcribing' || state.isStreaming}
              className={`learning-assistant__icon-tool ${recorder.state === 'recording' ? 'learning-assistant__icon-tool--recording' : ''}`}
              aria-label={
                recorder.state === 'recording'
                  ? zh
                    ? '停止录音并转写'
                    : 'Stop and transcribe'
                  : zh
                    ? '语音输入'
                    : 'Voice input'
              }
              title={zh ? '语音输入' : 'Voice input'}
            >
              {recorder.state === 'transcribing' ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Mic size={14} className={recorder.state === 'recording' ? 'animate-pulse' : ''} />
              )}
            </button>
          </div>
          {state.isStreaming ? (
            <button
              type="button"
              onClick={cancelStreamingTurn}
              className="learning-assistant__send"
              aria-label={zh ? '停止回答' : 'Stop response'}
            >
              <span className="h-2.5 w-2.5 rounded-[2px] bg-current" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              className="learning-assistant__send"
              aria-label={zh ? '发送问题' : 'Send question'}
            >
              <ArrowUp size={16} />
            </button>
          )}
        </div>
      </form>
    </section>
  )
}

function AssistantMessage({ message, zh }: { message: MessageItem; zh: boolean }) {
  if (message.role === 'system') return null
  const assistant = message.role === 'assistant'
  return (
    <div
      className={`learning-assistant__message ${assistant ? 'learning-assistant__message--assistant' : 'learning-assistant__message--user'}`}
    >
      {assistant ? (
        <span className="learning-assistant__avatar learning-assistant__avatar--small" aria-hidden>
          <MessageCircle size={13} />
        </span>
      ) : null}
      <div className="min-w-0 flex-1">
        <p className="learning-meta mb-1.5 text-[8px]">
          {assistant ? (zh ? '学习助手' : 'Assistant') : zh ? '你' : 'You'}
        </p>
        <MarkdownRenderer content={message.content} variant="compact" />
      </div>
    </div>
  )
}
