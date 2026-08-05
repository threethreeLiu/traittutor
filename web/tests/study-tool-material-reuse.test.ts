import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) =>
  readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("study tools can reuse an existing learning-pack material across courseware flashcards and quiz", () => {
  const workbench = read("components/space/StudyToolWorkbench.tsx");

  assert.match(workbench, /listLearningPacks/);
  assert.match(workbench, /type LearningPack/);
  assert.match(workbench, /function materialFromPack/);
  assert.match(workbench, /selectedPackMaterial/);
  assert.match(workbench, /chooseLearningPack/);
  assert.match(workbench, /const pack = selectedPack \?\? await createLearningPack/);
  assert.match(workbench, /material: materialWithAnalysis/);
  assert.match(workbench, /const analysisSessionId = resolvedAnalysis\.session_id \|\| materialSessionId\.current/);
  assert.match(workbench, /session_id: analysisSessionId/);
  assert.match(workbench, /updateLearningPack\(packIdForTask, \{ generation_id: loaded\.generation_id \}\)/);
  assert.match(workbench, /复用已有学习包/);
  assert.match(workbench, /已复用学习包材料/);
  assert.match(workbench, /同一目标可以持续追加材料/);
});

test("goal links prefill all study tools and keep the same learning pack", () => {
  const workbench = read("components/space/StudyToolWorkbench.tsx");

  assert.match(workbench, /new URLSearchParams\(window\.location\.search\)/);
  assert.match(workbench, /params\.get\("goal"\)/);
  assert.match(workbench, /params\.get\("pack"\)/);
  assert.match(workbench, /setQuizMode\("objective"\)/);
  assert.match(workbench, /autoStartRequestedRef/);
  assert.match(workbench, /void generate\(\)/);
  assert.match(workbench, /goal: isGoalSource/);
  assert.match(workbench, /source_type: "user_goal"/);
});
