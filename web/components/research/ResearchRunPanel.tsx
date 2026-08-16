"use client";

import { Pause, Play, RotateCcw, Square } from "lucide-react";
import type { ResearchBrief, ResearchRun, ResearchRunAction, ResearchRunState } from "@/lib/research-workspace-api";

type Copy = { zh: string; en: string };
type Tr = (copy: Copy) => string;

interface Props {
  brief: ResearchBrief | null;
  runs: ResearchRun[];
  busy: boolean;
  tr: Tr;
  onStart: () => Promise<void>;
  onAction: (run: ResearchRun, action: ResearchRunAction) => Promise<void>;
}

const STATUS_COPY: Record<ResearchRunState, Copy> = {
  draft: { zh: "草稿", en: "Draft" },
  queued: { zh: "排队中", en: "Queued" },
  running: { zh: "研究中", en: "Running" },
  pausing: { zh: "正在暂停", en: "Pausing" },
  paused: { zh: "已暂停", en: "Paused" },
  cancelling: { zh: "正在取消", en: "Cancelling" },
  cancelled: { zh: "已取消", en: "Cancelled" },
  completed: { zh: "已完成", en: "Completed" },
  failed: { zh: "失败", en: "Failed" },
  needs_review: { zh: "需要复核", en: "Needs review" },
};

export default function ResearchRunPanel({ brief, runs, busy, tr, onStart, onAction }: Props) {
  const orderedRuns = [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const hasActiveRun = orderedRuns.some((run) => ["queued", "running", "pausing", "paused", "cancelling"].includes(run.status));

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5" aria-labelledby="research-runs-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--primary)]">{tr({ zh: "研究运行", en: "Research runs" })}</p>
          <h2 id="research-runs-heading" className="mt-1 text-lg font-semibold">{tr({ zh: "执行与恢复", en: "Run and recover" })}</h2>
        </div>
        <button type="button" onClick={() => void onStart()} disabled={!brief || busy || hasActiveRun} className={primaryButtonClass}>
          <Play aria-hidden="true" className="h-4 w-4" />
          {tr({ zh: "启动研究", en: "Start research" })}
        </button>
      </div>
      {!brief ? <p className="mt-4 rounded-lg border border-dashed border-[var(--border)] px-4 py-5 text-sm text-[var(--muted-foreground)]">{tr({ zh: "先保存研究简报，再启动一次可恢复的研究运行。", en: "Save a research brief before starting a recoverable run." })}</p> : null}
      {brief && !orderedRuns.length ? <p className="mt-4 rounded-lg border border-dashed border-[var(--border)] px-4 py-5 text-sm text-[var(--muted-foreground)]">{tr({ zh: "还没有运行记录。每次启动都会固定简报版本并保留状态。", en: "No runs yet. Each run freezes its brief version and keeps a durable state." })}</p> : null}

      <div className="mt-4 space-y-3" aria-live="polite" aria-busy={busy}>
        {orderedRuns.map((run) => (
          <article key={run.run_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/20 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-medium">{tr({ zh: `研究运行 · 简报 v${run.brief_version}`, en: `Research run · brief v${run.brief_version}` })}</h3>
                  <span className={statusClass(run.status)}>{tr(STATUS_COPY[run.status])}</span>
                </div>
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">{tr({ zh: `更新于 ${formatDate(run.updated_at)}`, en: `Updated ${formatDate(run.updated_at)}` })}</p>
                {run.failure_reason ? <p role="alert" className="mt-2 max-w-2xl text-sm text-[var(--destructive)]">{run.failure_reason}</p> : null}
              </div>
              <RunActions run={run} disabled={busy} tr={tr} onAction={onAction} />
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function RunActions({ run, disabled, tr, onAction }: { run: ResearchRun; disabled: boolean; tr: Tr; onAction: Props["onAction"] }) {
  const actions: Array<{ action: ResearchRunAction; label: Copy; icon: typeof Pause }> = [];
  if (run.status === "running") actions.push({ action: "pause", label: { zh: "暂停", en: "Pause" }, icon: Pause });
  if (run.status === "paused") actions.push({ action: "resume", label: { zh: "继续", en: "Resume" }, icon: Play });
  if (["failed", "needs_review"].includes(run.status)) actions.push({ action: "retry", label: { zh: "重试", en: "Retry" }, icon: RotateCcw });
  if (["queued", "running", "paused"].includes(run.status)) actions.push({ action: "cancel", label: { zh: "取消", en: "Cancel" }, icon: Square });
  if (!actions.length) return null;

  return (
    <div className="flex flex-wrap gap-2">
      {actions.map(({ action, label, icon: Icon }) => (
        <button key={action} type="button" disabled={disabled} onClick={() => void onAction(run, action)} className={secondaryButtonClass}>
          <Icon aria-hidden="true" className="h-4 w-4" />{tr(label)}
        </button>
      ))}
    </div>
  );
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function statusClass(status: ResearchRunState): string {
  const base = "rounded-full px-2.5 py-1 text-xs font-medium";
  if (status === "completed") return `${base} bg-emerald-500/10 text-emerald-600 dark:text-emerald-300`;
  if (status === "failed" || status === "needs_review") return `${base} bg-[var(--destructive)]/10 text-[var(--destructive)]`;
  if (["running", "queued", "pausing", "cancelling"].includes(status)) return `${base} bg-[var(--primary)]/10 text-[var(--primary)]`;
  return `${base} bg-[var(--muted)] text-[var(--muted-foreground)]`;
}

const primaryButtonClass = "inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";
const secondaryButtonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm font-medium hover:border-[var(--primary)]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-50";
