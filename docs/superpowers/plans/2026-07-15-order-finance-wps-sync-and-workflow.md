# Order Finance WPS Sync and Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the enterprise WPS workbook the twice-daily authoritative Excel fact source and align order-finance stages, risks, summaries, and cards with the shipped-documented-paid lifecycle.

**Architecture:** Keep workbook parsing and business views in `order_finance.py`, replace the archive-all importer with one transactional snapshot diff, and add a focused `order_finance_wps_sync.py` adapter for WPS OAuth download and 09:00/17:00 scheduling. Persist only a singleton sync status, expose its successful time/count through the existing progress API, and render the revised fields in the existing order-finance page.

**Tech Stack:** Python 3, FastAPI, SQLite/PostgreSQL compatibility helpers, `requests`, `openpyxl`, vanilla JavaScript, Python `pytest`, Node `node:test`.

## Global Constraints

- Work only on branch `staging`; do not merge or deploy `main` or alter Production configuration/data without Gate B.
- WPS calls are read-only: token refresh, file metadata, and source-file download only; no upload, share, modify, or delete capability.
- Never place WPS app credentials, refresh/access tokens, download URLs, or database secrets in source, tests, logs, API responses, Git history, or release records.
- Read only workbook sheets `订单`, `额度`, and `预警`; ignore every other sheet.
- Preserve manual notes, follow-up date, and shipment confirmation across every Excel/WPS import.
- Automatic sync runs daily, including weekends and holidays, at 09:00 and 17:00 `Asia/Shanghai`.
- The page exposes only the last successful automatic-sync time and that sync's actual changed-row count; failures remain in sanitized structured server logs.
- Do not add an import report, sync-history UI/table, anomaly page, manual document button, manual payment button, port-arrival/FCR state, WPS event subscription, paid cron service, or a third order-finance page.
- Existing unrelated dirty files must remain untouched.
- Selected specialist skills: `superpowers:test-driven-development` for every behavior change, `superpowers:executing-plans` for inline execution, `browser:control-in-app-browser` for staging acceptance, and `superpowers:verification-before-completion` before any completion claim.

---

## File Map

- Create `backend/app/order_finance_wps_sync.py`: WPS user-token client, source download, slot calculation/claiming, one-shot sync, and daemon scheduler.
- Create `tests/test_order_finance_wps_sync.py`: fake-HTTP and fake-clock tests for credentials, read-only requests, scheduling, deduplication, failure boundaries, and sanitized logs.
- Modify `backend/app/db.py`: create/migrate the singleton `order_finance_sync_status` table for SQLite and PostgreSQL.
- Modify `backend/app/order_finance.py`: transactional fact snapshot diff, sync-status accessors, lifecycle/risk derivation, revised progress payload, and unchanged manual management APIs.
- Modify `backend/app/main.py`: start the order-finance scheduler from the existing application startup hook.
- Modify `frontend/app.js`: new summaries, stage filters, card/detail rendering, terminology, sync-status rendering, and shipment-button visibility.
- Modify `frontend/index.html`: bump the `app.js` cache key after the frontend change; retain the existing page/dialog structure.
- Modify `tests/test_order_finance.py`: snapshot-diff, rollback, state regression, multi-financing, risk, and API payload tests.
- Modify `tests/order_finance_frontend.test.mjs`: static UI contract tests for new labels, fields, stage filters, detail behavior, and removed duplicate concepts.
- Modify `README.md`: document the five WPS environment-variable names and the automatic-sync behavior without values.
- Modify `版本更新记录.md`: only after staging deployment and real-surface acceptance, record the deployed commit, checks, DB impact, and rollback point.

---

### Task 1: Transactional Excel Snapshot Diff and Minimal Sync Status

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/order_finance.py`
- Test: `tests/test_order_finance.py`

**Interfaces:**
- Produces: `apply_order_finance_snapshot(records: list[dict], imported_by: str = "", sync_success_at: str | None = None, source_version: str | None = None, attempt_slot: str | None = None) -> dict[str, int]`.
- Produces: `get_order_finance_sync_status() -> dict[str, str | int | None]`.
- Produces: `claim_order_finance_sync_slot(slot_key: str) -> bool`.
- Preserves: `import_order_finance_directory(...)` and `import_order_finance_upload(...)` public signatures.

- [ ] **Step 1: Write failing schema and snapshot-diff tests**

```python
def test_order_finance_schema_adds_singleton_sync_status(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(order_finance_sync_status)")}
    assert {"id", "last_success_at", "changed_count", "source_version", "last_attempt_slot"} <= columns


def test_identical_snapshot_changes_zero_and_preserves_management(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    first = import_order_finance_directory(NEW_LEDGER_WORKBOOK, imported_by="pytest")
    row = list_order_finance_records()[0]
    update_management_fields(row["id"], {"manager_note": "保留", "next_follow_up_date": "2026-07-20"})
    second = import_order_finance_directory(NEW_LEDGER_WORKBOOK, imported_by="pytest")
    saved = get_order_finance_record(row["id"])
    assert first["summary"]["changed_count"] == first["summary"]["record_count"]
    assert second["summary"]["changed_count"] == 0
    assert saved["manager_note"] == "保留"
    assert saved["next_follow_up_date"] == "2026-07-20"


def test_snapshot_counts_fact_update_and_archive_without_archiving_manual_rows(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    records = [progress_record("A", "存续"), progress_record("B", "存续", id=2)]
    assert order_finance.apply_order_finance_snapshot(records)["changed_count"] == 2
    changed = [dict(records[0], finance_amount_actual=12_000_000)]
    result = order_finance.apply_order_finance_snapshot(changed)
    assert result == {"inserted": 0, "updated": 1, "archived": 1, "changed_count": 2}
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_order_finance.py -k 'singleton_sync_status or identical_snapshot or snapshot_counts' -q`

Expected: FAIL because the status table, `apply_order_finance_snapshot`, and `changed_count` do not exist.

- [ ] **Step 3: Add the cross-database singleton table and migration**

```sql
CREATE TABLE IF NOT EXISTS order_finance_sync_status (
    id INTEGER PRIMARY KEY,
    last_success_at TEXT,
    changed_count INTEGER NOT NULL DEFAULT 0,
    source_version TEXT,
    last_attempt_slot TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK (id = 1)
);
```

Use `INTEGER PRIMARY KEY` for SQLite and `INTEGER PRIMARY KEY` for PostgreSQL because this table never allocates IDs. Insert row `id = 1` with the repository's `db._exec` placeholder compatibility, and extend `migrate_order_finance_schema(conn)` instead of creating a separate migration framework.

- [ ] **Step 4: Implement a single-transaction fact diff**

```python
MANAGEMENT_FIELDS = {
    "planned_drawdown_date", "planned_finance_amount", "amount_adjustment_note",
    "repayment_requirement", "repayment_requirement_status", "next_action",
    "next_follow_up_date", "manager_note", "manual_override_fields",
    "shipment_confirmed_date", "shipment_confirmed_by", "shipment_confirmed_at",
    "management_plan_json", "manual_change_log_json",
}


def _fact_values_equal(existing: dict, incoming: dict) -> bool:
    return all(existing.get(field) == incoming.get(field) for field in FACT_FIELDS)


def apply_order_finance_snapshot(records, imported_by="", sync_success_at=None,
                                 source_version=None, attempt_slot=None):
    # Open one db.connect() context, load every non-manual Excel row by business_key,
    # insert absent keys, update only FACT_FIELDS that differ, reactivate archived keys,
    # archive active old Excel keys absent from the snapshot, and update the singleton
    # success fields only after all row writes succeed in the same transaction.
    # Return inserted/updated/archived and their sum as changed_count.
```

Before an update, compare `document_submission_date` and `tail_payment_date`; if a non-empty value becomes empty, emit one structured `logging` record containing only `event`, `business_key`, and `field`. Do not include row JSON or credentials.

- [ ] **Step 5: Route local and uploaded imports through the snapshot function**

Replace the two-step `archive_existing_excel_order_finance_records()` plus `upsert_order_finance_records()` calls in both import entry points. Keep the response fields `inserted`, `updated`, and `archived`, add `changed_count`, and leave the automatic-sync status untouched for manual imports.

- [ ] **Step 6: Verify GREEN and transaction rollback**

Add a monkeypatched `db._exec` failure after the first write and assert that no partial business rows or success status survive. Run:

`.venv/bin/python -m pytest tests/test_order_finance.py -k 'sync_status or snapshot or preserves_management or archives_previous or upload_import' -q`

Expected: PASS, including identical import `changed_count == 0` and rollback evidence.

- [ ] **Step 7: Commit the independently working importer**

```bash
git add backend/app/db.py backend/app/order_finance.py tests/test_order_finance.py
git commit -m "feat: make order finance imports transactional"
```

---

### Task 2: Lifecycle, Document Deadline, Payment Risk, and Multi-Financing Semantics

**Files:**
- Modify: `backend/app/order_finance.py`
- Modify: `tests/test_order_finance.py`

**Interfaces:**
- Produces group payload fields: `document_deadline`, `payment_progress`, `payment_due_date`, `shipment_basis`, and financing-row `payment_state`.
- Changes `indicator_risks` keys to `shipment`, `document`, `payment`, and `reminder`; valid values remain `低`, `中`, and `高`.
- Stages are exactly `待放款`, `已放款待装船`, `已装船待交单`, `已交单待回款`, `已回款待结案`, and `已完成`.

- [ ] **Step 1: Write failing lifecycle and risk tests**

```python
def test_document_date_implies_shipped_and_documented():
    row = progress_record("DOC", "存续", document_submission_date=date.today().isoformat())
    item = build_order_finance_progress_view([row])["contracts"][0]
    assert item["shipment_completed"] is True
    assert item["shipment_basis"] == "document"
    assert item["stage"] == "已交单待回款"
    assert item["indicator_risks"]["shipment"] == "低"
    assert item["indicator_risks"]["document"] == "低"


def test_manual_shipment_waits_for_document_and_deadline_is_medium():
    today = date.today()
    row = progress_record(
        "SHIP", "存续", shipment_confirmed_date=today.isoformat(),
        finance_due_date=(today + timedelta(days=15)).isoformat(),
    )
    item = build_order_finance_progress_view([row])["contracts"][0]
    assert item["stage"] == "已装船待交单"
    assert item["document_deadline"] == today.isoformat()
    assert item["indicator_risks"]["document"] == "中"
    assert "document_follow_up" in item["weekly_focus_reasons"]


def test_partial_and_complete_multi_financing_payment():
    first = progress_record("MULTI", "存续", tail_payment_date=date.today().isoformat())
    second = progress_record("MULTI", "存续", id=2, business_key="ITEM|MULTI|2",
                             document_submission_date=date.today().isoformat())
    partial = build_order_finance_progress_view([first, second])["contracts"][0]
    assert partial["stage"] == "已交单待回款"
    assert partial["payment_progress"] == "部分回款 1/2笔"
    second["tail_payment_date"] = date.today().isoformat()
    complete = build_order_finance_progress_view([first, second])["contracts"][0]
    assert complete["stage"] == "已回款待结案"
    assert complete["payment_progress"] == "已回款 2/2笔"
```

Add boundary assertions for payment due `<= 7` or overdue = high, `8..30` = medium, document deadline before today = medium, manual/documented shipment suppressing shipment warnings, explicit closure suppressing all ongoing risk, and deletion of WPS document/payment dates causing stage regression while manual shipment remains.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_order_finance.py -k 'document_date_implies or manual_shipment_waits or partial_and_complete or indicator_risks or weekly_focus or lifecycle' -q`

Expected: FAIL on old stage names, old indicator keys, whole-group payment semantics, and missing document deadline.

- [ ] **Step 3: Implement the minimum business helpers and group payload**

```python
def _row_is_paid(row):
    return bool(_normalize_text(row.get("tail_payment_date")))


def _group_document_date(rows):
    dates = sorted(row["document_submission_date"] for row in rows if row.get("document_submission_date"))
    return dates[-1] if dates else ""


def _row_document_deadline(row):
    due = _parse_date(row.get("finance_due_date"))
    return (due - timedelta(days=15)).isoformat() if due else ""
```

Derive the stage from the spec's reverse order. Use the earliest unpaid financing due date and earliest unpaid document deadline for the card. Do not treat bill of lading, collection, or payment date as a manual shipment confirmation. Keep explicit `结案` as the only completed override.

- [ ] **Step 4: Rebuild risk, weekly-focus, and next-action logic**

Implement shipment risk only when neither manual shipment nor document date exists; document medium risk when any unpaid financing reaches its 15-calendar-day deadline and the group has no document date; payment high/medium risk per unpaid financing; manual follow-up medium risk at `days <= 10`; overall risk as the highest active indicator. Update next actions to reference document submission and payment rather than the old confirmation/repayment wording.

- [ ] **Step 5: Verify all order-finance business tests GREEN**

Run: `.venv/bin/python -m pytest tests/test_order_finance.py -q`

Expected: all order-finance tests PASS with no legacy stage names in expected payloads.

- [ ] **Step 6: Commit the independently working lifecycle**

```bash
git add backend/app/order_finance.py tests/test_order_finance.py
git commit -m "feat: align order finance stages and risk rules"
```

---

### Task 3: Read-Only WPS User-Authorization Client

**Files:**
- Create: `backend/app/order_finance_wps_sync.py`
- Create: `tests/test_order_finance_wps_sync.py`
- Modify: `requirements.txt` only if the pinned `requests` dependency is absent

**Interfaces:**
- Produces: `WpsOrderFinanceConfig.from_env() -> WpsOrderFinanceConfig`.
- Produces: `WpsOrderFinanceClient.download_workbook(target: Path) -> WpsDownloadResult`.
- Environment names: `ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED`, `WPS_APP_ID`, `WPS_APP_SECRET`, `WPS_USER_REFRESH_TOKEN`, `ORDER_FINANCE_WPS_DRIVE_ID`, `ORDER_FINANCE_WPS_FILE_ID`.

- [ ] **Step 1: Write failing fake-HTTP client tests**

```python
def test_wps_client_refreshes_user_token_and_downloads_source_xlsx(tmp_path, monkeypatch):
    http = FakeHttp([
        FakeResponse(200, {"access_token": "short-lived", "expires_in": 7200}),
        FakeResponse(200, {"file": {"name": "台账.xlsx", "version": "v8"}}),
        FakeResponse(200, {"url": "https://download.invalid/source"}),
        FakeResponse(200, content=b"PK\x03\x04xlsx"),
    ])
    client = WpsOrderFinanceClient(config=fake_config(), http=http)
    result = client.download_workbook(tmp_path / "source.xlsx")
    assert result.source_version == "v8"
    assert result.file_name == "台账.xlsx"
    assert (tmp_path / "source.xlsx").read_bytes().startswith(b"PK\x03\x04")
    assert all(call.method in {"POST", "GET"} for call in http.calls)
    assert not any(call.method in {"PUT", "PATCH", "DELETE"} for call in http.calls)
```

Also assert missing configuration fails before HTTP, non-2xx responses raise a stage-labelled exception, cached access tokens are reused before expiry, and exception/log text never contains app secret, refresh token, access token, or download URL.

- [ ] **Step 2: Run the new test file and verify RED**

Run: `.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py -q`

Expected: collection FAIL because `order_finance_wps_sync.py` does not exist.

- [ ] **Step 3: Implement config, token cache, metadata, and download**

```python
@dataclass(frozen=True)
class WpsOrderFinanceConfig:
    app_id: str
    app_secret: str
    refresh_token: str
    drive_id: str
    file_id: str


@dataclass(frozen=True)
class WpsDownloadResult:
    file_name: str
    source_version: str
```

Use only official WPS user-token refresh, file-info, and file-download endpoints already cited in the approved design. Set connect/read timeouts, stream the source bytes to the supplied temporary path, validate `.xlsx`/ZIP signature before returning, cache only the access token in memory, and redact response bodies and signed download URLs from errors.

- [ ] **Step 4: Run client tests GREEN**

Run: `.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py -k 'client or config or redacts' -q`

Expected: PASS with exactly token refresh, metadata, download-info, and source-byte requests.

- [ ] **Step 5: Commit the isolated read-only adapter**

```bash
git add backend/app/order_finance_wps_sync.py tests/test_order_finance_wps_sync.py requirements.txt
git commit -m "feat: add read-only WPS order finance client"
```

---

### Task 4: Twice-Daily Scheduler and Atomic Successful Sync

**Files:**
- Modify: `backend/app/order_finance_wps_sync.py`
- Modify: `backend/app/main.py`
- Modify: `tests/test_order_finance_wps_sync.py`

**Interfaces:**
- Produces: `due_order_finance_sync_slots(now: datetime, last_attempt_slot: str | None) -> list[str]`.
- Produces: `run_order_finance_wps_sync(slot_key: str, now: datetime | None = None, client: WpsOrderFinanceClient | None = None) -> dict`.
- Produces: `start_order_finance_wps_sync_scheduler(interval_seconds: int = 300) -> bool`.
- Consumes: `claim_order_finance_sync_slot`, `parse_order_finance_directory`, and `apply_order_finance_snapshot`.

- [ ] **Step 1: Write failing clock, deduplication, success, and failure tests**

```python
@pytest.mark.parametrize(("clock", "expected"), [
    ("2026-07-15T08:59:00+08:00", []),
    ("2026-07-15T09:00:00+08:00", ["2026-07-15T09:00+08:00"]),
    ("2026-07-15T16:59:00+08:00", ["2026-07-15T09:00+08:00"]),
    ("2026-07-15T17:00:00+08:00", ["2026-07-15T09:00+08:00", "2026-07-15T17:00+08:00"]),
])
def test_due_slots_include_weekends_and_two_shanghai_times(clock, expected):
    assert due_order_finance_sync_slots(datetime.fromisoformat(clock), None) == expected


def test_failed_sync_preserves_rows_and_last_success(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot([progress_record("OLD", "存续")],
        sync_success_at="2026-07-14T17:00:00+08:00", source_version="v1",
        attempt_slot="2026-07-14T17:00+08:00")
    with pytest.raises(OrderFinanceWpsSyncError):
        run_order_finance_wps_sync("2026-07-15T09:00+08:00", client=FailingClient())
    assert [row["business_key"] for row in list_order_finance_records()] == ["ITEM|OLD|1"]
    assert get_order_finance_sync_status()["last_success_at"] == "2026-07-14T17:00:00+08:00"
```

Add assertions that a claimed slot cannot be claimed again, successful temp files are removed, parse failure removes temp files, successful sync writes rows and success status atomically, and a disabled/missing config prevents scheduler startup without crashing application startup.

- [ ] **Step 2: Run scheduler tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py -k 'slot or sync or scheduler' -q`

Expected: FAIL because scheduler functions are absent.

- [ ] **Step 3: Implement Shanghai slots and one-shot sync**

```python
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SYNC_TIMES = (time(9, 0), time(17, 0))
_scheduler_lock = threading.Lock()


def run_order_finance_wps_sync(slot_key, now=None, client=None):
    if not claim_order_finance_sync_slot(slot_key):
        return {"status": "skipped", "reason": "slot_already_attempted"}
    # Download to NamedTemporaryFile, parse all rows before DB mutation, hash source
    # when WPS metadata has no stable version, call apply_order_finance_snapshot with
    # success fields, and always unlink the temporary file in finally.
```

The daemon loop checks every five minutes, calls each due unattempted slot once, and logs only structured event/stage/error-class fields. The startup function returns `False` unless `ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED=true` and all five credentials/IDs are present.

- [ ] **Step 4: Start the scheduler from the existing FastAPI startup hook**

Import `start_order_finance_wps_sync_scheduler` in `backend/app/main.py` and call it beside `start_iron_ore_basis_sync_scheduler()` after `db.init_db()`. Keep the existing startup exception boundary.

- [ ] **Step 5: Run scheduler and startup tests GREEN**

Run: `.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py tests/test_order_finance.py -q`

Expected: PASS; failures leave business rows and last successful time/count unchanged.

- [ ] **Step 6: Commit the scheduler**

```bash
git add backend/app/order_finance_wps_sync.py backend/app/main.py tests/test_order_finance_wps_sync.py
git commit -m "feat: schedule WPS order finance sync twice daily"
```

---

### Task 5: Existing Progress API and Page Presentation

**Files:**
- Modify: `backend/app/order_finance.py`
- Modify: `frontend/app.js`
- Modify: `frontend/index.html`
- Modify: `tests/test_order_finance.py`
- Modify: `tests/order_finance_frontend.test.mjs`

**Interfaces:**
- Existing `GET /api/order-finance/progress` adds `sync_status: {last_success_at, changed_count}`.
- Manual import response remains separate and never changes `sync_status`.
- Existing shipment confirmation and reminder endpoints remain unchanged.

- [ ] **Step 1: Write failing backend payload and frontend contract tests**

```python
def test_progress_view_exposes_only_successful_sync_time_and_count(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot([], sync_success_at="2026-07-15T09:02:00+08:00",
                                               source_version="v2", attempt_slot="2026-07-15T09:00+08:00")
    assert build_order_finance_progress_view()["sync_status"] == {
        "last_success_at": "2026-07-15T09:02:00+08:00",
        "changed_count": 0,
    }
```

```javascript
test("order finance renders new stages, payment terms, and compact sync status", () => {
  assert.match(appJs, /上次同步/);
  assert.match(appJs, /更新.*条/);
  for (const label of ["已装船待交单", "已交单待回款", "已回款待结案", "回款到期日", "回款日"]) {
    assert.match(appJs, new RegExp(label));
  }
  for (const removed of ["缺最迟装船/交单/还款", "展期状态", "确认状态", "已还款待结案", "已装船待回款"]) {
    assert.doesNotMatch(appJs, new RegExp(removed));
  }
});
```

Also assert the manual shipment button is hidden when `document_date` exists, single-financing detail omits amount/borrow/effective-due/payment fields already present on the card, multi-financing detail includes per-financing amount/drawdown/original/effective due/payment/state, and read-only users get no mutation buttons.

- [ ] **Step 2: Run backend/frontend tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_order_finance.py -k 'progress_view_exposes' -q && node --test tests/order_finance_frontend.test.mjs`

Expected: FAIL due to absent sync payload and legacy labels/card fields.

- [ ] **Step 3: Add minimal sync status to the progress response**

```python
def build_order_finance_progress_view(records=None):
    # Existing summary/contracts construction remains.
    status = get_order_finance_sync_status() if records is None else {"last_success_at": None, "changed_count": 0}
    return {
        "summary": summary,
        "contracts": contracts,
        "sync_status": {
            "last_success_at": status.get("last_success_at"),
            "changed_count": int(status.get("changed_count") or 0),
        },
    }
```

Do not return source version, slot key, credentials, errors, or history.

- [ ] **Step 4: Revise summary, filters, card, and detail rendering**

Top metrics are: unsettled, active financing, payment due in 7/30 days, weekly focus, each of the five active stages, completed, and data anomalies. The card shows quantity, bank/finance total, shipment status/date, document status/date or earliest deadline, effective payment due date with extension folded into the text, payment date/progress, next action, and notes/reminders. Rename all user-facing repayment terms to `回款`.

For one financing, detail rows show only bank, rate, original due, new due, extension days, and source. For multiple financings, show bank, amount, drawdown date, original due, effective due, payment date, and state; do not repeat the group document date in each row.

- [ ] **Step 5: Render automatic sync status without changing manual upload feedback**

```javascript
function renderOrderFinanceSyncStatus(syncStatus) {
  orderFinanceStatus.textContent = syncStatus?.last_success_at
    ? `上次同步：${dateTimeToMinute(syncStatus.last_success_at)} · 更新 ${Number(syncStatus.changed_count || 0)} 条`
    : "尚无自动同步记录";
}
```

Call it after a successful progress load. Keep manual upload's immediate result visible for that action; the next normal progress reload restores the authoritative automatic-sync status.

- [ ] **Step 6: Bump the frontend cache key and run all focused tests GREEN**

Change only the `app.js` query value in `frontend/index.html` to `order-finance-wps-sync-20260715`.

Run: `.venv/bin/python -m pytest tests/test_order_finance.py tests/test_order_finance_wps_sync.py -q && node --test tests/order_finance_frontend.test.mjs`

Expected: PASS with no legacy stage/term strings in the order-finance renderer.

- [ ] **Step 7: Commit the page/API slice**

```bash
git add backend/app/order_finance.py frontend/app.js frontend/index.html tests/test_order_finance.py tests/order_finance_frontend.test.mjs
git commit -m "feat: update order finance workflow presentation"
```

---

### Task 6: Full Local Gate, Staging Configuration, Deployment, and Real-Surface Acceptance

**Files:**
- Modify: `README.md`
- Modify after successful staging acceptance: `版本更新记录.md`

**Interfaces:**
- No new production interface.
- Staging Render receives the five WPS secret/ID variables and `ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED=true`; values are entered only in the staging service's secret configuration.

- [ ] **Step 1: Document configuration names and operating behavior**

Add a README section listing only:

```text
ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED
WPS_APP_ID
WPS_APP_SECRET
WPS_USER_REFRESH_TOKEN
ORDER_FINANCE_WPS_DRIVE_ID
ORDER_FINANCE_WPS_FILE_ID
```

State that synchronization is read-only, occurs at 09:00 and 17:00 Shanghai time, and the page shows the last successful time/count. Do not include example secret values.

- [ ] **Step 2: Run the complete local quality gate**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py tests/test_order_finance_wps_sync.py -q
node --test tests/order_finance_frontend.test.mjs
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
git diff --check
```

Expected: all Python and Node tests PASS; `git diff --check` emits no output. If a full-suite failure is unrelated and pre-existing, preserve the evidence and do not silently weaken the focused acceptance.

- [ ] **Step 3: Perform a secret-free local real-workbook dry run**

Use the already downloaded valid enterprise workbook file only as an input fixture. Parse it and compare in a temporary local SQLite database with `DATABASE_URL` removed. Verify sheet names include `订单`, `额度`, and `预警`; a second identical snapshot reports zero changes; no source data or credentials are committed.

- [ ] **Step 4: Commit documentation and push staging**

```bash
git add README.md
git commit -m "docs: document order finance WPS sync"
git push origin staging
```

Pushing `staging` and triggering the existing Render staging deployment are authorized by Gate A. Do not merge or push `main`.

- [ ] **Step 5: Configure and verify Staging only**

In the existing Render `ltm-web-staging` service, set the six environment names above using the approved user authorization values without echoing them. Before any database write, confirm the service maps to Supabase `LTM WEB STAGING`. Verify one real WPS sync reads file metadata and the valid workbook, then verify a second unchanged sync reports zero changes. Confirm no Production service/config/data is touched.

- [ ] **Step 6: Run browser-visible staging acceptance**

Using a clean in-app browser tab, open `https://ltm-web-staging.onrender.com/?codex=<commit>`, sign in to the staging app, and verify:

- the page title/URL and `/static/app.js?v=order-finance-wps-sync-20260715` are current;
- the toolbar shows only last successful automatic-sync time and changed-row count;
- summary and filters use all new stage names and `回款` terminology;
- document date hides manual shipment confirmation and implies shipped;
- manual shipment produces `已装船待交单`;
- document/payment deadline examples show the expected medium/high risk;
- partial/all multi-financing payment shows the correct stage and X/Y text;
- one-financing detail does not duplicate card fields and multi-financing detail remains complete;
- a read-only user cannot see mutation buttons;
- browser console has no application errors.

- [ ] **Step 7: Record staging evidence after it succeeds**

Append one Staging entry to `版本更新记录.md` with the deployed commit, focused/full test counts, real WPS repeat-sync result, browser acceptance, database impact (`order_finance_sync_status` table plus snapshot updates in Staging), and code rollback point. Do not record any secret or URL carrying signed parameters.

- [ ] **Step 8: Commit and push the release record**

```bash
git add 版本更新记录.md
git commit -m "docs: record order finance staging verification"
git push origin staging
```

- [ ] **Step 9: Present Gate B and stop**

Report the tested staging commit, tests, WPS read/repeat-sync evidence, browser acceptance, DB impact, known issues, production target, smoke test, and rollback plan. Do not merge `main`, set Production variables, migrate Production data, or deploy Production until the user explicitly approves Gate B.
