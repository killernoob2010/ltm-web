# Trading Management P0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P0 trading management module as a new first-level menu with immutable Wenhua facts, complete three-file batch replacement, whole-trade business classification, business close rematching, and five approved pages.

**Architecture:** Add a focused `trading_management.py` FastAPI router and new `trading_*` tables without changing the old ledger tables. Keep fact views separate from business views; business close allocations reference stable fact identities and can be transactionally rematched while fact PnL remains unchanged. Add a separate classic-JavaScript frontend controller and stylesheet, with only routing and permission delegation in the existing monolithic frontend.

**Tech Stack:** Python 3.9, FastAPI, Pydantic, SQLite/PostgreSQL through the existing `db` adapter, openpyxl read-only parsing, vanilla JavaScript, CSS, pytest, Node test runner.

## Global Constraints

- Work only on `staging`, local SQLite, and the Staging Supabase/Render environment.
- Do not modify or migrate old `sh_junneng_*` tables or old “台账管理” behavior.
- No real trading or funds operation is allowed anywhere in the module.
- A trade is classified as one whole record; no partial-lot business classification.
- Import confirmation requires all three Wenhua files.
- P0 floating PnL and option Greeks return `pending_calculation` and display “待计算”.
- P0 “汇总与导出” is an empty placeholder and does not generate files.
- Use the 2026-06 sample baseline from the approved design for real-data regression.
- Write a failing test before every production-code behavior.
- Stage and commit only trading-management files; preserve unrelated dirty-worktree files.

---

### Task 1: Database Schema, Module Registration, and Permission Skeleton

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/permissions.py`
- Modify: `backend/app/main.py`
- Create: `backend/app/trading_management.py`
- Create: `tests/test_trading_management.py`
- Modify: `tests/test_auth_permissions.py`

**Interfaces:**
- Produces: `trading_management.router: APIRouter`
- Produces: `trading_management_current_user(authorization) -> dict`
- Produces: `db.init_db()` creation of all approved `trading_*` tables and indexes.
- Produces module codes: `trading_overview`, `trading_positions`, `trading_sh_junneng`, `trading_options`, `trading_export`.

- [ ] **Step 1: Write failing schema and permission tests**

Add tests that initialize a temporary SQLite database and assert these tables exist: `trading_accounts`, `trading_import_batches`, `trading_source_rows`, `trading_fact_identities`, `trading_trade_facts`, `trading_close_facts`, `trading_position_snapshots`, `trading_contract_specs`, `trading_fact_close_allocations`, `trading_close_trade_links`, `trading_business_subjects`, `trading_strategies`, `trading_business_assignments`, `trading_business_close_allocations`, `trading_business_allocation_audit`.

Assert `db.MODULES` contains the five new second-level modules under `交易管理`; a 期货组 user defaults to `operate`, a 领导 to `view`, and an 管理员 to `sensitive`.

- [ ] **Step 2: Run the failing tests**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py tests/test_auth_permissions.py -q`

Expected: FAIL because the router, tables, and module codes do not exist.

- [ ] **Step 3: Add the minimal schema and router skeleton**

Create `trading_management.py` with:

```python
router = APIRouter()
TRADING_MODULES = {
    "overview": "trading_overview",
    "positions": "trading_positions",
    "junneng": "trading_sh_junneng",
    "options": "trading_options",
    "export": "trading_export",
}

async def trading_management_current_user(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
```

Register `app.include_router(trading_management.router, prefix="/api/trading-management")`. Add module/resource mappings and schema for both PostgreSQL and SQLite, including the uniqueness and lookup indexes defined in the approved design.

- [ ] **Step 4: Run schema and permission tests**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py tests/test_auth_permissions.py -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/db.py backend/app/permissions.py backend/app/main.py backend/app/trading_management.py tests/test_trading_management.py tests/test_auth_permissions.py
git commit -m "feat: add trading management data foundation"
```

### Task 2: Wenhua Three-File Parser and Preview Validation

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Produces: `parse_trade_workbook(path: Path) -> list[dict]`
- Produces: `parse_close_workbook(path: Path) -> list[dict]`
- Produces: `parse_position_workbook(path: Path) -> list[dict]`
- Produces: `build_fact_signature(fact_type: str, account_code: str, row: dict) -> str`
- Produces: `preview_trading_import(account_id, trade_path, close_path, position_path, actor) -> dict`

- [ ] **Step 1: Write failing parser tests with synthetic grouped workbooks**

Build three temporary workbooks with exact sheets `成交记录`, `平仓记录`, `期末持仓`, a header row, `YYYYMMDD` date markers, and representative future/option rows. Assert:

```python
assert trades[0]["open_close"] == "开仓"
assert trades[1]["open_close"] == "平仓"
assert trades[2]["asset_type"] == "option"
assert closes[0]["fact_close_pnl"] == 1200
assert positions[0]["margin"] == 50000
assert positions[0]["valuation_status"] == "pending_calculation"
```

Add tests that missing sheets, unknown headers, missing one of three files, account absence, and partially overlapping active ranges return explicit preview errors.

- [ ] **Step 2: Verify parser tests fail**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'parser or preview or signature' -q`

Expected: FAIL because the parser functions do not exist.

- [ ] **Step 3: Implement read-only parsing and normalized signatures**

Use `load_workbook(path, read_only=True, data_only=True)`. Implement a shared grouped-sheet reader that updates `current_date` only for eight-digit date markers. Reject unknown mandatory headers instead of inferring column positions.

Normalize dates, directions, open/close values, contract casing, decimals, and whitespace before SHA-256. Assign a batch-local occurrence index for identical signatures and mark ambiguous signature groups for inheritance review.

- [ ] **Step 4: Verify parser tests pass**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'parser or preview or signature' -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: parse trading management source files"
```

### Task 3: Complete Batch Confirmation and Version Replacement

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Produces: `confirm_trading_import(preview_batch_id: int, actor: dict) -> dict`
- Produces: `list_trading_imports(account_id: int) -> list[dict]`
- Consumes: parser and signature functions from Task 2.

- [ ] **Step 1: Write failing batch-version tests**

Test that a confirmed batch writes raw rows and normalized fact versions, becomes `active`, and cannot be confirmed twice. Confirming another complete batch with the exact same account/date range must mark the old batch `superseded` while preserving old source rows.

Test that matching unique signatures retain their stable identity. An ambiguous duplicate-signature group with different prior assignments must be reported as `inheritance_review_required`, not force-inherited.

- [ ] **Step 2: Verify batch tests fail**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'batch or supersede or inheritance' -q`

Expected: FAIL because confirmation/version switching is absent.

- [ ] **Step 3: Implement transactional confirmation**

Inside one `db.get_conn()` transaction:

1. lock/re-read the preview batch;
2. verify all three file hashes and status `preview`;
3. reject partial range overlap;
4. upsert stable identities and insert immutable fact versions/source rows;
5. calculate validation/matching records;
6. supersede the exact-range active batch;
7. activate the new batch;
8. preserve or invalidate business assignments according to signature uniqueness;
9. write an operation log summary.

Never delete old batch facts.

- [ ] **Step 4: Verify batch tests pass**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'batch or supersede or inheritance' -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: version complete trading import batches"
```

### Task 4: Fact Matching, Fact Queries, and Snapshot History

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Produces: `match_close_trade_facts(conn, batch_id: int) -> dict`
- Produces: `build_fact_close_allocations(conn, batch_id: int) -> dict`
- Produces: `query_fact_rows(view: str, filters: FactFilters) -> dict`
- Produces: `build_overview(filters: OverviewFilters) -> dict`

- [ ] **Step 1: Write failing matching/query tests**

Assert close records match close trades by account/date/contract/close direction/price and allocate fact PnL and fee by quantity only when group quantities reconcile. Assert fact open allocations match account/contract/open direction/open date/open price and use source order for identical open rows.

Add query tests for page sizes 20/50/100, identical summary/detail filters, latest snapshot selection, historical snapshot selection, and “无持仓快照” when the requested date has no snapshot.

- [ ] **Step 2: Verify fact tests fail**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'fact_match or fact_query or overview or snapshot' -q`

Expected: FAIL because matching/query services are absent.

- [ ] **Step 3: Implement minimal fact services and endpoints**

Add endpoints:

```text
GET /overview
GET /facts/positions
GET /facts/closes
GET /facts/trades
GET /imports
GET /imports/{id}/validation
```

Return `items`, `summary`, `page`, `page_size`, `total_items`, `total_pages`, and `data_status`. Use one normalized filter object for both detail SQL and summary SQL. Fact PnL must come from `trading_close_facts`; missing matches return `pending_verification` rather than zero.

- [ ] **Step 4: Verify fact tests pass**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'fact_match or fact_query or overview or snapshot' -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: query immutable trading facts"
```

### Task 5: Business Configuration and Whole-Trade Classification

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Produces: `list_trading_config() -> dict`
- Produces: `classify_trade_identities(identity_ids, subject_id, business_type, strategy_name, instruction_text, actor) -> dict`
- Produces: `remove_trade_assignment(identity_id, actor) -> dict`

- [ ] **Step 1: Write failing configuration/classification tests**

Assert business types accept only `basic_hedging` and `strategic_hedging`. Assert subject names normalize uniquely, disabled subjects cannot be newly assigned, manual strategy text creates a reusable strategy, and strategy merge does not erase historical assignment/audit data.

Assert classification applies to full fact identities only; payloads containing quantity fields are rejected. Assert “select all filtered results” uses a server preview token and detects changed results before confirmation.

- [ ] **Step 2: Verify business configuration tests fail**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'business_config or classify or strategy' -q`

Expected: FAIL because business services are absent.

- [ ] **Step 3: Implement configuration and classification endpoints**

Add `/config`, subject/strategy admin endpoints, and:

```text
POST /business-assignments/batch-preview
POST /business-assignments/batch-confirm
DELETE /business-assignments/{trade_identity_id}
```

Store exactly one current assignment per stable trade identity, audit before/after values, and trigger business allocation/holding recomputation.

- [ ] **Step 4: Verify business configuration tests pass**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'business_config or classify or strategy' -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: classify whole trading facts"
```

### Task 6: Default Business Allocations and Business Views

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Produces: `rebuild_default_business_allocations(conn, batch_id: int) -> dict`
- Produces: `calculate_business_pnl(open_price, close_price, side, quantity, multiplier) -> float`
- Produces: `query_business_rows(view: str, tab: str, filters: BusinessFilters) -> dict`

- [ ] **Step 1: Write failing business-allocation tests**

Assert default business allocations copy fact allocations, a classified open causes its linked close to inherit subject/type/strategy, business remaining quantity equals open quantity minus allocations, and fact PnL remains unchanged.

Assert Shanghai Junneng formal results include only assigned Shanghai Junneng records, RB/HC unassigned facts appear only as `candidate`, and option views include every option even when unclassified. Assert business pages expose both `fact_close_pnl` and `business_pnl` with explicit labels.

- [ ] **Step 2: Verify business-view tests fail**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'default_business or business_view or junneng or option' -q`

Expected: FAIL because default allocations/business views are absent.

- [ ] **Step 3: Implement business allocations and endpoints**

Add:

```text
GET /business/junneng/positions
GET /business/junneng/closes
GET /business/junneng/trades
GET /business/options/positions
GET /business/options/closes
GET /business/options/trades
```

Calculate business PnL from selected fact prices and `trading_contract_specs`. Missing multiplier returns `contract_parameter_pending`, never a guessed value. Return floating PnL and Greeks as `null` with `pending_calculation`.

- [ ] **Step 4: Verify business-view tests pass**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'default_business or business_view or junneng or option' -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: build trading business views"
```

### Task 7: Manual Business Close Rematching

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Produces: `list_business_close_candidates(close_identity_id: int) -> list[dict]`
- Produces: `preview_business_rematch(close_identity_id, selections, allocation_version) -> dict`
- Produces: `confirm_business_rematch(close_identity_id, preview_token, allocation_version, actor) -> dict`
- Produces: `restore_default_business_allocation(close_identity_id, actor) -> dict`

- [ ] **Step 1: Write failing rematch tests**

Cover one-open and multi-open rematches. Assert all candidates use the same account, contract, holding direction, open time not after close time, sufficient remaining quantity, and identical subject/type/strategy across multi-open selections.

Assert a confirmed rematch atomically restores old open remaining quantity, consumes new open quantity, inherits target assignment, changes business PnL, leaves fact PnL unchanged, writes before/after audit, and returns conflict on stale allocation version.

Assert fully closed contract business PnL reconciles with fact PnL; otherwise return `business_pnl_reconciliation_failed` with the difference.

- [ ] **Step 2: Verify rematch tests fail**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'rematch or allocation_version or reconciliation' -q`

Expected: FAIL because rematch services are absent.

- [ ] **Step 3: Implement preview/confirm/restore endpoints**

Add:

```text
GET  /business-close-allocations/{close_identity_id}/candidates
POST /business-close-allocations/{close_identity_id}/preview
POST /business-close-allocations/{close_identity_id}/confirm
POST /business-close-allocations/{close_identity_id}/restore-default
```

Re-run all constraints inside the confirmation transaction. Store only the current effective allocation rows plus immutable audit snapshots; do not mutate fact allocation rows.

- [ ] **Step 4: Verify rematch tests pass**

Run: `.venv/bin/python -m pytest tests/test_trading_management.py -k 'rematch or allocation_version or reconciliation' -q`

Expected: PASS.

- [ ] **Step 5: Commit Task 7**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: rematch trading business closes"
```

### Task 8: Frontend Navigation, Fact Pages, Business Pages, and Dialogs

**Files:**
- Create: `frontend/trading_management.js`
- Create: `frontend/trading_management.css`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Create: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Produces: `window.TradingManagement.init(context)`
- Produces: `window.TradingManagement.showPage(pageCode)`
- Consumes all APIs from Tasks 3–7.

- [ ] **Step 1: Write failing frontend structural tests**

Read the frontend source as existing Node tests do. Assert old “台账管理” remains, new “交易管理” contains five page buttons, the common tab order is 当前持仓/平仓记录/全部交易, page-size controls contain 20/50/100, fact/business PnL labels are distinct, and the export page has no download button.

Assert import confirmation checks all three file inputs, whole-trade classification has no quantity input, and rematch preview renders restored/consumed quantities and before/after business PnL.

- [ ] **Step 2: Verify frontend tests fail**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: FAIL because the frontend files and markup are absent.

- [ ] **Step 3: Implement navigation and page shells**

Load `/static/trading_management.css` and `/static/trading_management.js` with a new cache version. Add the five page containers and dialogs. Keep `app.js` changes limited to menu routing, permission visibility, auth context forwarding, and controller invocation.

Implement controller state per page: applied filters separate from search draft, page reset on filter change, 20/50/100 pagination, loading/error/empty states, and safe text rendering.

- [ ] **Step 4: Implement import, classification, and rematch interactions**

The import dialog requires account plus three files, calls preview first, and enables confirm only when the preview is valid. Classification submits selected complete identities. Rematch calls candidates, preview, then confirm and refreshes both closed and current-position business tabs.

- [ ] **Step 5: Verify frontend tests pass**

Run: `node --test tests/trading_management_frontend.test.mjs`

Expected: PASS.

- [ ] **Step 6: Run existing frontend regression tests**

Run: `node --test tests/*.test.mjs`

Expected: all existing and new tests PASS.

- [ ] **Step 7: Commit Task 8**

```bash
git add frontend/trading_management.js frontend/trading_management.css frontend/index.html frontend/app.js tests/trading_management_frontend.test.mjs
git commit -m "feat: add trading management pages"
```

### Task 9: Full Regression, Real Sample, Documentation, and Staging Verification

**Files:**
- Modify: `README.md`
- Modify: `版本更新记录.md`
- Modify: `docs/superpowers/plans/2026-07-12-trading-management-implementation.md`
- Test: `tests/test_trading_management.py`
- Test: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Consumes all preceding tasks.
- Produces a verified Staging P0 release candidate; no Production release.

- [ ] **Step 1: Run the complete local test suite**

Run:

```bash
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
git diff --check
```

Expected: all Python and Node tests PASS; no whitespace errors.

- [ ] **Step 2: Run the 2026-06 real-sample regression**

Preview the three approved files without modifying them and assert exact counts/totals from the design. Confirm them only against local SQLite or Staging test data. Verify the old ledger tables are unchanged before/after by count and checksum summaries.

- [ ] **Step 3: Update durable documentation**

Update README “当前已实现” and import usage. Add a Staging release-record entry only after local verification, including database impact, test counts, rollback point, and explicit “未发布到 Production”. Mark every completed plan checkbox.

- [ ] **Step 4: Commit documentation**

```bash
git add README.md 版本更新记录.md docs/superpowers/plans/2026-07-12-trading-management-implementation.md
git commit -m "docs: record trading management staging candidate"
```

- [ ] **Step 5: Push and verify Staging**

Push `staging`, wait for Render deployment, close previous project test tabs, and open `https://ltm-web-staging.onrender.com/?codex=<commit>` in the in-app browser. Verify URL/title, console health, static asset versions, old ledger availability, five new pages, three-file preview, fact/business labels, placeholder values, RB/HC candidates, all-option view, and rematch preview against test data.

- [ ] **Step 6: Stop before Production**

Report Staging results, database impact, test data cleanup, and rollback commit. Wait for explicit user approval before any merge to `main`, Production database change, or Production deploy.
