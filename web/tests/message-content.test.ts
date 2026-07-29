import test from "node:test";
import assert from "node:assert/strict";
import { normalizeMessageContent, truncateText } from "../lib/message-content";

test("normalizeMessageContent joins multimodal content parts", () => {
  assert.equal(
    normalizeMessageContent([
      { type: "text", text: "Here is the diagram" },
      { type: "image" },
    ]),
    "Here is the diagram [image]",
  );
});

test("normalizeMessageContent renders object payloads without React-unsafe objects", () => {
  assert.equal(
    normalizeMessageContent({ type: "custom", value: 42 }),
    '{"type":"custom","value":42}',
  );
});

test("truncateText preserves short strings and ellipsizes long strings", () => {
  assert.equal(truncateText("short", 10), "short");
  assert.equal(truncateText("abcdefghij", 5), "abcde\u2026");
});
