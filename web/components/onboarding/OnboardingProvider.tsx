"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, BrainCircuit, Check, Loader2 } from "lucide-react";
import {
  createTraitProfile,
  fetchTraitQuestions,
  listTraitProfiles,
  type TraitQuestionsResponse,
} from "@/lib/traittutor-api";
import { useAppShell } from "@/context/AppShellContext";

type OnboardingContextValue = {
  openAssessment: () => void;
  profileRevision: number;
};
const OnboardingContext = createContext<OnboardingContextValue | null>(null);
const ONBOARDING_DISMISSED_KEY = "traittutor:onboarding-profile-dismissed";

const copy = {
  zh: { eyebrow: "可选的个性化设置", title: "调整你的学习支持方式", boundary: "你可以先进入产品，从学习目标开始。这份简短测评只会调整讲解节奏、支架与检查点，不用于判断能力或贴上学习风格标签。", previous: "上一步", next: "下一题", finish: "完成并开始学习", saving: "正在创建画像", loading: "正在准备测评", complete: "已完成", skip: "稍后设置，先开始学习" },
  en: { eyebrow: "Optional personalization", title: "Tune your learning support", boundary: "You can start with a learning goal now. This brief assessment only adapts pace, scaffolding, and checkpoints; it does not judge ability or assign a learning style.", previous: "Previous", next: "Next", finish: "Finish and start learning", saving: "Creating your profile", loading: "Preparing assessment", complete: "Complete", skip: "Skip for now and start learning" },
};

const englishQuestions: Record<number, string> = {
  1: "I see myself as extraverted and enthusiastic.",
  2: "I see myself as critical and quarrelsome.",
  3: "I see myself as dependable and self-disciplined.",
  4: "I see myself as anxious and easily upset.",
  5: "I see myself as open to new experiences and complex.",
  6: "I see myself as reserved and quiet.",
  7: "I see myself as sympathetic and warm.",
  8: "I see myself as disorganized and careless.",
  9: "I see myself as calm and emotionally stable.",
  10: "I see myself as conventional and uncreative.",
};

const englishOptions: Record<number, string> = {
  1: "Disagree strongly",
  2: "Disagree a little",
  3: "Neither agree nor disagree",
  4: "Agree a little",
  5: "Agree strongly",
};

export function useOnboarding() {
  return useContext(OnboardingContext);
}

export default function OnboardingProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [checked, setChecked] = useState(false);
  const [profileRevision, setProfileRevision] = useState(0);

  const openAssessment = useCallback(() => setOpen(true), []);

  useEffect(() => {
    let active = true;
    void listTraitProfiles().then((profiles) => {
      if (!active) return;
      const dismissed = window.localStorage.getItem(ONBOARDING_DISMISSED_KEY) === "true";
      setOpen(profiles.length === 0 && !dismissed);
    }).catch(() => undefined).finally(() => {
      if (active) setChecked(true);
    });
    return () => { active = false; };
  }, []);

  const value = useMemo(
    () => ({ openAssessment, profileRevision }),
    [openAssessment, profileRevision],
  );
  const closeOnboarding = useCallback(() => {
    setOpen(false);
    setProfileRevision((revision) => revision + 1);
  }, []);
  const skipOnboarding = useCallback(() => {
    window.localStorage.setItem(ONBOARDING_DISMISSED_KEY, "true");
    setOpen(false);
  }, []);
  return <OnboardingContext.Provider value={value}>{children}{checked && open ? <BigFiveModal onComplete={closeOnboarding} onSkip={skipOnboarding} /> : null}</OnboardingContext.Provider>;
}

function BigFiveModal({ onComplete, onSkip }: { onComplete: () => void; onSkip: () => void }) {
  const { language, setLanguage } = useAppShell();
  const [questions, setQuestions] = useState<TraitQuestionsResponse | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [index, setIndex] = useState(0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const text = copy[language];
  const current = questions?.questions[index];
  const selected = answers[String(current?.id)];
  const isLast = Boolean(questions && index === questions.questions.length - 1);

  useEffect(() => {
    void fetchTraitQuestions().then(setQuestions).catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
  }, []);

  async function finish() {
    if (!questions || Object.keys(answers).length !== questions.questions.length) return;
    setSaving(true); setError("");
    try { await createTraitProfile(answers); onComplete(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); setSaving(false); }
  }

  function selectAnswer(value: number) {
    if (!questions || !current || saving) return;
    const nextAnswers = {
      ...answers,
      [String(current.id)]: value,
    };
    setAnswers(nextAnswers);
    if (!isLast) {
      setIndex((currentIndex) => Math.min(currentIndex + 1, questions.questions.length - 1));
    }
  }

  return <div role="dialog" aria-modal="true" aria-label={text.title} className="fixed inset-0 z-[100] flex items-center justify-center bg-black/65 p-4 backdrop-blur-sm">
    <section className="w-full max-w-2xl rounded-lg border border-[var(--border)] bg-[var(--card)] p-6 shadow-2xl sm:p-9">
      {!questions ? <div className="flex min-h-64 items-center justify-center text-sm text-[var(--muted-foreground)]">{error ? <p className="rounded-md bg-red-500/10 px-3 py-2 text-red-500">{error}</p> : <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{text.loading}</>}</div> : current ? <>
        <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-medium text-[var(--primary)]">{text.eyebrow}</p><h1 className="mt-2 font-serif text-2xl font-semibold sm:text-3xl">{text.title}</h1><p className="mt-3 text-sm leading-6 text-[var(--muted-foreground)]">{text.boundary}</p></div><button type="button" onClick={() => setLanguage(language === "zh" ? "en" : "zh")} aria-label={language === "zh" ? "Switch assessment language to English" : "切换测评语言为中文"} className="rounded-md border border-[var(--border)] px-2.5 py-1.5 text-xs text-[var(--muted-foreground)]">{language === "zh" ? "EN" : "中文"}</button></div>
        <div className="mt-7 flex items-center justify-between text-xs text-[var(--muted-foreground)]"><span>{index + 1} / {questions.questions.length}</span><span>{Math.round(((index + 1) / questions.questions.length) * 100)}%</span></div><div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--accent)]"><div className="h-full bg-[var(--primary)] transition-all" style={{ width: `${((index + 1) / questions.questions.length) * 100}%` }} /></div>
        <p className="mt-8 text-lg leading-8 sm:text-xl">{language === "zh" ? current.text : englishQuestions[current.id] ?? current.text}</p><div className="mt-6 grid gap-2">{questions.options.map((item) => <button key={item.value} type="button" disabled={saving} onClick={() => selectAnswer(item.value)} className={`flex min-h-12 items-center justify-between rounded-md border px-4 text-left text-sm disabled:opacity-50 ${selected === item.value ? "border-[var(--primary)] bg-[var(--primary)]/10" : "border-[var(--border)] hover:bg-[var(--accent)]"}`}><span>{language === "zh" ? item.label : englishOptions[item.value] ?? item.label}</span>{selected === item.value ? <Check className="h-4 w-4 text-[var(--primary)]" /> : <span className="text-xs text-[var(--muted-foreground)]">{item.value}</span>}</button>)}</div>
        {error ? <p className="mt-4 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p> : null}
        <div className="mt-8 flex flex-wrap items-center justify-between gap-3"><button type="button" onClick={onSkip} disabled={saving} className="h-10 rounded-md px-3 text-sm text-[var(--muted-foreground)] transition hover:bg-[var(--accent)] disabled:opacity-40">{text.skip}</button><div className="flex items-center gap-2"><button type="button" disabled={index === 0 || saving} onClick={() => setIndex((value) => value - 1)} className="inline-flex h-10 items-center gap-2 rounded-md px-3 text-sm text-[var(--muted-foreground)] disabled:opacity-40"><ArrowLeft className="h-4 w-4" />{text.previous}</button>{isLast ? <button type="button" disabled={!selected || saving} onClick={() => void finish()} className="inline-flex h-10 items-center gap-2 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <BrainCircuit className="h-4 w-4" />}{saving ? text.saving : text.finish}</button> : <button type="button" disabled={!selected || saving} onClick={() => setIndex((value) => value + 1)} className="inline-flex h-10 items-center gap-2 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">{text.next}<ArrowRight className="h-4 w-4" /></button>}</div></div>
      </> : null}
    </section>
  </div>;
}
