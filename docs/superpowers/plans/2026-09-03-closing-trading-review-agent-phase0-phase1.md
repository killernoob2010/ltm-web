# 收盘交易复盘 Agent Phase 0 + Phase 1 Implementation Plan

> **历史状态（2026-09-03）：** 本计划对应的确定性只读底座已实现并部署到 Staging。后续网页 Agent、DeepSeek、会话、权限和自动结果不得继续从本计划扩展，应以 `docs/superpowers/specs/2026-09-03-closing-trading-review-agent-staging-requirements.md` 及其配套实施计划为准。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有交易管理事实层上交付一个只读、无模型的宏源账户铁矿石期权指定交易日复盘服务和 FastAPI 合同。

**Architecture:** 新增一个聚焦的 `closing_trading_review` 模块，读取现有交易批次、来源、期权持仓、平仓事实和合约规格，执行权限后的确定性校验、分组、换算和盈亏计算。模块提供强类型请求/结果 Schema 和只读 router；不新增表、不解析原始文件、不调用行情/交易接口、不接 Prompt、Harness 或模型。

**Tech Stack:** Python 3.10+、FastAPI、Pydantic、现有 SQLite/PostgreSQL `db` 兼容层、pytest、FastAPI `TestClient`。

**Spec:** `/Users/wangjingze/Documents/轻量化交易管理系统WEB/docs/superpowers/specs/2026-09-03-closing-trading-review-agent-v1-design.md`

## Global Constraints

- 账户固定为现有 canonical account `hongyuan_futures`，API 不接受任意账户 ID 或账户号。
- 品种固定为大商所铁矿石期权，合约格式使用真实数据中的 `iYYYY-C/P-strike` 规范化形式；月份、Call/Put、行权价和乘数均来自事实/受控规格。
- 真实平仓盈亏只汇总 `trading_close_facts.fact_close_pnl`，不扣手续费；持仓浮盈浮亏单独计算和返回，不能合成一个总盈亏。
- Phase 1 估值口径固定为 `daily_settlement`，必须明确不代表未来 15:00 最后一笔有效成交价口径。
- `complete`、`partial`、`waiting_for_data`、`data_anomaly` 是确定性报告状态；缺数或冲突不得生成确定性结论，越权请求在取数前返回 403。
- 所有关键数值都通过 `NumericFact` 携带 `data_as_of`、`source`、`calculation_version`、`rule_version`、`freshness`、`completeness`、`warnings` 和 `evidence_refs`。
- 只使用合成/脱敏 fixture；不把真实账户标识、结算单原文、密钥、生产连接或业务敏感数据写入新测试、文档或日志。
- 不新增数据库迁移、后台调度、网页/企业微信入口、模型网关、Harness、Greeks/IV/Delta、交易执行或任何生产变更。

## Gate A assessment and traceability

- **D3:** 新能力位于单一交易管理业务模块内，包含其确定性计算、API 和测试；没有跨独立业务模块或系统级架构边界。
- **T3:** 需要验证现有数据库事实 → 新计算服务 → FastAPI 只读接口的直接链路；不做真实 UI 或跨业务流程验收。
- **R3:** 涉及真实平仓盈亏、持仓浮盈浮亏和权限边界，按核心业务/认证语义评估；本轮通过只读、无迁移、无生产边界降低操作风险。
- **C1:** 一个主 Agent 在当前隔离 worktree 内完成，不创建子 Agent。

Traceability IDs:

- `CR-001`: 指定日期、固定宏源账户和铁矿石期权范围，以及完整/部分/等待数据/数据异常状态。
- `CR-002`: 动态月份、Call/Put、买卖方向、行权价区间分组，净卖/净买手数与吨数换算。
- `CR-003`: 不含手续费真实平仓盈亏、日终结算口径持仓浮盈浮亏，两者独立返回并可勾稽。
- `CR-004`: 按真实合约组生成事实层平仓盈亏贡献和受限贡献比例。
- `CR-005`: 强类型 Schema、来源/版本/新鲜度/完整性/警告/证据元数据、权限先于取数的 API。
- `CR-006`: 合成数据覆盖多月份、买入抵减、净买、零持仓、缺日结、月结缺估值、冲突、证据缺失和越权。

Unchanged areas: existing settlement parser/import, existing valuation provider, trading database schema, business allocation writes, frontend, scheduler, WeCom, model/Harness, production environments and real trading accounts.

## Task 1: Define the deterministic report contract and service

**Files:**
- Create: `backend/app/closing_trading_review.py`
- Reference: `backend/app/trading_management.py`, `backend/app/trading_settlement.py`, `backend/app/trading_valuation.py`, `backend/app/permissions.py`

**Interfaces:**
- Consumes: `trading_accounts`, active `trading_import_batches`, `trading_source_rows`, `trading_fact_identities`, `trading_close_facts`, `trading_position_snapshots`, `trading_contract_specs`, `trading_fact_source_differences` through the existing `db.connect()` compatibility layer.
- Produces: `OptionReviewRequest`, `OptionDailyReviewResponse`, `NumericFact`, `EvidenceMetadata`, `EvidenceRef`, `build_option_daily_review(trading_date: str)`, and router `GET /api/closing-trading-review/options/daily-summary?trading_date=YYYYMMDD`.

- [ ] **Step 1: Write the failing service tests**

  Add synthetic SQLite helpers that seed only canonical account aliases, fake batch/source rows, option facts and the existing controlled multiplier. Assert that a complete report exposes structured values, dynamic groups, independent realized/unrealized P&L, settlement basis, and evidence metadata.

  ```python
  def test_complete_report_groups_dynamic_option_positions_and_separates_pnl(tmp_path, monkeypatch):
      use_temp_db(tmp_path, monkeypatch)
      seed_daily_option_facts(
          date="20260529",
          positions=[
              ("i2609-c-700", "卖", 3, 8, 5),
              ("i2610-p-650", "买", 1, 6, 7),
          ],
          closes=[("i2609-c-700", "卖", 2, 8, 5, 600)],
      )

      report = build_option_daily_review("2026-05-29")

      assert report.status == "complete"
      assert report.valuation_basis == "daily_settlement"
      assert report.realized_close_pnl.value == 600
      assert report.unrealized_pnl.value == -300
      assert {(item.expiry_month, item.option_type, item.direction)
              for item in report.position_groups} == {
          ("2609", "Call", "卖"), ("2610", "Put", "买")
      }
      assert report.realized_close_pnl.metadata.evidence_refs
      assert report.unrealized_pnl.metadata.source == "trading_position_snapshots"
  ```

- [ ] **Step 2: Run the service test to verify it fails**

  Run: `python3 -m pytest -q tests/test_closing_trading_review.py::test_complete_report_groups_dynamic_option_positions_and_separates_pnl`

  Expected: FAIL because the new module and report contract do not yet exist.

- [ ] **Step 3: Implement the minimal service and schemas**

  Implement only the requested boundary:

  - Normalize explicit `YYYYMMDD` or `YYYY-MM-DD` to `YYYYMMDD`; reject invalid dates without selecting a neighboring date.
  - Resolve `hongyuan_futures` after the caller has passed the existing option view permission; never accept a caller-supplied account ID.
  - Select active daily batches for the target date and active monthly batches covering the target. Prefer the daily snapshot; use monthly facts only for provable realized facts or a month-end snapshot, and label the result as partial/derived where daily valuation evidence is absent.
  - Validate source rows, option contract syntax, direction, positive quantity, contract spec and target-date facts. Map missing source evidence or conflicting source-difference records to `data_anomaly` and set affected numeric values to `None`.
  - Group positions by real expiry month, Call/Put and buy/sell direction; derive dynamic strike min/max and contract-level details; calculate Call/Put net sold as `sell - buy`, with negative net shown as `净买` magnitude.
  - Calculate option floating P&L from `settlement_price - average_price` for buys and the reverse for sells, multiplied by the controlled option multiplier; sum `fact_close_pnl` for same-day option close facts without adding `matched_fee`.
  - Build contract-group contribution from close facts, sorting by absolute P&L and marking the ratio as not suitable for standalone interpretation when signs materially offset or the denominator is near zero.
  - Return fixed metadata constants such as `closing-option-review-v1` and `option-review-rules-v1`; use batch/source-row/spec identifiers only as evidence references and never return raw statement text or account numbers.

- [ ] **Step 4: Run the service test to verify it passes**

  Run: `python3 -m pytest -q tests/test_closing_trading_review.py::test_complete_report_groups_dynamic_option_positions_and_separates_pnl`

  Expected: PASS with no new warning or error.

- [ ] **Step 5: Commit the atomic service contract**

  ```bash
  git add backend/app/closing_trading_review.py tests/test_closing_trading_review.py
  git commit -m "feat: add deterministic option review service"
  ```

## Task 2: Add the read-only FastAPI route and permission boundary

**Files:**
- Modify: `backend/app/main.py` (module import and `include_router` only)
- Modify: `backend/app/closing_trading_review.py` (router dependency only)
- Test: `tests/test_closing_trading_review.py`

**Interfaces:**
- Consumes: `OptionReviewRequest`, `build_option_daily_review`, existing `db.get_user_by_token` session and `require_permission` mapping for `trading.options`.
- Produces: `GET /api/closing-trading-review/options/daily-summary` with `response_model=OptionDailyReviewResponse`; authentication and option view permission run before any report data query.

- [ ] **Step 1: Write the failing API contract tests**

  Assert the route returns the typed report for an authorized synthetic user, rejects a user without option permission with 403, rejects invalid dates with 422, and does not expose raw account identifiers or statement content.

  ```python
  def test_option_review_api_requires_option_permission_before_data_access(tmp_path, monkeypatch):
      use_temp_db(tmp_path, monkeypatch)
      user_id = seed_user_without_option_permission()
      token = db.create_session(user_id)
      monkeypatch.setattr(
          closing_trading_review,
          "build_option_daily_review",
          lambda _date: pytest.fail("permission must be checked before data access"),
      )

      with TestClient(main.app) as client:
          response = client.get(
              "/api/closing-trading-review/options/daily-summary?trading_date=20260529",
              headers={"Authorization": f"Bearer {token}"},
          )

      assert response.status_code == 403
      assert "statement_account_code" not in response.text
  ```

- [ ] **Step 2: Run the API tests to verify they fail**

  Run: `python3 -m pytest -q tests/test_closing_trading_review.py -k api`

  Expected: FAIL because the route is not registered.

- [ ] **Step 3: Implement the minimal route**

  Import the module in `backend/app/main.py` and include it under `/api`; the route should call `require_permission(user, "trading.options", "view")` before invoking the service. Keep the fixed account scope inside the service and expose only structured response fields.

- [ ] **Step 4: Run the API tests to verify they pass**

  Run: `python3 -m pytest -q tests/test_closing_trading_review.py -k api`

  Expected: PASS with authorized response, 403 unauthorized response and 422 invalid-date response.

- [ ] **Step 5: Commit the API boundary**

  ```bash
  git add backend/app/main.py backend/app/closing_trading_review.py tests/test_closing_trading_review.py
  git commit -m "feat: expose read-only option review endpoint"
  ```

## Task 3: Expand Golden Cases and run the approved regression

**Files:**
- Test: `tests/test_closing_trading_review.py`
- Reference only: existing trading tests and synthetic fixture helpers

**Interfaces:**
- Consumes: the Task 1 service and Task 2 endpoint.
- Produces: executable coverage for `CR-001` through `CR-006` and a concise verification record in the final response.

- [ ] **Step 1: Add failing edge-case tests**

  Add independent tests for: multiple months; buy reducing sell; buy greater than sell displaying net buy; zero confirmed daily positions; missing daily statement; monthly-only missing historical valuation; source conflict; realized/unrealized separation; missing source evidence; missing contract multiplier; and API overreach/permission.

- [ ] **Step 2: Run each new case before its supporting implementation exists**

  Run: `python3 -m pytest -q tests/test_closing_trading_review.py`

  Expected: every newly added behavior that is not already covered fails for the missing behavior, while the initial complete-contract test remains the first confirmed RED test for the new module.

- [ ] **Step 3: Implement only the smallest rule changes required by the failures**

  Keep all formulas and status transitions in the deterministic module. Do not change parser, database schema, existing trading-management writes, permission defaults, or any model/Harness code.

- [ ] **Step 4: Run the focused suite and quality checks**

  Run: `python3 -m pytest -q tests/test_closing_trading_review.py`

  Expected: all new service and API tests pass.

  Run: `python3 -m py_compile backend/app/closing_trading_review.py backend/app/main.py && git diff --check`

  Expected: exit 0 with no syntax or whitespace errors.

- [ ] **Step 5: Run the affected regression set**

  Run: `python3 -m pytest -q tests/test_closing_trading_review.py tests/test_trading_settlement.py tests/test_trading_management.py tests/test_trading_valuation.py tests/test_trading_overview.py tests/test_auth_permissions.py`

  Expected: new tests and the previously recorded 163-test baseline pass; any difference is investigated and reported rather than folded into the baseline.

- [ ] **Step 6: Commit the verified Golden Cases**

  ```bash
  git add tests/test_closing_trading_review.py
  git commit -m "test: cover option review data boundaries"
  ```

## Final verification and handoff

- [ ] Re-read the requirements-to-test mapping and confirm every changed file belongs to `CR-001` through `CR-006`.
- [ ] Inspect `git diff --stat`, `git diff --check`, and `git status --short --branch`.
- [ ] Run the focused and affected regression commands freshly before making any completion claim.
- [ ] Record that no Staging/Production deployment, database migration, source data mutation, real-account access, or transaction operation occurred.
- [ ] State remaining real-data dependency: a controlled real sample and future read-only 15:00 last-trade source still require separate verification; Phase 1 is not a claim of those capabilities.
