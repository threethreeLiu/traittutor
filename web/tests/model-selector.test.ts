import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const source = fs.readFileSync(
  path.join(process.cwd(), "components", "chat", "home", "ModelSelector.tsx"),
  "utf8",
);

test("model selector uses the configured model options and groups profiles", () => {
  assert.match(source, /const groups = useMemo/);
  assert.match(source, /<ModelGroup/);
  assert.match(source, /profile_id: option\.profile_id/);
  assert.match(source, /model_id: option\.model_id/);
});

test("model selector directs an empty catalog to model settings", () => {
  assert.match(source, /href="\/settings\/llm"/);
  assert.match(source, /t\("Configure model"\)/);
});
