"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useAppShell } from "@/context/AppShellContext";
import {
  getLearningGovernance,
  type LearningGovernanceData,
} from "@/lib/learning-governance-api";

type GovernanceFocus = "errors" | "reviews" | "misconceptions";
type Copy = { zh: string; en: string };

export function LearnerSubjectGovernanceTab({
  subjectId,
  focus,
}: {
  subjectId: string;
  focus: GovernanceFocus;
}) {
  const { language } = useAppShell();
  const zh = language === "zh";
  const tr = useCallback((copy: Copy) => (zh ? copy.zh : copy.en), [zh]);
  const [data, setData] = useState<LearningGovernanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError("");
    try {
      const result = await getLearningGovernance(subjectId, { signal });
      if (!signal?.aborted) setData(result);
    } catch (cause) {
      if (!signal?.aborted) {
        setError(cause instanceof Error ? cause.message : tr({
          zh: "该模块暂时无法读取。",
          en: "This module is temporarily unavailable.",
        }));
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [subjectId, tr]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const copy = {
    errors: {
      title: tr({ zh: "错题与修复", en: "Errors and repairs" }),
      description: tr({
        zh: "只展示服务端可追溯的错误与修复状态；待归因记录不会成为掌握证据。",
        en: "Only server-traceable error and repair states are shown. Pending attribution never becomes mastery evidence.",
      }),
    },
    reviews: {
      title: tr({ zh: "复习计划", en: "Review plan" }),
      description: tr({
        zh: "复习安排来自 canonical 调度状态，复习结果仍需通过服务端判分事件链回写。",
        en: "Review items come from canonical scheduling; outcomes still return through the server-graded event chain.",
      }),
    },
    misconceptions: {
      title: tr({ zh: "误区候选", en: "Misconception hypotheses" }),
      description: tr({
        zh: "误区只是带证据边界的候选假设；一次错误不会直接形成稳定结论。",
        en: "Misconceptions are evidence-bounded hypotheses; one error never becomes a stable conclusion.",
      }),
    },
  }[focus];

  return (
    <section className="rounded-xl border border-[var(--border)] p-4 sm:p-5" aria-busy={loading}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">{copy.title}</h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">{copy.description}</p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-10 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm hover:border-[var(--primary)]/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:opacity-55"
        >
          <RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} aria-hidden="true" />
          {tr({ zh: "刷新", en: "Refresh" })}
        </button>
      </div>

      {error ? (
        <div role="alert" className="mt-4 rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-3 text-sm text-[var(--destructive)]">
          <p className="font-medium">{tr({ zh: "本模块暂不可用", en: "This module is unavailable" })}</p>
          <p className="mt-1 break-words text-xs">{error}</p>
        </div>
      ) : null}

      {loading && !data ? (
        <div className="mt-4 space-y-2" role="status" aria-label={tr({ zh: "正在加载", en: "Loading" })}>
          {[0, 1, 2].map((item) => <div key={item} className="h-20 animate-pulse rounded-lg bg-[var(--muted)]/55" />)}
        </div>
      ) : null}

      {data && focus === "errors" ? (
        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          <RecordGroup title={tr({ zh: `错误记录 ${data.errors.length}`, en: `Errors ${data.errors.length}` })} empty={tr({ zh: "没有可信错误记录。", en: "No trusted error records." })}>
            {data.errors.map((item) => (
              <article key={item.error_id} className="rounded-lg bg-[var(--muted)]/35 p-3 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div><h3 className="font-medium">{item.kc_id}</h3><p className="mt-1 text-xs text-[var(--muted-foreground)]">{item.status} · {item.error_type} · {item.source_event_ids.length} {tr({ zh: "条来源事件", en: "source events" })}</p></div>
                  <Attribution status={item.attribution_status} zh={zh} />
                </div>
              </article>
            ))}
          </RecordGroup>
          <RecordGroup title={tr({ zh: `修复进展 ${data.repairs.length}`, en: `Repairs ${data.repairs.length}` })} empty={tr({ zh: "还没有修复尝试。", en: "No repair attempts yet." })}>
            {data.repairs.map((item) => (
              <article key={item.error_id} className="rounded-lg bg-[var(--muted)]/35 p-3 text-sm">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div><h3 className="font-medium">{item.kc_id}</h3><p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr({ zh: `${item.attempt_count} 次尝试，${item.successful_attempt_count} 次通过`, en: `${item.attempt_count} attempts, ${item.successful_attempt_count} successful` })}</p></div>
                  <Attribution status={item.attribution_status} zh={zh} />
                </div>
              </article>
            ))}
          </RecordGroup>
        </div>
      ) : null}

      {data && focus === "reviews" ? (
        <RecordGroup title={tr({ zh: `复习安排 ${data.reviews.length}`, en: `Reviews ${data.reviews.length}` })} empty={tr({ zh: "当前没有复习安排。", en: "No reviews are currently scheduled." })} className="mt-5">
          {data.reviews.map((item) => {
            const timestamp = item.due_at < 10_000_000_000 ? item.due_at * 1000 : item.due_at;
            const due = new Intl.DateTimeFormat(zh ? "zh-CN" : "en", { dateStyle: "medium" }).format(timestamp);
            return <article key={item.review_id} className="rounded-lg bg-[var(--muted)]/35 p-3 text-sm"><div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="font-medium">{item.kc_id}</h3><p className="mt-1 text-xs text-[var(--muted-foreground)]">{item.status === "due" ? tr({ zh: `已到期 · ${due}`, en: `Due · ${due}` }) : tr({ zh: `计划于 ${due}`, en: `Scheduled for ${due}` })}</p></div><Attribution status={item.attribution_status} zh={zh} /></div></article>;
          })}
        </RecordGroup>
      ) : null}

      {data && focus === "misconceptions" ? (
        <RecordGroup title={tr({ zh: `误区候选 ${data.misconceptions.length}`, en: `Hypotheses ${data.misconceptions.length}` })} empty={tr({ zh: "还没有可展示的误区候选。", en: "No misconception hypotheses to show." })} className="mt-5">
          {data.misconceptions.map((item) => <article key={item.hypothesis_id} className="rounded-lg bg-[var(--muted)]/35 p-3 text-sm"><div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="font-medium">{item.pattern}</h3><p className="mt-1 text-xs text-[var(--muted-foreground)]">{item.status} · {item.kc_ids.join(" · ")} · {item.evidence_count} {tr({ zh: "条证据", en: "evidence refs" })}</p></div><Attribution status={item.attribution_status} zh={zh} /></div></article>)}
        </RecordGroup>
      ) : null}
    </section>
  );
}

function RecordGroup({ title, empty, className = "", children }: { title: string; empty: string; className?: string; children: React.ReactNode }) {
  const hasItems = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return <section className={`rounded-lg border border-[var(--border)] p-3 sm:p-4 ${className}`.trim()}><h2 className="text-sm font-semibold">{title}</h2>{hasItems ? <div className="mt-3 space-y-2">{children}</div> : <p className="mt-3 rounded-md border border-dashed border-[var(--border)] p-3 text-xs text-[var(--muted-foreground)]">{empty}</p>}</section>;
}

function Attribution({ status, zh }: { status: "verified" | "attribution_pending"; zh: boolean }) {
  const verified = status === "verified";
  return <span className={verified ? "shrink-0 rounded-full bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-700 dark:text-emerald-300" : "shrink-0 rounded-full bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300"}>{verified ? (zh ? "归因已验证" : "Attribution verified") : (zh ? "归因待确认" : "Attribution pending")}</span>;
}
