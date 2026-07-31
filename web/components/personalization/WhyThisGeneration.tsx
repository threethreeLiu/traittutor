"use client";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Info, X } from "lucide-react";

export function WhyThisGeneration({ snapshot, plan }: { snapshot?: Record<string, unknown> | null; plan?: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false);
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const source = (plan || snapshot?.plan || {}) as { rationale?: unknown };
  const rationale = Array.isArray(source.rationale) ? (source.rationale as Array<{ text?: string; evidence_refs?: string[] }>) : [];
  const degraded = Boolean(snapshot?.degraded);
  const degradationReason = String(snapshot?.degradation_reason || "");
  if (!snapshot && !plan) return null;
  return <>
    <button type="button" onClick={() => setOpen(true)} className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-3 py-2 text-xs hover:bg-[var(--accent)]"><Info className="h-3.5 w-3.5"/>{zh ? "为什么这样生成？" : "Why this result?"}</button>
    {open ? <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="dialog" aria-modal="true"><section className="w-full max-w-lg rounded-2xl border bg-[var(--card)] p-5 shadow-xl"><div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold">{zh ? "为什么这样生成？" : "Why this result?"}</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">{zh ? "这里只展示可见教学依据：目标、偏好、学科证据、薄弱概念和教学动作；不包含人格分数、隐藏推理或原始 Prompt。" : "This shows visible teaching evidence only: goals, preferences, subject evidence, weak concepts, and teaching actions. It does not expose personality scores, hidden reasoning, or raw prompts."}</p></div><button type="button" onClick={() => setOpen(false)} aria-label={zh ? "关闭" : "Close"}><X className="h-4 w-4"/></button></div><ul className="mt-4 space-y-2">{rationale.length ? rationale.map((item, index) => <li key={index} className="rounded-lg bg-[var(--muted)]/50 p-3 text-sm">{item.text || (zh ? "采用了当前任务的标准教学策略。" : "TraitTutor used the standard teaching strategy for this task.")}{item.evidence_refs?.length ? <p className="mt-1 text-xs text-[var(--muted-foreground)]">{zh ? "证据" : "Evidence"} {item.evidence_refs.length} {zh ? "条" : "refs"}</p> : null}</li>) : <li className="text-sm text-[var(--muted-foreground)]">{zh ? "采用 TraitTutor 的标准教学策略。" : "TraitTutor used its standard teaching strategy."}</li>}</ul>{degraded ? <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">{zh ? "已降级：" : "Degraded: "}{degradationReason || (zh ? "个性化上下文不可用，已回退到通用教学。" : "Personalization context was unavailable, so the result fell back to general teaching.")}</div> : null}<div className="mt-4 rounded-lg border p-3 text-xs text-[var(--muted-foreground)]">{zh ? "你可以在“我的学习模型”中查看、修改或关闭相关偏好和行为推断。" : "You can review, edit, or disable related preferences and behavioral inference in My Learning Model."}</div></section></div> : null}
  </>;
}
