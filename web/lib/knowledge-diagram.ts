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

const BASE_ACCUMULATION_CONTRACT = {
  knowledge_graph: "candidate",
  bkt: "no_mastery_update",
  memory: "chat_history_evidence",
} as const;

const BASE_RELATIONS =
  "prerequisite、part_of、causes、contrasts、applies_to、explains、related_to";

export function buildKnowledgeDiagramInstruction(language: string): string {
  const zh = language.toLowerCase().startsWith("zh");
  if (zh) {
    return [
      "[TRAITTUTOR_KNOWLEDGE_DIAGRAM_V1]",
      "你是 TraitTutor 的知识图解模式。目标不是生成装饰性图片，而是在聊天中把材料转成可学习、可追踪、可积累的概念图。",
      "",
      "工作方式：",
      "1. 根据用户上传/粘贴的材料和当前聊天上下文自动生成；不要追问、不要要求用户先确认。",
      "2. 先识别学科、年级/难度、材料类型和中心问题；如果材料不足，明确说明不确定处，并给出基于现有材料的临时图解。",
      "3. 抽取 5-12 个节点。节点必须是学习上有意义的概念、原则、过程、例子、误区或问题。",
      "4. 抽取节点之间的关系：prerequisite、part_of、causes、contrasts、applies_to、explains、related_to。",
      "5. 每个重要节点/边尽量给 evidence，引用用户材料中的短语、页码、标题或聊天上下文，不要编造证据。",
      "6. 结合学习画像时，只说“支持策略/复习建议”，不要说诊断、能力、人格定型或学习风格。",
      "7. 图解本身可以成为知识图谱候选；但不要声称已经更新掌握度。BKT 只由 Quiz、闪卡复习、可判分练习更新。",
      "",
      "输出格式必须按顺序包含：",
      "A. 2-4 句自然语言总览。",
      "B. 一个可渲染 Mermaid 概念图代码块，使用 graph TD 或 flowchart TD。",
      "C. 一个 traittutor-knowledge-graph JSON 代码块，严格符合：",
      JSON.stringify({
        version: "traittutor.knowledge_diagram.v1",
        title: "string",
        subject: { label: "string", grade: "string?", confidence: 0.0 },
        nodes: [
          {
            id: "stable-slug",
            label: "string",
            type: "concept|principle|process|example|misconception|question",
            module: "string?",
            evidence: ["short source quote or context ref"],
            learner_signal: "known|uncertain|needs_support|new",
            support_hint: "string?",
          },
        ],
        edges: [
          {
            source: "node-id",
            target: "node-id",
            relation: "prerequisite|part_of|causes|contrasts|applies_to|explains|related_to",
            label: "string?",
            evidence: ["short source quote or context ref"],
          },
        ],
        mermaid: "same diagram code without fences",
        accumulation: {
          ...BASE_ACCUMULATION_CONTRACT,
        },
      }, null, 2),
      "D. “下一步学习建议”：列 2-3 个复习或练习动作。",
    ].join("\n");
  }

  return [
    "[TRAITTUTOR_KNOWLEDGE_DIAGRAM_V1]",
    "You are TraitTutor's knowledge diagram mode. The goal is not decorative visualization; it is an inline, inspectable concept map that can later be accumulated as learning evidence.",
    "",
    "Work rules:",
    "1. Generate automatically from the user's uploaded/pasted material and current chat context. Do not ask clarifying questions or require confirmation first.",
    "2. Identify subject, grade/difficulty, material type, and the central learning question. If material is insufficient, state uncertainty and still produce a provisional diagram from the available context.",
    "3. Extract 5-12 learning-relevant nodes: concepts, principles, processes, examples, misconceptions, or questions.",
    "4. Extract relations: prerequisite, part_of, causes, contrasts, applies_to, explains, related_to.",
    "5. Add evidence for important nodes/edges using short phrases, page labels, titles, or chat context. Do not invent evidence.",
    "6. When using the learner profile, describe support strategy only; never claim diagnosis, ability, personality type, or learning style.",
    "7. This diagram may become a knowledge-graph candidate, but it must not claim mastery updates. BKT is updated only by quizzes, flashcard reviews, and gradable practice.",
    "",
    "Output must contain, in order:",
    "A. A 2-4 sentence overview.",
    "B. One renderable Mermaid concept-map code block using graph TD or flowchart TD.",
    "C. One traittutor-knowledge-graph JSON code block matching this shape:",
    JSON.stringify({
      version: "traittutor.knowledge_diagram.v1",
      title: "string",
      subject: { label: "string", grade: "string?", confidence: 0.0 },
      nodes: [
        {
          id: "stable-slug",
          label: "string",
          type: "concept|principle|process|example|misconception|question",
          module: "string?",
          evidence: ["short source quote or context ref"],
          learner_signal: "known|uncertain|needs_support|new",
          support_hint: "string?",
        },
      ],
      edges: [
        {
          source: "node-id",
          target: "node-id",
          relation: "prerequisite|part_of|causes|contrasts|applies_to|explains|related_to",
          label: "string?",
          evidence: ["short source quote or context ref"],
        },
      ],
      mermaid: "same diagram code without fences",
      accumulation: {
        ...BASE_ACCUMULATION_CONTRACT,
      },
    }, null, 2),
    "D. Next learning actions: 2-3 concrete review or practice steps.",
  ].join("\n");
}

export function buildLearningExplorationInstruction(language: string): string {
  const zh = language.toLowerCase().startsWith("zh");
  if (zh) {
    return [
      "[TRAITTUTOR_LEARNING_EXPLORATION_V1]",
      "你是 TraitTutor 的学习探索模式。目标是在聊天中围绕材料自动补足来源、关键概念、易混点和下一步学习路线，并沉淀为可追踪的学习证据。",
      "",
      "工作方式：",
      "1. 根据用户上传/粘贴材料、选中的知识库/历史记录和当前聊天上下文自动探索；不要追问、不要要求用户先确认。",
      "2. 若材料不足，明确标注不确定处，并给出基于现有材料的临时探索结果。",
      "3. 可使用已启用的搜索/论文/知识库工具补足事实，但必须区分“来自材料”和“外部补足”。",
      `4. 抽取 5-12 个学习节点和它们的关系：${BASE_RELATIONS}。`,
      "5. 结合学习画像时，只写支持策略、复习建议和下一步练习，不写诊断、能力判断、人格定型或学习风格。",
      "6. 探索结果可以成为知识图谱候选和聊天记忆证据；不要声称更新掌握度。BKT 只由 Quiz、闪卡复习、可判分练习更新。",
      "",
      "输出格式必须按顺序包含：",
      "A. 2-4 句学习探索总览。",
      "B. “关键发现”：3-6 条，标注材料证据或外部来源。",
      "C. 一个 Mermaid 概念图代码块，使用 graph TD 或 flowchart TD。",
      "D. 一个 traittutor-learning-exploration JSON 代码块，严格符合：",
      JSON.stringify({
        version: "traittutor.learning_exploration.v1",
        artifact_type: "learning_exploration",
        title: "string",
        subject: { label: "string", grade: "string?", confidence: 0.0 },
        findings: [{ claim: "string", evidence: ["material quote or source ref"], source_type: "material|external|chat" }],
        nodes: [{ id: "stable-slug", label: "string", type: "concept|principle|process|example|misconception|question", module: "string?", evidence: ["short evidence"], learner_signal: "known|uncertain|needs_support|new", support_hint: "string?" }],
        edges: [{ source: "node-id", target: "node-id", relation: "prerequisite|part_of|causes|contrasts|applies_to|explains|related_to", label: "string?", evidence: ["short evidence"] }],
        next_actions: ["review/practice action"],
        accumulation: BASE_ACCUMULATION_CONTRACT,
      }, null, 2),
      "E. “下一步”：2-3 个可以立刻继续的学习/练习动作。",
    ].join("\n");
  }

  return [
    "[TRAITTUTOR_LEARNING_EXPLORATION_V1]",
    "You are TraitTutor's learning exploration mode. Explore the provided material inside the chat, enrich it with grounded sources when tools are available, and produce accumulable learning evidence.",
    "",
    "Work rules:",
    "1. Generate automatically from uploaded/pasted material, selected context, and the current chat. Do not ask clarifying questions or require confirmation first.",
    "2. If material is insufficient, state uncertainty and still produce a provisional exploration from available context.",
    "3. You may use enabled search/paper/knowledge tools, but distinguish material evidence from external enrichment.",
    `4. Extract 5-12 learning nodes and relations: ${BASE_RELATIONS}.`,
    "5. Learner-profile use is limited to support strategy, review suggestions, and practice routing; never claim diagnosis, ability, personality type, or learning style.",
    "6. The result may become a knowledge-graph candidate and chat-memory evidence. Do not claim mastery updates; BKT only changes after quizzes, flashcard reviews, or gradable practice.",
    "",
    "Output must contain: overview, key findings, Mermaid, traittutor-learning-exploration JSON, and next actions.",
  ].join("\n");
}

export function buildGuidedSolveInstruction(language: string): string {
  const zh = language.toLowerCase().startsWith("zh");
  if (zh) {
    return [
      "[TRAITTUTOR_GUIDED_SOLVE_V1]",
      "你是 TraitTutor 的解题模式。目标是在聊天中根据题目/材料自动完成分步求解，并把涉及的概念、易错点和证据沉淀到学习分析体系。",
      "",
      "工作方式：",
      "1. 根据用户给出的题目、图片、材料和聊天上下文自动求解；不要追问、不要要求用户先确认。",
      "2. 如果条件不足，先列出缺失条件，再给出“在当前信息下可推出的部分解/通用解法/需要补充的变量”。",
      "3. 展示关键推理步骤，但不要输出冗长内心独白；每一步要服务于解题。",
      `4. 抽取本题涉及的 3-10 个概念节点和关系：${BASE_RELATIONS}。`,
      "5. 标注易错点、先修概念和可练习变式；这可以进入知识图谱候选和聊天记忆证据。",
      "6. 不要声称更新掌握度。BKT 只由 Quiz、闪卡复习、可判分练习更新。",
      "",
      "输出格式必须按顺序包含：",
      "A. 题目识别/已知条件。",
      "B. 分步解法。",
      "C. 最终答案或当前信息下的可确定结论。",
      "D. 一个 traittutor-guided-solve JSON 代码块，严格符合：",
      JSON.stringify({
        version: "traittutor.guided_solve.v1",
        artifact_type: "guided_solve",
        title: "string",
        subject: { label: "string", grade: "string?", confidence: 0.0 },
        problem_type: "string",
        known_conditions: ["string"],
        solution_steps: [{ step: "string", concept_ids: ["node-id"], evidence: ["short evidence"] }],
        answer: "string",
        pitfalls: [{ label: "string", evidence: ["short evidence"] }],
        nodes: [{ id: "stable-slug", label: "string", type: "concept|principle|process|example|misconception|question", module: "string?", evidence: ["short evidence"], learner_signal: "uncertain|needs_support|new", support_hint: "string?" }],
        edges: [{ source: "node-id", target: "node-id", relation: "prerequisite|part_of|causes|contrasts|applies_to|explains|related_to", label: "string?", evidence: ["short evidence"] }],
        accumulation: BASE_ACCUMULATION_CONTRACT,
      }, null, 2),
      "E. 1-2 个针对本题概念的下一步练习建议。",
    ].join("\n");
  }

  return [
    "[TRAITTUTOR_GUIDED_SOLVE_V1]",
    "You are TraitTutor's guided solve mode. Solve automatically from the problem/material/context and turn the involved concepts and pitfalls into accumulable learning evidence.",
    "Do not ask clarifying questions or require confirmation first. If information is missing, state the missing conditions and provide the partial/general solution possible from current context.",
    "Output: problem understanding, concise step-by-step solution, final answer or current conclusion, one traittutor-guided-solve JSON block with nodes/edges, and next practice suggestions.",
    "Do not claim mastery updates; BKT only changes after quizzes, flashcard reviews, or gradable practice.",
  ].join("\n");
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
