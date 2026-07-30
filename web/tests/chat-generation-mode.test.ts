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

test("home chat does not expose study generation shortcuts", () => {
  const source = read("app/(workspace)/home/[[...sessionId]]/page.tsx");

  assert.doesNotMatch(source, /onSelectGenerationShortcut=/);
  assert.doesNotMatch(source, /generationShortcut=/);
  assert.doesNotMatch(source, /traittutor_mode/);
});

test("my learning keeps dedicated courseware flashcard and quiz surfaces", () => {
  const source = read("components/space/SpaceDashboard.tsx");

  assert.match(source, /href:\s*"\/space\/courseware"/);
  assert.match(source, /href:\s*"\/space\/flashcards"/);
  assert.match(source, /href:\s*"\/space\/quiz"/);
  assert.doesNotMatch(source, /mastery_path/);
  assert.doesNotMatch(source, /\/space\/learning/);
  assert.doesNotMatch(source, /精通之路|Mastery Path/);
});
