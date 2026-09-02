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
