'use client'

/**
 * F-08 whitelist renderer (WS-9B).
 *
 * Renders a backend ``PageSchema`` as a sequence of safe content cards. It is the
 * frontend half of the PageSchema protocol: only registered learning component
 * types are rendered, and every value is emitted as a plain React text node
 * (auto-escaped) — there is no ``dangerouslySetInnerHTML``, no remote component
 * loading, and no script execution path (invariant #8). Answers/rubrics are
 * server-held and structurally absent from the schema, so they can never appear
 * here (invariant #5).
 *
 * This renderer is mounted only when the backend PAGE_SCHEMA_WIRING flag is ON
 * (``output.page_schema`` present). ``PageSchemaContent`` is shared by the
 * standalone courseware tool; ``PageSchemaRenderer`` adds learning-path actions.
 * Assessment/retrieval steps keep their existing server-graded views.
 */

import { useState, type ReactNode } from 'react'
import { Check, Columns2, GitBranch, Layers, RefreshCcw, SkipForward, Timer } from 'lucide-react'
import {
  recordLearningComponentEvent,
  type CoursewareOrchestrationSummary,
  type LearningComponent,
  type PageSchema,
  type PageSchemaComponentInstance,
} from '@/lib/traittutor-api'
import MarkdownRenderer from '@/components/common/MarkdownRenderer'
import { GenerationRunTracePanel } from './GenerationRunTrace'

type ComponentEvent = Parameters<typeof recordLearningComponentEvent>[3]
type ComponentEventResult = Awaited<ReturnType<typeof recordLearningComponentEvent>>

/**
 * Structured figure projected by the courseware executor from each lesson
 * section (see ``traittutor.orchestration.executors._public_figure``). Purely
 * presentational — never answer/rubric content — and always validated by the
 * executor, so the renderer can assume the shape is well-formed.
 */
type Figure =
  | { type: 'concept_map'; title: string; nodes: Array<{ id: string; label: string; detail?: string }>; edges: Array<{ from: string; to: string; label?: string }> }
  | { type: 'flow'; title: string; steps: string[] }
  | { type: 'timeline'; title: string; points: string[] }
  | { type: 'compare'; title: string; items: Array<{ label: string; detail?: string }> }

function asFigure(value: unknown): Figure | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const type = record.type
  if (type === 'concept_map') {
    const nodes = Array.isArray(record.nodes) ? record.nodes : []
    const edges = Array.isArray(record.edges) ? record.edges : []
    if (!nodes.length) return null
    const safeNodes = nodes
      .map(node => {
        if (!node || typeof node !== 'object') return null
        const n = node as Record<string, unknown>
        const id = typeof n.id === 'string' ? n.id : ''
        const label = typeof n.label === 'string' ? n.label : ''
        if (!id || !label) return null
        return {
          id,
          label,
          detail: typeof n.detail === 'string' ? n.detail : undefined,
        }
      })
      .filter((node): node is NonNullable<typeof node> => node !== null)
    if (!safeNodes.length) return null
    const safeEdges = edges
      .map(edge => {
        if (!edge || typeof edge !== 'object') return null
        const e = edge as Record<string, unknown>
        const from = typeof e.from === 'string' ? e.from : ''
        const to = typeof e.to === 'string' ? e.to : ''
        if (!from || !to) return null
        return {
          from,
          to,
          label: typeof e.label === 'string' ? e.label : undefined,
        }
      })
      .filter((edge): edge is NonNullable<typeof edge> => edge !== null)
    return {
      type: 'concept_map',
      title: typeof record.title === 'string' ? record.title : '',
      nodes: safeNodes,
      edges: safeEdges,
    }
  }
  if (type === 'flow') {
    const steps = Array.isArray(record.steps)
      ? record.steps.filter((s): s is string => typeof s === 'string' && s.trim().length > 0)
      : []
    if (!steps.length) return null
    return { type: 'flow', title: typeof record.title === 'string' ? record.title : '', steps }
  }
  if (type === 'timeline') {
    const points = Array.isArray(record.points)
      ? record.points.filter((s): s is string => typeof s === 'string' && s.trim().length > 0)
      : []
    if (!points.length) return null
    return { type: 'timeline', title: typeof record.title === 'string' ? record.title : '', points }
  }
  if (type === 'compare') {
    const items = Array.isArray(record.items) ? record.items : []
    const safeItems = items
      .map(item => {
        if (!item || typeof item !== 'object') return null
        const i = item as Record<string, unknown>
        const label = typeof i.label === 'string' ? i.label : ''
        if (!label) return null
        return {
          label,
          detail: typeof i.detail === 'string' ? i.detail : undefined,
        }
      })
      .filter((item): item is NonNullable<typeof item> => item !== null)
    if (safeItems.length < 2) return null
    return {
      type: 'compare',
      title: typeof record.title === 'string' ? record.title : '',
      items: safeItems,
    }
  }
  return null
}

// Client-side mirror of traittutor.components.registry — the F-08 whitelist.
// Any component_type not in this set is text-downgraded; the model cannot force
// an unregistered or active surface onto the page.
const REGISTERED_COMPONENT_TYPES: ReadonlySet<string> = new Set([
  'goal_map',
  'concept_explanation',
  'worked_example',
  'visual_map',
  'video_explanation',
  'audio_explanation',
  'diagnostic_check',
  'guided_practice',
  'calibration_checkpoint',
  'retrieval_card',
  'progress_checkpoint',
  'reflection_prompt',
  'transfer_challenge',
  'review_queue',
])

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const AGENT_LABELS: Record<string, { zh: string; en: string }> = {
  material: { zh: '材料', en: 'Material' },
  instruction: { zh: '讲解', en: 'Instruction' },
  practice: { zh: '练习', en: 'Practice' },
  srl: { zh: '学习支持', en: 'SRL support' },
  visual: { zh: '视觉', en: 'Visual' },
  ui_composer: { zh: '页面组装', en: 'UI composer' },
  evaluator: { zh: '校验', en: 'Evaluator' },
}

export function AgentRunSummary({
  summary,
  zh,
}: {
  summary?: CoursewareOrchestrationSummary
  zh: boolean
}) {
  if (!summary) return null
  const statusText = (status: CoursewareOrchestrationSummary['status']) => {
    if (status === 'succeeded') return zh ? '完成' : 'Completed'
    if (status === 'degraded') return zh ? '已降级' : 'Degraded'
    return zh ? '失败' : 'Failed'
  }
  return (
    <section className="learning-card" aria-label={zh ? '多 Agent 运行状态' : 'Multi-agent run status'}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="learning-eyebrow">{zh ? '多 Agent 编排' : 'Multi-agent orchestration'}</p>
          <h3 className="mt-1 font-serif text-lg">
            {zh ? '课件组件生成与发布校验' : 'Courseware generation and release gates'}
          </h3>
        </div>
        <span className="learning-status-pill">{statusText(summary.status)}</span>
      </div>
      <ol className="mt-4 flex flex-wrap gap-2">
        {summary.agents.map(agent => {
          const label = AGENT_LABELS[agent.task_id]
          return (
            <li
              key={agent.task_id}
              className="rounded-md border border-[var(--border)] px-3 py-2 text-xs"
            >
              <span className="font-medium">{label ? (zh ? label.zh : label.en) : agent.task_id}</span>
              <span className="learning-copy-muted ml-2">{statusText(agent.status)}</span>
              {agent.component_count > 0 ? (
                <span className="learning-copy-muted ml-1">· {agent.component_count}</span>
              ) : null}
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function itemToText(item: unknown): string {
  if (typeof item === 'string') return item
  if (typeof item === 'number' || typeof item === 'boolean') return String(item)
  if (item && typeof item === 'object') {
    const record = item as Record<string, unknown>
    const label = record.label ?? record.text ?? record.title ?? record.name ?? record.id
    if (typeof label === 'string') return label
    try {
      return JSON.stringify(item)
    } catch {
      return ''
    }
  }
  return ''
}

function asTextList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(itemToText).filter(entry => entry.length > 0)
}

/**
 * Defense-in-depth client scheme gate for ``media_url`` (review W2). The backend
 * PageSchema validator is the primary guard and rejects ``javascript:`` /
 * ``data:text/html`` / SVG / ``<script>``; if that gate is ever loosened the
 * client must still refuse to point an ``<img>`` at an off-policy URL. Only
 * http(s), protocol-relative, absolute/relative paths, and non-SVG
 * ``data:image/*;base64`` reach the DOM. SVG is blocked because it can carry
 * executable ``<script>`` that a plaintext scan may miss when base64-encoded.
 */
const DATA_IMAGE_OK = /^data:image\/(?!svg)[a-z0-9.+-]+;base64,/i
const SCHEME_LIKE = /^[a-z][a-z0-9+.-]*:/i

function isSafeMediaUrl(url: string): boolean {
  const value = url.trim()
  if (!value) return false
  const lowered = value.toLowerCase()
  if (lowered.startsWith('data:')) return DATA_IMAGE_OK.test(value)
  // Reject any non-http scheme (javascript:/vbscript:/file:/blob:/...). Relative
  // paths and protocol-relative URLs ("//host") have no scheme and are allowed.
  if (SCHEME_LIKE.test(value) && !lowered.startsWith('http')) return false
  return true
}

/** Render media_url with a localized text fallback on load failure or an
 *  off-policy URL (F-08: a failed media dependency must not block the core task;
 *  invariants #8/#11). */
function SafeMedia({ url, alt, zh }: { url: string; alt: string; zh: boolean }) {
  const [failed, setFailed] = useState(false)
  const fallback = alt || (zh ? '图片不可用。' : 'Image unavailable.')
  if (!isSafeMediaUrl(url) || failed) {
    return <p className="learning-copy-muted text-xs">{fallback}</p>
  }
  return (
    <img
      src={url}
      alt={alt}
      loading="lazy"
      className="h-auto w-full rounded-md object-contain"
      onError={() => setFailed(true)}
    />
  )
}

function SafeVideo({ url, alt, zh }: { url: string; alt: string; zh: boolean }) {
  const [failed, setFailed] = useState(false)
  const fallback = alt || (zh ? '视频不可用。' : 'Video unavailable.')
  if (!isSafeMediaUrl(url) || failed) {
    return <p className="learning-copy-muted text-xs">{fallback}</p>
  }
  return (
    <figure className="space-y-2">
      <video
        src={url}
        controls
        preload="metadata"
        aria-label={fallback}
        className="aspect-video h-auto w-full rounded-md bg-black object-contain"
        onError={() => setFailed(true)}
      />
      <figcaption className="learning-copy-muted text-xs">{fallback}</figcaption>
    </figure>
  )
}

function ListBlock({ label, items }: { label: string; items: string[] }): ReactNode {
  if (!items.length) return null
  return (
    <div className="mt-3">
      <p className="learning-meta text-[10px]">{label}</p>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm leading-6">
        {items.map((entry, index) => (
          <li key={index}>{entry}</li>
        ))}
      </ul>
    </div>
  )
}

const FIGURE_LABEL = {
  concept_map: { zh: '概念关系图', en: 'Concept map' },
  flow: { zh: '流程', en: 'Process' },
  timeline: { zh: '时间线', en: 'Timeline' },
  compare: { zh: '对比', en: 'Compare' },
} as const

/**
 * Renders a structured section figure as a compact visual card (invariant #8:
 * pure text/SVG from validated data — no HTML, no remote assets). Four shapes:
 * concept map (SVG node/edge graph), flow (numbered steps), timeline (vertical
 * sequence), and compare (side-by-side cards).
 */
function FigureView({ figure, zh }: { figure: Figure; zh: boolean }) {
  const label = FIGURE_LABEL[figure.type]
  const Icon =
    figure.type === 'concept_map'
      ? GitBranch
      : figure.type === 'flow'
        ? Layers
        : figure.type === 'timeline'
          ? Timer
          : Columns2
  return (
    <figure className="mt-4 rounded-xl border border-[var(--border)] bg-[var(--card)]/60 p-4">
      <figcaption className="flex flex-wrap items-center gap-2">
        <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-[var(--primary)]/10 text-[var(--primary)]">
          <Icon size={14} aria-hidden />
        </span>
        <span className="learning-meta text-[10px] uppercase tracking-[0.12em]">
          {zh ? label.zh : label.en}
        </span>
        {figure.title ? (
          <span className="font-serif text-sm text-[var(--foreground)]">{figure.title}</span>
        ) : null}
      </figcaption>
      <div className="mt-3">
        {figure.type === 'concept_map' ? (
          <ConceptMapFigure figure={figure} />
        ) : figure.type === 'flow' ? (
          <FlowFigure steps={figure.steps} />
        ) : figure.type === 'timeline' ? (
          <TimelineFigure points={figure.points} />
        ) : (
          <CompareFigure items={figure.items} />
        )}
      </div>
    </figure>
  )
}

function ConceptMapFigure({
  figure,
}: {
  figure: Extract<Figure, { type: 'concept_map' }>
}) {
  const width = 440
  const height = 250
  const cx = width / 2
  const cy = height / 2
  const rx = width / 2 - 84
  const ry = height / 2 - 52
  const positions = new Map<string, { x: number; y: number }>()
  figure.nodes.forEach((node, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / figure.nodes.length
    positions.set(node.id, {
      x: cx + rx * Math.cos(angle),
      y: cy + ry * Math.sin(angle),
    })
  })
  const shortLabel = (label: string) =>
    label.length > 16 ? `${label.slice(0, 15)}…` : label
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={figure.title || 'Concept map'}
    >
      {figure.edges.map((edge, index) => {
        const from = positions.get(edge.from)
        const to = positions.get(edge.to)
        if (!from || !to) return null
        const midX = (from.x + to.x) / 2
        const midY = (from.y + to.y) / 2
        return (
          <g key={index}>
            <line
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
              style={{ stroke: 'var(--border)' }}
              strokeWidth={1.5}
            />
            {edge.label ? (
              <text
                x={midX}
                y={midY - 6}
                textAnchor="middle"
                style={{ fill: 'var(--muted-foreground)' }}
                fontSize={10}
              >
                {edge.label}
              </text>
            ) : null}
          </g>
        )
      })}
      {figure.nodes.map(node => {
        const pos = positions.get(node.id)
        if (!pos) return null
        return (
          <g key={node.id}>
            <circle
              cx={pos.x}
              cy={pos.y}
              r={27}
              style={{ fill: 'var(--primary)' }}
              fillOpacity={0.08}
              stroke="var(--primary)"
              strokeOpacity={0.35}
              strokeWidth={1.5}
            />
            <text
              x={pos.x}
              y={pos.y}
              textAnchor="middle"
              dominantBaseline="middle"
              style={{ fill: 'var(--foreground)' }}
              fontSize={11}
              fontWeight={600}
            >
              {shortLabel(node.label)}
            </text>
            <title>
              {node.label}
              {node.detail ? ` — ${node.detail}` : ''}
            </title>
          </g>
        )
      })}
    </svg>
  )
}

function FlowFigure({ steps }: { steps: string[] }) {
  return (
    <ol className="space-y-2.5">
      {steps.map((step, index) => (
        <li key={index} className="flex items-start gap-3">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-[var(--primary)]/10 text-[11px] font-semibold text-[var(--primary)]">
            {index + 1}
          </span>
          <span className="text-sm leading-6 text-[var(--foreground)]">{step}</span>
        </li>
      ))}
    </ol>
  )
}

function TimelineFigure({ points }: { points: string[] }) {
  return (
    <ol className="ml-1.5 space-y-3 border-l border-[var(--border)] pl-5">
      {points.map((point, index) => (
        <li key={index} className="relative">
          <span
            className="absolute -left-[26px] top-1.5 h-2 w-2 rounded-full bg-[var(--primary)]"
            aria-hidden
          />
          <p className="text-sm leading-6 text-[var(--foreground)]">{point}</p>
        </li>
      ))}
    </ol>
  )
}

function CompareFigure({
  items,
}: {
  items: Array<{ label: string; detail?: string }>
}) {
  return (
    <div className={`grid gap-2 ${items.length >= 3 ? 'sm:grid-cols-3' : 'sm:grid-cols-2'}`}>
      {items.map((item, index) => (
        <div
          key={index}
          className="rounded-lg border border-[var(--border)]/70 bg-[var(--background)]/50 p-3"
        >
          <p className="text-[13px] font-semibold text-[var(--foreground)]">{item.label}</p>
          {item.detail ? (
            <p className="mt-1 text-xs leading-5 text-[var(--muted-foreground)]">{item.detail}</p>
          ) : null}
        </div>
      ))}
    </div>
  )
}

/**
 * Goal map — the mission that opens a learning path. It shows the learning
 * goal (title) and the knowledge points (milestones) as a vertical route, not
 * the internal component arrangement. No complete/continue action: reading it
 * is the whole point, and the component completes on open (see
 * LearningCanvas), so the footer is a quiet note instead of a button.
 */
function GoalMapView({
  instance,
  zh,
}: {
  instance: PageSchemaComponentInstance
  zh: boolean
}) {
  const props = instance.props
  const title = asString(props.title)
  const milestones = asTextList(props.milestones)
  const body = asString(props.body_markdown)
  return (
    <div className="goal-map">
      <p className="learning-eyebrow">
        {zh ? '本轮学习目标' : 'This learning goal'}
      </p>
      {title ? <h3 className="goal-map__title">{title}</h3> : null}
      {body ? (
        <p className="learning-copy-muted mt-3 text-sm leading-7">{body}</p>
      ) : null}
      {milestones.length ? (
        <ol className="goal-map__route">
          {milestones.map((point, index) => (
            <li key={index} className="goal-map__node">
              <span className="goal-map__marker" aria-hidden />
              <span className="goal-map__point">{point}</span>
            </li>
          ))}
        </ol>
      ) : null}
      <p className="goal-map__foot">
        {zh
          ? '先看清要掌握什么，再进入下面的讲解与练习。'
          : 'See what to master, then continue to the explanation and practice below.'}
      </p>
    </div>
  )
}

function StimulusBlock({ value }: { value: unknown }): ReactNode {
  const items = asTextList(value)
  if (items.length) {
    return (
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-6">
        {items.map((entry, index) => (
          <li key={index}>{entry}</li>
        ))}
      </ul>
    )
  }
  const text = asString(value)
  if (!text) return null
  return <p className="mt-3 text-sm leading-7">{text}</p>
}

function InstanceContent({
  instance,
  zh,
}: {
  instance: PageSchemaComponentInstance
  zh: boolean
}): ReactNode {
  const props = instance.props
  const body = asString(props.body_markdown)
  const prompt = asString(props.prompt)
  const front = asString(props.front)
  const hint = asString(props.hint)
  const stimulus = props.stimulus
  const mediaUrl = asString(props.media_url)
  const mediaAlt = asString(props.a11y_label) || asString(props.title)
  const figure = asFigure(props.figure)

  return (
    <>
      {/* key by media_url (review W3): a region is keyed by region_id, so a
          regenerated image for the same region must remount SafeMedia to reset
          its failed flag — otherwise it stays stuck on the degraded fallback. */}
      {mediaUrl ? (
        instance.component_type === 'video_explanation' ? (
          <SafeVideo key={mediaUrl} url={mediaUrl} alt={mediaAlt} zh={zh} />
        ) : (
          <SafeMedia key={mediaUrl} url={mediaUrl} alt={mediaAlt} zh={zh} />
        )
      ) : null}
      {prompt ? <p className="text-sm leading-7">{prompt}</p> : null}
      {front ? <p className="text-base leading-7">{front}</p> : null}
      {body ? (
        <div className="space-y-3 text-sm leading-7">
          {/* Markdown, not raw paragraphs: lesson core_content may carry lists,
              tables, and mermaid diagrams. HTML stays escaped and remote images
              are dropped by the renderer (invariant #6/#8). */}
          <MarkdownRenderer content={body} variant="compact" enableMermaid />
        </div>
      ) : null}
      {figure ? <FigureView figure={figure} zh={zh} /> : null}
      <ListBlock label={zh ? '步骤' : 'Steps'} items={asTextList(props.steps)} />
      <ListBlock label={zh ? '里程碑' : 'Milestones'} items={asTextList(props.milestones)} />
      <ListBlock label={zh ? '节点' : 'Nodes'} items={asTextList(props.nodes)} />
      <ListBlock label={zh ? '关系' : 'Edges'} items={asTextList(props.edges)} />
      <ListBlock label={zh ? '待复习' : 'Review items'} items={asTextList(props.item_refs)} />
      {stimulus !== undefined ? <StimulusBlock value={stimulus} /> : null}
      {hint ? <p className="learning-copy-muted mt-3 text-xs">{hint}</p> : null}
    </>
  )
}

function PageActionBar({
  component,
  zh,
  runId,
  busy,
  onRegenerate,
  onEvent,
}: {
  component: LearningComponent
  zh: boolean
  runId: string
  busy: boolean
  onRegenerate: () => void
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  const [pending, setPending] = useState<null | ComponentEvent['action']>(null)
  const [error, setError] = useState<string | null>(null)

  // review W4: block a double-click / concurrent fire and surface transport
  // failures instead of discarding the promise with `void onEvent(...)`.
  const fire = async (event: ComponentEvent) => {
    if (pending) return
    setError(null)
    setPending(event.action)
    try {
      await onEvent(event)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : zh
            ? '操作失败，请重试。'
            : 'Action failed; please retry.'
      )
    } finally {
      setPending(null)
    }
  }

  // review I4/I5 (invariant #4): a deterministic client event_id lets the server
  // dedup a double-click or network retry of the same logical action.
  const eventId = (action: ComponentEvent['action']): string =>
    `${runId}:${component.component_id}:${action}`

  // A completed/skipped step has no actions left: completing it again would be
  // a server 409 ("Cannot complete a completed component"), so do not offer
  // the buttons at all (re-selecting a finished step from the route included).
  // The core concept explanation additionally never shows a manual complete
  // button — it completes on open, mirroring the goal map.
  if (['completed', 'skipped'].includes(component.status)) return null
  const hideComplete = component.component_type === 'concept_explanation'

  return (
    <div className="learning-action-bar mt-6 flex flex-wrap gap-2 border-t pt-4">
      {!hideComplete ? (
        <button
          onClick={() =>
            void fire({ event_id: eventId('complete'), action: 'complete', replan: false })
          }
          disabled={pending !== null || busy}
          className="learning-button learning-button--primary"
        >
          <Check size={14} />
          {pending === 'complete'
            ? zh
              ? '处理中…'
              : 'Working…'
            : zh
              ? '完成并继续'
              : 'Complete and continue'}
        </button>
      ) : null}
      {!component.required ? (
        <button
          onClick={() => void fire({ event_id: eventId('skip'), action: 'skip', replan: false })}
          disabled={pending !== null || busy}
          className="learning-button learning-button--secondary"
        >
          <SkipForward size={14} />
          {zh ? '跳过' : 'Skip'}
        </button>
      ) : null}
      <button
        onClick={onRegenerate}
        disabled={pending !== null || busy}
        className="learning-button learning-button--secondary"
      >
        <RefreshCcw size={14} />
        {busy
          ? zh
            ? '生成中…'
            : 'Generating…'
          : zh
            ? '换一种解释'
            : 'Explain differently'}
      </button>
      {error ? (
        <p
          role="alert"
          className="learning-copy-muted ml-auto self-center text-xs text-red-600 dark:text-red-400"
        >
          {error}
        </p>
      ) : null}
    </div>
  )
}

export function PageSchemaContent({
  schema,
  zh,
  onRegenerate,
  busy = false,
}: {
  schema: PageSchema
  zh: boolean
  onRegenerate?: () => void
  busy?: boolean
}) {
  return (
    <div className="space-y-5">
      <GenerationRunTracePanel
        key={schema.generation_run_id}
        generationRunId={schema.generation_run_id}
        zh={zh}
        onRegenerate={onRegenerate}
        busy={busy}
      />
      {schema.regions.map(region => {
        const instance = region.component ?? null
        if (instance === null) {
          return (
            <section key={region.region_id} className="learning-card">
              {region.heading ? <h3 className="font-serif text-lg">{region.heading}</h3> : null}
            </section>
          )
        }
        if (!REGISTERED_COMPONENT_TYPES.has(instance.component_type)) {
          // F-08: unregistered/unknown component_type -> deterministic text downgrade.
          return (
            <section key={region.region_id} className="learning-card" role="status">
              <p className="learning-eyebrow">{zh ? '文字版本' : 'Text-only version'}</p>
              <h3 className="mt-2 font-serif text-lg">
                {zh
                  ? '该组件类型未注册，已降级为文字'
                  : 'Unsupported component type; showing text only'}
              </h3>
              <p className="learning-copy-muted mt-2 text-sm leading-7">
                {asString(instance.props.body_markdown) ||
                  asString(instance.props.title) ||
                  (zh ? '此内容无法安全展示。' : 'This content could not be shown safely.')}
              </p>
            </section>
          )
        }
        const title = region.heading ?? asString(instance.props.title)
        if (instance.component_type === 'goal_map') {
          return (
            <article key={region.region_id} className="learning-card learning-card--large">
              <GoalMapView instance={instance} zh={zh} />
            </article>
          )
        }
        return (
          <article key={region.region_id} className="learning-card">
            {title ? <h3 className="font-serif text-lg">{title}</h3> : null}
            <div className={title ? 'mt-3' : ''}>
              <InstanceContent instance={instance} zh={zh} />
            </div>
          </article>
        )
      })}
    </div>
  )
}

export function PageSchemaRenderer({
  schema,
  orchestration,
  component,
  zh,
  busy = false,
  onRegenerate,
  onEvent,
}: {
  schema: PageSchema
  orchestration?: CoursewareOrchestrationSummary
  component: LearningComponent
  zh: boolean
  busy?: boolean
  onRegenerate: () => void
  onEvent: (event: ComponentEvent) => Promise<ComponentEventResult>
}) {
  return (
    <div className="space-y-5">
      {component.component_type !== 'goal_map' ? (
        <AgentRunSummary summary={orchestration} zh={zh} />
      ) : null}
      <PageSchemaContent schema={schema} zh={zh} onRegenerate={onRegenerate} busy={busy} />
      {component.component_type !== 'goal_map' ? (
        <PageActionBar
          component={component}
          zh={zh}
          runId={schema.generation_run_id}
          busy={busy}
          onRegenerate={onRegenerate}
          onEvent={onEvent}
        />
      ) : null}
    </div>
  )
}
