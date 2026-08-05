import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const source = fs.readFileSync(
  path.join(process.cwd(), "components", "personalization", "LearnerModelApp.tsx"),
  "utf8",
);
const subjectSource = fs.readFileSync(
  path.join(process.cwd(), "app", "(utility)", "profile", "learning-model", "[subjectId]", "page.tsx"),
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

test("learner model exposes governed reflection controls", () => {
  assert.match(source, /REFLECTION GOVERNANCE/);
  assert.match(source, /哪些记忆会影响下一次生成/);
  assert.match(source, /已进入 Compass/);
  assert.match(source, /Candidates/);
  assert.match(source, /reflections\.filter\(\(item\) => item\.status === "candidate"\)\.length/);
  assert.match(source, /确认使用/);
  assert.match(source, /拒绝/);
});

test("learner model keeps concept reflections read-only", () => {
  assert.match(source, /reflection\.status === "candidate" && reflection\.category !== "concept"/);
  assert.match(source, /updateLearnerReflectionStatus\(reflectionId, status\)/);
});

test("learner model shows non-blocking reflection loading errors", () => {
  assert.match(source, /reflectionError/);
  assert.match(source, /学习反思暂不可用/);
  assert.match(source, /其他学习模型信息仍可正常查看/);
});

test("subject learner model fuses Hermes Compass and governed reflections", () => {
  assert.match(subjectSource, /HERMES COMPASS/);
  assert.match(subjectSource, /REFLECTION GOVERNANCE/);
  assert.match(subjectSource, /previewPersonalization\(\{/);
  assert.match(subjectSource, /subject: nextProfile\.subject/);
  assert.match(subjectSource, /updateLearnerReflectionStatus\(reflectionId, status\)/);
  assert.match(subjectSource, /会进入 Compass/);
});
