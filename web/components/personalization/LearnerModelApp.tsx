"use client";
/* eslint-disable i18n/no-literal-ui-text -- this Profile application is intentionally Chinese-first in this release. */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { PageBackLink } from "@/components/navigation/PageBackLink";
import {
  ArrowUpRight,
  BookOpenCheck,
  Brain,
  Database,
  Lightbulb,
  ListChecks,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import {
  getLearnerEvidence,
  getLearnerOverview,
  getLearnerReflections,
  reconcileLearnerMemory,
  saveLearnerPreference,
  setLearnerInference,
  updateLearnerReflectionStatus,
  type LearnerEvidence,
  type LearnerOverview,
  type LearnerProfile,
  type LearnerReflection,
} from "@/lib/learner-model-api";

const UNDERSTANDING_LABEL: Record<string, string> = {
  starting: "刚开始",
  learning: "学习中",
  familiar: "较熟悉",
  verified: "已验证掌握",
};

export default function LearnerModelApp() {
  const [overview, setOverview] = useState<LearnerOverview | null>(null);
  const [evidence, setEvidence] = useState<LearnerEvidence[]>([]);
  const [reflections, setReflections] = useState<LearnerReflection[]>([]);
  const [preference, setPreference] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const [overviewResult, evidenceResult, reflectionResult] = await Promise.allSettled([
      getLearnerOverview(),
      getLearnerEvidence(),
      getLearnerReflections(),
    ]);
    if (overviewResult.status === "fulfilled") {
      setOverview(overviewResult.value);
      setError("");
    } else {
      setError(overviewResult.reason instanceof Error ? overviewResult.reason.message : "学习模型暂时无法读取，请稍后刷新。");
    }
    if (evidenceResult.status === "fulfilled") setEvidence(evidenceResult.value.evidence);
    if (reflectionResult.status === "fulfilled") setReflections(reflectionResult.value.reflections);
    setLoading(false);
  }, []);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  async function action(work: () => Promise<void>) {
    setError("");
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作未完成，请重试。");
    }
  }

  async function addPreference() {
    if (!preference.trim()) return;
    await action(async () => {
      await saveLearnerPreference({ value: preference.trim(), category: "explanation" });
      setPreference("");
      await load();
    });
  }

  async function toggleInference() {
    if (!overview) return;
    await action(async () => {
      await setLearnerInference(!overview.inference_enabled);
      await load();
    });
  }

  async function syncMemory() {
    setSyncing(true);
    await action(async () => {
      await reconcileLearnerMemory();
      for (let attempt = 0; attempt < 12; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 500));
        const refreshed = await getLearnerOverview();
        setOverview(refreshed);
        if (["completed", "failed", "idle"].includes(refreshed.memory_reconcile?.state || "idle")) {
          if (refreshed.memory_reconcile?.state === "failed") throw new Error("旧记忆同步失败，请重试。");
          const latestEvidence = await getLearnerEvidence();
          const latestReflections = await getLearnerReflections();
          setEvidence(latestEvidence.evidence);
          setReflections(latestReflections.reflections);
          return;
        }
      }
      throw new Error("旧记忆仍在同步，请稍后刷新查看结果。");
    });
    setSyncing(false);
  }

  async function decideReflection(reflectionId: string, status: "confirmed" | "rejected") {
    await action(async () => {
      await updateLearnerReflectionStatus(reflectionId, status);
      await load();
    });
  }

  const model = useMemo(() => summarizeModel(overview, evidence), [overview, evidence]);

  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-6 pb-16 sm:px-6 sm:py-9 lg:px-10">
      <header className="border-b border-[var(--border)] pb-6 sm:pb-8">
        <PageBackLink href="/settings">返回设置</PageBackLink>
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-3 sm:gap-4">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]"><Brain className="h-5 w-5" /></span>
            <div className="min-w-0"><p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--muted-foreground)]">TraitTutor learner model</p><h1 className="mt-1 font-serif text-3xl font-semibold tracking-tight sm:text-4xl">我的学习模型</h1><p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">用可追溯的学习记忆和练习证据理解你的当前学习状态，并据此调整下一次讲解、练习与复习。</p></div>
          </div>
          <button type="button" onClick={() => void load()} className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3.5 text-sm transition-colors hover:border-[var(--primary)]/45 hover:text-[var(--primary)]"><RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />刷新</button>
        </div>
      </header>

      {error ? <div role="alert" className="mt-5 rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)]"><p className="font-medium">学习模型暂时不可用</p><p className="mt-1">{error}</p><p className="mt-2 text-xs opacity-90">你的聊天和生成仍可使用通用教学策略；刷新或重启本地服务后会自动恢复个性化信息。</p></div> : null}

      <section className="mt-7" aria-labelledby="model-overview-heading">
        <div className="flex flex-wrap items-end justify-between gap-2"><div><p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--muted-foreground)]">Model at a glance</p><h2 id="model-overview-heading" className="mt-1 text-lg font-semibold">学习状态总览</h2></div><p className="text-xs text-[var(--muted-foreground)]">状态来自你的材料、作答、复习与明确反馈</p></div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric icon={Database} label="可追溯学习记忆" value={loading ? "—" : String(model.memorySignals)} detail={loading ? "正在读取" : `${model.memorySources} 个原始来源`} />
          <Metric icon={BookOpenCheck} label="已覆盖概念" value={loading ? "—" : `${model.observedConcepts}/${model.concepts}`} detail={loading ? "正在读取" : "有练习或学习证据的概念"} />
          <Metric icon={Brain} label="已验证理解" value={loading ? "—" : `${Math.round(model.verifiedMastery * 100)}%`} detail={loading ? "正在读取" : `${model.verifiedObservations} 次可判分观测`} />
          <Metric icon={ListChecks} label="待复习" value={loading ? "—" : String(model.reviewLoad)} detail={loading ? "正在读取" : "优先安排巩固的概念"} />
        </div>
      </section>

      <div className="mt-7 grid gap-5 lg:grid-cols-12">
        <section className="lg:col-span-8" aria-labelledby="knowledge-heading"><Panel title="知识进度" eyebrow="BKT KNOWLEDGE TRACKING" icon={<Brain className="h-4 w-4" />}><div className="flex flex-col gap-3 border-b border-[var(--border)] pb-4 sm:flex-row sm:items-start sm:justify-between"><div><h2 id="knowledge-heading" className="text-lg font-semibold">学科学习地图</h2><p className="mt-1 max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">BKT 根据可判分作答、掌握路径和复习记录更新概念状态。这里呈现学习进度，不是能力分数。</p></div><Link href="#memory" className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-[var(--primary)] hover:underline">查看证据 <ArrowUpRight className="h-3.5 w-3.5" /></Link></div><div className="mt-4 space-y-3">{loading ? <LoadingRows /> : overview?.subjects.length ? overview.subjects.map((profile) => <SubjectProgress key={profile.subject?.subject_id} profile={profile} />) : <EmptyKnowledge />}</div></Panel></section>

        <section className="lg:col-span-4" id="memory" aria-labelledby="memory-heading"><Panel title="学习记忆" eyebrow="EVIDENCE MEMORY" icon={<Database className="h-4 w-4" />}><h2 id="memory-heading" className="text-lg font-semibold">记忆如何进入模型</h2><p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">只同步可回溯的明确偏好、目标、学科纠正和已验证误区；不会把整段聊天或 L3 摘要当作事实。</p><div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 p-3"><p className="text-xs font-medium">会话记忆与知识状态分开管理</p><p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">新会话会冻结一份精简的目标与明确偏好；Quiz、闪卡和练习的可验证结果则实时更新 BKT，并在下一次生成优先用于复习安排。</p></div><div className="mt-4 rounded-lg bg-[var(--muted)]/45 p-4"><p className="text-xs font-medium text-[var(--muted-foreground)]">当前同步状态</p><p className="mt-1 text-sm font-medium">{memoryStateLabel(overview?.memory_reconcile?.state, syncing)}</p><p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">{model.memorySignals ? `已纳入 ${model.memorySignals} 条信号，关联 ${model.memorySources} 个原始来源。` : "还没有可纳入的旧记忆证据。"}</p></div><button type="button" disabled={syncing} onClick={() => void syncMemory()} className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md border border-[var(--border)] text-sm font-medium transition-colors hover:border-[var(--primary)]/45 disabled:cursor-not-allowed disabled:opacity-55"><RefreshCw className={syncing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />{syncing ? "正在同步旧记忆" : "同步旧记忆"}</button><Link href="/memory" className="mt-3 inline-flex items-center gap-1 text-sm text-[var(--primary)] hover:underline">管理原始记忆 <ArrowUpRight className="h-3.5 w-3.5" /></Link></Panel></section>
      </div>

      <section className="mt-5" aria-labelledby="reflection-heading">
        <Panel title="学习反思" eyebrow="REFLECTION GOVERNANCE" icon={<ShieldCheck className="h-4 w-4" />}>
          <div className="flex flex-col gap-3 border-b border-[var(--border)] pb-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 id="reflection-heading" className="text-lg font-semibold">哪些记忆会影响下一次生成</h2>
              <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">候选记忆只展示给你看；只有“已确认”的偏好和可判分的薄弱概念会进入 Compass。拒绝的记忆会作为约束，避免系统继续按它生成。</p>
            </div>
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="rounded-full bg-[var(--primary)]/10 px-3 py-1 text-[var(--primary)]">已进入 Compass {reflections.filter((item) => item.applies_to_compass).length}</span>
              <span className="rounded-full bg-[var(--muted)] px-3 py-1 text-[var(--muted-foreground)]">候选 {reflections.filter((item) => item.status === "candidate").length}</span>
            </div>
          </div>
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {loading ? <LoadingRows /> : reflections.length ? reflections.slice(0, 6).map((reflection) => (
              <article key={reflection.reflection_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/20 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">{reflectionCategoryLabel(reflection.category)} · {reflection.scope === "subject" ? reflection.subject?.label || "学科" : "全局"}</p>
                    <h3 className="mt-2 line-clamp-2 font-medium">{reflection.value}</h3>
                  </div>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs ${reflectionStatusClass(reflection.status)}`}>{reflectionStatusLabel(reflection.status)}</span>
                </div>
                <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-[var(--muted-foreground)]">{reflection.reason}</p>
                <p className="mt-3 text-[11px] text-[var(--muted-foreground)]">证据 {reflection.evidence_refs.length} 条 · 置信度 {Math.round(reflection.confidence * 100)}%</p>
                {reflection.status === "candidate" && reflection.category !== "concept" ? (
                  <div className="mt-3 flex gap-2">
                    <button type="button" onClick={() => void decideReflection(reflection.reflection_id, "confirmed")} className="inline-flex h-8 items-center justify-center rounded-md bg-[var(--primary)] px-3 text-xs font-medium text-[var(--primary-foreground)]">确认使用</button>
                    <button type="button" onClick={() => void decideReflection(reflection.reflection_id, "rejected")} className="inline-flex h-8 items-center justify-center rounded-md border border-[var(--border)] px-3 text-xs font-medium hover:border-[var(--destructive)]/50 hover:text-[var(--destructive)]">拒绝</button>
                  </div>
                ) : null}
              </article>
            )) : <div className="rounded-lg border border-dashed border-[var(--border)] px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">还没有可治理的学习反思。完成一次材料分析、Quiz 或闪卡复习后会出现在这里。</div>}
          </div>
        </Panel>
      </section>

      <div className="mt-5 grid gap-5 lg:grid-cols-12">
        <section className="lg:col-span-7"><Panel title="教学偏好" eyebrow="YOUR CONTROL" icon={<Sparkles className="h-4 w-4" />}><div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div><h2 className="text-lg font-semibold">明确偏好优先</h2><p className="mt-1 max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">你的明确偏好会立即影响生成。行为推断只会在证据足够时补充，且可随时关闭。</p></div><button type="button" onClick={() => void toggleInference()} disabled={!overview} aria-pressed={Boolean(overview?.inference_enabled)} className={`inline-flex h-10 shrink-0 items-center justify-center rounded-full px-4 text-sm font-medium transition-colors disabled:opacity-50 ${overview?.inference_enabled ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "bg-[var(--muted)] text-[var(--foreground)]"}`}>{overview?.inference_enabled ? "行为推断已开启" : "行为推断已关闭"}</button></div><form onSubmit={(event) => { event.preventDefault(); void addPreference(); }} className="mt-5 flex flex-col gap-2 sm:flex-row"><label className="sr-only" htmlFor="learner-preference">告诉 TraitTutor 你的偏好</label><input id="learner-preference" value={preference} onChange={(event) => setPreference(event.target.value)} className="h-11 min-w-0 flex-1 rounded-md border border-[var(--border)] bg-transparent px-3 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/20" placeholder="例如：先给例子，再解释概念"/><button type="submit" disabled={!preference.trim()} className="inline-flex h-11 items-center justify-center gap-1 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"><Plus className="h-4 w-4" />保存偏好</button></form><div className="mt-4 flex flex-wrap gap-2">{overview?.global.preferences.length ? overview.global.preferences.map((item) => <span key={item.id} className="rounded-full border border-[var(--border)] px-3 py-1.5 text-xs"><span className="font-medium">{item.value}</span><span className="ml-1.5 text-[var(--muted-foreground)]">{item.state === "explicit" ? "明确" : "推断"}</span></span>) : <p className="text-sm text-[var(--muted-foreground)]">尚未记录明确偏好。写下你希望被如何讲解即可开始。</p>}</div></Panel></section>

        <aside className="lg:col-span-5"><div className="h-full rounded-xl border border-[var(--border)] bg-[var(--muted)]/30 p-5"><div className="flex gap-3"><ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-[var(--primary)]"/><div><h2 className="font-semibold">可查看、可纠正、可删除</h2><p className="mt-2 text-sm leading-relaxed text-[var(--muted-foreground)]">每个学科页都能查看概念证据、纠正学科归属或删除单条记录。删除后模型会从剩余证据重新计算。</p><Link href="/profile/learning-model" className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-[var(--primary)] hover:underline">进入证据治理 <ArrowUpRight className="h-3.5 w-3.5" /></Link></div></div></div></aside>
      </div>
    </main>
  );
}

function summarizeModel(overview: LearnerOverview | null, evidence: LearnerEvidence[]) {
  const subjects = overview?.subjects ?? [];
  const concepts = subjects.flatMap((profile) => profile.concept_signals);
  const observedConcepts = subjects.reduce((sum, profile) => sum + (profile.understanding?.observed_concept_count ?? 0), 0);
  const weightedMastery = subjects.reduce((sum, profile) => sum + (profile.understanding?.verified_mastery ?? 0) * (profile.understanding?.observed_concept_count ?? 0), 0);
  const memorySignals = evidence.filter((item) => item.kind.startsWith("memory_")).length;
  return { concepts: concepts.length, observedConcepts, verifiedMastery: observedConcepts ? weightedMastery / observedConcepts : 0, verifiedObservations: concepts.reduce((sum, item) => sum + (item.verified_observation_count ?? 0), 0), reviewLoad: subjects.reduce((sum, profile) => sum + (profile.understanding?.review_load ?? 0), 0), memorySignals, memorySources: new Set(evidence.filter((item) => item.kind.startsWith("memory_")).flatMap((item) => item.evidence_refs)).size };
}

function Panel({ title, eyebrow, icon, children }: { title: string; eyebrow: string; icon: React.ReactNode; children: React.ReactNode }) { return <section className="h-full rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5"><div className="mb-4 flex items-center gap-2 text-[11px] font-medium tracking-[0.16em] text-[var(--muted-foreground)]"><span className="text-[var(--primary)]">{icon}</span>{eyebrow}</div>{children}</section>; }
function Metric({ icon: Icon, label, value, detail }: { icon: typeof Brain; label: string; value: string; detail: string }) { return <article className="min-w-0 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"><div className="flex items-center gap-2 text-[var(--muted-foreground)]"><Icon className="h-4 w-4 text-[var(--primary)]"/><p className="text-xs font-medium">{label}</p></div><p className="mt-5 text-2xl font-semibold tabular-nums">{value}</p><p className="mt-1 min-h-5 text-xs leading-relaxed text-[var(--muted-foreground)]">{detail}</p></article>; }
function SubjectProgress({ profile }: { profile: LearnerProfile }) { const subject = profile.subject; const understanding = profile.understanding; if (!subject) return null; const mastery = understanding?.verified_mastery ?? 0; const verifiedObservations = profile.concept_signals.reduce((sum, concept) => sum + (concept.verified_observation_count ?? 0), 0); return <Link href={`/profile/learning-model/${encodeURIComponent(subject.subject_id)}`} className="block rounded-lg border border-[var(--border)] p-4 transition-colors hover:border-[var(--primary)]/50"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><div className="flex items-center gap-2"><h3 className="truncate font-medium">{subject.label}</h3><span className="shrink-0 rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px] text-[var(--muted-foreground)]">{UNDERSTANDING_LABEL[understanding?.status ?? "starting"]}</span></div><p className="mt-1 truncate text-xs text-[var(--muted-foreground)]">{subject.path.join(" · ") || "等待确认学科路径"}</p></div><div className="flex gap-4 text-xs text-[var(--muted-foreground)]"><span>{understanding?.observed_concept_count ?? 0}/{understanding?.concept_count ?? profile.concept_signals.length} 概念</span><span>{verifiedObservations} 次观测</span><span>待复习 {understanding?.review_load ?? 0}</span></div></div><div className="mt-4"><div className="flex justify-between text-xs"><span className="text-[var(--muted-foreground)]">已验证理解</span><span className="font-medium tabular-nums">{Math.round(mastery * 100)}%</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]"><div className="h-full rounded-full bg-[var(--primary)]" style={{ width: `${Math.round(mastery * 100)}%` }} /></div></div></Link>; }
function EmptyKnowledge() { return <div className="rounded-lg border border-dashed border-[var(--border)] px-4 py-8 text-center"><Lightbulb className="mx-auto h-5 w-5 text-[var(--primary)]"/><h3 className="mt-3 text-sm font-medium">学习模型会随活动形成</h3><p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-[var(--muted-foreground)]">上传材料并确认学科，或完成 Quiz、掌握路径和闪卡复习后，这里会开始显示有证据支撑的概念进度。</p></div>; }
function LoadingRows() { return <div className="space-y-3" aria-busy="true" aria-label="正在加载学习进度">{[0, 1, 2].map((index) => <div key={index} className="h-24 animate-pulse rounded-lg bg-[var(--muted)]/55" />)}</div>; }
function memoryStateLabel(state: string | undefined, syncing: boolean) { if (syncing || state === "queued" || state === "running") return "正在从旧记忆归纳证据"; if (state === "completed") return "旧记忆已同步"; if (state === "failed") return "上次同步未完成，可再次尝试"; return "尚未同步旧记忆"; }
function reflectionCategoryLabel(category: LearnerReflection["category"]) { return ({ goal: "目标", explanation: "讲解偏好", pacing: "节奏", feedback: "反馈", constraint: "约束", concept: "概念状态", strategy: "教学策略" } as const)[category] ?? category; }
function reflectionStatusLabel(status: LearnerReflection["status"]) { return ({ candidate: "候选", confirmed: "已确认", rejected: "已拒绝", stale: "已过期", needs_rebuild: "待重建" } as const)[status] ?? status; }
function reflectionStatusClass(status: LearnerReflection["status"]) { if (status === "confirmed") return "bg-[var(--primary)]/10 text-[var(--primary)]"; if (status === "candidate") return "bg-amber-500/10 text-amber-300"; if (status === "rejected") return "bg-[var(--destructive)]/10 text-[var(--destructive)]"; return "bg-[var(--muted)] text-[var(--muted-foreground)]"; }
