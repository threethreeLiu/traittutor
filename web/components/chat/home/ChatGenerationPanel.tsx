"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { FileUp, Loader2, RotateCcw, Sparkles, Square, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  createTraitTutorGenerationTask,
  cancelTraitTutorGenerationTask,
  analyzeTraitTutorMaterial,
  generationErrorMessage,
  getTraitTutorGenerationTask,
  listTraitProfiles,
  prepareTraitTutorMaterial,
  retryTraitTutorGenerationTask,
  subscribeTraitTutorGeneration,
  traitTutorGenerationTaskHandle,
  type GenerateKind,
  type GenerateSuiteResult,
  type GenerationTaskAccepted,
  type PreparedLearningMaterial,
  type MaterialAnalysis,
  type TraitProfile,
} from "@/lib/traittutor-api";
import { removePendingGenerationTask, readPendingGenerationTasks, writePendingGenerationTask, type PendingGenerationTask } from "@/lib/traittutor-generation-task-storage";
import { TraitTutorIcon, type TraitTutorIconName } from "@/components/brand/TraitTutorIcon";
import { GenerationSourceSummary, MaterialAnalysisSummary } from "@/components/traittutor/MaterialAnalysisSummary";
import { WhyThisGeneration } from "@/components/personalization/WhyThisGeneration";

const COPY: Record<GenerateKind, { zh: { title: string; hint: string }; en: { title: string; hint: string }; icon: TraitTutorIconName }> = {
  courseware: { zh: { title: "改写课件", hint: "上传材料或粘贴正文，TraitTutor 会先在此对话中生成预览。" }, en: { title: "Rewrite courseware", hint: "Upload material or paste text to generate a preview in this chat." }, icon: "courseware" },
  flashcards: { zh: { title: "生成闪卡", hint: "上传材料或粘贴正文，生成主动回忆卡组预览。" }, en: { title: "Generate flashcards", hint: "Upload material or paste text to generate an active-recall card preview." }, icon: "standard" },
  quiz: { zh: { title: "生成 Quiz", hint: "上传材料或粘贴正文，生成可练习的题目预览。" }, en: { title: "Generate quiz", hint: "Upload material or paste text to generate practice questions." }, icon: "measurement" },
};

export default function ChatGenerationPanel({ kind, onClose, sessionId }: { kind: GenerateKind; onClose: () => void; sessionId?: string | null }) {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const copy = { ...COPY[kind][zh ? "zh" : "en"], icon: COPY[kind].icon };
  const [text, setText] = useState("");
  const [prepared, setPrepared] = useState<PreparedLearningMaterial | null>(null);
  const [profile, setProfile] = useState<TraitProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<GenerateSuiteResult | null>(null);
  const [analysis, setAnalysis] = useState<MaterialAnalysis | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<"queued" | "running" | "failed" | "cancelled" | "interrupted" | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [pendingTasks, setPendingTasks] = useState<PendingGenerationTask[]>([]);
  const cleanupRef = useRef(new Map<string, () => void>());
  const generatedSessionId = useRef(`material-${crypto.randomUUID()}`);
  const effectiveSessionId = sessionId || generatedSessionId.current;
  const taskScope = `chat:${effectiveSessionId}:${kind}`;

  useEffect(() => { listTraitProfiles().then((rows) => setProfile(rows[0] ?? null)).catch(() => undefined); }, []);
  useEffect(() => () => cleanupRef.current.forEach((cleanup) => cleanup()), []);

  const updatePendingTask = useCallback((task: PendingGenerationTask) => {
    writePendingGenerationTask(taskScope, task);
    setPendingTasks((tasks) => [...tasks.filter((entry) => entry.generationId !== task.generationId), task]);
  }, [taskScope]);

  const removeTask = useCallback((generationId: string) => {
    cleanupRef.current.get(generationId)?.(); cleanupRef.current.delete(generationId);
    removePendingGenerationTask(taskScope, generationId);
    setPendingTasks((tasks) => tasks.filter((task) => task.generationId !== generationId));
  }, [taskScope]);

  const watchTask = useCallback((task: GenerationTaskAccepted) => {
    cleanupRef.current.get(task.generation_id)?.();
    updatePendingTask({ generationId: task.generation_id, status: task.status, surface: "chat", sessionId: effectiveSessionId, createdAt: new Date().toISOString() });
    setActiveTaskId(task.generation_id); setTaskStatus(task.status); setRetryable(false); setBusy(true);
    const unsubscribe = subscribeTraitTutorGeneration(task, (event) => {
      setStage(event.message);
      if (event.type === "retry_queued") { updatePendingTask({ generationId: task.generation_id, status: "queued", surface: "chat", sessionId: effectiveSessionId }); setTaskStatus("queued"); setRetryable(false); setBusy(true); }
      if (event.type === "generation_started") { updatePendingTask({ generationId: task.generation_id, status: "running", surface: "chat", sessionId: effectiveSessionId }); setTaskStatus("running"); }
      if (event.type === "cancelled" || event.type === "interrupted" || event.type === "failed") {
        if (event.type === "cancelled") removeTask(task.generation_id);
        else updatePendingTask({ generationId: task.generation_id, status: event.type, surface: "chat", sessionId: effectiveSessionId });
        setTaskStatus(event.type); setRetryable(Boolean(event.data.retryable)); setBusy(false);
      }
    }, () => setStage(zh ? "生成连接中断，正在恢复进度…" : "Generation connection interrupted; resuming progress…"));
    const poll = window.setInterval(async () => {
      try {
        const loaded = await getTraitTutorGenerationTask(task.generation_id);
        if ("result" in loaded) {
          window.clearInterval(poll); unsubscribe(); removeTask(task.generation_id);
          setResult(loaded); setBusy(false); setStage(""); setTaskStatus(null); setActiveTaskId(null);
        } else {
          setTaskStatus(loaded.status); setRetryable(loaded.retryable);
          if (["failed", "cancelled", "interrupted"].includes(loaded.status)) {
            window.clearInterval(poll); unsubscribe(); setBusy(false);
            if (loaded.status === "cancelled") removeTask(task.generation_id);
            else updatePendingTask({ generationId: task.generation_id, status: loaded.status, surface: "chat", sessionId: effectiveSessionId });
            if (loaded.status !== "cancelled") setError(generationErrorMessage(loaded.error_code || loaded.error, Boolean(zh)));
            else setStage(zh ? "生成已取消" : "Generation cancelled");
          }
        }
      } catch (cause) { window.clearInterval(poll); unsubscribe(); setBusy(false); setError(generationErrorMessage(cause, Boolean(zh))); }
    }, 800);
    cleanupRef.current.set(task.generation_id, () => { window.clearInterval(poll); unsubscribe(); });
  }, [effectiveSessionId, removeTask, updatePendingTask, zh]);

  useEffect(() => {
    let disposed = false;
    const pending = readPendingGenerationTasks(taskScope);
    if (!pending.length) return;
    setPendingTasks(pending);
    for (const task of pending) void getTraitTutorGenerationTask(task.generationId).then((loaded) => {
      if (disposed) return;
      if ("result" in loaded) {
        removeTask(task.generationId); setResult(loaded); return;
      }
      if (loaded.status === "cancelled") { removeTask(task.generationId); return; }
      setActiveTaskId(loaded.generation_id); setTaskStatus(loaded.status); setRetryable(loaded.retryable);
      if (loaded.status === "queued" || loaded.status === "running") watchTask(traitTutorGenerationTaskHandle(loaded.generation_id));
      else if (loaded.error) setError(generationErrorMessage(loaded.error_code || loaded.error, Boolean(zh)));
    }).catch(() => removeTask(task.generationId));
    return () => { disposed = true; };
  }, [removeTask, taskScope, watchTask, zh]);

  const upload = useCallback(async (file: File | null) => {
    if (!file) return;
    setBusy(true); setError(""); setStage(zh ? "正在转换为 PDF 并按页切片…" : "Converting to PDF and slicing pages…");
    try {
      const next = await prepareTraitTutorMaterial(file);
      setPrepared(next); setText(""); setStage(zh ? "正在识别学科与年级…" : "Identifying subject and grade…");
      setAnalysis(await analyzeTraitTutorMaterial({ session_id: effectiveSessionId, material: next }));
    }
    catch (cause) { setError(generationErrorMessage(cause, Boolean(zh))); }
    finally { setBusy(false); setStage(""); }
  }, [effectiveSessionId, zh]);

  const generate = useCallback(async () => {
    if (!prepared && !text.trim()) return;
    setBusy(true); setError(""); setResult(null); setStage(zh ? "正在准备个性化学习内容…" : "Preparing personalized learning content…");
    try {
      const material = prepared ?? { source_type: "paste" as const, title: copy.title, text };
      const resolvedAnalysis = analysis ?? await analyzeTraitTutorMaterial({ session_id: effectiveSessionId, material });
      setAnalysis(resolvedAnalysis);
      const task = await createTraitTutorGenerationTask({ generation_type: kind, material, learner_profile: profile ?? undefined, options: { language: i18n.language || "en", session_id: effectiveSessionId, analysis_id: resolvedAnalysis.analysis_id } });
      watchTask(task);
    } catch (cause) { setBusy(false); setError(generationErrorMessage(cause, Boolean(zh))); }
  }, [analysis, copy.title, effectiveSessionId, i18n.language, kind, prepared, profile, text, watchTask, zh]);

  const cancelTask = useCallback(async () => {
    if (!activeTaskId) return;
    setStage(zh ? "正在取消生成…" : "Cancelling generation…");
    try { const cancelled = await cancelTraitTutorGenerationTask(activeTaskId); setTaskStatus(cancelled.status); }
    catch (cause) { setError(generationErrorMessage(cause, Boolean(zh))); }
  }, [activeTaskId, zh]);

  const retryTask = useCallback(async () => {
    if (!activeTaskId) return;
    setError(""); setStage(zh ? "正在重新排队…" : "Retrying in queue…");
    try { watchTask(await retryTraitTutorGenerationTask(activeTaskId)); }
    catch (cause) { setError(generationErrorMessage(cause, Boolean(zh))); }
  }, [activeTaskId, watchTask, zh]);

  const destination = `/space/${kind}`;
  const summary = result?.result.title || "已完成生成";
  return <section className="mx-auto mb-3 w-full max-w-[960px] rounded-xl border border-teal-500/35 bg-teal-500/[0.06] p-4 shadow-sm">
    <div className="flex items-start gap-3"><span className="mt-0.5 rounded-lg bg-teal-500/10 p-2"><TraitTutorIcon name={copy.icon} size={19} /></span><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3"><h2 className="font-medium">{copy.title}</h2><button type="button" onClick={onClose} aria-label={zh ? "关闭生成面板" : "Close generation panel"} className="rounded p-1 text-[var(--muted-foreground)] hover:bg-[var(--accent)]"><X size={16} /></button></div><p className="mt-1 text-[12px] text-[var(--muted-foreground)]">{copy.hint}</p></div></div>
    {!result ? <><textarea value={text} disabled={Boolean(prepared) || busy} onChange={(event) => { setText(event.target.value); setAnalysis(null); }} placeholder={zh ? "粘贴学习材料、章节内容或学习目标…" : "Paste learning material, a section, or an objective…"} className="mt-3 min-h-24 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 text-[13px] disabled:opacity-50" /><div className="mt-3 flex flex-wrap items-center gap-2"><label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-[13px] hover:bg-[var(--accent)]"><FileUp size={15} />{zh ? "上传 PDF / Word" : "Upload PDF / Word"}<input type="file" className="sr-only" accept=".pdf,.doc,.docx,.rtf,.odt,.xls,.xlsx,.ods,.ppt,.pptx,.odp,.txt,.md,.csv,.html,.htm" onChange={(event) => void upload(event.target.files?.[0] ?? null)} /></label>{prepared ? <span className="text-[12px] text-teal-700 dark:text-teal-300">{prepared.title} · {String(prepared.metadata.page_count ?? 0)} {zh ? "页切片" : "page slices"}</span> : null}<button type="button" disabled={busy || (!prepared && !text.trim())} onClick={() => void generate()} className="inline-flex h-9 items-center gap-2 rounded-md bg-[var(--primary)] px-3 text-[13px] font-medium text-[var(--primary-foreground)] disabled:opacity-50">{busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}{busy ? (stage || (zh ? "处理中…" : "Working…")) : (zh ? "在对话中生成" : "Generate in chat")}</button>{activeTaskId && pendingTasks.some((task) => task.generationId === activeTaskId && (task.status === "queued" || task.status === "running")) ? <button type="button" onClick={() => void cancelTask()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px]"><Square size={14} />{zh ? "取消" : "Cancel"}</button> : null}{retryable && activeTaskId ? <button type="button" onClick={() => void retryTask()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px]"><RotateCcw size={14} />{zh ? "重试" : "Retry"}</button> : null}</div>{taskStatus ? <p className="mt-2 text-[12px] text-[var(--muted-foreground)]" role="status">{zh ? `任务状态：${taskStatus === "queued" ? "排队中" : taskStatus === "running" ? "生成中" : taskStatus === "cancelled" ? "已取消" : taskStatus === "interrupted" ? "已中断" : "失败"}` : `Task status: ${taskStatus}`}</p> : null}{analysis ? <MaterialAnalysisSummary analysis={analysis} /> : null}</> : <div className="mt-3 rounded-lg border border-[var(--border)] bg-[var(--background)] p-3"><p className="text-[13px] font-medium">{zh ? "生成预览：" : "Generation preview: "}{String(summary)}</p><p className="mt-1 text-[12px] text-[var(--muted-foreground)]">{zh ? "已保存为学习素材；可进入对应页面继续阅读、复习或作答。" : "Saved as learning material. Open the workspace to read, review, or practice."}</p><Link href={destination} className="mt-3 inline-flex h-9 items-center rounded-md bg-[var(--primary)] px-3 text-[13px] font-medium text-[var(--primary-foreground)]">{zh ? "打开" : "Open "}{copy.title}{zh ? "工作台" : " workspace"}</Link></div>}
    {result ? <div className="mt-3"><GenerationSourceSummary result={result} /><WhyThisGeneration snapshot={result.personalization_context_snapshot} plan={result.teaching_strategy_plan} /></div> : null}
    {pendingTasks.length > 1 ? <div className="mt-3 rounded-md border border-[var(--border)] p-2" aria-label={zh ? "进行中的生成任务" : "Pending generation tasks"}><p className="text-[12px] font-medium">{zh ? "进行中的生成任务" : "Pending generation tasks"}</p>{pendingTasks.map((task) => <button key={task.generationId} type="button" onClick={() => { setActiveTaskId(task.generationId); setTaskStatus(task.status ?? "queued"); setRetryable(task.status === "failed" || task.status === "interrupted"); }} className={`mt-1 block w-full rounded px-2 py-1 text-left text-[12px] ${activeTaskId === task.generationId ? "bg-[var(--accent)]" : "hover:bg-[var(--accent)]/60"}`}>{task.generationId.slice(0, 8)} · {task.status === "queued" ? (zh ? "排队中" : "Queued") : task.status === "running" ? (zh ? "生成中" : "Running") : task.status === "interrupted" ? (zh ? "已中断" : "Interrupted") : (zh ? "失败，可重试" : "Failed, retryable")}</button>)}</div> : null}
    {error ? <p className="mt-2 text-[12px] text-red-600">{error}</p> : null}
  </section>;
}
