import assert from "node:assert/strict";
import test from "node:test";

import { personaSearchText, presentPersona } from "@/lib/persona-localization";

const preset = {
  name: "teacher",
  description: "Patient Socratic tutor who guides through questions.",
  source: "admin" as const,
  read_only: true,
};

test("presents built-in personas in Chinese", () => {
  assert.deepEqual(presentPersona(preset, "zh-CN"), {
    name: "苏格拉底导师",
    description: "通过提问和分步引导，帮助你建立理解的耐心导师。",
  });
});

test("keeps user-authored personas unchanged", () => {
  const userPersona = { ...preset, name: "my-study-voice", source: "user" as const, read_only: false };
  assert.equal(presentPersona(userPersona, "zh").name, "my-study-voice");
});

test("searches preset names in both stored and displayed languages", () => {
  const text = personaSearchText(preset, "zh-CN");
  assert.ok(text.includes("teacher"));
  assert.ok(text.includes("苏格拉底导师"));
});
