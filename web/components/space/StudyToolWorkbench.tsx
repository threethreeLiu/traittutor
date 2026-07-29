"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, FileText, Layers3, Loader2, RotateCcw, Settings2, Sparkles } from "lucide-react";

import {
  createLearningPack,
  createTraitTutorGenerationTask,
  createTraitProfile,
  getTraitTutorGenerationTask,
  listTraitProfiles,
  subscribeTraitTutorGeneration,
  updateLearningPack,
  type GenerateKind,
  type GenerateSuiteResult,
  type TraitProfile,
} from "@/lib/traittutor-api";

type ToolKind = GenerateKind;

const CONFIG: Record<ToolKind, { title: string; description: string; icon: typeof FileText }> = {
  courseware: { title: "课件", description: "将材料转为可逐节学习的课件。", icon: FileText },
  flashcards: { title: "Flashcard 学习", description: "从材料创建主动回忆卡组。", icon: Layers3 },
  quiz: { title: "Quiz 测验", description: "生成、作答并复盘练习题。", icon: Check },
};

export default function StudyToolWorkbench({ kind }: { kind: ToolKind }) {
  const { i18n } = useTranslation();
  const zh = i18n.language?.startsWith("zh");
  const config = CONFIG[kind];
  const Icon = config.icon;
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [profile, setProfile] = useState<TraitProfile | null>(null);
  const [quizMode, setQuizMode] = useState("material");
  const [questionCount, setQuestionCount] = useState("8");
  const [difficulty, setDifficulty] = useState("mixed");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<GenerateSuiteResult | null>(null);
  const [packId, setPackId] = useState("");
  const [cardIndex, setCardIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [quizIndex, setQuizIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [submitted, setSubmitted] = useState(false);

  useEffect(() => {
    listTraitProfiles().then((profiles) => setProfile(profiles[0] ?? null)).catch(() => undefined);
  }, []);

  const generate = useCallback(async () => {
    if (!text.trim()) return;
    setBusy(true); setError(""); setResult(null); setStage(zh ? "正在创建学习包" : "Creating learning pack");
    try {
      const material = { source_type: "paste" as const, title: title.trim() || config.title, text };
      const pack = await createLearningPack({ title: material.title, material, profile_id: profile?.profile_id });
      setPackId(pack.pack_id);
      const task = await createTraitTutorGenerationTask({
        generation_type: kind,
        material,
        learner_profile: profile ?? undefined,
        options: kind === "quiz" ? { mode: quizMode, question_count: Number(questionCount), difficulty } : { language: zh ? "zh-CN" : "en" },
      });
      const unsubscribe = subscribeTraitTutorGeneration(task, (event) => {
        setStage(event.message);
        if (event.type === "failed") setError(zh ? "生成失败，请检查模型配置后重试。" : "Generation failed. Check model settings and retry.");
      }, () => setError(zh ? "生成连接中断。" : "Generation connection interrupted."));
      const poll = window.setInterval(async () => {
        const loaded = await getTraitTutorGenerationTask(task.generation_id);
        if ("result" in loaded) {
          window.clearInterval(poll); unsubscribe(); setResult(loaded); setBusy(false); setStage(zh ? "生成完成" : "Completed");
          await updateLearningPack(pack.pack_id, { artifact: loaded.result });
        } else if (loaded.status === "failed") {
          window.clearInterval(poll); unsubscribe(); setBusy(false); setError(loaded.error || "Generation failed");
        }
      }, 800);
    } catch (cause) {
      setBusy(false); setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [config.title, difficulty, kind, profile, questionCount, quizMode, text, title, zh]);

  const items = useMemo(() => result?.result.items ?? [], [result]);
  const currentCard = items[cardIndex];
  const currentQuestion = items[quizIndex];

  const markCard = async (state: string) => {
    if (packId && currentCard) await updateLearningPack(packId, { flashcard_progress: { [String(currentCard.node_id || cardIndex)]: state } });
    setRevealed(false); setCardIndex((value) => Math.min(value + 1, Math.max(0, items.length - 1)));
  };

  const finishQuiz = async () => {
    setSubmitted(true);
    if (packId) await updateLearningPack(packId, { quiz_attempt: { submitted_at: new Date().toISOString(), answers, total: items.length } });
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 pb-10">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--border)] pb-4">
        <div className="flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-lg bg-teal-500/10 text-teal-600 dark:text-teal-400"><Icon size={20} /></span><div><h1 className="text-[18px] font-semibold">{zh ? config.title : kind}</h1><p className="text-[12.5px] text-[var(--muted-foreground)]">{zh ? config.description : config.description}</p></div></div>
        <Link href="/settings/llm" className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[12.5px] hover:bg-[var(--accent)]"><Settings2 size={15} />{zh ? "配置模型" : "Model settings"}</Link>
      </header>

      {!result ? <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4 space-y-4">
        {kind === "quiz" ? <div className="grid gap-3 sm:grid-cols-3"><label className="text-[12px] text-[var(--muted-foreground)]">{zh ? "生成模式" : "Mode"}<select value={quizMode} onChange={(e) => setQuizMode(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[13px]"><option value="material">{zh ? "基于材料" : "Material"}</option><option value="variation">{zh ? "题目变式" : "Question variation"}</option><option value="objective">{zh ? "学习目标" : "Objective"}</option></select></label><label className="text-[12px] text-[var(--muted-foreground)]">{zh ? "题量" : "Questions"}<select value={questionCount} onChange={(e) => setQuestionCount(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[13px]"><option value="5">5</option><option value="8">8</option><option value="12">12</option></select></label><label className="text-[12px] text-[var(--muted-foreground)]">{zh ? "难度" : "Difficulty"}<select value={difficulty} onChange={(e) => setDifficulty(e.target.value)} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[13px]"><option value="easy">{zh ? "基础" : "Easy"}</option><option value="mixed">{zh ? "混合" : "Mixed"}</option><option value="hard">{zh ? "挑战" : "Hard"}</option></select></label></div> : null}
        <label className="block text-[12px] text-[var(--muted-foreground)]">{zh ? "学习包名称" : "Learning pack name"}<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={zh ? "例如：细胞生物学第一章" : "e.g. Cell biology chapter 1"} className="mt-1 h-9 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-[13px]" /></label>
        <label className="block text-[12px] text-[var(--muted-foreground)]">{quizMode === "objective" ? (zh ? "学习目标" : "Learning objective") : (zh ? "材料或已有题目" : "Material or existing question")}<textarea value={text} onChange={(e) => setText(e.target.value)} placeholder={zh ? "粘贴材料、题目或学习目标。上传、知识库和笔记本材料将在下一步接入同一学习包。" : "Paste material, questions, or a learning objective."} className="mt-1 min-h-52 w-full rounded-md border border-[var(--border)] bg-[var(--background)] p-3 text-[13px] leading-relaxed" /></label>
        {profile ? <p className="text-[12px] text-teal-700 dark:text-teal-300">{zh ? "将使用当前学习画像和自动匹配的学习角色。" : "Your current learning profile and matched persona will be used."}</p> : <p className="text-[12px] text-[var(--muted-foreground)]">{zh ? "未找到学习画像，将使用中性教学策略。" : "No profile found; neutral teaching strategy will be used."}</p>}
        {error ? <p className="text-[12px] text-red-600">{error}</p> : null}
        <button type="button" disabled={!text.trim() || busy} onClick={() => void generate()} className="inline-flex h-9 items-center gap-2 rounded-md bg-[var(--primary)] px-3 text-[13px] font-medium text-[var(--primary-foreground)] disabled:opacity-50">{busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}{busy ? stage : (zh ? "开始生成" : "Generate")}</button>
      </section> : null}

      {result && kind === "courseware" ? <CoursewareView result={result} zh={Boolean(zh)} /> : null}
      {result && kind === "flashcards" && currentCard ? <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><p className="mb-4 text-[12px] text-[var(--muted-foreground)]">{cardIndex + 1} / {items.length}</p><button type="button" onClick={() => setRevealed((value) => !value)} className="min-h-48 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-6 text-left"><p className="text-[12px] text-[var(--muted-foreground)]">{revealed ? (zh ? "答案" : "Answer") : (zh ? "问题" : "Prompt")}</p><p className="mt-3 text-[17px]">{revealed ? String(currentCard.back) : String(currentCard.front)}</p></button><div className="mt-4 flex justify-between gap-3"><button onClick={() => void markCard("review")} className="h-9 rounded-md border border-[var(--border)] px-3 text-[13px]">{zh ? "需要复习" : "Review"}</button><button onClick={() => void markCard("mastered")} className="h-9 rounded-md bg-[var(--primary)] px-3 text-[13px] text-[var(--primary-foreground)]">{zh ? "已掌握" : "Known"}</button></div></section> : null}
      {result && kind === "quiz" && currentQuestion ? <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><p className="mb-4 text-[12px] text-[var(--muted-foreground)]">{zh ? "第" : "Question "}{quizIndex + 1}{zh ? ` / ${items.length} 题` : ` of ${items.length}`}</p><p className="text-[16px] leading-relaxed">{String(currentQuestion.question)}</p><textarea value={answers[quizIndex] ?? ""} disabled={submitted} onChange={(e) => setAnswers((value) => ({ ...value, [quizIndex]: e.target.value }))} placeholder={zh ? "写下你的答案" : "Write your answer"} className="mt-4 min-h-24 w-full rounded-md border border-[var(--border)] bg-[var(--background)] p-3 text-[13px]" />{submitted ? <p className="mt-3 text-[13px] leading-relaxed text-[var(--muted-foreground)]"><b>{zh ? "解析：" : "Explanation: "}</b>{String(currentQuestion.explanation)}</p> : null}<div className="mt-4 flex justify-between"><button disabled={quizIndex === 0} onClick={() => setQuizIndex((value) => value - 1)} className="h-9 rounded-md border border-[var(--border)] px-3 text-[13px] disabled:opacity-40">{zh ? "上一题" : "Previous"}</button>{quizIndex < items.length - 1 ? <button onClick={() => setQuizIndex((value) => value + 1)} className="h-9 rounded-md bg-[var(--primary)] px-3 text-[13px] text-[var(--primary-foreground)]">{zh ? "下一题" : "Next"}</button> : <button onClick={() => void finishQuiz()} className="h-9 rounded-md bg-[var(--primary)] px-3 text-[13px] text-[var(--primary-foreground)]">{submitted ? (zh ? "已提交" : "Submitted") : (zh ? "提交并复盘" : "Submit")}</button>}</div></section> : null}
      {result ? <button type="button" onClick={() => { setResult(null); setCardIndex(0); setQuizIndex(0); setSubmitted(false); }} className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px]"><RotateCcw size={14} />{zh ? "创建新的学习包" : "Create another"}</button> : null}
    </div>
  );
}

function CoursewareView({ result, zh }: { result: GenerateSuiteResult; zh: boolean }) {
  return <section className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-5"><h2 className="text-[18px] font-semibold">{result.result.title}</h2><div className="mt-4 space-y-4">{(result.result.sections ?? []).map((section, index) => { const item = section as unknown as Record<string, unknown>; const heading = item.title ?? item.section_title ?? `Section ${index + 1}`; const content = item.content ?? item.core_content ?? ""; return <article key={index} className="border-l-2 border-teal-500/45 pl-4"><h3 className="font-medium">{String(heading)}</h3><p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-[var(--muted-foreground)]">{Array.isArray(content) ? content.join("\n") : String(content)}</p></article>; })}</div><Link href="/home" className="mt-5 inline-flex text-[13px] text-teal-700 underline underline-offset-4 dark:text-teal-300">{zh ? "继续与学习助手对话" : "Continue with the learning assistant"}</Link></section>;
}
