# Order Finance Weekly Focus Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the high-risk-only focus filter with a rolling 10-day weekly-focus workflow and add persistent per-item remarks with follow-up dates.

**Architecture:** Reuse `manager_note` and `next_follow_up_date`. The backend calculates reminder risk, focus membership, and focus reasons in the aggregated progress view; a new item-level PATCH endpoint updates every financing row for one item. The frontend adds a compact reminder line and dialog without changing the four-column field layout.

**Tech Stack:** FastAPI, Pydantic, SQLite/PostgreSQL-compatible data access, vanilla JavaScript, HTML/CSS, pytest, Node test runner.

## Global Constraints

- Work only on `staging`; do not merge or deploy Production.
- Keep the overall risk badge and four-column order card field layout.
- `本周重点` is today through day 10, not a calendar week.
- Manual reminder dates in the window or overdue create medium risk only; dates after day 10 create no risk and no focus membership.
- Excel re-import preserves `manager_note` and `next_follow_up_date`.
- Do not modify unrelated dirty files.

---

### Task 1: Backend weekly-focus calculation

**Files:**
- Modify: `tests/test_order_finance.py`
- Modify: `backend/app/order_finance.py`

**Interfaces:**
- Consumes: `_days_to`, `_group_shipment_completed`, `_group_indicator_risks`, `_build_progress_group`.
- Produces: `indicator_risks.reminder`, `is_weekly_focus`, `weekly_focus_reasons`, and weekly-focus semantics for `summary.focus_risk`.

- [ ] **Step 1: Write the failing boundary test**

Add `test_weekly_focus_uses_rolling_ten_day_actions` with active groups for shipment day 10/day 11, manual follow-up overdue/day 10/day 11, note-only data, a completed item, and a high-risk item. Assert risks, focus membership, reasons, and summary count.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/python -m pytest tests/test_order_finance.py::test_weekly_focus_uses_rolling_ten_day_actions -q`.
Expected: FAIL because reminder/focus fields do not exist and shipment still uses 7 days.

- [ ] **Step 3: Implement minimal calculation**

Extend the indicator map with `reminder`; set it to medium when an active normalized follow-up date has `_days_to(...) <= 10`; change shipment medium threshold to 10; calculate unique reasons from `high_risk`, `shipment_follow_up`, and `manual_follow_up`; output aggregate note/date/focus fields; count focused contracts in the summary.

- [ ] **Step 4: Verify GREEN**

Run `.venv/bin/python -m pytest tests/test_order_finance.py -q`.
Expected: all order-finance tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: calculate order finance weekly focus`.

### Task 2: Item-level reminder save and clear

**Files:**
- Modify: `tests/test_order_finance.py`
- Modify: `tests/test_auth_permissions.py`
- Modify: `backend/app/order_finance.py`

**Interfaces:**
- Consumes: `update_management_fields` and `_item_no`.
- Produces: `set_contract_reminder(item_no, manager_note, next_follow_up_date, updated_by)` and `PATCH /api/order-finance/contracts/{item_no}/reminder`.

- [ ] **Step 1: Write failing persistence and permission tests**

Test saving note/date to every row of multi-financing item `H-2026-3`, surviving re-import, clearing both values, rejecting an invalid date, and returning 403 without edit permission.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/python -m pytest tests/test_order_finance.py -k reminder tests/test_auth_permissions.py -q`.
Expected: FAIL because the item-level reminder function and endpoint do not exist.

- [ ] **Step 3: Implement request, function, and route**

Add `ContractReminderRequest`. Normalize note and date, accept an empty date as `None`, reject invalid dates, update all current rows sharing the item number, return normalized values and count, and protect the route with `order_finance_require_edit`.

- [ ] **Step 4: Verify GREEN**

Run `.venv/bin/python -m pytest tests/test_order_finance.py tests/test_auth_permissions.py -q`.
Expected: all tests pass.

- [ ] **Step 5: Commit**

Commit message: `feat: save order finance follow-up reminders`.

### Task 3: Frontend labels, dialog, and compact reminder

**Files:**
- Modify: `tests/order_finance_frontend.test.mjs`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`

**Interfaces:**
- Consumes: `manager_note`, `next_follow_up_date`, `is_weekly_focus`, `indicator_risks.reminder`, and the item reminder endpoint.
- Produces: `本周重点` labels, `orderFinanceReminderDialog`, compact reminder display, and edit/save/clear interactions.

- [ ] **Step 1: Write failing frontend tests**

Assert the new labels and dialog controls, `item.is_weekly_focus` filtering, shipment `days <= 10`, remark action/block, save/clear endpoint calls, confirmation before clear, and new asset cache versions.

- [ ] **Step 2: Verify RED**

Run `node --test tests/order_finance_frontend.test.mjs`.
Expected: FAIL on missing controls and reminder functions.

- [ ] **Step 3: Implement minimal UI**

Rename labels, add and bind the dialog, show note/date beneath next action, use `is_weekly_focus`, change shipment copy/threshold to 10, save and clear through the endpoint, reload progress, and add compact reminder-row styles. Bump script/style cache versions.

- [ ] **Step 4: Verify GREEN**

Run `node --test tests/*.test.mjs && node --check frontend/app.js`.
Expected: all Node tests and syntax checks pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add weekly focus reminder workflow`.

### Task 4: Regression, Staging deployment, and browser verification

**Files:**
- Modify after deployment: `版本更新记录.md`

**Interfaces:**
- Consumes: completed Tasks 1–3.
- Produces: verified Staging deployment, restored test data, and release record.

- [ ] **Step 1: Run complete verification**

Run the order-finance/permission pytest suite, all Node tests, JavaScript syntax check, backend compileall, and `git diff --check`. Run full pytest and record any pre-existing unrelated failure separately.

- [ ] **Step 2: Reconcile the real workbook**

Parse `/Users/wangjingze/建龙/贸易处/YOLANDA和香港建龙出口钢材信用证台账.xlsx` read-only. Record active risk distribution, weekly-focus count, and items included because of the 10-day shipment window.

- [ ] **Step 3: Push Staging**

Push `staging`, wait for Render to load cache-busted assets, and do not touch `main`.

- [ ] **Step 4: Browser-test and clean up**

On a low-risk item, save day 11 and verify no focus/risk; change to day 10 and verify medium/focus; change to overdue and verify medium/focus; re-import and verify persistence; clear and verify the original state. Also verify day-10/day-11 shipment boundaries, labels, compact note display, and page health.

- [ ] **Step 5: Record the release**

Update `版本更新记录.md` with commits, no schema impact, test counts, real-data/browser results, cleanup, rollback, and Production exclusion. Commit and push the release record to `staging`.
