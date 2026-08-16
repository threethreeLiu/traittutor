"use client";

import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Compass, ShieldCheck, Trash2 } from "lucide-react";
import { PageBackLink } from "@/components/navigation/PageBackLink";
import { LearnerSubjectGovernanceTab } from "@/components/personalization/LearnerSubjectGovernanceTab";
import { LearnerSubjectTabs } from "@/components/personalization/LearnerSubjectTabs";
import { LearningGovernancePanel } from "@/components/personalization/LearningGovernancePanel";
import { MasteryStateValue } from "@/components/personalization/MasteryStateValue";
import { useAppShell } from "@/context/AppShellContext";
import {
  getLearningModelSubjectDetail,
  type LearningModelSubjectDetail,
} from "@/lib/learning-model-read-api";
import {
  getLearnerSubjectLearningState,
  type LearnerSubjectLearningState,
} from "@/lib/learning-governance-api";
import {
  confirmLearnerSubject,
  correctLearnerSubject,
  deleteLearnerEvidence,
  getLearnerEvidence,
  getLearnerReflections,
  getLearnerSubject,
  getLearningKnowledgeGraph,
  previewPersonalization,
  recordLearnerEvent,
  updateLearnerReflectionStatus,
  type LearnerEvidence,
  type LearnerProfile,
  type LearnerReflection,
  type LearningKnowledgeGraph,
  type PersonalizationContext,
} from "@/lib/learner-model-api";

type Copy = { zh: string; en: string };
type Tr = (copy: Copy) => string;

const UNDERSTANDING_LABELS: Record<string, Copy> = {
  starting: { zh: "刚开始", en: "Getting started" },
  learning: { zh: "学习中", en: "Learning" },
  familiar: { zh: "较熟悉", en: "Familiar" },
  verified: { zh: "已验证掌握", en: "Verified mastery" },
};

export default function LearnerSubjectPage({ params }: { params: Promise<{ subjectId: string }> }) {
  const { subjectId } = use(params);
  const router = useRouter();
  const { language } = useAppShell();
  const zh = language === "zh";
  const tr = useCallback((copy: Copy) => (zh ? copy.zh : copy.en), [zh]);
  const [profile, setProfile] = useState<LearnerProfile | null>(null);
  const [evidence, setEvidence] = useState<LearnerEvidence[]>([]);
  const [graph, setGraph] = useState<LearningKnowledgeGraph | null>(null);
  const [reflections, setReflections] = useState<LearnerReflection[]>([]);
  const [compassPreview, setCompassPreview] = useState<PersonalizationContext | null>(null);
  const [canonicalState, setCanonicalState] = useState<LearnerSubjectLearningState | null>(null);
  const [readDetail, setReadDetail] = useState<LearningModelSubjectDetail | null>(null);
  const [reflectionError, setReflectionError] = useState("");
  const [moduleErrors, setModuleErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [detailResult, profileResult, evidenceResult, graphResult, reflectionsResult, canonicalResult] = await Promise.allSettled([
      getLearningModelSubjectDetail(subjectId),
      getLearnerSubject(subjectId),
      getLearnerEvidence(subjectId),
      getLearningKnowledgeGraph(subjectId),
      getLearnerReflections(subjectId),
      // The canonical ledger is the only BKT fact source. A temporary read
      // failure does not hide the rest of the learner profile, but never falls
      // back to a legacy percentage.
      getLearnerSubjectLearningState(subjectId),
    ]);

    if (profileResult.status === "rejected") throw profileResult.reason;
    const nextProfile = profileResult.value;
    setProfile(nextProfile);

    const nextModuleErrors: Record<string, string> = {};
    if (detailResult.status === "fulfilled") setReadDetail(detailResult.value);
    else {
      setReadDetail(null);
      nextModuleErrors.readModel = errorMessage(detailResult.reason, tr({ zh: "学习画像摘要暂不可用", en: "Learning-profile summary is unavailable" }));
    }
    if (evidenceResult.status === "fulfilled") setEvidence(evidenceResult.value.evidence);
    else nextModuleErrors.evidence = errorMessage(evidenceResult.reason, tr({ zh: "证据治理暂不可用", en: "Evidence governance is unavailable" }));
    if (graphResult.status === "fulfilled") setGraph(graphResult.value);
    else nextModuleErrors.graph = errorMessage(graphResult.reason, tr({ zh: "知识图谱暂不可用", en: "Knowledge graph is unavailable" }));
    if (reflectionsResult.status === "fulfilled") {
      setReflections(reflectionsResult.value.reflections);
      setReflectionError("");
    } else {
      setReflectionError(errorMessage(reflectionsResult.reason, tr({ zh: "学习反思暂不可用", en: "Learning reflections are unavailable" })));
    }
    if (canonicalResult.status === "fulfilled") setCanonicalState(canonicalResult.value);
    else {
      setCanonicalState(null);
      nextModuleErrors.canonical = errorMessage(canonicalResult.reason, tr({ zh: "知识状态暂不可用", en: "Knowledge state is unavailable" }));
    }

    if (nextProfile.subject) {
      const preview = await previewPersonalization({
        purpose: "courseware",
        subject: nextProfile.subject,
        current_instruction: "Preview the task-local Compass for this subject.",
      }).catch((cause) => {
        nextModuleErrors.compass = errorMessage(cause, tr({ zh: "教学支持预览暂不可用", en: "Teaching-support preview is unavailable" }));
        return null;
      });
      setCompassPreview(preview);
    }
    setModuleErrors(nextModuleErrors);
  }, [subjectId, tr]);

  useEffect(() => {
    void load()
      .then(() => setError(""))
      .catch((cause) => setError(
        cause instanceof Error
          ? cause.message
          : tr({ zh: "无法加载学习模型", en: "Unable to load the learner model" }),
      ));
  }, [load, tr]);

  async function action(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : tr({ zh: "操作未完成，请重试", en: "The action could not be completed. Please try again." }),
      );
    } finally {
      setBusy(false);
    }
  }

  async function remove(signalId: string) {
    await action(async () => {
      await deleteLearnerEvidence(signalId);
      await load();
    });
  }

  async function confirm() {
    if (!profile?.subject) return;
    await action(async () => {
      await confirmLearnerSubject(profile.subject!);
      await load();
    });
  }

  async function correct() {
    if (!profile?.subject) return;
    const currentSubject = profile.subject;
    const label = window.prompt(
      tr({ zh: "请输入正确的学科名称", en: "Enter the correct subject name" }),
      currentSubject.label,
    )?.trim();
    if (!label) return;
    const correctedSubjectId = window.prompt(
      tr({ zh: "请输入稳定学科标识（例如 mathematics-algebra）", en: "Enter a stable subject ID (for example, mathematics-algebra)" }),
      currentSubject.subject_id,
    )?.trim();
    if (!correctedSubjectId) return;
    await action(async () => {
      await correctLearnerSubject(currentSubject.subject_id, {
        subject_id: correctedSubjectId,
        label,
        path: [label],
      });
      router.push(`/settings/learning-model/${encodeURIComponent(correctedSubjectId)}`);
    });
  }

  async function selfAssess(observation: "known" | "unknown") {
    if (!profile?.subject) return;
    await action(async () => {
      await recordLearnerEvent({
        event_type: "self_assessment",
        subject_id: profile.subject!.subject_id,
        concept_id: "subject-self-assessment",
        concept_label: tr({ zh: "学科整体自评（待验证）", en: "Overall subject self-assessment (unverified)" }),
        observation,
        payload: {},
      });
      await load();
    });
  }

  async function decideReflection(reflectionId: string, status: "confirmed" | "rejected") {
    await action(async () => {
      await updateLearnerReflectionStatus(reflectionId, status);
      await load();
    });
  }

  if (!profile?.subject) {
    return (
      <div className="space-y-3 p-10 text-sm text-[var(--muted-foreground)]">
        <p role={error ? "alert" : "status"}>
          {error || tr({ zh: "正在加载学习模型…", en: "Loading learner model…" })}
        </p>
        <PageBackLink href="/settings/learning-model">
          {tr({ zh: "返回我的学习模型", en: "Back to my learner model" })}
        </PageBackLink>
      </div>
    );
  }

  const subject = profile.subject;
  const activeReflections = reflections.filter((item) => item.applies_to_compass);
  const candidateReflections = reflections.filter((item) => item.status === "candidate");
  const weakConcepts = compassPreview?.relevant_concept_signals ?? [];
  const appliedPreferences = [
    ...(compassPreview?.memory_snapshot?.explicit_preferences ?? []),
    ...((compassPreview?.plan.rationale ?? [])
      .filter((item) => item.source === "explicit_preference")
      .map((item) => item.text.replace(/^Used your preference:\s*/i, ""))),
  ].filter(Boolean);
  const updatedAt = formatDateTime(readDetail?.header?.updated_at ?? profile.updated_at, zh ? "zh-CN" : "en");
  const tabs = [
    { id: "overview" as const, label: tr({ zh: "总览", en: "Overview" }) },
    { id: "knowledge" as const, label: tr({ zh: "知识与 KC", en: "Knowledge & KCs" }) },
    { id: "errors" as const, label: tr({ zh: "错题与修复", en: "Errors & repairs" }) },
    { id: "reviews" as const, label: tr({ zh: "复习计划", en: "Reviews" }) },
    { id: "misconceptions" as const, label: tr({ zh: "误区候选", en: "Misconceptions" }) },
    { id: "support" as const, label: tr({ zh: "教学支持", en: "Teaching support" }) },
    { id: "governance" as const, label: tr({ zh: "数据与治理", en: "Data & governance" }) },
  ];

  return (
    <main className="w-full space-y-6 py-4">
      <PageBackLink href="/settings/learning-model">
        {tr({ zh: "返回我的学习模型", en: "Back to my learner model" })}
      </PageBackLink>
      {error ? <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}
      {moduleErrors.readModel ? <ModuleError title={tr({ zh: "学习画像摘要暂不可用", en: "Learning-profile summary unavailable" })} detail={moduleErrors.readModel} /> : null}

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-serif text-3xl font-semibold">{subject.label}</h1>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">{subject.path.join(" · ")}</p>
          <p className="mt-2 text-xs text-[var(--muted-foreground)]">
            {tr({ zh: `最近更新 ${updatedAt}`, en: `Last updated ${updatedAt}` })}
            {profile.needs_rebuild ? tr({ zh: " · 模型需要重建", en: " · Model rebuild required" }) : ""}
          </p>
        </div>
        <div className="flex gap-2">
          {subject.confirmed ? (
            <span className="inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs text-emerald-700">
              <Check className="h-3.5 w-3.5" />{tr({ zh: "已确认分类", en: "Classification confirmed" })}
            </span>
          ) : (
            <button type="button" disabled={busy} onClick={() => void confirm()} className="inline-flex items-center gap-1 rounded-full border border-amber-300 px-3 py-1 text-xs text-amber-700 disabled:opacity-50">
              <Check className="h-3.5 w-3.5" />{tr({ zh: "确认此分类", en: "Confirm classification" })}
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => void correct()} className="rounded-full border px-3 py-1 text-xs disabled:opacity-50">
            {tr({ zh: "纠正学科", en: "Correct subject" })}
          </button>
        </div>
      </header>

      <LearnerSubjectTabs
        tabs={tabs}
        ariaLabel={tr({ zh: "学科画像导航", en: "Subject profile navigation" })}
        panels={{
          overview: (
            <section className="rounded-xl border p-4 sm:p-5" aria-labelledby="subject-overview-heading">
              <h2 id="subject-overview-heading" className="font-semibold">{tr({ zh: "当前学习概况", en: "Current learning overview" })}</h2>
              <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                {profile.understanding
                  ? tr({ zh: `当前处于“${tr(UNDERSTANDING_LABELS[profile.understanding.status] ?? UNDERSTANDING_LABELS.starting)}”，待复习 ${profile.understanding.review_load} 项。`, en: `Currently ${tr(UNDERSTANDING_LABELS[profile.understanding.status] ?? UNDERSTANDING_LABELS.starting)}, with ${profile.understanding.review_load} reviews due.` })
                  : tr({ zh: "尚无可验证的概念练习证据。继续完成可判分练习后，这里会形成学习重点。", en: "No verifiable concept-practice evidence yet. Complete graded practice to establish learning priorities." })}
              </p>
              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                <SummaryCard title={tr({ zh: "已覆盖 KC", en: "Covered KCs" })} value={profile.understanding ? String(profile.understanding.concept_count) : tr({ zh: "积累中", en: "Collecting evidence" })} />
                <SummaryCard title={tr({ zh: "强证据", en: "Strong evidence" })} value={canonicalState ? String(canonicalState.strong_event_count) : tr({ zh: "证据不足", en: "Insufficient evidence" })} />
                <SummaryCard title={tr({ zh: "模型状态", en: "Model status" })} value={profile.needs_rebuild ? tr({ zh: "需要重建", en: "Needs rebuild" }) : tr({ zh: "当前可用", en: "Available" })} />
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <button type="button" disabled={busy} onClick={() => void selfAssess("unknown")} className="rounded-md border px-3 py-2 text-sm disabled:opacity-50">{tr({ zh: "我还不熟悉", en: "I am not familiar yet" })}</button>
                <button type="button" disabled={busy} onClick={() => void selfAssess("known")} className="rounded-md border px-3 py-2 text-sm disabled:opacity-50">{tr({ zh: "我已学过", en: "I have studied this" })}</button>
              </div>
            </section>
          ),
          knowledge: (
            <>
              {moduleErrors.canonical
                ? <ModuleError title={tr({ zh: "知识状态暂不可用", en: "Knowledge state unavailable" })} detail={moduleErrors.canonical} />
                : <CanonicalBktPanel state={canonicalState} profile={profile} tr={tr} />}
              <section className="rounded-xl border p-4 sm:p-5" aria-labelledby="concept-state-heading">
                <h2 id="concept-state-heading" className="font-semibold">{tr({ zh: "概念与 KC 列表", en: "Concept and KC list" })}</h2>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr({ zh: "这里只显示定性证据状态、错题修复和复习变化，不显示掌握概率。", en: "This view shows qualitative evidence, repair, and review changes—never mastery probabilities." })}</p>
                <div className="mt-3 space-y-2">
                  {profile.concept_signals.length ? profile.concept_signals.map((item) => (
                    <div key={item.concept_id} className="flex flex-col gap-1 rounded-md bg-[var(--muted)]/40 p-3 text-sm sm:flex-row sm:justify-between sm:gap-3">
                      <span className="min-w-0 break-words">{item.label}</span>
                      <span className="text-xs text-[var(--muted-foreground)] sm:text-right">
                        {tr({ zh: `${item.verified_observation_count ?? 0} 次可判分证据`, en: `${item.verified_observation_count ?? 0} graded observations` })}
                        <span className="ml-2"><MasteryStateValue evidenceState={item.evidence_state} /></span>
                      </span>
                    </div>
                  )) : <EmptyState text={tr({ zh: "还没有 KC 记录。", en: "No KC records yet." })} />}
                </div>
              </section>
              <section className="rounded-xl border p-4 sm:p-5" aria-labelledby="knowledge-map-heading">
                <h2 id="knowledge-map-heading" className="font-semibold">{tr({ zh: "学习知识地图", en: "Learning knowledge map" })}</h2>
                <p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr({ zh: "图谱只展示有材料依据的概念关系；图谱不可用时仍保留上方 KC 列表。", en: "The graph shows only material-grounded concept relationships. The KC list remains available when the graph is unavailable." })}</p>
                {moduleErrors.graph ? <ModuleError title={tr({ zh: "知识图谱暂不可用", en: "Knowledge graph unavailable" })} detail={moduleErrors.graph} /> : graph?.nodes.length ? (
                  <div className="mt-4 space-y-3">{graph.nodes.map((node) => {
                    const signal = profile.concept_signals.find((item) => item.concept_id === node.concept_id);
                    const prerequisites = graph.edges.filter((edge) => edge.relation === "prerequisite" && edge.target_concept_id === node.concept_id).map((edge) => graph.nodes.find((candidate) => candidate.concept_id === edge.source_concept_id)?.label).filter(Boolean);
                    return <article key={node.concept_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-medium">{node.label}</h3><span className="text-xs text-[var(--muted-foreground)]">{signal ? <><MasteryStateValue evidenceState={signal.evidence_state} /><span className="ml-2">{tr({ zh: `${signal.verified_observation_count ?? 0} 次验证`, en: `${signal.verified_observation_count ?? 0} verified observations` })}</span></> : tr({ zh: "尚未练习", en: "Not practiced yet" })}</span></div><p className="mt-1 text-xs text-[var(--muted-foreground)]">{node.module_label}{prerequisites.length ? tr({ zh: ` · 前置：${prerequisites.join("、")}`, en: ` · Prerequisites: ${prerequisites.join(", ")}` }) : ""}</p></article>;
                  })}</div>
                ) : <EmptyState text={tr({ zh: "尚无知识图谱，当前退化为 KC 列表。", en: "No knowledge graph yet; the KC list is used as the fallback." })} />}
              </section>
            </>
          ),
          errors: <LearnerSubjectGovernanceTab subjectId={subject.subject_id} focus="errors" />,
          reviews: <LearnerSubjectGovernanceTab subjectId={subject.subject_id} focus="reviews" />,
          misconceptions: <LearnerSubjectGovernanceTab subjectId={subject.subject_id} focus="misconceptions" />,
          support: (
            <>
      {moduleErrors.compass ? <ModuleError title={tr({ zh: "教学支持预览暂不可用", en: "Teaching-support preview unavailable" })} detail={moduleErrors.compass} /> : (
      <section className="rounded-xl border p-4 sm:p-5" aria-labelledby="compass-heading">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted-foreground)]"><Compass className="h-4 w-4 text-[var(--primary)]" />{"HERMES COMPASS"}</div>
            <h2 id="compass-heading" className="mt-2 font-semibold">{tr({ zh: "下一次生成会使用哪些记忆", en: "Memories used in the next generation" })}</h2>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">
              {tr({ zh: "Compass 是当前任务的最小个性化上下文：只纳入已确认偏好、明确约束和这门学科的可验证薄弱概念；候选反思不会直接影响生成。", en: "Compass is the minimum personalized context for the current task. It includes only confirmed preferences, explicit constraints, and verified weak concepts for this subject; candidate reflections do not directly affect generation." })}
            </p>
          </div>
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-[var(--primary)]/10 px-3 py-1 text-[var(--primary)]">{tr({ zh: `进入 Compass ${activeReflections.length}`, en: `${activeReflections.length} in Compass` })}</span>
            <span className="rounded-full bg-[var(--muted)] px-3 py-1 text-[var(--muted-foreground)]">{tr({ zh: `候选 ${candidateReflections.length}`, en: `${candidateReflections.length} candidates` })}</span>
            {compassPreview?.degraded ? <span className="rounded-full bg-amber-500/10 px-3 py-1 text-amber-700">{tr({ zh: "已降级", en: "Degraded" })}</span> : null}
          </div>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <SummaryCard title={tr({ zh: "明确偏好", en: "Explicit preferences" })} value={appliedPreferences.length ? appliedPreferences.slice(0, 2).join(zh ? "；" : "; ") : tr({ zh: "暂无已确认偏好", en: "No confirmed preferences" })} />
          <SummaryCard title={tr({ zh: "约束", en: "Constraints" })} value={compassPreview?.constraints.length ? compassPreview.constraints.slice(0, 2).join(zh ? "；" : "; ") : tr({ zh: "暂无拒绝约束", en: "No rejected constraints" })} />
          <SummaryCard title={tr({ zh: "薄弱概念", en: "Weak concepts" })} value={weakConcepts.length ? weakConcepts.slice(0, 3).map((item) => item.label).join(zh ? "、" : ", ") : tr({ zh: "等待 Quiz 或闪卡证据", en: "Waiting for quiz or flashcard evidence" })} />
        </div>
        {compassPreview?.plan.rationale?.length ? (
          <div className="mt-4 rounded-lg border border-[var(--border)] p-3">
            <p className="text-xs font-medium text-[var(--muted-foreground)]">{tr({ zh: "为什么这样生成", en: "Why generation is configured this way" })}</p>
            <ul className="mt-2 space-y-1 text-sm">{compassPreview.plan.rationale.slice(0, 3).map((item, index) => <li key={`${item.source}-${index}`}>· {item.text}</li>)}</ul>
          </div>
        ) : null}
      </section>
      )}

      <section className="rounded-xl border p-4 sm:p-5" aria-labelledby="reflection-heading">
        <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted-foreground)]"><ShieldCheck className="h-4 w-4 text-[var(--primary)]" />{"REFLECTION GOVERNANCE"}</div>
        <h2 id="reflection-heading" className="mt-2 font-semibold">{tr({ zh: "学习反思治理", en: "Learning reflection governance" })}</h2>
        <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">
          {tr({ zh: "这里展示这门学科的 Reflection：你可以确认或拒绝偏好类反思；概念类反思来自材料和练习证据，只能通过继续练习或删除证据重建。", en: "This section shows reflections for the subject. You can confirm or reject preference reflections; concept reflections come from material and practice evidence and change only through further practice or evidence removal." })}
        </p>
        {reflectionError ? <p role="status" className="mt-3 rounded-md border border-amber-400/35 bg-amber-400/10 p-3 text-sm text-amber-700">{tr({ zh: `学习反思暂不可用：${reflectionError}`, en: `Learning reflections are temporarily unavailable: ${reflectionError}` })}</p> : null}
        <div className="mt-4 space-y-2">
          {reflections.length ? reflections.slice(0, 8).map((reflection) => (
            <article key={reflection.reflection_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/25 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-xs text-[var(--muted-foreground)]">{reflectionCategoryLabel(reflection.category, tr)} · {reflectionStatusLabel(reflection.status, tr)}</p>
                  <h3 className="mt-1 break-words text-sm font-medium">{reflection.value}</h3>
                  <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">{reflection.reason}</p>
                </div>
                {reflection.status === "candidate" && reflection.category !== "concept" ? (
                  <div className="flex shrink-0 gap-2">
                    <button type="button" disabled={busy} onClick={() => void decideReflection(reflection.reflection_id, "confirmed")} className="rounded-md bg-[var(--primary)] px-2.5 py-1.5 text-xs font-medium text-[var(--primary-foreground)] disabled:opacity-50">{tr({ zh: "确认使用", en: "Confirm" })}</button>
                    <button type="button" disabled={busy} onClick={() => void decideReflection(reflection.reflection_id, "rejected")} className="rounded-md border px-2.5 py-1.5 text-xs disabled:opacity-50">{tr({ zh: "拒绝", en: "Reject" })}</button>
                  </div>
                ) : (
                  <span className="shrink-0 rounded-full bg-[var(--muted)] px-2.5 py-1 text-xs text-[var(--muted-foreground)]">{reflection.applies_to_compass ? tr({ zh: "会进入 Compass", en: "Included in Compass" }) : tr({ zh: "不直接注入", en: "Not injected directly" })}</span>
                )}
              </div>
            </article>
          )) : reflectionError ? null : <EmptyState text={tr({ zh: "还没有这门学科的反思。完成材料分析、Quiz 或闪卡复习后会出现。", en: "No reflections for this subject yet. They will appear after material analysis, a quiz, or a flashcard review." })} />}
        </div>
      </section>

      <section className="rounded-xl border p-5" aria-labelledby="strategy-evidence-heading">
        <h2 id="strategy-evidence-heading" className="font-semibold">{tr({ zh: "策略证据", en: "Strategy evidence" })}</h2>
        <div className="mt-3 space-y-2">
          {profile.strategy_evidence.length ? profile.strategy_evidence.map((item) => (
            <div key={item.id} className="rounded-md bg-[var(--muted)]/40 p-3 text-sm">{tr({ zh: `${item.task_type} · 正向 ${item.positive_weight} / 负向 ${item.negative_weight} · 已验证事件 ${item.event_ids?.length ?? 0}`, en: `${item.task_type} · positive ${item.positive_weight} / negative ${item.negative_weight} · ${item.event_ids?.length ?? 0} verified events` })}</div>
          )) : <p className="text-sm text-[var(--muted-foreground)]">{tr({ zh: "尚无策略反馈。", en: "No strategy feedback yet." })}</p>}
        </div>
      </section>
            </>
          ),
          governance: (
            <>
              <section className="rounded-xl border p-4 sm:p-5" aria-labelledby="data-status-heading">
                <h2 id="data-status-heading" className="font-semibold">{tr({ zh: "数据状态", en: "Data status" })}</h2>
                <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                  <div><dt className="text-[var(--muted-foreground)]">{tr({ zh: "最近更新", en: "Last updated" })}</dt><dd className="mt-1">{updatedAt}</dd></div>
                  <div><dt className="text-[var(--muted-foreground)]">{tr({ zh: "重建状态", en: "Rebuild status" })}</dt><dd className="mt-1">{profile.needs_rebuild ? tr({ zh: "需要重建", en: "Needs rebuild" }) : tr({ zh: "无需重建", en: "Current" })}</dd></div>
                  <div><dt className="text-[var(--muted-foreground)]">{tr({ zh: "BKT 参数版本", en: "BKT parameter version" })}</dt><dd className="mt-1 break-words">{canonicalState?.param_version ?? tr({ zh: "证据不足", en: "Insufficient evidence" })}</dd></div>
                  <div><dt className="text-[var(--muted-foreground)]">{tr({ zh: "推断开关", en: "Inference" })}</dt><dd className="mt-1">{profile.inference_enabled ? tr({ zh: "已开启", en: "Enabled" }) : tr({ zh: "已关闭", en: "Disabled" })}</dd></div>
                </dl>
                <p className="mt-4 rounded-md border border-dashed border-[var(--border)] p-3 text-xs text-[var(--muted-foreground)]">{tr({ zh: "学科合并、导出和整科删除目前没有可用的领域操作入口；本页不会用前端假操作伪装成功。", en: "Subject merge, export, and whole-subject deletion do not yet have available domain actions; this page does not simulate success in the browser." })}</p>
              </section>
              <LearningGovernancePanel subjectId={subject.subject_id} />
      <section className="rounded-xl border p-5" aria-labelledby="evidence-governance-heading">
        <h2 id="evidence-governance-heading" className="font-semibold">{tr({ zh: "逐条证据治理", en: "Evidence governance" })}</h2>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">{tr({ zh: "删除后，相关概念和策略会从剩余证据重新计算。", en: "After removal, related concepts and strategies are recalculated from the remaining evidence." })}</p>
        {moduleErrors.evidence ? <ModuleError title={tr({ zh: "证据治理暂不可用", en: "Evidence governance unavailable" })} detail={moduleErrors.evidence} /> : <div className="mt-4 space-y-2">
          {evidence.length ? evidence.map((item) => (
            <article key={item.signal_id} className="flex items-center justify-between gap-3 rounded-md border p-3">
              <div className="min-w-0">
                <h3 className="text-sm font-medium">{item.kind}</h3>
                <p className="truncate text-xs text-[var(--muted-foreground)]">{String(item.payload.value || item.payload.concept || item.payload.strategy || tr({ zh: "学习信号", en: "Learning signal" }))}</p>
              </div>
              <button type="button" disabled={busy} onClick={() => void remove(item.signal_id)} className="inline-flex shrink-0 items-center gap-1 rounded-md border border-red-200 px-2 py-1.5 text-xs text-red-700 disabled:opacity-50">
                <Trash2 className="h-3.5 w-3.5" />{tr({ zh: "删除", en: "Delete" })}
              </button>
            </article>
          )) : <p className="text-sm text-[var(--muted-foreground)]">{tr({ zh: "该学科还没有可治理的事件。", en: "This subject has no manageable events yet." })}</p>}
        </div>}
      </section>
            </>
          ),
        }}
      />
    </main>
  );
}

function CanonicalBktPanel({
  state,
  profile,
  tr,
}: {
  state: LearnerSubjectLearningState | null;
  profile: LearnerProfile;
  tr: Tr;
}) {
  const labels = new Map(profile.concept_signals.map((item) => [item.concept_id, item.label]));

  return (
    <section className="rounded-xl border p-4 sm:p-5" aria-labelledby="canonical-bkt-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--muted-foreground)]">{"CANONICAL BKT"}</p>
          <h2 id="canonical-bkt-heading" className="mt-2 font-semibold">
            {tr({ zh: "知识状态与可判分证据", en: "Knowledge state and graded evidence" })}
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-[var(--muted-foreground)]">
            {tr({ zh: "只读取服务端判分且归因可靠的作答，并始终以定性状态展示学习变化。", en: "Reads only server-graded, reliably attributed answers and always presents learning changes qualitatively." })}
          </p>
        </div>
        {state ? (
          <span className="rounded-full bg-[var(--muted)] px-3 py-1 text-xs text-[var(--muted-foreground)]">
            {state.calibrated
              ? tr({ zh: "已校准", en: "Calibrated" })
              : tr({ zh: "等待校准", en: "Awaiting calibration" })}
          </span>
        ) : null}
      </div>

      {!state ? (
        <p className="mt-4 text-sm text-[var(--muted-foreground)]" data-mastery-state="insufficient_evidence">
          {tr({ zh: "证据不足", en: "Insufficient evidence" })}
        </p>
      ) : state.knowledge.length ? (
        <div className="mt-4 space-y-3">
          <p className="text-xs text-[var(--muted-foreground)]">
            {tr({ zh: `${state.strong_event_count} 条强证据 · 参数 ${state.param_version}`, en: `${state.strong_event_count} strong events · ${state.param_version}` })}
          </p>
          {state.knowledge.map((item) => (
            <article key={item.kc_id} className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/20 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium">{labels.get(item.kc_id) || item.kc_id}</p>
                <MasteryStateValue
                  evidenceState={item.evidence_state}
                  changeSignal={item.change_signal}
                />
              </div>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                {tr({ zh: `${item.verified_observation_count} 次可判分观测`, en: `${item.verified_observation_count} graded observations` })}
              </p>
            </article>
          ))}
        </div>
      ) : (
        <p className="mt-4 text-sm text-[var(--muted-foreground)]" data-mastery-state="insufficient_evidence">
          {tr({ zh: "证据不足", en: "Insufficient evidence" })}
        </p>
      )}
    </section>
  );
}

function SummaryCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-lg bg-[var(--muted)]/35 p-3">
      <p className="text-xs font-medium text-[var(--muted-foreground)]">{title}</p>
      <p className="mt-2 text-sm">{value}</p>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <p className="mt-4 rounded-lg border border-dashed border-[var(--border)] p-4 text-sm text-[var(--muted-foreground)]">{text}</p>;
}

function ModuleError({ title, detail }: { title: string; detail: string }) {
  return (
    <div role="alert" className="mt-4 rounded-lg border border-amber-400/35 bg-amber-400/10 p-3 text-sm text-amber-800 dark:text-amber-200">
      <p className="font-medium">{title}</p>
      <p className="mt-1 break-words text-xs">{detail}</p>
    </div>
  );
}

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof Error ? cause.message : fallback;
}

function formatDateTime(value: string, locale: string): string {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp)
    ? new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(timestamp)
    : value;
}

function reflectionCategoryLabel(category: LearnerReflection["category"], tr: Tr) {
  const labels: Record<LearnerReflection["category"], Copy> = {
    goal: { zh: "目标", en: "Goal" },
    explanation: { zh: "讲解偏好", en: "Explanation" },
    pacing: { zh: "节奏", en: "Pacing" },
    feedback: { zh: "反馈", en: "Feedback" },
    constraint: { zh: "约束", en: "Constraint" },
    concept: { zh: "概念状态", en: "Concept state" },
    strategy: { zh: "教学策略", en: "Teaching strategy" },
  };
  return tr(labels[category]);
}

function reflectionStatusLabel(status: LearnerReflection["status"], tr: Tr) {
  const labels: Record<LearnerReflection["status"], Copy> = {
    candidate: { zh: "候选", en: "Candidate" },
    confirmed: { zh: "已确认", en: "Confirmed" },
    rejected: { zh: "已拒绝", en: "Rejected" },
    stale: { zh: "已过期", en: "Stale" },
    needs_rebuild: { zh: "待重建", en: "Needs rebuild" },
  };
  return tr(labels[status]);
}
