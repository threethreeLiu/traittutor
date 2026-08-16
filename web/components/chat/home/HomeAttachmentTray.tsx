"use client";

import { FileText, X } from "lucide-react";

export interface HomePendingAttachment {
  type: string;
  filename: string;
  base64?: string;
  previewUrl?: string;
  size?: number;
  mimeType?: string;
}

function formatFileSize(size?: number): string | null {
  if (typeof size !== "number" || !Number.isFinite(size) || size < 0) return null;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export default function HomeAttachmentTray({
  attachments,
  error,
  onRemove,
  zh,
}: {
  attachments: HomePendingAttachment[];
  error: string | null;
  onRemove: (index: number) => void;
  zh: boolean;
}) {
  if (!attachments.length && !error) return null;

  return (
    <div className="mb-2 space-y-2 px-1" aria-live="polite">
      {attachments.length ? (
        <div className="flex flex-wrap gap-2">
          {attachments.map((attachment, index) => {
            const size = formatFileSize(attachment.size);
            return (
              <div
                key={`${attachment.filename}:${attachment.size ?? 0}:${attachment.mimeType ?? ""}`}
                className="home-attachment--theme flex min-w-0 max-w-full items-center gap-2 rounded-xl border px-2.5 py-2"
              >
                <FileText size={14} className="shrink-0 text-[var(--muted-foreground)]" />
                <span className="max-w-[16rem] truncate text-[11px] font-medium text-[var(--foreground)]">
                  {attachment.filename}
                </span>
                {size ? <span className="shrink-0 text-[9.5px] text-[var(--muted-foreground)]">{size}</span> : null}
                <button
                  type="button"
                  onClick={() => onRemove(index)}
                  aria-label={zh ? `移除 ${attachment.filename}` : `Remove ${attachment.filename}`}
                  className="ml-0.5 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-[var(--muted-foreground)] transition hover:text-[var(--foreground)]"
                >
                  <X size={13} />
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
      {error ? (
        <p role="alert" className="text-[10.5px] text-[var(--destructive)]">
          {error}
        </p>
      ) : null}
    </div>
  );
}
