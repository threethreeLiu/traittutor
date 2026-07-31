"use client";

import { ExternalLink, Globe2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { GenerateSuiteResult, MaterialAnalysis } from "@/lib/traittutor-api";

const SUBJECT_LABELS: Record<string, { zh: string; en: string }> = {
  language_arts: { zh: "语文与语言", en: "Language arts" },
  mathematics: { zh: "数学", en: "Mathematics" },
  english_foreign_language: { zh: "英语与外语", en: "English & foreign languages" },
  science_engineering: { zh: "科学与工程", en: "Science & engineering" },
  social_sciences: { zh: "社会科学", en: "Social sciences" },
  computing_it: { zh: "计算机与信息技术", en: "Computing & IT" },
  arts_design: { zh: "艺术与设计", en: "Arts & design" },
  health_physical_education: { zh: "健康与体育", en: "Health & physical education" },
  vocational_professional: { zh: "职业与专业", en: "Vocational & professional" },
  interdisciplinary: { zh: "跨学科", en: "Interdisciplinary" },
  other: { zh: "其他", en: "Other" },
};

const DIFFICULTY_LABELS: Record<string, { zh: string; en: string }> = {
  foundation: { zh: "基础", en: "Foundation" },
  standard: { zh: "标准", en: "Standard" },
  advanced: { zh: "进阶", en: "Advanced" },
  competition_professional: { zh: "竞赛或专业", en: "Competition / professional" },
};

function displayLabel(value: string, labels: Record<string, { zh: string; en: string }>, zh: boolean) {
  return labels[value]?.[zh ? "zh" : "en"] ?? value.replace(/_/g, " ");
}

export function MaterialAnalysisSummary({ analysis, compact = false }: { analysis: MaterialAnalysis; compact?: boolean }) {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const locale = zh ? "zh" : "en";
  const subject = displayLabel(analysis.subject, SUBJECT_LABELS, Boolean(zh));
  const difficulty = displayLabel(analysis.difficulty, DIFFICULTY_LABELS, Boolean(zh));
  const evidence = analysis.page_evidence?.length ? analysis.page_evidence : analysis.evidence;
  const candidates = analysis.concept_candidates ?? [];
  const gradeBand = analysis.grade_band ?? { chinese: analysis.chinese_grade, international: analysis.international_grade };
  const augmentationNeeded = analysis.augmentation_decision?.needed ?? analysis.augmentation_needed;
  const augmentationReason = analysis.augmentation_decision?.reason ?? analysis.augmentation_reason;

  return <section className="mt-3 rounded-lg border border-teal-500/30 bg-teal-500/[0.04] p-3 text-[12px]" aria-label={zh ? "材料识别结果" : "Material analysis"}>
    <p className="font-medium">{zh ? "材料识别：" : "Material analysis: "}{analysis.sub_subject}</p>
    <p className="mt-1 text-[var(--muted-foreground)]">{subject} · {zh ? "中国" : "China"}：{gradeBand.chinese ?? analysis.chinese_grade} · {zh ? "国际" : "International"}：{gradeBand.international ?? analysis.international_grade} · {difficulty} · {zh ? "置信度" : "Confidence"} {Math.round(analysis.confidence * 100)}%</p>
    {!compact ? <p className="mt-1 text-[var(--muted-foreground)]">{augmentationNeeded
      ? (zh ? `将自动联网补足：${augmentationReason}` : `Web supplementation will be considered: ${augmentationReason}`)
      : (zh ? "材料充分，默认不联网补足。" : "The material is sufficient; web supplementation is off by default.")}</p> : null}
    {!compact && candidates.length > 0 ? <p className="mt-1 text-[var(--muted-foreground)]">{zh ? "候选概念：" : "Concept candidates: "}{candidates.slice(0, 4).map((item) => String(item.label || item.concept_id || "")).filter(Boolean).join(" · ")}</p> : null}
    {evidence.length > 0 ? <p className="mt-1 text-[var(--muted-foreground)]">{zh ? "识别依据：" : "Evidence: "}{evidence.slice(0, 2).map((item) => `${item.page ? (zh ? `第 ${item.page} 页` : `p. ${item.page}`) : (zh ? "材料片段" : "Material excerpt")} ${item.excerpt}`).join(" · ")}</p> : null}
  </section>;
}

export function GenerationSourceSummary({ result }: { result: GenerateSuiteResult }) {
  const { i18n } = useTranslation();
  const zh = i18n.language?.toLowerCase().startsWith("zh");
  const locale = zh ? "zh" : "en";
  const analysis = result.material?.analysis;
  const augmentation = result.material?.augmentation;
  const sources = result.result.external_sources ?? augmentation?.sources ?? [];
  const used = result.result.external_sources ? sources.length > 0 : Boolean(augmentation?.used);

  if (!analysis && !augmentation) return null;
  return <aside className="rounded-lg border border-[var(--border)] bg-[var(--card)] p-4 text-[12px]" aria-label={zh ? "生成来源" : "Generation sources"}>
    <h2 className="font-medium">{zh ? "材料与补足来源" : "Material and supplemental sources"}</h2>
    {analysis ? <MaterialAnalysisSummary analysis={analysis} compact /> : null}
    <div className="mt-3 text-[var(--muted-foreground)]">
      <p className="inline-flex items-center gap-1.5 font-medium text-[var(--foreground)]"><Globe2 size={14} />{zh ? "联网补足" : "Web supplementation"}</p>
      <p className="mt-1">{used
        ? (zh ? "以下外部来源已用于补足材料，并与上传材料分开标示。" : "These external sources were used to supplement the material and are kept distinct from it.")
        : (zh ? "本次生成未使用外部补足。" : "No external supplementation was used for this generation.")}</p>
      {sources.length > 0 ? <ul className="mt-2 space-y-1.5" role="list">{sources.map((source, index) => {
        const label = source.title || source.url;
        return <li key={`${source.url}-${index}`}><a href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-teal-700 underline underline-offset-4 dark:text-teal-300"><ExternalLink size={12} />{label}</a>{source.retrieved_at ? <span> · {new Date(source.retrieved_at).toLocaleDateString(locale)}</span> : null}</li>;
      })}</ul> : null}
    </div>
  </aside>;
}
