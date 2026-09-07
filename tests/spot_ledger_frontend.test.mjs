import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const indexHtml = readFileSync(new URL("../frontend/index.html", import.meta.url), "utf8");
const appJs = readFileSync(new URL("../frontend/app.js", import.meta.url), "utf8");
const spotJs = readFileSync(new URL("../frontend/spot_ledger.js", import.meta.url), "utf8");
const stylesCss = readFileSync(new URL("../frontend/styles.css", import.meta.url), "utf8");

function loadSpotLedgerModule() {
  const context = {
    window: {},
    document: {
      querySelector: () => null,
      querySelectorAll: () => [],
    },
  };
  runInNewContext(spotJs, context);
  return context.window.SpotLedger;
}

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
  assert.match(spotJs, /supplier_display_name/);
  assert.match(spotJs, /法定全称/);
  assert.match(spotJs, /历史范围外：不纳入 2026 年补录与异常检查/);
  assert.match(indexHtml, /商品分类/);
  assert.match(indexHtml, /供应商/);
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
  assert.match(spotJs, /pending\$\{queryString\(\{ \.\.\.moduleState\.filters, \.\.\.pageParams \}\)\}/);
  assert.match(spotJs, /sync-errors\$\{queryString\(\{ \.\.\.moduleState\.filters, \.\.\.pageParams \}\)\}/);
  assert.match(indexHtml, /id="spotLedgerPagination"/);
});

test("spot ledger prioritizes supplement status before the contract and keeps sync status later", () => {
  const tableHtml = indexHtml.match(/<table id="spotLedgerTable">([\s\S]*?)<\/table>/)?.[1] || "";
  const headers = [...tableHtml.matchAll(/<th>([^<]+)<\/th>/g)].map((match) => match[1]);

  assert.equal(headers[0], "补录状态");
  assert.equal(headers.at(-1), "同步状态");
  assert.equal(headers[1], "销售合同号");
  assert.ok(spotJs.indexOf("record.supplement_status") < spotJs.indexOf("displayValue(record.AD)"));
  assert.ok(spotJs.indexOf("displayValue(record.AD)") < spotJs.lastIndexOf("record.sync_status"));
});

test("spot ledger visible timestamps are reduced to seconds", () => {
  assert.match(spotJs, /slice\(0, 19\)/);
  assert.doesNotMatch(spotJs, /toISOString\(\)/);
});

test("spot ledger pads minute-only date-times to visible seconds", () => {
  const spotLedger = loadSpotLedgerModule();
  assert.equal(spotLedger.seconds("2026-08-24T09:00"), "2026-08-24 09:00:00");
  assert.equal(spotLedger.seconds("2026-08-24T09:00:12.345Z"), "2026-08-24 09:00:12");
});

test("spot ledger starts with four primary filters and discloses advanced filters on demand", () => {
  assert.match(indexHtml, /id="spotLedgerPrimaryFilters"/);
  assert.match(indexHtml, /id="spotLedgerAdvancedFilters"[^>]*class="[^"]*hidden/);
  assert.match(indexHtml, /id="spotLedgerToggleFiltersBtn"[^>]*aria-expanded="false"/);
  assert.match(indexHtml, /id="spotLedgerAdvancedFilterCount"/);
  assert.match(spotJs, /已启用 \$\{count\} 项高级条件/);
});

test("spot ledger keeps expanded filters, table scrolling and pagination reachable in the bounded workspace", () => {
  const pageBlock = stylesCss.match(/\.spot-ledger-page\s*\{([\s\S]*?)\}/)?.[1] || "";
  const panelBlock = stylesCss.match(/\.spot-ledger-filter-panel,\s*\.spot-ledger-list-panel\s*\{([\s\S]*?)\}/)?.[1] || "";
  const toolbarBlock = stylesCss.match(/\.spot-ledger-toolbar\s*\{([\s\S]*?)\}/)?.[1] || "";
  assert.match(pageBlock, /min-height:\s*0/);
  assert.match(pageBlock, /overflow-y:\s*auto/);
  assert.match(pageBlock, /overflow-x:\s*hidden/);
  assert.match(toolbarBlock, /flex:\s*0\s+0\s+auto/);
  assert.match(panelBlock, /flex:\s*0\s+0\s+auto/);
  assert.match(panelBlock, /overflow:\s*visible/);
  assert.match(stylesCss, /\.spot-ledger-table-wrap\s*\{[\s\S]*?overflow:\s*auto/);
  assert.match(indexHtml, /id="spotLedgerPagination"[\s\S]*?<\/section>/);
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

test("record details split system fields from manual entry slots", () => {
  assert.match(indexHtml, /id="spotLedgerSystemFields" class="spot-ledger-system-list"/);
  assert.match(indexHtml, /id="spotLedgerManualFields" class="spot-ledger-manual-list"/);
  assert.match(spotJs, /function hasDisplayValue\(/);
  assert.match(spotJs, /spotLedgerSystemFields/);
  assert.match(spotJs, /spotLedgerManualFields/);
  assert.doesNotMatch(spotJs, /field\.control\}[^\n]*field\.source_rule/);
  assert.doesNotMatch(spotJs, /record\.source_detail_id[^\n]*来源类型/);
});

test("record details keep blank system fields visible and mark them with a dash", () => {
  const spotLedger = loadSpotLedgerModule();
  const blankField = spotLedger.presentSystemField({}, { code: "AF", name: "销售业务" });
  const populatedField = spotLedger.presentSystemField(
    { AF: "张三" },
    { code: "AF", name: "销售业务" },
  );

  assert.equal(blankField.missing, true);
  assert.equal(blankField.value, "—");
  assert.equal(populatedField.missing, false);
  assert.equal(populatedField.value, "张三");
});

test("record details exclude only manual and deliberately hidden technical fields", () => {
  const spotLedger = loadSpotLedgerModule();
  const fields = [
    { code: "AF", name: "销售业务" },
    { code: "AG", name: "销售执行" },
    { code: "C", name: "合同归属" },
    { code: "A", name: "序号" },
  ];

  assert.equal(
    spotLedger.visibleSystemFields(fields).map((field) => field.code).join(","),
    "AF,AG",
  );
});

test("manual edit labels expose required fields and conditional long-contract rules", () => {
  assert.match(spotJs, /REQUIRED_MANUAL_FIELDS/);
  assert.match(spotJs, /function isRequiredField\(/);
  assert.match(spotJs, /requiredMarker/);
  assert.match(spotJs, /required \? " required"/);
  assert.match(spotJs, /field === "P"/);
  assert.match(spotJs, /long_contract_object/);
});

test("sales type keeps the source value and uses the backend land-goods relation", () => {
  assert.match(spotJs, /record\.is_land_goods/);
  assert.match(spotJs, /sales_type_options/);
  assert.match(indexHtml, /spotLedgerSalesTypeOptions/);
  assert.match(indexHtml, /spot-ledger-strategic-save-20260907/);
  assert.doesNotMatch(indexHtml, /<option>现货-市场加价<\/option>/);
});

test("sync status distinguishes latest task state from current row errors", () => {
  assert.match(
    spotJs,
    /setSyncStatus\(`最近同步任务：\$\{latest\.status\}[\s\S]*当前范围同步异常：\$\{errors\.count \|\| 0\} 条`\)/,
  );
});

function fakeElement(initialValue = "") {
  const listeners = new Map();
  const element = {
    value: initialValue,
    disabled: false,
    textContent: "",
    innerHTML: "",
    open: false,
    dataset: {},
    validity: true,
    resetCalled: false,
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    querySelectorAll() {
      return [];
    },
    dispatchEvent(event) {
      return listeners.get(event.type)?.(event);
    },
    reportValidity() {
      return this.validity;
    },
    reset() {
      this.resetCalled = true;
      this._entries = new Map(this._initialEntries);
    },
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
    },
  };
  return element;
}

function strategyEntries(overrides = {}) {
  return new Map(Object.entries({
    group_name: "大客户组",
    account: "宏源",
    contract: "I2609",
    open_direction: "多",
    opened_at: "2026-08-24T09:00",
    open_quantity: "10",
    quantity_unit: "吨",
    open_price: "800",
    price_currency: "元/吨",
    closed_at: "",
    close_quantity: "",
    close_price: "",
    remark: "",
    ...overrides,
  }));
}

function loadStrategyHarness({ entries = strategyEntries(), post, detail, refresh } = {}) {
  const selectors = new Map();
  const ids = [
    "spotLedgerStrategyForm", "spotLedgerStrategyStatus", "spotLedgerSaveStrategyBtn", "spotLedgerListStatus",
    "spotLedgerPendingCount", "spotLedgerErrorCount", "spotLedgerSyncStatus", "spotLedgerDetail",
    "spotLedgerDetailMeta", "spotLedgerSystemCount", "spotLedgerSystemFields", "spotLedgerManualHint",
    "spotLedgerManualFields", "spotLedgerEditActions", "spotLedgerEditForm", "spotLedgerEditStatus",
    "spotLedgerTableBody", "spotLedgerPagination",
  ];
  ids.forEach((id) => selectors.set(`#${id}`, fakeElement()));
  const form = selectors.get("#spotLedgerStrategyForm");
  form._entries = new Map(entries);
  form._initialEntries = new Map(entries);
  const saveButton = selectors.get("#spotLedgerSaveStrategyBtn");
  const calls = [];
  const createdRecord = {
    record_id: "strategy:test-id",
    record_source_type: "战略套保",
    strategic_status: "未平仓",
    strategic_opened_at: "2026-08-24 09:00:00",
    strategic_contract: "I2609",
  };
  const detailRecord = {
    ...createdRecord,
    strategic_group: "大客户组",
    strategic_account: "宏源",
    strategic_open_direction: "多",
    strategic_open_quantity: 10,
    strategic_quantity_unit: "吨",
    strategic_open_price: 800,
    strategic_price_currency: "元/吨",
    strategic_closed_at: null,
    strategic_close_quantity: null,
    strategic_close_price: null,
    strategic_remark: "战略备注",
    strategic_spot_record_id: null,
  };
  const api = async (path, options = {}) => {
    calls.push({ path, options });
    if (path === "/api/spot-ledger/field-definitions") return { fields: [] };
    if (path === "/api/spot-ledger/pending?limit=1") return { count: 0 };
    if (path === "/api/spot-ledger/sync-errors?limit=1") return { count: 0, runs: [] };
    if (path.startsWith("/api/spot-ledger/records?") || path === "/api/spot-ledger/records") {
      if (refresh) return refresh(path, options);
      return { records: [], count: 0, sales_type_options: [] };
    }
    if (path === "/api/spot-ledger/strategic-hedging") {
      return post ? post(path, options) : { record: createdRecord };
    }
    if (path === "/api/spot-ledger/records/strategy%3Atest-id") {
      if (detail) return detail(path, options);
      return { record: detailRecord, fields: [] };
    }
    throw new Error(`Unexpected API call: ${path}`);
  };
  class FakeFormData {
    constructor(target) {
      this.target = target;
    }
    entries() {
      return this.target._entries.entries();
    }
    forEach(callback) {
      this.target._entries.forEach((value, key) => callback(value, key));
    }
  }
  const context = {
    window: { DataVisualizationComponents: { renderPagination() {} } },
    document: {
      querySelector(selector) {
        return selectors.get(selector) || null;
      },
      querySelectorAll() {
        return [];
      },
    },
    FormData: FakeFormData,
    URLSearchParams,
  };
  runInNewContext(spotJs, context);
  return { module: context.window.SpotLedger, form, saveButton, selectors, calls, api, createdRecord, detailRecord };
}

async function activateStrategyHarness(options = {}) {
  const harness = loadStrategyHarness(options);
  await harness.module.activate({ api: harness.api, token: "test-token", canSensitive: true });
  harness.calls.length = 0;
  return harness;
}

test("strategic form exposes seven groups, direction labels, and the confirmed defaults", () => {
  assert.match(indexHtml, /<select name="group_name" required>[\s\S]*请选择[\s\S]*大客户组[\s\S]*南方组[\s\S]*<\/select>/);
  assert.match(indexHtml, /<input name="account" value="宏源" required>/);
  assert.match(indexHtml, /<select name="open_direction" required>[\s\S]*买入开仓（多）[\s\S]*卖出开仓（空）[\s\S]*<\/select>/);
  assert.match(indexHtml, /<input name="quantity_unit" value="吨"[^>]*readonly[^>]*>/);
});

test("strategic save trims text and refuses a missing opening price without posting", async () => {
  const harness = await activateStrategyHarness({
    entries: strategyEntries({
      account: " 财达 ", contract: " I2609-C-800 ", quantity_unit: " 吨 ", open_price: "  ", price_currency: " 元/吨 ",
    }),
  });

  await harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  assert.equal(harness.calls.filter((call) => call.path === "/api/spot-ledger/strategic-hedging").length, 0);
  assert.equal(harness.selectors.get("#spotLedgerStrategyStatus").textContent, "请填写开仓价格");
  assert.equal(harness.form._entries.get("account"), " 财达 ");
  assert.equal(harness.form.resetCalled, false);
});

test("strategic save trims text before posting a valid payload", async () => {
  const harness = await activateStrategyHarness({
    entries: strategyEntries({
      account: " 财达 ", contract: " I2609-C-800 ", quantity_unit: " 吨 ", open_price: " 800 ", price_currency: " 元/吨 ",
    }),
  });

  await harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  const post = harness.calls.find((call) => call.path === "/api/spot-ledger/strategic-hedging");
  assert.ok(post);
  const payload = JSON.parse(post.options.body);
  assert.equal(payload.account, "财达");
  assert.equal(payload.contract, "I2609-C-800");
  assert.equal(payload.quantity_unit, "吨");
  assert.equal(payload.open_price, 800);
  assert.equal(payload.price_currency, "元/吨");
});

test("strategic save rejects whitespace in required text fields without posting", async () => {
  const harness = await activateStrategyHarness({ entries: strategyEntries({ account: "   " }) });

  await harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  assert.equal(harness.calls.filter((call) => call.path === "/api/spot-ledger/strategic-hedging").length, 0);
  assert.equal(harness.selectors.get("#spotLedgerStrategyStatus").textContent, "请填写账户");
  assert.equal(harness.form.resetCalled, false);
});

test("strategic save submits only once while the request is pending", async () => {
  let resolvePost;
  const pendingPost = new Promise((resolve) => {
    resolvePost = resolve;
  });
  const harness = await activateStrategyHarness({ post: () => pendingPost });

  const first = harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });
  const second = harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  assert.equal(harness.calls.filter((call) => call.path === "/api/spot-ledger/strategic-hedging").length, 1);
  assert.equal(harness.saveButton.disabled, true);
  resolvePost({ record: harness.createdRecord });
  await first;
  await second;
  assert.equal(harness.saveButton.disabled, false);
});

test("strategic save keeps input after a failed request", async () => {
  const harness = await activateStrategyHarness({ post: async () => { throw new Error("保存失败"); } });
  const before = new Map(harness.form._entries);

  await harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  assert.deepEqual(harness.form._entries, before);
  assert.equal(harness.form.resetCalled, false);
  assert.equal(harness.selectors.get("#spotLedgerStrategyStatus").textContent, "保存失败");
});

test("strategic success opens the strategy readback and separates refresh failure from creation failure", async () => {
  const harness = await activateStrategyHarness({
    detail: async () => { throw new Error("详情刷新失败"); },
    refresh: async () => { throw new Error("列表刷新失败"); },
  });

  await harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  const status = harness.selectors.get("#spotLedgerStrategyStatus").textContent;
  assert.match(status, /已保存/);
  assert.match(status, /刷新失败/);
  assert.doesNotMatch(status, /^保存失败$/);
  assert.ok(harness.calls.some((call) => call.path === "/api/spot-ledger/records/strategy%3Atest-id"));
  assert.equal(harness.form.resetCalled, true);
});

test("strategic readback presents strategic fields instead of blank spot fields", async () => {
  const harness = await activateStrategyHarness();

  await harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  const meta = harness.selectors.get("#spotLedgerDetailMeta").innerHTML;
  assert.match(meta, /大客户组/);
  assert.match(meta, /宏源/);
  assert.match(meta, /I2609/);
  assert.match(meta, /买入开仓|多/);
  assert.match(meta, /吨/);
  assert.match(meta, /元\/吨/);
  assert.match(meta, /战略备注/);
  assert.equal(harness.selectors.get("#spotLedgerManualFields").innerHTML, "");
});

test("strategic records remain identifiable in the refreshed ledger list", async () => {
  const strategyRow = {
    record_id: "strategy:test-id",
    record_source_type: "战略套保",
    strategic_group: "大客户组",
    strategic_contract: "I2609",
    strategic_opened_at: "2026-08-24 09:00:00",
    strategic_open_quantity: 10,
    strategic_status: "未平仓",
  };
  const harness = await activateStrategyHarness({
    refresh: async () => ({ records: [strategyRow], count: 1, sales_type_options: [] }),
  });

  await harness.saveButton.dispatchEvent({ type: "click", preventDefault() {} });

  const rows = harness.selectors.get("#spotLedgerTableBody").innerHTML;
  assert.match(rows, /I2609/);
  assert.match(rows, /战略套保/);
});
