import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

function read(...parts: string[]) {
  return fs.readFileSync(path.join(process.cwd(), ...parts), "utf8");
}

test("consumer detail pages use the shared accessible back link", () => {
  const learnerModel = read("components", "personalization", "LearnerModelApp.tsx");
  const subject = read("app", "(utility)", "profile", "learning-model", "[subjectId]", "page.tsx");
  const settings = read("components", "settings", "SettingsMain.tsx");

  assert.match(learnerModel, /PageBackLink href="\/settings"/);
  assert.match(subject, /PageBackLink href="\/profile\/learning-model"/);
  assert.match(settings, /PageBackLink href="\/settings"/);
});
