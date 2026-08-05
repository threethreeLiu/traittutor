"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, BookOpen, Clock3, FileText, Layers3, Loader2, Plus, Route, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { listLearningPacks, type LearningPack } from "@/lib/traittutor-api";

export default function LearningPlansHome() {
  const { i18n } = useTranslation();
  const [packs, setPacks] = useState<LearningPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const zh = i18n.language.toLowerCase().startsWith("zh");
  useEffect(() => { void listLearningPacks().then(setPacks).catch(() => setError(true)).finally(() => setLoading(false)); }, []);
  const active = packs.filter((pack) => pack.goal?.status !== "completed");
  const reviewCount = useMemo(() => packs.reduce((total, pack) => total + Object.values(pack.flashcard_progress ?? {}).filter((state) => state !== "mastered" && state !== "known").length, 0), [packs]);
  const artifactCount = useMemo(() => packs.reduce((total, pack) => total + Object.values(pack.artifacts ?? {}).reduce((sum, values) => sum + values.length, 0), 0), [packs]);

  return <main className="learning-canvas min-h-screen px-4 py-8 md:px-8 md:py-12">
    <div className="mx-auto max-w-6xl">
      <header className="flex flex-wrap items-end justify-between gap-5 border-b border-[var(--border)] pb-8">
        <div><p className="learning-eyebrow">TraitTutor · Learning paths</p><h1 className="mt-3 font-serif text-4xl font-semibold">{zh ? "我的学习" : "My learning"}</h1><p className="learning-copy-muted mt-3 max-w-2xl text-sm leading-6">{zh ? "目标、材料、组件和学习证据都保留在同一个学习包中。" : "Goals, sources, components, and learning evidence stay together in one learning pack."}</p></div>
        <Link href="/home" className="learning-button learning-button--primary px-4 py-3 text-sm"><Plus size={16} />{zh ? "新建学习目标" : "New learning goal"}</Link>
      </header>

      <section className="mt-8 grid gap-3 sm:grid-cols-3">
        <Metric icon={Route} label={zh ? "进行中" : "In progress"} value={active.length} emphasis="primary" />
        <Metric icon={Clock3} label={zh ? "待复习" : "Due for review"} value={reviewCount} emphasis="muted" />
        <Metric icon={Layers3} label={zh ? "历史产物" : "Learning artifacts"} value={artifactCount} emphasis="muted" />
      </section>

      <section className="mt-10">
        <div className="flex items-center justify-between"><h2 className="font-serif text-2xl">{zh ? "进行中的路径" : "Active paths"}</h2><span className="learning-meta">Evidence-adaptive</span></div>
        {loading ? <div className="learning-copy-muted mt-6 flex items-center gap-2 text-sm"><Loader2 size={16} className="animate-spin" />{zh ? "正在读取学习路径" : "Loading paths"}</div> : null}
        {error ? <p className="learning-alert--error mt-6 p-4 text-sm">{zh ? "学习路径暂不可用，请稍后刷新。" : "Learning paths are temporarily unavailable."}</p> : null}
        {!loading && !error && !active.length ? <div className="learning-card learning-card--large mt-6 border-dashed p-10 text-center"><Sparkles className="learning-accent mx-auto" /><p className="mt-4 font-serif text-xl">{zh ? "还没有进行中的学习目标" : "No active learning goal yet"}</p><Link href="/home" className="learning-accent mt-4 inline-flex items-center gap-2 text-sm">{zh ? "告诉 TraitTutor 你想学什么" : "Tell TraitTutor what you want to learn"}<ArrowRight size={14} /></Link></div> : null}
        <div className="mt-5 grid gap-4 md:grid-cols-2">{active.map((pack) => { const plan = pack.component_plans?.find((item) => item.plan_id === pack.active_plan_id); const done = plan?.components.filter((item) => item.status === "completed").length ?? 0; const total = plan?.components.length ?? 0; return <Link key={pack.pack_id} href={`/space/learning/${pack.pack_id}`} className="learning-card learning-card--large group transition hover:-translate-y-0.5 hover:border-[var(--primary)]"><div className="flex items-start justify-between gap-4"><span className="learning-icon-badge"><Route size={17} /></span><ArrowRight size={16} className="learning-copy-muted transition group-hover:translate-x-1 group-hover:text-[var(--primary)]" /></div><h3 className="mt-6 font-serif text-xl">{pack.goal?.text ?? pack.title}</h3><p className="learning-copy-muted mt-2 text-xs">{String(plan?.subject_ref?.label ?? (zh ? "等待学科确认" : "Awaiting subject confirmation"))}</p><div className="mt-6 h-1 overflow-hidden rounded-full bg-[var(--muted)]"><div className="h-full bg-[var(--primary)]" style={{ width: total ? `${Math.round((done / total) * 100)}%` : "0%" }} /></div><div className="learning-meta mt-2 flex justify-between"><span>{done}/{total} {zh ? "组件" : "components"}</span><span>{plan ? `V${plan.version}` : "—"}</span></div></Link>; })}</div>
      </section>

      <section className="mt-12 grid gap-4 md:grid-cols-2">
        <ArchivePanel icon={FileText} title={zh ? "学习材料" : "Learning sources"} note={zh ? "原文件、分析快照、概念和页码证据" : "Original files, analysis snapshots, concepts, and page evidence"} count={packs.filter((pack) => Object.keys(pack.material ?? {}).length > 0).length} />
        <ArchivePanel icon={BookOpen} title={zh ? "历史产物" : "Artifact history"} note={zh ? "课件、闪卡、Quiz、图解和语音仍可回看与导出" : "Lessons, flashcards, quizzes, diagrams, and audio remain available"} count={artifactCount} />
      </section>
    </div>
  </main>;
}

function Metric({ icon: Icon, label, value, emphasis }: { icon: typeof Route; label: string; value: number; emphasis: "primary" | "muted" }) { const color = emphasis === "primary" ? "learning-accent" : "learning-copy-muted"; return <div className="learning-card"><Icon size={16} className={color} /><p className="mt-5 font-serif text-3xl">{value}</p><p className="learning-copy-muted mt-1 text-xs">{label}</p></div>; }
function ArchivePanel({ icon: Icon, title, note, count }: { icon: typeof FileText; title: string; note: string; count: number }) { return <div className="learning-card"><div className="flex items-center justify-between"><Icon size={17} className="learning-copy-muted" /><span className="learning-meta text-xs">{count}</span></div><h3 className="mt-5 font-serif text-lg">{title}</h3><p className="learning-copy-muted mt-2 text-xs leading-5">{note}</p></div>; }
