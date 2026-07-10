# Operation Log Retention and Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce operation-log noise and query cost while retaining 12 months or at most 200,000 online rows and safely archiving complete months to private Supabase Storage.

**Architecture:** Keep hot logs in `operation_logs`, query them with `(created_at, id)` keyset pagination, and archive eligible complete months as immutable gzip NDJSON objects. Store archive metadata and referenced users in PostgreSQL/SQLite, proxy downloads only for administrators, and provide dry-run/archive/restore commands with checksum and transaction guards.

**Tech Stack:** FastAPI, SQLite/PostgreSQL through the existing `backend.app.db` adapter, vanilla JavaScript, Python `requests`, gzip NDJSON, Supabase private Storage REST/TUS APIs, pytest, Node test runner.

## Global Constraints

- Work only on branch `staging`; do not modify `main`, Production Render, Production Supabase, Production data, or Production environment variables.
- Preserve all pre-existing dirty and untracked files that are unrelated to this feature.
- Do not use subagents; execute every task inline in the current session.
- Use TDD for every behavior change: write a focused test, observe the expected failure, implement the minimum change, and rerun the focused tests.
- Never log or return plaintext passwords, password hashes, session tokens, database URLs, Storage service-role keys, or archive contents.
- Do not archive the current incomplete calendar month. Treat 200,000 rows as a soft online cap.
- Do not create a paid Render Cron service in this implementation.
- Keep the Storage bucket private and keep `SUPABASE_SERVICE_ROLE_KEY` server-side only.

---

### Task 1: Database schema, indexes, and archive-aware user deletion protection

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/main.py`
- Test: `tests/test_operation_logs.py`

**Interfaces:**
- Produces tables `operation_log_archives` and `operation_log_archive_users` for both PostgreSQL and SQLite.
- Produces indexes `idx_operation_logs_created_id`, `idx_operation_logs_user_created_id`, and `idx_operation_logs_type_created_id`.
- User deletion treats either online logs or archive-user rows as immutable history.

- [ ] **Step 1: Write failing schema and deletion-protection tests**

Create `tests/test_operation_logs.py` with a temporary SQLite setup matching `tests/test_auth_permissions.py`. Assert `PRAGMA index_list('operation_logs')` contains all three names, both archive tables exist after two `db.init_db()` calls, and inserting an archive-user relation causes `DELETE /api/users/{id}` to return the existing Chinese “有业务或日志历史” error.

```python
def test_init_db_creates_operation_log_archive_schema_and_indexes(client):
    db.init_db()
    db.init_db()
    with db.connect() as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list('operation_logs')")}
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"idx_operation_logs_created_id", "idx_operation_logs_user_created_id", "idx_operation_logs_type_created_id"} <= indexes
    assert {"operation_log_archives", "operation_log_archive_users"} <= tables
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q`

Expected: failure because archive tables and operation-log indexes do not exist.

- [ ] **Step 3: Add idempotent schema and explicit archive-history protection**

Add equivalent PostgreSQL and SQLite DDL in `db.init_db()`:

```sql
CREATE INDEX IF NOT EXISTS idx_operation_logs_created_id
ON operation_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_operation_logs_user_created_id
ON operation_logs(user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_operation_logs_type_created_id
ON operation_logs(operation_type, created_at DESC, id DESC);
```

Create `operation_log_archives` with period bounds, immutable object path, row count, first/last timestamps, checksum, compressed bytes, `created_at`, and nullable `restored_at`. Create `operation_log_archive_users` with composite primary key `(archive_id, user_id)` and foreign keys to archive metadata and users. Extend the existing user-delete history query to count `operation_log_archive_users` as history.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py tests/test_auth_permissions.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the database unit**

```bash
git add backend/app/db.py backend/app/main.py tests/test_operation_logs.py
git commit -m "feat: add operation log archive schema"
```

---

### Task 2: Cursor-paginated online log API

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/test_operation_logs.py`

**Interfaces:**
- Consumes existing `row_to_dict`, `db.connect`, `db._exec`, and administrator permission checks.
- Produces `encode_operation_log_cursor(created_at: str, log_id: int) -> str` and `decode_operation_log_cursor(cursor: str) -> tuple[str, int]`.
- Produces `GET /api/operation-logs` response `{logs, has_more, next_cursor}`.

- [ ] **Step 1: Write failing API tests**

Insert logs with repeated timestamps and assert:

```python
first = client.get("/api/operation-logs?limit=2", headers=admin_headers).json()
second = client.get(
    "/api/operation-logs",
    params={"limit": 2, "cursor": first["next_cursor"]},
    headers=admin_headers,
).json()
assert len(first["logs"]) == 2
assert first["has_more"] is True
assert set(row["id"] for row in first["logs"]).isdisjoint(row["id"] for row in second["logs"])
assert "pagination" not in first
```

Add tests for default 100/max 200, invalid cursor 400, operation type, exact user name, inclusive start date, inclusive end date, and a source inspection assertion that the route body contains neither `COUNT(*)` nor `OFFSET`.

- [ ] **Step 2: Run API tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q`

Expected: failures because the API still returns offset pagination and total count.

- [ ] **Step 3: Implement stable keyset pagination**

Encode JSON `[created_at, id]` with URL-safe base64 and strict validation. Query `limit + 1` rows ordered by `ol.created_at DESC, ol.id DESC`; for subsequent pages add:

```sql
AND (ol.created_at < ? OR (ol.created_at = ? AND ol.id < ?))
```

Resolve exact `user_name` to matching user IDs before the main query. Add `start_date` as `created_at >= YYYY-MM-DD 00:00:00` and `end_date` as `< next-day 00:00:00`. Return only the first `limit` rows and derive `has_more` and `next_cursor` from the extra row.

- [ ] **Step 4: Run API tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py tests/test_auth_permissions.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the API unit**

```bash
git add backend/app/main.py tests/test_operation_logs.py
git commit -m "feat: paginate operation logs by cursor"
```

---

### Task 3: Log-page filters and incremental loading

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Create: `tests/operation_logs_frontend.test.mjs`

**Interfaces:**
- Consumes `{logs, has_more, next_cursor}` from Task 2.
- Maintains `state.operationLogCursor`, `state.operationLogs`, and `state.operationLogsHasMore`.
- Produces first-page search, “加载更多”, date filters, and current-loaded-row CSV export.

- [ ] **Step 1: Write failing frontend source tests**

Create Node tests that extract `loadLogs` and assert the source contains `limit=100`, `cursor`, `start_date`, `end_date`, append behavior for load-more, a visible `logsLoadMoreBtn`, and the label `导出当前已加载记录`. Assert no frontend request contains `offset` for operation logs.

```javascript
test("operation logs use cursor pagination and current-row export", () => {
  assert.match(indexHtml, /id="logsStartDate"/);
  assert.match(indexHtml, /id="logsEndDate"/);
  assert.match(indexHtml, /id="logsLoadMoreBtn"/);
  assert.match(indexHtml, /导出当前已加载记录/);
  assert.match(appJs, /next_cursor/);
  assert.doesNotMatch(operationLogBlock, /offset/);
});
```

- [ ] **Step 2: Run frontend test and verify RED**

Run: `node --test tests/operation_logs_frontend.test.mjs`

Expected: failure because date inputs and load-more control do not exist.

- [ ] **Step 3: Implement the minimal dialog behavior**

Add start/end date inputs, clarify the export label, add a status line and a load-more button. Change `loadLogs({ append = false })` so a new search resets cursor and rows while load-more appends. Hide or disable load-more when `has_more` is false and show the count of currently loaded rows without claiming a database total.

- [ ] **Step 4: Run frontend tests and syntax check**

Run: `node --test tests/operation_logs_frontend.test.mjs tests/auth_frontend.test.mjs && node --check frontend/app.js`

Expected: all tests and syntax check pass.

- [ ] **Step 5: Commit the frontend unit**

```bash
git add frontend/index.html frontend/app.js frontend/styles.css tests/operation_logs_frontend.test.mjs
git commit -m "feat: load operation logs incrementally"
```

---

### Task 4: Suppress automatic calculation logs while retaining manual audit

**Files:**
- Modify: `backend/app/main.py`
- Modify: `frontend/app.js`
- Modify: `tests/test_operation_logs.py`
- Modify: `tests/info_summary_frontend.test.mjs`

**Interfaces:**
- `InfoCalculateAllIn` gains `audit_source: Literal["automatic", "manual"] = "automatic"`.
- Manual buttons call `calculateAllInfo(false, "manual")`; page entry and visibility restoration use `"automatic"`.

- [ ] **Step 1: Write failing backend and frontend tests**

Assert automatic payloads do not add a `批量计算指标` row, manual payloads add exactly one, and frontend automatic/manual call sites pass their respective sources.

```python
before = operation_log_count()
client.post("/api/info-summary/calculate-all", json={"items": items, "audit_source": "automatic"}, headers=headers)
assert operation_log_count() == before
client.post("/api/info-summary/calculate-all", json={"items": items, "audit_source": "manual"}, headers=headers)
assert operation_log_count() == before + 1
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q && node --test tests/info_summary_frontend.test.mjs`

Expected: backend failure because all batch calculations are logged, and frontend failure because source is not sent.

- [ ] **Step 3: Add trigger-source handling**

Validate the two allowed source values with Pydantic. Include `audit_source` in the JSON payload. Wrap `db.log_operation(...)` in `if payload.audit_source == "manual"`. Do not use this field for authorization or calculation behavior.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py tests/test_info_summary_rules.py -q && node --test tests/info_summary_frontend.test.mjs`

Expected: all tests pass.

- [ ] **Step 5: Commit the audit-noise unit**

```bash
git add backend/app/main.py frontend/app.js tests/test_operation_logs.py tests/info_summary_frontend.test.mjs
git commit -m "fix: avoid logging automatic calculations"
```

---

### Task 5: Archive selection, gzip generation, and safe Storage client

**Files:**
- Create: `backend/app/operation_log_archive.py`
- Modify: `.env.example`
- Modify: `tests/test_operation_logs.py`

**Interfaces:**
- Produces `select_archive_periods(conn, today: date, retention_months: int = 12, max_online_rows: int = 200000) -> list[ArchivePeriod]`.
- Produces `build_archive_payload(conn, period: ArchivePeriod) -> ArchivePayload` with `content`, `sha256`, `row_count`, timestamps, and user IDs.
- Produces `SupabaseArchiveStorage` methods `upload_immutable`, `download`, and `verify`.

- [ ] **Step 1: Write failing retention and payload tests**

Cover 12-month cutoff, 200,000-row soft cap, current-month exclusion, stable ID order, gzip decompression, checksum, user IDs, empty month, standard upload at or below 6 MB, TUS above 6 MB, immutable-path conflict, and redacted errors.

- [ ] **Step 2: Run archive-core tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q`

Expected: import failure because `backend.app.operation_log_archive` does not exist.

- [ ] **Step 3: Implement focused archive primitives**

Use dataclasses for `ArchivePeriod` and `ArchivePayload`. Serialize each row with `json.dumps(..., ensure_ascii=False, separators=(",", ":")) + "\n"`, compress with deterministic gzip `mtime=0`, and hash the compressed bytes. Implement Storage calls with existing pinned `requests`, server-side headers, bounded timeouts, no key in exception text, standard upload for `<= 6 * 1024 * 1024`, and TUS 6 MB chunks above that threshold using the direct Storage hostname.

- [ ] **Step 4: Run archive-core tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q`

Expected: all archive-core tests pass.

- [ ] **Step 5: Commit the archive core**

```bash
git add backend/app/operation_log_archive.py .env.example tests/test_operation_logs.py
git commit -m "feat: build private operation log archives"
```

---

### Task 6: Transactional archive and restore commands

**Files:**
- Modify: `backend/app/operation_log_archive.py`
- Create: `scripts/archive_operation_logs.py`
- Create: `scripts/restore_operation_logs.py`
- Modify: `tests/test_operation_logs.py`

**Interfaces:**
- Produces `archive_due_logs(storage, apply: bool, today: date) -> ArchiveRunResult`.
- Produces `restore_archive(archive_id: int, storage) -> RestoreResult`.
- CLI defaults to dry-run; archive mutation requires `--apply`; restore requires explicit archive ID and `--apply`.

- [ ] **Step 1: Write failing transaction and CLI tests**

Test dry-run zero writes, missing configuration, upload failure, checksum failure, deletion-count mismatch, successful metadata/user-map insertion and deletion, repeated-run idempotency, restore checksum failure, restore ID conflict, successful original-ID restore, and no secret/archive body in stdout or stderr.

- [ ] **Step 2: Run transaction tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q`

Expected: failures because orchestration and command entry points do not exist.

- [ ] **Step 3: Implement archive/restore orchestration**

For PostgreSQL, acquire a fixed advisory lock for the run. Upload and verify before opening the metadata/delete transaction. In the transaction insert metadata and distinct archive-user rows, delete exactly the archived half-open month range, compare affected count, and commit only on equality. Restore by downloading and verifying before a transaction, reject any existing original ID, insert all rows, and set `restored_at`; do not delete the Storage object.

- [ ] **Step 4: Run transaction and CLI tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit command-line operations**

```bash
git add backend/app/operation_log_archive.py scripts/archive_operation_logs.py scripts/restore_operation_logs.py tests/test_operation_logs.py
git commit -m "feat: archive and restore operation logs"
```

---

### Task 7: Administrator archive list and on-demand streaming download

**Files:**
- Modify: `backend/app/main.py`
- Modify: `frontend/index.html`
- Modify: `frontend/app.js`
- Modify: `frontend/styles.css`
- Modify: `tests/test_operation_logs.py`
- Modify: `tests/operation_logs_frontend.test.mjs`

**Interfaces:**
- Produces `GET /api/operation-log-archives` metadata-only response.
- Produces `GET /api/operation-log-archives/{archive_id}/download` as an administrator-only streaming gzip response.
- Frontend lists archive months without fetching object bodies and downloads only after an explicit click.

- [ ] **Step 1: Write failing authorization, streaming, and UI tests**

Assert guest/user/leader receive 403, admin list response excludes service keys and object bodies, list does not invoke `storage.download`, download does invoke it once and returns `application/gzip`, missing Storage config returns a Chinese 503, and the browser source only calls the download endpoint from an archive-row button handler.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py -q && node --test tests/operation_logs_frontend.test.mjs`

Expected: 404/source failures because archive routes and controls do not exist.

- [ ] **Step 3: Implement metadata list and explicit streaming download**

Use `_require_admin(user)` for both routes. Query only archive metadata for the list. For download, resolve the archive row, instantiate server-side Storage from environment, and return `StreamingResponse(storage.iter_download(path), media_type="application/gzip")` with an attachment filename. Add a compact “历史归档” area that shows “暂无历史归档” when empty and creates download buttons without prefetching files.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_operation_logs.py tests/test_auth_permissions.py -q && node --test tests/operation_logs_frontend.test.mjs tests/auth_frontend.test.mjs && node --check frontend/app.js`

Expected: all tests pass.

- [ ] **Step 5: Commit the archive access unit**

```bash
git add backend/app/main.py frontend/index.html frontend/app.js frontend/styles.css tests/test_operation_logs.py tests/operation_logs_frontend.test.mjs
git commit -m "feat: download archived operation logs on demand"
```

---

### Task 8: Full verification, Staging migration, deployment, and browser acceptance

**Files:**
- Modify: `README.md`
- Modify after verified deploy: `版本更新记录.md`

**Interfaces:**
- Documents archive dry-run/restore commands and required server-only environment variables.
- Produces a verified Staging release only; Production remains unchanged.

- [ ] **Step 1: Run all automated verification**

Run:

```bash
.venv/bin/python -m pytest -q
node --test tests/*.mjs
.venv/bin/python -m compileall -q backend scripts
node --check frontend/app.js
git diff --check
```

Expected: all feature tests pass; if the one documented pre-existing realtime-quote mock-count test still fails, record its exact name and prove the feature-focused suites pass without changing unrelated behavior.

- [ ] **Step 2: Document operator commands and configuration**

Add README instructions for dry-run, `--apply`, restore, private bucket, and the server-only variables. Do not include any real URL, key, password, or connection string.

- [ ] **Step 3: Back up and migrate Staging safely**

Use the existing Staging backup process for `operation_logs`, `users`, and the new archive metadata tables before triggering initialization. Verify the three indexes and both tables in Staging after deployment. Do not manufacture 200,000 rows and do not delete current Staging logs.

- [ ] **Step 4: Commit, push Staging, and verify the deployed UI**

Commit documentation and static-resource version changes, push `staging`, then open a clean in-app browser tab at `https://ltm-web-staging.onrender.com/?codex=<commit>`. Verify administrator login, first 100 log rows, combined filters, load-more, current-loaded export wording, empty archive list, automatic calculation no-log behavior, manual refresh one-log behavior, current static version, and no application console errors.

- [ ] **Step 5: Update the release record after verification**

Record Staging commit/deploy, database objects, backup, automated test counts, browser-visible results, archive dry-run result, rollback point, and the fact that Production and paid Render Cron remain untouched. Commit and push the release record, then recheck the deployed static commit.

