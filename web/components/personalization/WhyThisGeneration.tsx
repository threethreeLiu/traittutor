"use client";
/* eslint-disable i18n/no-literal-ui-text -- visible rationale is supplied by the Chinese teaching product. */
import { useState } from "react";
import { Info, X } from "lucide-react";

export function WhyThisGeneration({ snapshot, plan }: { snapshot?: Record<string, unknown> | null; plan?: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false);
  const source = (plan || snapshot?.plan || {}) as { rationale?: unknown };
  const rationale = Array.isArray(source.rationale) ? (source.rationale as Array<{ text?: string; evidence_refs?: string[] }>) : [];
  if (!snapshot && !plan) return null;
  return <>
    <button type="button" onClick={() => setOpen(true)} className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-3 py-2 text-xs hover:bg-[var(--accent)]"><Info className="h-3.5 w-3.5"/>为什么这样生成？</button>
    {open ? <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="dialog" aria-modal="true"><section className="w-full max-w-lg rounded-2xl border bg-[var(--card)] p-5 shadow-xl"><div className="flex items-start justify-between gap-3"><div><h2 className="font-semibold">为什么这样生成？</h2><p className="mt-1 text-sm text-[var(--muted-foreground)]">以下是可见的教学依据，不包含人格分数、隐藏推理或原始对话。</p></div><button type="button" onClick={() => setOpen(false)} aria-label="关闭"><X className="h-4 w-4"/></button></div><ul className="mt-4 space-y-2">{rationale.length ? rationale.map((item, index) => <li key={index} className="rounded-lg bg-[var(--muted)]/50 p-3 text-sm">{item.text || "采用了当前任务的标准教学策略。"}{item.evidence_refs?.length ? <p className="mt-1 text-xs text-[var(--muted-foreground)]">证据 {item.evidence_refs.length} 条</p> : null}</li>) : <li className="text-sm text-[var(--muted-foreground)]">采用 TraitTutor 的标准教学策略。</li>}</ul><div className="mt-4 rounded-lg border p-3 text-xs text-[var(--muted-foreground)]">你可以在“我的学习模型”中查看、修改或关闭相关偏好和行为推断。</div></section></div> : null}
  </>;
}
