# Trading Layout Performance Chart Adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make trading filters compact, speed up fact-tab switching, reorganize business-ledger summaries, and render real daily close-PnL data on the overview.

**Architecture:** Extend the existing overview response with an active-version daily series, then render it through a small SVG helper. Keep the three existing fact APIs and add an in-memory frontend request cache with background prefetch and explicit invalidation after mutations. Preserve the approved prototype component set and change only the confirmed layout points.

**Tech Stack:** FastAPI, SQLite/PostgreSQL compatibility helpers, vanilla JavaScript, HTML/CSS, Node test runner, pytest, Browser-based Staging QA.

## Global Constraints

- Keep the existing system left navigation unchanged.
- At content width 960px or wider, trading filters must remain on one row.
- Do not calculate floating PnL or Greeks; display `待计算` only in the approved locations.
- Overview daily PnL must use active close facts only.
- Production is out of scope; deploy and mutate data only in Staging.
- Complete verification requires automated tests plus real Staging browser/API checks.

---

### Task 1: Active Daily Close-PnL Series

**Files:**
- Modify: `backend/app/trading_management.py:860-910`
- Test: `tests/test_trading_management.py`

**Interfaces:**
- Consumes: `build_overview(filters: FactFilters)` and active `trading_close_facts`.
- Produces: overview field `daily_close_pnl: list[{date: str, fact_close_pnl: float}]`.

- [ ] **Step 1: Write failing tests for daily aggregation and overwrite isolation**

```python
def test_overview_returns_daily_close_pnl_from_active_facts(tmp_path, monkeypatch):
    preview = create_preview_batch(tmp_path, monkeypatch)
    confirmed = trading_management.confirm_trading_import(preview["preview_batch_id"], actor="tester")
    trading_management.match_imported_facts(confirmed["batch_id"])
    overview = trading_management.build_overview(FactFilters())
    assert overview["daily_close_pnl"] == [{"date": "20260630", "fact_close_pnl": 1200}]

def test_overview_daily_close_pnl_ignores_superseded_batch(tmp_path, monkeypatch):
    first_preview = create_preview_batch(tmp_path, monkeypatch)
    first = trading_management.confirm_trading_import(first_preview["preview_batch_id"], actor="tester")
    trading_management.match_imported_facts(first["batch_id"])
    second_preview = trading_management.preview_trading_import(
        account_id=1,
        trade_path=tmp_path / "trades.xlsx",
        close_path=tmp_path / "closes.xlsx",
        position_path=tmp_path / "positions.xlsx",
        actor="tester2",
    )
    second = trading_management.confirm_trading_import(second_preview["preview_batch_id"], actor="tester2")
    trading_management.match_imported_facts(second["batch_id"])
    overview = trading_management.build_overview(FactFilters())
    assert overview["daily_close_pnl"] == [{"date": "20260630", "fact_close_pnl": 1200}]
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'daily_close_pnl' -q`

Expected: FAIL because `daily_close_pnl` is absent.

- [ ] **Step 3: Add one grouped active-fact query**

```python
daily_rows = db._exec(cur, """
    SELECT cf.close_date AS date, SUM(cf.fact_close_pnl) AS fact_close_pnl
    FROM trading_close_facts cf
    JOIN trading_import_batches b ON b.id = cf.batch_id AND b.status = 'active'
    WHERE (? = '' OR LOWER(cf.contract) LIKE LOWER(?))
      AND (? = '' OR cf.open_side = ?)
      AND (? = '' OR cf.asset_type = ?)
      AND (? = '' OR cf.close_date >= ?)
      AND (? = '' OR cf.close_date <= ?)
    GROUP BY cf.close_date ORDER BY cf.close_date
""", (
    filters.contract, f"%{filters.contract}%", filters.direction, filters.direction,
    filters.asset_type, filters.asset_type, filters.start_date, filters.start_date,
    filters.end_date, filters.end_date,
)).fetchall()
```

Return normalized floats under `daily_close_pnl` without changing existing overview keys.

- [ ] **Step 4: Run focused and real-sample tests**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'daily_close_pnl or close_query_and_overview' -q && .venv/bin/python -m pytest tests/test_trading_management_real_sample.py -q`

Expected: all selected tests PASS; real-sample daily total equals `3,497,480`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py tests/test_trading_management_real_sample.py
git commit -m "feat: add daily close pnl overview series"
```

### Task 2: Fact-Tab Cache, Prefetch, and Loading State

**Files:**
- Modify: `frontend/trading_management.js:1-185`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: existing `/facts/positions`, `/facts/closes`, `/facts/trades` endpoints.
- Produces: `factCacheKey(tab)`, `loadFactData(tab, {refresh})`, `invalidateFactCache()`, and background `prefetchFactTabs()`.

- [ ] **Step 1: Write failing frontend contract tests**

```javascript
test("fact tabs cache by tab filters and page and prefetch sibling tabs", () => {
  assert.match(tradingJs, /function factCacheKey/);
  assert.match(tradingJs, /function loadFactData/);
  assert.match(tradingJs, /function prefetchFactTabs/);
  assert.match(tradingJs, /function invalidateFactCache/);
  assert.match(tradingJs, /tm-table-loading/);
});
```

- [ ] **Step 2: Run the test and confirm RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL on the first missing cache function.

- [ ] **Step 3: Implement cache and in-flight de-duplication**

```javascript
const factCache = new Map();
const factRequests = new Map();
function factCacheKey(tab) {
  return JSON.stringify([tab, tm.query, tm.assetType, tm.side, tm.openClose,
    tm.classification, tm.dateFrom, tm.dateTo, tm.page, tm.pageSize]);
}
async function loadFactData(tab, { refresh = false } = {}) {
  const key = factCacheKey(tab);
  if (!refresh && factCache.has(key)) return factCache.get(key);
  if (factRequests.has(key)) return factRequests.get(key);
  const request = api(`/api/trading-management/facts/${tab}?${factQuery(tab)}`)
    .then(data => { factCache.set(key, data); return data; })
    .finally(() => factRequests.delete(key));
  factRequests.set(key, request);
  return request;
}
```

Render cached results synchronously. For misses, retain the panel chrome and show a table-region spinner. After the current result renders, prefetch sibling tabs with page 1. Call `invalidateFactCache()` after import, assignment, rematch, and restore-default success.

- [ ] **Step 4: Run frontend tests**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/trading_management.js tests/trading_management_frontend.test.mjs
git commit -m "perf: cache and prefetch trading fact tabs"
```

### Task 3: Compact Shared Filters

**Files:**
- Modify: `frontend/trading_management.js:127-129`
- Modify: `frontend/trading_management.css:82-90,130-140`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: existing `filters(includeOpenClose)` markup.
- Produces: shared `.tm-filters.compact` controls with explicit semantic classes.

- [ ] **Step 1: Add failing structure and CSS tests**

```javascript
test("trading filters use compact one-row desktop sizing", () => {
  assert.match(tradingJs, /tm-filter-search/);
  assert.match(tradingJs, /tm-filter-select/);
  assert.match(tradingJs, /tm-filter-date/);
  assert.match(css, /grid-template-columns:[^;]*140px/);
  assert.match(css, /@media \(max-width: 1209px\)/);
});
```

- [ ] **Step 2: Run and confirm RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL because compact classes and grid are absent.

- [ ] **Step 3: Implement compact markup and CSS**

```css
.tm-filters.compact {
  display:grid;
  grid-template-columns:140px 58px repeat(4,minmax(104px,1fr)) 128px 128px;
  gap:6px; align-items:center;
}
.tm-filter-search,.tm-filter-select,.tm-filter-date { min-width:0; width:100%; height:34px; }
@media (max-width:1209px) { .tm-filters.compact { display:flex; flex-wrap:wrap; } }
```

Business pages omit the open/close select and naturally use one fewer column. Preserve all existing element IDs and event behavior.

- [ ] **Step 4: Run frontend tests**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/trading_management.js frontend/trading_management.css tests/trading_management_frontend.test.mjs
git commit -m "style: compact trading ledger filters"
```

### Task 4: Business-Ledger Summary Reflow and Data-Quality Weight

**Files:**
- Modify: `frontend/trading_management.js:270-282`
- Modify: `frontend/trading_management.css:63-66,96-101`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Produces: full-width `.tm-ledger-hero.single`, `.tm-ledger-summary-primary`, and `.tm-ledger-summary-risk` rows.

- [ ] **Step 1: Write failing summary-layout tests**

```javascript
test("business ledgers replace the口径 card with approved summary rows", () => {
  assert.doesNotMatch(tradingJs, /<h2>口径说明<\/h2>/);
  assert.match(tradingJs, /tm-ledger-summary-primary/);
  assert.match(tradingJs, /tm-ledger-summary-risk/);
  assert.match(tradingJs, /浮动盈亏/);
});
```

- [ ] **Step 2: Run and confirm RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL because the口径 card remains.

- [ ] **Step 3: Rebuild the two summary variants**

```javascript
const primary = summaryRow([
  ["记录数", num(data.summary.record_count)], ["手数", num(data.summary.quantity)],
  ["业务归属盈亏", num(data.summary.business_pnl)], ["浮动盈亏", "待计算"],
], "tm-ledger-summary-primary");
const risk = view === "options" ? summaryRow([
  ["Delta","待计算"],["Gamma","待计算"],["Theta","待计算"],["Vega","待计算"],
], "tm-ledger-summary-risk") : "";
```

Remove the main-workspace口径 card. Keep detailed口径 in the existing top “数据说明” drawer. Increase quality-note text to 13px while reducing panel padding and row spacing.

- [ ] **Step 4: Run frontend tests**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/trading_management.js frontend/trading_management.css tests/trading_management_frontend.test.mjs
git commit -m "style: reorganize trading ledger summaries"
```

### Task 5: Render the Real Daily PnL Chart

**Files:**
- Modify: `frontend/trading_management.js:80-110`
- Modify: `frontend/trading_management.css:68-80`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: `data.daily_close_pnl` from Task 1.
- Produces: `dailyPnlChart(rows)` with data polyline, zero axis, dates, values, and empty state.

- [ ] **Step 1: Write failing chart tests**

```javascript
test("overview chart renders real daily close pnl instead of a fixed placeholder", () => {
  assert.match(tradingJs, /function dailyPnlChart/);
  assert.match(tradingJs, /data\.daily_close_pnl/);
  assert.doesNotMatch(tradingJs, /52,125 190,125 328,125/);
  assert.match(tradingJs, /暂无平仓盈亏数据/);
});
```

- [ ] **Step 2: Run and confirm RED**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL because the chart is static.

- [ ] **Step 3: Implement SVG scaling**

```javascript
function dailyPnlChart(rows) {
  if (!rows.length) return '<div class="tm-chart-empty">暂无平仓盈亏数据</div>';
  const values = rows.map(row => Number(row.fact_close_pnl || 0));
  const min = Math.min(0, ...values), max = Math.max(0, ...values);
  const span = max - min || 1;
  const x = index => 52 + index * 690 / Math.max(1, rows.length - 1);
  const y = value => 220 - (value - min) * 180 / span;
  // emit zero axis, polyline, sparse date labels, title elements, and endpoint values
}
```

Use positive red and negative green in accordance with the project PnL convention.

- [ ] **Step 4: Run frontend tests and syntax checks**

Run: `node --check frontend/trading_management.js && node --test tests/trading_management_frontend.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/trading_management.js frontend/trading_management.css tests/trading_management_frontend.test.mjs
git commit -m "feat: render daily close pnl trend"
```

### Task 6: Full Regression, Staging Deployment, and Real QA

**Files:**
- Modify after successful deployment: `版本更新记录.md`

**Interfaces:**
- Consumes all previous task outputs.
- Produces a verified Staging release only.

- [ ] **Step 1: Run complete automated verification**

Run: `.venv/bin/python -m pytest -q && node --test tests/*.test.mjs && node --check frontend/trading_management.js && git diff --check`

Expected: zero failures.

- [ ] **Step 2: Push Staging and wait for deployed assets**

Run: `git push origin staging`

Verify the current Staging URL and deployed asset version before testing.

- [ ] **Step 3: Verify real data and chart through Staging API**

Confirm:

```text
daily_close_pnl is non-empty
sum(daily_close_pnl.fact_close_pnl) == 3497480
positions.record_count == 669
```

- [ ] **Step 4: Run browser QA**

Target flows:

```text
交易总览 -> real daily chart -> visible dates and changing points
持仓与交易 -> 当前持仓 -> 平仓记录 -> 全部交易 -> cached return switch
上海钧能台账 -> compact filters -> full-width four-item summary
期权台账 -> compact filters -> primary row -> Greek row
```

Check 1440px desktop plus one narrow viewport, console health, clipping, overlap, loading state, and interaction response. Record initial and cached tab-switch timings.

- [ ] **Step 5: Update release record after verification**

Add the deployed commits, real daily total, test counts, browser viewports, and measured tab timings to `版本更新记录.md`. Do not include secrets.

- [ ] **Step 6: Commit and push the verified release record**

```bash
git add 版本更新记录.md
git commit -m "docs: record trading layout and chart staging verification"
git push origin staging
```
