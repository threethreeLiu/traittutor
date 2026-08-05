"use client";

import Link from "next/link";
import { ArrowUpRight, CheckCircle2, CircleDot, Loader2, Route, ShieldCheck } from "lucide-react";
import type { LearningComponentPlan } from "@/lib/traittutor-api";

const TRAIT_LOOP_STAGE = "Trait Loop · 01";

export type LearningJourneyState = {
  goal: string;
  packId?: string | null;
  plan?: LearningComponentPlan | null;
  status: "creating" | "ready" | "error";
};

export default function LearningJourneyLaunch({ journey, zh }: { journey: LearningJourneyState; zh: boolean }) {
  const components = journey.plan?.components ?? [];
  const startUrl = journey.plan?.start_url ?? (journey.packId ? `/space/learning/${journey.packId}` : "/space/learning");
  return (
    <section className="overflow-hidden rounded-[24px] border border-teal-500/25 bg-[var(--card)] shadow-[0_22px_70px_-55px_rgba(13,148,136,0.95)]">
      <div className="grid lg:grid-cols-[0.68fr_1.32fr]">
        <div className="border-b border-[var(--border)] bg-teal-500/[0.055] p-5 lg:border-r lg:border-b-0">
          <div className="flex items-center justify-between">
            <span className="grid h-9 w-9 place-items-center rounded-full border border-teal-500/30 text-teal-600 dark:text-teal-300">
              {journey.status === "creating" ? <Loader2 size={17} className="animate-spin" /> : <CheckCircle2 size={17} />}
            </span>
            <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-[var(--muted-foreground)]">
              {journey.plan ? `PLAN V${journey.plan.version}` : (zh ? "正在编排" : "planning")}
            </span>
          </div>
          <p className="mt-5 text-[9px] font-semibold uppercase tracking-[0.2em] text-teal-700 dark:text-teal-300">
            {zh ? "已建立学习目标" : "Learning goal created"}
          </p>
          <h2 className="mt-2 font-serif text-[19px] font-semibold leading-7">{journey.goal}</h2>
          <p className="mt-5 border-t border-dashed border-teal-500/25 pt-4 text-[10px] leading-4 text-[var(--muted-foreground)]">
            {journey.status === "creating"
              ? (zh ? "正在读取当前学科证据并安排学习组件。" : "Reading subject evidence and arranging learning components.")
              : journey.status === "error"
                ? (zh ? "目标已保留，但学习路径暂未建立。进入我的学习可重试。" : "The goal is saved, but the path could not be created yet. Retry in My Learning.")
                : (zh ? "路径已保存。它会在可判分作答后调整尚未开始的步骤。" : "The path is saved. Unstarted steps adapt after graded answers.")}
          </p>
        </div>

        <div className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[9px] font-semibold uppercase tracking-[0.2em] text-[var(--muted-foreground)]">{TRAIT_LOOP_STAGE}</p>
              <p className="mt-1 text-[12px] font-medium">{zh ? "系统安排的第一轮学习组件" : "Your first system-arranged component path"}</p>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] px-2.5 py-1 text-[9.5px] text-[var(--muted-foreground)]">
              <CircleDot size={11} />{journey.plan?.subject_ref?.label ?? (zh ? "等待学科证据" : "Awaiting subject evidence")}
            </span>
          </div>

          <ol className="mt-4 grid gap-2 sm:grid-cols-2">
            {(components.length ? components.slice(0, 6) : fallbackComponents(zh)).map((component, index) => (
              <li key={"component_id" in component ? component.component_id : component.label} className="flex min-h-14 items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--background)]/65 px-3 py-2.5">
                <span className="font-mono text-[9px] text-teal-600 dark:text-teal-300">{String(index + 1).padStart(2, "0")}</span>
                <span className="min-w-0 text-[11px] font-semibold">{"component_id" in component ? (zh ? component.label_zh : component.label_en) : component.label}</span>
              </li>
            ))}
          </ol>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] pt-3">
            <p className="inline-flex items-center gap-1.5 text-[9.5px] leading-4 text-[var(--muted-foreground)]">
              <ShieldCheck size={12} />{zh ? "阅读不算掌握；只有作答与复习形成知识证据。" : "Reading is not mastery; answers and reviews create evidence."}
            </p>
            <Link href={startUrl} className="inline-flex items-center gap-2 rounded-xl bg-teal-600 px-4 py-2.5 text-[11px] font-semibold text-white transition hover:bg-teal-500">
              <Route size={14} />{zh ? "开始第一步" : "Start the first step"}<ArrowUpRight size={13} />
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}

function fallbackComponents(zh: boolean): Array<{ label: string }> {
  return (zh
    ? ["目标地图", "起点诊断", "核心概念讲解", "引导练习", "主动回忆"]
    : ["Goal map", "Starting diagnostic", "Concept explanation", "Guided practice", "Active recall"]
  ).map((label) => ({ label }));
}
