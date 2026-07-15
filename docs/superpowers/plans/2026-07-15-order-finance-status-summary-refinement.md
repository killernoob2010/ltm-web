# Order Finance Status and Summary Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refine order-finance card copy and make data-issue counts represent only data-quality warnings.

**Architecture:** Keep all imported warnings available to the existing risk engine, but filter `excel_alert` entries when calculating data-issue totals and import anomaly totals. Change only the existing frontend formatting helpers for shipment, document, and multi-financing summary text.

**Tech Stack:** Python/FastAPI backend, vanilla JavaScript frontend, Node test runner, pytest.

## Global Constraints

- Work only on `staging` and Render Staging.
- Do not change risk evaluation, WPS synchronization, database schema, or business data.
- Use test-first changes and stop before Gate B.

---

### Task 1: Data-quality-only anomaly count

**Files:**
- Modify: `tests/test_order_finance.py`
- Modify: `backend/app/order_finance.py`

**Interfaces:**
- Consumes: warning dictionaries stored in `import_warnings_json`.
- Produces: `_is_data_quality_warning(warning)` and filtered warning counts.

- [ ] **Step 1: Write the failing test**

Add assertions that an `excel_alert` can still produce risk but contributes zero to `data_issue_count`, while a date-quality warning contributes one.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_order_finance.py -q`
Expected: FAIL because `excel_alert` is still counted as a data issue.

- [ ] **Step 3: Write minimal implementation**

Add a helper that returns false only for warnings whose `field` is `excel_alert`. Use it for progress `data_issue_count` and parser/import `warning_count`; leave risk evaluation on the unfiltered warning list.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_order_finance.py -q`
Expected: PASS.

### Task 2: Card status and financing-summary copy

**Files:**
- Modify: `tests/order_finance_frontend.test.mjs`
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: `shipment_basis`, `document_date`, `financing_count`, `total_finance`, and financing bank names.
- Produces: compact strings from `orderFinanceShipmentText`, `orderFinanceDocumentText`, and `orderFinanceBankAmountText`.

- [ ] **Step 1: Write the failing test**

Assert that document-derived shipment text has no date, document status includes its date, and multiple financing renders amount before `（N笔）`.

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/order_finance_frontend.test.mjs`
Expected: FAIL on the old copy order and missing document date.

- [ ] **Step 3: Write minimal implementation**

Return `已根据交单日认定装船`, return `已交单 / ${item.document_date}`, and format multiple financing as `${amount}（${count}笔）`. Bump the frontend asset version.

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/order_finance_frontend.test.mjs`
Expected: PASS.

### Task 3: Staging delivery and focused acceptance

**Files:**
- Modify after deployment: `版本更新记录.md`

**Interfaces:**
- Consumes: committed Staging version.
- Produces: browser-visible evidence for `H-2026-3` and a release record.

- [ ] **Step 1: Run focused local verification**

Run the two focused suites, `node --check frontend/app.js`, and `git diff --check`.

- [ ] **Step 2: Commit and push Staging**

Commit only the scoped files and push `staging`.

- [ ] **Step 3: Verify the real Staging surface**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>` in the in-app browser. Confirm the asset version, the three `H-2026-3` strings, single-line layout, data-issue count, and clean console.

- [ ] **Step 4: Record deployment**

Append a concise Staging entry to `版本更新记录.md`, commit, and push it. Stop before Gate B.
