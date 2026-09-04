import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const collectorJs = readFileSync(new URL("../frontend/trading_collector.js", import.meta.url), "utf8");
const html = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const css = readFileSync(new URL("../frontend/trading_collector.css", import.meta.url), "utf8");

test("collector is a separate admin-only module with masked fields", () => {
  assert.match(appJs, /trading_collector/);
  assert.match(html, /id="tradingCollectorPage"/);
  assert.match(html, /id="collectorPairingCode"/);
  assert.match(collectorJs, new RegExp("/api/trading-collector/admin/pairing-codes"));
  assert.match(collectorJs, new RegExp("/api/trading-collector/admin/devices"));
  assert.match(collectorJs, new RegExp("/api/trading-collector/fills"));
  assert.match(collectorJs, /account_masked_name/);
  assert.doesNotMatch(collectorJs, /token_hash/);
  assert.doesNotMatch(collectorJs, /password/);
});

test("collector page only permits pairing and revocation administration", () => {
  assert.match(collectorJs, /确认撤销/);
  assert.match(collectorJs, /canManage/);
  assert.match(collectorJs, /duplicate_observation|重复观察/);
  assert.match(css, /collector-device-table/);
});

test("collector page renders option volume and current position states", () => {
  assert.match(html, /collectorOptionVolume/);
  assert.match(html, /collectorCurrentPositions/);
  assert.match(collectorJs, new RegExp("/api/trading-collector/option-volume"));
  assert.match(collectorJs, new RegExp("/api/trading-collector/positions/current"));
  assert.match(collectorJs, /renderOptionVolume/);
  assert.match(collectorJs, /renderCurrentPositions/);
  assert.match(collectorJs, /持仓数据可能已过期/);
  assert.match(collectorJs, /多设备持仓不一致/);
  assert.doesNotMatch(collectorJs, /token_hash|service_role|DATABASE_URL|C:\\\\Users/);
});

test("collector page shows the server policy and bound environment without an environment selector", () => {
  assert.match(html, /collectorCollectionPolicy/);
  assert.match(html, /collectorReconcileBtn/);
  assert.match(collectorJs, new RegExp("/api/trading-collector/admin/collection-policy"));
  assert.match(collectorJs, new RegExp("/api/trading-collector/admin/reconcile"));
  assert.match(collectorJs, /history_start_date/);
  assert.match(collectorJs, /closed_ranges/);
  assert.match(collectorJs, /upload_ranges/);
  assert.match(collectorJs, /environment/);
  assert.doesNotMatch(html, /id="collectorEnvironment"[^>]*<select/);
});

test("collector fills use 20 50 100 server pagination", () => {
  assert.match(collectorJs, /fillPageSize:\s*20/);
  assert.match(collectorJs, /\[20,\s*50,\s*100\]/);
  assert.match(collectorJs, /page=\$\{state\.fillPage\}/);
  assert.match(collectorJs, /page_size=\$\{state\.fillPageSize\}/);
  assert.doesNotMatch(collectorJs, /limit=100/);
  assert.match(html, /id="collectorFillPagination"/);
});
