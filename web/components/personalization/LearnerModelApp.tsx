'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Brain, RefreshCw } from 'lucide-react'
import { useAppShell } from '@/context/AppShellContext'
import {
  getLearningModelOverview,
  type LearningModelOverview,
} from '@/lib/learning-model-read-api'
import { setLearnerInference } from '@/lib/learner-model-api'
import {
  GovernanceSection,
  OverviewSectionSkeleton,
  PendingSubjectsSection,
  SubjectsSection,
  TaskQueueSection,
  TodaySummarySection,
} from '@/components/personalization/LearningModelOverviewSections'

type Copy = { zh: string; en: string }

export default function LearnerModelApp() {
  const { language } = useAppShell()
  const zh = language === 'zh'
  const tr = useCallback((copy: Copy) => (zh ? copy.zh : copy.en), [zh])
  const [overview, setOverview] = useState<LearningModelOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [updatingInference, setUpdatingInference] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const next = await getLearningModelOverview()
      setOverview(next)
      setError('')
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : tr({
              zh: '学习画像暂时无法读取，请稍后刷新。',
              en: 'The learning profile is temporarily unavailable. Please refresh shortly.',
            })
      )
    } finally {
      setLoading(false)
    }
  }, [tr])

  useEffect(() => {
    void Promise.resolve().then(load)
  }, [load])

  const toggleInference = useCallback(async () => {
    if (!overview || updatingInference) return
    setUpdatingInference(true)
    setError('')
    try {
      if (typeof overview.support.inference_enabled !== 'boolean') return
      await setLearnerInference(!overview.support.inference_enabled)
      await load()
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : tr({ zh: '推断设置未能更新，请重试。', en: 'Inference settings could not be updated. Try again.' })
      )
    } finally {
      setUpdatingInference(false)
    }
  }, [load, overview, tr, updatingInference])

  const subjectLabels = useMemo(
    () => new Map(overview?.confirmed_subjects.items.map(item => [item.subject_id, item.label])),
    [overview]
  )

  return (
    <main className="w-full py-4">
      <header className="border-b border-[var(--border)] pb-6 sm:pb-8">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-3 sm:gap-4">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
              <Brain className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--muted-foreground)]">
                {tr({ zh: 'TraitTutor 学习画像', en: 'TraitTutor learning profile' })}
              </p>
              <h1 className="mt-1 font-serif text-3xl font-semibold tracking-tight sm:text-4xl">
                {tr({ zh: '学习画像', en: 'Learning profile' })}
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">
                {tr({
                  zh: '按学科查看可追溯的学习状态，并从复习、错题修复和当前目标中决定下一步。',
                  en: 'Review traceable learning state by subject and choose the next step from reviews, error repairs, and current goals.',
                })}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3.5 text-sm transition-colors hover:border-[var(--primary)]/45 hover:text-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-55"
          >
            <RefreshCw className={loading ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
            {tr({ zh: '刷新', en: 'Refresh' })}
          </button>
        </div>
      </header>

      {error ? (
        <div role="alert" className="mt-5 rounded-lg border border-[var(--destructive)]/30 bg-[var(--destructive)]/8 px-4 py-3 text-sm text-[var(--destructive)]">
          <p className="font-medium">
            {overview
              ? tr({ zh: '刷新未完成，继续显示上次可用数据', en: 'Refresh failed; showing the last available data' })
              : tr({ zh: '学习画像暂时不可用', en: 'Learning profile temporarily unavailable' })}
          </p>
          <p className="mt-1 text-xs opacity-90">{error}</p>
        </div>
      ) : null}

      {!overview && loading ? (
        <div className="mt-6 grid gap-5" aria-label={tr({ zh: '正在加载学习画像', en: 'Loading learning profile' })}>
          {Array.from({ length: 5 }).map((_, index) => (
            <section key={index} className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
              <OverviewSectionSkeleton label={tr({ zh: '正在加载区块', en: 'Loading section' })} />
            </section>
          ))}
        </div>
      ) : overview ? (
        <div className="mt-6 grid gap-5">
          <TodaySummarySection section={overview.today} tr={tr} />
          <SubjectsSection section={overview.confirmed_subjects} tr={tr} />
          <PendingSubjectsSection section={overview.pending_subjects} tr={tr} />
          <TaskQueueSection section={overview.task_queue} subjectLabels={subjectLabels} tr={tr} />
          <GovernanceSection
            section={overview.support}
            tr={tr}
            updatingInference={updatingInference}
            onToggleInference={() => void toggleInference()}
          />
        </div>
      ) : (
        <section className="mt-6 rounded-xl border border-dashed border-[var(--border)] px-5 py-12 text-center">
          <h2 className="text-base font-semibold">
            {tr({ zh: '暂时无法加载学习画像', en: 'Unable to load the learning profile' })}
          </h2>
          <p className="mt-2 text-sm text-[var(--muted-foreground)]">
            {tr({ zh: '其他学习功能不受影响。请确认服务已启动后重试。', en: 'Other learning features are unaffected. Check the service and try again.' })}
          </p>
          <button type="button" onClick={() => void load()} className="mt-4 inline-flex h-9 items-center gap-2 rounded-md bg-[var(--primary)] px-3 text-sm font-medium text-[var(--primary-foreground)]">
            <RefreshCw className="h-4 w-4" />
            {tr({ zh: '重新加载', en: 'Try again' })}
          </button>
        </section>
      )}
    </main>
  )
}
