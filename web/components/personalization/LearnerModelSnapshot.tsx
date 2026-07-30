"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BookOpenCheck, BrainCircuit, Database, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  getLearnerEvidence,
  getLearnerOverview,
  type LearnerEvidence,
  type LearnerOverview,
  type LearnerProfile,
} from "@/lib/learner-model-api";

type Copy = { zh: string; en: string };

const statusCopy: Record<string, Copy> = {
  starting: { zh: "刚开始", en: "Getting started" },
  learning: { zh: "学习中", en: "Learning" },
  familiar: { zh: "较熟悉", en: "Familiar" },
  verified: { zh: "已验证掌握", en: "Verified mastery" },
};

/**
 * A compact, user-facing view of the learner model. It intentionally exposes
 * observed learning state and source counts rather than BKT tuning parameters
 * or any inferred ability labels.
 */
export default function LearnerModelSnapshot() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.startsWith("zh");
  const tr = useCallback((copy: Copy) => (zh ? copy.zh : copy.en), [zh]);
  const [overview, setOverview] = useState<LearnerOverview | null>(null);
  const [evidence, setEvidence] = useState<LearnerEvidence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const [overviewResult, evidenceResult] = await Promise.allSettled([
      getLearnerOverview(),
      getLearnerEvidence(),
    ]);

    if (overviewResult.status === "fulfilled") {
      setOverview(overviewResult.value);
      setError("");
    } else {
      setError(
        overviewResult.reason instanceof Error
          ? overviewResult.reason.message
          : tr({ zh: "学习者模型暂时不可用", en: "Learner model is temporarily unavailable" }),
      );
    }
    if (evidenceResult.status === "fulfilled") setEvidence(evidenceResult.value.evidence);
    setLoading(false);
  }, [tr]);

  useEffect(() => {
    void Promise.resolve().then(load);
  }, [load]);

  const memoryEvidence = evidence.filter((item) => item.kind.startsWith("memory_"));
  const sourceReferences = new Set(memoryEvidence.flatMap((item) => item.evidence_refs)).size;
  const bktSubjects = overview?.subjects.filter((profile) => profile.concept_signals.length > 0) ?? [];

  return (
    <section className="mt-14 border-t border-[var(--border)] pt-8" aria-labelledby="learner-model-heading">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-[11.5px] font-medium uppercase tracking-[0.18em] text-[var(--muted-foreground)]">
            {tr({ zh: "持续学习模型", en: "Continuous learner model" })}
          </p>
          <h2 id="learner-model-heading" className="mt-2 font-serif text-[22px] font-semibold">
            {tr({ zh: "学习记忆与知识进度", en: "Learning memory and knowledge progress" })}
          </h2>
          <p className="mt-2 max-w-2xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {tr({
              zh: "这里汇总你已授权保留的学习证据与知识追踪状态。它会影响后续讲解、练习和复习建议，但不用于能力评定或人格诊断。",
              en: "This view summarizes your retained learning evidence and knowledge-tracking state. It informs future teaching and review, never ability ratings or diagnosis.",
            })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3 text-[13px] transition-colors hover:border-[var(--primary)]/40"
            aria-label={tr({ zh: "刷新学习模型", en: "Refresh learner model" })}
          >
            <RefreshCw className={loading ? "h-3.5 w-3.5 animate-spin" : "h-3.5 w-3.5"} />
            {tr({ zh: "刷新", en: "Refresh" })}
          </button>
          <Link href="/profile/learning-model" className="inline-flex h-9 items-center rounded-md bg-[var(--primary)] px-3 text-[13px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90">
            {tr({ zh: "查看完整模型", en: "Open full model" })}
          </Link>
        </div>
      </div>

      {error ? <p role="status" className="mt-5 rounded-md border border-[var(--destructive)]/30 bg-[var(--destructive)]/10 px-3 py-2 text-[13px] text-[var(--destructive)]">{error}</p> : null}

      <div className="mt-6 grid gap-5 lg:grid-cols-2">
        <MemoryEvidenceCard
          memoryEvidence={memoryEvidence}
          sourceReferences={sourceReferences}
          reconcileState={overview?.memory_reconcile?.state}
          lastCompletedAt={overview?.memory_reconcile?.last_completed_at}
          loading={loading}
          tr={tr}
        />
        <KnowledgeTracingCard subjects={bktSubjects} loading={loading} tr={tr} />
      </div>
    </section>
  );
}

function MemoryEvidenceCard({
  memoryEvidence,
  sourceReferences,
  reconcileState,
  lastCompletedAt,
  loading,
  tr,
}: {
  memoryEvidence: LearnerEvidence[];
  sourceReferences: number;
  reconcileState?: string;
  lastCompletedAt?: string | null;
  loading: boolean;
  tr: (copy: Copy) => string;
}) {
  const syncing = reconcileState === "queued" || reconcileState === "running";
  return (
    <article className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5">
      <div className="flex items-start gap-3"><span className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--primary)]/10 text-[var(--primary)]"><Database className="h-4.5 w-4.5" /></span><div><h3 className="font-medium">{tr({ zh: "学习记忆证据", en: "Learning-memory evidence" })}</h3><p className="mt-1 text-[12px] leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "仅展示从旧记忆中提取、且可回到原始记录核验的学习相关信号。", en: "Only learning signals that can be traced back to the original memory are included." })}</p></div></div>
      <dl className="mt-5 grid grid-cols-2 gap-3"><Metric label={tr({ zh: "已归纳信号", en: "Imported signals" })} value={loading ? "—" : String(memoryEvidence.length)} /><Metric label={tr({ zh: "原始来源", en: "Source references" })} value={loading ? "—" : String(sourceReferences)} /></dl>
      <p className="mt-4 text-[12px] text-[var(--muted-foreground)]">
        {syncing
          ? tr({ zh: "正在从旧记忆同步证据…", en: "Synchronizing evidence from memory…" })
          : lastCompletedAt
            ? tr({ zh: "旧记忆已完成同步，可在完整模型中逐条治理。", en: "Memory synchronization is complete; manage individual evidence in the full model." })
            : tr({ zh: "尚未同步旧记忆；新的明确偏好和学习反馈仍会即时写入。", en: "Memory has not been synchronized yet; new explicit preferences and feedback still apply immediately." })}
      </p>
    </article>
  );
}

function KnowledgeTracingCard({ subjects, loading, tr }: { subjects: LearnerProfile[]; loading: boolean; tr: (copy: Copy) => string }) {
  return <article className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-5"><div className="flex items-start gap-3"><span className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--primary)]/10 text-[var(--primary)]"><BrainCircuit className="h-4.5 w-4.5" /></span><div><h3 className="font-medium">{tr({ zh: "BKT 知识追踪", en: "BKT knowledge tracking" })}</h3><p className="mt-1 text-[12px] leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "基于可判分作答与复习记录估计各概念的当前学习状态，不等同于能力分数。", en: "Uses graded answers and review records to estimate each concept’s current learning state, not an ability score." })}</p></div></div>{loading ? <p className="mt-5 text-[13px] text-[var(--muted-foreground)]">{tr({ zh: "正在读取知识状态…", en: "Loading knowledge state…" })}</p> : subjects.length ? <div className="mt-5 space-y-3">{subjects.map((profile) => <BktSubjectRow key={profile.subject?.subject_id} profile={profile} tr={tr} />)}</div> : <div className="mt-5 rounded-md bg-[var(--muted)]/40 p-3 text-[13px] text-[var(--muted-foreground)]">{tr({ zh: "完成 Quiz、掌握路径或闪卡复习后，这里会展示有证据支撑的概念状态和待复习项。", en: "After a quiz, mastery activity, or flashcard review, evidence-backed concept states and reviews due will appear here." })}</div>}</article>;
}

function BktSubjectRow({ profile, tr }: { profile: LearnerProfile; tr: (copy: Copy) => string }) {
  const subject = profile.subject;
  const understanding = profile.understanding;
  if (!subject || !understanding) return null;
  const observations = profile.concept_signals.reduce((sum, concept) => sum + (concept.verified_observation_count ?? 0), 0);
  const status = statusCopy[understanding.status] ?? statusCopy.starting;
  return <Link href={`/profile/learning-model/${encodeURIComponent(subject.subject_id)}`} className="block rounded-md border border-[var(--border)] px-3 py-3 transition-colors hover:border-[var(--primary)]/45"><div className="flex items-center justify-between gap-3"><div className="min-w-0"><p className="truncate text-[13px] font-medium">{subject.label}</p><p className="mt-0.5 text-[11.5px] text-[var(--muted-foreground)]">{tr(status)} · {tr({ zh: `${understanding.observed_concept_count}/${understanding.concept_count} 个概念有练习证据`, en: `${understanding.observed_concept_count}/${understanding.concept_count} concepts observed` })}</p></div><span className="shrink-0 text-[12px] text-[var(--muted-foreground)]">{tr({ zh: `待复习 ${understanding.review_load}`, en: `${understanding.review_load} due` })}</span></div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--muted)]" aria-label={tr({ zh: `已验证理解 ${Math.round(understanding.verified_mastery * 100)}%`, en: `Verified understanding ${Math.round(understanding.verified_mastery * 100)}%` })}><div className="h-full rounded-full bg-[var(--primary)]" style={{ width: `${Math.round(understanding.verified_mastery * 100)}%` }} /></div><p className="mt-2 text-[11.5px] text-[var(--muted-foreground)]">{tr({ zh: `${observations} 次可判分观测`, en: `${observations} verified observations` })}</p></Link>;
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-md bg-[var(--muted)]/40 px-3 py-2.5"><dt className="text-[11.5px] text-[var(--muted-foreground)]">{label}</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd></div>; }
