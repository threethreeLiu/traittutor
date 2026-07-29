"use client";

import dynamic from "next/dynamic";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { langForFilename } from "@/lib/code-languages";
import { useTextSource } from "./useTextSource";

const RichCodeBlock = dynamic(
  () => import("@/components/common/RichCodeBlock"),
  { ssr: false },
);

/**
 * Plain text + code preview. Files with a recognised code extension render
 * inside RichCodeBlock with one-dark syntax highlighting; everything else
 * falls back to a tidy monospace block.
 *
 * Language mapping lives in ``@/lib/code-languages`` — adding a new
 * highlight target is a one-line edit there.
 */
export default function TextPreview({
  url,
  filename,
}: {
  url: string;
  filename: string;
}) {
  const { t } = useTranslation();
  const state = useTextSource(url);
  const lang = langForFilename(filename) ?? "text";

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
    <div className="px-4 py-4">
      <RichCodeBlock raw={state.text} lang={lang} />
    </div>
  );
}
