# Trading Prototype Data Consistency and Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the confirmed trading prototype behavior, add real business-ledger pagination and filters, show Shanghai Junneng candidates by default, and keep fact PnL separate from business-attribution PnL.

**Architecture:** Keep the existing FastAPI/SQLite-or-PostgreSQL backend and vanilla JavaScript workspace. Extend the shared `FactFilters` contract and backend row projections so the frontend receives authoritative assignment status, candidate membership, pagination metadata, and both PnL values. Restore prototype interactions in the existing frontend files without introducing a second component system.

**Tech Stack:** Python 3.9, FastAPI, SQLite/PostgreSQL-compatible SQL helpers, vanilla JavaScript, CSS, Python pytest, Node test runner.

## Global Constraints

- Left system navigation remains unchanged; only the trading workspace changes.
- Fact pages use the label `平仓盈亏`; business pages use `业务归属盈亏`.
- Fact PnL always comes from active Wenhua close facts and is never changed by business rematching.
- Business PnL uses the fact-default allocation until a manual allocation replaces it.
- Unassigned RB/HC rows appear in Shanghai Junneng by default and leave only after explicit assignment to another subject.
- Page sizes are exactly 20, 50, or 100.
- Floating PnL and option Greeks remain `待计算`.
- Summary/export remains a reserved placeholder.
- Use the confirmed prototype at `/Users/wangjingze/Documents/交易台账自动化处理/personal_workbench/trading_static/` as the direct component and interaction reference.
- Preserve unrelated working-tree files.

---

### Task 1: Make Fact Classification and Aggregated Position Data Authoritative

**Files:**
- Modify: `backend/app/trading_management.py`
- Test: `tests/test_trading_management.py`

**Interfaces:**
- Consumes: `FactFilters(classification: str, page: int, page_size: int)` and active trading fact tables.
- Produces: `query_fact_rows(view, filters)` rows with `assignment_status`, `business_subject`, `business_type`, `strategy`, and aggregated position metadata where applicable.

- [ ] **Step 1: Write failing fact classification tests**

Add tests that create assigned and unassigned trades, then assert:

```python
unassigned = query_fact_rows("trades", FactFilters(classification="unclassified", page=1, page_size=20))
assert all(row["assignment_status"] == "unclassified" for row in unassigned["items"])

assigned = query_fact_rows("trades", FactFilters(classification="classified", page=1, page_size=20))
assert all(row["assignment_status"] == "classified" for row in assigned["items"])
assert assigned["items"][0]["business_type"] == "basic_hedging"
```

Add a position test asserting that rows are grouped by contract/direction/asset type and expose `source_record_count` plus `assignment_status`/business display fields.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_trading_management.py -k "classification or aggregated_position" -q
```

Expected: FAIL because fact queries currently omit assignment metadata, ignore `classification`, and return raw position snapshots.

- [ ] **Step 3: Implement the shared fact projection and classification filter**

Extend `FactFilters` with `classification`. Join active trade facts to business assignments for trades. For positions, aggregate active position snapshots by contract/direction/asset type and derive business display state from remaining active opening trades. Apply:

```python
if filters.classification == "classified":
    items = [row for row in items if row["assignment_status"] == "classified"]
elif filters.classification == "unclassified":
    items = [row for row in items if row["assignment_status"] == "unclassified"]
```

Keep summaries over the complete filtered list before `_page_result` slices the current page.

- [ ] **Step 4: Verify GREEN**

Run the focused command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "fix: restore fact assignment status and position aggregation"
```

### Task 2: Correct Shanghai Junneng Candidate Scope and Business PnL Semantics

**Files:**
- Modify: `backend/app/trading_management.py`
- Test: `tests/test_trading_management.py`
- Test: `tests/test_trading_management_real_sample.py`

**Interfaces:**
- Consumes: `query_business_rows(view, tab, filters)` and business allocation rows.
- Produces: Junneng rows whose `ledger_membership` is `candidate` or `confirmed`, excluding explicit other-subject assignments; business close rows containing `fact_close_pnl`, `business_pnl`, and `allocation_source`.

- [ ] **Step 1: Write failing candidate and PnL tests**

Add three Junneng membership cases:

```python
assert unassigned_rb["ledger_membership"] == "candidate"
assert assigned_junneng["ledger_membership"] == "confirmed"
assert other_subject_identity not in {row["identity_id"] for row in result["items"]}
```

Add default/manual allocation assertions:

```python
assert default_row["business_pnl"] == default_row["fact_close_pnl"]
assert default_row["allocation_source"] == "fact_default"
assert manual_row["business_pnl"] == expected_adjusted_pnl
assert manual_row["fact_close_pnl"] == original_fact_pnl
assert manual_row["allocation_source"] == "manual_override"
```

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_trading_management.py -k "junneng and (candidate or business_pnl)" -q
```

Expected: FAIL because unassigned RB/HC rows are currently removed from formal items and PnL semantics are not exposed as one row-level contract.

- [ ] **Step 3: Implement one shared Junneng membership predicate**

Use the same rule for positions, closes, and trades:

```python
def _junneng_membership(row):
    if row.get("business_subject") == "上海钧能":
        return "confirmed"
    if row.get("business_subject"):
        return None
    if _product_code(row["contract"]) in {"rb", "hc"}:
        return "candidate"
    return None
```

For default close allocations, expose the inherited business value; for manual rows, expose the recalculated allocation value while retaining immutable `fact_close_pnl`.

- [ ] **Step 4: Verify GREEN and real-sample invariants**

Run:

```bash
.venv/bin/python -m pytest tests/test_trading_management.py -k "junneng or business_pnl" -q
.venv/bin/python -m pytest tests/test_trading_management_real_sample.py -q
```

Expected: PASS; fact PnL remains `3497480` in the confirmed sample.

- [ ] **Step 5: Commit**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py tests/test_trading_management_real_sample.py
git commit -m "fix: include junneng candidates and separate business pnl"
```

### Task 3: Apply Business Filters and Service-Side Pagination

**Files:**
- Modify: `backend/app/trading_management.py`
- Test: `tests/test_trading_management.py`

**Interfaces:**
- Consumes: contract, direction, classification, start/end dates, page, and page size from business routes.
- Produces: filtered `items`, full-filter `summary`, `page`, `page_size`, `total_items`, and `total_pages` for every Junneng/options tab.

- [ ] **Step 1: Write failing pagination/filter tests**

Create more than 20 business rows and assert:

```python
page_one = query_business_rows("options", "trades", FactFilters(page=1, page_size=20))
page_two = query_business_rows("options", "trades", FactFilters(page=2, page_size=20))
assert len(page_one["items"]) == 20
assert page_one["total_pages"] >= 2
assert page_one["summary"] == page_two["summary"]
assert {r["identity_id"] for r in page_one["items"]}.isdisjoint({r["identity_id"] for r in page_two["items"]})
```

Add direction/date/classification tests for both `junneng` and `options`.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_trading_management.py -k "business and (pagination or filters)" -q
```

Expected: FAIL because business queries currently apply only contract filtering in several paths.

- [ ] **Step 3: Implement one business-filter helper**

Add `_filter_business_items(items, tab, filters)` that maps `positions -> direction/no date`, `closes -> open_side/close_date`, and `trades -> side/trade_date`, then applies contract, direction, classification, start date, and end date before summary calculation. Keep `_page_result` as the only slicer.

- [ ] **Step 4: Verify GREEN**

Run the focused command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: filter and paginate business ledgers"
```

### Task 4: Restore Fact Workspace Prototype Fields and Selection Interactions

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `frontend/trading_management.js`
- Modify: `frontend/trading_management.css`
- Test: `tests/test_trading_management.py`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: Task 1 fact row metadata and pagination response.
- Produces: prototype-aligned fact tables, `待确认` labels, working classification filters, and select-all-filtered behavior.

- [ ] **Step 1: Write failing frontend contract tests**

Assert the frontend contains:

```javascript
assert.match(tradingJs, /业务类型 \/ 策略/);
assert.match(tradingJs, /待确认/);
assert.match(tradingJs, /tmSelectFiltered/);
assert.match(tradingJs, /classification_status/);
assert.match(tradingJs, /source_record_count/);
```

Also assert fact pages label `fact_close_pnl` as `平仓盈亏`, not `业务归属盈亏`.

- [ ] **Step 2: Verify RED**

Run:

```bash
node --test tests/trading_management_frontend.test.mjs
```

Expected: FAIL for missing prototype columns/status/select-all-filtered behavior.

- [ ] **Step 3: Restore fact tables and interactions**

Render assignment status as:

```javascript
function assignmentTag(row) {
  if (row.assignment_status !== "classified" || !row.business_type) return '<span class="tm-tag amber">待确认</span>';
  return `<span class="tm-tag blue">${businessType(row.business_type)}${row.strategy ? ` / ${esc(row.strategy)}` : ""}</span>`;
}
```

Restore aggregate-record count and `选择全部筛选结果`. Add `GET /facts/trades/selection-identities` before the dynamic fact route; it accepts the same filters except pagination and returns `{"identity_ids": [...]}` for all filtered trade facts. The frontend calls this endpoint, replaces `tm.selected` with the returned IDs, and rerenders the current page.

- [ ] **Step 4: Verify GREEN**

Run `node --check frontend/trading_management.js` and the Node test from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/trading_management.js frontend/trading_management.css tests/trading_management_frontend.test.mjs
git commit -m "fix: restore trading fact prototype status fields"
```

### Task 5: Implement Business Ledger Controls, Pagination, and Labels

**Files:**
- Modify: `frontend/trading_management.js`
- Modify: `frontend/trading_management.css`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: Tasks 2–3 business responses.
- Produces: independent Junneng/options tab/page/page-size/filter state, real query parameters, pagination controls, and correct PnL labels.

- [ ] **Step 1: Write failing business-page tests**

Assert:

```javascript
assert.match(tradingJs, /junnengPageSize:\s*20/);
assert.match(tradingJs, /optionsPageSize:\s*20/);
assert.match(tradingJs, /pagination\(data,\s*view/);
assert.match(tradingJs, /业务归属盈亏/);
assert.match(tradingJs, /ledger_membership/);
assert.doesNotMatch(tradingJs, /business\/${view}\/${tab}\?page_size=100/);
```

- [ ] **Step 2: Verify RED**

Run the Node test. Expected: FAIL because business ledgers have no page state, query wiring, or pagination rendering.

- [ ] **Step 3: Implement business state and request wiring**

Maintain per-view state for query, direction, classification, dates, page, and page size. Build URL parameters from that state, render `pagination(data, view)`, and wire search/change/previous/next/page-size controls. Reset page to 1 on tab or filter changes.

Use `业务归属盈亏` for business summaries and business PnL columns. Keep `事实平仓盈亏` only as the explicit comparison column on business close tables. Render candidate rows with orange `待确认` status.

- [ ] **Step 4: Verify GREEN**

Run `node --check frontend/trading_management.js` and the Node test. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/trading_management.js frontend/trading_management.css tests/trading_management_frontend.test.mjs
git commit -m "feat: add business ledger filters and pagination"
```

### Task 6: Compact Overview Cards and Activate Period Controls

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `frontend/trading_management.js`
- Modify: `frontend/trading_management.css`
- Test: `tests/test_trading_management.py`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes: overview start/end filters and active fact summaries.
- Produces: working day/month/quarter/custom controls and one-row three-card overview layout.

- [ ] **Step 1: Write failing period and layout tests**

Backend tests assert day/month/custom filters change trade, close, and daily-series totals together. Frontend tests assert:

```javascript
assert.match(tradingJs, /tmOverviewPeriod/);
assert.match(tradingJs, /tmOverviewFrom/);
assert.match(tradingJs, /tmOverviewTo/);
assert.match(tradingJs + css, /tm-overview-mini-grid/);
assert.match(css, /grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/);
```

- [ ] **Step 2: Verify RED**

Run focused Python overview tests and the Node frontend test. Expected: FAIL because period buttons are static and auxiliary panels use two separate two-column grids.

- [ ] **Step 3: Implement period queries and compact grid**

Add overview period state and compute exact date bounds in the frontend. Request `/overview?start_date=...&end_date=...`, then rerender all summary/chart values. Replace the lower two grids with one `.tm-overview-mini-grid` containing data quality, business distribution, and active contracts. Reduce only trading-workspace horizontal padding; preserve the system sidebar.

- [ ] **Step 4: Verify GREEN**

Run focused tests and `node --check`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/trading_management.py frontend/trading_management.js frontend/trading_management.css tests/test_trading_management.py tests/trading_management_frontend.test.mjs
git commit -m "feat: activate overview periods and compact cards"
```

### Task 7: Full Regression, Staging Deployment, and Browser QA

**Files:**
- Modify: `frontend/index.html`
- Modify: `tests/auth_frontend.test.mjs`
- Modify: `版本更新记录.md`

**Interfaces:**
- Consumes: completed Tasks 1–6.
- Produces: cache-busted Staging assets, full test evidence, browser-visible acceptance, and release record.

- [ ] **Step 1: Bump the trading static asset version**

Update the trading JS/CSS and app cache keys in `frontend/index.html`, and update the exact version assertion in `tests/auth_frontend.test.mjs`.

- [ ] **Step 2: Run complete automated verification**

```bash
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
node --check frontend/trading_management.js
git diff --check
```

Expected: all tests pass with zero failures.

- [ ] **Step 3: Commit and push Staging**

```bash
git add frontend/index.html tests/auth_frontend.test.mjs
git commit -m "chore: publish trading prototype consistency fixes"
git push origin staging
```

- [ ] **Step 4: Verify Staging with real imported data**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>` in the in-app browser and verify:

1. New static versions load and console has no relevant errors.
2. Overview shows three compact cards in one row and period changes update metrics/chart.
3. Fact rows visibly show `待确认` and classification filtering changes rows.
4. Junneng shows unassigned RB/HC candidates and removes an explicitly other-subject test row.
5. Junneng/options page sizes 20/50/100 and previous/next controls change page contents.
6. Fact close PnL remains `3,497,480` for the sample.
7. Default business PnL follows fact-default allocation; a reversible Staging-only manual rematch changes business PnL but not fact PnL, then restore the default relation.

- [ ] **Step 5: Record deployment evidence**

Append one Staging entry to `版本更新记录.md` containing commits, test counts, static asset version, real-data totals, browser checks, database impact, and rollback commits. Commit and push the record.
