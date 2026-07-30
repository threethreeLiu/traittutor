import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) =>
  readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("sidebars do not expose a duplicate profile or admin entry", () => {
  for (const path of [
    "components/sidebar/UtilitySidebar.tsx",
    "components/sidebar/WorkspaceSidebar.tsx",
  ]) {
    const source = read(path);
    assert.doesNotMatch(source, /ProfileLink/);
    assert.match(source, /LogoutButton/);
  }
});

test("account and privacy remains accessible from consumer settings", () => {
  const source = read("components/settings/SettingsHub.tsx");
  assert.match(source, /href:\s*"\/settings\/account"/);
});
