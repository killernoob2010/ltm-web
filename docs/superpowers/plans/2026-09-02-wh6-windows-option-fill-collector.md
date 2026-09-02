# WH6 Windows Option Fill Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first Staging-ready vertical slice that reads only reliably identifiable WH6 option fills, keeps a durable local outbox, uploads through a restricted device endpoint, and produces one deduplicated server-side intraday fill per real execution.

**Architecture:** A platform-neutral Python collector core owns path discovery, versioned binary decoding, account binding, trading-day normalization, and a local SQLite outbox. FastAPI exposes the existing authenticated admin/device endpoints and an internal ingest service backed by isolated `trading_collector_*` tables; the same service records every device observation before applying a database uniqueness constraint to canonical fills. The Windows delivery surface is a PyInstaller-buildable launcher and installer manifest for the later Windows 11 build; this Mac worktree validates the parser, queue, API, and SQLite/Postgres-compatible schema without claiming that a Windows `Setup.exe` has already been built.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite/PostgreSQL through the repository `db` adapter, pytest, vanilla JavaScript/CSS, PyInstaller on Windows for packaging.

**Spec:** `docs/superpowers/specs/2026-09-02-wh6-windows-option-fill-collector-design.md`

## Global Constraints

- Scope is only completed option fills; do not parse orders, positions, funds, P&L, quotes, or submit any trading action.
- Iron-ore options are the first real acceptance sample; parser must reject unsupported/ambiguous records instead of guessing.
- First run backfills all reliably identifiable historical fills in the bound cache, then polls for new records; complete records target the 10-second Staging path.
- The bound identity is the confirmed Macro Futures account, not a path. Account change, missing identity, unknown format, unreadable path, or conflicting source pauses new reads/uploads.
- Device tokens and pairing codes are short-lived/revocable and never expose a service-role key, database password, full account number, or full local path.
- Every server write records an observation and then performs transactional, unique-keyed upsert; duplicate observations from multiple devices remain auditable without duplicate canonical fills.
- Existing settlement-confirmed `trading_trade_facts` are not overwritten. Intraday fills are provisional and isolated until a later reconciliation feature.
- All changes and verification stay in Staging/local SQLite. No Production deployment, production credentials, or real-money action is permitted.

---

### Task 1: Collector domain models, account binding, time rules, and versioned WH6 match decoding

**Files:**
- Create: `collector/wh6_collector/__init__.py`
- Create: `collector/wh6_collector/models.py`
- Create: `collector/wh6_collector/account.py`
- Create: `collector/wh6_collector/formats.py`
- Create: `collector/wh6_collector/parser.py`
- Create: `collector/wh6_collector/discovery.py`
- Create: `tests/test_wh6_collector_core.py`

**Interfaces:**
- `FillRecord`, `AccountIdentity`, `SourceFile`, `ParseIssue`, and `CollectorStatus` are immutable dataclasses containing only normalized, JSON-safe values.
- `parse_match_records(path: Path, *, account: AccountIdentity, source_file: SourceFile) -> tuple[list[FillRecord], list[ParseIssue]]` reads only match/fill files and never writes to `path`.
- `detect_layout(header: bytes, record_size: int) -> MatchLayout | None` returns a named supported layout or `None`; no fixed-size fallback is allowed.
- `is_option_contract(contract: str) -> bool` and `normalize_contract(contract: str) -> str` accept the WH6 option forms used by iron ore (`i2607-C-750`, `i2607-p-750`) and reject future/stock-like values.
- `account_fingerprint(account_label: str, stable_id: str | None) -> str | None` returns a SHA-256 fingerprint only for a stable identifier; `confirm_weak_binding(...)` marks a source as manual-confirmation-required when no stable ID exists.
- `business_trading_day(local_dt: datetime, *, timezone=ZoneInfo("Asia/Shanghai")) -> str` maps night-session timestamps to their exchange trading date and preserves the actual timestamp.

- [ ] **Step 1: Write failing tests** for option-only filtering, the supported 268-byte match layout plus an explicitly versioned 269-byte padded layout fixture, truncated/unknown files, strong versus weak account identity, night-session trading-day mapping, and the four supplied reference sessions.
- [ ] **Step 2: Run `pytest -q tests/test_wh6_collector_core.py`** and confirm the new imports/functions fail before implementation.
- [ ] **Step 3: Implement the dataclasses, conservative layout registry, shifted-text/number decoder, option classifier, account fingerprint helper, and time utilities. Keep parser I/O read-only and return quarantine issues for missing fields.
- [ ] **Step 4: Re-run the focused tests, then add a property-style test that random non-option records never enter the fill list.
- [ ] **Step 5: Commit `feat: add wh6 option fill parsing core`.

### Task 2: Windows path discovery and durable local SQLite outbox

**Files:**
- Modify: `collector/wh6_collector/discovery.py`
- Create: `collector/wh6_collector/local_store.py`
- Create: `collector/wh6_collector/monitor.py`
- Create: `tests/test_wh6_collector_store.py`

**Interfaces:**
- `discover_wh6_sources(extra_roots: Sequence[Path] = ()) -> list[SourceFile]` checks running-install hints, standard Windows user-data roots, and explicitly supplied roots; candidates include a human label, path, account clue, file mtime, and validation reason.
- `validate_source(path: Path) -> SourceFile` checks that the selected directory contains readable supported match files without modifying WH6 data.
- `LocalOutbox(db_path: Path)` creates its own SQLite schema outside WH6 and exposes `put(fill: FillRecord)`, `claim(limit: int)`, `ack(event_keys: Sequence[str])`, `release(event_keys: Sequence[str], error: str)`, and `status()`.
- `scan_source(source: SourceFile, checkpoint: dict[str, int] | None) -> ScanBatch` handles historical backfill and append/rotation checkpoints; a bad file produces an issue but does not discard queued fills.

- [ ] **Step 1: Write failing tests** for automatic candidate discovery, manual path validation, checkpoint resume after restart, file rotation, duplicate local event keys, claim/ack/release semantics, and queue preservation while a path is missing.
- [ ] **Step 2: Run the focused store tests and capture the expected failures.
- [ ] **Step 3: Implement the minimal SQLite schema (`config`, `file_checkpoints`, `outbox`, `issues`) with WAL mode, atomic claim, retry metadata, and no writes under the selected WH6 directory.
- [ ] **Step 4: Implement discovery and polling with a 10-second default interval; pause on unknown account/layout and continue draining already-bound queued rows.
- [ ] **Step 5: Run focused tests plus a 1000-row queue smoke test and commit `feat: add wh6 discovery and local outbox`.

### Task 3: Isolated Staging schema and deterministic ingest service

**Files:**
- Modify: `backend/app/db.py:18-55, 1506-1835, 2069-2125`
- Create: `backend/app/trading_collector_service.py`
- Create: `tests/test_trading_collector_service.py`
- Create: `supabase/migrations/20260902_wh6_collector.sql`

**Interfaces:**
- `migrate_trading_collector_schema(conn)` creates equivalent SQLite/PostgreSQL tables: `trading_collector_pairing_codes`, `trading_collector_devices`, `trading_intraday_fill_observations`, `trading_intraday_fills`, and `trading_collector_issues`, with foreign keys, audit timestamps, account/device indexes, and uniqueness on `(account_id, source_event_key)`.
- `issue_pairing_code(account_id: int, actor_id: int, ttl_seconds: int = 900) -> dict` stores only a code hash and returns the one-time plaintext once.
- `activate_device(pairing_code: str, device_name: str, client_version: str, fingerprint: str) -> dict` consumes a valid code once and returns a device token once.
- `ingest_observations(device_token: str, observations: Sequence[dict]) -> IngestResult` resolves the account from the token, validates the allowlisted fill fields, inserts every observation, and upserts one canonical fill per stable event key in one transaction.
- `query_intraday_fills(account_id: int, *, start: str = "", end: str = "", contract: str = "", status: str = "accepted", limit: int = 500) -> dict` returns read-only, normalized provisional fills.

- [ ] **Step 1: Write failing SQLite tests** for schema presence, one-time/expired pairing codes, token revocation, client account spoof rejection, accepted fill insertion, same-device replay, cross-device dedup, and conflicting duplicate quarantine.
- [ ] **Step 2: Run `pytest -q tests/test_trading_collector_service.py` and confirm failures.
- [ ] **Step 3: Extend `TRADING_MANAGEMENT_TABLES`, migration calls, and permission seeding without altering existing settlement tables. Mirror the DDL in the checked-in Supabase migration; enable RLS and revoke broad roles in the PostgreSQL branch, keeping the ingest function as the only write path.
- [ ] **Step 4: Implement token hashing, pairing-code consumption, strict Pydantic-compatible field validation, canonical event-key construction, observation-first insert, and conflict issue recording. Use server-bound `account_id`; ignore/reject any client account field.
- [ ] **Step 5: Run focused tests and inspect the generated SQLite schema; commit `feat: add wh6 collector staging ingest schema`.

### Task 4: FastAPI device administration, ingest, status, and read-only query routes

**Files:**
- Create: `backend/app/trading_collector.py`
- Modify: `backend/app/main.py:45-75`
- Modify: `backend/app/permissions.py:8-45, 55-95`
- Create: `tests/test_trading_collector_api.py`

**Interfaces:**
- Admin routes under `/api/trading-collector/admin`: `POST /pairing-codes`, `GET /devices`, `POST /devices/{device_id}/revoke`.
- Device routes under `/api/trading-collector/device`: `POST /activate`, `POST /heartbeat`, `POST /ingest`.
- Authenticated read-only route: `GET /api/trading-collector/fills` with account/date/contract/status filters.
- `trading_collector_current_user` reuses repository bearer sessions; `device_auth` accepts only an `X-Collector-Token` header and returns the server-side device/account row.

- [ ] **Step 1: Write failing API tests** for admin permission enforcement, pairing-code response redaction, device activation/heartbeat/revoke, ingest without a browser session, account-bound query filtering, and unauthenticated/guest rejection.
- [ ] **Step 2: Run the focused API tests and confirm failures.
- [ ] **Step 3: Implement route models, dependencies, structured error codes, request-size/rate limits, and router registration. Do not import `main.current_user` to avoid circular imports; use the same `db.get_user_by_token` logic locally.
- [ ] **Step 4: Run focused API tests, then run the existing auth/permission tests to prove retired modules and guest permissions are unchanged. Commit `feat: expose wh6 collector staging api`.

### Task 5: Minimal admin “采集设备” page and read-only intraday fill view

**Files:**
- Modify: `frontend/index.html:312-370, 1358-1365`
- Modify: `frontend/app.js:1-110, 520-650`
- Create: `frontend/trading_collector.js`
- Modify: `frontend/styles.css` or create `frontend/trading_collector.css`
- Create: `tests/trading_collector_frontend.test.mjs`

**Interfaces:**
- Add module code `trading_collector` under 后台管理 with admin-only visibility and a page showing pairing-code creation, devices, revoke action, and a compact latest-fill table.
- The UI displays only masked account labels, device name/version/status/last-seen, and human actions; it never renders full account numbers, tokens, database credentials, or full Windows paths.
- `window.TradingCollector.activate()` loads `/api/trading-collector/admin/devices` and `/api/trading-collector/fills`, and stops rendering controls when permissions do not allow them.

- [ ] **Step 1: Write failing DOM/source tests** for module registration, admin-only controls, masked fields, revoke confirmation, and read-only fill rendering.
- [ ] **Step 2: Run `node --test tests/trading_collector_frontend.test.mjs` and confirm failures.
- [ ] **Step 3: Add the page, module dispatch, API calls, and CSS with existing app conventions; keep all buttons read-only except pairing/revoke administration.
- [ ] **Step 4: Run the focused frontend test and existing frontend test suite; commit `feat: add collector device admin view`.

### Task 6: Windows launcher, build manifest, and migration/operation documentation

**Files:**
- Create: `collector/wh6_collector/cli.py`
- Create: `collector/requirements-windows.txt`
- Create: `collector/WH6成交采集器.spec`
- Create: `collector/installer/README.md`
- Create: `docs/superpowers/plans/2026-09-02-wh6-windows-acceptance-runbook.md`
- Modify: `README.md` (setup/structure section only)

**Interfaces:**
- `python -m wh6_collector.cli --configure` supports manual source selection, pairing activation, and local status; `--once` performs one read-only scan/upload cycle; `--service` polls every 10 seconds.
- The launcher stores configuration under `%LOCALAPPDATA%\\WH6成交采集器`, uses HTTPS Staging URL supplied at build/config time, and refuses Production URLs in the test build.
- The spec and installer README define the future Windows x64 self-contained `Setup.exe` build, auto-start behavior, clean migration, uninstall preservation, and the requirement to run real-cache acceptance in the Windows 11 VM before calling the package compatible.

- [ ] **Step 1: Write failing CLI tests** for no-Production guard, default paths, `--once` exit codes, and queue-preserving offline behavior.
- [ ] **Step 2: Run focused CLI tests and confirm failures.
- [ ] **Step 3: Implement the thin CLI around Tasks 1–2 and add the PyInstaller spec/installer manifest without embedding secrets; keep Windows-only build commands documented rather than pretending Mac can produce the EXE.
- [ ] **Step 4: Run CLI tests, `python -m compileall collector backend/app`, and inspect the package manifest for secret/path leaks. Commit `feat: add windows collector launcher packaging manifest`.

### Task 7: End-to-end local verification and Staging readiness gate

**Files:**
- Create: `tests/test_wh6_collector_end_to_end.py`
- Modify: `docs/superpowers/plans/2026-09-02-wh6-windows-acceptance-runbook.md`
- Modify: `版本更新记录.md` only after a Staging deployment is actually verified

- [ ] **Step 1: Write a synthetic end-to-end test** that creates two 231/232-byte source files, scans them from two device stores, sends both batches to the local FastAPI app, and asserts one canonical fill plus two observations, with a second independent same-signature fill retained.
- [ ] **Step 2: Run the test and fix only failures caused by this feature.
- [ ] **Step 3: Run the focused collector suite, existing trading/auth/frontend suites, and record the known pre-existing baseline failures separately.
- [ ] **Step 4: If Staging credentials and deployment access are available, apply only the new migration to `LTM WEB STAGING`, run a test query and browser-visible read path, and record evidence; otherwise mark the migration as not applied and leave Production untouched.
- [ ] **Step 5: Perform a final diff/security review for read-only WH6 access, token/key leakage, account spoofing, duplicate handling, and full-path/account masking; then summarize whether Windows `Setup.exe` has been built or remains the next Windows-only gate.

---

## Spec coverage self-review

- Account binding, weak-binding pause, path discovery/manual selection, binary versioning, option-only scope, backfill/incremental polling, sessions, local queue, restricted ingest, dedup, statuses, admin device management, read-only system/Agent query, masking, and Staging boundary are covered by Tasks 1–6.
- Settlement reconciliation, multi-account operation, positions/P&L/quotes, production deployment, and real Windows acceptance remain explicitly out of this implementation slice and are called out as later gates in the spec.
- No placeholder terms are used; all functions and endpoints referenced later are defined above.
