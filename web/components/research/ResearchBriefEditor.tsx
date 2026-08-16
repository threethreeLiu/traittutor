"use client";

import { useEffect, useId, useState } from "react";
import { listKnowledgeBases, type KnowledgeBaseSummary } from "@/lib/knowledge-api";
import type { ResearchBrief, ResearchSourcePolicy, SaveResearchBriefInput } from "@/lib/research-workspace-api";

type Copy = { zh: string; en: string };
type Tr = (copy: Copy) => string;

interface Props {
  brief: ResearchBrief | null;
  workspaceRevision: number;
  disabled: boolean;
  tr: Tr;
  onSave: (input: SaveResearchBriefInput) => Promise<void>;
}

export default function ResearchBriefEditor({ brief, workspaceRevision, disabled, tr, onSave }: Props) {
  const questionId = useId();
  const objectivesId = useId();
  const constraintsId = useId();
  const policyId = useId();
  const [question, setQuestion] = useState(brief?.question ?? "");
  const [objectives, setObjectives] = useState((brief?.objectives ?? []).join("\n"));
  const [constraints, setConstraints] = useState((brief?.constraints ?? []).join("\n"));
  const [sourcePolicy, setSourcePolicy] = useState<ResearchSourcePolicy>(brief?.source_policy ?? "web");
  const [knowledgeBaseRef, setKnowledgeBaseRef] = useState(brief?.knowledge_base?.resource_id ?? "");
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [validationError, setValidationError] = useState("");

  useEffect(() => {
    if (sourcePolicy === "web") return;
    void listKnowledgeBases()
      .then((items) => setKnowledgeBases(items.filter((item) => item.available !== false)))
      .catch(() => setKnowledgeBases([]));
  }, [sourcePolicy]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion) {
      setValidationError(tr({ zh: "请先填写研究问题。", en: "Enter a research question before saving." }));
      return;
    }
    if (sourcePolicy !== "web" && !knowledgeBaseRef) {
      setValidationError(tr({ zh: "请选择一个有权访问的知识库。", en: "Choose an authorized knowledge base." }));
      return;
    }
    setValidationError("");
    await onSave({
      question: trimmedQuestion,
      objectives: lines(objectives),
      constraints: lines(constraints),
      source_policy: sourcePolicy,
      ...(sourcePolicy === "web" ? {} : { knowledge_base_ref: knowledgeBaseRef }),
      expected_workspace_revision: workspaceRevision,
      idempotency_key: crypto.randomUUID(),
    });
  }

  return (
    <section className="rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 sm:p-5" aria-labelledby="research-brief-heading">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.16em] text-[var(--primary)]">{tr({ zh: "研究简报", en: "Research brief" })}</p>
          <h2 id="research-brief-heading" className="mt-1 text-lg font-semibold">{tr({ zh: "定义研究任务", en: "Define the research task" })}</h2>
        </div>
        {brief ? <span className="rounded-full bg-[var(--muted)] px-2.5 py-1 text-xs text-[var(--muted-foreground)]">{tr({ zh: `版本 ${brief.version}`, en: `Version ${brief.version}` })}</span> : null}
      </div>
      <p className="mt-2 text-sm leading-relaxed text-[var(--muted-foreground)]">{tr({ zh: "写清问题、目标和来源范围。保存后启动的研究会冻结这一版本，不会被后续编辑静默改写。", en: "Set the question, objectives, and source scope. A run freezes the saved version and is not silently changed by later edits." })}</p>

      <form className="mt-5 space-y-4" onSubmit={(event) => void submit(event)} noValidate>
        <Field label={tr({ zh: "研究问题", en: "Research question" })} htmlFor={questionId} required>
          <textarea id={questionId} value={question} onChange={(event) => setQuestion(event.target.value)} rows={4} required disabled={disabled} aria-describedby={validationError ? `${questionId}-error` : undefined} className={fieldClass} placeholder={tr({ zh: "例如：哪些学习策略能稳定改善长期记忆？", en: "For example: Which learning strategies reliably improve long-term retention?" })} />
        </Field>
        {validationError ? <p id={`${questionId}-error`} role="alert" className="text-sm text-[var(--destructive)]">{validationError}</p> : null}

        <div className="grid gap-4 md:grid-cols-2">
          <Field label={tr({ zh: "研究目标（每行一项）", en: "Objectives (one per line)" })} htmlFor={objectivesId}>
            <textarea id={objectivesId} value={objectives} onChange={(event) => setObjectives(event.target.value)} rows={5} disabled={disabled} className={fieldClass} />
          </Field>
          <Field label={tr({ zh: "约束（每行一项）", en: "Constraints (one per line)" })} htmlFor={constraintsId}>
            <textarea id={constraintsId} value={constraints} onChange={(event) => setConstraints(event.target.value)} rows={5} disabled={disabled} className={fieldClass} />
          </Field>
        </div>

        <Field label={tr({ zh: "来源范围", en: "Source scope" })} htmlFor={policyId}>
          <select id={policyId} value={sourcePolicy} onChange={(event) => setSourcePolicy(event.target.value as ResearchSourcePolicy)} disabled={disabled} className="h-11 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] sm:max-w-sm">
            <option value="mixed">{tr({ zh: "网络与知识库", en: "Web and knowledge base" })}</option>
            <option value="web">{tr({ zh: "仅网络来源", en: "Web sources only" })}</option>
            <option value="knowledge_base">{tr({ zh: "仅知识库", en: "Knowledge base only" })}</option>
          </select>
        </Field>

        {sourcePolicy !== "web" ? <Field label={tr({ zh: "知识库", en: "Knowledge base" })} htmlFor={`${policyId}-kb`} required>
          <select id={`${policyId}-kb`} value={knowledgeBaseRef} onChange={(event) => setKnowledgeBaseRef(event.target.value)} disabled={disabled} className="h-11 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] sm:max-w-sm">
            <option value="">{tr({ zh: "选择可访问的知识库", en: "Select an accessible knowledge base" })}</option>
            {knowledgeBases.map((knowledgeBase) => {
              const ref = knowledgeBase.id ?? knowledgeBase.name;
              return <option key={ref} value={ref}>{knowledgeBase.name}{knowledgeBase.assigned ? ` · ${tr({ zh: "已授权", en: "Assigned" })}` : ""}</option>;
            })}
          </select>
        </Field> : null}

        <button type="submit" disabled={disabled || !question.trim()} className="inline-flex min-h-11 items-center justify-center rounded-md bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50">
          {disabled ? tr({ zh: "正在保存…", en: "Saving…" }) : tr({ zh: "保存研究简报", en: "Save research brief" })}
        </button>
      </form>
    </section>
  );
}

function Field({ label, htmlFor, required = false, children }: { label: string; htmlFor: string; required?: boolean; children: React.ReactNode }) {
  return <div><label htmlFor={htmlFor} className="mb-1.5 block text-sm font-medium">{label}{required ? <span aria-hidden="true" className="ml-1 text-[var(--destructive)]">*</span> : null}</label>{children}</div>;
}

function lines(value: string): string[] {
  return value.split("\n").map((entry) => entry.trim()).filter(Boolean);
}

const fieldClass = "min-h-11 w-full resize-y rounded-md border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm leading-relaxed outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] disabled:cursor-not-allowed disabled:opacity-60";
