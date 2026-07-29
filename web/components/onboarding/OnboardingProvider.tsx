"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ArrowRight, BrainCircuit, Check, Loader2 } from "lucide-react";
import {
  createTraitProfile,
  fetchTraitQuestions,
  listTraitProfiles,
  type TraitQuestionsResponse,
} from "@/lib/traittutor-api";

type OnboardingContextValue = { openAssessment: () => void };
const OnboardingContext = createContext<OnboardingContextValue | null>(null);

const copy = {
  zh: { eyebrow: "开始使用 TraitTutor", title: "先了解你的学习支持偏好", boundary: "这份简短测评只会调整讲解节奏、支架与检查点，不用于判断能力或贴上学习风格标签。", previous: "上一步", next: "下一题", finish: "完成并开始学习", saving: "正在创建画像", loading: "正在准备测评", complete: "已完成" },
  en: { eyebrow: "Welcome to TraitTutor", title: "Set up your learning support", boundary: "This brief assessment adapts pace, scaffolding, and checkpoints. It does not judge ability or assign a learning style.", previous: "Previous", next: "Next", finish: "Finish and start learning", saving: "Creating your profile", loading: "Preparing assessment", complete: "Complete" },
};

export function useOnboarding() {
  return useContext(OnboardingContext);
}

export default function OnboardingProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [checked, setChecked] = useState(false);

  const openAssessment = useCallback(() => setOpen(true), []);

  useEffect(() => {
    let active = true;
    void listTraitProfiles().then((profiles) => {
      if (!active) return;
      setOpen(profiles.length === 0);
    }).catch(() => undefined).finally(() => {
      if (active) setChecked(true);
    });
    return () => { active = false; };
  }, []);

  const value = useMemo(() => ({ openAssessment }), [openAssessment]);
  return <OnboardingContext.Provider value={value}>{children}{checked && open ? <BigFiveModal onComplete={() => setOpen(false)} /> : null}</OnboardingContext.Provider>;
}

function BigFiveModal({ onComplete }: { onComplete: () => void }) {
  const [questions, setQuestions] = useState<TraitQuestionsResponse | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [index, setIndex] = useState(0);
  const [language, setLanguage] = useState<"zh" | "en">("zh");
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

  return <div role="dialog" aria-modal="true" aria-label={text.title} className="fixed inset-0 z-[100] flex items-center justify-center bg-black/65 p-4 backdrop-blur-sm">
    <section className="w-full max-w-2xl rounded-lg border border-[var(--border)] bg-[var(--card)] p-6 shadow-2xl sm:p-9">
      {!questions ? <div className="flex min-h-64 items-center justify-center text-sm text-[var(--muted-foreground)]">{error ? <p className="rounded-md bg-red-500/10 px-3 py-2 text-red-500">{error}</p> : <><Loader2 className="mr-2 h-4 w-4 animate-spin" />{text.loading}</>}</div> : current ? <>
        <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-medium text-[var(--primary)]">{text.eyebrow}</p><h1 className="mt-2 font-serif text-2xl font-semibold sm:text-3xl">{text.title}</h1><p className="mt-3 text-sm leading-6 text-[var(--muted-foreground)]">{text.boundary}</p></div><button type="button" onClick={() => setLanguage((value) => value === "zh" ? "en" : "zh")} className="rounded-md border border-[var(--border)] px-2.5 py-1.5 text-xs text-[var(--muted-foreground)]">{language === "zh" ? "EN" : "中文"}</button></div>
        <div className="mt-7 flex items-center justify-between text-xs text-[var(--muted-foreground)]"><span>{index + 1} / {questions.questions.length}</span><span>{Math.round(((index + 1) / questions.questions.length) * 100)}%</span></div><div className="mt-2 h-1 overflow-hidden rounded-full bg-[var(--accent)]"><div className="h-full bg-[var(--primary)] transition-all" style={{ width: `${((index + 1) / questions.questions.length) * 100}%` }} /></div>
        <p className="mt-8 text-lg leading-8 sm:text-xl">{current.text}</p><div className="mt-6 grid gap-2">{questions.options.map((item) => <button key={item.value} type="button" onClick={() => setAnswers((value) => ({ ...value, [String(current.id)]: item.value }))} className={`flex min-h-12 items-center justify-between rounded-md border px-4 text-left text-sm ${selected === item.value ? "border-[var(--primary)] bg-[var(--primary)]/10" : "border-[var(--border)] hover:bg-[var(--accent)]"}`}><span>{item.label}</span>{selected === item.value ? <Check className="h-4 w-4 text-[var(--primary)]" /> : <span className="text-xs text-[var(--muted-foreground)]">{item.value}</span>}</button>)}</div>
        {error ? <p className="mt-4 rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-500">{error}</p> : null}
        <div className="mt-8 flex items-center justify-between"><button type="button" disabled={index === 0 || saving} onClick={() => setIndex((value) => value - 1)} className="inline-flex h-10 items-center gap-2 rounded-md px-3 text-sm text-[var(--muted-foreground)] disabled:opacity-40"><ArrowLeft className="h-4 w-4" />{text.previous}</button>{isLast ? <button type="button" disabled={!selected || saving} onClick={() => void finish()} className="inline-flex h-10 items-center gap-2 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">{saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <BrainCircuit className="h-4 w-4" />}{saving ? text.saving : text.finish}</button> : <button type="button" disabled={!selected} onClick={() => setIndex((value) => value + 1)} className="inline-flex h-10 items-center gap-2 rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">{text.next}<ArrowRight className="h-4 w-4" /></button>}</div>
      </> : null}
    </section>
  </div>;
}
