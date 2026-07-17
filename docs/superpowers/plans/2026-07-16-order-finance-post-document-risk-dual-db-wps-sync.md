# Order Finance Post-Document Risk and Dual-Database WPS Sync Implementation Plan

> **SUPERSEDED:** Do not execute this combined plan. The user approved splitting it into two independently assessed plans:
> - `2026-07-16-order-finance-post-document-risk.md` — D2/T2/R1/C1, execute first.
> - `2026-07-16-order-finance-dual-db-wps-sync-guard.md` — D3/T3/R2/C1, execute separately after the first task.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make documented orders depend only on unpaid-financing due dates for risk, and make Staging and Production independently consume the same WPS facts with identical guarded synchronization.

**Architecture:** Keep all business behavior inside the existing `order_finance` module. Add a documented-stage early branch to the existing risk aggregator, persist only the minimum pending-shrink identity needed for two-pass confirmation, and validate the downloaded workbook before the existing transactional snapshot apply. Staging and Production continue to run the same scheduler against separate Supabase databases and independently authorized WPS refresh tokens.

**Tech Stack:** Python 3, FastAPI, SQLite/PostgreSQL compatibility layer, openpyxl, pytest, vanilla JavaScript, Node test runner, Render, Supabase Postgres, WPS read-only API.

## Global Constraints

- Work only on branch `staging` until Staging acceptance is complete.
- Staging writes only Supabase `LTM WEB STAGING`; Production writes only Supabase `LTM WEB`.
- Do not merge or push `main`, change Production environment variables, write Production data, or deploy Production before Gate B approval.
- WPS remains read-only and only `订单`, `额度`, and `预警` participate in ingestion.
- WPS facts may update; management fields and manual shipment confirmation remain environment-local and must survive synchronization.
- Use independently issued WPS refresh tokens for Staging and Production; never print, compare, log, or commit token values.
- Missing sheets, empty orders, invalid business keys, or an unconfirmed key-set shrink must not write or archive data.
- A first key-set shrink is deferred; the same source version and key-set hash on a later scheduled run confirms it.
- Use one main Agent by default. Do not add Agents unless the user explicitly changes the cost boundary.
- D/T/R/C target: D3 / T3 / R2 / C1 for development through Staging. Production remains a separate R3 Gate B action.

---

## File Map

- Modify `backend/app/order_finance.py`: documented-stage risk, missing-due data issue count, pending shrink status persistence, and snapshot identity helpers.
- Modify `backend/app/order_finance_wps_sync.py`: parsed workbook validation and two-pass shrink orchestration.
- Modify `backend/app/db.py`: idempotent PostgreSQL and SQLite columns for pending shrink identity.
- Modify `frontend/app.js`: explicit missing financing due-date text for documented unpaid orders.
- Modify `frontend/index.html`: bump order-finance static asset versions after the JavaScript change.
- Modify `tests/test_order_finance.py`: risk boundaries, focus behavior, data issue count, schema, and pending status unit coverage.
- Modify `tests/test_order_finance_wps_sync.py`: required-sheet, empty-workbook, invalid-key, first-shrink, confirmed-shrink, and changed-candidate coverage.
- Modify `tests/order_finance_frontend.test.mjs`: missing-due presentation and asset-version contract.
- Modify `版本更新记录.md`: append the Staging result only after deployment and real-surface validation.

---

### Task 1: Lock the documented-stage repayment risk contract

**Files:**
- Modify: `tests/test_order_finance.py`
- Modify: `backend/app/order_finance.py:1766-1820`
- Modify: `backend/app/order_finance.py:1822-1848`
- Modify: `backend/app/order_finance.py:1883-1960`

**Interfaces:**
- Consumes: `_group_stage(rows: List[Dict[str, Any]]) -> str`, `_row_is_paid(row) -> bool`, `_days_to(value) -> Optional[int]`.
- Produces: `_group_indicator_risks(rows, stage) -> Dict[str, str]` with payment-only documented-stage risk; `_group_weekly_focus_reasons(rows, stage, risk) -> List[str]`; `data_issue_count` including each unpaid documented row with no due date.

- [ ] **Step 1: Replace the old 7/30-day documented-order test with exact day-boundary tests**

Add these tests next to `test_payment_risk_uses_seven_and_thirty_day_boundaries`:

```python
def test_documented_unpaid_risk_uses_due_day_boundary_only():
    today = date.today()
    records = [
        progress_record(
            f"DOC-{days}",
            "存续",
            id=index,
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


def test_documented_missing_due_is_medium_and_counts_data_issue():
    item = build_order_finance_progress_view([
        progress_record(
            "DOC-MISSING-DUE",
            "存续",
            document_submission_date=date.today().isoformat(),
            finance_due_date="",
        ),
    ])["contracts"][0]

    assert item["stage"] == "已交单待回款"
    assert item["risk"] == "中"
    assert item["indicator_risks"]["payment"] == "中"
    assert item["data_issue_count"] == 1
    assert item["is_weekly_focus"] is False


def test_documented_multiple_financings_use_only_unpaid_rows():
    today = date.today()
    paid = progress_record(
        "DOC-MULTI",
        "存续",
        document_submission_date=today.isoformat(),
        finance_due_date=(today - timedelta(days=10)).isoformat(),
        tail_payment_date=(today - timedelta(days=11)).isoformat(),
    )
    future_unpaid = progress_record(
        "DOC-MULTI",
        "存续",
        id=2,
        business_key="ITEM|DOC-MULTI|2",
        document_submission_date=today.isoformat(),
        finance_due_date=(today + timedelta(days=60)).isoformat(),
    )

    item = build_order_finance_progress_view([paid, future_unpaid])["contracts"][0]

    assert item["payment_progress"] == "部分回款 1/2笔"
    assert item["risk"] == "中"
    assert item["indicator_risks"]["payment"] == "中"


def test_all_paid_unclosed_is_low_and_not_weekly_focus():
    item = build_order_finance_progress_view([
        progress_record(
            "DOC-PAID",
            "存续",
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

- [ ] **Step 2: Run the focused tests and confirm the old implementation fails**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_order_finance.py::test_documented_unpaid_risk_uses_due_day_boundary_only \
  tests/test_order_finance.py::test_documented_missing_due_is_medium_and_counts_data_issue \
  tests/test_order_finance.py::test_documented_multiple_financings_use_only_unpaid_rows \
  tests/test_order_finance.py::test_all_paid_unclosed_is_low_and_not_weekly_focus -v
```

Expected: FAIL because a future day beyond 30 is currently low, missing due dates are not data issues, and manual reminders still affect documented orders.

- [ ] **Step 3: Add the documented-stage early branch and missing-due issue count**

In `_group_indicator_risks`, place this branch after the `已完成` return and before warning processing:

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
            "高"
            if any(days is not None and days <= 0 for days in due_days)
            else "中"
        )
        return risks
```

At the start of `_group_weekly_focus_reasons`, before the general reasons logic, add:

```python
    if stage == "已回款待结案":
        return []
    if stage == "已交单待回款":
        return ["high_risk"] if risk == "高" else []
```

In `_build_progress_group`, calculate the missing-due count from `unpaid_rows` and use it in the returned count:

```python
    missing_due_count = (
        sum(1 for row in unpaid_rows if not _normalize_text(row.get("finance_due_date")))
        if stage == "已交单待回款"
        else 0
    )
```

```python
        "data_issue_count": (
            len([warning for warning in warnings if _is_data_quality_warning(warning)])
            + missing_due_count
        ),
```

- [ ] **Step 4: Run the focused and existing risk regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k \
  'documented or payment_risk or weekly_focus or indicator_risks or partial_and_complete' -v
```

Expected: PASS. Update the obsolete `PAY-31` assertion from low to medium instead of retaining contradictory coverage.

- [ ] **Step 5: Commit the backend risk contract**

```bash
git add backend/app/order_finance.py tests/test_order_finance.py
git commit -m "fix: make documented order risk repayment-only"
```

---

### Task 2: Show the missing due-date anomaly on the real card

**Files:**
- Modify: `tests/order_finance_frontend.test.mjs`
- Modify: `frontend/app.js:2471-2483`
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: API fields `stage`, `payment_due_date`, `latest_due_date`, and `indicator_risks.payment` from Task 1.
- Produces: `orderFinancePaymentDueText(item) -> string` returning `融资到期日缺失` for documented unpaid orders without a due date.

- [ ] **Step 1: Add the frontend contract test**

Add to `tests/order_finance_frontend.test.mjs`:

```javascript
test("documented unpaid orders show a missing financing due-date anomaly", () => {
  assert.match(
    appJs,
    /if \(item\.stage === "已交单待回款" && !dueDate\) return "融资到期日缺失"/,
  );
  assert.match(indexHtml, /app\.js\?v=order-finance-repayment-risk-20260716/);
});
```

- [ ] **Step 2: Run the frontend test and confirm it fails**

Run:

```bash
node --test tests/order_finance_frontend.test.mjs
```

Expected: FAIL because the current function returns `未提供` and the asset version is still the 2026-07-15 value.

- [ ] **Step 3: Implement the explicit anomaly text and bump the asset version**

Change the start of `orderFinancePaymentDueText` to:

```javascript
function orderFinancePaymentDueText(item) {
  const dueDate = item.payment_due_date || item.latest_due_date;
  if (item.stage === "已交单待回款" && !dueDate) return "融资到期日缺失";
  if (!dueDate) return "未提供";
```

In `frontend/index.html`, change the `app.js` query value to exactly:

```html
<script src="/static/app.js?v=order-finance-repayment-risk-20260716"></script>
```

Update existing asset-version assertions that intentionally track the single current `app.js` version.

- [ ] **Step 4: Run the frontend test and syntax check**

Run:

```bash
node --test tests/order_finance_frontend.test.mjs
node --check frontend/app.js
```

Expected: all order-finance frontend tests PASS and JavaScript syntax exits 0.

- [ ] **Step 5: Commit the visible anomaly text**

```bash
git add frontend/app.js frontend/index.html tests/order_finance_frontend.test.mjs
git commit -m "fix: show missing order finance due dates"
```

---

### Task 3: Persist two-pass shrink confirmation state

**Files:**
- Modify: `tests/test_order_finance.py:290-410`
- Modify: `backend/app/db.py:2090-2160`
- Modify: `backend/app/order_finance.py:1138-1295`

**Interfaces:**
- Produces: `snapshot_business_keys_hash(records: List[Dict[str, Any]]) -> str`; `get_active_synced_business_keys() -> set[str]`; `record_pending_order_finance_shrink(source_version: str, business_keys_hash: str, record_count: int, attempt_slot: str) -> None`.
- Extends: `get_order_finance_sync_status() -> Dict[str, Any]` with `pending_source_version`, `pending_business_keys_hash`, and `pending_record_count` for internal scheduler use.
- Consumes: existing `apply_order_finance_snapshot(...)`; a successful apply clears all pending shrink columns in the same transaction.

- [ ] **Step 1: Add schema and persistence tests**

Extend `test_order_finance_schema_adds_singleton_sync_status` to require:

```python
        "pending_source_version", "pending_business_keys_hash", "pending_record_count",
```

Add:

```python
def test_pending_shrink_state_is_recorded_without_changing_last_success(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [progress_record("A", "存续"), progress_record("B", "存续", id=2, business_key="ITEM|B|1")],
        sync_success_at="2026-07-16T09:02:00+08:00",
        source_version="v10",
        attempt_slot="2026-07-16T09:00+08:00",
    )

    order_finance.record_pending_order_finance_shrink(
        "v11", "hash-a", 1, "2026-07-16T17:00+08:00",
    )
    status = order_finance.get_order_finance_sync_status()

    assert status["last_success_at"] == "2026-07-16T09:02:00+08:00"
    assert status["source_version"] == "v10"
    assert status["pending_source_version"] == "v11"
    assert status["pending_business_keys_hash"] == "hash-a"
    assert status["pending_record_count"] == 1


def test_successful_snapshot_clears_pending_shrink_state(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.record_pending_order_finance_shrink(
        "v11", "hash-a", 1, "2026-07-16T09:00+08:00",
    )

    order_finance.apply_order_finance_snapshot(
        [progress_record("A", "存续")],
        sync_success_at="2026-07-16T17:02:00+08:00",
        source_version="v11",
        attempt_slot="2026-07-16T17:00+08:00",
    )

    status = order_finance.get_order_finance_sync_status()
    assert status["pending_source_version"] is None
    assert status["pending_business_keys_hash"] is None
    assert status["pending_record_count"] == 0
```

- [ ] **Step 2: Run the new persistence tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k 'pending_shrink or singleton_sync_status' -v
```

Expected: FAIL because the columns and persistence function do not exist.

- [ ] **Step 3: Add idempotent columns for PostgreSQL and SQLite**

Add these fields to the `CREATE TABLE` statement in `migrate_order_finance_schema`:

```sql
pending_source_version TEXT,
pending_business_keys_hash TEXT,
pending_record_count INTEGER NOT NULL DEFAULT 0,
```

Use the existing PostgreSQL `ADD COLUMN IF NOT EXISTS` pattern and SQLite `PRAGMA table_info` pattern for all three columns. Do not create a new history table.

- [ ] **Step 4: Add identity and pending-state helpers**

Import `hashlib` in `backend/app/order_finance.py` and add:

```python
def snapshot_business_keys_hash(records: List[Dict[str, Any]]) -> str:
    keys = sorted({_normalize_text(row.get("business_key")) for row in records})
    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


def get_active_synced_business_keys() -> set[str]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            """SELECT business_key FROM order_finance_progress
               WHERE is_archived = 0 AND source_file != '手动新增'""",
        ).fetchall()
    return {_normalize_text(dict(row).get("business_key")) for row in rows}


def record_pending_order_finance_shrink(
    source_version: str,
    business_keys_hash: str,
    record_count: int,
    attempt_slot: str,
) -> None:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """UPDATE order_finance_sync_status
               SET pending_source_version = ?, pending_business_keys_hash = ?,
                   pending_record_count = ?, last_attempt_slot = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = 1""",
            (source_version, business_keys_hash, record_count, attempt_slot),
        )
```

Extend `get_order_finance_sync_status` to select and return all three pending fields. In the successful `apply_order_finance_snapshot` status update, set the three pending fields to `NULL`, `NULL`, and `0` in the same transaction.

- [ ] **Step 5: Run persistence tests and the snapshot regression group**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k \
  'pending_shrink or sync_status or snapshot' -v
```

Expected: PASS, including transaction rollback and management-field preservation tests.

- [ ] **Step 6: Commit the pending-shrink persistence**

```bash
git add backend/app/db.py backend/app/order_finance.py tests/test_order_finance.py
git commit -m "feat: persist pending WPS shrink identity"
```

---

### Task 4: Guard WPS apply and confirm stable shrink on the second run

**Files:**
- Modify: `tests/test_order_finance_wps_sync.py`
- Modify: `backend/app/order_finance_wps_sync.py:20-32`
- Modify: `backend/app/order_finance_wps_sync.py:266-310`

**Interfaces:**
- Consumes from Task 3: `snapshot_business_keys_hash`, `get_active_synced_business_keys`, `record_pending_order_finance_shrink`, and pending fields from `get_order_finance_sync_status`.
- Produces: `_validate_parsed_workbook(parsed: dict) -> list[dict]`; `_shrink_needs_confirmation(records, source_version, status) -> bool`.
- `run_order_finance_wps_sync` returns `{"status": "deferred", "reason": "source_shrink_confirmation", "changed_count": 0}` on first or changed shrink candidates.

- [ ] **Step 1: Add parsed-workbook guard tests**

Add parameterized cases to `tests/test_order_finance_wps_sync.py`:

```python
@pytest.mark.parametrize("parsed", [
    {"records": [], "files": [{"sheets": {"订单": True, "额度": True, "预警": True}}]},
    {"records": [snapshot_record("A")], "files": [{"sheets": {"订单": True, "额度": False, "预警": True}}]},
    {"records": [dict(snapshot_record("A"), business_key="")], "files": [{"sheets": {"订单": True, "额度": True, "预警": True}}]},
])
def test_invalid_parsed_workbook_preserves_existing_data(tmp_path, monkeypatch, parsed):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("OLD")],
        sync_success_at="2026-07-15T17:02:00+08:00",
        source_version="v10",
        attempt_slot="2026-07-15T17:00+08:00",
    )
    monkeypatch.setattr(sync, "parse_order_finance_directory", lambda path: parsed)

    with pytest.raises(OrderFinanceWpsSyncError, match="workbook_validation"):
        run_order_finance_wps_sync(
            "2026-07-16T09:00+08:00",
            now=datetime.fromisoformat("2026-07-16T09:02:00+08:00"),
            client=SuccessfulDownloadClient(),
        )

    assert [row["business_key"] for row in order_finance.list_order_finance_records()] == [
        "ITEM|OLD|1",
    ]
    assert order_finance.get_order_finance_sync_status()["source_version"] == "v10"
```

- [ ] **Step 2: Add first-shrink, confirmed-shrink, and changed-candidate tests**

Add a version-selectable download client next to `SuccessfulDownloadClient`:

```python
class VersionedDownloadClient(SuccessfulDownloadClient):
    def __init__(self, source_version):
        super().__init__()
        self.source_version = source_version

    def download_workbook(self, target):
        result = super().download_workbook(target)
        return WpsDownloadResult(
            file_name=result.file_name,
            source_version=self.source_version,
        )
```

Use two existing rows `A` and `B`, then return only `A` from the parser:

```python
def test_source_shrink_requires_same_candidate_twice(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("A"), snapshot_record("B")],
        sync_success_at="2026-07-15T17:02:00+08:00",
        source_version="v10",
        attempt_slot="2026-07-15T17:00+08:00",
    )
    parsed = {
        "records": [snapshot_record("A")],
        "files": [{"sheets": {"订单": True, "额度": True, "预警": True}}],
    }
    monkeypatch.setattr(sync, "parse_order_finance_directory", lambda path: parsed)

    first = run_order_finance_wps_sync(
        "2026-07-16T09:00+08:00",
        now=datetime.fromisoformat("2026-07-16T09:02:00+08:00"),
        client=VersionedDownloadClient("v11"),
    )
    assert first == {
        "status": "deferred", "reason": "source_shrink_confirmation", "changed_count": 0,
    }
    assert {row["business_key"] for row in order_finance.list_order_finance_records()} == {
        "ITEM|A|1", "ITEM|B|1",
    }

    second = run_order_finance_wps_sync(
        "2026-07-16T17:00+08:00",
        now=datetime.fromisoformat("2026-07-16T17:02:00+08:00"),
        client=VersionedDownloadClient("v11"),
    )
    assert second["status"] == "success"
    assert second["archived"] == 1
    assert [row["business_key"] for row in order_finance.list_order_finance_records()] == [
        "ITEM|A|1",
    ]
```

Add a separate test where the second candidate uses `VersionedDownloadClient("v12")` or a different key hash; assert it is deferred again and both original rows remain active.

- [ ] **Step 3: Run the new WPS guard tests and confirm failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py -k \
  'invalid_parsed_workbook or source_shrink' -v
```

Expected: FAIL because the current sync immediately applies any parsed snapshot.

- [ ] **Step 4: Implement workbook validation and two-pass confirmation**

Import the Task 3 helpers and add:

```python
def _validate_parsed_workbook(parsed: dict) -> list[dict]:
    files = parsed.get("files") or []
    records = parsed.get("records") or []
    required = {"订单", "额度", "预警"}
    if not files or any(
        not required.issubset({name for name, present in (item.get("sheets") or {}).items() if present})
        for item in files
    ):
        raise OrderFinanceWpsSyncError("workbook_validation")
    if not records or any(not str(row.get("business_key") or "").strip() for row in records):
        raise OrderFinanceWpsSyncError("workbook_validation")
    return records


def _shrink_needs_confirmation(
    records: list[dict],
    source_version: str,
    status: dict,
) -> bool:
    incoming_keys = {str(row["business_key"]).strip() for row in records}
    if not (get_active_synced_business_keys() - incoming_keys):
        return False
    identity = snapshot_business_keys_hash(records)
    return not (
        status.get("pending_source_version") == source_version
        and status.get("pending_business_keys_hash") == identity
    )
```

In `run_order_finance_wps_sync`, validate after parsing. When shrink needs confirmation, persist the candidate with the current slot and return `deferred`; otherwise call the existing transactional apply. Ensure changed candidates replace the pending identity. The successful apply clears pending state through Task 3.

- [ ] **Step 5: Run the complete WPS sync suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py -v
```

Expected: all WPS sync tests PASS; failure and deferred cases preserve rows and last-success state.

- [ ] **Step 6: Commit guarded WPS synchronization**

```bash
git add backend/app/order_finance_wps_sync.py tests/test_order_finance_wps_sync.py
git commit -m "fix: confirm WPS record shrink before apply"
```

---

### Task 5: Complete local quality gate and Staging acceptance

**Files:**
- Modify after deployment: `版本更新记录.md`

**Interfaces:**
- Consumes: all code and tests from Tasks 1-4.
- Produces: Staging commit/deploy evidence, a read-only fact fingerprint comparison, and a Gate B package. No Production mutation is included.

- [ ] **Step 1: Run the module quality gate**

Run:

```bash
.venv/bin/python -m pytest tests/test_order_finance.py tests/test_order_finance_wps_sync.py -v
node --test tests/order_finance_frontend.test.mjs
node --check frontend/app.js
.venv/bin/python -m compileall -q backend/app
git diff --check
```

Expected: all targeted Python and Node tests PASS; syntax, compile, and diff checks exit 0.

- [ ] **Step 2: Run the repository regression gate**

Run:

```bash
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
```

Expected: all tests PASS. If an unrelated pre-existing failure appears, prove it exists at the pre-implementation commit before recording it as unrelated.

- [ ] **Step 3: Push the tested commits to `staging`**

```bash
git status --short --branch
git push origin staging
```

Expected: only intended order-finance files and the existing design/plan commits are pushed; unrelated working-tree files remain untracked or unstaged.

- [ ] **Step 4: Configure and verify Staging WPS independence**

In Render service `ltm-web-staging`, confirm the six order-finance variable names are configured, `ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED=true`, and the refresh token came from a Staging-specific authorization event rather than copying the Production token. Keep the same drive ID, file ID, 09:00/17:00 scheduler, and source workbook as Production. Do not reveal or compare secret values.

Expected: Staging restarts successfully, scheduler starts, and Production configuration remains unchanged.

- [ ] **Step 5: Perform real Staging browser acceptance**

Close previous project test tabs, then open:

```text
https://ltm-web-staging.onrender.com/?codex=<tested-commit>
```

Verify with authenticated read-only navigation:

1. URL and title identify Staging.
2. `app.js?v=order-finance-repayment-risk-20260716` is loaded.
3. An already documented future-due item is medium and only payment fields are yellow.
4. A documented due-today/overdue fixture is high and enters `本周重点`.
5. A documented missing-due fixture says `融资到期日缺失`, is medium, and increments data issues.
6. Existing shipment/document warnings do not color completed nodes after documentation.
7. Console has no application errors.

Use only isolated Staging fixtures and remove them after acceptance. Do not write Production data.

- [ ] **Step 6: Exercise shrink protection on Staging**

Using an isolated Staging-only test workbook or mocked scheduler invocation:

1. Start from two active WPS business keys.
2. First candidate contains one key with a new source version: expect `deferred`, zero writes, zero archives, and unchanged last success.
3. Repeat the same source version and key set in a later slot: expect one archive in a single transaction.
4. Restore the original Staging dataset and confirm the source version and active key count return to baseline.

Expected: no partial state and no Production impact.

- [ ] **Step 7: Compare Staging and Production fact fingerprints read-only**

Run this command separately in each Render service shell so each process uses its own existing `DATABASE_URL`:

```bash
PYTHONPATH=backend python - <<'PY'
import hashlib
import json
from app.order_finance import FACT_FIELDS, get_order_finance_sync_status, list_order_finance_records

rows = [
    {field: row.get(field) for field in FACT_FIELDS}
    for row in list_order_finance_records()
    if row.get("source_file") != "手动新增"
]
rows.sort(key=lambda row: row.get("business_key") or "")
payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
status = get_order_finance_sync_status()
print(json.dumps({
    "source_version": status.get("source_version"),
    "record_count": len(rows),
    "fact_sha256": hashlib.sha256(payload).hexdigest(),
}, ensure_ascii=False))
PY
```

Expected: after both environments successfully consume the same WPS source version, `source_version`, `record_count`, and `fact_sha256` match. Do not print rows or secrets. If management fields differ, this command intentionally ignores them.

- [ ] **Step 8: Record the completed Staging deployment**

Append a dated Staging entry to `版本更新记录.md` containing the tested commit, test totals, real-page evidence, Staging database impact, shrink-protection result, fingerprint result, known issues, and rollback commit. Do not include WPS identifiers, tokens, database URLs, or secret hashes.

```bash
git add 版本更新记录.md
git commit -m "docs: record order finance repayment sync staging"
git push origin staging
```

- [ ] **Step 9: Present Gate B and stop**

Present the accepted Staging commit, tests, browser evidence, Staging data cleanup, schema additions, independent-token status, fact fingerprint comparison, Production target, backup/migration plan, smoke test, and rollback point. Do not merge `main`, alter Production variables, migrate Production, or deploy Production without explicit approval.
