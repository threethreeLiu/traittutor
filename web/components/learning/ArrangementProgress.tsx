import { AlertTriangle, Loader2, Route } from 'lucide-react'

export default function ArrangementProgress({
  phase,
  zh,
  error,
  onRetry,
  onUseBasicPath,
}: {
  phase: 'judging' | 'arranging' | 'error'
  zh: boolean
  error?: string | null
  onRetry?: () => void
  onUseBasicPath?: () => void
}) {
  const title =
    phase === 'judging'
      ? zh
        ? '正在判断是否需要前置提问…'
        : 'Checking whether a short pre-assessment would help…'
      : phase === 'arranging'
        ? zh
          ? '正在为你排列学习路径…'
          : 'Arranging your learning path…'
        : zh
          ? '智能排列暂时未完成'
          : 'Smart arrangement is not complete'

  return (
    <section
      className="learning-card learning-card--large mx-auto w-full max-w-2xl"
      aria-live="polite"
      data-testid={`arrangement-${phase}`}
    >
      <div className="flex items-start gap-3">
        {phase === 'error' ? (
          <AlertTriangle className="mt-0.5 text-amber-500" aria-hidden />
        ) : phase === 'arranging' ? (
          <Route className="learning-accent mt-0.5 animate-pulse" aria-hidden />
        ) : (
          <Loader2 className="learning-accent mt-0.5 animate-spin" aria-hidden />
        )}
        <div className="min-w-0 flex-1">
          <p className="learning-eyebrow">{zh ? '智能排列' : 'Smart arrangement'}</p>
          <h2 className="mt-2 font-serif text-2xl font-semibold">{title}</h2>
          <p className="learning-copy-muted mt-2 text-sm leading-6">
            {error ??
              (zh
                ? '学习材料、学科证据与支持偏好只用于选择教学顺序，不会形成能力诊断。'
                : 'Your material and support preferences shape the teaching order, not an ability diagnosis.')}
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            {phase === 'error' && onRetry ? (
              <button type="button" onClick={onRetry} className="learning-button px-4 py-2">
                {zh ? '重试' : 'Retry'}
              </button>
            ) : null}
            {onUseBasicPath ? (
              <button
                type="button"
                onClick={onUseBasicPath}
                className="learning-button learning-button--secondary px-4 py-2"
              >
                {zh ? '直接使用基础路径' : 'Use the basic path'}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  )
}
