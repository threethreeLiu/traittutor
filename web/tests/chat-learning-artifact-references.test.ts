import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) =>
  readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("home chat can attach generated learning artifacts as reusable references", () => {
  const page = read("app/(workspace)/home/[[...sessionId]]/page.tsx");
  const composer = read("components/chat/home/ChatComposer.tsx");
  const menu = read("components/chat/space/ChatSpaceMenu.tsx");
  const context = read("context/UnifiedChatContext.tsx");
  const ws = read("lib/unified-ws.ts");
  const picker = read("components/chat/LearningArtifactPicker.tsx");

  assert.match(picker, /listLearningPacks/);
  assert.match(picker, /courseware/);
  assert.match(picker, /flashcards/);
  assert.match(picker, /quiz/);
  assert.match(menu, /learning_artifacts/);
  assert.match(composer, /selectedLearningArtifacts/);
  assert.match(composer, /onSelectLearningArtifactPicker/);
  assert.match(composer, /onRemoveLearningArtifact/);
  assert.match(page, /showLearningArtifactPicker/);
  assert.match(page, /learningArtifactReferences: selectedLearningArtifacts/);
  assert.match(context, /learning_artifact_references: effectiveLearningArtifactReferences/);
  assert.match(ws, /learning_artifact_references/);
});
