"use client";

import { useId, type ReactNode } from "react";
import { AlertTriangle, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import PickerShell from "@/components/common/PickerShell";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** Body content — plain text or richer markup (e.g. an avatar row). */
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** "danger" renders a red confirm button for destructive actions. */
  tone?: "default" | "danger";
  /** Disables the buttons and swaps the confirm label while pending. */
  busy?: boolean;
  busyLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Small confirmation modal in the app's dialog style (overlay + card),
 * replacing bare window.confirm() prompts. Closes on Escape and on
 * overlay click; the cancel button takes initial focus so a stray Enter
 * never triggers a destructive action.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "default",
  busy = false,
  busyLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();
  const requestClose = () => {
    if (!busy) onCancel();
  };

  return (
    <PickerShell
      open={open}
      onClose={requestClose}
      labelledBy={titleId}
      describedBy={children ? descriptionId : undefined}
      role="alertdialog"
      zIndex={100}
      className="p-2 sm:p-4"
      backdropClass="bg-[var(--overlay)] backdrop-blur-[2px]"
    >
      <div
        aria-busy={busy}
        className="flex max-h-[calc(100dvh-1rem)] w-[calc(100vw-1rem)] max-w-md flex-col overflow-hidden rounded-3xl border border-[var(--border)] bg-[var(--card)] shadow-2xl shadow-black/20 sm:max-h-[min(90dvh,680px)]"
      >
        <div className="flex shrink-0 items-start gap-3 px-5 pb-3 pt-5 sm:px-6 sm:pt-6">
          <span className={`grid h-10 w-10 shrink-0 place-items-center rounded-2xl ${tone === "danger" ? "bg-red-500/10 text-red-600 dark:text-red-400" : "bg-[var(--primary)]/10 text-[var(--primary)]"}`}>
            <AlertTriangle size={19} strokeWidth={1.8} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="pt-0.5 text-[15px] font-semibold text-[var(--foreground)] [text-wrap:pretty]">
              {title}
            </h2>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="grid h-10 w-10 shrink-0 place-items-center rounded-xl text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)] hover:text-[var(--foreground)] disabled:opacity-40"
            aria-label={t("Close")}
          >
            <X size={16} />
          </button>
        </div>

        {children && (
          <div
            id={descriptionId}
            className="min-h-0 overflow-y-auto px-5 pb-5 text-[13px] leading-6 text-[var(--muted-foreground)] [overflow-wrap:anywhere] sm:px-6"
          >
            {children}
          </div>
        )}

        <div className="flex shrink-0 items-center justify-end gap-2 border-t border-[var(--border)]/70 px-5 py-4 sm:px-6">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            data-autofocus
            className="inline-flex min-h-10 items-center justify-center rounded-xl px-4 py-2 text-sm font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)] hover:text-[var(--foreground)] disabled:opacity-40"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm font-medium transition-colors disabled:opacity-40 ${
              tone === "danger"
              ? "bg-red-600 text-white shadow-sm shadow-red-600/20 hover:bg-red-700"
                : "bg-[var(--foreground)] text-[var(--background)] hover:opacity-90"
            }`}
          >
            {busy ? <Loader2 aria-hidden="true" size={15} className="animate-spin" /> : null}
            {busy ? (busyLabel ?? confirmLabel) : confirmLabel}
          </button>
        </div>
      </div>
    </PickerShell>
  );
}
