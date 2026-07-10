# Order Finance Shipment Confirmation and Indicator Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist manual shipment confirmation, color only the fields that cause high/medium risk, retain the overall risk badge, and remove the import-report panel.

**Architecture:** Add three nullable management columns to `order_finance_progress`, preserve them through Excel imports, and expose a group-level confirmation endpoint. Build an `indicator_risks` object in the progress view and let the frontend map each indicator to red/yellow/no tone while the overall risk remains the maximum indicator risk.

**Tech Stack:** Python 3.9, FastAPI, SQLite/PostgreSQL compatibility migrations, vanilla JavaScript, pytest, Node test runner.

## Global Constraints

- Staging data backup exists at `/tmp/order_finance_staging_backup_before_shipment_confirmation_20260710_122043.json` with 70 records.
- Migration is additive only: `shipment_confirmed_date`, `shipment_confirmed_by`, and `shipment_confirmed_at`.
- Manual shipment confirmation is not a bill-of-lading date.
- Completed orders never receive red/yellow indicator backgrounds.
- Multiple financing rows do not change risk; group confirmation applies to every row in the item.
- Preserve unrelated dirty files and do not touch Production.

---

### Task 1: Persist and expose manual shipment confirmation

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/order_finance.py`
- Test: `tests/test_order_finance.py`

**Interfaces:**
- Produces columns `shipment_confirmed_date TEXT`, `shipment_confirmed_by TEXT`, `shipment_confirmed_at TEXT`.
- Produces `PATCH /api/order-finance/contracts/{item_no}/shipment-confirmation` with body `{ "confirmed": true, "shipment_confirmed_date": "YYYY-MM-DD" }` or `{ "confirmed": false }`.

- [ ] Write failing tests that initialize a temporary database, assert the three columns exist, confirm all rows for one item, re-import the workbook, and assert the fields remain.
- [ ] Run `.venv/bin/python -m pytest tests/test_order_finance.py -q` and verify the new tests fail for missing schema/API helpers.
- [ ] Add the columns to PostgreSQL and SQLite create-table DDL plus an idempotent `migrate_order_finance_schema(conn)` called by `init_db()`.
- [ ] Add the fields to management insert/list handling, a request model, a group update helper with date validation and change logging, and the authenticated edit endpoint.
- [ ] Run the backend tests and verify they pass.

### Task 2: Compute indicator-level risk and shipment-completed state

**Files:**
- Modify: `backend/app/order_finance.py`
- Test: `tests/test_order_finance.py`

**Interfaces:**
- Produces progress fields `shipment_completed`, `shipment_confirmed_date`, `shipment_confirmed_by`, `shipment_confirmed_at`, and `indicator_risks` with keys `shipment`, `finance_due`, `repayment`, `confirmation` and values `高`, `中`, or `低`.

- [ ] Write failing tests for missing shipment date high, overdue shipment high, shipment within seven days medium, finance due within thirty days medium, repaid-but-active medium on repayment, manual/document confirmation suppressing shipment risk, low fields without risk, and completed fields all low.
- [ ] Run the specific pytest tests and verify RED.
- [ ] Implement shipment-completed inference from manual confirmation, bill date, document date, or repayment date. Map Excel shipment/due warnings to the appropriate indicator, compute indicator levels, and derive overall risk from their maximum.
- [ ] Make manual confirmation move an unpaid item from `已放款待装船` to `已装船待回款`; undo restores date-driven behavior.
- [ ] Run backend and permission tests and verify GREEN.

### Task 3: Add card actions, field colors, and remove import report

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Test: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Consumes progress `indicator_risks` and shipment confirmation fields.
- Calls the shipment-confirmation endpoint, then reloads progress.

- [ ] Write failing frontend tests for retained overall risk badge, `indicatorRiskTone(item, key)`, confirmation/undo controls, shipment-date dialog, absence of `orderFinanceImportReport`, and import completion status only.
- [ ] Run `node --test tests/order_finance_frontend.test.mjs` and verify RED.
- [ ] Add a compact shipment-confirmation dialog with a date defaulting to today; add confirm/undo actions to card headers; call the endpoint and reload progress.
- [ ] Apply red/yellow tones only from `indicator_risks`; low fields return no tone. Show manual confirmation in the shipment field and suppress deadline countdown after shipment completion.
- [ ] Remove import-report HTML, DOM bindings, and row rendering; keep `导入完成：N 条，异常 N 条` in the toolbar status.
- [ ] Bump static asset query versions and run all Node tests plus JavaScript syntax check.

### Task 4: Deploy and verify Staging

**Files:**
- Modify: `版本更新记录.md`

- [ ] Run order-finance/permission tests, all Node tests, compileall, and `git diff --check`.
- [ ] Commit only scoped files, push `staging`, and wait for the new static version.
- [ ] Verify the additive migration through the Staging API, then re-import the latest Excel and confirm 70 records.
- [ ] In a clean in-app browser tab, confirm a missing shipment-date field is red/high, a 8–30 day due field is yellow/medium, low fields are white, completed fields have no risk tone, manual confirmation changes stage and removes shipment risk, undo restores it, import report is absent, capital values are unchanged, and console logs are clean.
- [ ] Update and push the release record with migration, backup, data, test, browser, and rollback evidence.
