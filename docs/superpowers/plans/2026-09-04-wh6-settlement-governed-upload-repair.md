# WH6 月结单治理、数据纠错与覆盖升级修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (only after the user explicitly requests delegation) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 WH6 V2.1 按生效月结单停止重复历史上传，以月结单/日结单对盘中成交做字段级补全和修正，修复版本迁移与上传阻塞，并把后台成交明细改成 20/50/100 服务端分页。

**Architecture:** 服务端先从本环境 `active` 的完整月结批次生成明确关闭区间，并在设备上传入口再次强制校验；日结和月结通过独立协调器与 WH6 原始成交建立追加式来源关系，按“月结 200 > 日结 100 > WH6 0”生成有效字段。Windows 客户端通过受限策略接口跳过已关闭月份，在本地事务中迁移旧版本事件键、检查点和队列，再由自包含 Setup 原位覆盖安装。

**Tech Stack:** Python 3.11、FastAPI、Pydantic、SQLite、PostgreSQL/Supabase、vanilla JavaScript、Node test runner、PyInstaller、Inno Setup 6、GitHub Actions Windows runner。

**Spec:** `docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md`

## Global Constraints

- 采集关闭区间只来自当前环境、当前账户、`status='active'`、正文识别为完整自然月的月结单。
- 日结单绝不推进关闭区间，但可以补全或修正未月结成交。
- 字段优先级固定为月结单 200、日结单 100、WH6 0；高来源空值不得清空低来源有效值。
- 中间缺月时只跳过明确关闭的月份，不使用单一最大日期跨过缺口。
- 跨期组合是一笔 `future_spread` 成交，不拆腿，不进入期权成交量。
- Windows 目标机不安装 Python；有效路径、DPAPI 设备令牌和本地队列在覆盖升级后保留。
- 第一轮只允许 Staging 和 `LTM WEB STAGING`；Production 迁移、数据修复、合并和发布必须另行 Gate B 确认。
- WH6 全链路严格只读；不得增加下单、撤单、改单、平仓、行权、进程注入、内存读取、抓包或界面控制。
- 原始 WH6 观察、结算来源行和差异审计不物理删除。
- 当前计划基线观察为 `origin/staging=4eca33c`；执行开始时必须重新 fetch 并记录新的真实基线。

---

## 文件结构和职责

### 新建文件

- `backend/app/trading_collector_reconciliation.py`：结算覆盖区间、成交身份标准化、字段级协调和月结关闭逻辑。
- `collector/wh6_collector/version.py`：客户端版本、策略 schema 和本地 schema 的唯一版本源。
- `collector/wh6_collector/policy.py`：设备采集策略模型、缓存和日期覆盖判断。
- `collector/wh6_collector/migrations.py`：Windows 本地 SQLite 备份、版本迁移、事件键合并和检查点升级。
- `supabase/migrations/20260904_wh6_settlement_reconciliation.sql`：Staging/PostgreSQL 的向前兼容 schema 迁移。
- `scripts/reconcile_wh6_intraday.py`：默认 dry-run、显式 apply 的环境数据协调命令。
- `tests/test_trading_collector_reconciliation.py`：月结覆盖、日/月优先级、字段保留和异常匹配测试。
- `tests/test_wh6_collector_migrations.py`：V1/V2 本地事件键、队列状态和备份迁移测试。
- `tests/test_wh6_collector_policy.py`：策略缓存、缺月、离线和历史暂停测试。
- `tests/test_wh6_spread_parser.py`：真实脱敏组合成交单记录和不拆腿测试。
- `tests/fixtures/wh6_spread_match.dat`：仅含一个组合成交记录、已替换账户和人员标识的最小脱敏夹具。
- `tests/test_reconcile_wh6_intraday_script.py`：dry-run/apply、幂等和环境保护测试。

### 主要修改文件

- `backend/app/db.py`：SQLite/PostgreSQL 等价 schema、索引、RLS 对象清单。
- `backend/app/trading_management.py`：结算成交编号落列、月结完整性收口、确认事务内触发协调。
- `backend/app/trading_collector_service.py`：设备策略、月结二次拦截、逐条回执、分页和正确聚合。
- `backend/app/trading_collector.py`：策略接口、分页参数和响应合同。
- `collector/wh6_collector/models.py`：`future_spread` 的组合腿字段。
- `collector/wh6_collector/parser.py`：组合成交识别和规范化事件键。
- `collector/wh6_collector/monitor.py`：按关闭区间跳过历史文件，并把解析器版本写入检查点。
- `collector/wh6_collector/local_store.py`：新终态、退避时间、逐条确认和策略批量迁移。
- `collector/wh6_collector/uploader.py`：策略读取、100 条上传和逐条结果。
- `collector/wh6_collector/cli.py`：运行时版本、启动迁移、策略门禁和状态输出。
- `collector/launcher.py`：Windows 单实例互斥。
- `collector/installer/WH6成交采集器.iss`：0.2.1 覆盖升级、关闭旧实例和版本化输出。
- `collector/installer/build_windows.ps1`：统一版本、版本化发布目录和哈希校验。
- `.github/workflows/build-wh6-windows.yml`：从当前修复分支手工构建可下载安装包。
- `frontend/index.html`、`frontend/trading_collector.js`、`frontend/trading_collector.css`：20/50/100 分页和状态展示。
- `README.md`、`collector/installer/README.md`、V2 验收 runbook：新规则、安装和证据边界。

---

### Task 1: 从最新 Staging 建立干净修复基线

**Files:**
- Add unchanged to the clean worktree: `docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md`
- Add unchanged to the clean worktree: `docs/superpowers/plans/2026-09-04-wh6-settlement-governed-upload-repair.md`
- No application file changes in this task.
- Read: `README.md`
- Read: `开发流程_备忘.md`
- Read: `版本更新记录.md`
- Read: `docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md`

**Interfaces:**
- Consumes: latest fetched `origin/staging` and the two V2-only commits `b5f4e41`, `b963f2e`.
- Produces: isolated branch `codex/wh6-settlement-governed-upload-repair-20260904` containing current Staging, these two reviewed planning documents and only the required WH6 V2 changes.

- [ ] **Step 1: Fetch and record the real execution baseline**

```bash
git fetch origin
git rev-parse origin/staging origin/main
git status --short --branch
```

Expected: fetch succeeds; the recorded Staging commit replaces the planning snapshot if it has advanced. The current planning worktree may contain only the two reviewed untracked planning documents; any application-code change is a stop condition.

- [ ] **Step 2: Create an isolated worktree from current Staging**

Use `superpowers:using-git-worktrees` and run:

```bash
git worktree add ../wh6-settlement-governed-repair -b codex/wh6-settlement-governed-upload-repair-20260904 origin/staging
```

Expected: the new worktree starts exactly at current `origin/staging` and does not inherit the present stale branch's unrelated 60-file diff.

- [ ] **Step 3: Carry the reviewed plan into the clean branch**

Use `apply_patch` in the new worktree to add the exact reviewed spec and implementation plan from this planning worktree. Compare SHA-256 values with the source copies, then commit only those two files:

```bash
cmp /Users/wangjingze/.codex/worktrees/822e/轻量化交易管理系统WEB/docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md
cmp /Users/wangjingze/.codex/worktrees/822e/轻量化交易管理系统WEB/docs/superpowers/plans/2026-09-04-wh6-settlement-governed-upload-repair.md docs/superpowers/plans/2026-09-04-wh6-settlement-governed-upload-repair.md
shasum -a 256 docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md docs/superpowers/plans/2026-09-04-wh6-settlement-governed-upload-repair.md
git add docs/superpowers/specs/2026-09-04-wh6-settlement-governed-upload-repair-design.md docs/superpowers/plans/2026-09-04-wh6-settlement-governed-upload-repair.md
git commit -m "docs: plan WH6 settlement-governed repair"
```

Expected: the clean branch carries the accepted design and executable checklist without importing any other current-branch history.

- [ ] **Step 4: Port only the two V2 implementation commits**

```bash
git cherry-pick b5f4e41 b963f2e
```

Expected: only WH6 collector backend, V2 position/fill client, page, migration and their tests are added or changed. If a conflict touches order lifecycle, spot ledger, option review or another unrelated module, abort the cherry-pick and reapply the two commit diffs file-by-file instead of accepting the unrelated side.

- [ ] **Step 5: Prove scope isolation**

```bash
git diff --name-only origin/staging...HEAD
git diff --check
```

Expected: no unrelated order-lifecycle, spot-ledger, financing or option-review implementation is introduced by this repair branch.

- [ ] **Step 6: Run the imported V2 baseline tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_collector_core.py tests/test_wh6_position_parser.py tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_v2_end_to_end.py tests/test_trading_collector_service.py tests/test_trading_collector_positions_service.py tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py
node --test tests/trading_collector_frontend.test.mjs
```

Expected: imported V2 baseline passes before V2.1 behavior is added. Any failure is recorded as baseline evidence and fixed before later tasks are credited.

### Task 2: Add settlement identity and reconciliation schema

**Files:**
- Create: `supabase/migrations/20260904_wh6_settlement_reconciliation.sql`
- Modify: `backend/app/db.py:1436-1445`
- Modify: `backend/app/db.py:1535-1602`
- Modify: `backend/app/db.py:1800-1848`
- Modify: `backend/app/db.py:2082-2394`
- Modify: `tests/test_trading_management.py:147-180`
- Modify: `tests/test_trading_collector_service.py:60-77`
- Create: `tests/test_trading_collector_reconciliation.py`

**Interfaces:**
- Consumes: existing `trading_import_batches`, `trading_source_rows`, `trading_trade_facts`, `trading_intraday_fills`.
- Produces: settlement transaction columns, collector reconciliation state, append-only `trading_intraday_fill_reconciliations`, indexes and matching RLS/grant posture.

- [ ] **Step 1: Write failing schema tests**

```python
def test_reconciliation_schema_is_forward_only_and_auditable(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        trade_columns = {row[1] for row in conn.execute("PRAGMA table_info(trading_trade_facts)")}
        intraday_columns = {row[1] for row in conn.execute("PRAGMA table_info(trading_intraday_fills)")}
        assert {"transaction_no", "normalized_transaction_no"} <= trade_columns
        assert {
            "reconciliation_status", "settlement_identity_id", "settlement_batch_id",
            "effective_source", "reconciled_at",
        } <= intraday_columns
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trading_intraday_fill_reconciliations'"
        ).fetchone()
```

Also assert that existing source/fact rows survive a second `db.init_db()` unchanged and that the new table is in `TRADING_COLLECTOR_TABLES`.

- [ ] **Step 2: Run schema tests and confirm the expected failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_management.py -k statement_schema tests/test_trading_collector_service.py -k schema tests/test_trading_collector_reconciliation.py -k schema
```

Expected: FAIL because the new columns and audit table do not exist.

- [ ] **Step 3: Add equivalent SQLite and PostgreSQL definitions**

The PostgreSQL migration and `db.py` fallback must create the following contract without dropping or renaming existing objects:

```sql
ALTER TABLE trading_trade_facts ADD COLUMN IF NOT EXISTS transaction_no TEXT;
ALTER TABLE trading_trade_facts ADD COLUMN IF NOT EXISTS normalized_transaction_no TEXT;
ALTER TABLE trading_intraday_fills ADD COLUMN IF NOT EXISTS reconciliation_status TEXT NOT NULL DEFAULT 'unmatched';
ALTER TABLE trading_intraday_fills ADD COLUMN IF NOT EXISTS settlement_identity_id INTEGER;
ALTER TABLE trading_intraday_fills ADD COLUMN IF NOT EXISTS settlement_batch_id INTEGER;
ALTER TABLE trading_intraday_fills ADD COLUMN IF NOT EXISTS effective_source TEXT NOT NULL DEFAULT 'wh6';
ALTER TABLE trading_intraday_fills ADD COLUMN IF NOT EXISTS reconciled_at TEXT;
```

Create `trading_intraday_fill_reconciliations` with the exact fields in the spec, foreign keys to intraday fill, identity and batch, `is_current`, and indexes for current lookup. Enable RLS and revoke direct `anon/authenticated` privileges in PostgreSQL.

- [ ] **Step 4: Add idempotent SQLite column guards**

Use the repository's existing `_ensure_*_columns` pattern so an old SQLite file can be opened repeatedly without `duplicate column` errors. Do not rebuild or drop existing tables.

- [ ] **Step 5: Run schema and security tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_management.py -k statement_schema tests/test_trading_collector_service.py -k schema tests/test_trading_collector_reconciliation.py -k schema tests/test_auth_permissions.py
git diff --check
```

Expected: all targeted tests pass; existing facts remain; migration contains no grant to browser roles.

- [ ] **Step 6: Commit the schema slice**

```bash
git add backend/app/db.py supabase/migrations/20260904_wh6_settlement_reconciliation.sql tests/test_trading_management.py tests/test_trading_collector_service.py tests/test_trading_collector_reconciliation.py
git commit -m "feat: add auditable WH6 settlement reconciliation schema"
```

### Task 3: Implement active-monthly-only collection policy

**Files:**
- Create: `backend/app/trading_collector_reconciliation.py`
- Modify: `backend/app/trading_collector_service.py:183-336`
- Modify: `backend/app/trading_collector.py:89-118`
- Modify: `tests/test_trading_collector_reconciliation.py`
- Modify: `tests/test_trading_collector_api.py`

**Interfaces:**
- Produces: `normalize_exchange(value: str) -> str`.
- Produces: `normalize_transaction_no(value: object) -> str`.
- Produces: `get_active_monthly_ranges(cur, account_id: int) -> list[dict[str, object]]`.
- Produces: `build_collection_policy(account_id: int) -> dict[str, object]`.
- Produces: `get_device_collection_policy(device_id: int) -> dict[str, object]`.
- HTTP: `GET /api/trading-collector/device/collection-policy`, authenticated only by `X-Collector-Token`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_only_active_complete_monthly_batches_close_collection(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    insert_batch(account_id, "20260601", "20260630", "active", "monthly")
    insert_batch(account_id, "20260701", "20260731", "active", "daily")
    insert_batch(account_id, "20260801", "20260831", "preview", "monthly")
    insert_batch(account_id, "20260902", "20260930", "active", "monthly")
    policy = reconciliation.build_collection_policy(account_id)
    assert [(item["range_start"], item["range_end"]) for item in policy["closed_ranges"]] == [
        ("2026-06-01", "2026-06-30")
    ]

def test_policy_preserves_month_gap_instead_of_using_max_date(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    insert_batch(account_id, "20260601", "20260630", "active", "monthly")
    insert_batch(account_id, "20260801", "20260831", "active", "monthly")
    policy = reconciliation.build_collection_policy(account_id)
    assert [item["month"] for item in policy["closed_ranges"]] == ["2026-06", "2026-08"]
    assert "settled_through" not in policy
```

API tests must also prove that a device cannot request another account's policy and an unauthenticated request returns 401.

- [ ] **Step 2: Run policy tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_reconciliation.py -k policy tests/test_trading_collector_api.py -k collection_policy
```

Expected: FAIL because no policy service or route exists.

- [ ] **Step 3: Implement deterministic normalization and full-month validation**

Use `calendar.monthrange` and parsed dates. Accept a range only when it starts on day 1, ends on the actual last day, stays within one month, is `active`, and is `monthly`. Convert stored `YYYYMMDD` and `YYYY-MM-DD` forms to ISO output. Sort and deduplicate ranges before hashing them.

```python
def normalize_transaction_no(value: object) -> str:
    text = str(value or "").strip().lower()
    return str(int(text)) if text.isdigit() else text
```

`policy_revision` is SHA-256 over stable JSON containing schema version, minimum client version and sorted ranges.

- [ ] **Step 4: Implement the device-bound route**

The route uses the existing `device_auth` dependency and passes only `device["id"]`; it accepts no account parameter. Return the exact spec contract with `minimum_client_version="0.2.1"` and the four listed capabilities.

- [ ] **Step 5: Run policy/API tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_reconciliation.py -k policy tests/test_trading_collector_api.py
```

Expected: active daily, preview monthly and incomplete monthly batches never enter `closed_ranges`; account binding and 401 behavior pass.

- [ ] **Step 6: Commit the policy slice**

```bash
git add backend/app/trading_collector_reconciliation.py backend/app/trading_collector_service.py backend/app/trading_collector.py tests/test_trading_collector_reconciliation.py tests/test_trading_collector_api.py
git commit -m "feat: govern WH6 history with active monthly statements"
```

### Task 4: Persist settlement transaction numbers and close lower-priority monthly facts

**Files:**
- Modify: `backend/app/trading_management.py:365-417`
- Modify: `backend/app/trading_management.py:1101-1402`
- Modify: `backend/app/trading_collector_reconciliation.py`
- Modify: `tests/test_trading_management.py:539-610`
- Modify: `tests/test_trading_collector_reconciliation.py`

**Interfaces:**
- Produces: `backfill_settlement_transaction_numbers(cur, *, account_id: int | None = None) -> int`.
- Produces: `finalize_lower_priority_monthly_trades(cur, batch_id: int) -> dict[str, int]`.
- `confirm_settlement_import(...)` stores `transaction_no` and `normalized_transaction_no` on every trade fact.

- [ ] **Step 1: Write failing transaction-number and monthly-completeness tests**

```python
def test_monthly_confirmation_persists_normalized_transaction_number(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    result = confirm_fixture_statement("monthly", transaction_no="000123")
    with db.connect() as conn:
        row = conn.execute(
            "SELECT transaction_no, normalized_transaction_no FROM trading_trade_facts WHERE batch_id=?",
            (result["batch_id"],),
        ).fetchone()
    assert row["transaction_no"] == "000123"
    assert row["normalized_transaction_no"] == "123"

def test_monthly_absence_retires_daily_trade_without_deleting_history(tmp_path, monkeypatch):
    daily_id = confirm_daily_with_two_trades(tmp_path, monkeypatch)
    monthly_id = confirm_monthly_with_first_trade_only()
    assert current_trade_count() == 1
    assert all_trade_version_count() == 3
    assert latest_difference(monthly_id)["change_type"] == "absent_from_monthly"
```

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_management.py -k "monthly or transaction_number" tests/test_trading_collector_reconciliation.py -k settlement_key
```

Expected: FAIL because transaction numbers exist only in source JSON and monthly confirmation does not retire lower-priority rows absent from the complete month.

- [ ] **Step 3: Write transaction numbers during fact insertion**

Extend the `trading_trade_facts` insert tuple and SQL at the existing statement confirmation path. Keep the current stable identity and source rows unchanged.

- [ ] **Step 4: Add idempotent legacy backfill**

Read `trading_source_rows.raw_json` for statement trade facts whose normalized number is empty. Extract `transaction_no` from the parsed source row, normalize it, and update only the two new metadata columns. Return the changed row count and leave unparseable JSON untouched with an audited count.

- [ ] **Step 5: Finalize a complete monthly source set**

After all monthly trade rows are inserted but before commit, find current trade facts in the same account/range whose source priority is below 200 and whose identities are absent from the monthly batch. Set only those lower-priority versions `is_current=0` and append `trading_fact_source_differences` entries with `change_type="absent_from_monthly"`. Do not retire daily position snapshots for other dates.

- [ ] **Step 6: Run settlement regression tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_settlement.py tests/test_trading_management.py -k "statement or monthly or import"
```

Expected: existing monthly-over-daily tests still pass; transaction numbers are queryable; absent daily trades are historical, not deleted.

- [ ] **Step 7: Commit the settlement-key slice**

```bash
git add backend/app/trading_management.py backend/app/trading_collector_reconciliation.py tests/test_trading_management.py tests/test_trading_collector_reconciliation.py
git commit -m "fix: finalize monthly settlement trade facts"
```

### Task 5: Implement field-level WH6 and settlement reconciliation

**Files:**
- Modify: `backend/app/trading_collector_reconciliation.py`
- Modify: `backend/app/trading_management.py:1386-1402`
- Modify: `backend/app/trading_collector_service.py:339-510`
- Modify: `tests/test_trading_collector_reconciliation.py`
- Modify: `tests/test_trading_collector_service.py`

**Interfaces:**
- Produces: `match_intraday_fill(cur, fill: Mapping[str, object]) -> MatchDecision`.
- Produces: `resolve_fill_fields(wh6: Mapping, settlement: Mapping | None, authority_type: str) -> ResolvedFill`.
- Produces: `reconcile_intraday_fills_for_batch(cur, batch_id: int, actor: str) -> ReconciliationSummary`.
- Produces: `reconcile_intraday_range(cur, account_id: int, start: str, end: str, actor: str) -> ReconciliationSummary`.
- `MatchDecision.status` is one of `unmatched`, `ambiguous`, `matched_daily`, `corrected_daily`, `matched_monthly`, `corrected_monthly`, `monthly_unmatched`.

- [ ] **Step 1: Write failing reconciliation golden cases**

```python
def test_daily_corrects_present_fields_but_does_not_close_month(tmp_path, monkeypatch):
    fill_id = insert_wh6_fill(trade_time="09:31:02", price="785", fee=None)
    daily_batch = confirm_statement_trade(price=786, fee=1.5, transaction_no="000123", statement_type="daily")
    result = current_resolution(fill_id)
    assert result["result_status"] == "corrected_daily"
    assert result["resolved_fields"]["price"] == 786
    assert result["resolved_fields"]["fee"] == 1.5
    assert result["resolved_fields"]["trade_time"] == "09:31:02"
    assert result["field_sources"]["trade_time"] == "wh6"
    assert build_collection_policy(account_id)["closed_ranges"] == []

def test_monthly_overrides_daily_without_blank_overwrite(tmp_path, monkeypatch):
    fill_id = insert_wh6_fill(trade_time="09:31:02", price="785")
    confirm_statement_trade(price=786, transaction_no="123", statement_type="daily")
    confirm_statement_trade(price=787, transaction_no="000123", statement_type="monthly")
    result = current_resolution(fill_id)
    assert result["result_status"] == "corrected_monthly"
    assert result["resolved_fields"]["price"] == 787
    assert result["resolved_fields"]["trade_time"] == "09:31:02"
    assert result["field_sources"]["price"] == "monthly"
    assert result["field_sources"]["trade_time"] == "wh6"
```

Add cases for Chinese/English exchange aliases, leading-zero IDs, unique no-ID fallback, ambiguous duplicate fallback, a monthly month with no matching settlement trade, and a daily statement arriving after monthly.

- [ ] **Step 2: Run golden cases and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_reconciliation.py -k "daily or monthly or ambiguous or unmatched"
```

Expected: FAIL because no reconciliation engine exists.

- [ ] **Step 3: Implement identity-first matching**

Query candidates through current `trading_trade_facts`, `trading_fact_identities` and active batches. First use account/date/normalized exchange/normalized transaction number. Verify contract, side, open-close, quantity and Decimal-normalized price. If no ID is available, accept the composite fallback only when exactly one candidate exists.

- [ ] **Step 4: Implement nonblank field merge**

Resolve these statement-owned fields when present: `exchange`, `contract`, `asset_type`, `side`, `open_close`, `quantity`, `price`, `turnover`, `fee`, `hedge_flag`, `premium_cashflow`, `close_profit`. Preserve WH6-only `trade_time`, `trade_timestamp`, `order_id`, source hashes/path/index and parser version. Store resolved values and a source per field in JSON; compare Decimals canonically so `785`, `785.0` and `785.000` are equal.

- [ ] **Step 5: Persist one current resolution plus history**

In one transaction, mark the previous reconciliation row non-current, insert the new append-only row, and update the five status/link columns on `trading_intraday_fills`. Monthly matched/corrected rows become `settlement_covered`; monthly unmatched/ambiguous rows become `settlement_conflict`; daily rows remain `provisional`.

- [ ] **Step 6: Hook reconciliation into statement confirmation**

After `trading_import_batches.status` is set to `active` and before `conn.commit()`, call `reconcile_intraday_fills_for_batch(cur, preview_batch_id, actor)`. Include its counts in the confirmation result so failure rolls back both statement activation and reconciliation together.

- [ ] **Step 7: Run reconciliation and ingest regressions**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_reconciliation.py tests/test_trading_collector_service.py tests/test_trading_management.py -k "statement or monthly or intraday or conflict"
```

Expected: all source priority, no-blank, ambiguity and transaction rollback cases pass; original observations remain unchanged.

- [ ] **Step 8: Commit the reconciliation engine**

```bash
git add backend/app/trading_collector_reconciliation.py backend/app/trading_management.py backend/app/trading_collector_service.py tests/test_trading_collector_reconciliation.py tests/test_trading_collector_service.py
git commit -m "feat: reconcile WH6 fills with daily and monthly statements"
```

### Task 6: Add server-side monthly guard and per-item ingest receipts

**Files:**
- Modify: `backend/app/trading_collector_service.py:105-147`
- Modify: `backend/app/trading_collector_service.py:422-510`
- Modify: `backend/app/trading_collector.py:34-36`
- Modify: `tests/test_trading_collector_service.py`
- Modify: `tests/test_trading_collector_api.py`

**Interfaces:**
- `IngestResult.fill_results: tuple[dict[str, str], ...]`.
- Each fill result has exactly `event_key` and `status`.
- Accepted statuses: `accepted`, `duplicate`, `settlement_covered`, `conflict`, `quarantined`.
- The API continues accepting up to 500 rows temporarily for V1 compatibility; V2.1 sends at most 100.

- [ ] **Step 1: Write failing per-item receipt tests**

```python
def test_ingest_returns_terminal_result_for_every_event(tmp_path, monkeypatch):
    token = activate_with_active_august_monthly(tmp_path, monkeypatch)
    result = service.ingest_observations(token, [august_fill(), september_fill(), malformed_fill()]).to_dict()
    assert result["fill_results"] == [
        {"event_key": august_fill()["source_event_key"], "status": "settlement_covered"},
        {"event_key": september_fill()["source_event_key"], "status": "accepted"},
        {"event_key": malformed_fill()["source_event_key"], "status": "quarantined"},
    ]
```

Also test duplicate, canonical conflict, 501 rows rejected, and a monthly batch becoming active between policy fetch and upload.

- [ ] **Step 2: Run ingest tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_service.py -k receipt tests/test_trading_collector_api.py -k ingest
```

Expected: FAIL because only aggregate counters are returned and month coverage is not checked at ingest.

- [ ] **Step 3: Apply the server monthly guard before provisional acceptance**

After structural validation and device account binding, test the fill date against current active monthly ranges. Preserve an observation with status `settlement_covered`, return that terminal receipt, and do not create a normal provisional canonical fact. Existing already-created canonical rows are handled by Task 5 reconciliation.

- [ ] **Step 4: Return a receipt for every input item**

Preserve input order. Never make the client infer success from aggregate counters. Quarantined malformed mappings use their supplied safe `source_event_key` when present; records without one receive a request-local synthetic result key and are never retried by index.

- [ ] **Step 5: Run API/service tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_service.py tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py
```

Expected: aggregate counts equal receipt statuses; old 500-row requests still parse; the month guard remains authoritative.

- [ ] **Step 6: Commit the ingest contract**

```bash
git add backend/app/trading_collector_service.py backend/app/trading_collector.py tests/test_trading_collector_service.py tests/test_trading_collector_api.py
git commit -m "fix: return deterministic WH6 ingest receipts"
```

### Task 7: Implement true 20/50/100 pagination and correct option-volume aggregation

**Files:**
- Modify: `backend/app/trading_collector_service.py:807-881`
- Modify: `backend/app/trading_collector.py:120-188`
- Modify: `tests/test_trading_collector_service.py`
- Modify: `tests/test_trading_collector_api.py`
- Modify: `tests/test_trading_collector_positions_api.py`

**Interfaces:**
- `query_intraday_fills(account_id, *, start="", end="", contract="", status="active", page=1, page_size=20, asset_type=None) -> dict`.
- Response: `items`, `account_id`, `page`, `page_size`, `total_items`, `total_pages`.
- `query_option_volume(account_id, *, trade_date="", contract="") -> dict` aggregates every eligible row independently of list pagination.

- [ ] **Step 1: Write failing pagination and >500-row aggregate tests**

```python
def test_fill_query_uses_stable_server_pagination(tmp_path, monkeypatch):
    account_id = seed_45_intraday_options(tmp_path, monkeypatch)
    first = service.query_intraday_fills(account_id, page=1, page_size=20, asset_type="option")
    third = service.query_intraday_fills(account_id, page=3, page_size=20, asset_type="option")
    assert (first["total_items"], first["total_pages"], len(first["items"])) == (45, 3, 20)
    assert len(third["items"]) == 5
    assert first["items"][0]["id"] != third["items"][0]["id"]

def test_option_volume_is_not_capped_by_fill_page_size(tmp_path, monkeypatch):
    account_id = seed_650_intraday_options(tmp_path, monkeypatch, quantity=2)
    result = service.query_option_volume(account_id, trade_date="2026-09-04")
    assert result["total_quantity"] == 1300
```

Add tests that `settlement_covered`, `settlement_conflict`, futures and `future_spread` do not contribute to option volume; daily-resolved quantity does.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_service.py -k "pagination or volume" tests/test_trading_collector_api.py -k fills
```

Expected: FAIL because the current service returns `len(items)` after a fixed `LIMIT` and option volume sums only that limited list.

- [ ] **Step 3: Implement count plus stable page query**

Build one shared WHERE clause and parameters for count and detail SQL. Validate `page_size` against `{20, 50, 100}` and reject unsupported values with 422 at the API boundary. Use offset `(page - 1) * page_size` and order by `trade_date DESC, trade_time DESC, id DESC`.

- [ ] **Step 4: Resolve current daily fields on the returned page**

Join or batch-read the current reconciliation rows for page IDs and replace display fields from `resolved_fields_json`. Keep raw source and audit fields out of the browser response.

- [ ] **Step 5: Implement full-set option aggregation**

Load all eligible IDs and quantities for one day without the list page limit, apply current daily resolved values, deduplicate by canonical intraday ID, and build `total_quantity/by_contract/by_side/by_open_close/by_option_kind`. A month-closed date returns zero provisional volume rather than counting settlement-covered rows.

- [ ] **Step 6: Run service and API regression tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_collector_service.py tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py
```

Expected: page metadata and boundaries pass; 650-row total is correct; old account/permission filters still pass.

- [ ] **Step 7: Commit the query slice**

```bash
git add backend/app/trading_collector_service.py backend/app/trading_collector.py tests/test_trading_collector_service.py tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py
git commit -m "fix: paginate WH6 fills and aggregate complete option volume"
```

### Task 8: Add frontend 20/50/100 pagination

**Files:**
- Modify: `frontend/index.html:350-380`
- Modify: `frontend/trading_collector.js:1-127`
- Modify: `frontend/trading_collector.css`
- Modify: `tests/trading_collector_frontend.test.mjs`

**Interfaces:**
- `state.fillPage` defaults to 1.
- `state.fillPageSize` defaults to 20 and only accepts 20, 50, 100.
- `renderFillPagination(data)` renders total, page/total pages, page-size select, previous and next buttons.
- Changing account or page size resets `fillPage=1`; previous/next retain current account.

- [ ] **Step 1: Write failing frontend contract tests**

```javascript
test("collector fills use 20 50 100 server pagination", () => {
  assert.match(collectorJs, /fillPageSize:\s*20/);
  assert.match(collectorJs, /\[20,\s*50,\s*100\]/);
  assert.match(collectorJs, /page=\$\{state\.fillPage\}/);
  assert.match(collectorJs, /page_size=\$\{state\.fillPageSize\}/);
  assert.match(html, /collectorFillPagination/);
  assert.doesNotMatch(collectorJs, /limit=100/);
});
```

Also assert escaped text, disabled boundary buttons, page reset on account/page-size change, and no trading action controls.

- [ ] **Step 2: Run the frontend test and confirm failure**

```bash
node --test tests/trading_collector_frontend.test.mjs
```

Expected: FAIL because `loadData()` hardcodes `limit=100` and no pagination node exists.

- [ ] **Step 3: Add pagination markup and controller state**

Reuse the existing `.tm-pagination` visual contract while keeping collector-specific IDs. Render `共 N 条`, `每页 20/50/100 条`, `上一页`, `第 X / Y 页`, `下一页`. Avoid prefetching every page.

- [ ] **Step 4: Keep independent data requests**

Changing a fill page should reload devices, volume and current positions only when needed; the page implementation may split `loadFills()` from `loadSummaryData()` so flipping a page does not make unrelated requests.

- [ ] **Step 5: Bump the collector static asset version**

Change the `trading_collector.js` query version in `frontend/index.html` to a unique `20260904v21` value so Staging browser verification can distinguish the deployment.

- [ ] **Step 6: Run frontend regressions and syntax checks**

```bash
node --test tests/trading_collector_frontend.test.mjs tests/trading_management_frontend.test.mjs tests/trading_overview_frontend_behavior.test.mjs
node --check frontend/trading_collector.js
git diff --check
```

Expected: all tests pass; no `limit=100`; existing trading management pagination remains unchanged.

- [ ] **Step 7: Commit the page slice**

```bash
git add frontend/index.html frontend/trading_collector.js frontend/trading_collector.css tests/trading_collector_frontend.test.mjs
git commit -m "feat: paginate WH6 collector fills"
```

### Task 9: Make client version and local SQLite migration deterministic

**Files:**
- Create: `collector/wh6_collector/version.py`
- Create: `collector/wh6_collector/migrations.py`
- Modify: `collector/wh6_collector/cli.py:29-103`
- Modify: `collector/wh6_collector/local_store.py:18-71`
- Modify: `collector/wh6_collector/parser.py:416-430`
- Create: `tests/test_wh6_collector_migrations.py`
- Modify: `tests/test_wh6_collector_cli.py:104-118`
- Modify: `tests/test_wh6_collector_store.py`

**Interfaces:**
- `CLIENT_VERSION = "0.2.1"`.
- `LOCAL_SCHEMA_VERSION = 3`.
- `canonical_fill_event_key(payload: Mapping[str, object]) -> str`.
- `migrate_local_store(db_path: Path, *, policy: CollectionPolicy | None) -> LocalMigrationResult`.
- `LocalMigrationResult` exposes `backup_path`, `old_version`, `new_version`, `keys_rewritten`, `duplicates_merged`, `claims_released`, `monthly_covered`.

- [ ] **Step 1: Write failing config and migration tests**

```python
def test_loaded_config_uses_runtime_version_not_persisted_v1(tmp_path):
    path = write_legacy_config(tmp_path, client_version="0.1.0")
    config = CollectorConfig.load(path)
    assert config.client_version == "0.2.1"

def test_v1_and_v2_aliases_merge_without_losing_terminal_state(tmp_path):
    db_path = seed_v1_v2_alias_rows(tmp_path, status_a="acked", status_b="pending")
    result = migrate_local_store(db_path, policy=None)
    assert result.duplicates_merged == 1
    rows = read_outbox(db_path)
    assert len(rows) == 1
    assert rows[0]["event_key"] == "tradeid:2026-09-04:dce:123"
    assert rows[0]["status"] == "acked"
    assert Path(result.backup_path).is_file()
```

Add tests for six aliases, all `claimed` rows released to pending, repeat migration returning zero changes, failed migration retaining the original database, and backups being placed under `backups/`.

- [ ] **Step 2: Run migration tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_collector_migrations.py tests/test_wh6_collector_cli.py -k "config or version" tests/test_wh6_collector_store.py -k migration
```

Expected: FAIL because config trusts the saved 0.1.0 and no local schema migration exists.

- [ ] **Step 3: Centralize version constants**

Import `CLIENT_VERSION` from `version.py` in CLI/setup/installer tests. `CollectorConfig.load()` ignores a persisted `client_version`; `save()` either omits it or always writes the runtime constant. Device activation and heartbeat always send the runtime constant.

- [ ] **Step 4: Implement backup-first local migration**

Before the first version-3 transaction, use SQLite backup API to write `backups/collector-v<old>-<UTC timestamp>.sqlite3`. Verify the backup with `PRAGMA quick_check` before changing the source database. Create `local_schema_meta(version, migrated_at)`.

- [ ] **Step 5: Canonicalize and merge event keys**

For rows containing a trade ID, recompute `tradeid:<ISO-date>:<normalized-exchange>:<normalized-id>`. Merge aliases in one transaction using the fixed terminal-state order `acked > covered_by_monthly > quarantined/conflict > pending > claimed`; keep the earliest creation time, latest update time, maximum attempts and nonempty error history. Recompute the key in payload JSON as well.

- [ ] **Step 6: Version checkpoints**

Add `parser_generation` to match checkpoints. Generation 2 forces one rescan of only uncovered match files so V2.1 can add futures and valid spreads without reopening monthly-closed files.

- [ ] **Step 7: Run local migration tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_collector_migrations.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_store.py tests/test_wh6_collector_core.py
```

Expected: legacy 0.1.0 reports 0.2.1 after load; aliases collapse; backup is valid; rerun is idempotent.

- [ ] **Step 8: Commit the local migration slice**

```bash
git add collector/wh6_collector/version.py collector/wh6_collector/migrations.py collector/wh6_collector/cli.py collector/wh6_collector/local_store.py collector/wh6_collector/parser.py tests/test_wh6_collector_migrations.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_store.py tests/test_wh6_collector_core.py
git commit -m "fix: migrate WH6 V1 state into V2.1"
```

### Task 10: Fetch collection policy and suppress monthly-covered local history

**Files:**
- Create: `collector/wh6_collector/policy.py`
- Modify: `collector/wh6_collector/uploader.py:10-37`
- Modify: `collector/wh6_collector/local_store.py:109-205`
- Modify: `collector/wh6_collector/cli.py:106-283`
- Modify: `collector/wh6_collector/monitor.py:65-124`
- Create: `tests/test_wh6_collector_policy.py`
- Modify: `tests/test_wh6_collector_cli.py`
- Modify: `tests/test_wh6_collector_scheduler.py`

**Interfaces:**
- `CollectionPolicy.from_payload(payload: Mapping) -> CollectionPolicy` rejects unknown schema and malformed ranges.
- `CollectionPolicy.covers(trade_date: str) -> bool` checks explicit intervals, not a maximum date.
- `StagingUploader.get_collection_policy() -> dict` uses `X-Collector-Token`.
- `LocalOutbox.apply_collection_policy(policy) -> int` returns rows moved to or restored from monthly coverage.
- New client status: `policy_unavailable_history_paused`.

- [ ] **Step 1: Write failing policy client tests**

```python
def test_gap_policy_skips_june_and_august_but_keeps_july():
    policy = CollectionPolicy.from_payload(policy_payload(months=["2026-06", "2026-08"]))
    assert policy.covers("2026-06-15") is True
    assert policy.covers("2026-07-15") is False
    assert policy.covers("2026-08-15") is True

def test_first_start_without_policy_scans_today_but_pauses_history(tmp_path):
    result = run_once(config_with_today_and_history(tmp_path), policy_fetch=lambda: raise_offline())
    assert result["state"] == "policy_unavailable_history_paused"
    assert uploaded_trade_dates() == {today_iso()}
```

Add tests that daily-derived payloads are rejected by schema validation, policy revision changes re-evaluate covered rows, and removal of a closed range restores an unacked covered row to pending.

- [ ] **Step 2: Run policy client tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_collector_policy.py tests/test_wh6_collector_cli.py -k policy tests/test_wh6_collector_scheduler.py -k history
```

Expected: FAIL because every historical match file is currently scanned.

- [ ] **Step 3: Implement strict policy parsing and caching**

Persist only the last validated policy payload, revision and fetch time in local SQLite. A policy is fresh for 300 seconds. Never accept a server schema other than 1 or a `minimum_client_version` greater than runtime 0.2.1.

- [ ] **Step 4: Apply policy before reading files**

In `run_once`, fetch/validate policy before historical scan. If a match source has a known trading date inside `closed_ranges`, skip `scan_source()` entirely. Positions and today's realtime scan keep their existing read-only logic. Unknown-date history remains paused rather than guessed.

- [ ] **Step 5: Move queued covered history to a terminal local state**

In one SQLite transaction, inspect pending/claimed fill payload dates and mark covered rows `covered_by_monthly`; clear claims and preserve attempts/errors. If a later fresh policy removes that range, restore only unacked rows to pending. Never revive `acked`, `conflict` or `quarantined` rows.

- [ ] **Step 6: Run policy and scheduler tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_collector_policy.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_store.py
```

Expected: explicit month gaps work; policy outage does not flood history; realtime remains available.

- [ ] **Step 7: Commit the client policy slice**

```bash
git add collector/wh6_collector/policy.py collector/wh6_collector/uploader.py collector/wh6_collector/local_store.py collector/wh6_collector/cli.py collector/wh6_collector/monitor.py tests/test_wh6_collector_policy.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_store.py
git commit -m "feat: suppress WH6 history covered by monthly statements"
```

### Task 11: Preserve cross-month spreads as one composite fill

**Files:**
- Modify: `collector/wh6_collector/models.py:58-93`
- Modify: `collector/wh6_collector/parser.py:31-143`
- Modify: `collector/wh6_collector/parser.py:318-464`
- Modify: `backend/app/trading_collector_service.py:17-94`
- Modify: `backend/app/trading_collector_service.py:339-417`
- Modify: `backend/app/db.py:2122-2156`
- Modify: `supabase/migrations/20260904_wh6_settlement_reconciliation.sql`
- Create: `tests/test_wh6_spread_parser.py`
- Modify: `tests/test_trading_collector_service.py`
- Modify: `tests/test_trading_collector_positions_api.py`

**Interfaces:**
- `FillRecord.asset_type` accepts `future_spread`.
- `FillRecord.spread_legs` is an optional two-item tuple of normalized futures contracts.
- Server stores `spread_legs_json` and validates exactly two futures legs.
- One source record produces exactly one canonical fill and zero synthetic leg fills.

- [ ] **Step 1: Capture one real spread record as a sanitized fixture**

Read one of the already identified 72 July/August records with the existing read-only decoder and retain only that record plus a synthetic file header. Replace account, trader, broker and source-event identifiers with same-width synthetic values before writing `tests/fixtures/wh6_spread_match.dat`; verify with both decoded output and a strings scan that no original identifier, path, token or credential remains, then record the sanitized fixture's SHA-256 in the test. Do not copy an entire WH6 cache. If the record cannot be sanitized without invalidating its structure, keep it outside Git, use a fully synthetic committed fixture, and leave the real-record acceptance gate open. If the raw encoding cannot be validated, stop this task with `unknown_format` rather than inventing a grammar.

- [ ] **Step 2: Write the failing one-record/zero-leg test**

```python
def test_real_spread_record_becomes_one_composite_fill():
    fills, issues = parse_fixture("tests/fixtures/wh6_spread_match.dat")
    assert issues == []
    assert len(fills) == 1
    assert fills[0].asset_type == "future_spread"
    assert len(fills[0].spread_legs) == 2
    assert all(classify_contract(leg) == "future" for leg in fills[0].spread_legs)
```

Add a service test proving one observation creates one row and an option-volume test proving it contributes zero.

- [ ] **Step 3: Run spread tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_spread_parser.py tests/test_trading_collector_service.py -k spread tests/test_trading_collector_positions_api.py -k volume
```

Expected: FAIL because current classification accepts only `future` and `option`.

- [ ] **Step 4: Implement only the evidenced spread grammar**

Normalize the two verified legs while preserving `raw_contract`. Build the event key from the source trade ID using the same account/date/exchange rule as ordinary fills. Do not emit synthetic leg records or infer ratio quantities absent from the source.

- [ ] **Step 5: Extend schema and server validation**

Add nullable `spread_legs_json` to PostgreSQL and SQLite. Permit `future_spread` only when exactly two normalized legs match `FUTURE_RE`; reject a malformed composite. Keep option API filters at `asset_type='option'`.

- [ ] **Step 6: Run parser/service/API regressions**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_spread_parser.py tests/test_wh6_collector_core.py tests/test_trading_collector_service.py tests/test_trading_collector_positions_api.py
```

Expected: one input record equals one stored composite; option totals are unchanged.

- [ ] **Step 7: Commit the spread slice**

```bash
git add tests/fixtures/wh6_spread_match.dat collector/wh6_collector/models.py collector/wh6_collector/parser.py backend/app/trading_collector_service.py backend/app/db.py supabase/migrations/20260904_wh6_settlement_reconciliation.sql tests/test_wh6_spread_parser.py tests/test_trading_collector_service.py tests/test_trading_collector_positions_api.py
git commit -m "feat: preserve WH6 spread fills as composites"
```

### Task 12: Bound upload batches, apply per-item acknowledgements and retry backoff

**Files:**
- Modify: `collector/wh6_collector/local_store.py:109-167`
- Modify: `collector/wh6_collector/uploader.py:10-37`
- Modify: `collector/wh6_collector/cli.py:141-196`
- Modify: `tests/test_wh6_collector_store.py`
- Modify: `tests/test_wh6_collector_scheduler.py`
- Modify: `tests/test_wh6_collector_cli.py`
- Modify: `tests/test_wh6_collector_v2_end_to_end.py`

**Interfaces:**
- `UPLOAD_BATCH_SIZE = 100`.
- `LocalOutbox.ack_results(results: Sequence[Mapping[str, str]]) -> dict[str, int]`.
- `LocalOutbox.release(event_keys, error, *, retryable=True)` computes `available_at` from attempts.
- Terminal local statuses: `acked`, `covered_by_monthly`, `conflict`, `quarantined`.

- [ ] **Step 1: Write failing upload lifecycle tests**

```python
def test_client_acks_only_terminal_per_item_results(tmp_path):
    store = seed_three_pending_rows(tmp_path)
    store.ack_results([
        {"event_key": "a", "status": "accepted"},
        {"event_key": "b", "status": "conflict"},
        {"event_key": "c", "status": "quarantined"},
    ])
    assert statuses(store) == {"a": "acked", "b": "conflict", "c": "quarantined"}

def test_retry_delay_caps_at_five_minutes(tmp_path):
    store = seed_pending_with_attempts(tmp_path, attempts=20)
    claimed = store.claim(1)
    store.release([claimed[0]["event_key"]], "timeout")
    assert seconds_until_available(store) == 300
```

Add a test that a 1,000-row historical backlog is sent in 100-row claims while a new realtime row preempts the next history claim.

- [ ] **Step 2: Run uploader/store tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_cli.py -k "ack or retry or batch or realtime"
```

Expected: FAIL because the client claims 500, acknowledges every claimed row after any successful response, and retries immediately.

- [ ] **Step 3: Implement exact result mapping**

Require one receipt per submitted event key. Mark accepted/duplicate as `acked`, settlement-covered as `covered_by_monthly`, and conflict/quarantined as their terminal states. If a response omits or duplicates a key, release the unresolved keys and record `invalid_server_receipt`.

- [ ] **Step 4: Implement bounded retry**

Use `min(300, 5 * 2 ** min(max(attempts - 1, 0), 6))` seconds. HTTP 5xx and network uncertainty are retryable. HTTP 401/403 set client state `device_authorization_required` and leave rows pending without a tight loop.

- [ ] **Step 5: Set request behavior**

Send at most 100 combined fill/snapshot items. Use a connect/read timeout tuple `(5, 30)` so the server can return per-item receipts without an 8-second premature retry. Do not run two concurrent drains against one SQLite outbox.

- [ ] **Step 6: Run the local end-to-end gate**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_v2_end_to_end.py
```

Expected: no row is lost or endlessly retried; realtime preempts history; partial responses are safe.

- [ ] **Step 7: Commit the uploader slice**

```bash
git add collector/wh6_collector/local_store.py collector/wh6_collector/uploader.py collector/wh6_collector/cli.py tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_v2_end_to_end.py
git commit -m "fix: bound and acknowledge WH6 uploads safely"
```

### Task 13: Build a true direct-overlay Windows installer

**Files:**
- Modify: `collector/launcher.py`
- Modify: `collector/wh6_collector/setup_ui.py`
- Modify: `collector/installer/WH6成交采集器.iss`
- Modify: `collector/installer/build_windows.ps1`
- Modify: `collector/installer/README.md`
- Modify: `.github/workflows/build-wh6-windows.yml`
- Modify: `tests/test_wh6_installer.py`
- Modify: `tests/test_wh6_setup_ui.py`

**Interfaces:**
- App version is 0.2.1 everywhere.
- Windows mutex name: `Local\LTM-WH6-Collector-B7C23B59`.
- Release output: `collector/releases/0.2.1/WH6成交采集器-0.2.1-Setup.exe` and matching `.sha256`.
- Existing valid config bypasses the first-run setup UI.

- [ ] **Step 1: Write failing overlay-install contract tests**

```python
def test_installer_is_versioned_and_closes_only_collector():
    iss = installer_text("WH6成交采集器.iss")
    assert '#define MyAppVersion "0.2.1"' in iss
    assert "CloseApplications=force" in iss
    assert "RestartApplications=no" in iss
    assert "CloseApplicationsFilter=WH6成交采集器.exe" in iss
    assert "AppMutex=" not in iss
    assert "WH6.exe" not in iss

def test_release_name_contains_version_and_target_needs_no_python():
    script = installer_text("build_windows.ps1")
    assert "WH6成交采集器-0.2.1-Setup.exe" in script
    assert "collector\\releases\\0.2.1" in script
    assert "目标电脑无需安装 Python" in installer_readme()
```

Also assert the workflow supports `workflow_dispatch`, builds the current repair branch when manually selected, and uploads only the versioned Setup/hash/instructions.

- [ ] **Step 2: Run installer tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_installer.py tests/test_wh6_setup_ui.py
```

Expected: FAIL because installer version is 0.2.0, output is unversioned and no explicit single-instance contract exists.

- [ ] **Step 3: Add the Windows single-instance mutex**

At launcher startup create the named mutex through `ctypes.windll.kernel32.CreateMutexW`. If `GetLastError()` is 183, exit cleanly before opening SQLite. Keep the mutex handle alive for the process lifetime.

- [ ] **Step 4: Configure Inno Setup direct overlay**

Retain the existing AppId and install directory. Set `CloseApplications=force`, `RestartApplications=no`, and `CloseApplicationsFilter=WH6成交采集器.exe` so Windows Restart Manager closes only the process using the collector executable during replacement. Do not set Inno Setup `AppMutex`: it would block Setup before the automatic close phase. The runtime mutex from Step 3 remains the single-instance control after launch. Preserve the application-data directory and run the new executable after install. Never target the WH6 process.

- [ ] **Step 5: Synchronize version and artifact naming**

Read 0.2.1 from the version source during build and pass it into the Inno compiler. Place only the Setup, hash and installation instructions under `collector/releases/0.2.1/`. Keep all binaries ignored by Git.

- [ ] **Step 6: Update CI trigger and artifact**

Keep `workflow_dispatch` as the normal path. Remove the obsolete single old feature-branch push trigger or replace it with this repair branch only while the branch is active. The artifact name must include `0.2.1` and retention remains seven days.

- [ ] **Step 7: Run installer/static safety tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_wh6_installer.py tests/test_wh6_setup_ui.py tests/test_wh6_collector_cli.py
rg -n "ltm-web-gt13|service_role|DATABASE_URL|下单|撤单|改单" collector .github/workflows/build-wh6-windows.yml
git diff --check
```

Expected: tests pass; target installer has no Production URL, credentials or trading controls; README clearly separates build dependencies from target dependencies.

- [ ] **Step 8: Commit the installer slice**

```bash
git add collector/launcher.py collector/wh6_collector/setup_ui.py collector/installer/WH6成交采集器.iss collector/installer/build_windows.ps1 collector/installer/README.md .github/workflows/build-wh6-windows.yml tests/test_wh6_installer.py tests/test_wh6_setup_ui.py
git commit -m "fix: support direct WH6 V2.1 overlay upgrades"
```

### Task 14: Add dry-run/apply data reconciliation command

**Files:**
- Create: `scripts/reconcile_wh6_intraday.py`
- Create: `tests/test_reconcile_wh6_intraday_script.py`
- Modify: `docs/superpowers/plans/2026-09-03-wh6-intraday-fills-positions-collector-acceptance-runbook.md`
- Modify: `README.md`

**Interfaces:**
- Command: `python scripts/reconcile_wh6_intraday.py --environment staging --account-code hongyuan_futures` performs dry-run.
- Write mode additionally requires `--apply`.
- Production mode additionally requires `--production-confirmation <receipt>` and is not executed under this plan without Gate B.
- Output contains environment, database fingerprint without secrets, active monthly ranges, scanned, matched daily/monthly, corrected, covered, conflict, unchanged and rollback anchor.

- [ ] **Step 1: Write failing command safety tests**

```python
def test_reconcile_command_defaults_to_dry_run(tmp_path, monkeypatch):
    before = database_digest()
    result = run_command(["--environment", "staging", "--account-code", "hongyuan_futures"])
    assert result["mode"] == "dry-run"
    assert database_digest() == before

def test_production_apply_requires_explicit_receipt():
    result = run_command(["--environment", "production", "--account-code", "hongyuan_futures", "--apply"])
    assert result.exit_code == 2
```

Add apply idempotency and rollback-on-error tests.

- [ ] **Step 2: Run command tests and confirm failure**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_reconcile_wh6_intraday_script.py
```

Expected: FAIL because the command does not exist.

- [ ] **Step 3: Implement dry-run with the same service functions**

Do not duplicate matching logic in the script. In dry-run, open a transaction, call the reconciliation range function, collect counts, and roll back. In apply, commit only after all counts and invariants pass.

- [ ] **Step 4: Add invariants**

Reject apply when environment mapping is missing, monthly ranges are malformed, one intraday row gets multiple current reconciliation rows, or matched+corrected+conflict+unchanged does not equal scanned. Never print database URLs or tokens.

- [ ] **Step 5: Run command and reconciliation tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_reconcile_wh6_intraday_script.py tests/test_trading_collector_reconciliation.py
```

Expected: dry-run has zero persistent writes; apply is idempotent; production guard is enforced.

- [ ] **Step 6: Commit the operational tool**

```bash
git add scripts/reconcile_wh6_intraday.py tests/test_reconcile_wh6_intraday_script.py README.md docs/superpowers/plans/2026-09-03-wh6-intraday-fills-positions-collector-acceptance-runbook.md
git commit -m "feat: add auditable WH6 reconciliation runbook"
```

### Task 15: Run the complete local quality gate and self-review

**Files:**
- Modify only files exposed by failing tests within the approved scope.
- Modify: `README.md` if setup/structure changed.
- Do not modify: `版本更新记录.md` before real Staging deployment.

**Interfaces:**
- Produces: one local candidate with all target tests green and no claim of Windows/Staging/Production completion.

- [ ] **Step 1: Run all affected Python tests**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q tests/test_trading_settlement.py tests/test_trading_management.py tests/test_trading_collector_reconciliation.py tests/test_trading_collector_service.py tests/test_trading_collector_positions_service.py tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py tests/test_wh6_collector_core.py tests/test_wh6_spread_parser.py tests/test_wh6_position_parser.py tests/test_wh6_collector_store.py tests/test_wh6_collector_migrations.py tests/test_wh6_collector_policy.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_cli.py tests/test_wh6_setup_ui.py tests/test_wh6_installer.py tests/test_wh6_collector_end_to_end.py tests/test_wh6_collector_v2_end_to_end.py tests/test_reconcile_wh6_intraday_script.py
```

Expected: zero failures.

- [ ] **Step 2: Run affected frontend and syntax tests**

```bash
node --test tests/trading_collector_frontend.test.mjs tests/trading_management_frontend.test.mjs tests/trading_overview_frontend_behavior.test.mjs
node --check frontend/trading_collector.js
env -u DATABASE_URL .venv/bin/python -m compileall -q backend/app collector scripts
git diff --check
```

Expected: zero failures and no whitespace errors.

- [ ] **Step 3: Run the full repository regression**

```bash
env -u DATABASE_URL .venv/bin/python -m pytest -q
node --test tests/*.test.mjs
```

Expected: full suite passes. A pre-existing unrelated failure must be reproduced on untouched `origin/staging` before it is reported as baseline rather than silently ignored.

- [ ] **Step 4: Perform the read-only safety scan**

```bash
git diff --name-only origin/staging...HEAD
rg -n "service_role|DATABASE_URL|ltm-web-gt13|place_order|cancel_order|下单|撤单|改单|平仓" collector backend/app/trading_collector.py backend/app/trading_collector_service.py backend/app/trading_collector_reconciliation.py
```

Expected: only approved files changed; no client secret, Production target or transaction control exists.

- [ ] **Step 5: Review plan/spec coverage before Staging**

Verify every state, API field and enum in Tasks 2–14 matches the spec exactly. Record local counts separately from live environment evidence.

- [ ] **Step 6: Commit local integration repairs**

```bash
git add -A
git commit -m "test: verify WH6 settlement-governed collector repair"
```

Expected: commit contains only repairs discovered by the approved local gate.

### Task 16: Back up, migrate and deploy Staging server first

**Files:**
- Apply: `supabase/migrations/20260904_wh6_settlement_reconciliation.sql` to `LTM WEB STAGING` only.
- Modify after successful deployment: `版本更新记录.md`.

**Interfaces and gates:**
- Staging URL: `https://ltm-web-staging.onrender.com`.
- Staging branch: `staging`.
- Production remains untouched.

- [ ] **Step 1: Resolve and record Staging environment identity**

Verify branch mapping, Render service and Supabase project ID against `开发流程_备忘.md`. Stop if any live environment value disagrees.

- [ ] **Step 2: Create a verified Staging database backup**

Follow `docs/backup_restore.md`. Verify the dump is nonempty, listable by `pg_restore --list`, record its hash and store it outside the repository. A failed or empty dump is not a backup.

- [ ] **Step 3: Read active settlement coverage before migration**

Run a read-only query for the bound account showing batch ID, status, statement type, range start/end and confirmed time. Confirm separately whether June, July and August monthly batches are truly `active`. File existence is not accepted as evidence.

- [ ] **Step 4: Apply the schema migration and verify permissions**

Apply only the 20260904 migration to Staging. Verify new columns/table/indexes, RLS enabled, and no direct `anon/authenticated` grants.

- [ ] **Step 5: Push the candidate to Staging**

Merge or fast-forward the reviewed feature branch into `staging`, push `staging`, and record the resulting commit. Do not merge to `main`.

- [ ] **Step 6: Verify server capability before any client upgrade**

Use an authorized test device token to confirm policy schema 1, minimum client 0.2.1, expected active monthly ranges, per-item ingest receipts, `/positions/current`, `/option-volume`, and paginated `/fills`. A missing policy or V2 route blocks installer rollout.

- [ ] **Step 7: Run reconciliation dry-run**

```bash
.venv/bin/python scripts/reconcile_wh6_intraday.py --environment staging --account-code hongyuan_futures
```

Expected: dry-run reports actual counts and zero persistent changes. Compare the current source snapshot to the design evidence: 6—8 ordinary matches were previously 2,720/2,720 and queued options 2,297, but use newly read counts if they differ.

- [ ] **Step 8: Apply Staging reconciliation only after invariants pass**

```bash
.venv/bin/python scripts/reconcile_wh6_intraday.py --environment staging --account-code hongyuan_futures --apply
```

Expected: transaction commits once; rerunning dry-run reports no further changes; monthly-covered rows leave default provisional results; conflicts remain visible as exceptions.

- [ ] **Step 9: Verify the real Staging page in a clean browser tab**

Open `https://ltm-web-staging.onrender.com/?codex=<staging-commit>` in the in-app browser. Confirm title, `trading_collector.js?v=20260904v21`, console health, default 20 rows, 20/50/100, next/previous, correct total pages, option-volume independence from page size, device version and monthly coverage status.

- [ ] **Step 10: Update release record with obtained evidence only**

Record Staging commit/deploy, backup location/hash, schema impact, active monthly ranges, reconciliation counts, browser evidence and rollback point. Do not claim Windows installation or Production completion here.

### Task 17: Build and manually overlay-install Windows V2.1

**Files:**
- Generated outside Git: `collector/releases/0.2.1/WH6成交采集器-0.2.1-Setup.exe`.
- Generated outside Git: matching SHA-256 and installation instructions.
- Update after acceptance: `docs/superpowers/plans/2026-09-03-wh6-intraday-fills-positions-collector-acceptance-runbook.md`.

**Interfaces and gates:**
- Staging server Task 16 must be complete first.
- Installation is manual double-click by the user or authorized local operation; no Python on the target VM.

- [ ] **Step 1: Run the Windows build workflow**

Trigger `.github/workflows/build-wh6-windows.yml` manually on the reviewed Staging-ready commit. Download the 0.2.1 artifact and verify its SHA-256 against the included file.

- [ ] **Step 2: Prepare one clean delivery folder**

Place only the 0.2.1 Setup, checksum and installation instructions in the agreed new folder. After the new hash is verified, remove obsolete 1.0 packages from the active delivery folder as previously requested; do not delete `%LOCALAPPDATA%\WH6成交采集器`.

- [ ] **Step 3: Capture pre-upgrade state**

Record current collector process count, installed version, config existence, token decryptability, SQLite row counts by status, checkpoint count and backup directory. Do not display the token or full account number.

- [ ] **Step 4: Run the Setup once**

Confirm the installer closes only `WH6成交采集器.exe`, overwrites the same installation, starts one new process and does not touch WH6. Do not launch a second installer or manually copy another EXE during this run.

- [ ] **Step 5: Verify zero-friction migration**

Confirm no Python installer, no path selection and no one-time code prompt appears. Verify 0.2.1 runtime version, retained config/token/path, valid local backup, schema version 3 and no duplicate process.

- [ ] **Step 6: Verify queue migration**

With active June—August monthly ranges, confirm covered historical rows are terminal locally, V1/V2 aliases are merged, no stale claimed rows remain and September/current uncovered data is prioritized. Recompute counts from the VM rather than forcing the planning snapshot.

- [ ] **Step 7: Verify one natural read-only data path**

Observe an already naturally occurring new fill or harmless existing current-day cache append. Confirm it appears in the Staging database/page within the V2 10-second target under normal network conditions. No order may be created for testing.

- [ ] **Step 8: Verify spread behavior when an uncovered sample exists**

Confirm one source spread equals one `future_spread` row and zero synthetic legs; if no natural uncovered spread exists, retain the sanitized fixture result and leave real-Windows spread acceptance explicitly open.

- [ ] **Step 9: Record Windows evidence separately**

Update the acceptance runbook with installer hash, VM version, migration counts, process count, page readback and any open natural-fill/spread gates. Do not label fixture replay as a real Windows observation.

### Task 18: Production promotion gate for the same rule

**Files:**
- Modify after an authorized release: `版本更新记录.md`.
- No Production action is authorized by this plan alone.

**Interfaces and gates:**
- Same application code and migration as Staging.
- Production uses only Production `active` monthly batches and its own bound devices.

- [ ] **Step 1: Present Gate B evidence to the user**

Provide Staging tests, real page verification, database backup/rollback, active monthly ranges, data reconciliation counts, Windows installer hash and overlay-install result. Explicitly list unresolved Windows or data conflicts.

- [ ] **Step 2: Wait for explicit Production authorization**

Do not merge `main`, push `main`, alter Production environment variables, migrate Production Supabase, reconcile Production data or connect a Production collector before the user confirms this release.

- [ ] **Step 3: After authorization, back up and dry-run Production**

Create and verify a Production backup, read Production active monthly ranges, apply the schema migration, then run the reconciliation command without `--apply`. If Production monthly coverage differs from Staging, use Production truth and report the difference.

- [ ] **Step 4: Apply and release Production in the approved window**

Use the explicit confirmation receipt required by the command, apply one transaction, merge the reviewed Staging commit to `main`, push and wait for Render Production to become live.

- [ ] **Step 5: Perform read-only Production verification**

Verify `/api/health`, static asset version, authenticated page pagination, policy output for the Production-bound test device, active monthly ranges, reconciliation invariants and no direct table grants. Do not perform any trading or business-edit action.

- [ ] **Step 6: Update the Production release record**

Record exact code commit, deploy identity, backup and rollback anchors, migration and reconciliation counts, page evidence and remaining exceptions. Never record secrets.

---

## Acceptance matrix

| Business requirement | Implementation tasks | Automated evidence | Live evidence |
| --- | --- | --- | --- |
| Only active monthly statements stop history | 3, 6, 10 | policy/gap/ingest tests | Staging policy readback |
| Daily statements never close a month | 3, 5 | daily policy and reconciliation tests | daily-only Staging case |
| Monthly > daily > WH6, no blank overwrite | 4, 5 | golden reconciliation tests | dry-run/apply diff sample |
| Existing 6—8 history stops retrying | 9, 10, 16, 17 | local migration/policy tests | VM queue counts |
| V1/V2 duplicate keys are merged | 9 | six-alias migration fixture | VM before/after counts |
| Correct total despite page size | 7, 8 | 650-row aggregate and UI tests | 20/50/100 page comparison |
| Cross-month spread is one composite | 11 | sanitized real record test | natural Windows sample if available |
| Direct overlay with no Python or re-pairing | 13, 17 | installer/setup contract tests | VM overlay acceptance |
| Staging and Production share rules but not data | 3, 16, 18 | environment guard tests | separate environment readback |
| Original evidence and conflicts remain auditable | 2, 5, 14 | immutable audit/idempotency tests | reconciliation query sample |

## Rollback model

- Code: revert the repair commits or redeploy the last accepted Staging commit.
- Staging schema: new nullable columns/tables remain in place during code rollback; do not destructively drop them.
- Staging data: restore only from the verified pre-migration backup after checking whether newer settlement imports or assignments exist. Ordinary rollback should switch code and leave append-only audit rows.
- Windows program: reinstall the prior verified Setup while preserving `%LOCALAPPDATA%\WH6成交采集器`; restore the automatic local SQLite backup only if migration validation proves corruption.
- Queue coverage: a fresh policy can move unacked `covered_by_monthly` rows back to pending if an active monthly range is legitimately removed.
- Production: no rollback operation occurs without a separate user decision and a verified Production restore point.

## Self-review

- Spec coverage: all goals in sections 1–13 of the design are mapped in the acceptance matrix and Tasks 1–18.
- Scope isolation: the plan begins from latest `origin/staging` and explicitly rejects unrelated files from the stale current branch.
- Type consistency: `CollectionPolicy`, `LocalMigrationResult`, `MatchDecision`, `ResolvedFill`, `ReconciliationSummary`, per-item statuses and API pagination fields have one spelling throughout the plan.
- Source priority: monthly 200, daily 100 and WH6 0 are unchanged across schema, service, client and tests.
- No destructive shortcut: historical rows are transitioned to terminal/audit states; settlement and WH6 source evidence are retained.
- Evidence honesty: local, Staging, Windows and Production gates are separated; no API, package or test result substitutes for a later environment.
- Production boundary: Task 18 explicitly pauses before every Production mutation until Gate B is received.
