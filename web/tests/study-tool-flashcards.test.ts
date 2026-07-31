import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) =>
  readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("flashcard review shows an unsqueezed counter and completion state", () => {
  const workbench = read("components/space/StudyToolWorkbench.tsx");

  assert.match(workbench, /flashcardsComplete/);
  assert.match(workbench, /setFlashcardsComplete\(true\)/);
  assert.match(workbench, /本轮闪卡学习完成/);
  assert.match(workbench, /Flashcard review complete/);
  assert.match(workbench, /min-w-\[5\.25rem\]/);
  assert.match(workbench, /whitespace-nowrap/);
  assert.match(workbench, /tabular-nums/);
});
