# Order Finance Post-Document Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an order with a document-submission date depend only on unpaid-financing due dates for current risk.

**Architecture:** Add a documented-stage early branch to the existing order-finance risk aggregator and keep all pre-document logic unchanged. Reuse the existing API and card layout; only add explicit missing-due text and count that condition as a data issue.

**Tech Stack:** Python 3, FastAPI, pytest, vanilla JavaScript, Node test runner, Render Staging.

## Global Constraints

- D/T/R/C: D2 / T2 / R1 / C1.
- Modify only order-finance risk calculation, focus selection, missing-due display, tests, and the Staging release record.
- Do not modify database schema, WPS scheduler, WPS authorization, snapshot apply, capital monitoring, permissions, or Production.
- After documentation, future unpaid due dates are medium; due today or overdue is high; missing due is medium plus data issue; all paid unclosed is low.
- After documentation, shipment/document warnings, other WPS warnings, and manual reminders do not change total risk or add weekly focus.
- Use one main Agent. Production release remains behind Gate B.

---

### Task 1: Lock repayment-only risk after documentation

**Files:**
- Modify: `tests/test_order_finance.py`
- Modify: `backend/app/order_finance.py:1766-1820`
- Modify: `backend/app/order_finance.py:1822-1848`
- Modify: `backend/app/order_finance.py:1883-1960`

**Interfaces:**
- Consumes: `_group_stage`, `_row_is_paid`, `_days_to`.
- Produces: payment-only `indicator_risks` for `已交单待回款`; `weekly_focus_reasons` containing only `high_risk` for documented orders; missing due dates included in `data_issue_count`.

- [ ] **Step 1: Write failing boundary tests**

Add to `tests/test_order_finance.py`:

```python
def test_documented_unpaid_risk_uses_due_day_boundary_only():
    today = date.today()
    records = [
        progress_record(
            f"DOC-{days}", "存续", id=index,
            business_key=f"ITEM|DOC-{days}|1",
            document_submission_date=today.isoformat(),
            finance_due_date=(today + timedelta(days=days)).isoformat(),
            import_warnings_json=json.dumps([
                {"field": "excel_alert", "level": "高", "message": "交单旧预警"},
            ], ensure_ascii=False),
            next_follow_up_date=today.isoformat(),
        )
        for index, days in enumerate((-1, 0, 1, 31), start=1)
    ]
    items = {
        item["item_no"]: item
        for item in build_order_finance_progress_view(records)["contracts"]
    }

    assert items["DOC--1"]["risk"] == "高"
    assert items["DOC-0"]["risk"] == "高"
    assert items["DOC-1"]["risk"] == "中"
    assert items["DOC-31"]["risk"] == "中"
    assert items["DOC-1"]["indicator_risks"] == {
        "shipment": "低", "document": "低", "payment": "中", "reminder": "低",
    }
    assert items["DOC-1"]["weekly_focus_reasons"] == []
    assert items["DOC-0"]["weekly_focus_reasons"] == ["high_risk"]


def test_documented_missing_due_is_medium_data_issue():
    item = build_order_finance_progress_view([
        progress_record(
            "DOC-MISSING-DUE", "存续",
            document_submission_date=date.today().isoformat(),
            finance_due_date="",
        ),
    ])["contracts"][0]

    assert item["stage"] == "已交单待回款"
    assert item["risk"] == "中"
    assert item["indicator_risks"]["payment"] == "中"
    assert item["data_issue_count"] == 1
    assert item["is_weekly_focus"] is False


def test_documented_multiple_financings_ignore_paid_rows():
    today = date.today()
    paid = progress_record(
        "DOC-MULTI", "存续",
        document_submission_date=today.isoformat(),
        finance_due_date=(today - timedelta(days=10)).isoformat(),
        tail_payment_date=(today - timedelta(days=11)).isoformat(),
    )
    unpaid = progress_record(
        "DOC-MULTI", "存续", id=2, business_key="ITEM|DOC-MULTI|2",
        document_submission_date=today.isoformat(),
        finance_due_date=(today + timedelta(days=60)).isoformat(),
    )

    item = build_order_finance_progress_view([paid, unpaid])["contracts"][0]
    assert item["payment_progress"] == "部分回款 1/2笔"
    assert item["risk"] == "中"


def test_all_paid_unclosed_is_low_and_not_focus():
    item = build_order_finance_progress_view([
        progress_record(
            "DOC-PAID", "存续",
            document_submission_date=date.today().isoformat(),
            finance_due_date=date.today().isoformat(),
            tail_payment_date=date.today().isoformat(),
            next_follow_up_date=date.today().isoformat(),
        ),
    ])["contracts"][0]

    assert item["stage"] == "已回款待结案"
    assert item["risk"] == "低"
    assert item["indicator_risks"] == {
        "shipment": "低", "document": "低", "payment": "低", "reminder": "低",
    }
    assert item["is_weekly_focus"] is False
```

- [ ] **Step 2: Confirm the tests fail before implementation**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k \
  'documented_unpaid_risk or documented_missing_due or documented_multiple_financings or all_paid_unclosed' -v
```

Expected: failures show the current 7/30-day thresholds, missing issue count, and manual reminder behavior.

- [ ] **Step 3: Implement the documented-stage early branch**

In `_group_indicator_risks`, immediately after the completed return:

```python
    if stage == "已回款待结案":
        return risks
    if stage == "已交单待回款":
        unpaid_rows = [row for row in rows if not _row_is_paid(row)]
        due_days = [
            _days_to(row.get("finance_due_date"))
            for row in unpaid_rows
            if row.get("finance_due_date")
        ]
        risks["payment"] = (
            "高" if any(days is not None and days <= 0 for days in due_days) else "中"
        )
        return risks
```

At the start of `_group_weekly_focus_reasons`:

```python
    if stage == "已回款待结案":
        return []
    if stage == "已交单待回款":
        return ["high_risk"] if risk == "高" else []
```

In `_build_progress_group`, after `unpaid_rows`:

```python
    missing_due_count = (
        sum(1 for row in unpaid_rows if not _normalize_text(row.get("finance_due_date")))
        if stage == "已交单待回款"
        else 0
    )
```

Change the returned issue count to:

```python
        "data_issue_count": (
            len([warning for warning in warnings if _is_data_quality_warning(warning)])
            + missing_due_count
        ),
```

- [ ] **Step 4: Run focused risk regressions**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k \
  'documented or payment_risk or weekly_focus or indicator_risks or partial_and_complete' -v
```

Expected: PASS. Replace the obsolete assertion that a documented unpaid item more than 30 days away is low with medium.

- [ ] **Step 5: Commit backend risk behavior**

```bash
git add backend/app/order_finance.py tests/test_order_finance.py
git commit -m "fix: make documented order risk repayment-only"
```

---

### Task 2: Display missing due date and accept on Staging

**Files:**
- Modify: `tests/order_finance_frontend.test.mjs`
- Modify: `frontend/app.js:2471-2483`
- Modify: `frontend/index.html`
- Modify after deployment: `版本更新记录.md`

**Interfaces:**
- Consumes: `stage`, `payment_due_date`, `latest_due_date`, and `indicator_risks.payment`.
- Produces: `orderFinancePaymentDueText(item)` returning `融资到期日缺失` for documented unpaid orders with no due date.

- [ ] **Step 1: Write the failing frontend contract**

```javascript
test("documented unpaid orders show a missing financing due-date anomaly", () => {
  assert.match(
    appJs,
    /if \(item\.stage === "已交单待回款" && !dueDate\) return "融资到期日缺失"/,
  );
  assert.match(indexHtml, /app\.js\?v=order-finance-repayment-risk-20260716/);
});
```

- [ ] **Step 2: Confirm the frontend contract fails**

```bash
node --test tests/order_finance_frontend.test.mjs
```

Expected: FAIL because the current page says `未提供`.

- [ ] **Step 3: Implement the text and asset version**

```javascript
function orderFinancePaymentDueText(item) {
  const dueDate = item.payment_due_date || item.latest_due_date;
  if (item.stage === "已交单待回款" && !dueDate) return "融资到期日缺失";
  if (!dueDate) return "未提供";
```

Set the single current script reference in `frontend/index.html` to:

```html
<script src="/static/app.js?v=order-finance-repayment-risk-20260716"></script>
```

Update other tests that intentionally assert the current `app.js` version.

- [ ] **Step 4: Run the complete risk-task quality gate**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -v
node --test tests/order_finance_frontend.test.mjs
node --check frontend/app.js
git diff --check
```

Expected: all targeted tests PASS; syntax and diff checks exit 0.

- [ ] **Step 5: Commit and push only the risk task**

```bash
git add frontend/app.js frontend/index.html tests/order_finance_frontend.test.mjs
git commit -m "fix: show documented repayment risk clearly"
git push origin staging
```

- [ ] **Step 6: Verify the real Staging page**

Open `https://ltm-web-staging.onrender.com/?codex=<tested-commit>` in a clean in-app browser tab. Confirm a documented future-due item is medium, a due-today/overdue item is high and in `本周重点`, missing due says `融资到期日缺失` and increments data issues, paid-unclosed is low, only payment fields are colored, and the console has no application errors.

- [ ] **Step 7: Record Staging and present Gate B**

Append the tested commit, test totals, browser evidence, no-database-impact statement, and rollback point to `版本更新记录.md`; commit and push the record. Present Gate B for this risk-only release. Do not start the synchronization-protection task or release Production implicitly.
