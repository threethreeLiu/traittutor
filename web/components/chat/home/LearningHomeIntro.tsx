"use client";

import { type ClipboardEvent, type DragEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUp, FileUp, Loader2, Sparkles } from "lucide-react";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";
import { ATTACHMENT_ACCEPT } from "@/lib/doc-attachments";
import { getLearnerOverview, type LearnerOverview } from "@/lib/learner-model-api";
import HomeAttachmentTray, { type HomePendingAttachment } from "./HomeAttachmentTray";

const STARTERS = {
  zh: [
    { index: "01", label: "7 天入门个人理财", prompt: "我想用 7 天入门个人理财，请从基础诊断开始。" },
    { index: "02", label: "用教材安排英语学习", prompt: "我想系统学习英语，请先判断我的起点并安排第一课。" },
    { index: "03", label: "解决牛顿第二定律", prompt: "我不理解牛顿第二定律，请从相关概念开始带我学会。" },
  ],
  en: [
    { index: "01", label: "Learn personal finance in 7 days", prompt: "I want to learn personal finance in 7 days. Start with a short diagnostic." },
    { index: "02", label: "Turn a textbook into an English plan", prompt: "I want to improve my English. Assess my starting point and begin the first lesson." },
    { index: "03", label: "Understand Newton's second law", prompt: "I do not understand Newton's second law. Teach me from the prerequisite concepts." },
  ],
};

export default function LearningHomeIntro({
  zh,
  onStart,
  onFiles,
  attachments,
  attachmentError,
  onRemoveAttachment,
  starting,
  pathStatus,
}: {
  zh: boolean;
  onStart: (prompt: string) => void;
  onFiles: (files: File[]) => void;
  attachments: HomePendingAttachment[];
  attachmentError: string | null;
  onRemoveAttachment: (index: number) => void;
  starting: boolean;
  pathStatus?: "creating" | "ready" | "error" | null;
}) {
  const starters = STARTERS[zh ? "zh" : "en"];
  const [overview, setOverview] = useState<LearnerOverview | null>(null);
  const [draft, setDraft] = useState("");

  const startDraft = () => {
    const prompt = draft.trim();
    if (!prompt && !attachments.length) return;
    setDraft("");
    onStart(prompt);
  };

  const addClipboardFiles = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === "file")
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null);
    if (!files.length) return;
    event.preventDefault();
    onFiles(files);
  };

  const addDroppedFiles = (event: DragEvent<HTMLFormElement>) => {
    event.preventDefault();
    const files = Array.from(event.dataTransfer.files);
    if (files.length) onFiles(files);
  };

  useEffect(() => {
    let active = true;
    getLearnerOverview()
      .then((value) => {
        if (active) setOverview(value);
      })
      .catch(() => {
        // Starting a path never depends on the memory service being available.
      });
    return () => {
      active = false;
    };
  }, []);

  const learnerState = useMemo(() => {
    const subjects = (overview?.subjects ?? []).filter((item) => (item.concept_signals ?? []).length > 0);
    const signals = subjects.flatMap((item) => item.concept_signals ?? []);
    return {
      subjects: subjects.length,
      observations: signals.reduce((total, signal) => total + (signal.verified_observation_count ?? 0), 0),
      reviewLoad: subjects.reduce((total, item) => total + (item.understanding?.review_load ?? 0), 0),
    };
  }, [overview]);
  const hasLearningEvidence = learnerState.subjects + learnerState.observations + learnerState.reviewLoad > 0;

  return (
    <section className="w-full max-w-[920px] animate-fade-in px-1">
      <div className="learning-home-card relative overflow-hidden rounded-[28px] border">
        <div className="learning-home-rule absolute inset-x-0 top-0 h-px" />
        <div className="relative px-6 py-6 sm:px-9 sm:py-7">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <TraitTutorMark className="h-8 w-8 shrink-0 select-none" />
              <div>
                <p className="learning-home-accent text-[9px] font-semibold uppercase tracking-[0.24em]">
                  TraitTutor · {zh ? "自适应学习路径" : "Adaptive learning path"}
                </p>
                <p className="mt-0.5 text-[10.5px] text-[var(--muted-foreground)]">
                  {zh ? "不只回答问题，还会根据作答改变下一步" : "More than an answer: every response changes what comes next"}
                </p>
              </div>
            </div>
            {hasLearningEvidence ? (
              <Link
                href="/profile/learning-model"
                className="learning-home-chip inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-[10px] transition"
              >
                <span className="learning-home-dot h-1.5 w-1.5 rounded-full" />
                {zh
                  ? `${learnerState.subjects} 个学科 · ${learnerState.observations} 条证据 · ${learnerState.reviewLoad} 项待复习`
                  : `${learnerState.subjects} subjects · ${learnerState.observations} observations · ${learnerState.reviewLoad} due`}
                <ArrowRight size={11} />
              </Link>
            ) : null}
          </div>

          <div className="mt-6 grid gap-5 lg:grid-cols-[1.35fr_0.65fr] lg:items-end">
            <div>
              <h1 className="max-w-xl font-serif text-[clamp(2rem,4vw,2.75rem)] font-medium leading-[1.05] tracking-[-0.035em] text-[var(--foreground)]">
                {zh ? "今天，想真正学会什么？" : "What do you want to truly learn today?"}
              </h1>
              <p className="mt-3 max-w-xl text-[12.5px] leading-5.5 text-[var(--muted-foreground)] sm:text-[13px]">
                {zh
                  ? "说一个目标、上传一份材料，或贴一道不会的题。系统会判断起点，安排讲解与练习，并用你的作答调整后续路径。"
                  : "Name a goal, upload a source, or paste a difficult problem. TraitTutor finds your starting point, arranges instruction and practice, then adapts from your answers."}
              </p>
              <div className="mt-4 flex gap-2 sm:hidden">
                <button
                  type="button"
                  disabled={starting}
                  onClick={() => onStart(starters[0].prompt)}
                  className="learning-home-secondary-action inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-[10.5px] font-semibold"
                >
                  <Sparkles size={12} />{zh ? "试一个学习目标" : "Try a learning goal"}
                </button>
              </div>
            </div>

            <div className="hidden gap-1.5 sm:grid sm:grid-cols-3 lg:grid-cols-1">
              {starters.map((starter) => (
                <button
                  key={starter.index}
                  type="button"
                  disabled={starting}
                  onClick={() => onStart(starter.prompt)}
                  className="learning-home-starter group flex min-w-0 items-center gap-3 rounded-xl border border-transparent px-3 py-2 text-left transition duration-200"
                >
                  <span className="learning-home-accent font-mono text-[9px] tracking-[0.16em] opacity-80">{starter.index}</span>
                  <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-[var(--foreground)]">{starter.label}</span>
                  <ArrowRight size={12} className="shrink-0 -translate-x-1 text-[var(--muted-foreground)] opacity-0 transition group-hover:translate-x-0 group-hover:opacity-100" />
                </button>
              ))}
            </div>
          </div>

          <div className="mt-6 border-t border-[var(--border)]/70 pt-4">
            <HomeAttachmentTray
              attachments={attachments}
              error={attachmentError}
              onRemove={onRemoveAttachment}
              zh={zh}
            />
            {pathStatus === "creating" || starting ? (
              <p role="status" className="learning-home-accent mb-2 inline-flex items-center gap-2 px-1 text-[10.5px]">
                <Loader2 size={12} className="animate-spin" />
                {zh ? "正在分析材料并建立学习组件路径…" : "Analyzing the source and building the component path…"}
              </p>
            ) : pathStatus === "error" ? (
              <p role="alert" className="mb-2 px-1 text-[10.5px] text-[var(--destructive)]">
                {zh ? "学习路径暂未建立，请重新提交。" : "The learning path could not be created. Please try again."}
              </p>
            ) : null}
            <form
              onSubmit={(event) => {
                event.preventDefault();
                startDraft();
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={addDroppedFiles}
              className="learning-home-form flex items-end gap-2 rounded-2xl border border-[var(--border)] bg-[var(--background)]/55 p-2.5 transition"
            >
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onPaste={addClipboardFiles}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    startDraft();
                  }
                }}
                rows={2}
                placeholder={zh ? "输入一个学习目标，或贴一道不会的题…" : "Enter a learning goal or paste a difficult problem…"}
                className="min-h-12 flex-1 resize-none bg-transparent px-2 py-1 text-[13px] leading-5 outline-none placeholder:text-[var(--muted-foreground)]"
              />
              <label className="learning-home-source inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-xl px-2.5 text-[10.5px] font-semibold text-[var(--muted-foreground)] transition">
                <FileUp size={14} />
                <span className="hidden sm:inline">{zh ? "材料" : "Source"}</span>
                <input
                  type="file"
                  multiple
                  disabled={starting}
                  accept={ATTACHMENT_ACCEPT}
                  className="sr-only"
                  onChange={(event) => {
                    const files = Array.from(event.target.files ?? []);
                    if (files.length) onFiles(files);
                    event.target.value = "";
                  }}
                />
              </label>
              <button
                type="submit"
                disabled={starting || (!draft.trim() && !attachments.length)}
                aria-label={zh ? "开始学习" : "Start learning"}
                className="learning-home-submit inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl transition disabled:cursor-not-allowed disabled:opacity-30"
              >
                {starting ? <Loader2 size={16} className="animate-spin" /> : <ArrowUp size={16} />}
              </button>
            </form>
            <div className="mt-3 hidden items-center justify-end gap-2 text-[9.5px] text-[var(--muted-foreground)] sm:flex" aria-label={zh ? "学习路径" : "Learning path"}>
              <Sparkles size={12} className="learning-home-accent" />
              <span>{zh ? "理解目标" : "Understand"}</span><span className="opacity-40">→</span>
              <span>{zh ? "安排组件" : "Arrange"}</span><span className="opacity-40">→</span>
              <span>{zh ? "根据证据调整" : "Adapt from evidence"}</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
