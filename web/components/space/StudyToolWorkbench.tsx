"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, FileUp, Loader2, RotateCcw, Sparkles, Square } from "lucide-react";
import { TraitTutorIcon, type TraitTutorIconName } from "@/components/brand/TraitTutorIcon";
import { GenerationSourceSummary, MaterialAnalysisSummary } from "@/components/traittutor/MaterialAnalysisSummary";
import { WhyThisGeneration } from "@/components/personalization/WhyThisGeneration";

import {
  createLearningPack,
  analyzeTraitTutorMaterial,
  createTraitTutorGenerationTask,
  cancelTraitTutorGenerationTask,
  createTraitProfile,
  generationErrorMessage,
  getTraitTutorGenerationTask,
  listTraitProfiles,
  prepareTraitTutorMaterial,
  retryTraitTutorGenerationTask,
  saveGenerationResult,
  subscribeTraitTutorGeneration,
  traitTutorGenerationTaskHandle,
  updateLearningPack,
  type GenerateKind,
  type GenerateSuiteResult,
  type GenerationTaskAccepted,
  type MaterialAnalysis,
  type TraitProfile,
} from "@/lib/traittutor-api";
import { removePendingGenerationTask, readPendingGenerationTasks, writePendingGenerationTask, type PendingGenerationTask } from "@/lib/traittutor-generation-task-storage";

type ToolKind = GenerateKind;

const CONFIG: Record<ToolKind, { title: string; description: string; icon: TraitTutorIconName }> = {
  courseware: { title: "课件", description: "将材料转为可逐节学习的课件。", icon: "courseware" },
  flashcards: { title: "Flashcard 学习", description: "从材料创建主动回忆卡组。", icon: "standard" },
  quiz: { title: "Quiz 测验", description: "生成、作答并复盘练习题。", icon: "measurement" },
};

export default function StudyToolWorkbench({ kind }: { kind: ToolKind }) {
  const { i18n } = useTranslation();
  const zh = i18n.language?.startsWith("zh");
  const config = CONFIG[kind];
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [uploadedMaterial, setUploadedMaterial] = useState<Awaited<ReturnType<typeof prepareTraitTutorMaterial>> | null>(null);
  const [analysis, setAnalysis] = useState<MaterialAnalysis | null>(null);
  const [uploading, setUploading] = useState(false);
  const [profile, setProfile] = useState<TraitProfile | null>(null);
  const [quizMode, setQuizMode] = useState("material");
  const [questionCount, setQuestionCount] = useState("8");
  const [difficulty, setDifficulty] = useState("mixed");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<GenerateSuiteResult | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [taskStatus, setTaskStatus] = useState<"queued" | "running" | "failed" | "cancelled" | "interrupted" | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [pendingTasks, setPendingTasks] = useState<PendingGenerationTask[]>([]);
  const [packId, setPackId] = useState("");
  const [cardIndex, setCardIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [quizIndex, setQuizIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [checkedQuestions, setCheckedQuestions] = useState<Record<number, boolean>>({});
  const [submitted, setSubmitted] = useState(false);
  const cleanupGenerationRef = useRef(new Map<string, () => void>());
  const materialSessionId = useRef(`space-${crypto.randomUUID()}`);
  const taskScope = `space:${kind}`;

  useEffect(() => {
    listTraitProfiles().then((profiles) => setProfile(profiles[0] ?? null)).catch(() => undefined);
  }, []);

  useEffect(() => () => cleanupGenerationRef.current.forEach((cleanup) => cleanup()), []);

  const updatePendingTask = useCallback((task: PendingGenerationTask) => {
    writePendingGenerationTask(taskScope, task);
    setPendingTasks((tasks) => [...tasks.filter((entry) => entry.generationId !== task.generationId), task]);
  }, [taskScope]);

  const removeTask = useCallback((generationId: string) => {
    cleanupGenerationRef.current.get(generationId)?.(); cleanupGenerationRef.current.delete(generationId);
    removePendingGenerationTask(taskScope, generationId);
    setPendingTasks((tasks) => tasks.filter((task) => task.generationId !== generationId));
  }, [taskScope]);

  const watchTask = useCallback(async (task: GenerationTaskAccepted, packIdForTask: string) => {
    cleanupGenerationRef.current.get(task.generation_id)?.();
    updatePendingTask({ generationId: task.generation_id, packId: packIdForTask, status: task.status, surface: "space", sessionId: materialSessionId.current, createdAt: new Date().toISOString() });
    setActiveTaskId(task.generation_id); setTaskStatus(task.status); setRetryable(false); setBusy(true);
    const unsubscribe = subscribeTraitTutorGeneration(task, (event) => {
      setStage(event.message);
      if (event.type === "retry_queued") { updatePendingTask({ generationId: task.generation_id, packId: packIdForTask, status: "queued", surface: "space", sessionId: materialSessionId.current }); setTaskStatus("queued"); setRetryable(false); setBusy(true); }
      if (event.type === "generation_started") { updatePendingTask({ generationId: task.generation_id, packId: packIdForTask, status: "running", surface: "space", sessionId: materialSessionId.current }); setTaskStatus("running"); }
      if (event.type === "failed" || event.type === "cancelled" || event.type === "interrupted") {
        if (event.type === "cancelled") removeTask(task.generation_id);
        else updatePendingTask({ generationId: task.generation_id, packId: packIdForTask, status: event.type, surface: "space", sessionId: materialSessionId.current });
        setTaskStatus(event.type); setRetryable(Boolean(event.data.retryable)); setBusy(false);
      }
    }, () => setStage(zh ? "生成连接中断，正在恢复进度…" : "Generation connection interrupted; resuming progress…"));
    const poll = window.setInterval(async () => {
      try {
        const loaded = await getTraitTutorGenerationTask(task.generation_id);
        if ("result" in loaded) {
          window.clearInterval(poll); unsubscribe();
          setResult(loaded); setStage(zh ? "正在保存到学习空间" : "Saving to learning space");
          // Attach the verified server-owned artifact before writing a
          // convenience copy to the question bank/notebook. A downstream
          // notebook outage must never discard the answer key needed for
          // quiz grading, BKT updates, or flashcard review events.
          await updateLearningPack(packIdForTask, { generation_id: loaded.generation_id });
          try { await saveGenerationResult(loaded); } catch { /* artifact remains usable in the learning pack */ }
          removeTask(task.generation_id);
          setBusy(false); setStage(zh ? "已生成并保存" : "Generated and saved"); setTaskStatus(null); setActiveTaskId(null);
        } else {
          setTaskStatus(loaded.status); setRetryable(loaded.retryable);
          if (["failed", "cancelled", "interrupted"].includes(loaded.status)) {
            window.clearInterval(poll); unsubscribe(); setBusy(false);
            if (loaded.status === "cancelled") removeTask(task.generation_id);
            else updatePendingTask({ generationId: task.generation_id, packId: packIdForTask, status: loaded.status, surface: "space", sessionId: materialSessionId.current });
            if (loaded.status !== "cancelled") setError(generationErrorMessage(loaded.error_code || loaded.error, Boolean(zh)));
            else setStage(zh ? "生成已取消" : "Generation cancelled");
          }
        }
      } catch (cause) { window.clearInterval(poll); unsubscribe(); setBusy(false); setError(generationErrorMessage(cause, Boolean(zh))); }
    }, 800);
    cleanupGenerationRef.current.set(task.generation_id, () => { window.clearInterval(poll); unsubscribe(); });
  }, [removeTask, taskScope, updatePendingTask, zh]);

  useEffect(() => {
    let disposed = false;
    const pending = readPendingGenerationTasks(taskScope);
    if (!pending.length) return;
    setPendingTasks(pending);
    for (const task of pending) void getTraitTutorGenerationTask(task.generationId).then(async (loaded) => {
      if (disposed) return;
      if ("result" in loaded) {
        if (!task.packId) { removeTask(task.generationId); return; }
        setResult(loaded); setStage(zh ? "正在保存到学习空间" : "Saving to learning space");
        await updateLearningPack(task.packId, { generation_id: loaded.generation_id });
        try { await saveGenerationResult(loaded); } catch { /* artifact remains usable in the learning pack */ }
        if (!disposed) { removeTask(task.generationId); setStage(zh ? "已生成并保存" : "Generated and saved"); }
        return;
      }
      if (loaded.status === "cancelled" || !task.packId) { removeTask(task.generationId); return; }
      setPackId(task.packId); setActiveTaskId(loaded.generation_id); setTaskStatus(loaded.status); setRetryable(loaded.retryable);
      if (loaded.status === "queued" || loaded.status === "running") await watchTask(traitTutorGenerationTaskHandle(loaded.generation_id), task.packId);
      else if (loaded.error) setError(generationErrorMessage(loaded.error_code || loaded.error, Boolean(zh)));
    }).catch(() => removeTask(task.generationId));
    return () => { disposed = true; };
  }, [removeTask, taskScope, watchTask, zh]);

  const generate = useCallback(async () => {
    if (!text.trim() && !uploadedMaterial) return;
    setBusy(true); setError(""); setResult(null); setStage(zh ? "正在创建学习包" : "Creating learning pack");
    try {
      const material = uploadedMaterial ?? { source_type: "paste" as const, title: title.trim() || config.title, text };
      const resolvedAnalysis = analysis ?? await analyzeTraitTutorMaterial({ session_id: materialSessionId.current, material });
      setAnalysis(resolvedAnalysis);
      const pack = await createLearningPack({ title: material.title, material: { ...material, metadata: { ...("metadata" in material ? material.metadata : {}), learner_analysis: resolvedAnalysis } }, profile_id: profile?.profile_id });
      setPackId(pack.pack_id);
      const task = await createTraitTutorGenerationTask({
        generation_type: kind,
        material,
        learner_profile: profile ?? undefined,
        options: { ...(kind === "quiz" ? { mode: quizMode, question_count: Number(questionCount), difficulty } : { language: zh ? "zh-CN" : "en" }), session_id: materialSessionId.current, analysis_id: resolvedAnalysis.analysis_id },
      });
      await watchTask(task, pack.pack_id);
    } catch (cause) {
      setBusy(false); setError(generationErrorMessage(cause, Boolean(zh)));
    }
  }, [analysis, config.title, difficulty, kind, profile, questionCount, quizMode, text, title, uploadedMaterial, watchTask, zh]);

  const cancelTask = useCallback(async () => {
    if (!activeTaskId) return;
    setStage(zh ? "正在取消生成…" : "Cancelling generation…");
    try { const cancelled = await cancelTraitTutorGenerationTask(activeTaskId); setTaskStatus(cancelled.status); }
    catch (cause) { setError(generationErrorMessage(cause, Boolean(zh))); }
  }, [activeTaskId, zh]);

  const retryTask = useCallback(async () => {
    if (!activeTaskId || !packId) return;
    setError(""); setStage(zh ? "正在重新排队…" : "Retrying in queue…");
    try { await watchTask(await retryTraitTutorGenerationTask(activeTaskId), packId); }
    catch (cause) { setError(generationErrorMessage(cause, Boolean(zh))); }
  }, [activeTaskId, packId, watchTask, zh]);

  const chooseMaterial = useCallback(async (file: File | null) => {
    if (!file) return;
    setUploading(true); setError("");
    try {
      const prepared = await prepareTraitTutorMaterial(file);
      setUploadedMaterial(prepared); setTitle((value) => value || prepared.title);
      setText("");
      setStage(zh ? "正在识别学科与年级" : "Identifying subject and grade");
      setAnalysis(await analyzeTraitTutorMaterial({ session_id: materialSessionId.current, material: prepared }));
    } catch (cause) {
      setError(generationErrorMessage(cause, Boolean(zh)));
    } finally { setUploading(false); }
  }, [zh]);

  const items = useMemo(() => result?.result.items ?? [], [result]);
  const currentCard = items[cardIndex];
  const currentQuestion = items[quizIndex];
  const questionOptions = Array.isArray(currentQuestion?.options) ? currentQuestion.options as Array<{ text?: unknown }> : [];
  const questionChecked = Boolean(checkedQuestions[quizIndex] || submitted);
  const selectedOption = questionOptions.find((option) => String(option.text ?? "") === answers[quizIndex]);
  const answerIsCorrect = questionOptions.length
    ? Boolean((selectedOption as { is_correct?: unknown } | undefined)?.is_correct)
    : Boolean(
      answers[quizIndex]?.trim()
      && answers[quizIndex].trim().toLocaleLowerCase() === String(currentQuestion?.correct_answer ?? "").trim().toLocaleLowerCase(),
    );

  const markCard = async (state: string) => {
    if (packId && currentCard) await updateLearningPack(packId, { flashcard_progress: { [String(currentCard.node_id || cardIndex)]: state }, review_id: crypto.randomUUID() });
    setRevealed(false); setCardIndex((value) => Math.min(value + 1, Math.max(0, items.length - 1)));
  };

  const finishQuiz = async () => {
    setSubmitted(true);
    if (packId) await updateLearningPack(packId, { quiz_attempt: { submitted_at: new Date().toISOString(), answers, total: items.length } });
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 pb-10">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--border)] pb-4">
        <div className="flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-lg bg-teal-500/10"><TraitTutorIcon name={config.icon} size={20} /></span><div><h1 className="text-[18px] font-semibold">{zh ? config.title : kind}</h1><p className="text-[12.5px] text-[var(--muted-foreground)]">{zh ? config.description : config.description}</p></div></div>
        <span className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[12.5px] text-[var(--muted-foreground)]"><Bot size={15} />{zh ? "自动匹配模型" : "Automatic model selection"}</span>
      </header>

      {!result ? <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4 space-y-4">
        {kind === "quiz" ? <div className="grid gap-3 sm:grid-cols-3"><label className="text-[12px] text-[var(--muted-foreground)]">{zh ? "生成模式" : "Mode"}<select value={quizMode} onChange={(e) => setQuizMode(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[13px]"><option value="material">{zh ? "基于材料" : "Material"}</option><option value="variation">{zh ? "题目变式" : "Question variation"}</option><option value="objective">{zh ? "学习目标" : "Objective"}</option></select></label><label className="text-[12px] text-[var(--muted-foreground)]">{zh ? "题量" : "Questions"}<select value={questionCount} onChange={(e) => setQuestionCount(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[13px]"><option value="5">5</option><option value="8">8</option><option value="12">12</option></select></label><label className="text-[12px] text-[var(--muted-foreground)]">{zh ? "难度" : "Difficulty"}<select value={difficulty} onChange={(e) => setDifficulty(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[13px]"><option value="easy">{zh ? "基础" : "Easy"}</option><option value="mixed">{zh ? "混合" : "Mixed"}</option><option value="hard">{zh ? "挑战" : "Hard"}</option></select></label></div> : null}
        <label className="block text-[12px] text-[var(--muted-foreground)]">{zh ? "学习包名称" : "Learning pack name"}<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={zh ? "例如：细胞生物学第一章" : "e.g. Cell biology chapter 1"} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-[13px]" /></label>
        <label className="block text-[12px] text-[var(--muted-foreground)]">{quizMode === "objective" ? (zh ? "学习目标" : "Learning objective") : (zh ? "材料或已有题目" : "Material or existing question")}<textarea value={text} disabled={Boolean(uploadedMaterial)} onChange={(e) => { setText(e.target.value); setAnalysis(null); }} placeholder={zh ? "粘贴材料、题目或学习目标。" : "Paste material, questions, or a learning objective."} className="mt-1 min-h-52 w-full rounded-md border border-[var(--border)] bg-[var(--background)] p-3 text-[13px] leading-relaxed disabled:opacity-50" /></label>
        <label className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px] hover:bg-[var(--accent)]"><FileUp size={15} />{uploading ? (zh ? "正在转为 PDF…" : "Converting to PDF…") : (zh ? "上传 PDF / Word / PPT / Excel" : "Upload PDF / Word / PPT / Excel")}<input type="file" className="sr-only" accept=".pdf,.doc,.docx,.rtf,.odt,.xls,.xlsx,.ods,.ppt,.pptx,.odp,.txt,.md,.csv,.html,.htm" onChange={(event) => void chooseMaterial(event.target.files?.[0] ?? null)} /></label>
        {uploadedMaterial ? <p className="text-[12px] text-teal-700 dark:text-teal-300">{zh ? `已准备 ${uploadedMaterial.title}（${String(uploadedMaterial.metadata.page_count ?? 0)} 页 PDF 切片）` : `${uploadedMaterial.title} is ready (${String(uploadedMaterial.metadata.page_count ?? 0)} PDF pages)`}</p> : null}
        {analysis ? <MaterialAnalysisSummary analysis={analysis} /> : null}
        {profile ? <p className="text-[12px] text-teal-700 dark:text-teal-300">{zh ? "将使用当前学习画像和自动匹配的学习角色。" : "Your current learning profile and matched persona will be used."}</p> : <p className="text-[12px] text-[var(--muted-foreground)]">{zh ? "未找到学习画像，将使用中性教学策略。" : "No profile found; neutral teaching strategy will be used."}</p>}
        {error ? <p className="flex items-center gap-1.5 text-[12px] text-red-600"><TraitTutorIcon name="mismatched" size={15} strokeWidth={2} />{error}</p> : null}
        <div className="flex flex-wrap items-center gap-2"><button type="button" disabled={(!text.trim() && !uploadedMaterial) || busy || uploading} onClick={() => void generate()} className="inline-flex h-9 items-center gap-2 rounded-md bg-[var(--primary)] px-3 text-[13px] font-medium text-[var(--primary-foreground)] disabled:opacity-50">{busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}{busy ? stage : (error ? (zh ? "重新生成" : "Try again") : (zh ? "开始生成" : "Generate"))}</button>{activeTaskId && pendingTasks.some((task) => task.generationId === activeTaskId && (task.status === "queued" || task.status === "running")) ? <button type="button" onClick={() => void cancelTask()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px]"><Square size={14} />{zh ? "取消" : "Cancel"}</button> : null}{retryable && activeTaskId ? <button type="button" onClick={() => void retryTask()} className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px]"><RotateCcw size={14} />{zh ? "重试" : "Retry"}</button> : null}</div>
        {taskStatus ? <p className="text-[12px] text-[var(--muted-foreground)]" role="status">{zh ? `任务状态：${taskStatus === "queued" ? "排队中" : taskStatus === "running" ? "生成中" : taskStatus === "cancelled" ? "已取消" : taskStatus === "interrupted" ? "已中断" : "失败"}` : `Task status: ${taskStatus}`}</p> : null}
        {pendingTasks.length > 1 ? <div className="rounded-md border border-[var(--border)] p-2" aria-label={zh ? "进行中的生成任务" : "Pending generation tasks"}><p className="text-[12px] font-medium">{zh ? "进行中的生成任务" : "Pending generation tasks"}</p>{pendingTasks.map((task) => <button key={task.generationId} type="button" onClick={() => { setActiveTaskId(task.generationId); setPackId(task.packId ?? ""); setTaskStatus(task.status ?? "queued"); setRetryable(task.status === "failed" || task.status === "interrupted"); }} className={`mt-1 block w-full rounded px-2 py-1 text-left text-[12px] ${activeTaskId === task.generationId ? "bg-[var(--accent)]" : "hover:bg-[var(--accent)]/60"}`}>{task.generationId.slice(0, 8)} · {task.status === "queued" ? (zh ? "排队中" : "Queued") : task.status === "running" ? (zh ? "生成中" : "Running") : task.status === "interrupted" ? (zh ? "已中断" : "Interrupted") : (zh ? "失败，可重试" : "Failed, retryable")}</button>)}</div> : null}
      </section> : null}

      {result && kind === "courseware" ? <CoursewareView result={result} zh={Boolean(zh)} /> : null}
      {result && kind === "flashcards" && currentCard ? <section className="overflow-hidden rounded-[28px] border border-[var(--border)] bg-[var(--card)] shadow-[0_22px_55px_-44px_var(--foreground)]"><header className="flex items-center justify-between border-b border-[var(--border)] px-5 py-4 sm:px-7"><div><p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--primary)]">{zh ? "主动回忆" : "Active recall"}</p><h2 className="mt-1 text-[18px] font-semibold">{result.result.title}</h2></div><span className="rounded-full bg-[var(--muted)] px-3 py-1 text-[12px] font-medium text-[var(--muted-foreground)]">{cardIndex + 1} / {items.length}</span></header><div className="p-5 sm:p-7"><LearningImage images={(currentCard.images as Array<{ url?: unknown; alt?: unknown }> | undefined) ?? result.result.images} /><button type="button" onClick={() => setRevealed((value) => !value)} aria-pressed={revealed} className="group min-h-[18rem] w-full rounded-[24px] border border-[var(--border)] bg-[linear-gradient(145deg,var(--background),color-mix(in_srgb,var(--primary)_7%,var(--background)))] p-7 text-left transition duration-300 hover:-translate-y-0.5 hover:border-[var(--primary)]/60 focus:outline-none focus:ring-2 focus:ring-[var(--primary)]/40 sm:p-10"><div className="flex items-center justify-between text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--muted-foreground)]"><span>{revealed ? (zh ? "答案" : "Answer") : (zh ? "想一想" : "Think first")}</span><span className="rounded-full border border-[var(--border)] px-2.5 py-1 transition group-hover:text-[var(--primary)]">{zh ? "点击翻面" : "Flip card"}</span></div><p className="mx-auto mt-12 max-w-2xl text-center text-[clamp(1.25rem,3vw,2rem)] font-medium leading-relaxed">{revealed ? String(currentCard.back) : String(currentCard.front)}</p></button><p className="mt-3 text-center text-[12px] text-[var(--muted-foreground)]">{revealed ? (zh ? "根据记忆强度选择下一步" : "Choose the recall strength that fits") : (zh ? "先在脑中作答，再翻面核对" : "Answer mentally, then flip to check")}</p><div className="mt-5 grid gap-2 sm:grid-cols-3"><button type="button" onClick={() => void markCard("review")} className="h-11 rounded-xl border border-amber-500/35 bg-amber-500/8 px-4 text-[13px] font-medium text-amber-700 transition hover:bg-amber-500/15 dark:text-amber-300">{zh ? "还不熟 · 稍后复习" : "Still learning · Review"}</button><button type="button" onClick={() => void markCard("uncertain")} className="h-11 rounded-xl border border-[var(--border)] px-4 text-[13px] font-medium transition hover:bg-[var(--accent)]">{zh ? "有点模糊" : "A little unsure"}</button><button type="button" onClick={() => void markCard("mastered")} className="h-11 rounded-xl bg-[var(--primary)] px-4 text-[13px] font-semibold text-[var(--primary-foreground)] transition hover:brightness-110">{zh ? "掌握了" : "I know this"}</button></div></div></section> : null}
      {result && kind === "quiz" && currentQuestion ? <section className="overflow-hidden rounded-[28px] border border-[var(--border)] bg-[var(--card)] shadow-[0_22px_55px_-44px_var(--foreground)]"><header className="border-b border-[var(--border)] px-5 py-4 sm:px-7"><div className="flex items-center justify-between gap-4"><div><p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--primary)]">{zh ? "引导式解题" : "Guided solve"}</p><h2 className="mt-1 text-[17px] font-semibold">{zh ? `第 ${quizIndex + 1} 题` : `Question ${quizIndex + 1}`}</h2></div><span className="text-[13px] text-[var(--muted-foreground)]">{quizIndex + 1} / {items.length}</span></div><div className="mt-4 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]"><div className="h-full rounded-full bg-[var(--primary)] transition-all" style={{ width: `${((quizIndex + 1) / items.length) * 100}%` }} /></div></header><div className="p-5 sm:p-7"><LearningImage images={currentQuestion.images as Array<{ url?: unknown; alt?: unknown }> | undefined} /><div className="rounded-2xl border border-[var(--border)] bg-[var(--background)]/55 p-5 sm:p-6"><p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--muted-foreground)]">{zh ? "题目" : "Problem"}</p><p className="mt-3 text-[17px] leading-8">{String(currentQuestion.question)}</p></div>{questionOptions.length ? <div className="mt-5 grid gap-2">{questionOptions.map((option, index) => { const chosen = answers[quizIndex] === String(option.text ?? ""); const optionIsCorrect = Boolean((option as { is_correct?: unknown }).is_correct); const feedbackClass = questionChecked ? (optionIsCorrect ? "border-emerald-500/70 bg-emerald-500/10" : chosen ? "border-rose-500/70 bg-rose-500/10" : "border-[var(--border)] opacity-65") : chosen ? "border-[var(--primary)] bg-[var(--primary)]/10" : "border-[var(--border)] hover:border-[var(--primary)]/45"; return <button key={index} type="button" disabled={questionChecked} onClick={() => setAnswers((value) => ({ ...value, [quizIndex]: String(option.text ?? "") }))} className={`flex min-h-12 items-center gap-3 rounded-xl border px-4 py-3 text-left text-[14px] transition ${feedbackClass}`}><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full border border-current text-[11px] font-semibold">{String.fromCharCode(65 + index)}</span><span>{String(option.text ?? "")}</span></button>; })}</div> : <textarea value={answers[quizIndex] ?? ""} disabled={questionChecked} onChange={(e) => setAnswers((value) => ({ ...value, [quizIndex]: e.target.value }))} placeholder={zh ? "写下你的答案和思路" : "Write your answer and reasoning"} className="mt-5 min-h-28 w-full rounded-xl border border-[var(--border)] bg-[var(--background)] p-4 text-[14px] leading-relaxed" />}{questionChecked ? <div className={`mt-5 rounded-2xl border p-5 ${answerIsCorrect ? "border-emerald-500/35 bg-emerald-500/8" : "border-amber-500/35 bg-amber-500/8"}`}><p className="text-[13px] font-semibold">{answerIsCorrect ? (zh ? "答对了，继续保持这个思路。" : "Correct — keep this reasoning path.") : (zh ? "这里容易混淆，我们拆开看。" : "This is a common trap. Let's unpack it.")}</p><p className="mt-2 text-[13px] leading-6 text-[var(--muted-foreground)]"><b className="text-[var(--foreground)]">{zh ? "分步解析：" : "Step-by-step: "}</b>{String(currentQuestion.explanation)}</p></div> : null}<div className="mt-6 flex flex-wrap items-center justify-between gap-3"><button type="button" disabled={quizIndex === 0} onClick={() => setQuizIndex((value) => value - 1)} className="h-10 rounded-xl border border-[var(--border)] px-4 text-[13px] disabled:opacity-40">{zh ? "上一题" : "Previous"}</button>{!questionChecked ? <button type="button" disabled={!answers[quizIndex]?.trim()} onClick={() => setCheckedQuestions((value) => ({ ...value, [quizIndex]: true }))} className="h-10 rounded-xl bg-[var(--primary)] px-5 text-[13px] font-semibold text-[var(--primary-foreground)] disabled:opacity-40">{zh ? "检查答案" : "Check answer"}</button> : quizIndex < items.length - 1 ? <button type="button" onClick={() => setQuizIndex((value) => value + 1)} className="h-10 rounded-xl bg-[var(--primary)] px-5 text-[13px] font-semibold text-[var(--primary-foreground)]">{zh ? "下一题" : "Next problem"}</button> : <button type="button" onClick={() => void finishQuiz()} className="h-10 rounded-xl bg-[var(--primary)] px-5 text-[13px] font-semibold text-[var(--primary-foreground)]">{submitted ? (zh ? "学习记录已保存" : "Saved") : (zh ? "保存学习记录" : "Save learning record")}</button>}</div></div></section> : null}
      {result ? <div className="space-y-2"><GenerationSourceSummary result={result} /><WhyThisGeneration snapshot={result.personalization_context_snapshot} plan={result.teaching_strategy_plan} /></div> : null}
      {result ? <div className="flex flex-wrap items-center gap-3"><p className="inline-flex items-center gap-1.5 text-[12px] text-teal-700 dark:text-teal-300"><TraitTutorIcon name="matched" size={16} strokeWidth={2} />{zh ? "已生成并保存到学习空间" : "Generated and saved to Learning Space"}</p><button type="button" onClick={() => { setResult(null); setCardIndex(0); setQuizIndex(0); setCheckedQuestions({}); setSubmitted(false); }} className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px]"><RotateCcw size={14} />{zh ? "创建新的学习包" : "Create another"}</button></div> : null}
    </div>
  );
}

function CoursewareView({ result, zh }: { result: GenerateSuiteResult; zh: boolean }) {
  return <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><h2 className="text-[18px] font-semibold">{result.result.title}</h2><LearningImage images={result.result.images} /><div className="mt-4 space-y-4">{(result.result.sections ?? []).map((section, index) => { const item = section as unknown as Record<string, unknown>; const heading = item.title ?? item.section_title ?? `Section ${index + 1}`; const content = item.content ?? item.core_content ?? ""; return <article key={index} className="border-l-2 border-teal-500/45 pl-4"><h3 className="font-medium">{String(heading)}</h3><p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-[var(--muted-foreground)]">{Array.isArray(content) ? content.join("\n") : String(content)}</p></article>; })}</div><Link href="/home" className="mt-5 inline-flex text-[13px] text-teal-700 underline underline-offset-4 dark:text-teal-300">{zh ? "继续与学习助手对话" : "Continue with the learning assistant"}</Link></section>;
}

function LearningImage({ images }: { images?: Array<{ url?: unknown; alt?: unknown }> }) {
  const image = images?.[0];
  if (!image?.url || typeof image.url !== "string") return null;
  return <img src={image.url} alt={typeof image.alt === "string" ? image.alt : "Learning illustration"} className="my-4 max-h-80 w-full rounded-md border border-[var(--border)] object-cover" />;
}
