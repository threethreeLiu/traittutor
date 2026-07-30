"use client";

import { Compass } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

interface GeogebraOpenCTAProps {
  /** Raw ggbscript body. */
  script: string;
  /** Stable id from the ```ggbscript[id;title] fence — used for tab dedupe. */
  payloadId?: string;
  /** Title to show on the CTA + the resulting tab. */
  title?: string;
  className?: string;
}

/**
 * Card-style CTA shown in-place of a ```ggbscript fence in chat answers.
 * TraitTutor keeps these artifacts inside the chat flow, so the card expands
 * inline instead of opening a separate activity/viewer panel.
 */
export default function GeogebraOpenCTA({
  script,
  title,
  className = "",
}: GeogebraOpenCTAProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`my-3 ${className}`}>
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="group flex w-full items-center gap-3 rounded-xl border border-[var(--border)] bg-[var(--card)] px-4 py-3 text-left transition-colors hover:border-[var(--primary)]/60 hover:bg-[var(--muted)]/30"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--primary)]/10 text-[var(--primary)]">
          <Compass size={18} strokeWidth={1.9} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-[var(--foreground)]">
            {title || t("Interactive GeoGebra figure")}
          </span>
          <span className="block text-xs text-[var(--muted-foreground)]">
            {t(
              expanded
                ? "Click to collapse the inline construction script."
                : "Click to expand the inline construction script.",
            )}
          </span>
        </span>
      </button>
      {expanded ? (
        <pre className="mt-2 max-h-72 overflow-auto rounded-xl border border-[var(--border)] bg-[var(--muted)]/35 p-3 text-xs leading-relaxed text-[var(--foreground)]">
          <code>{script}</code>
        </pre>
      ) : null}
    </div>
  );
}
