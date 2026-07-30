"use client";

import { BrainCircuit, GitBranch, Network } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { KnowledgeDiagramPayload } from "@/lib/knowledge-diagram";

const RELATION_LABEL: Record<string, string> = {
  prerequisite: "prerequisite",
  part_of: "part of",
  causes: "causes",
  contrasts: "contrasts",
  applies_to: "applies to",
  explains: "explains",
  related_to: "related",
};

const ARTIFACT_LABEL: Record<string, string> = {
  "traittutor.knowledge_diagram.v1": "Knowledge diagram candidate",
  "traittutor.learning_exploration.v1": "Learning exploration candidate",
  "traittutor.guided_solve.v1": "Guided solve candidate",
};

export default function KnowledgeDiagramCard({
  diagram,
  className = "",
}: {
  diagram: KnowledgeDiagramPayload;
  className?: string;
}) {
  const { t } = useTranslation();
  const nodes = diagram.nodes.slice(0, 12);
  const edges = diagram.edges.slice(0, 16);
  const nodeLabels = new Map(diagram.nodes.map((node) => [node.id, node.label]));

  return (
    <section className={`rounded-2xl border border-[var(--border)] bg-[var(--card)]/70 p-4 shadow-sm ${className}`}>
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[var(--primary)]/10 text-[var(--primary)]">
          <Network className="h-4.5 w-4.5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-sm font-semibold text-[var(--foreground)]">
              {diagram.title}
            </h3>
            <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[10.5px] text-[var(--muted-foreground)]">
              {t(ARTIFACT_LABEL[diagram.version] || "TraitTutor KG candidate")}
            </span>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
            {diagram.subject?.label
              ? `${diagram.subject.label}${diagram.subject.grade ? ` · ${diagram.subject.grade}` : ""}`
              : t("Inline knowledge diagram")}
            {" · "}
            {t("{{count}} nodes", { count: diagram.nodes.length })}
            {" · "}
            {t("{{count}} relations", { count: diagram.edges.length })}
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-[var(--border)]/70 bg-[var(--background)]/55 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
            <BrainCircuit className="h-3.5 w-3.5" />
            {t("Concept nodes")}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {nodes.map((node) => (
              <span
                key={node.id}
                title={node.evidence?.join("\n")}
                className="rounded-full border border-[var(--border)] bg-[var(--muted)]/35 px-2 py-1 text-[11.5px] text-[var(--foreground)]"
              >
                {node.label}
                {node.learner_signal ? (
                  <span className="ml-1 text-[10px] text-[var(--muted-foreground)]">
                    {node.learner_signal}
                  </span>
                ) : null}
              </span>
            ))}
          </div>
        </div>

        <div className="rounded-xl border border-[var(--border)]/70 bg-[var(--background)]/55 p-3">
          <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
            <GitBranch className="h-3.5 w-3.5" />
            {t("Relations")}
          </div>
          <div className="space-y-1.5">
            {edges.map((edge, index) => (
              <div
                key={`${edge.source}-${edge.target}-${edge.relation}-${index}`}
                title={edge.evidence?.join("\n")}
                className="rounded-lg bg-[var(--muted)]/25 px-2 py-1.5 text-[11.5px] leading-relaxed text-[var(--muted-foreground)]"
              >
                <span className="font-medium text-[var(--foreground)]">
                  {nodeLabels.get(edge.source) || edge.source}
                </span>
                <span className="mx-1">→</span>
                <span className="font-medium text-[var(--foreground)]">
                  {nodeLabels.get(edge.target) || edge.target}
                </span>
                <span className="ml-1">
                  ({edge.label || RELATION_LABEL[edge.relation] || edge.relation})
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <p className="mt-3 rounded-xl bg-[var(--primary)]/[0.06] px-3 py-2 text-[11.5px] leading-relaxed text-[var(--muted-foreground)]">
        {t(
          "This diagram is saved in the chat as knowledge-graph candidate evidence. It does not update BKT mastery until a quiz, flashcard review, or gradable practice confirms learning.",
        )}
      </p>
    </section>
  );
}
