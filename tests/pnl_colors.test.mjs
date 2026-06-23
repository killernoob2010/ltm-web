import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const styles = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");

function cssBlock(selector) {
  const match = styles.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `${selector} rule exists`);
  return match[1];
}

test("positive PnL is red and negative PnL is green", () => {
  assert.match(cssBlock(".numeric-up"), /color:\s*var\(--danger\)/);
  assert.match(cssBlock(".numeric-down"), /color:\s*#087443/);
});
