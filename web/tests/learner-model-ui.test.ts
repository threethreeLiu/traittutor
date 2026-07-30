import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const source = fs.readFileSync(
  path.join(process.cwd(), "components", "personalization", "LearnerModelApp.tsx"),
  "utf8",
);

test("learner model combines memory evidence and BKT progress", () => {
  assert.match(source, /EVIDENCE MEMORY/);
  assert.match(source, /BKT KNOWLEDGE TRACKING/);
  assert.match(source, /getLearnerEvidence/);
  assert.match(source, /verified_observation_count/);
  assert.match(source, /reviewLoad/);
});

test("learner model uses responsive reflow for mobile and desktop", () => {
  assert.match(source, /px-4 py-6[\s\S]*sm:px-6[\s\S]*lg:px-10/);
  assert.match(source, /grid gap-5 lg:grid-cols-12/);
  assert.match(source, /flex flex-col gap-2 sm:flex-row/);
  assert.match(source, /flex flex-col gap-5 sm:flex-row/);
});
