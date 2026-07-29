"use client";

import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import { useTextSource } from "./useTextSource";

/**
 * Markdown preview that reuses the chat's main MarkdownRenderer (math,
 * tables, code highlight, mermaid all auto-detected).
 */
export default function MarkdownPreview({ url }: { url: string }) {
  const { t } = useTranslation();
  const state = useTextSource(url);

  if (state.kind === "loading") {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-[12px] text-[var(--muted-foreground)]">
        <Loader2 size={14} className="animate-spin" />
        <span>{t("Loading preview…")}</span>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-[12px] text-[var(--muted-foreground)]">
        {state.message}
      </div>
    );
  }

  return (
    <div className="px-6 py-5">
      <MarkdownRenderer content={state.text} variant="prose" />
    </div>
  );
}
