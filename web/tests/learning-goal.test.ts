import assert from "node:assert/strict";
import test from "node:test";

import { isLearningGoalMessage, learningStarterHref, normalizeLearningGoal } from "../lib/learning-goal";

test("learning goal detection recognizes Chinese and English goal statements", () => {
  assert.equal(isLearningGoalMessage("我想学理财"), true);
  assert.equal(isLearningGoalMessage("请教我英语"), true);
  assert.equal(isLearningGoalMessage("I want to learn personal finance"), true);
  assert.equal(isLearningGoalMessage("你能干嘛？"), false);
  assert.equal(isLearningGoalMessage("今天天气怎么样"), false);
});

test("learning starter links preserve the goal and diagnostic contract", () => {
  const href = learningStarterHref("quiz", "  我想学   理财  ", "pack-1");
  assert.match(href, /^\/space\/quiz\?/);
  const params = new URLSearchParams(href.split("?")[1]);
  assert.equal(params.get("goal"), "我想学 理财");
  assert.equal(params.get("pack"), "pack-1");
  assert.equal(params.get("autostart"), "1");
  assert.equal(params.get("mode"), "objective");
  assert.equal(params.get("questions"), "3");
  assert.equal(normalizeLearningGoal("  learn   finance "), "learn finance");
});
