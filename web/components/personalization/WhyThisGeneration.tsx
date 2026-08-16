'use client'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Info } from 'lucide-react'
import Modal from '@/components/common/Modal'
import { GenerationRunTracePanel } from '@/components/learning/GenerationRunTrace'

/**
 * "Why this generation?" disclosure.
 *
 * Surfacing only the *visible* teaching evidence (goals, preferences,
 * subject evidence, weak concepts, teaching actions) — never personality
 * scores, hidden reasoning, or raw prompts.
 *
 * WS-3 (G3 / §11.1 release gate): the dialog reuses the shared
 * {@link Modal} component so it inherits the full a11y contract — Escape
 * to close, focus trap, initial focus into the dialog, and focus
 * restoration to the trigger button — instead of a bare `role=dialog`.
 */
export function WhyThisGeneration({
  snapshot,
  plan,
  generationRunId,
}: {
  snapshot?: Record<string, unknown> | null
  plan?: Record<string, unknown> | null
  generationRunId?: string | null
}) {
  const [open, setOpen] = useState(false)
  const { i18n } = useTranslation()
  const zh = i18n.language?.toLowerCase().startsWith('zh')
  const source = (plan || snapshot?.plan || {}) as { rationale?: unknown }
  const rationale = Array.isArray(source.rationale)
    ? (source.rationale as Array<{ text?: string; evidence_refs?: string[] }>)
    : []
  const degraded = Boolean(snapshot?.degraded)
  const degradationReason = String(snapshot?.degradation_reason || '')
  if (!snapshot && !plan) return null

  const title = zh ? '为什么这样生成？' : 'Why this result?'
  const description = zh
    ? '这里只展示可见教学依据：目标、偏好、学科证据、薄弱概念和教学动作；不包含人格分数、隐藏推理或原始 Prompt。'
    : 'This shows visible teaching evidence only: goals, preferences, subject evidence, weak concepts, and teaching actions. It does not expose personality scores, hidden reasoning, or raw prompts.'
  const footerNote = zh
    ? '你可以在“我的学习模型”中查看、修改或关闭相关偏好和行为推断。'
    : 'You can review, edit, or disable related preferences and behavioral inference in My Learning Model.'

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-3 py-2 text-xs hover:bg-[var(--accent)]"
      >
        <Info className="h-3.5 w-3.5" />
        {title}
      </button>
      <Modal
        isOpen={open}
        onClose={() => setOpen(false)}
        title={title}
        titleIcon={<Info className="h-4 w-4" />}
        width="lg"
        footer={<p className="text-xs text-[var(--muted-foreground)]">{footerNote}</p>}
      >
        <div className="space-y-4 p-5">
          <p className="text-sm text-[var(--muted-foreground)]">{description}</p>
          <ul className="space-y-2">
            {rationale.length ? (
              rationale.map((item, index) => (
                <li key={index} className="rounded-lg bg-[var(--muted)]/50 p-3 text-sm">
                  {item.text ||
                    (zh
                      ? '采用了当前任务的标准教学策略。'
                      : 'TraitTutor used the standard teaching strategy for this task.')}
                  {item.evidence_refs?.length ? (
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                      {zh ? '证据' : 'Evidence'} {item.evidence_refs.length} {zh ? '条' : 'refs'}
                    </p>
                  ) : null}
                </li>
              ))
            ) : (
              <li className="text-sm text-[var(--muted-foreground)]">
                {zh
                  ? '采用 TraitTutor 的标准教学策略。'
                  : 'TraitTutor used its standard teaching strategy.'}
              </li>
            )}
          </ul>
          {degraded ? (
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">
              {zh ? '已降级：' : 'Degraded: '}
              {degradationReason ||
                (zh
                  ? '个性化上下文不可用，已回退到通用教学。'
                  : 'Personalization context was unavailable, so the result fell back to general teaching.')}
            </div>
          ) : null}
          {generationRunId ? (
            <GenerationRunTracePanel generationRunId={generationRunId} zh={zh} />
          ) : null}
        </div>
      </Modal>
    </>
  )
}
