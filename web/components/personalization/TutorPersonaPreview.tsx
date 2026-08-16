'use client'

import { Eye, Volume2 } from 'lucide-react'
import { useAppShell } from '@/context/AppShellContext'
import type { TutorPersonaContract } from '@/lib/tutor-persona-api'

export default function TutorPersonaPreview({
  preview,
  loading,
  error,
}: {
  preview: TutorPersonaContract | null
  loading: boolean
  error: string
}) {
  const { language } = useAppShell()
  const zh = language === 'zh'

  return (
    <section
      aria-labelledby="tutor-persona-preview-heading"
      aria-busy={loading}
      className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5"
    >
      <div className="flex items-center gap-2">
        <Eye className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
        <h2 id="tutor-persona-preview-heading" className="font-semibold">
          {zh ? '确定性表达预览' : 'Deterministic expression preview'}
        </h2>
      </div>

      {error ? (
        <p
          role="status"
          className="mt-4 rounded-lg border border-amber-500/35 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200"
        >
          {error}
        </p>
      ) : null}

      {loading && !preview ? (
        <div className="mt-4 space-y-3" role="status">
          <span className="sr-only">
            {zh ? '正在编译表达预览' : 'Compiling expression preview'}
          </span>
          <div className="h-20 animate-pulse rounded-lg bg-[var(--muted)]/55" />
          <div className="h-24 animate-pulse rounded-lg bg-[var(--muted)]/45" />
        </div>
      ) : null}

      {preview ? (
        <div className={loading ? 'mt-4 space-y-3 opacity-60' : 'mt-4 space-y-3'}>
          <div className="rounded-lg bg-[var(--muted)]/35 p-4">
            <p className="text-xs font-medium uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
              {zh ? '身份呈现' : 'Presentation identity'}
            </p>
            <p className="mt-2 text-lg font-semibold">{preview.identity.display_name}</p>
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {zh ? '头像' : 'Avatar'}: {label(preview.identity.avatar_ref, zh)} ·{' '}
              {zh ? '称呼' : 'Address'}:{' '}
              {preview.identity.address_terms.map(item => label(item, zh)).join(' · ')}
            </p>
          </div>

          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <PreviewItem label={zh ? '语气' : 'Tone'} value={label(preview.expression.tone, zh)} />
            <PreviewItem
              label={zh ? '反馈方式' : 'Feedback'}
              value={label(preview.expression.feedback_format, zh)}
            />
            <PreviewItem
              label={zh ? '直接程度' : 'Directness'}
              value={label(preview.expression.directness, zh)}
            />
            <PreviewItem
              label={zh ? '鼓励程度' : 'Encouragement'}
              value={label(preview.expression.encouragement_level, zh)}
            />
            <PreviewItem
              label={zh ? '幽默程度' : 'Humor'}
              value={label(preview.expression.humor_level, zh)}
            />
            <PreviewItem
              label={zh ? '表情符号' : 'Emoji'}
              value={label(preview.expression.emoji_policy, zh)}
            />
          </dl>

          <div className="flex items-center gap-2 rounded-lg border border-[var(--border)] p-3 text-sm">
            <Volume2 className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            <span>
              {zh ? '语音' : 'Voice'}: {label(preview.modality.voice_id, zh)} ·{' '}
              {preview.modality.speech_rate.toFixed(2)}×
            </span>
          </div>
        </div>
      ) : null}
    </section>
  )
}

function PreviewItem({ label: title, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[var(--border)] px-3 py-2.5">
      <dt className="text-xs text-[var(--muted-foreground)]">{title}</dt>
      <dd className="mt-0.5 font-medium">{value}</dd>
    </div>
  )
}

function label(value: string, zh: boolean): string {
  const labels: Record<string, { zh: string; en: string }> = {
    default: { zh: '默认', en: 'Default' },
    mentor: { zh: '导师', en: 'Mentor' },
    guide: { zh: '向导', en: 'Guide' },
    study_buddy: { zh: '学习伙伴', en: 'Study buddy' },
    name: { zh: '名字', en: 'Name' },
    you: { zh: '你', en: 'You' },
    learner: { zh: '学习者', en: 'Learner' },
    classmate: { zh: '同学', en: 'Classmate' },
    warm: { zh: '温暖', en: 'Warm' },
    neutral: { zh: '中性', en: 'Neutral' },
    energetic: { zh: '有活力', en: 'Energetic' },
    calm: { zh: '平静', en: 'Calm' },
    low: { zh: '低', en: 'Low' },
    medium: { zh: '中', en: 'Medium' },
    high: { zh: '高', en: 'High' },
    concise: { zh: '精简', en: 'Concise' },
    balanced: { zh: '平衡', en: 'Balanced' },
    detailed: { zh: '详细', en: 'Detailed' },
    socratic: { zh: '苏格拉底式', en: 'Socratic' },
    none: { zh: '不用', en: 'None' },
    minimal: { zh: '少量', en: 'Minimal' },
    moderate: { zh: '适量', en: 'Moderate' },
    bright: { zh: '明快', en: 'Bright' },
    steady: { zh: '稳定', en: 'Steady' },
  }
  return labels[value]?.[zh ? 'zh' : 'en'] ?? value.replaceAll('_', ' ')
}
