"use client";

import { Download, FileQuestion } from "lucide-react";
import { useTranslation } from "react-i18next";
import { docIconFor } from "@/lib/doc-attachments";

/**
 * Last-resort preview: a centred icon + filename + Download CTA. Used for
 * encrypted PDFs we cannot iframe, exotic binary formats, or attachments
 * predating the storage rollout where the URL is missing.
 */
export default function FallbackPreview({
  filename,
  url,
  reason,
}: {
  filename: string;
  url: string | null;
  reason?: "legacy" | "unsupported";
}) {
  const { t } = useTranslation();
  const spec = docIconFor(filename);
  const Icon = url ? spec.Icon : FileQuestion;
  const tint = url ? spec.tint : "text-[var(--muted-foreground)]";

  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-[var(--muted)]/60">
        <Icon size={40} strokeWidth={1.4} className={tint} />
      </div>
      <div className="space-y-1">
        <div className="text-[14px] font-medium text-[var(--foreground)]">
          {filename}
        </div>
        <div className="text-[12px] text-[var(--muted-foreground)]">
          {reason === "legacy"
            ? t(
                "Original file is not stored (sent before preview was supported).",
              )
            : t("Preview is not available for this file type.")}
        </div>
      </div>
      {url && (
        <a
          href={url}
          download={filename}
          className="inline-flex items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--card)] px-3 py-1.5 text-[12px] font-medium text-[var(--muted-foreground)] transition-colors hover:border-[var(--primary)]/40 hover:text-[var(--primary)]"
        >
          <Download size={13} strokeWidth={1.7} />
          {t("Download")}
        </a>
      )}
    </div>
  );
}
