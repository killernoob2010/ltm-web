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
  const sharedStart = appJs.indexOf("function dateTimeToSecond(value)");
  const sharedEnd = appJs.indexOf("function pnlClass(value)", sharedStart);
  const alertStart = appJs.indexOf("function formatAlertTime(value)");
  const alertEnd = appJs.indexOf("async function loadRiskAlert()", alertStart);
  const functionSource = `${appJs.slice(sharedStart, sharedEnd)}
${appJs.slice(alertStart, alertEnd)}`;
  const formatAlertTime = new Function(`${functionSource}; return formatAlertTime;`)();

  assert.equal(
    formatAlertTime("2026-07-17 06:09:33.023732+00"),
    "2026-07-17 14:09:33",
  );
  assert.equal(
    formatAlertTime("2026-07-17 14:09:33.023732"),
    "2026-07-17 14:09:33",
  );
  assert.equal(
    formatAlertTime("2026-07-17 18:09:33+00"),
    "2026-07-18 02:09:33",
  );
  assert.equal(
    formatAlertTime("2026-07-17T14:09:33+08:00"),
    "2026-07-17 14:09:33",
  );
  assert.match(appJs, /formatAlertTime\(item\.latest_alert_time\)/);
  assert.doesNotMatch(appJs, /<td>\$\{item\.alert_time \|\| ""\}<\/td>/);
});

test("risk alert history groups by rule with server pagination and lazy details", () => {
  assert.match(html, /id="riskAlertOwnerFilter"/);
  assert.match(html, /id="historySummaryList"/);
  assert.match(html, /id="alertHistoryPagination"/);
  assert.doesNotMatch(html, /<tbody id="historyTable"><\/tbody>/);
  assert.match(appJs, /\/api\/risk-alert\/history\/summary/);
  assert.match(appJs, /\/api\/risk-alert\/history\/rules\/\$\{alertId\}/);
  assert.match(appJs, /function renderAlertHistoryPagination/);
  assert.match(appJs, /function loadMoreAlertHistory/);
  assert.match(appJs, /删除.*全部 \$\{item\.alert_count\} 条预警历史/);
});

test("risk alert history static assets use the current versions", () => {
  assert.match(
    html,
    /styles\.css\?v=risk-alert-summary-layout-20260717/,
  );
  assert.match(
    html,
    /app\.js\?v=risk-alert-beijing-time-20260717/,
  );
});

test("risk alert summary keeps compact checkboxes and horizontally readable actions", () => {
  assert.match(
    css,
    /#selectAllAlerts,\s*\.alert-select\s*\{[\s\S]*width:\s*12px;[\s\S]*height:\s*12px;/,
  );
  assert.match(
    css,
    /\.alert-history-summary-main\s*\{[\s\S]*grid-template-columns:[\s\S]*minmax\(112px,\s*auto\);[\s\S]*gap:\s*8px;/,
  );
  assert.match(
    css,
    /\.alert-history-summary-main\s*>\s*\.row-actions\s*\{[\s\S]*flex-direction:\s*column;/,
  );
  assert.match(
    css,
    /\.alert-history-summary-main\s*>\s*\.row-actions\s+button\s*\{[\s\S]*white-space:\s*nowrap;[\s\S]*writing-mode:\s*horizontal-tb;/,
  );
  assert.match(
    html,
    /styles\.css\?v=risk-alert-summary-layout-20260717/,
  );
});
