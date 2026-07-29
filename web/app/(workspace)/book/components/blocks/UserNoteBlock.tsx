"use client";

import { StickyNote } from "lucide-react";
import { useTranslation } from "react-i18next";
import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import type { Block } from "@/lib/book-types";

export interface UserNoteBlockProps {
  block: Block;
}

export default function UserNoteBlock({ block }: UserNoteBlockProps) {
  const { t } = useTranslation();
  const body = String(block.payload?.body || "");
  return (
    <aside className="flex gap-3 border-l-[3px] border-dashed border-[var(--primary)]/50 bg-[var(--primary)]/[0.04] py-2 pl-4 pr-3">
      <StickyNote className="mt-[3px] h-4 w-4 shrink-0 text-[var(--primary)]" />
      <div className="min-w-0 space-y-1">
        <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--primary)]">
          {t("Your note")}
        </div>
        {body ? (
          <MarkdownRenderer content={body} variant="compact" />
        ) : (
          <div className="text-xs text-[var(--muted-foreground)]">
            {t("Empty note – start writing your own annotation.")}
          </div>
        )}
      </div>
    </aside>
  );
}
