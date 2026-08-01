"use client";
/* eslint-disable i18n/no-literal-ui-text -- this Profile application is intentionally Chinese-first in this release. */

import { use, useCallback, useEffect, useState } from "react";
import { Check, Compass, ShieldCheck, Trash2 } from "lucide-react";
import { PageBackLink } from "@/components/navigation/PageBackLink";
import { confirmLearnerSubject, correctLearnerSubject, deleteLearnerEvidence, getLearnerEvidence, getLearnerReflections, getLearnerSubject, getLearningKnowledgeGraph, previewPersonalization, recordLearnerEvent, updateLearnerReflectionStatus, type LearnerEvidence, type LearnerProfile, type LearnerReflection, type LearningKnowledgeGraph, type PersonalizationContext } from "@/lib/learner-model-api";

export default function LearnerSubjectPage({ params }: { params: Promise<{ subjectId: string }> }) {
  const { subjectId } = use(params);
  const [profile, setProfile] = useState<LearnerProfile | null>(null);
  const [evidence, setEvidence] = useState<LearnerEvidence[]>([]);
  const [graph, setGraph] = useState<LearningKnowledgeGraph | null>(null);
  const [reflections, setReflections] = useState<LearnerReflection[]>([]);
  const [compassPreview, setCompassPreview] = useState<PersonalizationContext | null>(null);
  const [reflectionError, setReflectionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    const [nextProfile, nextEvidence, nextGraph, nextReflections] = await Promise.all([
      getLearnerSubject(subjectId),
      getLearnerEvidence(subjectId),
      getLearningKnowledgeGraph(subjectId),
      getLearnerReflections(subjectId).catch((cause) => {
        setReflectionError(cause instanceof Error ? cause.message : "学习反思暂不可用");
        return { reflections: [], summary: { candidate: 0, confirmed: 0, rejected: 0, stale: 0, needs_rebuild: 0, applies_to_compass: 0 } };
      }),
    ]);
    setProfile(nextProfile); setEvidence(nextEvidence.evidence); setGraph(nextGraph); setReflections(nextReflections.reflections);
    if (nextReflections.reflections.length) setReflectionError("");
    if (nextProfile.subject) {
      const preview = await previewPersonalization({
        purpose: "courseware",
        subject: nextProfile.subject,
        current_instruction: "Preview the task-local Compass for this subject.",
      }).catch(() => null);
      setCompassPreview(preview);
    }
  }, [subjectId]);
  useEffect(() => { void load().then(() => setError("")).catch((cause) => setError(cause instanceof Error ? cause.message : "无法加载学习模型")); }, [load]);
  async function action(work: () => Promise<void>) { setBusy(true); setError(""); try { await work(); } catch (cause) { setError(cause instanceof Error ? cause.message : "操作未完成，请重试"); } finally { setBusy(false); } }
  async function remove(signalId: string) { await action(async () => { await deleteLearnerEvidence(signalId); await load(); }); }
  async function confirm() { await action(async () => { await confirmLearnerSubject(subject); await load(); }); }
  async function correct() { const label = window.prompt("请输入正确的学科名称", subject.label)?.trim(); if (!label) return; const subjectId = window.prompt("请输入稳定学科标识（例如 mathematics-algebra）", subject.subject_id)?.trim(); if (!subjectId) return; await action(async () => { await correctLearnerSubject(subject.subject_id, { subject_id: subjectId, label, path: [label] }); window.location.assign(`/profile/learning-model/${encodeURIComponent(subjectId)}`); }); }
  async function selfAssess(observation: "known" | "unknown") { await action(async () => { await recordLearnerEvent({ event_type: "self_assessment", subject_id: subject.subject_id, concept_id: "subject-self-assessment", concept_label: "学科整体自评（待验证）", observation, payload: {} }); await load(); }); }
  async function decideReflection(reflectionId: string, status: "confirmed" | "rejected") { await action(async () => { await updateLearnerReflectionStatus(reflectionId, status); await load(); }); }
  if (!profile?.subject) return <div className="space-y-3 p-10 text-sm text-[var(--muted-foreground)]"><p>{error || "正在加载学习模型…"}</p><PageBackLink href="/profile/learning-model">返回我的学习模型</PageBackLink></div>;
  const subject = profile.subject;
  const activeReflections = reflections.filter((item) => item.applies_to_compass);
  const candidateReflections = reflections.filter((item) => item.status === "candidate");
  const weakConcepts = compassPreview?.relevant_concept_signals ?? [];
  const appliedPreferences = [
    ...(compassPreview?.memory_snapshot?.explicit_preferences ?? []),
    ...((compassPreview?.plan.rationale ?? []).filter((item) => item.source === "explicit_preference").map((item) => item.text.replace(/^Used your preference:\s*/i, ""))),
  ].filter(Boolean);
  return <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6 sm:py-10">
    <PageBackLink href="/profile/learning-model">返回我的学习模型</PageBackLink>
    {error && <p className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="font-serif text-3xl font-semibold">{subject.label}</h1><p className="mt-1 text-sm text-[var(--muted-foreground)]">{subject.path.join(" · ")}</p></div><div className="flex gap-2">{subject.confirmed ? <span className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs text-emerald-700"><Check className="h-3.5 w-3.5"/>已确认分类</span> : <button type="button" disabled={busy} onClick={() => void confirm()} className="inline-flex items-center gap-1 rounded-full border border-amber-300 px-3 py-1 text-xs text-amber-700 disabled:opacity-50"><Check className="h-3.5 w-3.5"/>确认此分类</button>}<button type="button" disabled={busy} onClick={() => void correct()} className="rounded-full border px-3 py-1 text-xs disabled:opacity-50">纠正学科</button></div></header>
    <section className="rounded-xl border p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted-foreground)]"><Compass className="h-4 w-4 text-[var(--primary)]"/>HERMES COMPASS</div>
          <h2 className="mt-2 font-semibold">下一次生成会用什么记忆</h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">Compass 是当前任务的最小个性化上下文：只纳入已确认偏好、明确约束和这门学科的可验证薄弱概念；候选反思不会直接影响生成。</p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="rounded-full bg-[var(--primary)]/10 px-3 py-1 text-[var(--primary)]">进入 Compass {activeReflections.length}</span>
          <span className="rounded-full bg-[var(--muted)] px-3 py-1 text-[var(--muted-foreground)]">候选 {candidateReflections.length}</span>
          {compassPreview?.degraded ? <span className="rounded-full bg-amber-500/10 px-3 py-1 text-amber-700">已降级</span> : null}
        </div>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div className="rounded-lg bg-[var(--muted)]/35 p-3">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">明确偏好</p>
          <p className="mt-2 text-sm">{appliedPreferences.length ? appliedPreferences.slice(0, 2).join("；") : "暂无已确认偏好"}</p>
        </div>
        <div className="rounded-lg bg-[var(--muted)]/35 p-3">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">约束</p>
          <p className="mt-2 text-sm">{compassPreview?.constraints.length ? compassPreview.constraints.slice(0, 2).join("；") : "暂无拒绝约束"}</p>
        </div>
        <div className="rounded-lg bg-[var(--muted)]/35 p-3">
          <p className="text-xs font-medium text-[var(--muted-foreground)]">薄弱概念</p>
          <p className="mt-2 text-sm">{weakConcepts.length ? weakConcepts.slice(0, 3).map((item) => item.label).join("、") : "等待 Quiz/闪卡证据"}</p>
        </div>
      </div>
      {compassPreview?.plan.rationale?.length ? <div className="mt-4 rounded-lg border border-[var(--border)] p-3"><p className="text-xs font-medium text-[var(--muted-foreground)]">为什么这样生成</p><ul className="mt-2 space-y-1 text-sm">{compassPreview.plan.rationale.slice(0, 3).map((item, index) => <li key={`${item.source}-${index}`}>· {item.text}</li>)}</ul></div> : null}
    </section>
    <section className="rounded-xl border p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted-foreground)]"><ShieldCheck className="h-4 w-4 text-[var(--primary)]"/>REFLECTION GOVERNANCE</div>
          <h2 className="mt-2 font-semibold">学习反思治理</h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">这里展示这门学科的 Reflection：你可以确认或拒绝偏好类反思；概念类反思来自材料和练习证据，只能通过继续练习或删除证据重建。</p>
        </div>
      </div>
      {reflectionError ? <p className="mt-3 rounded-md border border-amber-400/35 bg-amber-400/10 p-3 text-sm text-amber-700">学习反思暂不可用：{reflectionError}</p> : null}
      <div className="mt-4 space-y-2">{reflections.length ? reflections.slice(0, 8).map((reflection) => <div key={reflection.reflection_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/25 p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0"><p className="text-xs text-[var(--muted-foreground)]">{reflectionCategoryLabel(reflection.category)} · {reflectionStatusLabel(reflection.status)}</p><p className="mt-1 break-words text-sm font-medium">{reflection.value}</p><p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">{reflection.reason}</p></div>
          {reflection.status === "candidate" && reflection.category !== "concept" ? <div className="flex shrink-0 gap-2"><button type="button" disabled={busy} onClick={() => void decideReflection(reflection.reflection_id, "confirmed")} className="rounded-md bg-[var(--primary)] px-2.5 py-1.5 text-xs font-medium text-[var(--primary-foreground)] disabled:opacity-50">确认使用</button><button type="button" disabled={busy} onClick={() => void decideReflection(reflection.reflection_id, "rejected")} className="rounded-md border px-2.5 py-1.5 text-xs disabled:opacity-50">拒绝</button></div> : <span className="shrink-0 rounded-full bg-[var(--muted)] px-2.5 py-1 text-xs text-[var(--muted-foreground)]">{reflection.applies_to_compass ? "会进入 Compass" : "不直接注入"}</span>}
        </div>
      </div>) : <p className="rounded-lg border border-dashed border-[var(--border)] p-4 text-sm text-[var(--muted-foreground)]">还没有这门学科的反思。完成材料分析、Quiz 或闪卡复习后会出现。</p>}</div>
    </section>
    <section className="rounded-xl border p-4 sm:p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">概念状态</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">{profile.understanding ? `当前：${({ starting: "刚开始", learning: "学习中", familiar: "较熟悉", verified: "已验证掌握" } as Record<string, string>)[profile.understanding.status]}；已验证理解 ${Math.round(profile.understanding.verified_mastery * 100)}%，待复习 ${profile.understanding.review_load} 项。` : "尚无可验证的概念练习证据。"}</p></div><div className="flex flex-wrap gap-2"><button type="button" disabled={busy} onClick={() => void selfAssess("unknown")} className="rounded-md border px-2.5 py-1.5 text-xs">我还不熟悉</button><button type="button" disabled={busy} onClick={() => void selfAssess("known")} className="rounded-md border px-2.5 py-1.5 text-xs">我已学过</button></div></div><div className="mt-3 space-y-2">{profile.concept_signals.map((item) => <div key={item.concept_id} className="flex flex-col gap-1 rounded-md bg-[var(--muted)]/40 p-3 text-sm sm:flex-row sm:justify-between sm:gap-3"><span className="min-w-0 break-words">{item.label}</span><span className="text-xs text-[var(--muted-foreground)] sm:text-right">{item.support_level} · {item.verified_observation_count ?? 0} 次可判分证据 · {Math.round((item.mastery_probability ?? 0) * 100)}%</span></div>)}</div></section>
    <section className="rounded-xl border p-4 sm:p-5"><h2 className="font-semibold">学习知识地图</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">图谱定义概念之间的材料依据关系；BKT 覆盖在对应节点上，用于寻找前置薄弱点和下一步复习重点。</p>{graph?.nodes.length ? <div className="mt-4 space-y-3">{graph.nodes.map((node) => { const signal = profile.concept_signals.find((item) => item.concept_id === node.concept_id); const prerequisites = graph.edges.filter((edge) => edge.relation === "prerequisite" && edge.target_concept_id === node.concept_id).map((edge) => graph.nodes.find((candidate) => candidate.concept_id === edge.source_concept_id)?.label).filter(Boolean); return <div key={node.concept_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium">{node.label}</p><span className="text-xs text-[var(--muted-foreground)]">{signal ? `${Math.round((signal.mastery_probability ?? 0) * 100)}% · ${signal.verified_observation_count ?? 0} 次验证` : "尚未练习"}</span></div><p className="mt-1 text-xs text-[var(--muted-foreground)]">{node.module_label}{prerequisites.length ? ` · 前置：${prerequisites.join("、")}` : ""}</p></div>; })}</div> : <div className="mt-4 rounded-lg border border-dashed border-[var(--border)] p-4 text-sm text-[var(--muted-foreground)]">完成一次材料分析与生成后，TraitTutor 会从材料中建立有出处的概念关系；没有证据时不会猜测前置关系。</div>}</section>
    <section className="rounded-xl border p-5"><h2 className="font-semibold">策略证据</h2><div className="mt-3 space-y-2">{profile.strategy_evidence.length ? profile.strategy_evidence.map((item) => <div key={item.id} className="rounded-md bg-[var(--muted)]/40 p-3 text-sm">{item.task_type} · 正向 {item.positive_weight} / 负向 {item.negative_weight} · 已验证事件 {item.event_ids?.length ?? 0}</div>) : <p className="text-sm text-[var(--muted-foreground)]">尚无策略反馈。</p>}</div></section>
    <section className="rounded-xl border p-5"><h2 className="font-semibold">逐条证据治理</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">删除后，相关概念和策略会从剩余证据重新计算。</p><div className="mt-4 space-y-2">{evidence.length ? evidence.map((item) => <div key={item.signal_id} className="flex items-center justify-between gap-3 rounded-md border p-3"><div className="min-w-0"><p className="text-sm font-medium">{item.kind}</p><p className="truncate text-xs text-[var(--muted-foreground)]">{String(item.payload.value || item.payload.concept || item.payload.strategy || "学习信号")}</p></div><button type="button" disabled={busy} onClick={() => void remove(item.signal_id)} className="inline-flex shrink-0 items-center gap-1 rounded-md border border-red-200 px-2 py-1.5 text-xs text-red-700 disabled:opacity-50"><Trash2 className="h-3.5 w-3.5"/>删除</button></div>) : <p className="text-sm text-[var(--muted-foreground)]">该学科还没有可治理的事件。</p>}</div></section>
  </div>;
}

function reflectionCategoryLabel(category: LearnerReflection["category"]) { return ({ goal: "目标", explanation: "讲解偏好", pacing: "节奏", feedback: "反馈", constraint: "约束", concept: "概念状态", strategy: "教学策略" } as const)[category] ?? category; }
function reflectionStatusLabel(status: LearnerReflection["status"]) { return ({ candidate: "候选", confirmed: "已确认", rejected: "已拒绝", stale: "已过期", needs_rebuild: "待重建" } as const)[status] ?? status; }
