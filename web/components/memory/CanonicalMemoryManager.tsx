"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  CheckCircle2,
  DatabaseZap,
  History,
  RefreshCw,
  ShieldCheck,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import Modal from "@/components/common/Modal";
import { MemoryCandidateList } from "@/components/memory/MemoryCandidateList";
import { MemoryConflictDialog } from "@/components/memory/MemoryConflictDialog";
import { MemorySourceDetails } from "@/components/memory/MemorySourceDetails";
import { useAppShell } from "@/context/AppShellContext";
import {
  activateMemoryCandidate,
  createMemoryOperationId,
  deactivateMemoryItem,
  deleteMemoryItem,
  getCanonicalMemorySnapshot,
  listMemoryAccessSummaries,
  rebuildLongTermIndex,
  rejectMemoryCandidate,
  supersedeMemoryConflict,
  type CanonicalMemorySnapshot,
  type MemoryCandidate,
  type MemoryConflict,
  type MemoryItem,
  type MemoryAccessSummary,
} from "@/lib/canonical-memory-api";

type ItemAction = { kind: "deactivate" | "delete"; item: MemoryItem };

export default function CanonicalMemoryManager() {
  const { language } = useAppShell();
  const zh = language === "zh";
  const [snapshot, setSnapshot] = useState<CanonicalMemorySnapshot | null>(null);
  const [accessSummaries, setAccessSummaries] = useState<MemoryAccessSummary[]>([]);
  const [accessError, setAccessError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedConflict, setSelectedConflict] = useState<MemoryConflict | null>(null);
  const [itemAction, setItemAction] = useState<ItemAction | null>(null);

  const load = useCallback(async (signal?: AbortSignal, quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [snapshotResult, accessResult] = await Promise.allSettled([
        getCanonicalMemorySnapshot(signal),
        listMemoryAccessSummaries(signal),
      ]);
      if (accessResult.status === "fulfilled") {
        setAccessSummaries(accessResult.value);
        setAccessError("");
      } else if (!(accessResult.reason instanceof DOMException && accessResult.reason.name === "AbortError")) {
        setAccessError(messageFrom(
          accessResult.reason,
          zh ? "暂时无法读取记忆访问记录" : "Memory access history is temporarily unavailable",
        ));
      }
      if (snapshotResult.status === "rejected") throw snapshotResult.reason;
      setSnapshot(snapshotResult.value);
      setError("");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [zh]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal).catch((cause) => {
      if (cause instanceof DOMException && cause.name === "AbortError") return;
      setError(messageFrom(cause, zh ? "无法加载记忆" : "Unable to load memory"));
    });
    return () => controller.abort();
  }, [load, zh]);

  const candidates = useMemo(
    () => snapshot?.candidates.filter((item) => item.status === "candidate") ?? [],
    [snapshot],
  );
  const activeItems = useMemo(
    () => snapshot?.items.filter((item) => item.status === "active") ?? [],
    [snapshot],
  );
  const historyItems = useMemo(
    () => snapshot?.items.filter((item) => item.status !== "active") ?? [],
    [snapshot],
  );
  async function runAction(
    actionId: string,
    work: () => Promise<unknown>,
    success: string,
  ) {
    setBusyId(actionId);
    setError("");
    setNotice("");
    try {
      await work();
      await load(undefined, true);
      setNotice(success);
      return true;
    } catch (cause) {
      setError(messageFrom(cause, zh ? "操作未完成，请重试" : "The action could not be completed"));
      return false;
    } finally {
      setBusyId(null);
    }
  }

  function activate(candidate: MemoryCandidate) {
    void runAction(
      candidate.candidate_id,
      () => activateMemoryCandidate(candidate.candidate_id, createMemoryOperationId("activate")),
      zh ? "候选已确认并进入生效记忆。" : "The candidate is now active memory.",
    );
  }

  function reject(candidate: MemoryCandidate) {
    void runAction(
      candidate.candidate_id,
      () => rejectMemoryCandidate(candidate.candidate_id, createMemoryOperationId("reject")),
      zh ? "候选已拒绝。" : "The candidate was rejected.",
    );
  }

  function confirmConflict() {
    if (!selectedConflict) return;
    const conflict = selectedConflict;
    void runAction(
      conflict.candidate_id,
      () => supersedeMemoryConflict(conflict.candidate_id, createMemoryOperationId("supersede")),
      zh ? "冲突已解决，新值已生效。" : "Conflict resolved; the new value is active.",
    ).then((succeeded) => {
      if (succeeded) setSelectedConflict(null);
    });
  }

  async function confirmItemAction() {
    if (!itemAction) return;
    const { item, kind } = itemAction;
    const actionId = `${kind}-${item.memory_id}`;
    setBusyId(actionId);
    setError("");
    setNotice("");
    try {
      const result = kind === "delete"
        ? await deleteMemoryItem(item.memory_id, createMemoryOperationId("delete"))
        : await deactivateMemoryItem(item.memory_id, createMemoryOperationId("deactivate"));
      // Apply the redacted server result immediately. A failed refresh must not
      // leave deleted content visible in client state.
      setSnapshot((current) => current ? {
        ...current,
        items: current.items.map((entry) =>
          entry.memory_id === result.item.memory_id ? result.item : entry,
        ),
        index: { ...current.index, generation: result.invalidated_index_generation },
      } : current);
      setItemAction(null);
      setNotice(kind === "delete"
        ? (zh ? "记忆已删除，召回资格已立即移除。" : "Memory deleted and removed from recall immediately.")
        : (zh ? "记忆已停用。" : "Memory deactivated."));
      await load(undefined, true);
    } catch (cause) {
      setError(messageFrom(cause, zh ? "操作未完成，请重试" : "The action could not be completed"));
    } finally {
      setBusyId(null);
    }
  }

  function rebuild(entryId = "profile") {
    void runAction(
      `index-${entryId}`,
      () => rebuildLongTermIndex(entryId),
      zh ? "长期记忆索引已按当前生效记忆重建。" : "Long-term memory was rebuilt from current active memory.",
    );
  }

  if (loading && !snapshot) {
    return <MemoryManagerSkeleton zh={zh} />;
  }

  return (
    <main className="w-full space-y-6 py-4">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted-foreground)]">
            <ShieldCheck aria-hidden="true" className="h-4 w-4 text-[var(--primary)]" />
            {"CANONICAL MEMORY"}
          </div>
          <h1 className="mt-2 font-serif text-3xl font-semibold tracking-tight">
            {zh ? "我的记忆" : "My memory"}
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">
            {zh
              ? "查看 TraitTutor 会召回的长期记忆。推断内容必须先由你确认；范围与来源始终可见。"
              : "Review the long-term memory TraitTutor may recall. Inferences require your confirmation, and scope and provenance remain visible."}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load().catch((cause) => setError(messageFrom(cause, zh ? "刷新失败" : "Refresh failed")))}
          disabled={Boolean(busyId)}
          className="inline-flex h-10 items-center justify-center gap-2 self-start rounded-lg border border-[var(--border)] px-4 text-sm font-medium transition-colors hover:bg-[var(--muted)] disabled:opacity-50"
        >
          <RefreshCw aria-hidden="true" className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          {zh ? "刷新" : "Refresh"}
        </button>
      </header>

      {error ? (
        <p role="alert" className="rounded-xl border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 p-3 text-sm text-[var(--destructive)]">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="rounded-xl border border-emerald-500/35 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">
          {notice}
        </p>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label={zh ? "记忆概览" : "Memory overview"}>
        <Metric label={zh ? "生效记忆" : "Active"} value={activeItems.length} icon={CheckCircle2} />
        <Metric label={zh ? "等待确认" : "Candidates"} value={candidates.length} icon={History} />
        <Metric label={zh ? "需要解决" : "Conflicts"} value={snapshot?.conflicts.length ?? 0} icon={TriangleAlert} />
        <Metric label={zh ? "索引代次" : "Index generation"} value={snapshot?.index.generation ?? 0} icon={DatabaseZap} />
      </section>

      <Section
        eyebrow="REVIEW"
        title={zh ? "等待你确认" : "Waiting for your review"}
        description={zh ? "确认后才会进入可召回记忆；拒绝不会影响学习掌握度。" : "Only confirmed candidates become recallable. Rejection does not affect mastery."}
      >
        <MemoryCandidateList candidates={candidates} busyId={busyId} onActivate={activate} onReject={reject} />
      </Section>

      <Section
        eyebrow="CONFLICTS"
        title={zh ? "冲突记忆" : "Memory conflicts"}
        description={zh ? "系统不会静默覆盖旧值。请逐项比较后明确选择。" : "Existing values are never silently overwritten. Compare and choose explicitly."}
      >
        {snapshot?.conflicts.length ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {snapshot.conflicts.map((conflict) => (
              <article key={conflict.candidate_id} className="rounded-xl border border-amber-500/35 bg-amber-500/5 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">{conflict.key}</p>
                    <p className="mt-2 text-sm">{conflict.candidate_value}</p>
                  </div>
                  <TriangleAlert aria-hidden="true" className="h-5 w-5 shrink-0 text-amber-500" />
                </div>
                <p className="mt-3 text-xs text-[var(--muted-foreground)]">
                  {zh ? `与 ${conflict.values.length} 个生效值冲突` : `Conflicts with ${conflict.values.length} active value(s)`}
                </p>
                <button
                  type="button"
                  onClick={() => setSelectedConflict(conflict)}
                  disabled={Boolean(busyId)}
                  className="mt-4 h-9 rounded-lg border border-amber-500/50 px-3 text-xs font-medium text-amber-700 hover:bg-amber-500/10 disabled:cursor-not-allowed disabled:opacity-50 dark:text-amber-300"
                >
                  {zh ? "比较并处理" : "Compare and resolve"}
                </button>
              </article>
            ))}
          </div>
        ) : (
          <Empty text={zh ? "当前没有冲突。" : "There are no current conflicts."} />
        )}
      </Section>

      <Section
        eyebrow="ACTIVE"
        title={zh ? "当前生效记忆" : "Active memory"}
        description={zh ? "停用可保留历史但停止召回；删除会立即移除召回资格并清除内容。" : "Deactivation preserves history but stops recall. Deletion immediately removes recall eligibility and content."}
      >
        {activeItems.length ? (
          <div className="space-y-3">
            {activeItems.map((item) => (
              <MemoryItemCard
                key={item.memory_id}
                item={item}
                zh={zh}
                disabled={Boolean(busyId)}
                onDeactivate={() => setItemAction({ kind: "deactivate", item })}
                onDelete={() => setItemAction({ kind: "delete", item })}
              />
            ))}
          </div>
        ) : (
          <Empty text={zh ? "还没有生效记忆。" : "No active memory yet."} />
        )}

        {historyItems.length ? (
          <details className="mt-4 rounded-xl border border-[var(--border)] p-4">
            <summary className="cursor-pointer text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]">
              {zh ? `历史与已删除记录（${historyItems.length}）` : `History and deleted records (${historyItems.length})`}
            </summary>
            <div className="mt-3 space-y-2">
              {historyItems.map((item) => (
                <div key={item.memory_id} className="rounded-lg bg-[var(--muted)]/30 p-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium">{item.key}</span>
                    <span className="text-xs text-[var(--muted-foreground)]">{item.status}</span>
                  </div>
                  <p className="mt-1 break-words text-[var(--muted-foreground)]">
                    {item.redacted ? (zh ? "内容已删除" : "Content deleted") : item.value}
                  </p>
                </div>
              ))}
            </div>
          </details>
        ) : null}
      </Section>

      <Section
        eyebrow="ACCESS LOG"
        title={zh ? "最近的记忆使用" : "Recent memory use"}
        description={zh ? "这里只显示访问时间、范围、用途和结果数，不复制或展示记忆正文。" : "Only access time, scope, purpose, and result count are shown. Memory content is never copied into this log."}
      >
        {accessError ? (
          <p role="status" className="rounded-xl border border-amber-500/35 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300">
            {accessError}
          </p>
        ) : accessSummaries.length ? (
          <div className="space-y-2">
            {accessSummaries.slice(0, 20).map((summary) => (
              <article
                key={`${summary.snapshot_id}:${summary.created_at}:${summary.scope}:${summary.purpose}`}
                data-testid="memory-access-summary"
                className="rounded-xl border border-[var(--border)] p-4"
              >
                <time dateTime={summary.created_at} className="text-xs text-[var(--muted-foreground)]">
                  {formatAccessTime(summary.created_at, zh)}
                </time>
                <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
                  <AccessField label={zh ? "范围" : "Scope"} value={humanizeToken(summary.scope)} />
                  <AccessField label={zh ? "用途" : "Purpose"} value={humanizeToken(summary.purpose)} />
                  <AccessField label={zh ? "结果数" : "Result count"} value={String(summary.result_count)} />
                </dl>
              </article>
            ))}
          </div>
        ) : (
          <Empty text={zh ? "还没有记忆访问记录。" : "No memory access has been recorded yet."} />
        )}
      </Section>

      <Section
        eyebrow="LONG-TERM INDEX"
        title={zh ? "长期记忆索引" : "Long-term memory index"}
        description={zh ? "索引仅由当前生效记忆生成。迁入的旧文本必须先经你确认，才会参与召回。" : "The index is built only from active memory. Imported legacy text requires your confirmation before it can be recalled."}
      >
        <div className="space-y-3">
          {snapshot?.index.entries.length ? snapshot.index.entries.map((entry) => (
            <article key={entry.entry_id} className="flex flex-col gap-3 rounded-xl border border-[var(--border)] p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <p className="truncate font-mono text-sm font-medium">{entry.entry_id}</p>
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                  {zh ? `代次 ${entry.generation} · ${entry.claim_count} 条主张` : `Generation ${entry.generation} · ${entry.claim_count} claims`}
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {entry.assertion_states.map((state) => (
                    <span key={state} className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-300">
                      {assertionLabel(state, zh)}
                    </span>
                  ))}
                </div>
              </div>
              <button
                type="button"
                onClick={() => rebuild(entry.entry_id)}
                disabled={Boolean(busyId)}
                className="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] px-3 text-xs font-medium hover:bg-[var(--muted)] disabled:opacity-50"
              >
                <RefreshCw aria-hidden="true" className={`h-3.5 w-3.5 ${busyId === `index-${entry.entry_id}` ? "animate-spin" : ""}`} />
                {zh ? "重建" : "Rebuild"}
              </button>
            </article>
          )) : (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-[var(--border)] p-4">
              <p className="text-sm text-[var(--muted-foreground)]">{zh ? "尚未生成长期记忆索引。" : "No long-term memory index has been built."}</p>
              <button type="button" onClick={() => rebuild()} disabled={Boolean(busyId)} className="h-9 rounded-lg bg-[var(--primary)] px-3 text-xs font-medium text-[var(--primary-foreground)] disabled:opacity-50">
                {zh ? "生成索引" : "Build index"}
              </button>
            </div>
          )}
        </div>
      </Section>

      <MemoryConflictDialog
        conflict={selectedConflict}
        busy={Boolean(selectedConflict && busyId === selectedConflict.candidate_id)}
        onConfirm={confirmConflict}
        onClose={() => setSelectedConflict(null)}
      />
      <ItemActionDialog
        action={itemAction}
        busy={Boolean(itemAction && busyId === `${itemAction.kind}-${itemAction.item.memory_id}`)}
        zh={zh}
        onConfirm={() => void confirmItemAction()}
        onClose={() => setItemAction(null)}
      />
    </main>
  );
}

function MemoryItemCard({
  item,
  zh,
  disabled,
  onDeactivate,
  onDelete,
}: {
  item: MemoryItem;
  zh: boolean;
  disabled: boolean;
  onDeactivate: () => void;
  onDelete: () => void;
}) {
  return (
    <article className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium">{item.key}</h3>
            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-300">
              {zh ? "生效" : "Active"}
            </span>
          </div>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed">{item.value}</p>
          <div className="mt-4">
            <MemorySourceDetails
              scope={item.scope}
              scopeId={item.scope_id}
              subjectId={item.subject_id}
              kcId={item.kc_id}
              provenance={item.provenance}
              sensitivity={item.sensitivity}
              evidenceRefs={item.evidence_refs}
              sourceRef={item.source_ref}
              confidence={item.confidence}
              compact
            />
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button type="button" onClick={onDeactivate} disabled={disabled} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 text-xs font-medium hover:bg-[var(--muted)] disabled:opacity-50">
            <Archive aria-hidden="true" className="h-3.5 w-3.5" />
            {zh ? "停用" : "Deactivate"}
          </button>
          <button type="button" onClick={onDelete} disabled={disabled} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-[var(--destructive)]/40 px-3 text-xs font-medium text-[var(--destructive)] hover:bg-[var(--destructive)]/10 disabled:opacity-50">
            <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
            {zh ? "删除" : "Delete"}
          </button>
        </div>
      </div>
    </article>
  );
}

function ItemActionDialog({
  action,
  busy,
  zh,
  onConfirm,
  onClose,
}: {
  action: ItemAction | null;
  busy: boolean;
  zh: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const deleting = action?.kind === "delete";
  return (
    <Modal
      isOpen={Boolean(action)}
      onClose={() => { if (!busy) onClose(); }}
      title={deleting ? (zh ? "删除这条记忆？" : "Delete this memory?") : (zh ? "停用这条记忆？" : "Deactivate this memory?")}
      titleIcon={deleting ? <Trash2 aria-hidden="true" className="h-5 w-5 text-[var(--destructive)]" /> : <Archive aria-hidden="true" className="h-5 w-5 text-amber-500" />}
      width="sm"
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      showCloseButton={!busy}
      footer={<div className="flex justify-end gap-2"><button type="button" data-autofocus onClick={onClose} disabled={busy} className="h-10 rounded-lg border border-[var(--border)] px-4 text-sm font-medium disabled:opacity-50">{zh ? "取消" : "Cancel"}</button><button type="button" onClick={onConfirm} disabled={busy} className={`h-10 rounded-lg px-4 text-sm font-medium text-white disabled:opacity-50 ${deleting ? "bg-[var(--destructive)]" : "bg-amber-600"}`}>{busy ? (zh ? "处理中…" : "Working…") : deleting ? (zh ? "确认删除" : "Delete") : (zh ? "确认停用" : "Deactivate")}</button></div>}
    >
      <div className="space-y-3 p-5 text-sm leading-relaxed text-[var(--muted-foreground)]">
        <p className="font-medium text-[var(--foreground)]">{action?.item.key}</p>
        <p>{deleting
          ? (zh ? "删除会立即阻止这条记忆被再次召回，并使当前长期记忆索引代次失效。内容不会出现在删除审计中。" : "Deletion immediately prevents recall and invalidates the current long-term memory index generation. Deleted content is not retained in the deletion audit.")
          : (zh ? "停用会保留历史记录，但后续生成不再召回它。" : "Deactivation preserves history, but future generation will no longer recall it.")}</p>
      </div>
    </Modal>
  );
}

function Section({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children: React.ReactNode }) {
  return <section className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5"><p className="text-[11px] font-medium tracking-[0.16em] text-[var(--primary)]">{eyebrow}</p><h2 className="mt-2 text-lg font-semibold">{title}</h2><p className="mt-1 mb-4 text-sm leading-relaxed text-[var(--muted-foreground)]">{description}</p>{children}</section>;
}

function Metric({ label, value, icon: Icon }: { label: string; value: number; icon: typeof ShieldCheck }) {
  return <article className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"><div className="flex items-center gap-2 text-xs text-[var(--muted-foreground)]"><Icon aria-hidden="true" className="h-4 w-4 text-[var(--primary)]" />{label}</div><p className="mt-4 text-2xl font-semibold tabular-nums">{value}</p></article>;
}

function Empty({ text }: { text: string }) {
  return <p className="rounded-xl border border-dashed border-[var(--border)] px-4 py-7 text-center text-sm text-[var(--muted-foreground)]">{text}</p>;
}

function AccessField({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-[var(--muted-foreground)]">{label}</dt><dd className="mt-1 break-words font-medium">{value}</dd></div>;
}

function humanizeToken(value: string): string {
  return value.replaceAll("_", " ");
}

function formatAccessTime(value: string, zh: boolean): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return zh ? "时间不可用" : "Time unavailable";
  return new Intl.DateTimeFormat(zh ? "zh-CN" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function MemoryManagerSkeleton({ zh }: { zh: boolean }) {
  return <main className="w-full py-4" aria-busy="true" aria-label={zh ? "正在加载记忆" : "Loading memory"}><div className="h-9 w-48 animate-pulse rounded bg-[var(--muted)]" /><div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((item) => <div key={item} className="h-24 animate-pulse rounded-xl bg-[var(--muted)]/60" />)}</div><div className="mt-6 h-72 animate-pulse rounded-2xl bg-[var(--muted)]/45" /><p role="status" className="sr-only">{zh ? "正在加载记忆" : "Loading memory"}</p></main>;
}

function assertionLabel(state: string, zh: boolean): string {
  if (state === "inferred_confirmed") return zh ? "推断·已确认" : "Inference · confirmed";
  return zh ? "已验证" : "Verified";
}

function messageFrom(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}
