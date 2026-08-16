"use client";

import { ExternalLink, Fingerprint, Layers3 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  MemoryProvenance,
  MemoryScope,
  MemorySensitivity,
} from "@/lib/canonical-memory-api";

interface MemorySourceDetailsProps {
  scope: MemoryScope;
  scopeId?: string | null;
  subjectId?: string | null;
  kcId?: string | null;
  provenance: MemoryProvenance;
  sensitivity: MemorySensitivity;
  evidenceRefs?: string[];
  sourceRef?: string | null;
  confidence?: number;
  compact?: boolean;
}

export function MemorySourceDetails({
  scope,
  scopeId,
  subjectId,
  kcId,
  provenance,
  sensitivity,
  evidenceRefs = [],
  sourceRef,
  confidence,
  compact = false,
}: MemorySourceDetailsProps) {
  const { i18n } = useTranslation();
  const zh = i18n.language.toLowerCase().startsWith("zh");
  const scopeText = [scopeLabel(scope, zh), scopeId, subjectId, kcId]
    .filter(Boolean)
    .join(" · ");
  const sourceIsLink = Boolean(sourceRef && /^https?:\/\//i.test(sourceRef));

  return (
    <div className={`text-xs text-[var(--muted-foreground)] ${compact ? "space-y-1.5" : "space-y-2.5"}`}>
      <div className="flex flex-wrap gap-2">
        <span className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)] px-2.5 py-1">
          <Layers3 aria-hidden="true" className="h-3 w-3" />
          {scopeText}
        </span>
        <span className="rounded-full bg-[var(--muted)] px-2.5 py-1">
          {provenance === "explicit"
            ? zh ? "由你明确提供" : "Explicitly provided"
            : zh ? "系统推断" : "System inferred"}
        </span>
        <span className="rounded-full bg-[var(--muted)] px-2.5 py-1">
          {sensitivityLabel(sensitivity, zh)}
        </span>
        {typeof confidence === "number" ? (
          <span className="rounded-full bg-[var(--muted)] px-2.5 py-1 tabular-nums">
            {zh ? "置信度" : "Confidence"} {Math.round(confidence * 100)}%
          </span>
        ) : null}
      </div>

      {sourceRef ? (
        <div className="flex min-w-0 items-start gap-1.5">
          <ExternalLink aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="shrink-0">{zh ? "来源" : "Source"}</span>
          {sourceIsLink ? (
            <a
              href={sourceRef}
              target="_blank"
              rel="noreferrer"
              className="min-w-0 break-all text-[var(--primary)] underline underline-offset-2"
            >
              {sourceRef}
            </a>
          ) : (
            <span className="min-w-0 break-all">{sourceRef}</span>
          )}
        </div>
      ) : null}

      {evidenceRefs.length ? (
        <details>
          <summary className="cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]">
            <span className="inline-flex items-center gap-1.5">
              <Fingerprint aria-hidden="true" className="h-3.5 w-3.5" />
              {zh ? `${evidenceRefs.length} 条证据引用` : `${evidenceRefs.length} evidence references`}
            </span>
          </summary>
          <ul className="mt-2 space-y-1 pl-5 font-mono text-[11px]">
            {evidenceRefs.map((reference) => (
              <li key={reference} className="break-all">{reference}</li>
            ))}
          </ul>
        </details>
      ) : (
        <p>{zh ? "没有附带证据引用" : "No evidence references attached"}</p>
      )}
    </div>
  );
}

function scopeLabel(scope: MemoryScope, zh: boolean): string {
  const labels: Record<MemoryScope, [string, string]> = {
    conversation: ["对话", "Conversation"],
    research: ["研究", "Research"],
    project: ["项目", "Project"],
    subject: ["学科", "Subject"],
    global: ["全局", "Global"],
  };
  return labels[scope][zh ? 0 : 1];
}

function sensitivityLabel(value: MemorySensitivity, zh: boolean): string {
  const labels: Record<MemorySensitivity, [string, string]> = {
    public: ["公开", "Public"],
    personal: ["个人", "Personal"],
    sensitive: ["敏感", "Sensitive"],
  };
  return labels[value][zh ? 0 : 1];
}
