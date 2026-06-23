import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");

test("swap month diff uses month-diff controls", () => {
  assert.match(appJs, /type === "月差" \|\| type === "掉期月差"/);
});

test("info summary month dropdowns use per-type month options from backend config", () => {
  assert.match(appJs, /month_options_by_type/);
});

test("info summary JavaScript URL is cache busted", () => {
  assert.match(indexHtml, /src="\/static\/app\.js\?v=info-summary-\d+"/);
});
