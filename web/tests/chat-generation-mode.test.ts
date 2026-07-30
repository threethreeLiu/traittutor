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

test("home chat keeps analysis shortcuts but hides study artifact shortcuts", () => {
  const page = read("app/(workspace)/home/[[...sessionId]]/page.tsx");
  const composer = read("components/chat/home/ChatComposer.tsx");

  assert.match(page, /onSelectGenerationShortcut=\{handleSelectGenerationShortcut\}/);
  assert.match(page, /generationShortcut=\{chatGenerationKind \?\? null\}/);
  assert.match(page, /traittutor_mode/);
  assert.match(composer, /kind:\s*"guided_solve"/);
  assert.match(composer, /kind:\s*"learning_exploration"/);
  assert.match(composer, /kind:\s*"knowledge_diagram"/);
  assert.doesNotMatch(composer, /kind:\s*"courseware"/);
  assert.doesNotMatch(composer, /kind:\s*"flashcards"/);
  assert.doesNotMatch(composer, /kind:\s*"quiz"/);
});

test("my learning keeps dedicated courseware flashcard and quiz surfaces", () => {
  const source = read("components/space/SpaceDashboard.tsx");

  assert.match(source, /href:\s*"\/space\/courseware"/);
  assert.match(source, /href:\s*"\/space\/flashcards"/);
  assert.match(source, /href:\s*"\/space\/quiz"/);
});
