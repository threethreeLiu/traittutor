import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = process.cwd();
const read = (relativePath: string) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("learner-model and account surfaces follow the active interface language", () => {
  const learnerModel = read("components/personalization/LearnerModelApp.tsx");
  const learnerSubject = read("app/(utility)/profile/learning-model/[subjectId]/page.tsx");
  const account = read("components/settings/AccountPrivacyPage.tsx");

  for (const source of [learnerModel, learnerSubject, account]) {
    assert.match(source, /useTranslation/);
    assert.match(source, /i18n\.language/);
    assert.doesNotMatch(source, /intentionally Chinese-first/);
  }

  assert.match(learnerModel, /My learner model/);
  assert.match(learnerModel, /Teaching preferences/);
  assert.match(learnerSubject, /Back to my learner model/);
  assert.match(learnerSubject, /Learning reflection governance/);
  assert.match(account, /Account & privacy/);
});

test("mobile navigation translates both visible and accessibility labels", () => {
  const navigation = read("components/sidebar/MobileNavigation.tsx");
  const en = JSON.parse(read("locales/en/app.json")) as Record<string, string>;
  const zh = JSON.parse(read("locales/zh/app.json")) as Record<string, string>;

  assert.match(navigation, /useTranslation/);
  assert.match(navigation, /aria-label=\{t\("Main navigation"\)\}/);
  assert.match(navigation, /\{t\(label\)\}/);
  for (const key of ["Home", "Learn", "Assistant", "Learning Space", "Learner Model", "Personas", "Settings", "Main navigation"]) {
    assert.equal(typeof en[key], "string", `missing English locale key: ${key}`);
    assert.equal(typeof zh[key], "string", `missing Chinese locale key: ${key}`);
  }
});

test("persona selection and detail views use the localized preset presentation", () => {
  const picker = read("components/chat/PersonaPicker.tsx");
  const personas = read("components/space/PersonasSection.tsx");

  assert.match(picker, /const selectedDisplay = selected/);
  assert.match(picker, /name: selectedDisplay\?\.name/);
  assert.match(personas, /const viewerDisplay = viewer/);
  assert.match(personas, /\{viewerDisplay\?\.name\}/);
  assert.match(personas, /\{viewerDisplay\.description\}/);
});

test("onboarding uses the global language and translates assessment content", () => {
  const onboarding = read("components/onboarding/OnboardingProvider.tsx");

  assert.match(onboarding, /useAppShell/);
  assert.match(onboarding, /const \{ language, setLanguage \} = useAppShell\(\)/);
  assert.match(onboarding, /englishQuestions/);
  assert.match(onboarding, /englishOptions/);
  assert.doesNotMatch(onboarding, /useState<"zh" \| "en">\("zh"\)/);
});
