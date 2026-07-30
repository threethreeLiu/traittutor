import assert from "node:assert/strict";
import test from "node:test";
import {
  buildGuidedSolveInstruction,
  buildKnowledgeDiagramInstruction,
  buildLearningExplorationInstruction,
  parseKnowledgeDiagramPayload,
} from "@/lib/knowledge-diagram";

test("knowledge diagram instruction defines TraitTutor inline graph protocol", () => {
  const prompt = buildKnowledgeDiagramInstruction("zh");

  assert.match(prompt, /TRAITTUTOR_KNOWLEDGE_DIAGRAM_V1/);
  assert.match(prompt, /Mermaid/);
  assert.match(prompt, /traittutor-knowledge-graph/);
  assert.match(prompt, /knowledge_graph/);
  assert.match(prompt, /candidate/);
  assert.match(prompt, /no_mastery_update/);
  assert.match(prompt, /自动生成/);
  assert.match(prompt, /不要追问/);
  assert.doesNotMatch(prompt, /visualize capability/i);
});

test("learning exploration and guided solve are automatic accumulable chat artifacts", () => {
  const exploration = buildLearningExplorationInstruction("zh");
  assert.match(exploration, /TRAITTUTOR_LEARNING_EXPLORATION_V1/);
  assert.match(exploration, /traittutor-learning-exploration/);
  assert.match(exploration, /不要追问/);
  assert.match(exploration, /knowledge_graph/);
  assert.match(exploration, /no_mastery_update/);

  const solve = buildGuidedSolveInstruction("zh");
  assert.match(solve, /TRAITTUTOR_GUIDED_SOLVE_V1/);
  assert.match(solve, /traittutor-guided-solve/);
  assert.match(solve, /不要追问/);
  assert.match(solve, /knowledge_graph/);
  assert.match(solve, /no_mastery_update/);
});

test("knowledge diagram payload parser accepts only the TraitTutor schema", () => {
  const payload = parseKnowledgeDiagramPayload(JSON.stringify({
    version: "traittutor.knowledge_diagram.v1",
    title: "Photosynthesis map",
    subject: { label: "Biology", grade: "grade_7", confidence: 0.8 },
    nodes: [{ id: "light", label: "Light energy", type: "concept", evidence: ["light"] }],
    edges: [{ source: "light", target: "glucose", relation: "causes", evidence: ["energy"] }],
    accumulation: {
      knowledge_graph: "candidate",
      bkt: "no_mastery_update",
      memory: "chat_history_evidence",
    },
  }));

  assert.equal(payload?.title, "Photosynthesis map");
  assert.equal(payload?.nodes.length, 1);
  assert.equal(payload?.edges[0].relation, "causes");
  assert.equal(parseKnowledgeDiagramPayload(JSON.stringify({
    version: "traittutor.guided_solve.v1",
    artifact_type: "guided_solve",
    title: "Slope problem",
    nodes: [{ id: "slope", label: "Slope" }],
    edges: [],
  }))?.artifact_type, "guided_solve");
  assert.equal(parseKnowledgeDiagramPayload("{\"version\":\"other\"}"), null);
});
