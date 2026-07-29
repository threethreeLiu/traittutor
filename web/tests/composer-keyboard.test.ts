import test from "node:test";
import assert from "node:assert/strict";

import {
  isImeComposing,
  shouldSubmitOnEnter,
  type KeyboardSubmitEventLike,
} from "../lib/composer-keyboard";

const enterEvent: KeyboardSubmitEventLike = { key: "Enter" };

test("shouldSubmitOnEnter allows plain Enter", () => {
  assert.equal(shouldSubmitOnEnter(enterEvent), true);
});

test("shouldSubmitOnEnter ignores Shift+Enter and non-Enter keys", () => {
  assert.equal(shouldSubmitOnEnter({ key: "Enter", shiftKey: true }), false);
  assert.equal(shouldSubmitOnEnter({ key: "Escape" }), false);
});

test("shouldSubmitOnEnter ignores active composition tracked by the textarea", () => {
  assert.equal(shouldSubmitOnEnter(enterEvent, true), false);
});

test("shouldSubmitOnEnter ignores browser IME composition flags", () => {
  assert.equal(shouldSubmitOnEnter({ key: "Enter", isComposing: true }), false);
  assert.equal(
    shouldSubmitOnEnter({ key: "Enter", nativeEvent: { isComposing: true } }),
    false,
  );
});

test("isImeComposing handles process key fallbacks used by some IMEs", () => {
  assert.equal(isImeComposing({ key: "Enter", keyCode: 229 }), true);
  assert.equal(isImeComposing({ key: "Enter", which: 229 }), true);
  assert.equal(
    isImeComposing({ key: "Enter", nativeEvent: { keyCode: 229 } }),
    true,
  );
  assert.equal(
    isImeComposing({ key: "Enter", nativeEvent: { which: 229 } }),
    true,
  );
});
