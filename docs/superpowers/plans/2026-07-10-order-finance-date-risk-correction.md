# Order Finance Date and Risk Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the latest shipment deadline on order cards and correct effective due-date, repayment-timeliness, completed-order, multi-financing, and risk calculations.

**Architecture:** Keep the existing three-sheet importer and two-page UI. Normalize shipment and effective financing dates in `backend/app/order_finance.py`, expose presentation-ready values in the existing progress view, and render them through the existing card/detail components in `frontend/app.js` without changing page structure.

**Tech Stack:** Python 3.9, FastAPI, openpyxl importer already used by the project, vanilla JavaScript, Node test runner, pytest.

## Global Constraints

- Only Excel sheets `订单`, `额度`, and `预警` are data sources.
- `最迟装船日` and `提单日` are different fields and must never fill each other.
- Completed orders do not participate in high/medium/low risk or data-issue totals.
- Multiple financing rows do not create a warning and do not change risk.
- Keep the existing page structure, card count, field count, and capital-monitor layout.
- Do not change Production.

---

### Task 1: Correct backend date ingestion and risk semantics

**Files:**
- Modify: `backend/app/order_finance.py`
- Test: `tests/test_order_finance.py`

**Interfaces:**
- Consumes: `订单.最迟装船日`, `原到期日`, `展期天数`, `新到期日`, `还款日`, `状态`.
- Produces: record `latest_shipment_date`, progress item `latest_shipment_date`, `repayment_timing`, corrected `risk`, and active-only `data_issues`.

- [ ] **Step 1: Write failing parsing and grouping tests**

Add tests that create synthetic `订单/额度/预警` sheets and assert:

```python
assert record["latest_shipment_date"] == "2026-07-20"
assert progress_item["latest_shipment_date"] == "2026-07-20"
assert progress_item["latest_due_date"] == "2026-08-15"
assert progress_item["repayment_timing"] == "提前 2 天还款"
```

Add a multi-row item test where two financing rows remain independently represented but do not create `item_no` warnings and do not raise risk.

- [ ] **Step 2: Run backend tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -q
```

Expected: failures for missing latest-shipment mapping, duplicate warnings, repayment timing, and the old risk rules.

- [ ] **Step 3: Implement effective dates and corrected risk**

In `_order_sheet_record`, map `最迟装船日` to `latest_shipment_date`. Resolve effective financing due date in this order: valid `新到期日`; otherwise valid `原到期日 + 展期天数`; otherwise original due date. Stop appending duplicate-item warnings in `_parse_order_sheet`.

Update `_group_risk` so completed groups return `已完成`; active groups are high only for severe active warnings or unpaid effective due within 7 days/overdue; medium for unpaid due within 8–30 days or repaid-but-still-active; otherwise low. Remove stage and row-count risk promotion.

Add a helper that compares `tail_payment_date` with effective `finance_due_date` and returns:

```python
"提前 N 天还款" | "按期还款" | "逾期 N 天还款" | ""
```

For grouped items, use the earliest non-empty latest shipment date and expose the repayment-timing result alongside the existing latest due and repayment dates.

- [ ] **Step 4: Correct active-only summary calculations**

Calculate `data_issues` only from `open_contracts`. Calculate missing milestones using `latest_shipment_date`, `document_date`, and `repay_date` rather than `bill_date`.

- [ ] **Step 5: Run backend tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py tests/test_auth_permissions.py -q
```

Expected: all tests pass.

### Task 2: Correct card and detail presentation

**Files:**
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`
- Test: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Consumes: progress item `latest_shipment_date`, `latest_due_date`, `repay_date`, `repayment_timing`, `stage`, and `risk`.
- Produces: unchanged card layout with corrected labels, countdowns, tones, and seven-column financing details.

- [ ] **Step 1: Write failing frontend tests**

Assert the card renderer contains `orderFinanceField("最迟装船日"` and `orderFinanceField("还款日"`, does not contain `orderFinanceField("提单日"`, and the detail header no longer contains `<th>提单日</th>`. Assert the summary label is `缺最迟装船/交单/还款`.

- [ ] **Step 2: Run frontend test and verify RED**

Run:

```bash
node --test tests/order_finance_frontend.test.mjs
```

Expected: failures showing the old labels and detail column.

- [ ] **Step 3: Implement the minimal UI changes**

Add a shipment-deadline formatter that shows `日期 / N 天后需确认`, `日期 / 7 天内需联系工厂`, or `日期 / 已超过 N 天`, with normal/yellow/red tone. Completed missing values show `未提供`; active missing values show `待 Excel 补充`.

For completed orders, render financing due as `有效到期日 / repayment_timing` instead of comparing the due date with today. Rename the card repayment field to `还款日`. Remove the bill-date column and cells from `renderOrderFinanceFinancingRows`. Update the waiting-for-shipment next-action wording to contact the factory about shipment progress.

Bump the CSS and JavaScript query versions in `frontend/index.html` to a new `order-finance-shipment-risk-20260710` value.

- [ ] **Step 4: Run frontend tests and verify GREEN**

Run:

```bash
node --test tests/*.mjs
node --check frontend/app.js
```

Expected: all Node tests pass and JavaScript syntax is valid.

### Task 3: Real workbook, staging import, and release verification

**Files:**
- Modify: `backend/app/order_finance_seed.json`
- Modify: `版本更新记录.md`

**Interfaces:**
- Consumes: `/Users/wangjingze/建龙/贸易处/YOLANDA和香港建龙出口钢材信用证台账.xlsx`.
- Produces: current seed snapshot, committed staging code, updated staging data, and browser-verification evidence.

- [ ] **Step 1: Verify the real workbook locally**

Parse the real workbook and assert only `订单/额度/预警` contribute data. Report active/completed counts, effective due dates, latest shipment dates, risk distribution, completed repayment-timing distribution, and active-only data issues.

- [ ] **Step 2: Run complete relevant verification**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py tests/test_auth_permissions.py -q
node --test tests/*.mjs
node --check frontend/app.js
.venv/bin/python -m compileall -q backend/app
git diff --check
```

Expected: all commands pass.

- [ ] **Step 3: Commit and push staging**

Stage only order-finance implementation, tests, seed, spec, plan, and release-record files. Preserve unrelated dirty files. Push `staging` and wait for Render Staging to serve the new static version.

- [ ] **Step 4: Re-import the latest workbook to Staging**

Use the authenticated Staging import endpoint with the latest workbook. Confirm 70 records are processed and the target sheet availability is true.

- [ ] **Step 5: Validate in the in-app browser**

Open a clean Staging tab and verify page identity, nonblank content, no framework overlay, no console errors/warnings, card `最迟装船日` countdown/tone, completed repayment timing, `还款日`, seven-column details without `提单日`, corrected risk distribution, and unchanged capital totals. Capture one screenshot and keep the final Staging tab as deliverable.

- [ ] **Step 6: Record and push the Staging release note**

Add the exact commit, data-import summary, risk counts, repayment-timing distribution, browser evidence, database impact, and rollback point to `版本更新记录.md`; commit and push `staging`.
