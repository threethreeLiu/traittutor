import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (relativePath: string) =>
  readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

test("study tool upload validates files and does not describe PDF uploads as conversion", () => {
  const workbench = read("components/space/StudyToolWorkbench.tsx");
  const api = read("lib/traittutor-api.ts");

  assert.match(workbench, /validateMaterialFile/);
  assert.match(workbench, /MATERIAL_MAX_BYTES\s*=\s*200 \* 1024 \* 1024/);
  assert.match(workbench, /uploadStage === "pdf"/);
  assert.match(workbench, /正在解析 PDF/);
  assert.doesNotMatch(workbench, /正在转为 PDF/);
  assert.match(api, /mime_type:\s*file\.type \|\| ""/);
});
