'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, Loader2, RefreshCcw, Workflow } from 'lucide-react'
import {
  getGenerationRunTrace,
  type GenerationRunTaskStatus,
  type GenerationRunTrace,
  type GenerationRunStatus,
} from '@/lib/generation-run-api'

type TraceState =
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'ready'; trace: GenerationRunTrace }

const TASK_LABELS: Record<string, { zh: string; en: string }> = {
  material: { zh: '材料', en: 'Material' },
  instruction: { zh: '讲解', en: 'Instruction' },
  practice: { zh: '练习', en: 'Practice' },
  srl: { zh: '学习支持', en: 'SRL support' },
  visual: { zh: '视觉', en: 'Visual' },
  ui_composer: { zh: '页面组装', en: 'UI composer' },
  evaluator: { zh: '校验', en: 'Evaluator' },
}

const VALIDATION_LABELS: Record<string, { zh: string; en: string }> = {
  component_schema: { zh: '组件安全与结构', en: 'Component safety and schema' },
  source_attribution: { zh: '来源归因', en: 'Source attribution' },
  concept_version: { zh: '概念版本一致性', en: 'Concept version consistency' },
  language_constraint: { zh: '语言约束', en: 'Language constraint' },
  evaluator_unavailable: { zh: '校验器不可用', en: 'Evaluator unavailable' },
  validation_failed: { zh: '发布校验', en: 'Release validation' },
}

const DEGRADATION_LABELS: Record<string, { zh: string; en: string }> = {
  run_degraded: { zh: '本次生成已降级', en: 'Run degraded' },
  task_degraded: { zh: '部分任务已降级', en: 'Task degraded' },
  task_failed: { zh: '部分任务失败', en: 'Task failed' },
  validation_not_passed: { zh: '发布校验未完全通过', en: 'Validation did not fully pass' },
}

function localizedLabel(
  value: string,
  labels: Record<string, { zh: string; en: string }>,
  zh: boolean,
  fallback: { zh: string; en: string }
): string {
  const label = labels[value]
  const publicLabel = label ?? fallback
  return zh ? publicLabel.zh : publicLabel.en
}

function taskLabel(taskId: string, zh: boolean): string {
  return localizedLabel(taskId, TASK_LABELS, zh, { zh: '任务', en: 'Task' })
}

function statusLabel(status: GenerationRunStatus | GenerationRunTaskStatus, zh: boolean): string {
  const labels: Record<string, { zh: string; en: string }> = {
    pending: { zh: '等待中', en: 'Pending' },
    running: { zh: '运行中', en: 'Running' },
    succeeded: { zh: '完成', en: 'Completed' },
    degraded: { zh: '已降级', en: 'Degraded' },
    failed: { zh: '失败', en: 'Failed' },
  }
  return localizedLabel(status, labels, zh, { zh: '未知状态', en: 'Unknown status' })
}

function validationStatusLabel(status: GenerationRunTrace['validation']['status'], zh: boolean) {
  const labels: Record<string, { zh: string; en: string }> = {
    passed: { zh: '通过', en: 'Passed' },
    repair: { zh: '需要修订', en: 'Needs repair' },
    degraded: { zh: '降级通过', en: 'Degraded' },
    failed: { zh: '未通过', en: 'Failed' },
    unavailable: { zh: '暂不可用', en: 'Unavailable' },
  }
  return localizedLabel(status, labels, zh, { zh: '校验状态未知', en: 'Unknown validation' })
}

function formatDuration(value: number | null, zh: boolean): string {
  if (value === null) return zh ? '暂不可用' : 'Unavailable'
  if (value < 1_000) return `${value} ms`
  return `${(value / 1_000).toFixed(1)} s`
}

export function GenerationRunTracePanel({
  generationRunId,
  zh,
  onRegenerate,
  busy = false,
}: {
  generationRunId: string
  zh: boolean
  onRegenerate?: () => void
  busy?: boolean
}) {
  const [state, setState] = useState<TraceState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    void getGenerationRunTrace(generationRunId, controller.signal)
      .then(trace => {
        if (!controller.signal.aborted) setState({ kind: 'ready', trace })
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ kind: 'error' })
      })
    return () => controller.abort()
  }, [generationRunId])

  const retry = async () => {
    setState({ kind: 'loading' })
    try {
      const trace = await getGenerationRunTrace(generationRunId)
      setState({ kind: 'ready', trace })
    } catch {
      setState({ kind: 'error' })
    }
  }

  const label = zh ? '生成过程' : 'Generation trace'
  if (state.kind === 'loading') {
    return (
      <section
        data-testid="generation-run-trace"
        className="learning-card"
        aria-busy="true"
        aria-label={zh ? '正在加载生成过程' : 'Loading generation trace'}
      >
        <div className="flex items-center gap-2 text-sm text-[var(--muted-foreground)]">
          <RefreshCcw aria-hidden="true" className="h-4 w-4 animate-spin" />
          {zh ? '正在读取任务图、预算和校验状态' : 'Loading task graph, budget, and validation'}
        </div>
      </section>
    )
  }

  if (state.kind === 'error') {
    return (
      <section
        data-testid="generation-run-trace"
        className="learning-card border-[var(--destructive)]/30"
        aria-label={label}
      >
        <div role="alert" className="flex items-start gap-3">
          <AlertTriangle
            aria-hidden="true"
            className="mt-0.5 h-4 w-4 text-[var(--destructive)]"
          />
          <div>
            <h3 className="font-medium">
              {zh ? '生成过程暂时无法读取' : 'Generation trace unavailable'}
            </h3>
            <p className="learning-copy-muted mt-1 text-xs">
              {zh
                ? '课件内容仍可使用；重新生成后即可显示完整内容。'
                : 'The lesson remains available. Regenerate to show the full content.'}
            </p>
            {onRegenerate ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  // Regenerate the component AND re-fetch this run record, so
                  // the panel leaves the error state instead of feeling dead.
                  onRegenerate()
                  void retry()
                }}
                className="learning-button learning-button--secondary mt-3 px-3 py-2 text-xs"
              >
                {busy ? (
                  <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCcw aria-hidden="true" className="h-3.5 w-3.5" />
                )}
                {busy ? (zh ? '正在生成' : 'Generating') : zh ? '重新生成' : 'Regenerate'}
              </button>
            ) : null}
          </div>
        </div>
      </section>
    )
  }

  const trace = state.trace
  if (trace.graph_status === 'unavailable') {
    return (
      <section data-testid="generation-run-trace" className="learning-card" aria-label={label}>
        <div className="flex items-start gap-3" role="status">
          <Workflow aria-hidden="true" className="mt-0.5 h-4 w-4 text-amber-600" />
          <div>
            <h3 className="font-medium">
              {zh ? '任务图暂不可用' : 'Task graph unavailable'}
            </h3>
            <p className="learning-copy-muted mt-1 text-xs">
              {zh
                ? '这是较早的运行记录，课件内容不受影响。'
                : 'This is an older run record. The lesson content is unaffected.'}
            </p>
            <RetryButton zh={zh} onRetry={retry} label="reload" />
          </div>
        </div>
      </section>
    )
  }

  return <GenerationRunTraceView trace={trace} zh={zh} />
}

export function GenerationRunTraceView({
  trace,
  zh,
}: {
  trace: GenerationRunTrace
  zh: boolean
}) {
  const label = zh ? '生成过程' : 'Generation trace'
  return (
    <section data-testid="generation-run-trace" className="learning-card" aria-label={label}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="learning-eyebrow">{zh ? '可回查运行记录' : 'Inspect the run'}</p>
          <h3 className="mt-1 flex items-center gap-2 font-serif text-lg">
            <Workflow aria-hidden="true" className="h-4 w-4 text-[var(--primary)]" />
            {label}
          </h3>
        </div>
        <span className="learning-status-pill">{statusLabel(trace.status, zh)}</span>
      </div>

      <dl className="mt-4 grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label={zh ? '计划预算' : 'Planned budget'}
          value={formatDuration(trace.budget.total_planned_budget_ms, zh)}
        />
        <Metric
          label={zh ? '聚合超时上限' : 'Aggregate timeout'}
          value={formatDuration(trace.budget.total_timeout_ms, zh)}
        />
        <Metric
          label={zh ? '实际耗时' : 'Elapsed time'}
          value={formatDuration(trace.budget.elapsed_ms, zh)}
        />
        <Metric
          label={zh ? '重试上限' : 'Retry limit'}
          value={
            trace.budget.total_retry_limit === null
              ? zh
                ? '暂不可用'
                : 'Unavailable'
              : String(trace.budget.total_retry_limit)
          }
        />
      </dl>

      <div className="mt-5">
        <p className="learning-meta">{zh ? '任务节点' : 'Task nodes'}</p>
        <ol className="mt-2 grid gap-2 md:grid-cols-2">
          {trace.nodes.map(node => (
            <li key={node.task_id} className="rounded-md border border-[var(--border)] p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{taskLabel(node.task_id, zh)}</span>
                <span className="learning-copy-muted">{statusLabel(node.status, zh)}</span>
              </div>
              <p className="learning-copy-muted mt-2">
                {zh ? '依赖：' : 'Depends on: '}
                {node.depends_on.length
                  ? node.depends_on.map(item => taskLabel(item, zh)).join(' · ')
                  : zh
                    ? '无'
                    : 'None'}
              </p>
              <p className="learning-copy-muted mt-1">
                {zh ? '安全输入引用：' : 'Safe input refs: '}
                {node.input_refs.length}
                {node.redacted_input_ref_count
                  ? zh
                    ? `；${node.redacted_input_ref_count} 条已隐藏`
                    : `; ${node.redacted_input_ref_count} hidden`
                  : ''}
              </p>
            </li>
          ))}
        </ol>
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <TraceList
          icon={<CheckCircle2 aria-hidden="true" className="h-4 w-4" />}
          title={`${zh ? '校验' : 'Validation'} · ${validationStatusLabel(trace.validation.status, zh)}`}
          empty={zh ? '没有公开校验问题' : 'No public validation findings'}
          items={trace.validation.category_codes.map(code =>
            localizedLabel(code, VALIDATION_LABELS, zh, {
              zh: '其他公开校验项',
              en: 'Other public validation',
            })
          )}
        />
        <TraceList
          icon={<Clock3 aria-hidden="true" className="h-4 w-4" />}
          title={zh ? '降级状态' : 'Degradation'}
          empty={zh ? '本次运行没有降级' : 'No degradation recorded'}
          items={trace.degradation_codes.map(code =>
            localizedLabel(code, DEGRADATION_LABELS, zh, {
              zh: '其他降级状态',
              en: 'Other degradation',
            })
          )}
        />
      </div>
    </section>
  )
}

function RetryButton({
  zh,
  onRetry,
  label = 'retry',
}: {
  zh: boolean
  onRetry: () => void
  label?: 'retry' | 'reload'
}) {
  return (
    <button
      type="button"
      onClick={onRetry}
      className="learning-button learning-button--secondary mt-3 px-3 py-2 text-xs"
    >
      <RefreshCcw aria-hidden="true" className="h-3.5 w-3.5" />
      {label === 'reload'
        ? zh
          ? '重新读取'
          : 'Try again'
        : zh
          ? '重试'
          : 'Try again'}
    </button>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-[var(--muted)]/40 px-3 py-2">
      <dt className="learning-copy-muted">{label}</dt>
      <dd className="mt-1 font-medium">{value}</dd>
    </div>
  )
}

function TraceList({
  icon,
  title,
  items,
  empty,
}: {
  icon: ReactNode
  title: string
  items: string[]
  empty: string
}) {
  return (
    <div className="rounded-md border border-[var(--border)] p-3 text-xs">
      <h4 className="flex items-center gap-2 font-medium">
        {icon}
        {title}
      </h4>
      {items.length ? (
        <ul className="learning-copy-muted mt-2 list-disc space-y-1 pl-5">
          {items.map(item => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : (
        <p className="learning-copy-muted mt-2">{empty}</p>
      )}
    </div>
  )
}
