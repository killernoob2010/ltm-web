import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const html = fs.readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const css = fs.readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");
const appJs = fs.readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");

test("risk alert management stacks rules above history without changing shared content grids", () => {
  assert.match(html, /id="riskAlertPage"[\s\S]*class="risk-alert-stack"/);
  assert.match(
    css,
    /\.risk-alert-stack\s*\{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/,
  );
  assert.match(css, /\.content-grid\s*\{[\s\S]*minmax\(0,\s*1\.2fr\)/);
});

test("risk alert form binds recipients automatically to the creator", () => {
  assert.match(html, /<th>设置人<\/th>/);
  assert.doesNotMatch(html, /id="reminderUsers"/);
  assert.doesNotMatch(appJs, /reminder_users:/);
  assert.match(appJs, /item\.creator \|\| "-"/);
});

test("risk alert timestamps render only through seconds", () => {
  assert.match(appJs, /function formatAlertTime\(value\)/);
  assert.match(
    appJs,
    /text\.match\(\/\^\(\\d\{4\}-\\d\{2\}-\\d\{2\}\)\[ T\]\(\\d\{2\}:\\d\{2\}:\\d\{2\}\)\//,
  );
  assert.match(appJs, /formatAlertTime\(item\.alert_time\)/);
  assert.doesNotMatch(appJs, /<td>\$\{item\.alert_time \|\| ""\}<\/td>/);
});
