import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) =>
  readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("generation shortcut drives the composer mode chip before chat capability", () => {
  const source = read("components/chat/home/ChatComposer.tsx");

  assert.match(source, /activeGenerationShortcut\?\.icon\s*\?\?\s*activeCap\.icon/);
  assert.match(source, /activeGenerationShortcut\?\.label\s*\?\?\s*activeCap\.label/);
  assert.match(source, /selected=\{!generationShortcut && activeCap\.value === cap\.value\}/);
});

test("courseware and flashcards expose generation-specific placeholders", () => {
  const source = read("app/(workspace)/home/[[...sessionId]]/page.tsx");

  assert.match(source, /chatGenerationKind === "courseware"/);
  assert.match(source, /structured courseware/);
  assert.match(source, /chatGenerationKind === "flashcards"/);
  assert.match(source, /active-recall flashcards/);
});
