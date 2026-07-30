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

test("full-bleed learning tools reflow instead of retaining a desktop split", () => {
  const spaceMain = read("components", "space", "SpaceMain.tsx");
  const masteryPath = read("app", "(utility)", "space", "learning", "page.tsx");

  assert.match(spaceMain, /overflow-y-auto md:overflow-hidden/);
  assert.match(masteryPath, /flex min-h-full flex-col md:h-full md:flex-row/);
  assert.match(masteryPath, /max-h-\[46dvh\].*md:w-64/);
});
