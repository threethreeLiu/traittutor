"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, FileText, Layers, Loader2, Search, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import PickerShell from "@/components/common/PickerShell";
import PickerHeader from "@/components/common/PickerHeader";
import {
  listLearningPacks,
  type GenerateKind,
  type LearningPack,
} from "@/lib/traittutor-api";
import type { LearningArtifactReferencePayload } from "@/context/UnifiedChatContext";

export interface SelectedLearningArtifactReference extends LearningArtifactReferencePayload {
  title: string;
  pack_title: string;
}

interface LearningArtifactPickerProps {
  open: boolean;
  initialReferences: SelectedLearningArtifactReference[];
  onClose: () => void;
  onApply: (references: SelectedLearningArtifactReference[]) => void;
}

interface ArtifactOption extends SelectedLearningArtifactReference {
  updated_at: string;
}

const TYPE_LABELS: Record<GenerateKind, { zh: string; en: string }> = {
  courseware: { zh: "课件", en: "Courseware" },
  flashcards: { zh: "闪卡", en: "Flashcards" },
  quiz: { zh: "题目", en: "Quiz" },
};

function artifactTitle(pack: LearningPack, type: GenerateKind, artifact: Record<string, unknown>, index: number): string {
  return String(artifact.title || `${pack.title} · ${TYPE_LABELS[type].zh} ${index + 1}`);
}

function flattenArtifacts(packs: LearningPack[]): ArtifactOption[] {
  return packs.flatMap((pack) =>
    (["courseware", "flashcards", "quiz"] as GenerateKind[]).flatMap((type) => {
      const artifacts = Array.isArray(pack.artifacts?.[type]) ? pack.artifacts[type] : [];
      return artifacts.map((artifact, index) => ({
        pack_id: pack.pack_id,
        artifact_type: type,
        artifact_index: index,
        title: artifactTitle(pack, type, artifact, index),
        pack_title: pack.title,
        updated_at: pack.updated_at,
      }));
    }),
  );
}

function referenceKey(ref: LearningArtifactReferencePayload): string {
  return `${ref.pack_id}:${ref.artifact_type}:${ref.artifact_index ?? -1}`;
}

export default function LearningArtifactPicker({
  open,
  initialReferences,
  onClose,
  onApply,
}: LearningArtifactPickerProps) {
  const { t, i18n } = useTranslation();
  const zh = i18n.language?.startsWith("zh");
  const [packs, setPacks] = useState<LearningPack[]>([]);
  const [selected, setSelected] = useState<SelectedLearningArtifactReference[]>(initialReferences);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let mounted = true;
    void (async () => {
      if (!mounted) return;
      setSelected(initialReferences);
      setLoading(true);
      try {
        const items = await listLearningPacks();
        if (mounted) setPacks(items);
      } catch {
        if (mounted) setPacks([]);
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => {
      mounted = false;
    };
  }, [open, initialReferences]);

  const options = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const all = flattenArtifacts(packs);
    if (!keyword) return all;
    return all.filter((item) =>
      `${item.title} ${item.pack_title} ${item.artifact_type}`.toLowerCase().includes(keyword),
    );
  }, [packs, query]);

  const selectedKeys = useMemo(() => new Set(selected.map(referenceKey)), [selected]);

  const toggle = (option: ArtifactOption) => {
    const key = referenceKey(option);
    setSelected((current) =>
      selectedKeys.has(key)
        ? current.filter((item) => referenceKey(item) !== key)
        : [...current, option],
    );
  };

  const handleApply = () => {
    onApply(selected);
    onClose();
  };

  return (
    <PickerShell
      open={open}
      onClose={onClose}
      labelledBy="learning-artifact-picker-title"
      className="p-4 backdrop-blur-md"
      backdropClass="bg-[var(--background)]/65"
    >
      <div className="surface-card w-full max-w-4xl overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)] text-[var(--card-foreground)] shadow-[0_22px_70px_rgba(0,0,0,0.18)]">
        <PickerHeader
          icon={Sparkles}
          titleId="learning-artifact-picker-title"
          title={t("选择学习产物")}
          subtitle={t("选择已生成的课件、闪卡或题目，作为下一轮聊天问询的上下文。")}
          onClose={onClose}
        />
        <div className="bg-[var(--background)]/40 p-5">
          <div className="mb-4 flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("搜索学习包、课件、闪卡或题目")}
                className="w-full rounded-xl border border-[var(--border)] bg-[var(--card)] py-2.5 pl-9 pr-3 text-[13px] text-[var(--foreground)] outline-none transition focus:border-[var(--primary)]/50 focus:ring-2 focus:ring-[var(--primary)]/15"
              />
            </div>
            <button
              onClick={() => setSelected([])}
              className="rounded-xl border border-[var(--border)] bg-[var(--card)] px-3 py-2.5 text-[12px] font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
            >
              {t("Clear")}
            </button>
          </div>
          <div className="max-h-[56vh] overflow-y-auto rounded-2xl border border-[var(--border)] bg-[var(--card)]">
            {loading ? (
              <div className="flex min-h-[260px] items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
              </div>
            ) : options.length ? (
              <div className="divide-y divide-[var(--border)]">
                {options.map((option) => {
                  const active = selectedKeys.has(referenceKey(option));
                  const label = TYPE_LABELS[option.artifact_type][zh ? "zh" : "en"];
                  return (
                    <button
                      key={referenceKey(option)}
                      onClick={() => toggle(option)}
                      className={`flex w-full items-start gap-3 px-4 py-3 text-left transition-colors ${
                        active ? "bg-[var(--primary)]/8" : "hover:bg-[var(--muted)]/40"
                      }`}
                    >
                      <div
                        className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-colors ${
                          active
                            ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]"
                            : "border-[var(--border)] text-transparent"
                        }`}
                      >
                        <Check size={12} />
                      </div>
                      <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--muted)]/60 text-[var(--primary)]">
                        {option.artifact_type === "courseware" ? <FileText size={16} /> : <Layers size={16} />}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="truncate text-[14px] font-medium text-[var(--foreground)]">{option.title}</span>
                          <span className="rounded-full bg-[var(--primary)]/10 px-2 py-0.5 text-[10px] font-semibold text-[var(--primary)]">{label}</span>
                        </div>
                        <p className="mt-1 truncate text-[12px] text-[var(--muted-foreground)]">
                          {option.pack_title}
                        </p>
                      </div>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="flex min-h-[260px] flex-col items-center justify-center px-6 text-center">
                <Sparkles className="h-6 w-6 text-[var(--muted-foreground)]" />
                <p className="mt-3 text-[14px] font-medium">{t("还没有可引用的学习产物")}</p>
                <p className="mt-1 text-[12px] text-[var(--muted-foreground)]">
                  {t("先在我的学习中生成课件、闪卡或 Quiz，之后就能在聊天里选择它们继续追问。")}
                </p>
              </div>
            )}
          </div>
          <div className="mt-4 flex items-center justify-between gap-3">
            <div className="text-[12px] text-[var(--muted-foreground)]">
              {t("已选择 {{n}} 个学习产物", { n: selected.length })}
            </div>
            <button
              onClick={handleApply}
              disabled={!selected.length}
              className="btn-primary rounded-xl bg-[var(--primary)] px-4 py-2.5 text-[13px] font-medium text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t("使用选中产物")}
            </button>
          </div>
        </div>
      </div>
    </PickerShell>
  );
}
