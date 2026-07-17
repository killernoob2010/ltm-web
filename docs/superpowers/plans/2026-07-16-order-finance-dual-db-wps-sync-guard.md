# Order Finance Dual-Database WPS Sync Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Staging and Production independently consume the same WPS facts while preventing an incomplete or newly reduced source snapshot from immediately archiving records.

**Architecture:** Persist a minimal pending shrink identity in each environment's existing singleton sync-status row. Validate all three authoritative sheets and non-empty business keys before apply; defer the first reduced key set and apply only when a later scheduled run has the same source version and key-set hash.

**Tech Stack:** Python 3, SQLite/PostgreSQL compatibility layer, openpyxl, pytest, Render, Supabase Postgres, WPS read-only API.

## Global Constraints

- D/T/R/C: D3 / T3 / R2 / C1 through Staging; Production migration/config/deploy is R3 and requires Gate B.
- Staging and Production remain separate databases and use independently issued refresh tokens.
- Both environments use the same WPS drive/file, three sheets, parser, 09:00/17:00 schedule, transaction, and fact-field rules.
- WPS may update facts but must preserve environment-local management fields.
- Missing sheets, empty orders, invalid keys, database failure, or an unconfirmed shrink must preserve current rows and last-success status.
- Manual upload remains outside the automatic two-pass shrink gate.
- Do not modify the risk rules delivered by the first task.

---

### Task 1: Persist the pending shrink identity

**Files:**
- Modify: `tests/test_order_finance.py:290-410`
- Modify: `backend/app/db.py:2090-2160`
- Modify: `backend/app/order_finance.py:1138-1295`

**Interfaces:**
- Produces: `snapshot_business_keys_hash(records) -> str`, `get_active_synced_business_keys() -> set[str]`, `record_pending_order_finance_shrink(source_version, business_keys_hash, record_count, attempt_slot) -> None`.
- Extends: `get_order_finance_sync_status()` with `pending_source_version`, `pending_business_keys_hash`, and `pending_record_count`.
- A successful automatic or manual snapshot clears pending identity in the same transaction.

- [ ] **Step 1: Write failing schema and state tests**

Require the three pending columns in `test_order_finance_schema_adds_singleton_sync_status`, then add:

```python
def test_pending_shrink_preserves_last_success(tmp_path, monkeypatch):
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


def test_successful_snapshot_clears_pending_shrink(tmp_path, monkeypatch):
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

- [ ] **Step 2: Confirm tests fail**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k 'pending_shrink or singleton_sync_status' -v
```

Expected: missing columns and helper failures.

- [ ] **Step 3: Add idempotent columns**

Add to the sync status `CREATE TABLE` and both migration paths:

```sql
pending_source_version TEXT,
pending_business_keys_hash TEXT,
pending_record_count INTEGER NOT NULL DEFAULT 0,
```

Use PostgreSQL `ADD COLUMN IF NOT EXISTS` and SQLite `PRAGMA table_info`; do not add a history table.

- [ ] **Step 4: Add exact state helpers**

Import `hashlib` and add:

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
                   updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
            (source_version, business_keys_hash, record_count, attempt_slot),
        )
```

Select and return the fields from `get_order_finance_sync_status`. Clear them in both success and manual snapshot status branches.

- [ ] **Step 5: Verify and commit state persistence**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k \
  'pending_shrink or sync_status or snapshot' -v
git add backend/app/db.py backend/app/order_finance.py tests/test_order_finance.py
git commit -m "feat: persist pending WPS shrink identity"
```

Expected: tests PASS and only the three intended files are committed.

---

### Task 2: Validate WPS and require two identical shrink candidates

**Files:**
- Modify: `tests/test_order_finance_wps_sync.py`
- Modify: `backend/app/order_finance_wps_sync.py:20-32`
- Modify: `backend/app/order_finance_wps_sync.py:266-310`

**Interfaces:**
- Consumes Task 1 helpers.
- Produces: `_validate_parsed_workbook(parsed) -> list[dict]`; first/changed shrink returns `{"status":"deferred","reason":"source_shrink_confirmation","changed_count":0}`.

- [ ] **Step 1: Write invalid workbook tests**

```python
@pytest.mark.parametrize("parsed", [
    {"records": [], "files": [{"sheets": {"订单": True, "额度": True, "预警": True}}]},
    {"records": [snapshot_record("A")], "files": [{"sheets": {"订单": True, "额度": False, "预警": True}}]},
    {"records": [dict(snapshot_record("A"), business_key="")], "files": [{"sheets": {"订单": True, "额度": True, "预警": True}}]},
])
def test_invalid_parsed_workbook_preserves_data(tmp_path, monkeypatch, parsed):
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
            client=VersionedDownloadClient("v11"),
        )
    assert [row["business_key"] for row in order_finance.list_order_finance_records()] == ["ITEM|OLD|1"]
```

- [ ] **Step 2: Write the two-pass shrink test**

Add:

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


def test_changed_shrink_candidate_defers_again(tmp_path, monkeypatch):
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

    run_order_finance_wps_sync(
        "2026-07-16T09:00+08:00",
        now=datetime.fromisoformat("2026-07-16T09:02:00+08:00"),
        client=VersionedDownloadClient("v11"),
    )
    changed = run_order_finance_wps_sync(
        "2026-07-16T17:00+08:00",
        now=datetime.fromisoformat("2026-07-16T17:02:00+08:00"),
        client=VersionedDownloadClient("v12"),
    )

    assert changed["status"] == "deferred"
    assert order_finance.get_order_finance_sync_status()["pending_source_version"] == "v12"
    assert {row["business_key"] for row in order_finance.list_order_finance_records()} == {
        "ITEM|A|1", "ITEM|B|1",
    }
```

- [ ] **Step 3: Confirm guard tests fail**

```bash
.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py -k \
  'invalid_parsed_workbook or source_shrink' -v
```

Expected: current sync immediately applies parsed records.

- [ ] **Step 4: Implement validation and confirmation**

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
```

After parsing, compute incoming keys and the key hash. If active synced keys are missing and the pending source version/hash do not both match, call `record_pending_order_finance_shrink` and return deferred. If both match, call the existing transaction; it clears pending state.

- [ ] **Step 5: Verify and commit WPS guarding**

```bash
.venv/bin/python -m pytest tests/test_order_finance_wps_sync.py -v
git add backend/app/order_finance_wps_sync.py tests/test_order_finance_wps_sync.py
git commit -m "fix: confirm WPS record shrink before apply"
```

---

### Task 3: Staging migration and dual-environment acceptance

**Files:**
- Modify after deployment: `版本更新记录.md`

**Interfaces:**
- Produces: Staging migration evidence, independent-token evidence, shrink acceptance, read-only fact fingerprint comparison, and Gate B package.

- [ ] **Step 1: Run complete local gates**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py tests/test_order_finance_wps_sync.py -v
.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
.venv/bin/python -m compileall -q backend/app
git diff --check
```

Expected: all tests PASS; any pre-existing unrelated failure must be reproduced at the pre-task commit.

- [ ] **Step 2: Push and verify Staging migration**

```bash
git push origin staging
```

Verify Render `ltm-web-staging` is live, the three nullable/default columns exist in `LTM WEB STAGING`, and no order records changed merely from migration.

- [ ] **Step 3: Verify independent Staging authorization**

Confirm the six order-finance environment variable names exist, auto sync is true, Staging uses a separately issued refresh token, and the drive/file identifiers target the same WPS workbook as Production. Do not reveal or compare secret values. Production configuration remains unchanged.

- [ ] **Step 4: Accept the shrink flow on Staging**

With isolated Staging fixtures, prove first reduced candidate defers with zero writes/archives, identical second candidate archives once, changed candidate defers again, and restoration returns the active-key count and source version to baseline.

- [ ] **Step 5: Compare facts read-only**

Run separately in each Render service shell:

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

Expected: after both consume the same source version, all three values match. Do not print rows or secrets.

- [ ] **Step 6: Record Staging and stop at Gate B**

Update `版本更新记录.md` after deployment with commits, tests, migration, shrink evidence, fingerprint result, cleanup, and rollback. Commit and push the record, then present Production backup/migration/smoke/rollback details. Do not modify Production without explicit Gate B approval.
