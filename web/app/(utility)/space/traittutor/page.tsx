"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { BrainCircuit, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { TraitTutorIcon } from "@/components/brand/TraitTutorIcon";
import LearnerModelSnapshot from "@/components/personalization/LearnerModelSnapshot";
import { useOnboarding } from "@/components/onboarding/OnboardingProvider";
import {
  deleteTraitProfile,
  listTraitProfiles,
  type SlrSupport,
  type TraitKey,
  type TraitProfile,
} from "@/lib/traittutor-api";

/**
 * Learning Profile — presented as an assessment report, not a dashboard.
 *
 * The page reads like a printed evaluation: a thin-line radar figure with a
 * figure caption, then report blocks separated by hairlines. The single
 * accent is the theme primary (terracotta on Cream) — no per-trait colours,
 * no HUD chrome. The visual claim matches the product claim: these are
 * teaching cues, quietly presented, never a diagnosis.
 */

type Lang = { zh: string; en: string };

const TRAIT_DETAILS: Record<TraitKey, Lang> = {
  O: { zh: "开放性", en: "Openness" },
  C: { zh: "尽责性", en: "Conscientiousness" },
  E: { zh: "外向性", en: "Extraversion" },
  A: { zh: "宜人性", en: "Agreeableness" },
  N: { zh: "情绪敏感性", en: "Negative emotionality" },
};

const TRAIT_ORDER: TraitKey[] = ["O", "C", "E", "A", "N"];
const SLR_KEYS = ["goal_planning", "monitoring_regulation", "reflection_transfer", "motivation_emotion"] as const;

// Pentagon geometry — centre (240,190), radius 150, first vertex at top.
const RADAR_CENTER = { x: 240, y: 190 };
const RADAR_RADIUS = 150;
const RADAR_ANGLES = [-90, -18, 54, 126, 198].map((d) => (d * Math.PI) / 180);

function radarPoint(index: number, scale: number): [number, number] {
  return [
    RADAR_CENTER.x + RADAR_RADIUS * scale * Math.cos(RADAR_ANGLES[index]),
    RADAR_CENTER.y + RADAR_RADIUS * scale * Math.sin(RADAR_ANGLES[index]),
  ];
}

function polygonPoints(scale: number): string {
  return TRAIT_ORDER.map((_, i) => radarPoint(i, scale).join(",")).join(" ");
}

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
    <div className="mx-auto max-w-5xl pb-16">
      <header className="border-b border-[var(--border)] pb-7 pt-2">
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div>
            <div className="flex items-center gap-2.5"><TraitTutorIcon name="personality" size={25} strokeWidth={1.65} /><h1 className="font-serif text-[26px] font-semibold tracking-tight text-[var(--foreground)]">
              {tr({ zh: "学习画像", en: "Learning Profile" })}
            </h1></div>
            <p className="mt-2 max-w-xl text-[13px] leading-relaxed text-[var(--muted-foreground)]">
              {tr({ zh: "用学习支持信号调整过程，不用于判断能力或固定学习风格。", en: "Signals shape learning support, never ability judgments or fixed learning styles." })}
            </p>
          </div>
          {profile ? (
            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={() => onboarding?.openAssessment()}
                className="inline-flex h-9 items-center gap-2 rounded-md border border-[var(--border)] px-3.5 text-[13px] text-[var(--foreground)] transition-colors hover:border-[var(--primary)]/40 hover:text-[var(--primary)]"
              >
                <RefreshCw size={14} />
                {tr({ zh: "重新测评", en: "Retake" })}
              </button>
              <button
                type="button"
                disabled={removing}
                onClick={() => void remove()}
                className="inline-flex h-9 items-center gap-2 rounded-md px-3 text-[13px] text-[var(--muted-foreground)] transition-colors hover:text-[var(--destructive)] disabled:opacity-50"
              >
                <Trash2 size={14} />
                {tr({ zh: "删除", en: "Delete" })}
              </button>
            </div>
          ) : null}
        </div>
      </header>

      {error ? (
        <p className="mt-5 rounded-md border border-[var(--destructive)]/30 bg-[var(--destructive)]/10 px-3 py-2 text-[13px] text-[var(--destructive)]">
          {error}
        </p>
      ) : null}

      {loading ? (
        <div className="flex min-h-80 items-center justify-center text-[var(--muted-foreground)]">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      ) : profile && persona ? (
        <ProfileReport profile={profile} persona={persona} tr={tr} />
      ) : (
        <section className="mt-10 flex min-h-72 flex-col items-center justify-center text-center">
          <BrainCircuit className="h-7 w-7 text-[var(--primary)]" strokeWidth={1.6} />
          <h2 className="mt-4 font-serif text-[17px] font-semibold">
            {tr({ zh: "尚未创建学习画像", en: "No learning profile yet" })}
          </h2>
          <p className="mt-2 max-w-md text-[13px] leading-relaxed text-[var(--muted-foreground)]">
            {tr({ zh: "完成大五测评后，这里会展示你的学习支持信号。", en: "Complete the Big Five assessment to see your learning support signals." })}
          </p>
          <button
            type="button"
            onClick={() => onboarding?.openAssessment()}
            className="mt-6 inline-flex h-9 items-center rounded-md bg-[var(--primary)] px-4 text-[13px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90"
          >
            {tr({ zh: "开始测评", en: "Start assessment" })}
          </button>
        </section>
      )}

      <LearnerModelSnapshot />
    </div>
  );
}

function ProfileReport({
  profile,
  persona,
  tr,
}: {
  profile: TraitProfile;
  persona: { name: string; detail: string };
  tr: (value: Lang) => string;
}) {
  const slrSupport = profile.metadata?.slr_support;
  const dataPolygon = TRAIT_ORDER.map((key, i) =>
    radarPoint(i, Math.min(1, Math.max(0, profile.scores[key] / 10))).join(","),
  ).join(" ");

  return (
    <div className="mt-10">
      <div className="grid gap-14 lg:grid-cols-[minmax(0,460px)_1fr]">
        <figure>
          <svg viewBox="-30 0 540 380" className="w-full" role="img"
            aria-label={tr({ zh: "大五画像雷达图", en: "Big Five radar chart" })}>
            <g stroke="var(--border)" fill="none" strokeWidth="1">
              <polygon points={polygonPoints(1)} />
              <polygon points={polygonPoints(0.5)} strokeDasharray="2 6" />
              {TRAIT_ORDER.map((_, i) => {
                const [x, y] = radarPoint(i, 1);
                return <line key={i} x1={RADAR_CENTER.x} y1={RADAR_CENTER.y} x2={x} y2={y} />;
              })}
            </g>
            <polygon
              points={dataPolygon}
              fill="var(--primary)"
              fillOpacity="0.08"
              stroke="var(--primary)"
              strokeWidth="1.5"
            />
            {TRAIT_ORDER.map((key, i) => {
              const [x, y] = radarPoint(i, Math.min(1, Math.max(0, profile.scores[key] / 10)));
              return <circle key={key} cx={x} cy={y} r="3" fill="var(--primary)" />;
            })}
            {TRAIT_ORDER.map((key, i) => {
              const [x, y] = radarPoint(i, 1.16);
              const anchor = i === 0 ? "middle" : i === 1 || i === 2 ? "start" : i === 3 ? "middle" : "end";
              return (
                <text
                  key={key}
                  x={x}
                  y={y}
                  textAnchor={anchor}
                  className="fill-[var(--foreground)]"
                  fontSize="12.5"
                  fontFamily="Georgia, 'Songti SC', serif"
                >
                  {tr(TRAIT_DETAILS[key])} · {profile.scores[key]}
                </text>
              );
            })}
          </svg>
          <figcaption className="mt-4 text-center text-[12px] tracking-wide text-[var(--muted-foreground)]">
            {tr({ zh: "图 1 · BFI-10 五维信号", en: "Fig. 1 · BFI-10 signals" })}
          </figcaption>
        </figure>

        <div>
          <ReportBlock label={tr({ zh: "当前学习角色", en: "Current learning role" })}>
            <p className="font-serif text-[19px] font-semibold text-[var(--primary)]">{persona.name}</p>
            <p className="mt-1.5 text-[13px] text-[var(--muted-foreground)]">{persona.detail}</p>
          </ReportBlock>
          <ReportBlock label={tr({ zh: "教学响应", en: "Teaching response" })}>
            <p className="text-[13px] leading-relaxed text-[var(--muted-foreground)]">
              {tr({ zh: "课件、卡片与测验将动态调整支架、节奏与检查点。", en: "Courseware, cards, and quizzes adjust scaffolding, pace, and checkpoints." })}
            </p>
          </ReportBlock>

          <table className="mt-7 w-full border-collapse">
            <tbody>
              {TRAIT_ORDER.map((key) => {
                const score = profile.scores[key];
                const pct = Math.min(100, Math.max(0, score * 10));
                return (
                  <tr key={key} className="border-t border-[var(--border)] last:border-b">
                    <td className="w-28 py-2.5 text-[13px] text-[var(--foreground)]">{tr(TRAIT_DETAILS[key])}</td>
                    <td className="py-2.5 pr-4">
                      <div className="h-[2px] w-full bg-[var(--border)]/70">
                        <div className="h-full bg-[var(--primary)]" style={{ width: `${pct}%` }} />
                      </div>
                    </td>
                    <td className="w-16 py-2.5 text-right text-[13px] tabular-nums text-[var(--muted-foreground)]">
                      {score} / 10
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          <p className="mt-8 border-t border-[var(--border)] pt-5 text-[13px] leading-[1.9] text-[var(--muted-foreground)]">
            {profile.summary}
          </p>
        </div>
      </div>

      {slrSupport ? <SlrSupportSection support={slrSupport} tr={tr} /> : null}
    </div>
  );
}

function ReportBlock({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="border-t border-[var(--border)] py-5 first:border-t-0 first:pt-0">
      <h3 className="text-[11.5px] font-medium uppercase tracking-[0.18em] text-[var(--muted-foreground)]">
        {label}
      </h3>
      <div className="mt-2.5">{children}</div>
    </section>
  );
}

function SlrSupportSection({ support, tr }: { support: SlrSupport; tr: (value: Lang) => string }) {
  return (
    <section className="mt-14">
      <h2 className="flex items-center gap-2 pb-2.5 text-[11.5px] font-medium uppercase tracking-[0.2em] text-[var(--muted-foreground)]">
        <TraitTutorIcon name="srl" size={17} />{tr({ zh: "SLR · 学习支持网络", en: "SLR · Learning support network" })}
      </h2>
      <p className="pb-4 text-[13px] text-[var(--muted-foreground)]">
        {tr({ zh: "由当前大五画像生成的初始支持路径；真实学习活动会持续补充信号。", en: "Initial support paths generated from the current Big Five profile; real learning activity will add signals over time." })}
      </p>
      <div>
        {SLR_KEYS.map((key) => {
          const item = support.dimensions[key];
          return (
            <div key={key} className="flex flex-wrap items-baseline gap-x-5 gap-y-1 border-t border-[var(--border)] py-3.5 last:border-b">
              <span className="w-36 shrink-0 text-[14px] font-medium text-[var(--foreground)]">{item.label}</span>
              <span className="min-w-0 flex-1 text-[13px] leading-relaxed text-[var(--muted-foreground)]">{item.detail}</span>
              <span className="shrink-0 text-[12.5px] text-[var(--primary)]">
                {item.emphasis === "strong" ? tr({ zh: "重点", en: "Focus" }) : tr({ zh: "持续", en: "Ongoing" })}
              </span>
              <span className="shrink-0 text-[12.5px] tabular-nums text-[var(--muted-foreground)]">
                {tr({ zh: `${item.evidence_count} 条证据`, en: `${item.evidence_count} signals` })}
              </span>
            </div>
          );
        })}
      </div>
      <p className="mt-6 border-t border-[var(--border)] pt-4 text-[12px] leading-relaxed text-[var(--muted-foreground)]">
        {support.boundary}
      </p>
    </section>
  );
}
