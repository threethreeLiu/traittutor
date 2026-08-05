"use client";

import { ArrowRight, ArrowUp, BarChart3, FileSearch, FileUp, PenLine, Sigma } from "lucide-react";
import { type ClipboardEvent, type DragEvent, useState } from "react";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";
import { ATTACHMENT_ACCEPT } from "@/lib/doc-attachments";
import HomeAttachmentTray, { type HomePendingAttachment } from "./HomeAttachmentTray";

const TASKS = {
  zh: [
    { icon: FileSearch, label: "调研一个主题", prompt: "请围绕这个主题进行深入研究，整理关键结论、证据和来源。" },
    { icon: BarChart3, label: "分析数据并出图", prompt: "请分析我提供的数据，识别关键趋势，并选择合适的图表呈现。" },
    { icon: Sigma, label: "分步解决问题", prompt: "请分步分析并解决这个问题，说明每一步依据。" },
    { icon: PenLine, label: "整理或改写内容", prompt: "请帮我整理并改写这段内容，保留原意，让表达更清楚自然。" },
  ],
  en: [
    { icon: FileSearch, label: "Research a topic", prompt: "Research this topic in depth and organize the key findings, evidence, and sources." },
    { icon: BarChart3, label: "Analyze data and chart it", prompt: "Analyze the data I provide, identify the key trends, and choose suitable charts." },
    { icon: Sigma, label: "Solve a problem step by step", prompt: "Analyze and solve this problem step by step, explaining the basis for each step." },
    { icon: PenLine, label: "Organize or rewrite content", prompt: "Organize and rewrite this content while preserving its meaning and making it clearer." },
  ],
};

export default function AssistHomeIntro({
  zh,
  onStart,
  onFiles,
  attachments,
  attachmentError,
  onRemoveAttachment,
}: {
  zh: boolean;
  onStart: (prompt: string) => void;
  onFiles: (files: File[]) => void;
  attachments: HomePendingAttachment[];
  attachmentError: string | null;
  onRemoveAttachment: (index: number) => void;
}) {
  const tasks = TASKS[zh ? "zh" : "en"];
  const [draft, setDraft] = useState("");

  const startDraft = () => {
    const prompt = draft.trim() || (attachments.length
      ? (zh ? "请读取已上传文件并根据内容协助我完成任务。" : "Read the uploaded files and help me complete the task using their content.")
      : "");
    if (!prompt) return;
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
  return (
    <section className="w-full max-w-[880px] animate-fade-in px-1">
      <div className="relative overflow-hidden rounded-[24px] border border-white/[0.08] bg-[var(--card)] px-5 py-5 shadow-[0_28px_90px_-76px_rgba(139,92,246,0.9)] sm:rounded-[28px] sm:px-9 sm:py-9">
        <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-violet-400/55 to-transparent" />
        <div className="flex items-center gap-3">
          <TraitTutorMark className="h-8 w-8 shrink-0 select-none" />
          <div>
            <p className="text-[9px] font-semibold uppercase tracking-[0.24em] text-violet-500 dark:text-violet-300">
              TraitTutor {"·"} {zh ? "任务助手" : "Task assistant"}
            </p>
            <p className="mt-0.5 text-[10.5px] text-[var(--muted-foreground)]">
              {zh ? "研究、分析、解题与表达，集中在一段对话中完成" : "Research, analysis, problem solving, and writing in one conversation"}
            </p>
          </div>
        </div>

        <div className="mt-5 grid gap-5 sm:mt-7 sm:gap-7 lg:grid-cols-[1.05fr_0.95fr] lg:items-end">
          <div>
            <h1 className="font-serif text-[clamp(1.8rem,4vw,2.8rem)] font-medium leading-[1.05] tracking-[-0.035em]">
              {zh ? "今天，需要我帮你完成什么？" : "What can I help you complete today?"}
            </h1>
            <p className="mt-3 max-w-xl text-[12.5px] leading-5.5 text-[var(--muted-foreground)]">
              {zh
                ? "直接描述任务，或上传文件作为上下文。需要建立长期学习路径时，请进入学习页。"
                : "Describe the task or upload files as context. Switch to Learn when you want a long-term adaptive learning path."}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {tasks.map(({ icon: Icon, label, prompt }) => (
              <button
                key={label}
                type="button"
                onClick={() => onStart(prompt)}
                className="group flex min-w-0 items-center gap-2 rounded-xl border border-[var(--border)]/65 bg-[var(--background)]/35 px-2.5 py-2.5 text-left text-[10.5px] font-medium transition hover:border-violet-400/35 hover:bg-violet-500/[0.06] sm:gap-2.5 sm:px-3 sm:text-[11px]"
              >
                <Icon size={13} className="shrink-0 text-violet-500 dark:text-violet-300" />
                <span className="min-w-0 flex-1 truncate">{label}</span>
                <ArrowRight size={11} className="shrink-0 opacity-0 transition group-hover:opacity-70" />
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 border-t border-[var(--border)]/70 pt-4 sm:mt-6">
          <HomeAttachmentTray
            attachments={attachments}
            error={attachmentError}
            onRemove={onRemoveAttachment}
            zh={zh}
            accent="violet"
          />
          <form
            onSubmit={(event) => {
              event.preventDefault();
              startDraft();
            }}
            onDragOver={(event) => event.preventDefault()}
            onDrop={addDroppedFiles}
            className="flex items-end gap-2 rounded-2xl border border-[var(--border)] bg-[var(--background)]/45 p-2.5 transition focus-within:border-violet-400/45"
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
              placeholder={zh ? "描述你要完成的任务…" : "Describe the task you want to complete…"}
              className="min-h-12 flex-1 resize-none bg-transparent px-2 py-1 text-[13px] leading-5 outline-none placeholder:text-[var(--muted-foreground)]"
            />
            <label className="inline-flex h-9 shrink-0 cursor-pointer items-center gap-1.5 rounded-xl px-2.5 text-[10.5px] font-semibold text-[var(--muted-foreground)] transition hover:bg-violet-500/10 hover:text-violet-500 dark:hover:text-violet-300">
              <FileUp size={14} />
              <span className="hidden sm:inline">{zh ? "文件" : "Files"}</span>
              <input
                type="file"
                multiple
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
              disabled={!draft.trim() && !attachments.length}
              aria-label={zh ? "开始任务" : "Start task"}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-violet-500 text-white transition hover:bg-violet-400 disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ArrowUp size={16} />
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}
