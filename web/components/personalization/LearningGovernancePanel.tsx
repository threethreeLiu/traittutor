"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ListChecks, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";
import { useAppShell } from "@/context/AppShellContext";
import {
  getLearningGovernance,
  type ErrorRecordStatus,
  type GovernanceAttributionStatus,
  type GovernanceMisconception,
  type GovernanceReview,
  type LearningGovernanceData,
} from "@/lib/learning-governance-api";

type Copy = { zh: string; en: string };
type Tr = (copy: Copy) => string;

export function LearningGovernancePanel({ subjectId }: { subjectId: string }) {
  const { language } = useAppShell();
  const zh = language === "zh";
  const tr = useCallback((copy: Copy) => (zh ? copy.zh : copy.en), [zh]);
  const [data, setData] = useState<LearningGovernanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError("");
      try {
        const result = await getLearningGovernance(subjectId, { signal });
        if (!signal?.aborted) setData(result);
      } catch (cause) {
        if (!signal?.aborted) {
          setError(
            cause instanceof Error
              ? cause.message
              : tr({
                  zh: "学习治理记录暂时无法读取。",
                  en: "Learning governance records are temporarily unavailable.",
                }),
          );
        }
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [subjectId, tr],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  return (
    <section
      className="rounded-xl border border-[var(--border)] p-4 sm:p-5"
      aria-labelledby="learning-governance-heading"
      aria-busy={loading}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted-foreground)]">
            <ShieldCheck className="h-4 w-4 text-[var(--primary)]" aria-hidden="true" />
            {tr({ zh: "可信学习治理", en: "Trusted learning governance" })}
          </div>
          <h2 id="learning-governance-heading" className="mt-2 font-semibold">
            {tr({ zh: "错误、修复、误概念与复习", en: "Errors, repairs, misconceptions, and reviews" })}
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">
            {tr({
              zh: "只展示服务端可追溯的作答与复习状态。归因仍待确认的记录会明确标注，不会被当作已掌握证据。",
              en: "Only server-traceable answer and review states are shown. Records awaiting attribution are labeled explicitly and never treated as mastery evidence.",
            })}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-10 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm transition-colors hover:border-[var(--primary)]/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-55"
        >
          <RefreshCw
            className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"}
            aria-hidden="true"
          />
          {tr({ zh: "刷新治理记录", en: "Refresh records" })}
        </button>
      </div>

      {error ? (
        <div
          role="alert"
          className="mt-4 rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-3 text-sm text-[var(--destructive)]"
        >
          <p className="font-medium">
            {tr({ zh: "学习治理记录暂不可用", en: "Learning governance is unavailable" })}
          </p>
          <p className="mt-1 break-words text-xs">{error}</p>
        </div>
      ) : null}

      {loading && !data ? (
        <div
          className="mt-4 grid gap-3 sm:grid-cols-2"
          role="status"
          aria-label={tr({ zh: "正在加载学习治理记录", en: "Loading learning governance records" })}
        >
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="h-24 animate-pulse rounded-lg bg-[var(--muted)]/55" />
          ))}
        </div>
      ) : null}

      {data ? (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <GovernanceGroup
            title={tr({ zh: `错误记录 ${data.errors.length}`, en: `Errors ${data.errors.length}` })}
            icon={<AlertTriangle className="h-4 w-4" aria-hidden="true" />}
            empty={tr({ zh: "没有这门学科的可信错误记录。", en: "No trusted error records for this subject." })}
          >
            {data.errors.map((item) => (
              <article key={item.error_id} className="rounded-lg bg-[var(--muted)]/35 p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h4 className="break-words text-sm font-medium">{item.kc_id}</h4>
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                      {errorStatus(item.status, tr)} · {errorType(item.error_type, tr)}
                    </p>
                  </div>
                  <AttributionBadge status={item.attribution_status} tr={tr} />
                </div>
                <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                  {tr({
                    zh: `${item.source_event_ids.length} 条来源事件`,
                    en: `${item.source_event_ids.length} source events`,
                  })}
                </p>
              </article>
            ))}
          </GovernanceGroup>

          <GovernanceGroup
            title={tr({ zh: `修复进展 ${data.repairs.length}`, en: `Repairs ${data.repairs.length}` })}
            icon={<RotateCcw className="h-4 w-4" aria-hidden="true" />}
            empty={tr({ zh: "还没有修复尝试。", en: "No repair attempts yet." })}
          >
            {data.repairs.map((item) => (
              <article key={item.error_id} className="rounded-lg bg-[var(--muted)]/35 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h4 className="text-sm font-medium">{item.kc_id}</h4>
                    <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                      {tr({
                        zh: `${item.attempt_count} 次尝试，${item.successful_attempt_count} 次通过`,
                        en: `${item.attempt_count} attempts, ${item.successful_attempt_count} successful`,
                      })}
                    </p>
                  </div>
                  <AttributionBadge status={item.attribution_status} tr={tr} />
                </div>
              </article>
            ))}
          </GovernanceGroup>

          <GovernanceGroup
            title={tr({
              zh: `误概念假设 ${data.misconceptions.length}`,
              en: `Misconception hypotheses ${data.misconceptions.length}`,
            })}
            icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
            empty={tr({ zh: "还没有可展示的误概念假设。", en: "No misconception hypotheses to show." })}
          >
            {data.misconceptions.map((item) => (
              <MisconceptionItem key={item.hypothesis_id} item={item} tr={tr} />
            ))}
          </GovernanceGroup>

          <GovernanceGroup
            title={tr({ zh: `复习安排 ${data.reviews.length}`, en: `Reviews ${data.reviews.length}` })}
            icon={<ListChecks className="h-4 w-4" aria-hidden="true" />}
            empty={tr({ zh: "当前没有复习安排。", en: "No reviews are currently scheduled." })}
          >
            {data.reviews.map((item) => (
              <ReviewItem key={item.review_id} item={item} tr={tr} locale={zh ? "zh-CN" : "en"} />
            ))}
          </GovernanceGroup>
        </div>
      ) : null}
    </section>
  );
}

function GovernanceGroup({
  title,
  icon,
  empty,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  empty: string;
  children: React.ReactNode;
}) {
  const hasItems = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return (
    <section className="rounded-lg border border-[var(--border)] p-3 sm:p-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <span className="text-[var(--primary)]">{icon}</span>
        {title}
      </h3>
      {hasItems ? (
        <div className="mt-3 space-y-2">{children}</div>
      ) : (
        <p className="mt-3 rounded-md border border-dashed border-[var(--border)] p-3 text-xs text-[var(--muted-foreground)]">
          {empty}
        </p>
      )}
    </section>
  );
}

function AttributionBadge({ status, tr }: { status: GovernanceAttributionStatus; tr: Tr }) {
  const verified = status === "verified";
  return (
    <span
      className={
        verified
          ? "shrink-0 rounded-full bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-700 dark:text-emerald-300"
          : "shrink-0 rounded-full bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300"
      }
    >
      {verified
        ? tr({ zh: "归因已验证", en: "Attribution verified" })
        : tr({ zh: "归因待确认", en: "Attribution pending" })}
    </span>
  );
}

function MisconceptionItem({ item, tr }: { item: GovernanceMisconception; tr: Tr }) {
  const labels = {
    candidate: tr({ zh: "候选", en: "Candidate" }),
    confirmed: tr({ zh: "已确认", en: "Confirmed" }),
    resolved: tr({ zh: "已解决", en: "Resolved" }),
  };
  return (
    <article className="rounded-lg bg-[var(--muted)]/35 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="break-words text-sm font-medium">{item.pattern}</h4>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {labels[item.status]} · {item.kc_ids.join(" · ")} · {item.evidence_count}{" "}
            {tr({ zh: "条证据", en: "evidence refs" })}
          </p>
        </div>
        <AttributionBadge status={item.attribution_status} tr={tr} />
      </div>
    </article>
  );
}

function ReviewItem({
  item,
  tr,
  locale,
}: {
  item: GovernanceReview;
  tr: Tr;
  locale: string;
}) {
  const timestamp = item.due_at < 10_000_000_000 ? item.due_at * 1000 : item.due_at;
  const due = new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(timestamp);
  return (
    <article className="rounded-lg bg-[var(--muted)]/35 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-medium">{item.kc_id}</h4>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {item.status === "due"
              ? tr({ zh: `已到期 · ${due}`, en: `Due · ${due}` })
              : tr({ zh: `计划于 ${due}`, en: `Scheduled for ${due}` })}
          </p>
        </div>
        <AttributionBadge status={item.attribution_status} tr={tr} />
      </div>
    </article>
  );
}

function errorStatus(status: ErrorRecordStatus, tr: Tr): string {
  return {
    open: tr({ zh: "待修复", en: "Open" }),
    repaired: tr({ zh: "已修复", en: "Repaired" }),
    relapsed: tr({ zh: "再次出现", en: "Relapsed" }),
  }[status];
}

function errorType(
  type: "structural" | "deviation" | "application" | "metacognitive",
  tr: Tr,
): string {
  return {
    structural: tr({ zh: "知识结构", en: "Knowledge structure" }),
    deviation: tr({ zh: "理解偏差", en: "Understanding deviation" }),
    application: tr({ zh: "应用错误", en: "Application error" }),
    metacognitive: tr({ zh: "元认知", en: "Metacognitive" }),
  }[type];
}
