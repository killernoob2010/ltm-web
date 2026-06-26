import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");

test("data visualization filters preserve empty selections", () => {
  assert.match(appJs, /function appendMultiSelectParam\(/);
  assert.match(appJs, /selectedValues\.length === 0/);
  assert.match(appJs, /__EMPTY__/);
});

test("integrated data cells without ids are not inline editable", () => {
  assert.match(appJs, /if \(!cell\.dataset\.id\) return;/);
});
