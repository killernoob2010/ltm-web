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
  assert.match(indexHtml, /src="\/static\/app\.js\?v=[^"]+"/);
});

test("info summary exposes historical cache refresh entry", () => {
  assert.match(indexHtml, /refreshInfoCacheBtn/);
  assert.match(appJs, /\/api\/info-summary\/cache\/backfill/);
  assert.match(appJs, /\/api\/info-summary\/cache\/status/);
});

test("info summary auto calculates on page load with in-flight guard", () => {
  assert.match(appJs, /展示已加载，正在自动计算/);
  assert.match(appJs, /calculateAllInfo\(false\)\.catch/);
  assert.match(appJs, /if \(state\.infoSummaryRefreshInFlight\)/);
  assert.match(appJs, /state\.infoSummaryRefreshInFlight = true;/);
  assert.match(appJs, /state\.infoSummaryRefreshInFlight = false;/);
  const start = appJs.indexOf("function startInfoSummaryAutoRefresh()");
  const end = appJs.indexOf("function startMidEventAutoRefresh()", start);
  const body = appJs.slice(start, end);
  assert.doesNotMatch(body, /setInterval/);
});

test("guest info summary refresh still calculates display data", () => {
  const loadStart = appJs.indexOf("async function loadInfoSummary()");
  const loadEnd = appJs.indexOf("async function loadInfoCacheStatus()", loadStart);
  const loadBody = appJs.slice(loadStart, loadEnd);
  assert.doesNotMatch(loadBody, /if \(isGuest\(\)\)/);

  const calcStart = appJs.indexOf("async function calculateAllInfo");
  const calcEnd = appJs.indexOf("function updateInfoStatus", calcStart);
  const calcBody = appJs.slice(calcStart, calcEnd);
  assert.doesNotMatch(calcBody, /isGuest\(\)/);
});

test("info summary visibility refresh triggers guarded calculation", () => {
  const start = appJs.indexOf('document.addEventListener("visibilitychange"');
  const end = appJs.indexOf("async function loadRiskAlert", start);
  const body = appJs.slice(start, end);
  assert.match(body, /calculateAllInfo\(false\)/);
});

test("info summary manual refresh uses one batched request", () => {
  assert.match(appJs, /\/api\/info-summary\/calculate-all/);
  assert.match(appJs, /function buildInfoPayload\(card\)/);
  assert.match(appJs, /const resultsByType = new Map\(\(result\.cards \|\| \[\]\)\.map\(\(item\) => \[item\.info_type, item\]\)\);/);
  assert.match(appJs, /applyInfoResult\(card, item\);/);
  assert.match(appJs, /calculateAllInfoBtn/);
});

test("info summary batch payload uses the selected month controls", () => {
  assert.match(appJs, /month: card\.querySelector\("\.info-month"\)\?\.value \|\| "09"/);
  assert.match(appJs, /month1: card\.querySelector\("\.info-month1"\)\?\.value \|\| undefined/);
  assert.match(appJs, /month2: card\.querySelector\("\.info-month2"\)\?\.value \|\| undefined/);
});

test("info summary stale history is labelled instead of shown as current history", () => {
  assert.match(appJs, /history_stale/);
  assert.match(appJs, /历史缓存截至/);
});

test("info summary cache status includes daily close update state", () => {
  assert.match(appJs, /last_close_cache_update/);
  assert.match(appJs, /自动收盘缓存/);
});

test("risk alert notifications avoid overlapping fast polling", () => {
  assert.match(appJs, /alertNotificationInFlight:\s*false/);
  assert.match(appJs, /if \(state\.alertNotificationInFlight\) return;/);
  assert.match(appJs, /state\.alertNotificationInFlight = true;/);
  assert.match(appJs, /finally\s*\{\s*state\.alertNotificationInFlight = false;/);
  assert.match(appJs, /}, 30000\);/);
});
