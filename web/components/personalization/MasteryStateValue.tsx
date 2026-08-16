"use client";

import { useAppShell } from "@/context/AppShellContext";
import { masteryDisplay, type MasteryEvidence } from "@/lib/mastery-display";

interface MasteryStateValueProps extends MasteryEvidence {
  className?: string;
}

const LABELS = {
  insufficient_evidence: { zh: "证据不足", en: "Insufficient evidence" },
  needs_support: { zh: "需要支持", en: "Needs support" },
  developing: { zh: "正在发展", en: "Developing" },
  supported: { zh: "证据支持", en: "Supported" },
} as const;

const CHANGE_LABELS = {
  none: null,
  needs_review: { zh: "待修复", en: "Needs review" },
  repaired: { zh: "已修复", en: "Repaired" },
  due_for_review: { zh: "待复习", en: "Due for review" },
} as const;

export function MasteryStateValue({
  evidenceState,
  changeSignal,
  className = "",
}: MasteryStateValueProps) {
  const { language } = useAppShell();
  const zh = language === "zh";
  const display = masteryDisplay({ evidenceState, changeSignal });
  const stateLabel = LABELS[display.evidenceState];
  const changeLabel = CHANGE_LABELS[display.changeSignal];
  return (
    <span
      className={`text-[var(--muted-foreground)] ${className}`.trim()}
      data-mastery-state={display.evidenceState}
      data-change-signal={display.changeSignal}
    >
      {zh ? stateLabel.zh : stateLabel.en}
      {changeLabel ? ` · ${zh ? changeLabel.zh : changeLabel.en}` : ""}
    </span>
  );
}
