# Trading Overview Filter and Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the trading overview fast and internally consistent across account, period, and fact/business scope filters, while showing fact PnL only for “全部”, business-attribution PnL only for “基础套保 / 战略套保”, and renaming the fact-detail entry to “持仓与交易明细”.

**Architecture:** Replace overview composition from paginated detail queries with one focused read-only aggregation module. The API will return the effective filter contract together with five summary metrics, daily PnL, snapshot metadata, and data-quality counts. The frontend will keep editable filter state separate from the last successfully applied filter, use a monotonically increasing request version so only the newest response may render, and retain the previous successful result while loading or on error.

**Tech Stack:** FastAPI, Python dataclasses, SQLite/PostgreSQL-compatible aggregate SQL, vanilla JavaScript, pytest, Node test runner, Render Staging, Supabase Staging.

## Global Constraints

- The confirmed business specification is `docs/2026-07-22-trading-overview-filter-scope-business-requirements.md` and is the source of truth for semantics.
- Work starts from the latest `origin/staging` in a clean non-production development path. Preserve the current dirty/stale checkout and do not overwrite unrelated user files.
- One main Agent executes the plan. Do not use subagents unless the user separately authorizes them.
- Do not touch `main`, Production, Production environment variables, Production database/data, or any real trading control.
- Do not rewrite imported facts, business assignments, close allocations, or audit history. All new behavior is read-only aggregation and display.
- No table, column, or index migration is planned. Existing batch/account/date and business-allocation indexes are the starting point; aggregate SQL and bounded round trips must meet the performance gate without a schema change.
- “全部” includes unclassified current facts and uses fact PnL. Business scopes include only classified allocated portions and use `trading_business_close_allocations.business_pnl`.
- All-account position reporting resolves the latest snapshot on or before the end date separately per account. If effective snapshot dates differ, the API returns all dates and the UI says “各账户最近快照”; it must not silently omit an older account snapshot.
- Staging deploy and browser acceptance are included after Gate A. Production remains a separate Gate B.

## API Contract

Create an overview-only request model that does not enlarge the detail-list `FactFilters` contract:

```python
@dataclass
class OverviewFilters:
    account_id: int | None = None
    scope: str = "all"
    start_date: str = ""
    end_date: str = ""

    def __post_init__(self) -> None:
        if self.scope not in {"all", "basic_hedging", "strategic_hedging"}:
            raise ValueError("未知总览统计范围")
        if bool(self.start_date) != bool(self.end_date):
            raise ValueError("开始日期和结束日期必须同时提供")
        if self.start_date and self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
```

`GET /api/trading-management/overview` accepts:

```text
account_id=<optional integer>
scope=all|basic_hedging|strategic_hedging
start_date=YYYYMMDD
end_date=YYYYMMDD
```

It returns one stable shape for all scopes:

```json
{
  "filters": {
    "account_id": null,
    "scope": "all",
    "scope_label": "全部",
    "pnl_metric": "fact_pnl",
    "start_date": "20260701",
    "end_date": "20260731"
  },
  "trades": {"record_count": 0, "quantity": 0, "fee": 0},
  "pnl": {"value": 0, "metric": "fact_pnl"},
  "positions": {
    "group_count": null,
    "quantity": 0,
    "margin": null,
    "snapshot_status": "missing",
    "snapshot_dates": []
  },
  "daily_pnl": [{"date": "20260701", "value": 0}],
  "data_quality": {
    "unassigned_trade_count": 0,
    "unallocated_close_count": 0,
    "missing_snapshot_account_count": 0,
    "unmatched_business_position_count": 0
  }
}
```

The response `filters` object is authoritative. The frontend renders only when it exactly matches the newest request’s account, scope, start date, and end date.

---

### Task 1: Add overview filter/date contract and focused test fixtures

**Files:**
- Create: `backend/app/trading_overview.py`
- Modify: `tests/test_trading_management.py`
- Create: `tests/test_trading_overview.py`

**Interfaces:**
- `OverviewFilters(account_id, scope, start_date, end_date)` validates the overview-only contract.
- `latest_overview_date(account_id: int | None) -> str | None` returns the latest active current trade/close/snapshot business date available to the account scope.
- `build_trading_overview(filters: OverviewFilters) -> dict[str, Any]` is the only overview aggregation entry point.

- [ ] Add fixtures with two accounts, classified basic/strategic opens, an unclassified fact, different snapshot dates, and one manually overridden business close allocation.
- [ ] Write failing tests for an invalid scope, partial date range, reversed custom range, account isolation, and latest available date.
- [ ] Add `OverviewFilters`, date normalization helpers, result-shape helpers, and `latest_overview_date()` without importing `trading_management.py` back into the new module.
- [ ] Run the contract tests and make them green:

```bash
.venv/bin/pytest -q tests/test_trading_overview.py -k 'filters or latest_date or account'
```

### Task 2: Implement fact-scope aggregate semantics

**Files:**
- Modify: `backend/app/trading_overview.py`
- Modify: `tests/test_trading_overview.py`

**Interfaces:**
- `scope="all"` reads only current facts from active batches.
- Trades use `trade_date`; PnL and daily series use `close_date` and `fact_close_pnl`.
- Positions use the latest snapshot `<= end_date` per selected account, grouped by `(account_id, contract, direction, asset_type)`.

- [ ] Write failing tests proving all current facts, including unclassified facts, contribute to trade count/quantity/fee and fact PnL.
- [ ] Write failing tests proving superseded facts do not contribute and an explicit account never leaks another account’s rows.
- [ ] Write failing snapshot tests for exact date, weekend/month-end fallback, different per-account latest dates, no snapshot, and a real zero position.
- [ ] Implement fact aggregates with one database connection and aggregate-only SQL. Do not call `query_fact_rows()` or request `page_size=100`.
- [ ] Return `margin=null` and `snapshot_status="missing"` when an applicable account has no historical snapshot; return numeric zero only when a real snapshot aggregates to zero.
- [ ] Assert bounded database work so result size does not increase query count:

```python
assert calls["execute"] <= 8
assert "items" not in result
```

- [ ] Run:

```bash
.venv/bin/pytest -q tests/test_trading_overview.py -k 'fact or snapshot or bounded'
```

### Task 3: Implement business-scope aggregates and true business PnL

**Files:**
- Modify: `backend/app/trading_overview.py`
- Modify: `tests/test_trading_overview.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Business trades are attributed at assignment/allocation grain and cannot count an entire fact when only a portion belongs to the selected scope.
- Business PnL is `SUM(trading_business_close_allocations.business_pnl)` for effective allocated rows whose open trade assignment has the selected `business_type`; it must not use an allocated share of `fact_close_pnl`.
- Business positions are assigned open quantity less effective business close allocations through `end_date`, independent of `start_date`.
- Business margin is allocated from the per-account fact snapshot by the business remaining-quantity share of `(account, contract, direction, asset_type)`.

- [ ] Replace the weak existing test `test_overview_business_type_filters_assigned_fact_shares` with assertions for exact record count, quantity, fee, position groups, margin, business PnL, and daily business PnL for both scopes.
- [ ] Write a failing rematch regression: changing an effective allocation changes the selected business PnL but leaves `scope="all"` fact PnL unchanged.
- [ ] Write failing tests proving unclassified facts enter only `all`, and a mixed allocation contributes only its selected matched quantity and fee share.
- [ ] Implement business aggregates using current assignments and allocations. Preserve allocation overrides/version semantics already used by business detail queries.
- [ ] If any nonzero business position cannot be reconciled to its account’s selected fact snapshot, return `margin=null`, increment `unmatched_business_position_count`, and do not fabricate zero.
- [ ] Run:

```bash
.venv/bin/pytest -q tests/test_trading_overview.py tests/test_trading_management.py -k 'overview or rematch'
```

### Task 4: Wire the dedicated endpoint and account/default-date metadata

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Add `_api_overview_filters(...) -> OverviewFilters`; retain `_api_filters(...) -> FactFilters` for detail pages.
- `GET /overview` calls `build_trading_overview()` from the focused module.
- Extend `/config` with `latest_overview_date`; keep current configuration fields compatible.

```python
def _api_overview_filters(
    account_id: int | None = None,
    scope: str = "all",
    start_date: str = "",
    end_date: str = "",
) -> OverviewFilters:
    return OverviewFilters(
        account_id=account_id,
        scope=scope,
        start_date=start_date,
        end_date=end_date,
    )
```

- [ ] Write failing route tests for all four query parameters, invalid date error mapping, and unchanged authorization requirements.
- [ ] Write a test proving the endpoint response echoes the effective filter contract exactly.
- [ ] Wire the new module, remove the old overview composition path, and keep detail APIs unchanged.
- [ ] Run:

```bash
.venv/bin/pytest -q tests/test_trading_management.py tests/test_trading_overview.py
```

### Task 5: Build explicit frontend period state and latest-response-wins behavior

**Files:**
- Modify: `frontend/trading_management.js`
- Modify: `frontend/index.html`
- Modify: `frontend/trading_management.css`
- Modify: `tests/trading_management_frontend.test.mjs`
- Create: `tests/trading_overview_frontend_behavior.test.mjs`

**Interfaces:**
- Keep independent draft selectors for day, month, year/quarter, and custom from/to values.
- Keep `overviewAppliedFilters` separate from draft values and from detail-page `dateFrom/dateTo`.
- Use real calendar boundaries; do not derive first load from a previous API response and do not append `31` to every month/quarter.
- Use a request version so only the latest call may commit:

```javascript
const requestVersion = ++tm.overviewRequestVersion;
setOverviewLoading(true);
try {
  const data = await api(`/api/trading-management/overview?${params}`);
  if (requestVersion !== tm.overviewRequestVersion) return;
  if (!overviewResponseMatches(data.filters, requestedFilters)) return;
  tm.overview = data;
  tm.overviewAppliedFilters = requestedFilters;
  renderOverviewView(data);
} catch (error) {
  if (requestVersion === tm.overviewRequestVersion) showOverviewError(error);
} finally {
  if (requestVersion === tm.overviewRequestVersion) setOverviewLoading(false);
}
```

- [ ] Build behavior tests with a minimal fake DOM/API harness, not source-regex-only assertions.
- [ ] Test first load sends the latest month’s real first/last dates; day, month, quarter, and custom controls send exact dates.
- [ ] Test custom dates remain visible after success, draft changes do not relabel applied results, and reversed dates make no API call.
- [ ] Test account changes include `account_id`, scope changes include `scope`, and both retain the chosen period.
- [ ] Resolve two promises out of order and assert the older response cannot overwrite the newest scope/result.
- [ ] Test a failed request retains the last successful result and applied-range label while showing “本次筛选未生效”.
- [ ] Remove the decorative top `tmDateFilter`; wire `tmAccountFilter` to overview reload. Render the period selector in the overview filter bar.
- [ ] Add local `aria-busy`/loading styling that preserves existing cards and appears within 200 ms of an action.
- [ ] Run:

```bash
node --test tests/trading_management_frontend.test.mjs tests/trading_overview_frontend_behavior.test.mjs
node --check frontend/trading_management.js
```

### Task 6: Render scope-specific labels, quality states, and rename the detail entry

**Files:**
- Modify: `frontend/trading_management.js`
- Modify: `frontend/trading_management.css`
- Modify: `backend/app/db.py`
- Modify: `tests/trading_management_frontend.test.mjs`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- `all`: “事实口径 / 期间事实盈亏 / 逐日事实盈亏趋势”.
- Business scopes: “业务口径 / 期间业务归属盈亏 / 逐日业务归属盈亏趋势”.
- Module code remains `trading_positions`; only its visible label/subtitle changes.

- [ ] Add failing render tests for all three scope labels and for zero/no-data/no-snapshot distinctions.
- [ ] Add failing tests for real quality counts: unassigned trades, unallocated closes, missing snapshots, and unmatched business positions.
- [ ] Render the authoritative applied range and snapshot metadata. When all-account snapshot dates differ, show “各账户最近快照” with the date range in the detail note.
- [ ] Rename `MODULES` label and `VIEW_COPY` to “持仓与交易明细”; set subtitle to “查询和核验全部成交、平仓及持仓事实”; update overview jump copy. Keep permissions and audit module codes unchanged.
- [ ] Run:

```bash
.venv/bin/pytest -q tests/test_trading_management.py tests/test_trading_overview.py
node --test tests/trading_management_frontend.test.mjs tests/trading_overview_frontend_behavior.test.mjs
```

### Task 7: Full regression, performance gate, and Staging acceptance

**Files:**
- Modify after successful Staging deploy: `README.md` only if setup/structure changed
- Modify after successful Staging deploy: `版本更新记录.md`
- Modify only if continuation state materially changes: `项目交接摘要.md`

- [ ] Run all repository tests and static checks:

```bash
.venv/bin/pytest -q
node --test tests/*.test.mjs
.venv/bin/python -m compileall -q backend/app
node --check frontend/trading_management.js
git diff --check
```

- [ ] Run a pre/post read-only data regression against Staging-safe fixtures or an approved Staging snapshot: fact identity counts, fact PnL, assignment counts, allocation counts, and allocation PnL must be unchanged.
- [ ] Measure the overview builder locally on a representative copy/fixture for all three scopes and four time modes; verify bounded query count and no detail rows in the response.
- [ ] Review the complete branch diff for scope leakage, business-PnL substitution, stale-response rendering, unrelated file changes, and real-trading safety.
- [ ] Commit and push the tested feature to the Staging branch, allowing the normal non-production Render deploy.
- [ ] In the in-app browser, open `https://ltm-web-staging.onrender.com/?codex=<commit>` and verify URL/title, console health, deployed asset versions, and the visible flow.
- [ ] After warm-up, record browser-visible/network timing for all/month/day/quarter/custom and all/basic/strategic. Each normal interaction must stabilize in `<= 2s`; any normal interaction `> 3s` fails acceptance.
- [ ] Verify custom values persist, latest-click wins during rapid switching, account filtering changes results, snapshot fallback is visible, and “持仓与交易明细” opens with unchanged detail behavior.
- [ ] Update the release record only after Staging deployment succeeds. Stop at Gate B; do not merge or deploy Production.

## Rollback

- Code rollback: revert the feature commit on Staging and redeploy the prior tested Staging commit.
- Database rollback: none required because this plan creates no schema and writes no trading/business data.
- Data rollback: none required; acceptance explicitly verifies immutable fact and business-allocation counts/checksums before and after.
- Frontend cache rollback: restore the previous asset references together with the reverted commit; verify the visible page loads that commit’s assets.

## Gate A Acceptance Package

- Requirements: confirmed business document dated 2026-07-22.
- Non-goals: no overview detail table, no fourth fact button, no side-by-side fact PnL in business scopes, no new business types, no import/allocation rewrite, no Production.
- Complexity: `D3 / T3 / R2 / C1`, one main Agent.
- Environments: latest `origin/staging`, Render Staging, Supabase Staging only.
- Database impact: read-only queries only; no migration or business-data write.
- Verification: backend exact-value tests, frontend behavior tests, full regression, immutable-data comparison, real Staging browser verification, and warm interaction performance gate.
- Release authority requested: implement, test, commit, push Staging, allow Staging deploy, and perform read-only Staging verification. Production authority is explicitly excluded.
