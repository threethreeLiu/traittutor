"use client";

import { Check, Sparkles, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MemoryCandidate } from "@/lib/canonical-memory-api";
import { MemorySourceDetails } from "@/components/memory/MemorySourceDetails";

interface MemoryCandidateListProps {
  candidates: MemoryCandidate[];
  busyId: string | null;
  onActivate: (candidate: MemoryCandidate) => void;
  onReject: (candidate: MemoryCandidate) => void;
}

export function MemoryCandidateList({
  candidates,
  busyId,
  onActivate,
  onReject,
}: MemoryCandidateListProps) {
  const { i18n } = useTranslation();
  const zh = i18n.language.toLowerCase().startsWith("zh");

  if (!candidates.length) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border)] px-4 py-8 text-center">
        <Sparkles aria-hidden="true" className="mx-auto h-5 w-5 text-[var(--primary)]" />
        <p className="mt-3 text-sm font-medium">
          {zh ? "没有等待确认的记忆" : "No memories are waiting for review"}
        </p>
        <p className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-[var(--muted-foreground)]">
          {zh
            ? "系统推断不会静默进入长期记忆；新的候选会先出现在这里。"
            : "Inferences never silently become long-term memory; new candidates appear here first."}
        </p>
      </div>
    );
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {candidates.map((candidate) => {
        const busy = busyId === candidate.candidate_id;
        const actionInProgress = busyId !== null;
        return (
          <article
            key={candidate.candidate_id}
            className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                  {candidate.key}
                </p>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--foreground)]">
                  {candidate.value}
                </p>
              </div>
              <span className="shrink-0 rounded-full bg-amber-500/10 px-2.5 py-1 text-xs text-amber-700 dark:text-amber-300">
                {zh ? "待确认" : "Candidate"}
              </span>
            </div>

            <div className="mt-4">
              <MemorySourceDetails
                scope={candidate.scope}
                scopeId={candidate.scope_id}
                subjectId={candidate.subject_id}
                kcId={candidate.kc_id}
                provenance={candidate.provenance}
                sensitivity={candidate.sensitivity}
                evidenceRefs={candidate.evidence_refs}
                sourceRef={candidate.source_ref}
                confidence={candidate.confidence}
                compact
              />
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onActivate(candidate)}
                disabled={actionInProgress}
                className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg bg-[var(--primary)] px-3 text-xs font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Check aria-hidden="true" className="h-3.5 w-3.5" />
                {busy ? (zh ? "正在保存…" : "Saving…") : (zh ? "确认记住" : "Confirm")}
              </button>
              <button
                type="button"
                onClick={() => onReject(candidate)}
                disabled={actionInProgress}
                className="inline-flex h-9 items-center justify-center gap-1.5 rounded-lg border border-[var(--border)] px-3 text-xs font-medium transition-colors hover:border-[var(--destructive)]/50 hover:text-[var(--destructive)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <X aria-hidden="true" className="h-3.5 w-3.5" />
                {zh ? "拒绝" : "Reject"}
              </button>
            </div>
          </article>
        );
      })}
    </div>
  );
}
