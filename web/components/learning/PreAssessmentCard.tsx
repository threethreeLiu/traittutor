import { useMemo, useState } from 'react'
import { CheckCircle2, CircleX, Loader2 } from 'lucide-react'

import type {
  PreAssessmentDecision,
  PreAssessmentResult,
} from '@/lib/traittutor-api'

type RequiredPreAssessment = Extract<PreAssessmentDecision, { needed: true }>

export default function PreAssessmentCard({
  assessment,
  result,
  zh,
  busy,
  onSubmit,
  onSkip,
  onContinue,
}: {
  assessment: RequiredPreAssessment
  result?: PreAssessmentResult | null
  zh: boolean
  busy: boolean
  onSubmit: (answers: Array<{ question_id: string; selected_index: number }>) => void
  onSkip: () => void
  onContinue: () => void
}) {
  const [selected, setSelected] = useState<Record<string, number>>({})
  const complete = useMemo(
    () => assessment.questions.every(question => selected[question.question_id] !== undefined),
    [assessment.questions, selected]
  )
  const results = new Map(result?.results.map(item => [item.question_id, item]))

  return (
    <section
      className="learning-card learning-card--large mx-auto w-full max-w-3xl"
      aria-labelledby="pre-assessment-title"
      data-testid="pre-assessment-card"
    >
      <p className="learning-eyebrow">{zh ? '前置提问' : 'Starting-point check'}</p>
      <h2 id="pre-assessment-title" className="mt-2 font-serif text-2xl font-semibold">
        {zh ? '用几个问题确定学习起点' : 'A few questions to choose your starting point'}
      </h2>
      <p className="learning-copy-muted mt-2 text-sm leading-6">
        {zh
          ? '这些回答只用于排列本次教学组件，不进入 BKT、错题或能力判断。'
          : 'These answers only arrange this path. They do not update BKT or create an ability judgement.'}
      </p>

      <div className="mt-6 space-y-6">
        {assessment.questions.map((question, questionIndex) => {
          const graded = results.get(question.question_id)
          return (
            <fieldset key={question.question_id} className="border-t border-[var(--border)] pt-5">
              <legend className="font-medium">
                {questionIndex + 1}. {question.question}
              </legend>
              <div className="mt-3 grid gap-2">
                {question.options.map((option, optionIndex) => (
                  <label
                    key={`${question.question_id}-${optionIndex}`}
                    className="flex cursor-pointer items-start gap-3 rounded-xl border border-[var(--border)] px-3 py-3 text-sm"
                  >
                    <input
                      type="radio"
                      name={question.question_id}
                      value={optionIndex}
                      checked={selected[question.question_id] === optionIndex}
                      disabled={busy || Boolean(result)}
                      onChange={() =>
                        setSelected(current => ({
                          ...current,
                          [question.question_id]: optionIndex,
                        }))
                      }
                    />
                    <span>{option}</span>
                  </label>
                ))}
              </div>
              {graded ? (
                <div
                  className={`mt-4 flex items-start gap-2 rounded-xl px-3 py-3 text-sm ${graded.correct ? 'bg-emerald-500/10' : 'bg-amber-500/10'}`}
                  role="status"
                >
                  {graded.correct ? (
                    <CheckCircle2 size={18} className="mt-0.5 text-emerald-500" />
                  ) : (
                    <CircleX size={18} className="mt-0.5 text-amber-500" />
                  )}
                  <span>
                    <strong>{graded.correct ? (zh ? '回答正确' : 'Correct') : zh ? '可继续学习' : 'Keep learning'}</strong>
                    <span className="mt-1 block">{graded.rationale}</span>
                  </span>
                </div>
              ) : null}
            </fieldset>
          )
        })}
      </div>

      <div className="mt-6 flex flex-wrap gap-2">
        {result ? (
          <button type="button" onClick={onContinue} className="learning-button px-4 py-2">
            {zh ? '继续生成学习路径' : 'Continue to build the path'}
          </button>
        ) : (
          <button
            type="button"
            disabled={!complete || busy}
            onClick={() =>
              onSubmit(
                assessment.questions.map(question => ({
                  question_id: question.question_id,
                  selected_index: selected[question.question_id],
                }))
              )
            }
            className="learning-button px-4 py-2"
          >
            {busy ? <Loader2 size={15} className="animate-spin" /> : null}
            {zh ? '提交并查看结果' : 'Submit and view results'}
          </button>
        )}
        {!result ? (
          <button
            type="button"
            disabled={busy}
            onClick={onSkip}
            className="learning-button learning-button--secondary px-4 py-2"
          >
            {zh ? '跳过，直接生成路径' : 'Skip and build the path'}
          </button>
        ) : null}
      </div>
    </section>
  )
}
