import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const spotJs = readFileSync(new URL("../frontend/spot_ledger.js", import.meta.url), "utf8");
const stylesCss = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");

test("spot ledger page is wired into the existing shell and route", () => {
  assert.match(indexHtml, /id="spotLedgerPage"/);
  assert.match(indexHtml, /spot_ledger\.js/);
  assert.match(appJs, /spotLedgerPage/);
  assert.match(appJs, /code === "spot_ledger"/);
  assert.match(appJs, /window\.SpotLedger\.activate/);
});

test("spot ledger renders all field definitions, pending/errors tabs, filters and export", () => {
  assert.match(spotJs, /spotLedgerFieldDefinitions/);
  assert.match(spotJs, /field\.code/);
  assert.match(spotJs, /待补录/);
  assert.match(spotJs, /同步异常/);
  assert.match(spotJs, /spot-ledger-export/);
  assert.match(spotJs, /strategic-hedging/);
  assert.match(indexHtml, /id="spotLedgerFilters"/);
  assert.match(indexHtml, /id="spotLedgerPendingTab"/);
  assert.match(indexHtml, /id="spotLedgerErrorsTab"/);
  assert.match(indexHtml, /id="spotLedgerExportBtn"/);
  assert.match(indexHtml, /id="spotLedgerStrategyBtn"/);
  assert.doesNotMatch(indexHtml + spotJs, /立即同步|sync-now|手动同步/);
  assert.match(stylesCss, /\.spot-ledger-page/);
});

test("spot ledger visible timestamps are reduced to seconds", () => {
  assert.match(spotJs, /slice\(0, 19\)/);
  assert.doesNotMatch(spotJs, /toISOString\(\)/);
});
