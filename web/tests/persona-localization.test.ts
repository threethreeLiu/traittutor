import assert from "node:assert/strict";
import test from "node:test";

import { personaSearchText, presentPersona } from "@/lib/persona-localization";

const preset = {
  name: "lesson-designer",
  description: "Artifact designer for courseware, flashcards, quiz, and explanations.",
  source: "admin" as const,
  read_only: true,
};

test("presents built-in personas in Chinese", () => {
  assert.deepEqual(presentPersona(preset, "zh-CN"), {
    name: "讲解设计师",
    description: "按材料分析、SLR 支持和薄弱概念设计课件、闪卡、测验与讲解。",
  });
});

test("keeps user-authored personas unchanged", () => {
  const userPersona = { ...preset, name: "my-study-voice", source: "user" as const, read_only: false };
  assert.equal(presentPersona(userPersona, "zh").name, "my-study-voice");
});

test("searches preset names in both stored and displayed languages", () => {
  const text = personaSearchText(preset, "zh-CN");
  assert.ok(text.includes("lesson-designer"));
  assert.ok(text.includes("讲解设计师"));
});
