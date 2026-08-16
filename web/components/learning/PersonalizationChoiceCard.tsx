'use client'

import { Sparkles, Wrench } from 'lucide-react'

/**
 * First step of the Learn intermediate page, shown after the uploaded
 * material has been parsed and the basic Pack/Plan exists, but BEFORE the
 * LLM-driven flow (pre-assessment judgement + smart arrangement) starts.
 *
 * The learner decides whether the LLM may auto-select this path's components:
 * - "auto" runs the existing judge → arrange pipeline;
 * - "basic" skips every LLM call and starts from the deterministic plan.
 * The step itself stores nothing and changes no Pack data.
 */
export default function PersonalizationChoiceCard({
  zh,
  onAutoArrange,
  onUseBasicPath,
}: {
  zh: boolean
  onAutoArrange: () => void
  onUseBasicPath: () => void
}) {
  return (
    <section
      className="learning-card learning-card--large mx-auto w-full max-w-3xl"
      aria-labelledby="personalization-choice-title"
      data-testid="personalization-choice-card"
    >
      <p className="learning-eyebrow">{zh ? '个性化设置' : 'Personalization'}</p>
      <h2 id="personalization-choice-title" className="mt-2 font-serif text-2xl font-semibold">
        {zh ? '是否要自动根据 AI 选出个性化学习组件？' : 'Let AI choose your learning components?'}
      </h2>
      <p className="learning-copy-muted mt-2 text-sm leading-6">
        {zh
          ? '开启后，系统会先判断是否需要前置提问，再为你智能排列本次学习组件；关闭则直接使用基础组件开始学习。'
          : 'When on, we check your starting point first, then arrange this path with AI. When off, you start directly with the basic components.'}
      </p>
      <p className="learning-copy-muted mt-1 text-sm leading-6">
        {zh
          ? '排列只调整本次教学支持，不影响判分与学习证据。'
          : 'Arrangement shapes teaching support only; it never affects grading or learning evidence.'}
      </p>

      <div className="mt-6 grid gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={onAutoArrange}
          className="learning-button px-4 py-2.5"
          data-testid="personalization-choice-auto"
        >
          <Sparkles size={15} />
          {zh ? '自动选择（推荐）' : 'Auto-select (recommended)'}
        </button>
        <button
          type="button"
          onClick={onUseBasicPath}
          className="learning-button learning-button--secondary px-4 py-2.5"
          data-testid="personalization-choice-basic"
        >
          <Wrench size={15} />
          {zh ? '使用基础组件' : 'Use basic components'}
        </button>
      </div>
    </section>
  )
}
