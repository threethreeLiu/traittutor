import test from "node:test";
import assert from "node:assert/strict";

import { parseAuthEnabled } from "../lib/api";
import { safeAuthRedirect } from "../lib/auth";

test("parseAuthEnabled is true for accepted truthy flags", () => {
  for (const value of ["true", "1", "yes", "on", "TRUE", " true "]) {
    assert.equal(
      parseAuthEnabled(value),
      true,
      `expected ${JSON.stringify(value)} -> true`,
    );
  }
});

test("parseAuthEnabled is false for falsy / empty / undefined", () => {
  for (const value of ["false", "0", "no", "off", "", undefined]) {
    assert.equal(
      parseAuthEnabled(value),
      false,
      `expected ${JSON.stringify(value)} -> false`,
    );
  }
});

// Guards the failure mode this flag was hardened against: if the Docker
// placeholder is ever served unsubstituted, it must read as "disabled" rather
// than throwing or accidentally enabling auth.
test("parseAuthEnabled treats an unsubstituted Docker placeholder as disabled", () => {
  assert.equal(
    parseAuthEnabled("__NEXT_PUBLIC_AUTH_ENABLED_PLACEHOLDER__"),
    false,
  );
});

test("safeAuthRedirect keeps only in-app paths", () => {
  assert.equal(safeAuthRedirect("/space/traittutor?tab=map"), "/space/traittutor?tab=map");
  assert.equal(safeAuthRedirect("https://example.com"), "/");
  assert.equal(safeAuthRedirect("//example.com"), "/");
  assert.equal(safeAuthRedirect("\\\\example.com"), "/");
  assert.equal(safeAuthRedirect(null, "/home"), "/home");
});
