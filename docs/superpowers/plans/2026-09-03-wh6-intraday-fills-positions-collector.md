# WH6 Windows 盘中成交与持仓采集器 V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在只读边界内把 WH6 盘中期货/期权成交与完整持仓快照接入本地可靠队列和 Supabase Staging，并让第一阶段只读页面、统计和 API 只暴露期权当日成交量与当前持仓。

**Architecture:** 客户端用显式版本注册表解析 `match.dat` 与经完整性验证的持仓缓存；解析出的期货和期权资料先写独立 SQLite outbox。实时队列和历史队列分离，实时任务以 2 秒漏通知扫描、持仓以 5 秒内容哈希检查，上传前始终让实时任务抢占历史。服务端以设备令牌绑定账户，观察记录与标准成交/快照分表保存，数据库唯一约束和事务完成多设备幂等及冲突记录；读取 API 在服务端筛选期权，拒绝把期货、路径、令牌或凭据暴露到第一阶段页面和 Agent。

**Tech Stack:** Python 3.11, dataclasses, versioned binary/JSON parsing, SQLite WAL, FastAPI/Pydantic, SQLite/PostgreSQL-compatible migrations, Supabase Staging RLS, vanilla JavaScript, Node `node:test`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-03-wh6-intraday-fills-positions-collector-design.md`

## Global Constraints

- 原始采集范围为期货和期权成交、持仓全量采集；第一阶段页面、统计、Agent 和验收只读取期权。
- 只采集已成交记录和完整账户持仓快照；不采集委托、撤单、未成交、资金、最终手续费/盈亏/保证金/Greeks。
- 实时数据高优先级，历史回补低优先级；历史积压不得阻塞实时数据，正常条件下 WH6 完整写入后 10 秒内可在测试库读取。
- 解析成功的成交和完整持仓快照先写本地 SQLite，再发送网络请求；未知结果保持未确认并重试。
- 成交观察和持仓观察逐设备保留；成交按账户/交易日/交易所/成交编号或字段签名+出现序号幂等，持仓快照不得相加。
- 账户变化、身份不明、来源冲突、未知格式和疑似不完整快照必须失败关闭并保留队列。
- 只允许连接 `LTM WEB STAGING`；客户端不得持有数据库密码或 `service_role`；Production、正式 Render、正式 Supabase 和正式设备接入不在本计划内。
- WH6 只读访问，不写入源文件，不控制进程或界面，不注入、不读内存、不抓包、不模拟交易协议，不下单、撤单、改单、平仓、行权或转账。
- 完整账户号、完整 Windows 路径、设备令牌和数据库凭据不得进入普通日志、页面、构建产物或 API 响应。
- 当前 WH6 版本是否提供可靠持仓缓存由阶段 0 的显式格式/完整性测试和 Windows 只读验收决定；没有证据时返回不可用或明确 `reconstructed`，不猜测为实时持仓。

## Traceability and Gate A Package

| Item ID | Business requirement | Implementation | Automation | Real-surface evidence |
| --- | --- | --- | --- | --- |
| AC2-001 | 期货与期权成交被采集并分类 | full-asset match parser, ingest schema | parser and ingest tests | Windows cache replay and Staging rows |
| AC2-002 | 完整期货与期权持仓快照被采集并分类 | position parser, snapshot ingest | complete/empty/truncated tests | Windows WH6 read-only cache and screenshot comparison |
| AC2-005/006 | 新成交/持仓变化 10 秒内进入 Staging | 2s/5s scheduler and immediate upload | scheduler timing-order tests | natural Windows fill/position change and Staging readback |
| AC2-007 | 历史积压不阻塞实时 | separated priority queues and bounded drain | realtime-preempts-history tests | Windows historical replay during natural event |
| AC2-008/009 | 多设备重复去重且独立同内容成交保留 | observation-first canonical ingest | two-device and same-signature occurrence tests | two Windows devices/readback |
| AC2-010 | 快照不相加，持续冲突显示异常 | snapshot identity/conflict state | same/short-diff/persistent-diff tests | two devices and status readback |
| AC2-011 | 断网、超时、重启不丢不重 | WAL outbox and ack/retry | restart/offline/partial-result tests | Windows offline/restart test |
| AC2-012 | 账户切换上传前暂停 | source account comparison | account-switch tests | Windows account switch observation |
| AC2-013 | 不完整/未知格式失败关闭 | explicit layout registry and quarantine | malformed source tests | Windows format/path status |
| AC2-014/015 | 第一阶段只读期权且超过 30 秒显示过期 | option-only query API and page | API/frontend scope and stale tests | page and Agent read path |
| AC2-016/017/018 | 无交易能力、只写 Staging、路径可变 | build guard, RLS, discovery/setup | leak/safety/manifest tests | Windows install and Staging only |

Assessment: D3/T3/R3/C1. 这是一个有明确边界的单一交易采集模块，虽然跨客户端、解析、本地存储、服务端、API 和一个页面，但没有连接两个独立业务模块的新业务结果，因此不升为 D4/T4；R3 来自核心交易数据、数据库/RLS 和真实账户只读边界，C 仍保持单 Agent，不使用子 Agent。

Allowed components: `collector/`, `backend/app/db.py`, `backend/app/trading_collector.py`, `backend/app/trading_collector_service.py`, `backend/app/permissions.py`（仅必要权限同步）、`frontend/index.html`, `frontend/app.js`, `frontend/trading_collector.*`, `supabase/migrations/20260903_wh6_intraday_fills_positions.sql`, targeted tests, `README.md`, and the V2 acceptance runbook/release record.

Unchanged components: settlement facts, `trading_trade_facts`, existing trading-management calculations, order finance, risk rules, Production configuration/data, WH6 source files, and all transaction-control paths.

Rollback point: current baseline `09aa7a5`; code rollback is branch/commit rollback. New Staging tables are isolated `trading_intraday_*` collector tables; rollback preserves them and stops the collector rather than deleting observations or altering settlement tables.

---

### Task 1: Full-asset immutable models and conservative WH6 parsers

**Files:**
- Modify: `collector/wh6_collector/models.py`
- Modify: `collector/wh6_collector/formats.py`
- Modify: `collector/wh6_collector/parser.py`
- Modify: `collector/wh6_collector/discovery.py`
- Modify: `tests/test_wh6_collector_core.py`
- Create: `tests/test_wh6_position_parser.py`

**Interfaces:**
- `class PositionRow`: immutable JSON-safe value object with `contract`, `raw_contract`, `asset_type`, `exchange`, `direction`, `quantity`, optional `today_quantity`, `yesterday_quantity`, `average_price`, `hedge_flag`, option parts, `source_record_index`, and `source_record_sha256`.
- `class PositionSnapshot`: immutable JSON-safe value object with `source_snapshot_key`, account fingerprint, `trade_date`, `snapshot_time`, `snapshot_timestamp`, tuple of `PositionRow`, `complete`, `source_path`, `source_snapshot_sha256`, `parser_version`, `data_status`, `verification_status`, and `to_payload()`.
- `class PositionLayout`: explicit registered layout; `detect_position_layout(data: bytes) -> PositionLayout | None` accepts only the test-proven JSON envelope `{"format":"wh6-position-v1"}` or a header-magic/declared-count registered binary layout, including an explicitly versioned padded variant.
- `classify_contract(contract: str) -> str | None` returns only `future`, `option`, or `None`; `normalize_contract` keeps futures normalized and converts Call/Put option forms to `underlyingexpiry-c/p-strike`.
- `parse_match_records(path, *, account, source_file, asset_types: Sequence[str] | None = None) -> tuple[list[FillRecord], list[ParseIssue]]` parses both asset types by default, while an explicit `asset_types=("option",)` supports the legacy option-only helper.
- `parse_position_snapshot(path, *, account, source_file) -> tuple[PositionSnapshot | None, list[ParseIssue]]` returns a complete snapshot only after account-independent structure, asset type, row fields, count and completion checks pass; unknown or partial sources raise/quarantine without writes.

- [ ] **Step 1: Write failing behavior tests** for future/Call/Put classification, two independent same-signature fill occurrences, full 268/269-byte match parsing, JSON and registered binary position snapshots, multi-contract and empty snapshots, truncated/unknown/incomplete snapshots, and no source-file mutation.

```python
def test_full_match_parser_keeps_future_and_option_records(tmp_path):
    path = tmp_path / "20260903match.dat"
    _write_match(path, [_record(contract="i2607"), _record(contract="i2607-C-750", match_id="OPT")], size=268)
    fills, issues = parse_match_records(path, account=_account(), source_file=_source(path))
    assert [(fill.asset_type, fill.contract) for fill in fills] == [
        ("future", "i2607"), ("option", "i2607-c-750")
    ]
    assert not issues

def test_position_parser_rejects_partial_snapshot_and_accepts_empty_complete_snapshot(tmp_path):
    path = tmp_path / "20260903position.dat"
    write_position_json(path, rows=[], complete=True)
    snapshot, issues = parse_position_snapshot(path, account=_account(), source_file=_position_source(path))
    assert snapshot is not None and snapshot.complete and snapshot.rows == ()
    path.write_bytes(b'{"format":"wh6-position-v1","complete":false,"rows":[]}')
    snapshot, issues = parse_position_snapshot(path, account=_account(), source_file=_position_source(path))
    assert snapshot is None and any(issue.code == "incomplete_position_snapshot" for issue in issues)
```

- [ ] **Step 2: Run the focused tests to verify the requested behavior fails.**

Run: `python3 -m pytest -q tests/test_wh6_collector_core.py tests/test_wh6_position_parser.py`

Expected: FAIL because the current parser drops futures and has no `PositionSnapshot` or complete-position parser.

- [ ] **Step 3: Implement the smallest conservative parser change.** Keep the existing 268/269 and order-enrichment offsets; generalize required-field validation to both registered asset types, retain option fields only when the option classifier proves them, include the trading date/exchange/trade ID in the stable event key, and add the explicit position envelope/layout. A malformed row adds a `ParseIssue` and invalidates the whole snapshot; no unknown record length or missing completion marker is accepted.

- [ ] **Step 4: Run the focused tests and the existing parser tests.**

Run: `python3 -m pytest -q tests/test_wh6_collector_core.py tests/test_wh6_position_parser.py`

Expected: all focused parser tests pass, including the legacy option-only assertion through the explicit filter; no source file hash changes.

- [ ] **Step 5: Commit the parser slice.**

```bash
git add collector/wh6_collector/models.py collector/wh6_collector/formats.py collector/wh6_collector/parser.py collector/wh6_collector/discovery.py tests/test_wh6_collector_core.py tests/test_wh6_position_parser.py
git commit -m "feat: parse full-asset WH6 fills and positions"
```

### Task 2: Durable local store and realtime/history scheduler

**Files:**
- Modify: `collector/wh6_collector/local_store.py`
- Modify: `collector/wh6_collector/monitor.py`
- Modify: `collector/wh6_collector/discovery.py`
- Modify: `collector/wh6_collector/uploader.py`
- Modify: `tests/test_wh6_collector_store.py`
- Create: `tests/test_wh6_collector_scheduler.py`

**Interfaces:**
- `LocalOutbox.put(fill, *, priority="history")`, `put_position(snapshot, *, priority="realtime")`, `put_many(fills, *, priority="history")`, `claim(limit=100, *, priority=None)`, `ack(event_keys)`, `release(event_keys, error)`, `status()`, `save_checkpoint(source_path, checkpoint, *, kind="match")`, and `load_checkpoint(source_path, *, kind="match")` preserve the existing compatibility calls while using one SQLite WAL owned outside WH6.
- `ScanBatch` contains `fills`, optional `position_snapshot`, `issues`, `checkpoint`, `priority`, and `source_kind`; `scan_source` dispatches by `SourceFile.kind`, updates a position checkpoint only for a complete changed snapshot, and never discards queued data on path failure.
- `DualChannelScheduler(realtime_interval=2, position_interval=5, history_interval=10)` exposes `enqueue_realtime(source)`, `enqueue_history(source)`, `next_task()`, and `tick(now)`; `next_task()` always drains realtime before history and records no task as realtime-ready until its interval is due.
- `StagingUploader.send(token, fills, position_snapshots)` posts one restricted payload with `observations` and `position_snapshots`; `__call__` remains a fill-only compatibility adapter.

- [ ] **Step 1: Write failing tests** for position discovery/manual validation, durable position queue entries, checkpoint restart/rotation, realtime claim ahead of 1000 historical entries, 2-second fill scans, 5-second position scans, history starvation prevention, offline release, and queue preservation when the source disappears.

```python
def test_realtime_claim_preempts_history_backlog(tmp_path):
    store = LocalOutbox(tmp_path / "collector.sqlite3")
    store.put(_future_fill("history-1"), priority="history")
    store.put(_future_fill("history-2"), priority="history")
    store.put(_option_fill("realtime-1"), priority="realtime")
    claimed = store.claim(1, priority=None)
    assert claimed[0]["event_key"] == "realtime-1"

def test_position_checkpoint_advances_only_after_complete_snapshot(tmp_path):
    source = _position_source(tmp_path / "20260903position.dat")
    write_position_json(source.path, rows=[position_row("i2607")], complete=True)
    first = scan_source(source, None, account=_account())
    assert first.position_snapshot is not None
    write_incomplete_position(source.path)
    second = scan_source(source, first.checkpoint, account=_account())
    assert second.position_snapshot is None
    assert any(issue.code == "incomplete_position_snapshot" for issue in second.issues)
```

- [ ] **Step 2: Run the focused store/scheduler tests to confirm failure.**

Run: `python3 -m pytest -q tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py`

Expected: FAIL because the outbox has no priority or position payload and the monitor has no dual-channel scheduler.

- [ ] **Step 3: Extend the SQLite schema and implement the scheduler.** Use `item_type` and `priority` columns with a migration-safe `ALTER TABLE` for existing V1 local stores, keep composite logical keys prefixed (`fill:`/`position:`) so a fill and snapshot cannot collide, reclaim stale claims, and order `realtime` before `history`. Discover only explicit `match.dat`, `position.dat`, and registered variants below supplied/known WH6 roots; never scan an entire drive.

- [ ] **Step 4: Run focused tests plus a 1000-item queue smoke test.**

Run: `python3 -m pytest -q tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py && python3 -m pytest -q tests/test_wh6_collector_end_to_end.py`

Expected: queue rows survive reopen, history is bounded behind realtime, and the existing two-device fill path still works with no source writes.

- [ ] **Step 5: Commit the local collector slice.**

```bash
git add collector/wh6_collector/local_store.py collector/wh6_collector/monitor.py collector/wh6_collector/discovery.py collector/wh6_collector/uploader.py tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py
git commit -m "feat: prioritize realtime WH6 collection"
```

### Task 3: Isolated Staging schema and deterministic fill/position ingest

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/trading_collector_service.py`
- Create: `supabase/migrations/20260903_wh6_intraday_fills_positions.sql`
- Modify: `tests/test_trading_collector_service.py`
- Create: `tests/test_trading_collector_positions_service.py`

**Interfaces:**
- `migrate_trading_collector_schema(conn)` creates/retains the V1 pairing/device/fill tables and adds `trading_intraday_position_observations`, `trading_intraday_position_snapshots`, and `trading_intraday_position_rows` in SQLite/PostgreSQL-compatible form; all collector tables are included in RLS/broad-role revocation.
- `ingest_observations(device_token, observations, position_snapshots=()) -> IngestResult` validates both `future` and `option` fills, accepts only the server-bound account, inserts observations before canonical facts, and ingests complete snapshots without adding rows across devices.
- `query_intraday_fills(account_id, *, ..., asset_type=None)` remains a generic read helper; `query_option_volume(account_id, *, trade_date, contract="", limit=500)` returns `total_quantity`, `by_contract`, `by_side`, `by_open_close`, `by_option_kind`, and option-only `items`; `query_current_option_positions(account_id, *, now=None)` returns latest complete option rows, `snapshot_timestamp`, `source_status`, and `is_expired`.
- `IngestResult` adds position accepted/duplicate/conflict/quarantine/observation counts without removing existing fill counters.

- [ ] **Step 1: Write failing service tests** for future plus option acceptance, position schema presence, same snapshot from two devices, snapshot quantity not summed, short-lived and persistent conflicts, expired current snapshot, malformed position quarantine, account spoof ignored/rejected, and option-only volume/position queries.

```python
def test_two_devices_same_complete_snapshot_is_one_snapshot_not_sum(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    first, second = activate(account_id, name="pc-a"), activate(account_id, name="pc-b")
    snapshot = position_payload(quantity=3, snapshot_key="snapshot:2026-09-03T09:05:00+08:00")
    assert service.ingest_observations(first["token"], [], [snapshot]).positions_accepted == 1
    result = service.ingest_observations(second["token"], [], [snapshot])
    assert result.position_duplicates == 1
    current = service.query_current_option_positions(account_id)
    assert current["items"][0]["quantity"] == 3

def test_futures_are_stored_but_option_query_never_returns_them(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    device = activate(account_id)
    service.ingest_observations(device["token"], [future_payload(), option_payload()])
    assert service.query_option_volume(account_id, trade_date="2026-09-03")["total_quantity"] == 1
    assert all(item["asset_type"] == "option" for item in service.query_intraday_fills(account_id)["items"])
```

- [ ] **Step 2: Run the focused service tests and capture the expected failures.**

Run: `python3 -m pytest -q tests/test_trading_collector_service.py tests/test_trading_collector_positions_service.py`

Expected: FAIL because validation only permits options and the three position tables/query services do not exist.

- [ ] **Step 3: Implement the schema and transactional service.** Add strict normalized regex/decimal/date validation for futures and options, use `(account_id, source_event_key)` as the final fill uniqueness boundary, use `(account_id, source_snapshot_key)` for canonical snapshot identity, preserve every device observation, and set `conflict_status` without replacing the first canonical rows. A matching hash within 30 seconds clears a transient difference; an unresolved difference older than 30 seconds returns `multi_device_conflict`. Do not change `trading_trade_facts` or settlement data.

- [ ] **Step 4: Run service tests and inspect the generated schema.**

Run: `python3 -m pytest -q tests/test_trading_collector_service.py tests/test_trading_collector_positions_service.py`

Expected: all service tests pass; `trading_trade_facts` remains present and untouched; the new tables contain foreign keys, uniqueness, account/device indexes, and no plaintext token.

- [ ] **Step 5: Commit the Staging ingest slice.**

```bash
git add backend/app/db.py backend/app/trading_collector_service.py supabase/migrations/20260903_wh6_intraday_fills_positions.sql tests/test_trading_collector_service.py tests/test_trading_collector_positions_service.py
git commit -m "feat: ingest WH6 fills and position snapshots"
```

### Task 4: FastAPI read-only option results and device routes

**Files:**
- Modify: `backend/app/trading_collector.py`
- Modify: `tests/test_trading_collector_api.py`
- Create: `tests/test_trading_collector_positions_api.py`

**Interfaces:**
- Existing admin/device routes remain under `/api/trading-collector/admin/*` and `/api/trading-collector/device/*`; `/device/ingest` accepts `observations` plus `position_snapshots` and never accepts a client account override.
- `GET /api/trading-collector/fills` returns only option provisional fills and retains existing account/date/contract filters.
- `GET /api/trading-collector/option-volume` returns the option-only daily volume contract from Task 3.
- `GET /api/trading-collector/positions/current` returns option-only current snapshot rows and explicit `snapshot_timestamp`, `source_status`, `is_expired`, and stale/conflict messages.
- All read routes require the existing authenticated `trading.options:view`; guest and unauthenticated callers are rejected; only the bound `hongyuan_futures` account is available in this first phase.

- [ ] **Step 1: Write failing API tests** for position ingest without browser session, option-volume aggregation, current-position filtering, future invisibility, stale/conflict response, request-size limits, admin permission enforcement, revoked device rejection, and guest rejection.

```python
def test_option_volume_and_current_positions_are_option_only(client):
    token = activate_device_via_api(client)
    client.post("/api/trading-collector/device/ingest", headers={"X-Collector-Token": token}, json={
        "observations": [option_payload(), future_payload()],
        "position_snapshots": [position_payload(quantity=2)],
    })
    volume = client.get("/api/trading-collector/option-volume", headers=auth_headers()).json()
    positions = client.get("/api/trading-collector/positions/current", headers=auth_headers()).json()
    assert volume["total_quantity"] == 1
    assert all(item["asset_type"] == "option" for item in positions["items"])
    assert all("i2607" not in item["contract"] or "-" in item["contract"] for item in positions["items"])
```

- [ ] **Step 2: Run focused API tests and confirm failure.**

Run: `python3 -m pytest -q tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py`

Expected: FAIL because the new payload field and two read routes are not registered.

- [ ] **Step 3: Implement route models/dependencies and redaction.** Reuse the local bearer-session lookup; use `X-Collector-Token` only for device routes; call the service account-bound functions; do not add WH6 control endpoints, full path fields, or Agent tools.

- [ ] **Step 4: Run focused API and existing permission/auth tests.**

Run: `python3 -m pytest -q tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py tests/test_permissions.py tests/test_auth.py`

Expected: all targeted collector/auth tests pass and existing guest/retired-module permission behavior remains unchanged.

- [ ] **Step 5: Commit the API slice.**

```bash
git add backend/app/trading_collector.py tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py
git commit -m "feat: expose option intraday read paths"
```

### Task 5: First-phase option volume and current-position page

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/trading_collector.js`
- Modify: `frontend/trading_collector.css`
- Modify: `frontend/app.js`
- Modify: `tests/trading_collector_frontend.test.mjs`

**Interfaces:**
- `window.TradingCollector.activate({canManage})` loads devices, option volume and current option positions; it renders only masked account labels, option fills, volume summaries, snapshot time/source state, and stale/conflict messages.
- `renderOptionVolume(data)` renders total option hands and contract/side/open-close/Call-Put summaries; it never consumes a future item.
- `renderCurrentPositions(data)` renders option contract, direction, quantity, reliable optional average price, snapshot time and source status; it does not sum device rows and visibly labels stale/conflict/unavailable states.

- [ ] **Step 1: Write failing DOM/source tests** for the volume and position sections, new API calls, future exclusion in rendered output, stale/conflict labels, masked account/path/credential rules, and admin-only pairing/revoke controls.

```javascript
test("collector page renders only option volume and current position states", () => {
  assert.match(html, /collectorOptionVolume/);
  assert.match(html, /collectorCurrentPositions/);
  assert.match(collectorJs, /api\\/trading-collector\\/option-volume/);
  assert.match(collectorJs, /api\\/trading-collector\\/positions\\/current/);
  assert.match(collectorJs, /持仓数据可能已过期/);
  assert.match(collectorJs, /多设备持仓不一致/);
  assert.doesNotMatch(collectorJs, /token_hash|service_role|DATABASE_URL|C:\\\\\\\\Users/);
});
```

- [ ] **Step 2: Run the focused frontend test to confirm failure.**

Run: `node --test tests/trading_collector_frontend.test.mjs`

Expected: FAIL because the page has only device and fill sections.

- [ ] **Step 3: Add the read-only sections and render state.** Keep pairing and revoke as the only administrative actions; do not add refresh controls that write to the collector or any trading control.

- [ ] **Step 4: Run focused and existing frontend tests.**

Run: `node --test tests/trading_collector_frontend.test.mjs tests/trading_management_frontend.test.mjs tests/trading_overview_frontend_behavior.test.mjs`

Expected: all frontend tests pass and JavaScript syntax is valid.

- [ ] **Step 5: Commit the page slice.**

```bash
git add frontend/index.html frontend/trading_collector.js frontend/trading_collector.css frontend/app.js tests/trading_collector_frontend.test.mjs
git commit -m "feat: show option intraday volume and positions"
```

### Task 6: Windows V2 launcher, upload loop, and documentation guards

**Files:**
- Modify: `collector/wh6_collector/cli.py`
- Modify: `collector/wh6_collector/setup_ui.py`
- Modify: `collector/wh6_collector/credential_store.py` only if the V2 config requires a new protected field
- Modify: `collector/launcher.py`
- Modify: `collector/WH6成交采集器.spec`
- Modify: `collector/installer/README.md`
- Modify: `collector/installer/WH6成交采集器.iss`
- Modify: `collector/installer/build_windows.ps1`
- Modify: `collector/installer/build_windows.cmd` only if the V2 executable name/entry needs synchronization
- Modify: `README.md`
- Create: `docs/superpowers/plans/2026-09-03-wh6-intraday-fills-positions-collector-acceptance-runbook.md`
- Modify: `tests/test_wh6_collector_cli.py`
- Modify: `tests/test_wh6_installer.py`

**Interfaces:**
- `run_once(config, *, upload=None)` scans full-asset sources, queues realtime current-date fills/complete positions before history, drains realtime before bounded history, and returns separate fill/position counters and explicit `account_pending`, `account_changed`, `offline_queue`, `path_unavailable`, `format_unknown`, `position_stale`, or `normal` states.
- `run_service(config, stop_event=None)` uses a 2-second safety scan cadence, a 5-second position cadence, and retains the 10-second end-to-end target; it never exits after successful first setup.
- `CollectorConfig` keeps the Staging URL guard, DPAPI token protection on Windows, non-Windows local-test fallback, and `%LOCALAPPDATA%\\WH6成交采集器` data directory. The configuration stores no stable account ID in plaintext.
- The V2 runbook states that Mac tests/cache replay and package manifest checks are not Windows/WH6/Staging acceptance; real Windows cache, natural fill, Staging readback and entity migration remain explicit gates.

- [ ] **Step 1: Write failing CLI/manifest tests** for future+position queueing, realtime-before-history upload order, position payload upload, 2/5 second scheduler values, Staging-only URL guard, offline queue preservation, no-argument setup-to-service lifecycle, and absence of Production URL/credentials/trading verbs in client/builder content.

```python
def test_once_uploads_realtime_position_before_historical_fill(tmp_path):
    config = config_for_sources(tmp_path)
    uploaded = []
    result = run_once(config, upload=lambda token, items, positions=(): uploaded.append((items, positions)) or {
        "accepted": len(items), "positions_accepted": len(positions)
    })
    assert uploaded[0][1] and uploaded[0][1][0]["asset_type"] in {"future", "option"}
    assert result["state"] == "normal"
```

- [ ] **Step 2: Run CLI and installer tests to verify the V2 behavior fails.**

Run: `python3 -m pytest -q tests/test_wh6_collector_cli.py tests/test_wh6_installer.py`

Expected: FAIL because `run_once` currently queues option fills only and the service loop has no position cadence.

- [ ] **Step 3: Implement the thin V2 CLI changes.** Preserve account-switch pause and old-account queue draining, pass both lists to the uploader, use only Staging/local URLs, and update installer/setup text from option-only to full asset collection with option-only first-phase display.

- [ ] **Step 4: Run collector CLI, installer, compile, and leak checks.**

Run: `python3 -m pytest -q tests/test_wh6_collector_cli.py tests/test_wh6_installer.py tests/test_wh6_setup_ui.py && python3 -m compileall -q collector backend/app && node --check frontend/trading_collector.js && git diff --check`

Expected: all targeted tests pass; no Production URL, database credential, service-role token, full account number, or trading-control path is in the checked-in bundle.

- [ ] **Step 5: Commit the Windows/documentation slice.**

```bash
git add collector/wh6_collector/cli.py collector/wh6_collector/setup_ui.py collector/launcher.py collector/WH6成交采集器.spec collector/installer README.md docs/superpowers/plans/2026-09-03-wh6-intraday-fills-positions-collector-acceptance-runbook.md tests/test_wh6_collector_cli.py tests/test_wh6_installer.py
git commit -m "feat: prepare WH6 V2 Windows collector"
```

### Task 7: Integrated regression, evidence, and Staging readiness

**Files:**
- Modify: `tests/test_wh6_collector_end_to_end.py`
- Create: `tests/test_wh6_collector_v2_end_to_end.py`
- Modify: `docs/superpowers/plans/2026-09-03-wh6-intraday-fills-positions-collector-acceptance-runbook.md`
- Modify: `版本更新记录.md` only after a real Staging deployment/version readback is verified

**Interfaces and acceptance:**
- The local vertical slice writes future and option fills plus full snapshots to two local outboxes, ingests both device observations into SQLite service tables, and verifies one standard fill/snapshot with preserved observations.
- Golden cases cover historical backlog, new option/future fill, complete/empty/partial snapshot, two-device dedup, same-signature independent rows, short/persistent snapshot conflict, offline/restart/account switch, option-only API, and >30-second stale status.
- All required local evidence is recorded with command, count, failure count, and environment; local evidence is labeled separately from Windows 11, Staging, and Production.

- [ ] **Step 1: Write the failing integrated test** that creates two devices, a historical backlog, a current future/option fill batch, same-time complete snapshots, a conflict, an offline release/restart, and asserts API option-only results and no settlement-table writes.

- [ ] **Step 2: Run the integrated test to verify it fails at the missing V2 path.**

Run: `python3 -m pytest -q tests/test_wh6_collector_v2_end_to_end.py`

Expected: FAIL before the new local-to-service position path is wired.

- [ ] **Step 3: Implement only integration repairs exposed by the test.** Do not broaden into settlement reconciliation, business assignment, Production deployment, real credentials, or transaction controls.

- [ ] **Step 4: Run the consolidated local quality gate.**

Run: `python3 -m pytest -q tests/test_wh6_collector_core.py tests/test_wh6_position_parser.py tests/test_wh6_collector_store.py tests/test_wh6_collector_scheduler.py tests/test_wh6_collector_cli.py tests/test_wh6_collector_end_to_end.py tests/test_wh6_collector_v2_end_to_end.py tests/test_trading_collector_service.py tests/test_trading_collector_positions_service.py tests/test_trading_collector_api.py tests/test_trading_collector_positions_api.py && node --test tests/trading_collector_frontend.test.mjs && python3 -m compileall -q collector backend/app && git diff --check`

Expected: all affected tests pass. Any unrelated baseline failure is listed by test name and not relabeled as a V2 result.

- [ ] **Step 5: Run a read-only safety review.** Verify with `rg` that only approved files changed, WH6 paths are opened with read APIs, no transaction-control call or GUI automation was added, API responses contain no full path/account/token, and the Supabase migration targets only `LTM WEB STAGING` collector objects.

- [ ] **Step 6: Update the runbook and release record only for evidence actually obtained.** The Mac worktree cannot claim Windows installer build, natural-fill latency, user screenshot comparison, entity Windows migration, or Staging data readback without those artifacts. Do not write a staging release entry merely because local tests pass.

- [ ] **Step 7: Before completion, re-read this plan and report Gate B state.** Include change summary, files, tests, database impact, rollback, current branch/commit, and separately list open Windows/Staging/Production gates. Production remains untouched and requires separate user confirmation.

## Spec coverage self-review

- Sections 2–4 are covered by the full-asset model/parser, dual-channel scheduler, isolated schema, option-only read surface, installer guard, and explicit non-goal tests.
- Sections 5 and 7 are covered by provisional/confirmed status fields, account binding, source hash/path redaction, and fail-closed account/format handling; settlement confirmation remains a source relationship only, not an automatic promotion.
- Sections 8–11 are covered by 2/5-second scheduling, priority queues, full snapshot identity, WAL outbox, retries, and no source-file writes.
- Sections 12–14 are covered by the three position tables, RLS/revocation, device-bound ingest, option-only API/page, and stale/conflict status.
- Sections 15–18 are covered by latency timestamps, targeted tests, the V2 runbook, and the explicit distinction between local replay and real Windows/Staging evidence.
- Sections 19–22 are respected by stopping at Staging readiness when real Windows/Staging evidence is unavailable and by keeping Production outside the branch.

Placeholder scan: no step depends on an unnamed function, an unbounded “handle edge cases” instruction, or a Production action. Type consistency: `PositionSnapshot`/`PositionRow`, `ingest_observations(..., position_snapshots=())`, `query_option_volume`, `query_current_option_positions`, and `DualChannelScheduler` are defined before their consuming tasks.

