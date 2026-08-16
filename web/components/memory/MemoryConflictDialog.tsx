"use client";

import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import Modal from "@/components/common/Modal";
import type { MemoryConflict } from "@/lib/canonical-memory-api";

interface MemoryConflictDialogProps {
  conflict: MemoryConflict | null;
  busy: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function MemoryConflictDialog({
  conflict,
  busy,
  onConfirm,
  onClose,
}: MemoryConflictDialogProps) {
  const { i18n } = useTranslation();
  const zh = i18n.language.toLowerCase().startsWith("zh");
  const [acknowledgedCandidateId, setAcknowledgedCandidateId] = useState<string | null>(null);
  const acknowledged = acknowledgedCandidateId === conflict?.candidate_id;

  function close() {
    setAcknowledgedCandidateId(null);
    onClose();
  }

  return (
    <Modal
      isOpen={Boolean(conflict)}
      onClose={() => {
        if (!busy) close();
      }}
      title={zh ? "确认替换冲突记忆" : "Confirm memory replacement"}
      titleIcon={<AlertTriangle aria-hidden="true" className="h-5 w-5 text-amber-500" />}
      width="lg"
      closeOnBackdrop={!busy}
      closeOnEscape={!busy}
      showCloseButton={!busy}
      footer={
        <div className="flex flex-wrap justify-end gap-2">
          <button
            type="button"
            onClick={close}
            disabled={busy}
            data-autofocus
            className="h-10 rounded-lg border border-[var(--border)] px-4 text-sm font-medium disabled:opacity-50"
          >
            {zh ? "取消" : "Cancel"}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy || !acknowledged}
            className="h-10 rounded-lg bg-amber-600 px-4 text-sm font-medium text-white transition-colors hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {busy
              ? (zh ? "正在替换…" : "Replacing…")
              : (zh ? "替换旧记忆" : "Replace old memory")}
          </button>
        </div>
      }
    >
      {conflict ? (
        <div className="space-y-4 p-5">
          <p className="text-sm leading-relaxed text-[var(--muted-foreground)]">
            {zh
              ? "新候选与当前生效记忆冲突。替换会保留历史关系，但后续召回将使用新值。"
              : "This candidate conflicts with active memory. Replacement preserves history, but future recall will use the new value."}
          </p>

          <div className="grid gap-3 sm:grid-cols-2">
            <section className="rounded-xl border border-[var(--border)] bg-[var(--muted)]/25 p-4">
              <h4 className="text-xs font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                {zh ? "当前记忆" : "Current memory"}
              </h4>
              <ul className="mt-3 space-y-2 text-sm">
                {conflict.values.map((value, index) => (
                  <li key={`${conflict.memory_ids[index] ?? index}-${value}`} className="break-words">
                    {value}
                  </li>
                ))}
              </ul>
            </section>
            <section className="rounded-xl border border-amber-500/35 bg-amber-500/10 p-4">
              <h4 className="text-xs font-medium uppercase tracking-[0.12em] text-amber-700 dark:text-amber-300">
                {zh ? "新候选" : "New candidate"}
              </h4>
              <p className="mt-3 break-words text-sm">{conflict.candidate_value}</p>
            </section>
          </div>

          <p className="text-xs text-[var(--muted-foreground)]">
            {zh ? "记忆键" : "Memory key"}: <span className="font-mono">{conflict.key}</span>
            {conflict.scope_id ? ` · ${conflict.scope_id}` : ""}
            {conflict.subject_id ? ` · ${conflict.subject_id}` : ""}
            {conflict.kc_id ? ` · ${conflict.kc_id}` : ""}
          </p>

          <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-[var(--border)] p-3 text-sm">
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(event) => setAcknowledgedCandidateId(
                event.target.checked ? (conflict?.candidate_id ?? null) : null,
              )}
              disabled={busy}
              className="mt-0.5 h-4 w-4 rounded border-[var(--border)] accent-[var(--primary)]"
            />
            <span>
              {zh
                ? "我确认用新候选替换当前生效值。"
                : "I confirm that the candidate should replace the active value."}
            </span>
          </label>
        </div>
      ) : null}
    </Modal>
  );
}
