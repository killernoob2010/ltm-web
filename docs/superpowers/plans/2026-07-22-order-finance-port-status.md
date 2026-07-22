# Order Finance Port Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `已放款待集港` and `已集港待装船` stages, persistent port confirmation, the two-day shipment-deadline risk rule, and matching filters and controls to the existing order-finance progress page.

**Architecture:** Keep all business rules in `backend/app/order_finance.py`, following the existing shipment-confirmation pattern. Store three nullable local-management fields in `order_finance_progress`, keep them outside `FACT_FIELDS` so WPS/Excel snapshots cannot overwrite them, and extend the existing server-rendered JSON view and vanilla-JavaScript page without introducing a new module.

**Tech Stack:** FastAPI, Pydantic, SQLite/PostgreSQL schema initialization, pytest, vanilla JavaScript/HTML, Node test runner, Render Staging.

## Global Constraints

- State order is exactly `待放款 → 已放款待集港 → 已集港待装船 → 已装船待交单 → 已交单待回款 → 已回款待结案 → 已完成`.
- `已放款待集港` preserves the existing unshipped risk behavior.
- `已集港待装船` is medium risk when the latest shipment date is more than 2 days away and high risk when it is 2 days away or less, today, overdue, missing, or invalid.
- Shipment or document facts immediately exit the port-specific rule and use the existing downstream risk logic.
- Port confirmation is environment-local management data and must not enter WPS fact snapshots.
- Do not change capital monitoring, WPS scheduling, permissions, other modules, `main`, or Production.
- Execute as D2/T2/R2/C1 with one Agent.

---

### Task 1: Persist and mutate port confirmation

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/order_finance.py`
- Test: `tests/test_order_finance.py`

**Interfaces:**
- Produces: nullable columns `port_confirmed_date`, `port_confirmed_by`, `port_confirmed_at`.
- Produces: `PortConfirmationRequest(confirmed: bool, port_confirmed_date: Optional[str])`.
- Produces: `set_port_confirmation(item_no: str, confirmed: bool, port_confirmed_date: Optional[str] = None, updated_by: str = "") -> Dict[str, Any]`.
- Produces: `PATCH /api/order-finance/contracts/{item_no}/port-confirmation`.
- Preserves: `FACT_FIELDS` remains unchanged, so snapshot export and apply continue to exclude port management fields.

- [ ] **Step 1: Write failing persistence and mutation tests**

Extend the schema assertion and add a test beside the shipment-confirmation persistence test:

```python
assert {
    "port_confirmed_date", "port_confirmed_by", "port_confirmed_at",
}.issubset(columns)


def test_port_confirmation_persists_across_reimport_and_can_be_undone(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    workbook = build_three_sheet_workbook(tmp_path / "order-finance.xlsx")
    import_order_finance_directory(workbook, imported_by="pytest")

    result = order_finance.set_port_confirmation(
        "Y-2026-3",
        confirmed=True,
        port_confirmed_date="2026-07-20",
        updated_by="pytest",
    )
    confirmed = [
        row for row in list_order_finance_records()
        if json.loads(row["source_json"])["item_no"] == "Y-2026-3"
    ]
    assert result == {"item_no": "Y-2026-3", "confirmed": True, "updated": 1}
    assert {row["port_confirmed_date"] for row in confirmed} == {"2026-07-20"}
    assert {row["port_confirmed_by"] for row in confirmed} == {"pytest"}

    import_order_finance_directory(workbook, imported_by="pytest")
    reimported = [
        row for row in list_order_finance_records()
        if json.loads(row["source_json"])["item_no"] == "Y-2026-3"
    ]
    assert {row["port_confirmed_date"] for row in reimported} == {"2026-07-20"}

    order_finance.set_port_confirmation("Y-2026-3", confirmed=False, updated_by="pytest")
    undone = [
        row for row in list_order_finance_records()
        if json.loads(row["source_json"])["item_no"] == "Y-2026-3"
    ]
    assert {row["port_confirmed_date"] for row in undone} == {None}
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest \
  tests/test_order_finance.py::test_order_finance_schema_adds_manual_shipment_confirmation_columns \
  tests/test_order_finance.py::test_port_confirmation_persists_across_reimport_and_can_be_undone -q
```

Expected: FAIL because the three columns and `set_port_confirmation` do not exist.

- [ ] **Step 3: Add the nullable schema fields and management API**

Add the three columns to both `CREATE TABLE order_finance_progress` declarations and to `migrate_order_finance_schema.columns`:

```python
"port_confirmed_date": "TEXT",
"port_confirmed_by": "TEXT",
"port_confirmed_at": "TEXT",
```

Add the fields to `MANAGEMENT_FIELDS` and `ORDER_FINANCE_LIST_FIELDS`, without adding them to `FACT_FIELDS`. Add the request model and service following the shipment implementation:

```python
class PortConfirmationRequest(BaseModel):
    confirmed: bool = True
    port_confirmed_date: Optional[str] = None


def set_port_confirmation(
    item_no: str,
    confirmed: bool,
    port_confirmed_date: Optional[str] = None,
    updated_by: str = "",
) -> Dict[str, Any]:
    normalized_item = _normalize_text(item_no)
    matching = [row for row in list_order_finance_records() if _item_no(row) == normalized_item]
    if not matching:
        raise KeyError(normalized_item)
    if confirmed:
        normalized_date = _normalize_date(port_confirmed_date or date.today().isoformat())
        if not _parse_date(normalized_date):
            raise ValueError("实际集港日格式不正确")
        changes = {
            "port_confirmed_date": normalized_date,
            "port_confirmed_by": updated_by,
            "port_confirmed_at": datetime.now().isoformat(timespec="seconds"),
        }
    else:
        changes = {
            "port_confirmed_date": None,
            "port_confirmed_by": None,
            "port_confirmed_at": None,
        }
    for row in matching:
        update_management_fields(row["id"], changes, updated_by=updated_by)
    return {"item_no": normalized_item, "confirmed": confirmed, "updated": len(matching)}
```

Expose the new PATCH route with `order_finance_require_edit`, translating `KeyError` to 404 and invalid dates to 400 exactly like shipment confirmation.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command again.

Expected: `2 passed` and the reimport retains the port confirmation.

- [ ] **Step 5: Commit the persistence slice**

```bash
git add backend/app/db.py backend/app/order_finance.py tests/test_order_finance.py
git commit -m "feat: persist order finance port confirmation"
```

---

### Task 2: Derive the port stages and two-day risk

**Files:**
- Modify: `backend/app/order_finance.py`
- Test: `tests/test_order_finance.py`

**Interfaces:**
- Consumes: `port_confirmed_date`, `port_confirmed_by`, and `port_confirmed_at` from Task 1.
- Produces: progress-view fields `port_confirmed_date`, `port_confirmed_by`, and `port_confirmed_at`.
- Produces: summary keys `financed_uncollected` and `collected_unshipped`.
- Preserves: existing `shipment_confirmed_date`, document, repayment, completion, warning, and reminder rules outside `已集港待装船`.

- [ ] **Step 1: Write failing stage and risk-boundary tests**

```python
def test_port_stage_uses_two_day_shipment_boundary():
    today = date.today()
    records = [
        progress_record("PORT-3", "存续", id=1,
                        port_confirmed_date=today.isoformat(),
                        latest_shipment_date=(today + timedelta(days=3)).isoformat()),
        progress_record("PORT-2", "存续", id=2,
                        port_confirmed_date=today.isoformat(),
                        latest_shipment_date=(today + timedelta(days=2)).isoformat()),
        progress_record("PORT-0", "存续", id=3,
                        port_confirmed_date=today.isoformat(),
                        latest_shipment_date=today.isoformat()),
        progress_record("PORT-OVERDUE", "存续", id=4,
                        port_confirmed_date=today.isoformat(),
                        latest_shipment_date=(today - timedelta(days=1)).isoformat()),
        progress_record("PORT-MISSING", "存续", id=5,
                        port_confirmed_date=today.isoformat(), latest_shipment_date=""),
    ]
    items = {item["item_no"]: item for item in build_order_finance_progress_view(records)["contracts"]}

    assert {item["stage"] for item in items.values()} == {"已集港待装船"}
    assert items["PORT-3"]["risk"] == "中"
    assert items["PORT-2"]["risk"] == "高"
    assert items["PORT-0"]["risk"] == "高"
    assert items["PORT-OVERDUE"]["risk"] == "高"
    assert items["PORT-MISSING"]["risk"] == "高"
    assert items["PORT-2"]["next_action"] == "确认装船状态或交单状态"


def test_shipment_and_document_override_port_stage():
    today = date.today().isoformat()
    shipped = progress_record("PORT-SHIPPED", "存续", port_confirmed_date=today,
                              shipment_confirmed_date=today)
    documented = progress_record("PORT-DOCUMENTED", "存续", port_confirmed_date=today,
                                 document_submission_date=today)
    items = {
        item["item_no"]: item
        for item in build_order_finance_progress_view([shipped, documented])["contracts"]
    }
    assert items["PORT-SHIPPED"]["stage"] == "已装船待交单"
    assert items["PORT-DOCUMENTED"]["stage"] == "已交单待回款"
```

Update the existing stage-set assertion to include `已放款待集港` and `已集港待装船`, and assert summary counts for both stages.

- [ ] **Step 2: Run the new tests and verify RED**

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest \
  tests/test_order_finance.py::test_port_stage_uses_two_day_shipment_boundary \
  tests/test_order_finance.py::test_shipment_and_document_override_port_stage -q
```

Expected: FAIL because port confirmation is not part of stage or risk derivation.

- [ ] **Step 3: Implement stage priority, risk override, view fields, and summary**

Add port priority after shipment:

```python
if _group_has_value(rows, "shipment_confirmed_date"):
    return "已装船待交单"
if _group_has_value(rows, "port_confirmed_date"):
    return "已集港待装船"
return "已放款待集港" if has_loan else "待放款"
```

At the start of `_group_indicator_risks`, after completed/paid/documented early returns, add a port-stage early return:

```python
if stage == "已集港待装船":
    shipment_days = [
        _days_to(row.get("latest_shipment_date"))
        for row in rows if row.get("latest_shipment_date")
    ]
    valid_days = [days for days in shipment_days if days is not None]
    risks["shipment"] = "高" if not valid_days or min(valid_days) <= 2 else "中"
    return risks
```

Return the port confirmation audit fields from `_build_progress_group`; use the exact next-action copy from the spec; include high port-stage items in weekly focus; rename the old summary key to `financed_uncollected` and add `collected_unshipped`.

- [ ] **Step 4: Run the order-finance backend suite and verify GREEN**

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_order_finance.py -q
```

Expected: all order-finance tests pass, including unchanged shipment, document, repayment, WPS snapshot, and completion tests.

- [ ] **Step 5: Commit the business-rule slice**

```bash
git add backend/app/order_finance.py tests/test_order_finance.py
git commit -m "feat: add order finance port stage risk"
```

---

### Task 3: Add port controls, filter, summary, and display

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Test: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Consumes: `stage`, `risk`, `port_confirmed_date`, `financed_uncollected`, and `collected_unshipped` from Task 2.
- Consumes: `PATCH /api/order-finance/contracts/{item_no}/port-confirmation` from Task 1.
- Produces: filter IDs `financedUncollected` and `collectedUnshipped`.
- Produces: dialog IDs `orderFinancePortDialog`, `orderFinancePortForm`, `orderFinancePortItem`, and `orderFinancePortDate`.

- [ ] **Step 1: Write failing frontend contract tests**

Add one focused Node test:

```javascript
test("order finance supports port confirmation and the collected-unshipped stage", () => {
  assert.match(indexHtml, /data-filter="financedUncollected"[^>]*>已放款待集港<\/button>/);
  assert.match(indexHtml, /data-filter="collectedUnshipped"[^>]*>已集港待装船<\/button>/);
  assert.match(indexHtml, /id="orderFinancePortDialog"/);
  assert.match(indexHtml, /id="orderFinancePortDate"[^>]*type="date"/);
  assert.match(appJs, /filter === "financedUncollected" && item\.stage !== "已放款待集港"/);
  assert.match(appJs, /filter === "collectedUnshipped" && item\.stage !== "已集港待装船"/);
  assert.match(appJs, /order-finance-port-confirm-btn/);
  assert.match(appJs, /order-finance-port-undo-btn/);
  assert.match(appJs, /\/port-confirmation/);
  assert.match(appJs, /已确认集港：\$\{item\.port_confirmed_date\}/);
  assert.match(appJs, /\["已放款待集港", summary\.financed_uncollected \|\| 0\]/);
  assert.match(appJs, /\["已集港待装船", summary\.collected_unshipped \|\| 0\]/);
});
```

- [ ] **Step 2: Run the frontend test and verify RED**

```bash
node --test tests/order_finance_frontend.test.mjs
```

Expected: FAIL because the port dialog, actions, filters, and display do not exist.

- [ ] **Step 3: Implement the minimal existing-page interaction**

In `frontend/index.html`, replace the old financed-unshipped filter with two buttons and add the date dialog:

```html
<button class="filter-button" data-filter="financedUncollected" type="button">已放款待集港</button>
<button class="filter-button" data-filter="collectedUnshipped" type="button">已集港待装船</button>

<dialog id="orderFinancePortDialog">
  <form id="orderFinancePortForm" method="dialog" class="dialog-form">
    <h2>确认已集港</h2>
    <p id="orderFinancePortItem" class="toolbar-status"></p>
    <label>实际集港日 <input id="orderFinancePortDate" type="date" required /></label>
    <div class="dialog-actions">
      <button id="cancelOrderFinancePortBtn" type="button" class="secondary">取消</button>
      <button type="submit">确认已集港</button>
    </div>
  </form>
</dialog>
```

In `frontend/app.js`, bind the dialog elements, add `orderFinancePortItemNo`, update stage filtering and summary labels, and implement these actions:

```javascript
async function saveOrderFinancePort(event) {
  event.preventDefault();
  await api(`/api/order-finance/contracts/${encodeURIComponent(orderFinancePortItemNo)}/port-confirmation`, {
    method: "PATCH",
    body: JSON.stringify({ confirmed: true, port_confirmed_date: orderFinancePortDate.value }),
  });
  orderFinancePortDialog.close();
  await loadOrderFinanceProgress();
  orderFinanceStatus.textContent = `已确认 ${orderFinancePortItemNo} 集港`;
}
```

Implement the undo request with `{ confirmed: false }`. Render “确认已集港” only in `已放款待集港`; render “确认已装船”和“撤销集港确认” in `已集港待装船`; keep existing shipment undo behavior. Show `已确认集港：日期` before the ordinary deadline text when no shipment/document fact exists.

Update the `app.js` and `styles.css` query versions in `frontend/index.html` to one new shared order-finance version string so Render cannot serve stale behavior.

- [ ] **Step 4: Run frontend tests and syntax checks**

```bash
node --test tests/order_finance_frontend.test.mjs
node --check frontend/app.js
git diff --check
```

Expected: all order-finance frontend tests pass; JavaScript syntax and whitespace checks return exit code 0.

- [ ] **Step 5: Commit the page slice**

```bash
git add frontend/index.html frontend/app.js tests/order_finance_frontend.test.mjs
git commit -m "feat: add order finance port controls"
```

---

### Task 4: Verify, deploy to Staging, and record the tested result

**Files:**
- Modify after successful Staging deployment: `版本更新记录.md`
- Verify: `backend/app/db.py`
- Verify: `backend/app/order_finance.py`
- Verify: `frontend/index.html`
- Verify: `frontend/app.js`
- Verify: `tests/test_order_finance.py`
- Verify: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Consumes: all Tasks 1-3.
- Produces: one tested Staging commit and release-record commit on `staging`.
- Does not produce: any `main` or Production change.

- [ ] **Step 1: Run the local T2 quality gate**

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_order_finance.py -q
node --test tests/order_finance_frontend.test.mjs
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
node --check frontend/app.js
git diff --check
git status --short
```

Expected: focused and full Python/Node suites pass, syntax and diff checks pass, and only intentional commits are present.

- [ ] **Step 2: Record local T2 evidence and push the tested code to Staging**

Record the exact counts with `ai_sdlc.cli record-test`, then push without changing the dirty canonical checkout:

```bash
git push origin HEAD:staging
```

Expected: GitHub accepts the update and Render begins deploying `ltm-web-staging`.

- [ ] **Step 3: Verify the real Render Staging surface**

Open:

```text
https://ltm-web-staging.onrender.com/?codex=<tested-commit>
```

Confirm URL/title, the new `app.js` asset version, no console errors or warnings, `已放款待集港` and `已集港待装船` filters, confirm/undo port interactions, port-to-shipment flow, the 3-day/2-day risk boundary, and unchanged shipment/document behavior. Use only reversible Staging test data and remove it after acceptance.

- [ ] **Step 4: Record Staging T2 evidence and update the release record**

Record the browser-visible acceptance with `ai_sdlc.cli record-test` and `record-release`. Add a concise `版本更新记录.md` entry containing environment, tested commit, scope, test counts, browser evidence, database impact, rollback point, and the explicit statement that Production was not changed.

- [ ] **Step 5: Commit and push the post-deploy record**

```bash
git add 版本更新记录.md
git commit -m "docs: record order finance port staging release"
git push origin HEAD:staging
```

Expected: the docs-only commit deploys successfully; recheck `/api/health` and the target page. Stop at `staging_delivered` and present Gate B separately if Production is later requested.
