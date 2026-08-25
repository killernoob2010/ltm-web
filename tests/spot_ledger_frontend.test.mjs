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
  assert.match(appJs, /window\.SpotLedger\.activate\(\{\s*api,\s*token: state\.token/);
});

test("spot ledger keeps the complete field contract while presenting pending/errors tabs, filters and export", () => {
  assert.match(spotJs, /moduleState\.fields/);
  assert.match(spotJs, /field\.code/);
  assert.match(spotJs, /待补录/);
  assert.match(spotJs, /同步异常/);
  assert.match(spotJs, /spot-ledger-export/);
  assert.match(spotJs, /strategic-hedging/);
  assert.match(spotJs, /sync_error_summary/);
  assert.match(indexHtml, /id="spotLedgerFilters"/);
  assert.match(indexHtml, /id="spotLedgerPendingTab"/);
  assert.match(indexHtml, /id="spotLedgerErrorsTab"/);
  assert.match(indexHtml, /id="spotLedgerExportBtn"/);
  assert.match(indexHtml, /id="spotLedgerStrategyBtn"/);
  assert.match(indexHtml, /id="spotLedgerSaveStrategyBtn" type="button"/);
  assert.match(spotJs, /spotLedgerSaveStrategyBtn/);
  assert.match(spotJs, /moduleState\.token/);
  assert.doesNotMatch(indexHtml + spotJs, /立即同步|sync-now|手动同步/);
  assert.match(stylesCss, /\.spot-ledger-page/);
  assert.match(spotJs, /pageSize:\s*20/);
  assert.match(spotJs, /DataVisualizationComponents\.renderPagination/);
  assert.match(spotJs, /pageSizes:\s*\[20, 50, 100\]/);
  assert.match(spotJs, /limit:\s*moduleState\.pageSize/);
  assert.match(spotJs, /offset:\s*\(moduleState\.page - 1\) \* moduleState\.pageSize/);
  assert.match(indexHtml, /id="spotLedgerPagination"/);
});

test("spot ledger visible timestamps are reduced to seconds", () => {
  assert.match(spotJs, /slice\(0, 19\)/);
  assert.doesNotMatch(spotJs, /toISOString\(\)/);
});

test("spot ledger starts with four primary filters and discloses advanced filters on demand", () => {
  assert.match(indexHtml, /id="spotLedgerPrimaryFilters"/);
  assert.match(indexHtml, /id="spotLedgerAdvancedFilters"[^>]*class="[^"]*hidden/);
  assert.match(indexHtml, /id="spotLedgerToggleFiltersBtn"[^>]*aria-expanded="false"/);
  assert.match(indexHtml, /id="spotLedgerAdvancedFilterCount"/);
  assert.match(spotJs, /已启用 \$\{count\} 项高级条件/);
});

test("spot ledger view switch reuses the data visualization tab component", () => {
  assert.match(indexHtml, /class="dv-tabs spot-ledger-tabs"[^>]*role="tablist"/);
  assert.match(indexHtml, /id="spotLedgerRecordsTab" class="dv-tab spot-ledger-tab active"/);
  assert.match(indexHtml, /id="spotLedgerPendingTab" class="dv-tab spot-ledger-tab"/);
  assert.match(indexHtml, /id="spotLedgerErrorsTab" class="dv-tab spot-ledger-tab"/);
});

test("record details expose inline manual editing without a second edit mode", () => {
  assert.match(indexHtml, /<dialog id="spotLedgerDetail"[^>]*class="spot-ledger-detail/);
  assert.doesNotMatch(indexHtml, /id="spotLedgerEditBtn"/);
  assert.match(indexHtml, /id="spotLedgerEditForm"[^>]*class="[^"]*spot-ledger-inline-edit-form/);
  assert.match(indexHtml, /id="spotLedgerEditActions"/);
  assert.match(indexHtml, /id="spotLedgerCloseDetailBtn"/);
  assert.match(spotJs, /function renderManualContent\(/);
  assert.match(spotJs, /spotLedgerEditActions/);
  assert.match(spotJs, /spot-ledger-edit-label/);
  assert.doesNotMatch(spotJs, /function showEditForm\(/);
  assert.match(spotJs, /showModal\(\)/);
});

test("record details split populated system fields from manual entry slots", () => {
  assert.match(indexHtml, /id="spotLedgerSystemFields" class="spot-ledger-system-list"/);
  assert.match(indexHtml, /id="spotLedgerManualFields" class="spot-ledger-manual-list"/);
  assert.match(spotJs, /function hasDisplayValue\(/);
  assert.match(spotJs, /spotLedgerSystemFields/);
  assert.match(spotJs, /spotLedgerManualFields/);
  assert.doesNotMatch(spotJs, /field\.control\}[^\n]*field\.source_rule/);
  assert.doesNotMatch(spotJs, /record\.source_detail_id[^\n]*来源类型/);
});

test("manual edit labels expose required fields and conditional long-contract rules", () => {
  assert.match(spotJs, /REQUIRED_MANUAL_FIELDS/);
  assert.match(spotJs, /function isRequiredField\(/);
  assert.match(spotJs, /requiredMarker/);
  assert.match(spotJs, /required \? " required"/);
  assert.match(spotJs, /field === "P"/);
  assert.match(spotJs, /long_contract_object/);
});

test("sync error view keeps the fixture or source-mode label visible", () => {
  assert.match(
    spotJs,
    /view === "errors"[\s\S]*setSyncStatus\(`\$\{result\.runs\[0\]\.status\}[\s\S]*\$\{result\.runs\[0\]\.source_mode/,
  );
});
