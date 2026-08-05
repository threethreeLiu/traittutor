import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = process.cwd();
const onboarding = fs.readFileSync(path.join(root, "components", "onboarding", "OnboardingProvider.tsx"), "utf8");
const profile = fs.readFileSync(path.join(root, "app", "(utility)", "space", "traittutor", "page.tsx"), "utf8");

test("onboarding can create an optional Big Five support profile", () => {
  assert.match(onboarding, /fetchTraitQuestions\(\)/);
  assert.match(onboarding, /createTraitProfile\(answers\)/);
  assert.match(onboarding, /Object\.keys\(answers\)\.length !== questions\.questions\.length/);
  assert.match(onboarding, /onComplete\(\)/);
});

test("new learners may defer the assessment instead of being blocked", () => {
  assert.match(onboarding, /listTraitProfiles\(\)/);
  assert.match(onboarding, /role="dialog"/);
  assert.match(onboarding, /onSkip/);
  assert.match(onboarding, /ONBOARDING_DISMISSED_KEY/);
  assert.doesNotMatch(onboarding, /router\.replace/);
});

test("learning profile page no longer contains the assessment form", () => {
  assert.doesNotMatch(profile, /fetchTraitQuestions/);
  assert.doesNotMatch(profile, /createTraitProfile/);
  assert.match(profile, /openAssessment/);
});

test("learning profile includes the inspectable learner-model snapshot", () => {
  assert.match(profile, /LearnerModelSnapshot/);
  const snapshot = fs.readFileSync(
    path.join(root, "components", "personalization", "LearnerModelSnapshot.tsx"),
    "utf8",
  );
  assert.match(snapshot, /BKT knowledge tracking/);
  assert.match(snapshot, /Learning-memory evidence/);
  assert.match(snapshot, /getLearnerOverview/);
  assert.match(snapshot, /getLearnerEvidence/);
});
