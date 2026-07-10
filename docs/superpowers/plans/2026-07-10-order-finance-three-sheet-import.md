# Order Finance Three-Sheet Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make order finance imports read only the Excel sheets `订单`, `额度`, and `预警`, use `订单` as the authoritative order source, and correct the bill-of-lading/document-submission/repayment semantics without changing the approved web layout.

**Architecture:** Keep `backend/app/order_finance.py` as the module boundary. Parse order rows into the existing `order_finance_progress` table, store workbook-level quota metadata and row-level alert provenance inside the existing auditable `source_json`/`import_warnings_json` fields, and derive progress/capital views from those persisted facts. Keep the current two-page frontend and card/detail structure; change only field bindings, labels, and stage predicates.

**Tech Stack:** Python 3.9, FastAPI, openpyxl, SQLite/PostgreSQL-compatible data access, vanilla JavaScript, Node test runner, pytest.

## Global Constraints

- Work only on `staging`; do not touch `main` or Production.
- Parse only Excel sheets `订单`, `额度`, and `预警`; ignore every other sheet.
- `订单` is the only order fact source; do not backfill from `数据合并`, `YOLANDA`, `JLHK`, `合作`, or `映射`.
- Preserve the existing two web pages, card layout, filters, detail expansion, capital layout, and import flow.
- Treat `提单日`, bank `交单日`, and `还款日` as different milestones.
- Explicit `存续` overrides the presence of a repayment date; explicit `结案` is completed.
- Do not overwrite management fields during re-import.
- Preserve unrelated local changes in `.gitignore`, data-visualization backup/handoff files, and data-visualization scripts.

---

### Task 1: Parse the three target Excel sheets

**Files:**
- Modify: `backend/app/order_finance.py`
- Modify: `tests/test_order_finance.py`

**Interfaces:**
- Consumes: uploaded `.xlsx` bytes or a local workbook path.
- Produces: `parse_order_finance_xlsx_workbook(path) -> {file, sheet, sheets, records, capital, summary}` where records come only from `订单`, capital comes only from `额度`, and alert warnings come only from `预警`.

- [ ] **Step 1: Write failing synthetic-workbook tests**

Create a temporary workbook containing `订单`, `额度`, `预警`, and a deliberately conflicting `数据合并` sheet. Assert that:

```python
assert parsed["sheet"] == "订单"
assert parsed["sheets"] == {"订单": True, "额度": True, "预警": True}
assert {row["source_sheet"] for row in parsed["records"]} == {"订单"}
assert parsed["records"][0]["source_json"].find("CONFLICT-DATA-MERGE") == -1
```

Also assert field aliases, repayment mapping to `tail_payment_date`, alert attachment by item number, duplicate-item warnings, and quota metadata parsing.

- [ ] **Step 2: Run the focused parser tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -q -k 'three_sheet or duplicate_item or target_sheet'
```

Expected: failures showing the current parser still requires `数据合并` and does not return `sheets`/`capital`.

- [ ] **Step 3: Implement the minimal three-sheet parser**

Add focused helpers in `backend/app/order_finance.py` with these exact interfaces:

```python
TARGET_XLSX_SHEETS = ("订单", "额度", "预警")

def _xlsx_header_map(headers: list[Any]) -> dict[str, int]:
    return {
        _normalize_text(header).replace("\n", ""): index
        for index, header in enumerate(headers)
        if _normalize_text(header)
    }

def _parse_order_sheet(book, path: Path, alerts_by_item: dict[str, list[dict[str, str]]], capital: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        record
        for row_index, values in enumerate(book["订单"].iter_rows(min_row=4, values_only=True), start=4)
        if (record := _order_sheet_record(path, book["订单"].title, values, row_index, alerts_by_item, capital))
    ]

def _parse_quota_sheet(book) -> dict[str, Any]:
    return {"banks": _quota_bank_rows(book["额度"])}

def _parse_alert_sheet(book) -> dict[str, list[dict[str, str]]]:
    return _alerts_grouped_by_item(book["预警"])
```

Map item, quantity, product/material, supplier/factory, contract, bank, amount, rate, borrow/original due/extension/new due, bill-of-lading date, document-submission date, repayment date, and status from `订单`. Store standardized auxiliary values in `source_json` so no database migration is required.

- [ ] **Step 4: Run parser tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -q -k 'three_sheet or duplicate_item or target_sheet'
```

Expected: all selected tests pass.

### Task 2: Correct lifecycle, warnings, and capital calculations

**Files:**
- Modify: `backend/app/order_finance.py`
- Modify: `tests/test_order_finance.py`

**Interfaces:**
- Consumes: persisted order records with `business_status` in `存续/结案` and auxiliary source metadata.
- Produces: progress contracts with stages `待放款`, `已放款待装船`, `已装船待回款`, `已交单待回款`, `已还款待结案`, `已完成`; capital metrics based on quota-sheet limits/usage and order-sheet active amounts.

- [ ] **Step 1: Write failing lifecycle tests**

Cover these exact cases:

```python
assert stage(active_with_repayment) == "已还款待结案"
assert stage(closed_without_repayment) == "已完成"
assert stage(active_with_document_without_repayment) == "已交单待回款"
assert stage(active_with_bill_without_document) == "已装船待回款"
```

Assert that `missing_milestones` checks bill/document/repayment, that alerts increase `data_issues`, and that the capital view uses parsed quota limits/current usage while exposing the order-calculated usage and difference.

- [ ] **Step 2: Run focused view tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -q -k 'explicit_status or lifecycle or capital_uses_quota'
```

Expected: failures because the current logic treats repayment as automatic completion, depends on collection date, and uses hardcoded limits.

- [ ] **Step 3: Implement minimal lifecycle and capital logic**

Update `_is_completed_group`, `_group_stage`, `_group_risk`, `_group_next_action`, `_build_progress_group`, `build_order_finance_progress_view`, and `build_order_finance_capital_view` to use explicit status and the three corrected milestones. Keep legacy manual records compatible when explicit `存续/结案` is absent.

- [ ] **Step 4: Run focused and full backend tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py tests/test_auth_permissions.py -q
```

Expected: all tests pass.

### Task 3: Rebind the existing frontend without changing layout

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Consumes: corrected `/api/order-finance/progress` and `/api/order-finance/capital` payloads.
- Produces: the same page/card/detail DOM structure with corrected labels and fields.

- [ ] **Step 1: Write failing frontend source-contract tests**

Assert the existing layout classes remain and the renderer now contains:

```javascript
assert.match(contractRenderer, /orderFinanceField\("提单日"/);
assert.match(contractRenderer, /orderFinanceField\("展期状态"/);
assert.match(detailRenderer, /<th>提单日<\/th>/);
assert.match(detailRenderer, /<th>交单日<\/th>/);
assert.match(detailRenderer, /<th>还款日<\/th>/);
assert.doesNotMatch(detailRenderer, /<th>收汇日<\/th>/);
```

Also assert corrected summary/filter labels `已交单待回款`, `已还款待结案`, and `缺提单/交单/还款`.

- [ ] **Step 2: Run frontend tests and verify RED**

Run:

```bash
node --test tests/order_finance_frontend.test.mjs
```

Expected: failures on old shipment/collection labels.

- [ ] **Step 3: Update only labels and bindings**

Keep `order-finance-summary`, `order-finance-workbench`, `order-finance-field-strip`, `order-finance-detail-table`, filter button order, expand behavior, and capital DOM unchanged. Bind bill date, document date, repayment date, and extension metadata from the corrected API.

- [ ] **Step 4: Run frontend tests and syntax check**

Run:

```bash
node --test tests/order_finance_frontend.test.mjs
node --check frontend/app.js
```

Expected: all tests pass and syntax check exits 0.

### Task 4: Refresh fallback data, documentation, and staging deployment

**Files:**
- Modify: `backend/app/order_finance_seed.json`
- Modify: `README.md`
- Modify after staging deployment: `版本更新记录.md`

**Interfaces:**
- Consumes: current local target workbook and completed parser.
- Produces: a fallback seed generated by the same three-sheet parser, current setup documentation, and a post-deploy release record.

- [ ] **Step 1: Generate fallback seed from the current workbook**

Use the completed parser to serialize the result to `backend/app/order_finance_seed.json`. Confirm every record has `source_sheet == "订单"` and no record source contains `数据合并`.

- [ ] **Step 2: Run full local verification**

Run:

```bash
.venv/bin/python -m pytest -q
node --test tests/*.mjs
node --check frontend/app.js
PYTHONPYCACHEPREFIX=/tmp/ltm-pycache .venv/bin/python -m compileall backend/app
git diff --check
```

Expected: zero failures and zero syntax/diff errors.

- [ ] **Step 3: Commit only order-finance scope and push staging**

Stage the exact order-finance files, plan, README, and static version changes. Do not stage pre-existing unrelated files. Push `staging` to `origin/staging`.

- [ ] **Step 4: Verify Render Staging in a clean browser tab**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>`, log in, import the current workbook, and verify:

- import report identifies `订单`, `额度`, and `预警`;
- order cards keep the approved layout;
- bill/document/repayment fields have corrected meanings;
- active/closed and `已还款待结案` behavior is correct;
- capital totals come from `额度` and order usage is cross-checked;
- filters, expand/collapse, bank selection, and page switching work;
- console has no application errors;
- deployed static resource query includes the new version token.

- [ ] **Step 5: Update and commit the staging release record**

Record the deployed commit, database impact (`none`), imported staging test data scope, local test counts, browser-visible verification, and rollback point in `版本更新记录.md`, then push the documentation commit to `staging`.
