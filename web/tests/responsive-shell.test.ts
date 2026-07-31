import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

function read(...parts: string[]) {
  return fs.readFileSync(path.join(process.cwd(), ...parts), "utf8");
}

test("app shells keep a mobile navigation and a scrollable content region", () => {
  const utility = read("app", "(utility)", "layout.tsx");
  const workspace = read("app", "(workspace)", "layout.tsx");
  const mobileNavigation = read("components", "sidebar", "MobileNavigation.tsx");

  assert.match(utility, /h-\[100dvh\][\s\S]*min-w-0[\s\S]*overflow-y-auto/);
  assert.match(workspace, /h-\[100dvh\][\s\S]*min-w-0/);
  assert.match(mobileNavigation, /md:hidden/);
  assert.match(mobileNavigation, /overflow-x-auto/);
});

test("legacy mastery route redirects into the current learner profile", () => {
  const spaceMain = read("components", "space", "SpaceMain.tsx");
  const legacyLearning = read("app", "(utility)", "space", "learning", "page.tsx");

  assert.match(spaceMain, /overflow-y-auto/);
  assert.match(legacyLearning, /redirect\("\/space\/traittutor"\)/);
  assert.doesNotMatch(legacyLearning, /Mastery Path|精通之路/);
});
