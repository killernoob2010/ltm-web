# Trading Prototype Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the trading-management right workspace from prototype commit `c1de82b`, keep the existing system left navigation unchanged, then apply the approved import-entry and import-feedback changes.

**Architecture:** Keep the existing authenticated application shell and module routing. Replace only `#tradingManagementPage` and its controller with namespaced ports of the approved prototype components, backed by the existing formal trading APIs; extend the overview response only where the prototype needs grouped data. Implement the original workspace first, verify it, then add the approved import changes as a separate TDD task.

**Tech Stack:** FastAPI, SQLite/PostgreSQL-compatible SQL, vanilla JavaScript, HTML, CSS, Node test runner, pytest, Codex in-app browser.

## Global Constraints

- Prototype source is `/Users/wangjingze/Documents/交易台账自动化处理/personal_workbench/trading_static/` at commit `c1de82b`.
- The system left navigation, menu grouping, permissions, collapse state, colors, and width remain unchanged.
- The right trading workspace ports the prototype structure, components, field order, tab order, drawer, toast, and responsive behavior.
- First restore the prototype; only after that add the approved import-entry and loading changes.
- No real order, cancel, close, exercise, transfer, or funds controls may exist.
- Current-position, close-record, and all-trade tab order is fixed across the three ledger pages.
- Floating PnL and option Greeks remain `待计算`.
- Summary/export keeps the prototype structure but performs no real export.
- Unrelated dirty working-tree files must remain untouched.

---

### Task 1: Lock the approved prototype as a frontend contract

**Files:**
- Modify: `tests/trading_management_frontend.test.mjs`
- Reference read-only: `/Users/wangjingze/Documents/交易台账自动化处理/personal_workbench/trading_static/index.html`
- Reference read-only: `/Users/wangjingze/Documents/交易台账自动化处理/personal_workbench/trading_static/styles.css`
- Reference read-only: `/Users/wangjingze/Documents/交易台账自动化处理/personal_workbench/trading_static/app.js`

**Interfaces:**
- Consumes: approved prototype component vocabulary.
- Produces: failing structural tests that later frontend tasks must satisfy.

- [ ] **Step 1: Add failing structural tests**

```js
test("trading workspace ports the approved prototype components", () => {
  for (const token of [
    "tm-workspace-topbar", "tm-summary-band", "tm-content-grid", "tm-panel-header",
    "tm-filter-summary", "tm-ledger-hero", "tm-drawer", "tm-toast",
  ]) assert.match(html + tradingJs + css, new RegExp(token));
});

test("all three ledgers use the prototype tab order", () => {
  assert.match(tradingJs, /当前持仓[\s\S]*平仓记录[\s\S]*全部交易/);
  assert.doesNotMatch(tradingJs, /业务持仓[\s\S]*业务成交[\s\S]*业务平仓/);
});

test("prototype sections are not replaced by the simplified placeholder layout", () => {
  assert.match(tradingJs, /逐日平仓盈亏趋势/);
  assert.match(tradingJs, /数据质量/);
  assert.match(tradingJs, /业务归属分布/);
  assert.match(tradingJs, /统一输出/);
  assert.doesNotMatch(html, /class="trading-metric-grid"/);
});
```

- [ ] **Step 2: Run the contract test and verify RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL because the current simplified page lacks the prototype workspace classes and sections.

- [ ] **Step 3: Commit only the RED tests**

```bash
git add tests/trading_management_frontend.test.mjs
git commit -m "test: require approved trading prototype structure"
```

---

### Task 2: Restore the prototype workspace shell and visual system

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/trading_management.css`
- Modify: `frontend/app.js`
- Modify: `frontend/trading_management.js`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: existing `activateModule(code)` and `showOnly(page)` routing.
- Produces: `window.TradingManagement.activate(moduleCode, permissions)` rendering the prototype workspace inside the existing application shell.

- [ ] **Step 1: Replace the simplified HTML shell with the namespaced prototype shell**

```html
<section id="tradingManagementPage" class="page hidden tm-page">
  <header class="tm-workspace-topbar">
    <div><h1 id="tmPageTitle"></h1><p id="tmPageSubtitle"></p></div>
    <div class="tm-top-actions">
      <select id="tmAccountFilter" aria-label="账户"></select>
      <select id="tmDateFilter" aria-label="交易日期"></select>
      <button id="tmDataInfoButton" class="tm-ghost-button" type="button">数据说明</button>
    </div>
  </header>
  <div id="tmLoadingState" class="tm-loading-state">正在读取交易数据…</div>
  <div id="tmErrorState" class="tm-error-state hidden"></div>
  <div id="tmContent" class="hidden">
    <section id="tmOverviewView" class="tm-view"></section>
    <section id="tmPositionsView" class="tm-view hidden"></section>
    <section id="tmJunnengView" class="tm-view hidden"></section>
    <section id="tmOptionsView" class="tm-view hidden"></section>
    <section id="tmExportView" class="tm-view hidden"></section>
  </div>
</section>
<div id="tmDrawerBackdrop" class="tm-drawer-backdrop hidden"></div>
<aside id="tmDrawer" class="tm-drawer" aria-hidden="true">
  <div class="tm-drawer-header">
    <div><small id="tmDrawerKicker">详细信息</small><h2 id="tmDrawerTitle">数据说明</h2></div>
    <button id="tmCloseDrawer" type="button" aria-label="关闭">×</button>
  </div>
  <div id="tmDrawerBody" class="tm-drawer-body"></div>
</aside>
<div id="tmToast" class="tm-toast hidden" role="status"></div>
```

- [ ] **Step 2: Port prototype CSS under `tm-` names**

Copy each prototype rule without changing values, renaming global classes such as `.summary-band`, `.content-grid`, `.panel`, `.drawer`, and `.toast` to `.tm-summary-band`, `.tm-content-grid`, `.tm-panel`, `.tm-drawer`, and `.tm-toast`. Do not redefine project-global `body`, `.sidebar`, `.workspace`, `.topbar`, `.panel`, `table`, `button`, or `.hidden`.

```css
.tm-page { color: #172033; --tm-blue:#2359c4; --tm-border:#dfe5ed; }
.tm-workspace-topbar { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; margin-bottom:22px; }
.tm-summary-band { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); background:#fff; border:1px solid var(--tm-border); border-radius:10px; overflow:hidden; }
.tm-content-grid { display:grid; grid-template-columns:minmax(0,1.65fr) minmax(290px,.75fr); gap:16px; margin-top:16px; }
.tm-drawer { position:fixed; top:0; right:0; z-index:51; width:min(460px,100vw); height:100vh; transform:translateX(102%); overflow:auto; }
.tm-drawer.open { transform:translateX(0); }
```

- [ ] **Step 3: Hide only the existing global right header while a trading module is active**

Add `id="globalTopbar"` to the existing header and toggle it in `activateModule`:

```js
const tradingCodes = ["trading_overview", "trading_positions", "trading_sh_junneng", "trading_options", "trading_export"];
globalTopbar.classList.toggle("hidden", tradingCodes.includes(code));
```

The left navigation DOM and CSS must not change.

- [ ] **Step 4: Add prototype view mapping and common drawer/toast helpers**

```js
const TM_VIEW_COPY = {
  trading_overview: ["交易总览", "全量文华交易的领导驾驶舱", "overview"],
  trading_positions: ["持仓与交易", "查询、核验和归类全部真实交易事实", "positions"],
  trading_sh_junneng: ["上海钧能台账", "钢材套保业务的专用视图", "junneng"],
  trading_options: ["期权台账", "期权持仓、成交与风险视图", "options"],
  trading_export: ["汇总与导出", "统一预览并输出业务台账与交易模板", "export"],
};

function openDrawer(kicker, title, body) {
  $("#tmDrawerKicker").textContent = kicker;
  $("#tmDrawerTitle").textContent = title;
  $("#tmDrawerBody").innerHTML = body;
  $("#tmDrawerBackdrop").classList.remove("hidden");
  $("#tmDrawer").classList.add("open");
  $("#tmDrawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() {
  $("#tmDrawerBackdrop").classList.add("hidden");
  $("#tmDrawer").classList.remove("open");
  $("#tmDrawer").setAttribute("aria-hidden", "true");
}
function showToast(message) {
  const toast = $("#tmToast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.setTimeout(() => toast.classList.add("hidden"), 2600);
}
```

- [ ] **Step 5: Run structural tests**

Run: `node --test tests/trading_management_frontend.test.mjs && node --check frontend/trading_management.js`

Expected: prototype shell test PASS; page-specific tests may remain RED until later tasks.

- [ ] **Step 6: Commit the restored shell**

```bash
git add frontend/index.html frontend/trading_management.css frontend/app.js frontend/trading_management.js tests/trading_management_frontend.test.mjs
git commit -m "feat: restore trading prototype workspace shell"
```

---

### Task 3: Restore the overview data and page

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `frontend/trading_management.js`
- Test: `tests/test_trading_management.py`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: active trading facts and `FactFilters`.
- Produces: `build_overview()` with `daily_summary`, `contract_summary`, `quality`, and `business_distribution` in addition to existing summaries.

- [ ] **Step 1: Add failing backend tests for prototype aggregates**

```python
result = trading_management.build_overview(trading_management.FactFilters(page=1, page_size=20))
assert result["daily_summary"]["20260630"]["trade_count"] == 2
assert result["contract_summary"][0]["contract"] == "rb2610"
assert result["quality"]["close_fee_coverage"] == {"matched": 1, "total": 1}
assert "classified" in result["business_distribution"]
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k prototype_overview -q`

Expected: FAIL with missing aggregate keys.

- [ ] **Step 3: Implement SQL-backed grouped aggregates**

Use active-batch facts and the same filters as the page. Build `daily_summary` from grouped trade dates and close dates, `contract_summary` from active trade facts grouped by contract, fee coverage from `trading_close_facts.fee_status`, and classification counts from active trade identities left-joined to assignments. Preserve existing `trades`, `closes`, `positions`, and `data_status` keys:

```python
return {
    "trades": trades["summary"],
    "closes": closes["summary"],
    "positions": positions_summary,
    "daily_summary": daily_summary,
    "contract_summary": contract_summary,
    "quality": {"close_fee_coverage": {"matched": matched, "total": total}},
    "business_distribution": {"classified": classified, "unclassified": unclassified},
    "data_status": data_status,
}
```

- [ ] **Step 4: Port `period-bar`, `summary-band`, trend chart, quality, business distribution, and active-contract renderers**

Create `renderOverviewView(data)`, `renderSummaryBand(data)`, `renderProfitChart(data.daily_summary)`, `renderQuality(data.quality)`, `renderBusinessDistribution(data.business_distribution)`, and `renderActiveContracts(data.contract_summary)`. Concatenate them in this exact order: period bar, summary band, trend/quality two-column grid, business distribution/active contracts two-column grid. Replace unavailable floating values with `待计算` in the original component location.

- [ ] **Step 5: Run backend and frontend tests**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'overview or real_sample' -q && node --test tests/trading_management_frontend.test.mjs`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/trading_management.py frontend/trading_management.js tests/test_trading_management.py tests/trading_management_frontend.test.mjs
git commit -m "feat: restore trading prototype overview"
```

---

### Task 4: Restore the three fact-ledger tabs before modifying import

**Files:**
- Modify: `frontend/trading_management.js`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: `/facts/positions`, `/facts/closes`, `/facts/trades`.
- Produces: prototype `当前持仓 / 平仓记录 / 全部交易` page with filters, summaries, selection, tables, pagination, and detail drawer.

- [ ] **Step 1: Add failing tests for tab order, filters, selection, and page sizes**

```js
assert.match(tradingJs, /当前持仓[\s\S]*平仓记录[\s\S]*全部交易/);
for (const label of ["资产类型", "方向", "开平", "归类状态", "20", "50", "100", "选择当前页", "选择全部筛选结果"]) {
  assert.match(tradingJs, new RegExp(label));
}
```

- [ ] **Step 2: Verify RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL on prototype controls missing from the simplified page.

- [ ] **Step 3: Port renderers and wire them to formal APIs**

Implement these exact units:

```js
async function renderPositionsView() {
  const tabPath = {positions:"positions", closes:"closes", trades:"trades"}[tm.factsTab];
  const data = await api(`/api/trading-management/facts/${tabPath}?${factQuery()}`);
  $("#tmPositionsView").innerHTML = `${renderFactTabs()}${renderFactFilters()}${renderFilterSummary(data.summary)}${renderFactTable(data.items)}${renderPagination(data)}`;
  wireFactActions();
}
```

`renderFactTabs()` outputs `当前持仓`, `平仓记录`, `全部交易` in that order. `renderFactFilters()` outputs contract search, asset type, side, open/close, classification, from/to dates. `renderFactTable()` uses `identity_id` as the stable row key and the prototype field order. `openFactDetail()` sends the selected row to the shared drawer.

- [ ] **Step 4: Verify the restored page before import changes**

Run: `node --test tests/trading_management_frontend.test.mjs && node --check frontend/trading_management.js`

Expected: PASS.

- [ ] **Step 5: Commit the unmodified prototype page restoration**

```bash
git add frontend/trading_management.js tests/trading_management_frontend.test.mjs
git commit -m "feat: restore prototype facts ledger"
```

---

### Task 5: Apply the approved import-entry and loading changes

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/trading_management.css`
- Modify: `frontend/trading_management.js`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: existing `/imports/preview` and `/imports/{preview_batch_id}/confirm`.
- Produces: one import entry in the positions view and a real single-column prototype drawer flow.

- [ ] **Step 1: Add failing tests for entry scope and processing states**

```js
test("three-table import exists only in the positions renderer", () => {
  const positions = sourceSlice(tradingJs, "function renderPositionsView", "function renderJunnengView");
  assert.match(positions, /导入三表/);
  assert.doesNotMatch(html, /id="tradingImportBtn"/);
});

test("import drawer exposes explicit preview and confirmation loading", () => {
  assert.match(tradingJs, /正在预检三表，请稍候/);
  assert.match(tradingJs, /正在覆盖导入并建立事实匹配，请勿关闭窗口/);
  assert.match(tradingJs, /setImportBusy\(true/);
  assert.match(tradingJs, /finally[\s\S]*setImportBusy\(false/);
});
```

- [ ] **Step 2: Verify RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL because current import is a shared toolbar button and has no drawer busy state.

- [ ] **Step 3: Implement import state and reset invalid previews**

```js
function setImportBusy(busy, message = "") {
  ["#tmImportAccount", "#tmTradeFile", "#tmCloseFile", "#tmPositionFile",
   "#tmImportCancel", "#tmImportPreview", "#tmImportConfirm"]
    .forEach((selector) => { const el = $(selector); if (el) el.disabled = busy; });
  $("#tmImportProgress").innerHTML = busy ? `<span class="spinner"></span><span>${escapeHtml(message)}</span>` : "";
}

function invalidateImportPreview() {
  tm.importPreviewId = null;
  $("#tmImportConfirm")?.classList.add("hidden");
  $("#tmImportResult").textContent = "文件已变化，请重新预检。";
}
```

- [ ] **Step 4: Wrap preview and confirm in explicit loading/error flows**

```js
async function confirmImport() {
  setImportBusy(true, "正在覆盖导入并建立事实匹配，请勿关闭窗口");
  try {
    const result = await api(`/api/trading-management/imports/${tm.importPreviewId}/confirm`, {method:"POST"});
    closeDrawer();
    showToast(`导入完成：成交 ${result.counts.trade}，平仓 ${result.counts.close}，持仓 ${result.counts.position}`);
    await refresh();
  } catch (error) {
    $("#tmImportResult").textContent = `导入失败：${error.message}`;
    throw error;
  } finally {
    setImportBusy(false);
  }
}
```

- [ ] **Step 5: Run tests and browser-check the complete drawer**

Run: `node --test tests/trading_management_frontend.test.mjs && node --check frontend/trading_management.js`

Expected: PASS; drawer remains single-column and confirmation visibly enters loading.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/trading_management.css frontend/trading_management.js tests/trading_management_frontend.test.mjs
git commit -m "fix: scope and clarify trading imports"
```

---

### Task 6: Restore Shanghai Junneng and option ledgers

**Files:**
- Modify: `frontend/trading_management.js`
- Modify: `backend/app/trading_management.py`
- Test: `tests/trading_management_frontend.test.mjs`
- Test: `tests/test_trading_management.py`

**Interfaces:**
- Consumes: `/business/junneng/{tab}`, `/business/options/{tab}` and rematch APIs.
- Produces: prototype ledger hero, summaries, filters, fixed tab order, tables, pagination, and drawer actions.

- [ ] **Step 1: Add failing tests for both prototype ledger contracts**

```js
test("business ledgers preserve prototype structure and fields", () => {
  assert.match(tradingJs + css, /tm-ledger-hero/);
  assert.match(tradingJs, /当前持仓[\s\S]*平仓记录[\s\S]*全部交易/);
  assert.match(tradingJs, /RB\/HC 待归属/);
  for (const label of ["标的", "看涨\/看跌", "行权价", "Delta", "Gamma", "Theta", "Vega"]) {
    assert.match(tradingJs, new RegExp(label));
  }
});
```

- [ ] **Step 2: Verify RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL on missing original ledger structure.

- [ ] **Step 3: Port the Shanghai Junneng renderer**

Implement `renderJunnengView()` using `/business/junneng/${tm.junnengTab}`. Output `tm-ledger-hero`, the original three/four-column ledger summary, filters, fixed tabs, filter summary, table, and pagination. Put rematch candidate selection, impact preview, confirm, and restore-default inside `openDrawer("调整业务开平关系", contract, body)`.

- [ ] **Step 4: Port the option renderer**

Implement `renderOptionsView()` using `/business/options/${tm.optionsTab}`. Default to all options, derive `{underlying, kind, strike}` with the prototype `optionAnatomy(contract)` regex, and render Delta/Gamma/Theta/Vega columns as `待计算`.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'business_views or rematch or option' -q && node --test tests/trading_management_frontend.test.mjs`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/trading_management.js backend/app/trading_management.py tests/trading_management_frontend.test.mjs tests/test_trading_management.py
git commit -m "feat: restore prototype business ledgers"
```

---

### Task 7: Restore the export workspace without enabling exports

**Files:**
- Modify: `frontend/trading_management.js`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: overview and business counts already loaded by the controller.
- Produces: prototype `统一输出`, `输出前检查`, and `最近输出版本` structures with disabled actions.

- [ ] **Step 1: Add a failing export-structure test**

```js
for (const label of ["统一输出", "完整交易台账", "上海钧能台账", "期权台账", "自定义明细", "输出前检查", "最近输出版本"]) {
  assert.match(tradingJs, new RegExp(label));
}
assert.match(tradingJs, /功能暂未开放/);
```

- [ ] **Step 2: Verify RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL because the current page is one centered placeholder.

- [ ] **Step 3: Port the prototype export structures**

Implement `renderExportView()` with exactly four output rows (`完整交易台账`, `上海钧能台账`, `期权台账`, `自定义明细`), the four original pre-output checks, and `最近输出版本`. Every action button has `disabled` and text `功能暂未开放`; no handler calls an export endpoint.

- [ ] **Step 4: Verify and commit**

```bash
node --test tests/trading_management_frontend.test.mjs
git add frontend/trading_management.js tests/trading_management_frontend.test.mjs
git commit -m "feat: restore prototype export workspace"
```

---

### Task 8: Side-by-side visual QA, full regression, and Staging release

**Files:**
- Modify after deploy: `README.md`
- Modify after deploy: `版本更新记录.md`.

**Interfaces:**
- Consumes: completed frontend and backend.
- Produces: evidence that the existing left menu is unchanged and each right workspace matches the prototype except approved differences.

- [ ] **Step 1: Run complete automated verification**

```bash
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
node --check frontend/app.js
node --check frontend/trading_management.js
git diff --check
```

Expected: all tests PASS.

- [ ] **Step 2: Start a temporary local app with an isolated SQLite database**

```bash
DATABASE_URL=sqlite:////tmp/trading-prototype-fidelity.db \
  .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001
```

- [ ] **Step 3: Compare all five pages against `c1de82b`**

Use the in-app browser at the same viewport. Capture one screenshot per prototype page and one per implemented page. Check:

- unchanged system left navigation;
- title/subtitle and top actions;
- section order and grid ratios;
- tab/filter/field/pagination order;
- drawer placement and responsive behavior;
- only approved differences are present.

Do not accept “similar”; fix any unexplained visual difference before continuing.

- [ ] **Step 4: Verify import behavior locally**

Open import only from `持仓与交易`. Confirm it is absent from the other four pages. Select three non-sensitive test workbooks, run preview, then confirm against the isolated database; observe both loading messages and disabled controls.

- [ ] **Step 5: Commit final corrections and push Staging**

```bash
git push origin staging
```

- [ ] **Step 6: Verify Render Staging**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>` in the in-app browser. Verify the new asset versions, all five pages, the import drawer, and zero console errors. Do not upload the user's real workbooks to Staging without separately confirmed authorization.

- [ ] **Step 7: Update release documentation after successful deploy**

Record commits, prototype source `c1de82b`, approved differences, automated test counts, screenshot comparison, Staging URL, database impact, and rollback point in `版本更新记录.md`.

- [ ] **Step 8: Final commit and push**

```bash
git add README.md 版本更新记录.md
git commit -m "docs: record prototype fidelity staging release"
git push origin staging
```
