import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const hub = fs.readFileSync(
  path.join(process.cwd(), "components", "settings", "SettingsHub.tsx"),
  "utf8",
);

test("consumer settings expose only personal learning controls", () => {
  assert.match(hub, /界面外观/);
  assert.doesNotMatch(hub, /界面与语言/);
  assert.match(hub, /我的学习模型/);
  assert.match(hub, /账户与数据/);
  assert.match(hub, /href: "\/settings\/account"/);
  assert.doesNotMatch(hub, /href="\/settings\/models"/);
  assert.doesNotMatch(hub, /href="\/settings\/network"/);
  assert.doesNotMatch(hub, /href="\/settings\/mcp"/);
});
