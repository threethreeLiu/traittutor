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
  assert.match(workbench, /updateLearningPack\(packIdForTask, \{ generation_id: loaded\.generation_id \}\)/);
  assert.match(workbench, /复用已有材料/);
  assert.match(workbench, /已复用学习包材料/);
  assert.match(workbench, /Flashcard 和 Quiz 会挂回同一个学习包/);
});
