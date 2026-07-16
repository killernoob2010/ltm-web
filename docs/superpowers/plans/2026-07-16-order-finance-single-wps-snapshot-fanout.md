# Order Finance Single-WPS Snapshot Fanout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse the approved WPS application and one rotating user token while delivering the same versioned order-finance facts to isolated Production and Staging databases twice daily.

**Architecture:** Production remains the only WPS reader and exposes an authenticated, read-only snapshot containing only `FACT_FIELDS`. Staging runs a follower scheduler that validates and applies that snapshot to its own database; neither service receives the other environment's database connection.

**Tech Stack:** Python 3, FastAPI, requests, SQLite/PostgreSQL compatibility layer, pytest, Render, Supabase Postgres.

## Global Constraints

- D/T/R/C is D3 / T3 / R2 / C1 through Staging; any Production action is R3 and remains behind Gate B.
- Do not create a second WPS application, request another administrator approval, or configure one rotating refresh token in two services.
- Production uses `wps_source`; Staging uses `snapshot_follower`.
- Both environments retain separate `DATABASE_URL` values and environment-local management fields.
- The snapshot contains only active non-manual records and the existing `FACT_FIELDS`.
- The internal endpoint requires a server-side Bearer secret and returns 404 for missing or invalid credentials.
- Follow TDD: every behavior change starts with a failing test observed before implementation.
- Preserve unrelated dirty-worktree files and do not modify `main`, Production Render, Production Supabase, Production secrets, or Production data.

---

### Task 1: Deterministic fact snapshot export

**Files:**
- Modify: `tests/test_order_finance.py`
- Modify: `backend/app/order_finance.py`

**Interfaces:**
- Produces: `list_order_finance_fact_snapshot_records() -> list[dict]`.
- Produces: `order_finance_facts_hash(records: list[dict]) -> str`.
- Guarantees sorted, active, non-manual records with keys exactly equal to `FACT_FIELDS`.

- [ ] **Step 1: Write failing export and hash tests**

```python
def test_fact_snapshot_excludes_manual_archived_and_management_fields(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot([progress_record("A", "存续")])
    created = order_finance.create_manual_order_finance_record(
        {"business_key": "MANUAL|1", "subsidiary": "北满"}, created_by="pytest"
    )
    order_finance.update_management_fields(1, {"manager_note": "staging-only"}, "pytest")

    records = order_finance.list_order_finance_fact_snapshot_records()

    assert [row["business_key"] for row in records] == ["ITEM|A|1"]
    assert set(records[0]) == set(order_finance.FACT_FIELDS)
    assert "manager_note" not in records[0]
    assert created["business_key"] not in {row["business_key"] for row in records}


def test_fact_hash_is_stable_for_record_order():
    first = [progress_record("B", "存续"), progress_record("A", "存续")]
    second = list(reversed(first))
    assert order_finance.order_finance_facts_hash(first) == order_finance.order_finance_facts_hash(second)
```

- [ ] **Step 2: Run the tests and observe the expected missing-helper failures**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k 'fact_snapshot or fact_hash' -v
```

Expected: FAIL because the two helpers do not exist.

- [ ] **Step 3: Implement the minimal deterministic export**

Add a direct query restricted to `is_archived = 0 AND source_file != '手动新增'`, select only `FACT_FIELDS`, order by `business_key`, and normalize every returned row through `_row_to_dict`.

Implement the hash with stable JSON:

```python
def order_finance_facts_hash(records: List[Dict[str, Any]]) -> str:
    normalized = [
        {field: row.get(field) for field in FACT_FIELDS}
        for row in sorted(records, key=lambda item: str(item.get("business_key") or ""))
    ]
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run focused and module tests**

```bash
.venv/bin/python -m pytest tests/test_order_finance.py -k 'fact_snapshot or fact_hash or snapshot' -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/order_finance.py tests/test_order_finance.py
git commit -m "feat: export deterministic order finance facts"
```

---

### Task 2: Authenticated snapshot endpoint

**Files:**
- Create: `backend/app/order_finance_snapshot_sync.py`
- Create: `tests/test_order_finance_snapshot_sync.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `router = APIRouter()` with `GET /internal/order-finance/snapshot`.
- Produces: `build_order_finance_snapshot() -> dict`.
- Consumes Task 1 fact export and hash helpers.

- [ ] **Step 1: Write failing snapshot contract and authentication tests**

```python
def test_snapshot_requires_matching_server_secret(monkeypatch):
    monkeypatch.setenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET", "expected-secret-value")
    with pytest.raises(HTTPException) as missing:
        snapshot_sync.get_order_finance_snapshot(None)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as wrong:
        snapshot_sync.get_order_finance_snapshot("Bearer wrong")
    assert wrong.value.status_code == 404


def test_snapshot_returns_only_versioned_fact_fields(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("A")],
        sync_success_at="2026-07-16T09:02:00+08:00",
        source_version="v20",
        attempt_slot="2026-07-16T09:00+08:00",
    )
    monkeypatch.setenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET", "expected-secret-value")

    payload = snapshot_sync.get_order_finance_snapshot("Bearer expected-secret-value")

    assert payload["schema_version"] == 1
    assert payload["source_version"] == "v20"
    assert payload["source_success_at"] == "2026-07-16T09:02:00+08:00"
    assert payload["record_count"] == 1
    assert set(payload["records"][0]) == set(order_finance.FACT_FIELDS)
    assert payload["facts_hash"] == order_finance.order_finance_facts_hash(payload["records"])
```

- [ ] **Step 2: Run tests and verify expected import/missing-route failure**

```bash
.venv/bin/python -m pytest tests/test_order_finance_snapshot_sync.py -k snapshot -v
```

Expected: FAIL because `order_finance_snapshot_sync` does not exist.

- [ ] **Step 3: Implement the endpoint and constant-time secret check**

Create a focused module with:

```python
router = APIRouter()

def _require_snapshot_secret(authorization: Optional[str]) -> None:
    expected = (os.getenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET") or "").strip()
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=404, detail="Not Found")

@router.get("/internal/order-finance/snapshot")
def get_order_finance_snapshot(authorization: Optional[str] = Header(default=None)):
    _require_snapshot_secret(authorization)
    return build_order_finance_snapshot()
```

`build_order_finance_snapshot` must reject an empty source version or empty fact set with a server-side 503; it returns schema, source version, source success time, hash, count, and records only.

Include the router in `main.py` under `/api`.

- [ ] **Step 4: Verify endpoint tests and API route registration**

```bash
.venv/bin/python -m pytest tests/test_order_finance_snapshot_sync.py -k snapshot -v
.venv/bin/python -c 'from backend.app.main import app; assert any(r.path == "/api/internal/order-finance/snapshot" for r in app.routes)'
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/order_finance_snapshot_sync.py backend/app/main.py tests/test_order_finance_snapshot_sync.py
git commit -m "feat: expose protected order finance snapshot"
```

---

### Task 3: Snapshot follower validation and transactional apply

**Files:**
- Modify: `backend/app/order_finance_snapshot_sync.py`
- Modify: `tests/test_order_finance_snapshot_sync.py`

**Interfaces:**
- Produces: `SnapshotFollowerConfig.from_env()`.
- Produces: `OrderFinanceSnapshotClient.fetch_snapshot() -> dict`.
- Produces: `run_order_finance_snapshot_follow(slot_key, now=None, client=None) -> dict`.
- Reuses `apply_order_finance_snapshot`, shrink identity helpers, and `FACT_FIELDS`.

- [ ] **Step 1: Write failing validation and preservation tests**

Add parameterized tests for empty records, duplicate/empty business keys, wrong count, wrong hash, unsupported schema, and `source_success_at` older than the requested slot. Each test seeds one existing row and asserts both rows and last-success status remain unchanged.

```python
@pytest.mark.parametrize("mutate", [
    lambda p: {**p, "records": [], "record_count": 0},
    lambda p: {**p, "record_count": p["record_count"] + 1},
    lambda p: {**p, "facts_hash": "wrong"},
    lambda p: {**p, "schema_version": 2},
])
def test_invalid_follow_snapshot_preserves_staging_data(tmp_path, monkeypatch, mutate):
    use_temp_db(tmp_path, monkeypatch)
    seed_existing_snapshot("OLD", source_version="v10")
    payload = mutate(valid_snapshot_payload("NEW", source_version="v11"))
    client = StaticSnapshotClient(payload)
    with pytest.raises(snapshot_sync.OrderFinanceSnapshotSyncError):
        snapshot_sync.run_order_finance_snapshot_follow(
            "2026-07-16T09:00+08:00", client=client
        )
    assert active_business_keys() == {"ITEM|OLD|1"}
    assert order_finance.get_order_finance_sync_status()["source_version"] == "v10"
```

Add a success test that sets a local `manager_note`, follows a changed fact snapshot, and verifies the fact changed while `manager_note` remains.

- [ ] **Step 2: Run validation tests and observe missing follower failures**

```bash
.venv/bin/python -m pytest tests/test_order_finance_snapshot_sync.py -k 'invalid_follow or follower_applies' -v
```

Expected: FAIL because follower interfaces do not exist.

- [ ] **Step 3: Implement the minimum client and validator**

The client performs only HTTPS GET with:

```python
headers={"Authorization": f"Bearer {config.shared_secret}"}
```

The validator requires `schema_version == 1`, a non-empty source version, count equality, records with exactly the allowed fact keys, unique non-empty business keys, and a matching deterministic hash. It raises `OrderFinanceSnapshotSyncError(stage, status_code)` without response bodies or secrets.

- [ ] **Step 4: Implement slot freshness, shrink guard, and apply**

`run_order_finance_snapshot_follow` must:

1. Fetch and validate before claiming the local slot.
2. Return `{"status":"deferred","reason":"source_not_ready"}` when the upstream success time is earlier than the requested slot.
3. Return zero changes for the current `source_version + facts_hash`.
4. Reuse the existing two-pass shrink candidate guard.
5. Call `apply_order_finance_snapshot(records, imported_by="WPS快照跟随", sync_success_at=source_success_at, source_version=source_version, attempt_slot=slot_key)` only after all checks pass.
6. Claim/complete the slot only on unchanged or successful application, so deferred and transient failures retry.

- [ ] **Step 5: Verify follower and regression tests**

```bash
.venv/bin/python -m pytest tests/test_order_finance_snapshot_sync.py tests/test_order_finance_wps_sync.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/app/order_finance_snapshot_sync.py tests/test_order_finance_snapshot_sync.py
git commit -m "feat: follow validated order finance snapshots"
```

---

### Task 4: Environment-mode scheduler routing

**Files:**
- Modify: `backend/app/order_finance_snapshot_sync.py`
- Modify: `backend/app/order_finance_wps_sync.py`
- Modify: `backend/app/main.py`
- Modify: `tests/test_order_finance_snapshot_sync.py`
- Modify: `tests/test_order_finance_wps_sync.py`

**Interfaces:**
- Produces: `start_order_finance_sync_scheduler(interval_seconds=300) -> bool`.
- `wps_source` delegates to the existing WPS scheduler.
- `snapshot_follower` starts a follower loop and never constructs `WpsOrderFinanceClient`.

- [ ] **Step 1: Write failing mode-routing tests**

```python
def test_wps_source_mode_starts_only_wps(monkeypatch):
    monkeypatch.setenv("ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED", "true")
    monkeypatch.setenv("ORDER_FINANCE_SYNC_MODE", "wps_source")
    started = []
    monkeypatch.setattr(snapshot_sync, "start_order_finance_wps_sync_scheduler", lambda interval_seconds=300: started.append("wps") or True)
    assert snapshot_sync.start_order_finance_sync_scheduler() is True
    assert started == ["wps"]


def test_snapshot_follower_mode_starts_only_follower(monkeypatch):
    monkeypatch.setenv("ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED", "true")
    monkeypatch.setenv("ORDER_FINANCE_SYNC_MODE", "snapshot_follower")
    configure_follower_env(monkeypatch)
    started = []
    monkeypatch.setattr(snapshot_sync, "_start_snapshot_follower_scheduler", lambda interval_seconds=300: started.append("follower") or True)
    assert snapshot_sync.start_order_finance_sync_scheduler() is True
    assert started == ["follower"]


def test_invalid_or_incomplete_mode_does_not_start(monkeypatch):
    monkeypatch.setenv("ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED", "true")
    monkeypatch.setenv("ORDER_FINANCE_SYNC_MODE", "snapshot_follower")
    monkeypatch.delenv("ORDER_FINANCE_SNAPSHOT_UPSTREAM_URL", raising=False)
    monkeypatch.delenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET", raising=False)
    assert snapshot_sync.start_order_finance_sync_scheduler() is False
```

The follower-loop test uses a fake client and clock to prove 09:00 and 17:00 use the same slot strings as the WPS source and that a deferred slot remains retryable.

- [ ] **Step 2: Run tests and observe routing failures**

```bash
.venv/bin/python -m pytest tests/test_order_finance_snapshot_sync.py -k 'mode or follower_loop' -v
```

Expected: FAIL because the unified starter does not exist.

- [ ] **Step 3: Implement unified routing and startup**

Replace the `main.py` startup import/call with `start_order_finance_sync_scheduler()`. Keep the existing `ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED=true` master switch for backward compatibility; require the new mode when enabled.

The follower loop polls every 5 minutes. It logs only stage, status code, source version/count when available, and exception class.

- [ ] **Step 4: Run scheduler, module, and startup tests**

```bash
.venv/bin/python -m pytest tests/test_order_finance_snapshot_sync.py tests/test_order_finance_wps_sync.py tests/test_order_finance.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add backend/app/order_finance_snapshot_sync.py backend/app/order_finance_wps_sync.py backend/app/main.py tests/test_order_finance_snapshot_sync.py tests/test_order_finance_wps_sync.py
git commit -m "feat: route order finance sync by environment mode"
```

---

### Task 5: Documentation, full local gate, and Staging delivery

**Files:**
- Modify: `README.md`
- Modify after successful Staging deploy: `版本更新记录.md`

**Interfaces:**
- Documents the exact environment-variable names and role mapping without values.
- Records the actual Staging commit, tests, browser acceptance, limitations, and rollback.

- [ ] **Step 1: Update configuration documentation**

Document:

```text
Production: ORDER_FINANCE_SYNC_MODE=wps_source
Staging: ORDER_FINANCE_SYNC_MODE=snapshot_follower
Both: ORDER_FINANCE_SNAPSHOT_SHARED_SECRET=<Render secret>
Staging only: ORDER_FINANCE_SNAPSHOT_UPSTREAM_URL=<Production base URL>
```

State explicitly that Staging ignores WPS credentials in follower mode and that Production never receives the Staging database URL.

- [ ] **Step 2: Run the full local machine gate**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q backend
node --check frontend/app.js
git diff --check
```

Expected: all repository tests pass, compilation/syntax checks pass, and no whitespace errors.

- [ ] **Step 3: Commit and push the Staging candidate**

```bash
git add README.md
git commit -m "docs: document single WPS snapshot sync"
git push origin staging
```

- [ ] **Step 4: Configure only Staging for self-loop acceptance**

In Render `ltm-web-staging`, set a new random Staging-only shared secret, set mode to `snapshot_follower`, and point upstream to the Staging service's own protected endpoint. Do not read or modify Production configuration. Confirm the deployed commit is live.

- [ ] **Step 5: Run real Staging acceptance**

Use the in-app browser at:

```text
https://ltm-web-staging.onrender.com/?codex=<commit>
```

Verify URL/title, console, static resources, order-finance progress, capital monitoring, current 70 active records, unchanged management fields, and current sync status. Use a server-side test request to verify correct-secret success and wrong-secret rejection without exposing the secret or snapshot body.

- [ ] **Step 6: Record Staging result and push the record**

Only after deployment and acceptance, add the actual results and rollback commit to `版本更新记录.md`, commit, and push `staging`.

- [ ] **Step 7: Present Gate B and stop**

Gate B must include the tested Staging commit, real acceptance evidence, no-schema-impact statement, Production environment-variable changes, Production smoke test, rollback plan, and the remaining post-release step that changes Staging upstream from self-loop to the protected Production snapshot endpoint. Do not merge or deploy Production without explicit confirmation.
