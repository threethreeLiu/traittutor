"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useTranslation } from "react-i18next";
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

type Copy = { zh: string; en: string };
type Tr = (copy: Copy) => string;

const UNDERSTANDING_LABEL: Record<string, Copy> = {
  starting: { zh: "刚开始", en: "Getting started" },
  learning: { zh: "学习中", en: "Learning" },
  familiar: { zh: "较熟悉", en: "Familiar" },
  verified: { zh: "已验证掌握", en: "Verified mastery" },
};

export default function LearnerModelApp() {
  const { i18n } = useTranslation();
  const zh = i18n.language.toLowerCase().startsWith("zh");
  const tr = useCallback((copy: Copy) => zh ? copy.zh : copy.en, [zh]);
  const [overview, setOverview] = useState<LearnerOverview | null>(null);
  const [evidence, setEvidence] = useState<LearnerEvidence[]>([]);
  const [reflections, setReflections] = useState<LearnerReflection[]>([]);
  const [preference, setPreference] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [reflectionError, setReflectionError] = useState("");

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
      setError(overviewResult.reason instanceof Error ? overviewResult.reason.message : tr({ zh: "学习模型暂时无法读取，请稍后刷新。", en: "The learner model is temporarily unavailable. Please refresh shortly." }));
    }
    if (evidenceResult.status === "fulfilled") setEvidence(evidenceResult.value.evidence);
    if (reflectionResult.status === "fulfilled") {
      setReflections(reflectionResult.value.reflections);
      setReflectionError("");
    } else {
      setReflectionError(reflectionResult.reason instanceof Error ? reflectionResult.reason.message : tr({ zh: "学习反思暂时无法读取。", en: "Learning reflections are temporarily unavailable." }));
    }
    setLoading(false);
  }, [tr]);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  async function action(work: () => Promise<void>) {
    setError("");
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr({ zh: "操作未完成，请重试。", en: "The action could not be completed. Please try again." }));
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
          if (refreshed.memory_reconcile?.state === "failed") throw new Error(tr({ zh: "旧记忆同步失败，请重试。", en: "Memory synchronization failed. Please try again." }));
          const latestEvidence = await getLearnerEvidence();
          const latestReflections = await getLearnerReflections();
          setEvidence(latestEvidence.evidence);
          setReflections(latestReflections.reflections);
          setReflectionError("");
          return;
        }
      }
      throw new Error(tr({ zh: "旧记忆仍在同步，请稍后刷新查看结果。", en: "Memory is still synchronizing. Refresh shortly to see the result." }));
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
        <PageBackLink href="/settings">{tr({ zh: "返回设置", en: "Back to settings" })}</PageBackLink>
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 items-start gap-3 sm:gap-4">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]"><Brain className="h-5 w-5" /></span>
            <div className="min-w-0"><p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--muted-foreground)]">TraitTutor learner model</p><h1 className="mt-1 font-serif text-3xl font-semibold tracking-tight sm:text-4xl">{tr({ zh: "我的学习模型", en: "My learner model" })}</h1><p className="mt-2 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "用可追溯的学习记忆和练习证据理解你的当前学习状态，并据此调整下一次讲解、练习与复习。", en: "Understand your current learning state through traceable memory and practice evidence, then adapt the next explanation, exercise, and review." })}</p></div>
          </div>
          <button type="button" onClick={() => void load()} className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-md border border-[var(--border)] px-3.5 text-sm transition-colors hover:border-[var(--primary)]/45 hover:text-[var(--primary)]"><RefreshCw className={loading ? "h-4 w-4 animate-spin" : "h-4 w-4"} />{tr({ zh: "刷新", en: "Refresh" })}</button>
        </div>
      </header>

      {error ? <div role="alert" className="mt-5 rounded-lg border border-[var(--destructive)]/35 bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)]"><p className="font-medium">{tr({ zh: "学习模型暂时不可用", en: "Learner model temporarily unavailable" })}</p><p className="mt-1">{error}</p><p className="mt-2 text-xs opacity-90">{tr({ zh: "你的聊天和生成仍可使用通用教学策略；刷新或重启本地服务后会自动恢复个性化信息。", en: "Chat and generation can still use the standard teaching strategy. Personalization will return after a refresh or service restart." })}</p></div> : null}

      <section className="mt-7" aria-labelledby="model-overview-heading">
        <div className="flex flex-wrap items-end justify-between gap-2"><div><p className="text-[11px] font-medium uppercase tracking-[0.18em] text-[var(--muted-foreground)]">Model at a glance</p><h2 id="model-overview-heading" className="mt-1 text-lg font-semibold">{tr({ zh: "学习状态总览", en: "Learning state overview" })}</h2></div><p className="text-xs text-[var(--muted-foreground)]">{tr({ zh: "状态来自你的材料、作答、复习与明确反馈", en: "State is derived from your materials, answers, reviews, and explicit feedback" })}</p></div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric icon={Database} label={tr({ zh: "可追溯学习记忆", en: "Traceable learning memory" })} value={loading ? "—" : String(model.memorySignals)} detail={loading ? tr({ zh: "正在读取", en: "Loading" }) : tr({ zh: `${model.memorySources} 个原始来源`, en: `${model.memorySources} original sources` })} />
          <Metric icon={BookOpenCheck} label={tr({ zh: "已覆盖概念", en: "Concept coverage" })} value={loading ? "—" : `${model.observedConcepts}/${model.concepts}`} detail={loading ? tr({ zh: "正在读取", en: "Loading" }) : tr({ zh: "有练习或学习证据的概念", en: "Concepts with practice or learning evidence" })} />
          <Metric icon={Brain} label={tr({ zh: "已验证理解", en: "Verified understanding" })} value={loading ? "—" : `${Math.round(model.verifiedMastery * 100)}%`} detail={loading ? tr({ zh: "正在读取", en: "Loading" }) : tr({ zh: `${model.verifiedObservations} 次可判分观测`, en: `${model.verifiedObservations} graded observations` })} />
          <Metric icon={ListChecks} label={tr({ zh: "待复习", en: "Due for review" })} value={loading ? "—" : String(model.reviewLoad)} detail={loading ? tr({ zh: "正在读取", en: "Loading" }) : tr({ zh: "优先安排巩固的概念", en: "Concepts prioritized for reinforcement" })} />
        </div>
      </section>

      <div className="mt-7 grid gap-5 lg:grid-cols-12">
        <section className="lg:col-span-8" aria-labelledby="knowledge-heading"><Panel title={tr({ zh: "知识进度", en: "Knowledge progress" })} eyebrow="BKT KNOWLEDGE TRACKING" icon={<Brain className="h-4 w-4" />}><div className="flex flex-col gap-3 border-b border-[var(--border)] pb-4 sm:flex-row sm:items-start sm:justify-between"><div><h2 id="knowledge-heading" className="text-lg font-semibold">{tr({ zh: "学科学习地图", en: "Subject learning map" })}</h2><p className="mt-1 max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "BKT 根据可判分作答、掌握路径和复习记录更新概念状态。这里呈现学习进度，不是能力分数。", en: "BKT updates concept states from graded answers, learning paths, and review records. This shows learning progress, not an ability score." })}</p></div><Link href="#memory" className="inline-flex shrink-0 items-center gap-1 text-sm font-medium text-[var(--primary)] hover:underline">{tr({ zh: "查看证据", en: "View evidence" })} <ArrowUpRight className="h-3.5 w-3.5" /></Link></div><div className="mt-4 space-y-3">{loading ? <LoadingRows tr={tr} /> : overview?.subjects.length ? overview.subjects.map((profile) => <SubjectProgress key={profile.subject?.subject_id} profile={profile} tr={tr} />) : <EmptyKnowledge tr={tr} />}</div></Panel></section>

        <section className="lg:col-span-4" id="memory" aria-labelledby="memory-heading"><Panel title={tr({ zh: "学习记忆", en: "Learning memory" })} eyebrow="EVIDENCE MEMORY" icon={<Database className="h-4 w-4" />}><h2 id="memory-heading" className="text-lg font-semibold">{tr({ zh: "记忆如何进入模型", en: "How memory enters the model" })}</h2><p className="mt-1 text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "只同步可回溯的明确偏好、目标、学科纠正和已验证误区；不会把整段聊天或旧跨会话摘要当作事实。", en: "Only traceable explicit preferences, goals, subject corrections, and verified misconceptions are synchronized. Entire chats and old cross-session summaries are not treated as facts." })}</p><div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 p-3"><p className="text-xs font-medium">{tr({ zh: "会话记忆与知识状态分开管理", en: "Conversation memory and knowledge state are managed separately" })}</p><p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "新会话会冻结一份精简的目标与明确偏好；Quiz、闪卡和练习的可验证结果则实时更新 BKT，并在下一次生成优先用于复习安排。", en: "A new session freezes a compact goal and explicit-preference snapshot. Verified quiz, flashcard, and practice results update BKT in real time and guide future review." })}</p></div><div className="mt-4 rounded-lg bg-[var(--muted)]/45 p-4"><p className="text-xs font-medium text-[var(--muted-foreground)]">{tr({ zh: "当前同步状态", en: "Current synchronization status" })}</p><p className="mt-1 text-sm font-medium">{memoryStateLabel(overview?.memory_reconcile?.state, syncing, tr)}</p><p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">{model.memorySignals ? tr({ zh: `已纳入 ${model.memorySignals} 条信号，关联 ${model.memorySources} 个原始来源。`, en: `${model.memorySignals} signals from ${model.memorySources} original sources are included.` }) : tr({ zh: "还没有可纳入的旧记忆证据。", en: "No eligible historical memory evidence yet." })}</p></div><button type="button" disabled={syncing} onClick={() => void syncMemory()} className="mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-md border border-[var(--border)] text-sm font-medium transition-colors hover:border-[var(--primary)]/45 disabled:cursor-not-allowed disabled:opacity-55"><RefreshCw className={syncing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />{syncing ? tr({ zh: "正在同步旧记忆", en: "Synchronizing memory" }) : tr({ zh: "同步旧记忆", en: "Synchronize memory" })}</button></Panel></section>
      </div>

      <section className="mt-5" aria-labelledby="reflection-heading">
        <Panel title={tr({ zh: "学习反思", en: "Learning reflections" })} eyebrow="REFLECTION GOVERNANCE" icon={<ShieldCheck className="h-4 w-4" />}>
          <div className="flex flex-col gap-3 border-b border-[var(--border)] pb-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 id="reflection-heading" className="text-lg font-semibold">{tr({ zh: "哪些记忆会影响下一次生成", en: "Which memories influence the next generation" })}</h2>
              <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "候选记忆只展示给你看；只有“已确认”的偏好和可判分的薄弱概念会进入 Compass。拒绝的记忆会作为约束，避免系统继续按它生成。", en: "Candidate memories are visible only for review. Only confirmed preferences and graded weak concepts enter Compass; rejected memories become constraints." })}</p>
            </div>
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="rounded-full bg-[var(--primary)]/10 px-3 py-1 text-[var(--primary)]">{tr({ zh: "已进入 Compass", en: "In Compass" })} {reflections.filter((item) => item.applies_to_compass).length}</span>
              <span className="rounded-full bg-[var(--muted)] px-3 py-1 text-[var(--muted-foreground)]">{tr({ zh: "候选", en: "Candidates" })} {reflections.filter((item) => item.status === "candidate").length}</span>
            </div>
          </div>
          {reflectionError ? <div role="status" className="mt-4 rounded-lg border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{tr({ zh: `学习反思暂不可用：${reflectionError}。其他学习模型信息仍可正常查看。`, en: `Learning reflections are temporarily unavailable: ${reflectionError}. Other learner-model information remains available.` })}</div> : null}
          <div className="mt-4 grid gap-3 lg:grid-cols-2">
            {loading ? <LoadingRows tr={tr} /> : reflections.length ? reflections.slice(0, 6).map((reflection) => (
              <article key={reflection.reflection_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/20 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">{reflectionCategoryLabel(reflection.category, tr)} · {reflection.scope === "subject" ? reflection.subject?.label || tr({ zh: "学科", en: "Subject" }) : tr({ zh: "全局", en: "Global" })}</p>
                    <h3 className="mt-2 line-clamp-2 font-medium">{reflection.value}</h3>
                  </div>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs ${reflectionStatusClass(reflection.status)}`}>{reflectionStatusLabel(reflection.status, tr)}</span>
                </div>
                <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-[var(--muted-foreground)]">{reflection.reason}</p>
                <p className="mt-3 text-[11px] text-[var(--muted-foreground)]">{tr({ zh: `证据 ${reflection.evidence_refs.length} 条 · 置信度 ${Math.round(reflection.confidence * 100)}%`, en: `${reflection.evidence_refs.length} evidence refs · ${Math.round(reflection.confidence * 100)}% confidence` })}</p>
                {reflection.status === "candidate" && reflection.category !== "concept" ? (
                  <div className="mt-3 flex gap-2">
                    <button type="button" onClick={() => void decideReflection(reflection.reflection_id, "confirmed")} className="inline-flex h-8 items-center justify-center rounded-md bg-[var(--primary)] px-3 text-xs font-medium text-[var(--primary-foreground)]">{tr({ zh: "确认使用", en: "Confirm" })}</button>
                    <button type="button" onClick={() => void decideReflection(reflection.reflection_id, "rejected")} className="inline-flex h-8 items-center justify-center rounded-md border border-[var(--border)] px-3 text-xs font-medium hover:border-[var(--destructive)]/50 hover:text-[var(--destructive)]">{tr({ zh: "拒绝", en: "Reject" })}</button>
                  </div>
                ) : null}
              </article>
            )) : <div className="rounded-lg border border-dashed border-[var(--border)] px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">{tr({ zh: "还没有可治理的学习反思。完成一次材料分析、Quiz 或闪卡复习后会出现在这里。", en: "No manageable learning reflections yet. They will appear after material analysis, a quiz, or a flashcard review." })}</div>}
          </div>
        </Panel>
      </section>

      <div className="mt-5 grid gap-5 lg:grid-cols-12">
        <section className="lg:col-span-7"><Panel title={tr({ zh: "教学偏好", en: "Teaching preferences" })} eyebrow="YOUR CONTROL" icon={<Sparkles className="h-4 w-4" />}><div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between"><div><h2 className="text-lg font-semibold">{tr({ zh: "明确偏好优先", en: "Explicit preferences come first" })}</h2><p className="mt-1 max-w-xl text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "你的明确偏好会立即影响生成。行为推断只会在证据足够时补充，且可随时关闭。", en: "Your explicit preferences affect generation immediately. Behavioral inference is only added when evidence is sufficient, and you can disable it at any time." })}</p></div><button type="button" onClick={() => void toggleInference()} disabled={!overview} aria-pressed={Boolean(overview?.inference_enabled)} className={`inline-flex h-10 shrink-0 items-center justify-center rounded-full px-4 text-sm font-medium transition-colors disabled:opacity-50 ${overview?.inference_enabled ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "bg-[var(--muted)] text-[var(--foreground)]"}`}>{overview?.inference_enabled ? tr({ zh: "行为推断已开启", en: "Behavioral inference on" }) : tr({ zh: "行为推断已关闭", en: "Behavioral inference off" })}</button></div><form onSubmit={(event) => { event.preventDefault(); void addPreference(); }} className="mt-5 flex flex-col gap-2 sm:flex-row"><label className="sr-only" htmlFor="learner-preference">{tr({ zh: "告诉 TraitTutor 你的偏好", en: "Tell TraitTutor your preference" })}</label><input id="learner-preference" value={preference} onChange={(event) => setPreference(event.target.value)} className="h-11 min-w-0 flex-1 rounded-md border border-[var(--border)] bg-transparent px-3 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/20" placeholder={tr({ zh: "例如：先给例子，再解释概念", en: "For example: show an example before explaining the concept" })}/><button type="submit" disabled={!preference.trim()} className="inline-flex h-11 items-center justify-center gap-1 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45"><Plus className="h-4 w-4" />{tr({ zh: "保存偏好", en: "Save preference" })}</button></form><div className="mt-4 flex flex-wrap gap-2">{overview?.global.preferences.length ? overview.global.preferences.map((item) => <span key={item.id} className="rounded-full border border-[var(--border)] px-3 py-1.5 text-xs"><span className="font-medium">{item.value}</span><span className="ml-1.5 text-[var(--muted-foreground)]">{item.state === "explicit" ? tr({ zh: "明确", en: "Explicit" }) : tr({ zh: "推断", en: "Inferred" })}</span></span>) : <p className="text-sm text-[var(--muted-foreground)]">{tr({ zh: "尚未记录明确偏好。写下你希望被如何讲解即可开始。", en: "No explicit preferences recorded yet. Describe how you would like concepts explained to get started." })}</p>}</div></Panel></section>

        <aside className="lg:col-span-5"><div className="h-full rounded-xl border border-[var(--border)] bg-[var(--muted)]/30 p-5"><div className="flex gap-3"><ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-[var(--primary)]"/><div><h2 className="font-semibold">{tr({ zh: "可查看、可纠正、可删除", en: "Visible, correctable, and removable" })}</h2><p className="mt-2 text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "每个学科页都能查看概念证据、纠正学科归属或删除单条记录。删除后模型会从剩余证据重新计算。", en: "Each subject page lets you inspect concept evidence, correct subject attribution, or remove individual records. The model recalculates from the remaining evidence." })}</p><Link href="/profile/learning-model" className="mt-4 inline-flex items-center gap-1 text-sm font-medium text-[var(--primary)] hover:underline">{tr({ zh: "进入证据治理", en: "Manage evidence" })} <ArrowUpRight className="h-3.5 w-3.5" /></Link></div></div></div></aside>
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
function SubjectProgress({ profile, tr }: { profile: LearnerProfile; tr: Tr }) { const subject = profile.subject; const understanding = profile.understanding; if (!subject) return null; const mastery = understanding?.verified_mastery ?? 0; const verifiedObservations = profile.concept_signals.reduce((sum, concept) => sum + (concept.verified_observation_count ?? 0), 0); return <Link href={`/profile/learning-model/${encodeURIComponent(subject.subject_id)}`} className="block rounded-lg border border-[var(--border)] p-4 transition-colors hover:border-[var(--primary)]/50"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><div className="flex items-center gap-2"><h3 className="truncate font-medium">{subject.label}</h3><span className="shrink-0 rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px] text-[var(--muted-foreground)]">{tr(UNDERSTANDING_LABEL[understanding?.status ?? "starting"])}</span></div><p className="mt-1 truncate text-xs text-[var(--muted-foreground)]">{subject.path.join(" · ") || tr({ zh: "等待确认学科路径", en: "Awaiting subject-path confirmation" })}</p></div><div className="flex gap-4 text-xs text-[var(--muted-foreground)]"><span>{tr({ zh: `${understanding?.observed_concept_count ?? 0}/${understanding?.concept_count ?? profile.concept_signals.length} 概念`, en: `${understanding?.observed_concept_count ?? 0}/${understanding?.concept_count ?? profile.concept_signals.length} concepts` })}</span><span>{tr({ zh: `${verifiedObservations} 次观测`, en: `${verifiedObservations} observations` })}</span><span>{tr({ zh: `待复习 ${understanding?.review_load ?? 0}`, en: `${understanding?.review_load ?? 0} due for review` })}</span></div></div><div className="mt-4"><div className="flex justify-between text-xs"><span className="text-[var(--muted-foreground)]">{tr({ zh: "已验证理解", en: "Verified understanding" })}</span><span className="font-medium tabular-nums">{Math.round(mastery * 100)}%</span></div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]"><div className="h-full rounded-full bg-[var(--primary)]" style={{ width: `${Math.round(mastery * 100)}%` }} /></div></div></Link>; }
function EmptyKnowledge({ tr }: { tr: Tr }) { return <div className="rounded-lg border border-dashed border-[var(--border)] px-4 py-8 text-center"><Lightbulb className="mx-auto h-5 w-5 text-[var(--primary)]"/><h3 className="mt-3 text-sm font-medium">{tr({ zh: "学习模型会随活动形成", en: "Your learner model grows with activity" })}</h3><p className="mx-auto mt-1 max-w-md text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "上传材料并确认学科，或完成 Quiz、学习路径和闪卡复习后，这里会开始显示有证据支撑的概念进度。", en: "Upload material and confirm its subject, or complete a quiz, learning path, or flashcard review to see evidence-backed concept progress here." })}</p></div>; }
function LoadingRows({ tr }: { tr: Tr }) { return <div className="space-y-3" aria-busy="true" aria-label={tr({ zh: "正在加载学习进度", en: "Loading learning progress" })}>{[0, 1, 2].map((index) => <div key={index} className="h-24 animate-pulse rounded-lg bg-[var(--muted)]/55" />)}</div>; }
function memoryStateLabel(state: string | undefined, syncing: boolean, tr: Tr) { if (syncing || state === "queued" || state === "running") return tr({ zh: "正在从旧记忆归纳证据", en: "Deriving evidence from historical memory" }); if (state === "completed") return tr({ zh: "旧记忆已同步", en: "Historical memory synchronized" }); if (state === "failed") return tr({ zh: "上次同步未完成，可再次尝试", en: "The last synchronization did not finish; you can retry" }); return tr({ zh: "尚未同步旧记忆", en: "Historical memory has not been synchronized" }); }
function reflectionCategoryLabel(category: LearnerReflection["category"], tr: Tr) { const labels: Record<LearnerReflection["category"], Copy> = { goal: { zh: "目标", en: "Goal" }, explanation: { zh: "讲解偏好", en: "Explanation" }, pacing: { zh: "节奏", en: "Pacing" }, feedback: { zh: "反馈", en: "Feedback" }, constraint: { zh: "约束", en: "Constraint" }, concept: { zh: "概念状态", en: "Concept state" }, strategy: { zh: "教学策略", en: "Teaching strategy" } }; return tr(labels[category]); }
function reflectionStatusLabel(status: LearnerReflection["status"], tr: Tr) { const labels: Record<LearnerReflection["status"], Copy> = { candidate: { zh: "候选", en: "Candidate" }, confirmed: { zh: "已确认", en: "Confirmed" }, rejected: { zh: "已拒绝", en: "Rejected" }, stale: { zh: "已过期", en: "Stale" }, needs_rebuild: { zh: "待重建", en: "Needs rebuild" } }; return tr(labels[status]); }
function reflectionStatusClass(status: LearnerReflection["status"]) { if (status === "confirmed") return "bg-[var(--primary)]/10 text-[var(--primary)]"; if (status === "candidate") return "bg-amber-500/10 text-amber-300"; if (status === "rejected") return "bg-[var(--destructive)]/10 text-[var(--destructive)]"; return "bg-[var(--muted)] text-[var(--muted-foreground)]"; }
