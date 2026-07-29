"use client";

import { useState } from "react";
import { ChevronRight, Sparkles, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Block } from "@/lib/book-types";

interface Suggestion {
  topic?: string;
  rationale?: string;
}

export interface DeepDiveBlockProps {
  block: Block;
  onDeepDive?: (topic: string, blockId: string) => Promise<void> | void;
  pendingTopic?: string | null;
}

export default function DeepDiveBlock({
  block,
  onDeepDive,
  pendingTopic,
}: DeepDiveBlockProps) {
  const { t } = useTranslation();
  const suggestions =
    (block.payload?.suggestions as Suggestion[] | undefined) || [];
  const linkedPageId = block.metadata?.deep_dive_page_id as string | undefined;
  const [busy, setBusy] = useState<string | null>(null);

  if (suggestions.length === 0) return null;

  return (
    <div className="rounded-2xl border border-[var(--primary)]/30 bg-gradient-to-br from-[var(--primary)]/5 to-transparent p-4 shadow-sm">
      <div className="mb-3 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-[var(--primary)]" />
        <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--primary)]">
          {t("Go Deeper")}
        </span>
      </div>
      <ul className="space-y-2">
        {suggestions.map((s, i) => {
          const topic = s.topic || "";
          const isPending = busy === topic || pendingTopic === topic;
          return (
            <li key={i}>
              <button
                onClick={async () => {
                  if (!onDeepDive || !topic) return;
                  setBusy(topic);
                  try {
                    await onDeepDive(topic, block.id);
                  } finally {
                    setBusy(null);
                  }
                }}
                disabled={isPending || !!linkedPageId}
                className="group flex w-full items-start justify-between gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-left transition hover:border-[var(--primary)]/40 disabled:opacity-60"
              >
                <div className="flex-1">
                  <div className="text-sm font-medium text-[var(--foreground)]">
                    {topic}
                  </div>
                  {s.rationale && (
                    <div className="mt-0.5 text-xs leading-relaxed text-[var(--muted-foreground)]">
                      {s.rationale}
                    </div>
                  )}
                </div>
                {isPending ? (
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin text-[var(--primary)]" />
                ) : (
                  <ChevronRight className="h-4 w-4 shrink-0 text-[var(--muted-foreground)] transition group-hover:translate-x-0.5 group-hover:text-[var(--primary)]" />
                )}
              </button>
            </li>
          );
        })}
      </ul>
      {linkedPageId && (
        <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
          {t("Linked sub-page already exists.")}
        </p>
      )}
    </div>
  );
}
