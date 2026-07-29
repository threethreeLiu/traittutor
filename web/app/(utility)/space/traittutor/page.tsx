"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BadgeCheck,
  BrainCircuit,
  ClipboardCheck,
  Gauge,
  Loader2,
  RefreshCw,
  Route,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
  UserRound,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { TraitTutorMark } from "@/components/brand/TraitTutorMark";
import { useOnboarding } from "@/components/onboarding/OnboardingProvider";
import {
  deleteTraitProfile,
  listTraitProfiles,
  type SlrSupport,
  type TraitKey,
  type TraitProfile,
} from "@/lib/traittutor-api";

type Lang = { zh: string; en: string };
type TraitSignal = { key: TraitKey; score: number; label: Lang; barClass: string; position: string };

const TRAIT_DETAILS: Record<TraitKey, Lang> = {
  O: { zh: "开放性", en: "Openness" },
  C: { zh: "尽责性", en: "Conscientiousness" },
  E: { zh: "外向性", en: "Extraversion" },
  A: { zh: "宜人性", en: "Agreeableness" },
  N: { zh: "情绪敏感性", en: "Negative emotionality" },
};

const TRAIT_POSITIONS = [
  "left-1/2 top-4 -translate-x-1/2",
  "right-2 top-[31%]",
  "right-[12%] bottom-5",
  "left-[12%] bottom-5",
  "left-2 top-[31%]",
];

const SLR_KEYS = ["goal_planning", "monitoring_regulation", "reflection_transfer", "motivation_emotion"] as const;
const SLR_POSITIONS = [
  "left-1/2 top-3 -translate-x-1/2",
  "right-2 top-1/2 -translate-y-1/2",
  "left-1/2 bottom-3 -translate-x-1/2",
  "left-2 top-1/2 -translate-y-1/2",
];

export default function LearningProfilePage() {
  const { i18n } = useTranslation();
  const zh = i18n.language?.startsWith("zh");
  const tr = useCallback((value: Lang) => (zh ? value.zh : value.en), [zh]);
  const [profile, setProfile] = useState<TraitProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState("");
  const onboarding = useOnboarding();

  useEffect(() => {
    void listTraitProfiles()
      .then((profiles) => setProfile(profiles[0] ?? null))
      .catch((cause) => setError(String(cause)))
      .finally(() => setLoading(false));
  }, []);

  const persona = useMemo(() => {
    if (!profile) return null;
    if (profile.scores.N >= 8 || profile.scores.C <= 5) {
      return {
        name: tr({ zh: "结构化导师", en: "Structured Tutor" }),
        detail: tr({ zh: "分步讲解与更多检查点", en: "Step-by-step guidance and more checkpoints" }),
      };
    }
    if (profile.scores.O >= 8) {
      return {
        name: tr({ zh: "探索伙伴", en: "Exploration Partner" }),
        detail: tr({ zh: "开放问题与举例探索", en: "Open questions and exploratory examples" }),
      };
    }
    return {
      name: tr({ zh: "学习教练", en: "Learning Coach" }),
      detail: tr({ zh: "清晰讲解与适度练习", en: "Clear explanations and balanced practice" }),
    };
  }, [profile, tr]);

  async function remove() {
    if (!profile || !window.confirm(tr({ zh: "删除这份学习画像？之后需要重新完成测评。", en: "Delete this learning profile? You will need to complete the assessment again." }))) return;
    setRemoving(true);
    setError("");
    try {
      await deleteTraitProfile(profile.profile_id);
      setProfile(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRemoving(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl pb-12">
      <header className="relative overflow-hidden border-b border-[var(--border)] pb-7 pt-2">
        <div className="absolute inset-x-0 bottom-0 h-px bg-cyan-400/50" />
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="flex items-start gap-4">
            <div className="flex h-12 w-12 items-center justify-center border border-cyan-400/30 bg-cyan-400/5 shadow-[0_0_28px_rgba(34,211,238,0.10)]">
              <TraitTutorMark className="h-8 w-8" />
            </div>
            <div>
              <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.2em] text-cyan-600 dark:text-cyan-300">
                <span className="h-1.5 w-1.5 bg-cyan-400" />
                {tr({ zh: "个人学习信号系统", en: "Personal learning signal system" })}
              </div>
              <h1 className="mt-2 text-2xl font-semibold tracking-wide">{tr({ zh: "学习画像", en: "Learning Profile" })}</h1>
              <p className="mt-1.5 max-w-xl text-[13px] leading-6 text-[var(--muted-foreground)]">
                {tr({ zh: "用学习支持信号调整过程，不用于判断能力或固定学习风格。", en: "Signals shape learning support, never ability judgments or fixed learning styles." })}
              </p>
            </div>
          </div>
          {profile ? (
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => onboarding?.openAssessment()} className="inline-flex h-10 items-center gap-2 border border-[var(--border)] px-3.5 text-[13px] transition-colors hover:border-cyan-400/60 hover:bg-cyan-400/5">
                <RefreshCw size={14} />{tr({ zh: "重新测评", en: "Retake" })}
              </button>
              <button type="button" disabled={removing} onClick={() => void remove()} className="inline-flex h-10 items-center gap-2 border border-red-500/40 px-3.5 text-[13px] text-red-500 transition-colors hover:bg-red-500/10 disabled:opacity-50">
                <Trash2 size={14} />{tr({ zh: "删除", en: "Delete" })}
              </button>
            </div>
          ) : null}
        </div>
      </header>

      {error ? <p className="mt-5 border border-red-500/30 bg-red-500/10 px-3 py-2 text-[13px] text-red-600">{error}</p> : null}
      {loading ? (
        <div className="flex min-h-80 items-center justify-center text-[var(--muted-foreground)]"><Loader2 className="h-5 w-5 animate-spin" /></div>
      ) : profile && persona ? (
        <ProfileSignalBoard profile={profile} persona={persona} tr={tr} />
      ) : (
        <section className="mt-7 flex min-h-72 flex-col items-center justify-center border border-dashed border-[var(--border)] px-6 text-center">
          <BrainCircuit className="h-8 w-8 text-cyan-500" />
          <h2 className="mt-4 text-base font-semibold">{tr({ zh: "尚未创建学习画像", en: "No learning profile yet" })}</h2>
          <p className="mt-2 max-w-md text-sm text-[var(--muted-foreground)]">{tr({ zh: "完成大五测评后，这里会展示你的学习支持信号。", en: "Complete the Big Five assessment to see your learning support signals." })}</p>
          <button type="button" onClick={() => onboarding?.openAssessment()} className="mt-5 inline-flex h-10 items-center border border-cyan-400/50 bg-cyan-400/10 px-4 text-sm font-medium text-cyan-700 hover:bg-cyan-400/15 dark:text-cyan-200">{tr({ zh: "开始测评", en: "Start assessment" })}</button>
        </section>
      )}
    </div>
  );
}

function ProfileSignalBoard({ profile, persona, tr }: { profile: TraitProfile; persona: { name: string; detail: string }; tr: (value: Lang) => string }) {
  const signals: TraitSignal[] = (["O", "C", "E", "A", "N"] as TraitKey[]).map((key, index) => ({
    key,
    score: profile.scores[key],
    label: TRAIT_DETAILS[key],
    barClass: ["bg-cyan-400", "bg-sky-400", "bg-violet-400", "bg-emerald-400", "bg-amber-400"][index],
    position: TRAIT_POSITIONS[index],
  }));
  const slrSupport = profile.metadata?.slr_support;

  return (
    <div className="mt-7 space-y-5">
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_295px]">
        <section className="relative overflow-hidden border border-[var(--border)] bg-[var(--card)]/45 p-5 shadow-[0_20px_55px_rgba(0,0,0,0.08)]">
          <GridTexture />
          <div className="relative flex items-center justify-between gap-3">
            <PanelKicker icon={Gauge} label={tr({ zh: "Big Five · 信号阵列", en: "Big Five · Signal array" })} />
            <span className="font-mono text-[10px] tracking-[0.16em] text-[var(--muted-foreground)]">BFI-10 / TIPI</span>
          </div>
          <div className="relative mx-auto mt-1 h-[395px] max-w-[620px]">
            <svg aria-hidden viewBox="0 0 620 395" preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible text-cyan-400/45">
              <polygon points="310,73 510,202 435,355 185,355 110,202" fill="rgba(34,211,238,0.035)" stroke="currentColor" strokeWidth="1" />
              <polygon points="310,121 465,220 405,322 215,322 155,220" fill="none" stroke="currentColor" strokeOpacity="0.4" strokeWidth="1" strokeDasharray="3 7" />
              {signals.map((signal, index) => {
                const targets = [[310, 73], [510, 202], [435, 355], [185, 355], [110, 202]];
                return <line key={signal.key} x1="310" y1="210" x2={targets[index][0]} y2={targets[index][1]} stroke="currentColor" strokeOpacity="0.45" />;
              })}
            </svg>
            <div className="absolute left-1/2 top-[53%] flex h-32 w-32 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center border border-cyan-400/45 bg-[var(--card)]/90 text-center shadow-[0_0_42px_rgba(34,211,238,0.12)] backdrop-blur">
              <BrainCircuit size={28} className="text-cyan-500" />
              <span className="mt-2 text-[13px] font-semibold">{tr({ zh: "学习信号", en: "Learning signals" })}</span>
              <span className="mt-1 font-mono text-[9px] uppercase tracking-[0.16em] text-cyan-600 dark:text-cyan-300">Active profile</span>
            </div>
            {signals.map((signal) => <TraitSignalNode key={signal.key} signal={signal} tr={tr} />)}
          </div>
          <div className="relative border-t border-[var(--border)] pt-4 text-[13px] leading-6 text-[var(--muted-foreground)]">{profile.summary}</div>
        </section>

        <aside className="space-y-3">
          <StatusReadout icon={UserRound} label={tr({ zh: "当前学习角色", en: "Current learning role" })} value={persona.name} detail={persona.detail} tone="cyan" />
          <StatusReadout icon={Workflow} label={tr({ zh: "教学响应", en: "Teaching response" })} value={tr({ zh: "个性化支持已启用", en: "Personalized support active" })} detail={tr({ zh: "课件、卡片与测验将动态调整支架、节奏与检查点。", en: "Courseware, cards, and quizzes adjust scaffolding, pace, and checkpoints." })} tone="violet" />
          <StatusReadout icon={ShieldCheck} label={tr({ zh: "使用边界", en: "Use boundary" })} value={tr({ zh: "仅作用于教学策略", en: "Teaching strategy only" })} detail={tr({ zh: "不会作为能力判断、心理诊断或学习风格标签。", en: "Never used as an ability judgment, diagnosis, or learning-style label." })} tone="amber" />
        </aside>
      </div>
      {slrSupport ? <SlrSupportNetwork support={slrSupport} tr={tr} /> : null}
    </div>
  );
}

function TraitSignalNode({ signal, tr }: { signal: TraitSignal; tr: (value: Lang) => string }) {
  const scorePercent = `${Math.min(100, Math.max(0, (signal.score / 10) * 100))}%`;
  return (
    <div className={`absolute w-[135px] border border-[var(--border)] bg-[var(--card)]/90 p-3 backdrop-blur ${signal.position}`}>
      <div className="flex items-center justify-between"><span className="font-mono text-[11px] text-[var(--muted-foreground)]">{signal.key}</span><span className="font-mono text-[10px] text-[var(--muted-foreground)]">{signal.score}/10</span></div>
      <p className="mt-1 text-[12px] font-medium">{tr(signal.label)}</p>
      <div className="mt-3 h-px bg-[var(--border)]"><div className={`h-full ${signal.barClass}`} style={{ width: scorePercent }} /></div>
    </div>
  );
}

function SlrSupportNetwork({ support, tr }: { support: SlrSupport; tr: (value: Lang) => string }) {
  return (
    <section className="relative overflow-hidden border border-[var(--border)] bg-[var(--card)]/45 p-5 shadow-[0_20px_55px_rgba(0,0,0,0.06)]">
      <GridTexture />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div>
          <PanelKicker icon={Target} label={tr({ zh: "SLR · 学习支持网络", en: "SLR · Learning support network" })} />
          <p className="mt-2 text-[13px] text-[var(--muted-foreground)]">{tr({ zh: "由当前大五画像生成的初始支持路径；真实学习活动会持续补充信号。", en: "Initial support paths generated from the current Big Five profile; real learning activity will add signals over time." })}</p>
        </div>
        <span className="inline-flex items-center gap-1.5 border border-cyan-400/30 bg-cyan-400/5 px-2.5 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-cyan-700 dark:text-cyan-200"><Sparkles size={12} />{tr({ zh: "初始支持计划", en: "Initial support plan" })}</span>
      </div>
      <div className="relative mx-auto mt-5 h-[392px] max-w-[800px]">
        <svg aria-hidden viewBox="0 0 800 392" preserveAspectRatio="none" className="absolute inset-0 h-full w-full text-cyan-400/40">
          <line x1="400" y1="196" x2="400" y2="64" stroke="currentColor" /><line x1="400" y1="196" x2="656" y2="196" stroke="currentColor" /><line x1="400" y1="196" x2="400" y2="328" stroke="currentColor" /><line x1="400" y1="196" x2="144" y2="196" stroke="currentColor" />
          <circle cx="400" cy="196" r="104" fill="none" stroke="currentColor" strokeDasharray="3 9" strokeOpacity="0.45" />
        </svg>
        <div className="absolute left-1/2 top-1/2 flex h-32 w-32 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center border border-cyan-400/45 bg-[var(--card)]/90 text-center shadow-[0_0_42px_rgba(34,211,238,0.10)] backdrop-blur"><ClipboardCheck size={27} className="text-cyan-500" /><span className="mt-2 text-[13px] font-semibold">SLR</span><span className="mt-1 font-mono text-[9px] uppercase tracking-[0.16em] text-cyan-600 dark:text-cyan-300">Support loop</span></div>
        {SLR_KEYS.map((key, index) => <SlrNode key={key} item={support.dimensions[key]} position={SLR_POSITIONS[index]} tr={tr} />)}
      </div>
      <div className="relative grid gap-x-8 gap-y-3 border-t border-[var(--border)] pt-4 sm:grid-cols-2">
        {SLR_KEYS.map((key) => {
          const item = support.dimensions[key];
          return <div key={key} className="flex gap-3 text-[12px]"><BadgeCheck size={15} className="mt-0.5 shrink-0 text-cyan-500" /><p><span className="font-medium">{item.label}</span><span className="text-[var(--muted-foreground)]"> · {item.actions.join("；")}</span></p></div>;
        })}
      </div>
      <p className="relative mt-5 text-[11px] leading-5 text-[var(--muted-foreground)]">{support.boundary}</p>
    </section>
  );
}

function SlrNode({ item, position, tr }: { item: SlrSupport["dimensions"][keyof SlrSupport["dimensions"]]; position: string; tr: (value: Lang) => string }) {
  return <div className={`absolute w-[192px] border border-[var(--border)] bg-[var(--card)]/90 p-3.5 backdrop-blur ${position}`}><div className="flex items-center justify-between gap-2"><p className="text-[12px] font-semibold">{item.label}</p><span className="font-mono text-[9px] tracking-[0.08em] text-cyan-600 dark:text-cyan-300">{item.emphasis === "strong" ? tr({ zh: "重点", en: "FOCUS" }) : tr({ zh: "持续", en: "ONGOING" })}</span></div><p className="mt-2 text-[11px] leading-5 text-[var(--muted-foreground)]">{item.detail}</p><div className="mt-3 flex items-center gap-1.5 border-t border-[var(--border)] pt-2 font-mono text-[10px] text-[var(--muted-foreground)]"><Route size={12} />{tr({ zh: `${item.evidence_count} 条学习证据`, en: `${item.evidence_count} learning signals` })}</div></div>;
}

function StatusReadout({ icon: Icon, label, value, detail, tone }: { icon: LucideIcon; label: string; value: string; detail: string; tone: "cyan" | "violet" | "amber" }) {
  const colors = { cyan: "border-cyan-400/30 text-cyan-600 dark:text-cyan-300", violet: "border-violet-400/30 text-violet-600 dark:text-violet-300", amber: "border-amber-400/30 text-amber-600 dark:text-amber-300" };
  return <section className="border border-[var(--border)] bg-[var(--card)]/45 p-4"><div className={`flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.12em] ${colors[tone]}`}><Icon size={14} />{label}</div><p className="mt-4 text-[15px] font-semibold">{value}</p><p className="mt-2 text-[12px] leading-5 text-[var(--muted-foreground)]">{detail}</p></section>;
}

function PanelKicker({ icon: Icon, label }: { icon: LucideIcon; label: string }) {
  return <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.16em] text-cyan-700 dark:text-cyan-200"><Icon size={14} />{label}</div>;
}

function GridTexture() {
  return <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.12]" style={{ backgroundImage: "linear-gradient(to right, currentColor 1px, transparent 1px), linear-gradient(to bottom, currentColor 1px, transparent 1px)", backgroundSize: "32px 32px" }} />;
}
