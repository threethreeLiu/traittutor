'use client'

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { ArrowUp, FileSearch, Loader2, MessageCircle, Mic, Minimize2 } from 'lucide-react'
import RichMarkdownRenderer from '@/components/common/RichMarkdownRenderer'
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder'
import type { ResearchBrief, ResearchReport, ResearchRun } from '@/lib/research-workspace-api'

const ACTIVE_RUN_STATES = new Set(['queued', 'running', 'pausing', 'paused', 'cancelling'])

const SUGGESTIONS = {
  zh: ['补充最新研究进展', '比较来源中的不同观点', '检查当前结论的证据缺口'],
  en: [
    'Add the latest research developments',
    'Compare differing views across sources',
    'Check the evidence gaps in the current findings',
  ],
}

interface Props {
  workspaceTitle: string
  brief: ResearchBrief | null
  reports: ResearchReport[]
  runs: ResearchRun[]
  sourceCount: number
  busy: boolean
  disabled: boolean
  zh: boolean
  onMinimize: () => void
  onFollowUp: (report: ResearchReport, question: string) => Promise<void>
}

export default function ResearchAssistant({
  workspaceTitle,
  brief,
  reports,
  runs,
  sourceCount,
  busy,
  disabled,
  zh,
  onMinimize,
  onFollowUp,
}: Props) {
  const [input, setInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const latestReport = [...reports].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
  const activeRun = [...runs]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .find(run => ACTIVE_RUN_STATES.has(run.status))
  const latestReportRun = latestReport
    ? runs.find(run => run.run_id === latestReport.run_id)
    : undefined
  const reportAnswersCurrentBrief = Boolean(brief && latestReportRun?.brief_id === brief.brief_id)
  const canFollowUp = Boolean(
    latestReport?.evidence_status === 'active' && !activeRun && !busy && !disabled
  )
  const recorder = useVoiceRecorder(
    useCallback((transcript: string) => {
      setInput(current => (current.trim() ? `${current.trimEnd()} ${transcript}` : transcript))
    }, [])
  )

  useEffect(() => {
    const root = scrollRef.current
    if (root) root.scrollTop = root.scrollHeight
  }, [activeRun?.status, brief?.brief_id, latestReport?.report_id])

  const submit = useCallback(
    async (value = input) => {
      const question = value.trim()
      if (!question || !latestReport || !canFollowUp || submitting) return
      setSubmitting(true)
      try {
        await onFollowUp(latestReport, question)
        setInput('')
      } finally {
        setSubmitting(false)
      }
    },
    [canFollowUp, input, latestReport, onFollowUp, submitting]
  )

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit()
  }

  const currentQuestion = brief?.question ?? null

  return (
    <section
      className="learning-assistant"
      aria-label={zh ? '研究追问助手' : 'Research follow-up assistant'}
    >
      <header className="learning-assistant__header">
        <div className="flex min-w-0 items-center gap-3">
          <span className="learning-assistant__mark" aria-hidden="true">
            <FileSearch size={15} />
          </span>
          <div className="min-w-0">
            <p className="learning-eyebrow">{zh ? '研究追问' : 'Research follow-up'}</p>
            <h2 className="mt-1 truncate font-serif text-lg">
              {zh ? '深化当前研究' : 'Deepen this research'}
            </h2>
          </div>
        </div>
        <button
          type="button"
          onClick={onMinimize}
          className="learning-icon-button"
          aria-label={zh ? '最小化研究助手' : 'Minimize research assistant'}
          title={zh ? '最小化' : 'Minimize'}
        >
          <Minimize2 size={15} />
        </button>
      </header>

      <div className="learning-assistant__context" title={workspaceTitle}>
        <span className="learning-assistant__context-dot" />
        <span className="truncate">
          {zh ? '当前研究：' : 'Researching: '}
          {workspaceTitle}
        </span>
      </div>

      <div ref={scrollRef} className="learning-assistant__messages" aria-live="polite">
        {latestReport ? (
          <>
            {reportAnswersCurrentBrief && currentQuestion ? (
              <ResearchQuestionMessage
                question={currentQuestion}
                followUp={Boolean(brief?.continuation)}
                zh={zh}
              />
            ) : null}
            <ResearchReportMessage report={latestReport} zh={zh} />
            {!reportAnswersCurrentBrief && brief?.continuation && currentQuestion ? (
              <ResearchQuestionMessage question={currentQuestion} followUp zh={zh} />
            ) : null}
          </>
        ) : (
          <div className="learning-assistant__welcome">
            <span className="learning-assistant__avatar" aria-hidden="true">
              <FileSearch size={18} />
            </span>
            <div>
              <p className="text-sm leading-6">
                {zh
                  ? '完成首轮研究后，可以在这里基于报告继续追问、比较来源或补充检索。'
                  : 'After the first run, continue here to question the report, compare sources, or retrieve more evidence.'}
              </p>
              <p className="learning-copy-muted mt-2 text-[11px] leading-5">
                {zh
                  ? '首轮问题与来源范围仍在研究简报中设置。'
                  : 'Set the initial question and source scope in the research brief.'}
              </p>
            </div>
          </div>
        )}

        {latestReport && !activeRun && canFollowUp ? (
          <div className="mt-4 flex flex-wrap gap-2">
            {SUGGESTIONS[zh ? 'zh' : 'en'].map(suggestion => (
              <button
                key={suggestion}
                type="button"
                onClick={() => void submit(suggestion)}
                className="learning-assistant__suggestion"
              >
                {suggestion}
              </button>
            ))}
          </div>
        ) : null}

        {activeRun || submitting ? (
          <div className="learning-assistant__thinking" role="status">
            <span className="learning-assistant__thinking-dots" aria-hidden>
              <span className="learning-assistant__thinking-dot" />
              <span className="learning-assistant__thinking-dot" />
              <span className="learning-assistant__thinking-dot" />
            </span>
            {zh
              ? '正在结合已有报告与来源开展下一轮研究…'
              : 'Running the next research round from the report and its sources…'}
          </div>
        ) : null}
      </div>

      <form className="learning-assistant__composer" onSubmit={onSubmit}>
        <textarea
          value={input}
          onChange={event => setInput(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              void submit()
            }
          }}
          rows={2}
          maxLength={12000}
          disabled={!latestReport || Boolean(activeRun) || busy || disabled || submitting}
          placeholder={
            latestReport
              ? zh
                ? '追问报告、补充检索或核对证据…'
                : 'Question the report, retrieve more, or verify evidence…'
              : zh
                ? '完成首轮研究后可继续追问'
                : 'Complete the first run to ask a follow-up'
          }
          aria-label={zh ? '输入研究追问' : 'Enter a research follow-up'}
        />
        {recorder.error ? (
          <p className="px-1 text-[10px] text-red-500" role="alert">
            {recorder.error}
          </p>
        ) : null}
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={recorder.toggle}
              disabled={!canFollowUp || submitting || recorder.state === 'transcribing'}
              className={`learning-assistant__icon-tool ${recorder.state === 'recording' ? 'learning-assistant__icon-tool--recording' : ''}`}
              aria-label={
                recorder.state === 'recording'
                  ? zh
                    ? '停止录音并转写'
                    : 'Stop and transcribe'
                  : zh
                    ? '语音输入追问'
                    : 'Voice input for follow-up'
              }
              title={zh ? '语音输入' : 'Voice input'}
            >
              {recorder.state === 'transcribing' ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Mic size={14} className={recorder.state === 'recording' ? 'animate-pulse' : ''} />
              )}
            </button>
            <span className="truncate text-[10px] text-[var(--muted-foreground)]">
              {zh ? `已关联 ${sourceCount} 个研究来源` : `${sourceCount} research sources linked`}
            </span>
          </div>
          <button
            type="submit"
            disabled={!input.trim() || !canFollowUp || submitting}
            className="learning-assistant__send"
            aria-label={zh ? '提交研究追问' : 'Submit research follow-up'}
          >
            {submitting ? <Loader2 size={15} className="animate-spin" /> : <ArrowUp size={16} />}
          </button>
        </div>
      </form>
    </section>
  )
}

function ResearchQuestionMessage({
  question,
  followUp,
  zh,
}: {
  question: string
  followUp: boolean
  zh: boolean
}) {
  return (
    <div className="learning-assistant__message learning-assistant__message--user">
      <div className="min-w-0 flex-1">
        <p className="learning-meta mb-1.5 text-[8px]">
          {followUp ? (zh ? '你的追问' : 'Your follow-up') : zh ? '研究问题' : 'Research question'}
        </p>
        <p className="whitespace-pre-wrap">{question}</p>
      </div>
    </div>
  )
}

function ResearchReportMessage({ report, zh }: { report: ResearchReport; zh: boolean }) {
  return (
    <div className="learning-assistant__message learning-assistant__message--assistant">
      <span
        className="learning-assistant__avatar learning-assistant__avatar--small"
        aria-hidden="true"
      >
        <MessageCircle size={13} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="learning-meta mb-1.5 text-[8px]">
          {zh ? '研究助手 · 最新报告' : 'Research assistant · latest report'}
        </p>
        <RichMarkdownRenderer content={report.body} variant="compact" />
      </div>
    </div>
  )
}
