export type KnowledgeDiagramNodeType =
  | "concept"
  | "principle"
  | "process"
  | "example"
  | "misconception"
  | "question";

export type KnowledgeDiagramRelation =
  | "prerequisite"
  | "part_of"
  | "causes"
  | "contrasts"
  | "applies_to"
  | "explains"
  | "related_to";

export interface KnowledgeDiagramNode {
  id: string;
  label: string;
  type?: KnowledgeDiagramNodeType;
  module?: string;
  evidence?: string[];
  learner_signal?: "known" | "uncertain" | "needs_support" | "new";
  support_hint?: string;
}

export interface KnowledgeDiagramEdge {
  source: string;
  target: string;
  relation: KnowledgeDiagramRelation;
  label?: string;
  evidence?: string[];
}

export interface KnowledgeDiagramPayload {
  version:
    | "traittutor.knowledge_diagram.v1"
    | "traittutor.learning_exploration.v1"
    | "traittutor.guided_solve.v1";
  artifact_type?: "knowledge_diagram" | "learning_exploration" | "guided_solve";
  title: string;
  subject?: {
    label: string;
    grade?: string;
    confidence?: number;
  };
  nodes: KnowledgeDiagramNode[];
  edges: KnowledgeDiagramEdge[];
  mermaid?: string;
  accumulation?: {
    knowledge_graph: "candidate";
    bkt: "no_mastery_update";
    memory: "chat_history_evidence";
  };
}

export function parseKnowledgeDiagramPayload(raw: string): KnowledgeDiagramPayload | null {
  try {
    const payload = JSON.parse(raw) as Partial<KnowledgeDiagramPayload>;
    if (
      payload.version !== "traittutor.knowledge_diagram.v1" &&
      payload.version !== "traittutor.learning_exploration.v1" &&
      payload.version !== "traittutor.guided_solve.v1"
    ) return null;
    if (!payload.title || !Array.isArray(payload.nodes) || !Array.isArray(payload.edges)) return null;
    return {
      version: payload.version,
      artifact_type: payload.artifact_type,
      title: String(payload.title),
      subject: payload.subject,
      nodes: payload.nodes,
      edges: payload.edges,
      mermaid: payload.mermaid,
      accumulation: payload.accumulation,
    };
  } catch {
    return null;
  }
}
