"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowRight, Plus, RefreshCw, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ResearchApiError, createResearchWorkspace, listResearchWorkspaces, type ResearchWorkspaceSummary } from "@/lib/research-workspace-api";
import { publishResearchWorkspaces } from "@/lib/research-workspace-sync";

type Copy = { zh: string; en: string };

export default function ResearchWorkspaceIndex() {
  const { i18n } = useTranslation();
  const router = useRouter();
  const titleId = useId();
  const alertRef = useRef<HTMLDivElement>(null);
  const zh = i18n.language.toLowerCase().startsWith("zh");
  const tr = useCallback((copy: Copy) => zh ? copy.zh : copy.en, [zh]);
  const [workspaces, setWorkspaces] = useState<ResearchWorkspaceSummary[]>([]);
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const nextWorkspaces = await listResearchWorkspaces(signal);
      setWorkspaces(nextWorkspaces);
      publishResearchWorkspaces(nextWorkspaces);
      setError("");
    } catch (cause) {
      if (signal?.aborted || (cause instanceof DOMException && cause.name === "AbortError")) return;
      setError(cause instanceof Error ? cause.message : tr({ zh: "研究工作区暂时无法读取。", en: "Research workspaces are temporarily unavailable." }));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [tr]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!title.trim()) return;
    setCreating(true);
    setError("");
    try {
      const workspace = await createResearchWorkspace({
        title: title.trim(),
        idempotency_key: crypto.randomUUID(),
      });
      publishResearchWorkspaces([
        workspace,
        ...workspaces.filter((item) => item.workspace_id !== workspace.workspace_id),
      ]);
      router.push(`/research/${encodeURIComponent(workspace.workspace_id)}`);
    } catch (cause) {
      const message = cause instanceof ResearchApiError && cause.status === 409
        ? tr({ zh: "同名或同一请求的工作区已经存在。请刷新列表后继续。", en: "That workspace or request already exists. Refresh the list to continue." })
        : cause instanceof Error ? cause.message : tr({ zh: "无法创建研究工作区，请重试。", en: "The workspace could not be created. Try again." });
      setError(message);
      window.requestAnimationFrame(() => alertRef.current?.focus());
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="traittutor-scroll-area h-full overflow-y-auto px-4 py-6 pb-16 sm:px-8 sm:py-8 lg:px-10 xl:px-12 2xl:px-16">
      <div className="w-full">
        <header className="border-b border-[var(--border)] pb-6 sm:pb-8">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--primary)]">{tr({ zh: "研究工作区", en: "Research workspace" })}</p>
              <h1 className="mt-1 font-serif text-3xl font-semibold tracking-tight sm:text-4xl">{tr({ zh: "研究工作区", en: "Research workspaces" })}</h1>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "从版本化简报到有来源的报告，在一个可暂停、恢复和审阅的空间中推进研究。", en: "Move from a versioned brief to a sourced report in a workspace that can be paused, resumed, and reviewed." })}</p>
            </div>
            <button type="button" onClick={() => void load()} disabled={loading} className={secondaryButtonClass}><RefreshCw aria-hidden="true" className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />{tr({ zh: "刷新", en: "Refresh" })}</button>
          </div>
        </header>

        {error ? <div ref={alertRef} tabIndex={-1} role="alert" className="mt-5 rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)] focus:outline-none"><p>{error}</p><button type="button" onClick={() => void load()} className="mt-2 min-h-8 rounded font-medium underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]">{tr({ zh: "重新加载列表", en: "Reload the list" })}</button></div> : null}

        <section className="mt-6 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5" aria-labelledby="create-research-heading">
          <div className="flex items-start gap-3"><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-[var(--primary)]/10 text-[var(--primary)]"><Plus aria-hidden="true" className="h-4 w-4" /></span><div><h2 id="create-research-heading" className="font-semibold">{tr({ zh: "建立新工作区", en: "Create a workspace" })}</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr({ zh: "先给研究命名；进入后再保存研究问题与来源策略。", en: "Name the research first, then define its question and source policy inside." })}</p></div></div>
          <form onSubmit={(event) => void create(event)} className="mt-4 grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
            <div><label htmlFor={titleId} className="mb-1.5 block text-sm font-medium">{tr({ zh: "工作区名称", en: "Workspace name" })}</label><input id={titleId} value={title} onChange={(event) => setTitle(event.target.value)} required disabled={creating} className={fieldClass} placeholder={tr({ zh: "例如：长期记忆学习策略", en: "For example: long-term retention strategies" })} /></div>
            <button type="submit" disabled={creating || !title.trim()} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"><Plus aria-hidden="true" className="h-4 w-4" />{creating ? tr({ zh: "正在创建…", en: "Creating…" }) : tr({ zh: "创建", en: "Create" })}</button>
          </form>
        </section>

        <section className="mt-7" aria-labelledby="research-list-heading">
          <div className="flex items-center justify-between gap-4"><h2 id="research-list-heading" className="text-lg font-semibold">{tr({ zh: "我的工作区", en: "My workspaces" })}</h2>{!loading ? <span className="text-sm tabular-nums text-[var(--muted-foreground)]">{workspaces.length}</span> : null}</div>
          {loading ? <div className="mt-3 grid gap-3 md:grid-cols-2 2xl:grid-cols-3" aria-busy="true" aria-label={tr({ zh: "正在加载工作区", en: "Loading workspaces" })}>{[0, 1, 2, 3].map((item) => <div key={item} className="h-32 animate-pulse rounded-xl bg-[var(--muted)]/55" />)}</div> : null}
          {!loading && !workspaces.length ? <div className="mt-3 rounded-xl border border-dashed border-[var(--border)] px-5 py-12 text-center"><Search aria-hidden="true" className="mx-auto h-6 w-6 text-[var(--primary)]" /><h3 className="mt-3 font-medium">{tr({ zh: "还没有研究工作区", en: "No research workspaces yet" })}</h3><p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "在上方命名第一个工作区，然后用研究简报明确问题、目标和来源边界。", en: "Name your first workspace above, then use its brief to set the question, objectives, and source boundary." })}</p></div> : null}
          {!loading && workspaces.length ? <div className="mt-3 grid gap-3 md:grid-cols-2 2xl:grid-cols-3">{[...workspaces].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).map((workspace) => <Link key={workspace.workspace_id} href={`/research/${encodeURIComponent(workspace.workspace_id)}`} className="group rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 transition-colors hover:border-[var(--primary)]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><h3 className="break-words font-semibold">{workspace.title}</h3><p className="mt-1 text-xs text-[var(--muted-foreground)]">{workspace.active_brief_id ? tr({ zh: "已有研究简报", en: "Research brief ready" }) : tr({ zh: "等待研究简报", en: "Awaiting a research brief" })}</p></div><ArrowRight aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-[var(--muted-foreground)] transition-transform group-hover:translate-x-0.5" /></div><div className="mt-5 flex flex-wrap items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]"><span className="rounded-full bg-[var(--muted)] px-2.5 py-1">{workspace.status === "active" ? tr({ zh: "进行中", en: "Active" }) : workspace.status === "archived" ? tr({ zh: "已归档", en: "Archived" }) : tr({ zh: "已删除", en: "Deleted" })}</span><time dateTime={workspace.updated_at}>{tr({ zh: `更新于 ${formatDate(workspace.updated_at)}`, en: `Updated ${formatDate(workspace.updated_at)}` })}</time></div></Link>)}</div> : null}
        </section>
      </div>
    </main>
  );
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

const fieldClass = "h-11 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-60";
const secondaryButtonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3 text-sm font-medium hover:border-[var(--primary)]/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-50";
