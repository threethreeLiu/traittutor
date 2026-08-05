import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) => readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("the learning surface is a component path rather than a generator menu", () => {
  const canvas = read("components/learning/LearningCanvas.tsx");
  const home = read("components/learning/LearningPlansHome.tsx");
  const route = read("app/(utility)/space/learning/[packId]/page.tsx");
  const api = read("lib/traittutor-api.ts");

  assert.match(canvas, /学习路径/);
  assert.match(canvas, /当前目标/);
  assert.match(canvas, /知识阶段/);
  assert.match(canvas, /教学动作/);
  assert.match(canvas, /材料证据/);
  assert.match(canvas, /根据刚才的作答/);
  assert.match(canvas, /recordLearningComponentEvent/);
  assert.match(api, /learner_state_updated/);
  assert.match(api, /replanned_plan/);
  assert.match(canvas, /executorKind/);
  assert.match(route, /LearningCanvas/);
  assert.match(home, /进行中的路径/);
  assert.match(home, /待复习/);
  assert.match(home, /学习材料/);
  assert.match(home, /历史产物/);
});

test("legacy generators remain executors and historical artifacts", () => {
  const canvas = read("components/learning/LearningCanvas.tsx");
  const api = read("lib/traittutor-api.ts");
  const courseware = read("app/(utility)/space/courseware/page.tsx");
  const flashcards = read("app/(utility)/space/flashcards/page.tsx");
  const quiz = read("app/(utility)/space/quiz/page.tsx");

  assert.match(canvas, /createTraitTutorGenerationTask/);
  assert.match(canvas, /executor === "assessment" \? "quiz"/);
  assert.match(canvas, /executor === "retrieval" \? "flashcards"/);
  assert.match(api, /artifacts: Record<GenerateKind/);
  assert.match(courseware, /StudyToolWorkbench/);
  assert.match(flashcards, /StudyToolWorkbench/);
  assert.match(quiz, /StudyToolWorkbench/);
});

test("learning surfaces consume the shared theme tokens", () => {
  const canvas = read("components/learning/LearningCanvas.tsx");
  const home = read("components/learning/LearningPlansHome.tsx");
  const globals = read("app/globals.css");

  assert.match(globals, /\.learning-canvas\s*\{/);
  assert.match(globals, /--learning-panel: var\(--card\)/);
  assert.match(globals, /background: var\(--background\)/);
  assert.match(globals, /background: var\(--primary\)/);
  assert.match(canvas, /className="learning-canvas"/);
  assert.match(home, /className="learning-canvas min-h-screen/);
  assert.doesNotMatch(canvas, /(?:bg|text|border)-(?:teal|slate|white|rose|amber)/);
  assert.doesNotMatch(canvas, /#[0-9a-fA-F]{3,8}/);
  assert.doesNotMatch(home, /(?:bg|text|border)-(?:teal|slate|white|rose|amber)/);
  assert.doesNotMatch(home, /#[0-9a-fA-F]{3,8}/);
});

test("the learning canvas owns the full application workspace", () => {
  const canvas = read("components/learning/LearningCanvas.tsx");
  const spaceMain = read("components/space/SpaceMain.tsx");
  const globals = read("app/globals.css");

  assert.match(spaceMain, /FULL_BLEED: string\[\] = \["\/space\/learning"\]/);
  assert.match(globals, /\.learning-canvas\s*\{[\s\S]*h-full[\s\S]*w-full[\s\S]*overflow-hidden/);
  assert.match(globals, /\.learning-canvas__layout\s*\{[\s\S]*min-h-0[\s\S]*w-full[\s\S]*flex-1/);
  assert.doesNotMatch(canvas, /max-w-\[1500px\]/);
  assert.doesNotMatch(canvas, /mx-auto flex w-full max-w-4xl/);
  assert.doesNotMatch(globals, /\.learning-canvas__layout\s*\{[\s\S]{0,160}max-w/);
});

test("entering a learning canvas temporarily collapses the application sidebar", () => {
  const canvas = read("components/learning/LearningCanvas.tsx");

  assert.match(canvas, /readStoredSidebarCollapsed/);
  assert.match(canvas, /setSidebarCollapsed\(true\)/);
  assert.match(canvas, /if \(readStoredSidebarCollapsed\(\)\) setSidebarCollapsed\(wasCollapsed\)/);
});

test("completed component outputs are restored from durable generation ids", () => {
  const canvas = read("components/learning/LearningCanvas.tsx");

  assert.match(canvas, /component\.output_ref/);
  assert.match(canvas, /getTraitTutorGenerationTask\(component\.output_ref\)/);
  assert.match(canvas, /setOutputs\(Object\.fromEntries/);
});
