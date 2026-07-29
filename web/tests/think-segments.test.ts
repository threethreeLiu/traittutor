import test from "node:test";
import assert from "node:assert/strict";

import {
  hasModelThinking,
  parseModelThinkingSegments,
} from "../lib/think-segments";

test("returns a single text segment when there is no <think> tag", () => {
  const segments = parseModelThinkingSegments("Hello there");
  assert.deepEqual(segments, [{ kind: "text", content: "Hello there" }]);
});

test("splits raw streaming form into think + trailing text", () => {
  const segments = parseModelThinkingSegments(
    "<think>let me reason</think>Hello!",
  );
  assert.equal(segments.length, 2);
  assert.deepEqual(segments[0], {
    kind: "think",
    content: "let me reason",
    closed: true,
  });
  assert.deepEqual(segments[1], { kind: "text", content: "Hello!" });
});

test("captures leading text before the <think> block", () => {
  const segments = parseModelThinkingSegments("intro\n<think>foo</think>tail");
  assert.equal(segments.length, 3);
  assert.equal(segments[0].kind, "text");
  assert.equal(segments[0].content, "intro\n");
  assert.equal(segments[1].kind, "think");
  if (segments[1].kind === "think") {
    assert.equal(segments[1].content, "foo");
    assert.equal(segments[1].closed, true);
  }
  assert.equal(segments[2].kind, "text");
  assert.equal(segments[2].content, "tail");
});

test("recognises post-normalized backtick-wrapped tags", () => {
  const segments = parseModelThinkingSegments(
    "`<think>`reasoning body`</think>`Hello!",
  );
  assert.equal(segments.length, 2);
  assert.deepEqual(segments[0], {
    kind: "think",
    content: "reasoning body",
    closed: true,
  });
  assert.deepEqual(segments[1], { kind: "text", content: "Hello!" });
});

test("treats unclosed <think> as still-streaming", () => {
  const segments = parseModelThinkingSegments(
    "<think>partial reasoning still going",
  );
  assert.equal(segments.length, 1);
  assert.deepEqual(segments[0], {
    kind: "think",
    content: "partial reasoning still going",
    closed: false,
  });
});

test("supports the <thinking> alias", () => {
  const segments = parseModelThinkingSegments("<thinking>foo</thinking>bar");
  assert.equal(segments.length, 2);
  assert.equal(segments[0].kind, "think");
  if (segments[0].kind === "think") {
    assert.equal(segments[0].closed, true);
    assert.equal(segments[0].content, "foo");
  }
  assert.equal(segments[1].kind, "text");
});

test("ignores <think> that lives inside a fenced code block", () => {
  const input = "before\n```\n<think>not real</think>\n```\nafter";
  const segments = parseModelThinkingSegments(input);
  assert.equal(segments.length, 1);
  assert.equal(segments[0].kind, "text");
  assert.equal(segments[0].content, input);
});

test("handles consecutive <think> blocks", () => {
  const segments = parseModelThinkingSegments(
    "<think>first</think>middle<think>second</think>end",
  );
  assert.equal(segments.length, 4);
  assert.equal(segments[0].kind, "think");
  if (segments[0].kind === "think") {
    assert.equal(segments[0].closed, true);
    assert.equal(segments[0].content, "first");
  }
  assert.equal(segments[1].kind, "text");
  assert.equal(segments[1].content, "middle");
  assert.equal(segments[2].kind, "think");
  if (segments[2].kind === "think") {
    assert.equal(segments[2].closed, true);
    assert.equal(segments[2].content, "second");
  }
  assert.equal(segments[3].kind, "text");
  assert.equal(segments[3].content, "end");
});

test("trims surrounding whitespace inside the think block", () => {
  const segments = parseModelThinkingSegments(
    "<think>\n\nlots of newlines\n\n</think>",
  );
  assert.equal(segments.length, 1);
  if (segments[0].kind !== "think") {
    assert.fail("Expected first segment to be a think block");
  }
  assert.equal(segments[0].content, "lots of newlines");
});

test("hasModelThinking detects raw and post-normalized tags", () => {
  assert.equal(hasModelThinking("hello"), false);
  assert.equal(hasModelThinking("<think>x</think>"), true);
  assert.equal(hasModelThinking("`<think>`x`</think>`"), true);
  assert.equal(hasModelThinking("<thinking>x</thinking>"), true);
});
