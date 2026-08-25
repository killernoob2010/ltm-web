# 测试版贸易台账异常治理与 Excel 回填 Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (recommended) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在测试版完成贸易台账导航调整、2026 年 Excel 有值字段安全回填、供应商与商品分类异常治理，并确保旧历史记录不再进入本轮异常和待补录范围。

**Architecture:** 保持现有现货台账同步和权限边界。供应商 Q 改为法定全称主数据，已确认简称通过公开响应中的展示字段提供；商品 AU 继续由系统分类字典生成并补齐当前异常商品的精确映射。新增 2026-01-01 范围门禁、受控存量重分类维护接口和测试版 API 回填脚本；不新增手工“立即同步”入口，不直接写 Production 数据库。

**Tech Stack:** Python 3、SQLite/Postgres 兼容 SQL、现有 Flask API、原生前端 JavaScript、pytest、Node.js test、openpyxl（仅读取业务 Excel）。

**Spec:** `docs/superpowers/specs/2026-08-25-trade-ledger-navigation-backfill-anomaly-design.md`

## Global Constraints

- 仅修改当前隔离 worktree 并发布到 `staging`；不得合并或推送 `main`，不得写 Production。
- Excel 回填默认 dry-run；只处理 U >= 2026-01-01、唯一精确匹配、Excel 实际有值的字段。
- Q 保留法定全称；不根据当前 Excel 猜测 113 个供应商简称。已确认映射只能作为展示别名。
- AU 以系统商品分类字典为准；Excel 的 AU 重复 H 不覆盖系统分析分类。
- 不把空值填成“无”“不适用”或“——”；非空冲突不自动覆盖，需进入报告。
- 每个任务先写失败测试，再写最小实现；每一步完成后运行对应的定向测试。
- Staging 数据写入必须先记录 dry-run 结果，并在写入后通过 API 回读；不记录用户名密码和业务原始响应。

## Task 1: Lock navigation and public status semantics

**Files:** `backend/app/db.py`, `backend/app/spot_ledger.py`, `tests/test_spot_ledger.py`, new focused tests if needed.

- [x] Add a failing test that requires `spot_ledger` to be the first sidebar module and still precede `交易管理`.
- [x] Add failing tests for the 2026 boundary: 2025-12-31 is historical-scope-out, 2026-01-01 is in scope, and invalid/missing U is not silently historical.
- [x] Move the module tuple to the first sidebar position.
- [x] Add a shared focus-date helper and make public records expose `历史范围外` without red anomaly summaries for pre-2026 records.
- [x] Add the focus date to pending and sync-error queries so counts and lists exclude pre-2026 rows at the database query boundary.
- [x] Run the focused backend tests and confirm they fail before implementation and pass after implementation.

## Task 2: Make supplier names canonical and resolve product categories through the system dictionary

**Files:** `backend/app/spot_ledger.py`, `backend/app/spot_ledger_mapping_data.py`, `backend/app/spot_ledger_sync.py`, backend tests.

- [x] Add failing tests proving an unknown but non-empty legal supplier name is retained in Q and does not create a conversion-mapping anomaly.
- [x] Add failing tests proving an existing confirmed supplier alias is exposed as a display alias while Q remains the legal full name.
- [x] Add failing tests for the current exact AU unknown names and their system category result.
- [x] Change normalization so supplier mapping is display-only; keep canonical legal names in Q and retain true empty/conflict errors.
- [x] Add the explicit current product aliases to `PRODUCT_CATEGORY_MAPPINGS`, using the existing system category pattern and documenting that Excel AU is not the authority.
- [x] Add a restricted admin-only stored-mapping reconciliation operation to reverse known legacy supplier aliases, re-evaluate AU, and remove only resolved mapping errors from current-scope records.
- [x] Run focused normalization and reconciliation tests.

## Task 3: Make history workbook migration 2026-only and conflict-safe

**Files:** `backend/app/spot_ledger_sync.py`, `scripts/import_spot_ledger_history.py`, `tests/test_spot_ledger_sync.py`.

- [x] Add failing tests that pre-2026 workbook rows are skipped, 2026 rows are eligible, blank Excel fields are ignored, and non-empty conflicts are reported without overwrite.
- [x] Extend migration matching to require a valid 2026 focus date and preserve exact unique matching.
- [x] Permit safe history backfill of K when the system value is blank; keep the existing manual-field set and never overwrite non-empty differences.
- [x] Return aggregate dry-run/apply counts for matched, skipped, updated, identical, conflicts, and unmatched rows.
- [x] Run all sync migration tests and confirm existing sync behavior remains intact.

## Task 4: Add a guarded Staging API backfill runner

**Files:** new `scripts/import_spot_ledger_staging.py`, new script tests or testable helper module.

- [x] Add failing tests for exact host guard, default dry-run, 2026 filtering, unique matching, blank-only updates, conflict reporting, and no credential logging.
- [x] Implement login, one-shot current-scope snapshot reads, exact AD/H/U identity matching with X/L quantity and Z price consistency checks, and PATCH only for permitted fields.
- [x] Require `--apply` for writes and require the base URL to be exactly `https://ltm-web-staging.onrender.com` (allowing the app query suffix only for page navigation, not API writes).
- [x] Keep source workbook path explicit and report source sheet/row counts without dumping source payloads.
- [x] Run script unit tests, then run a real Staging dry-run. Do not apply until the code and mapping reconciliation are deployed.

## Task 5: Update the frontend to present the new business status

**Files:** `frontend/spot_ledger.js`, `frontend/app.js` if needed, `tests/spot_ledger_frontend.test.mjs`.

- [x] Add failing static tests for supplier display alias/full-name fallback, historical-scope wording, and no new manual-sync button.
- [x] Show the display alias when available with the legal full name available for audit; otherwise show the legal full name.
- [x] Render historical-scope status as explanatory text instead of a red sync anomaly.
- [x] Keep existing tab/filter permissions and module navigation behavior unchanged apart from order.
- [x] Run Node syntax and frontend tests.

## Task 6: Local integrated quality gate

- [x] Run backend focused tests, full relevant pytest modules, frontend Node tests, syntax checks, compile checks, and `git diff --check`.
- [x] Inspect the diff for scope, secrets, accidental data files, and generated artifacts.
- [x] Commit the implementation and push only to `origin/staging`.

## Task 7: Staging deployment, data entry, and real-page acceptance

- [x] Wait for the Staging build and verify version identity using the in-app Browser.
- [x] Confirm the sidebar ordering and open the real trade-ledger page without console errors.
- [x] Run the admin-only stored-mapping reconciliation in dry-run mode, review counts, then apply it on Staging.
- [x] Run `import_spot_ledger_staging.py` dry-run against `/Users/wangjingze/Downloads/现货业务台账 (5).xlsx`; record candidate updates, conflicts, unmatched and skipped historical rows.
- [x] Apply only the approved dry-run updates on Staging and read back representative K fields plus aggregate update counts; this run had zero approved Excel writes because all 391 non-empty K values already matched.
- [x] Read back 2026 counts for total, pending, true sync errors, and confirm pre-2026 records are excluded from those tabs/counts.
- [ ] Verify after the next scheduled sync that the resolved supplier and product samples do not reintroduce mapping errors; no manual sync was added or triggered in this run.
- [x] Update `版本更新记录.md` only after Staging deployment and data verification; include no credentials or raw source payloads.

## Acceptance Evidence

- [x] Automated tests pass with fresh command output.
- [x] Staging dry-run and apply summaries are retained in `版本更新记录.md` without credentials or raw business payloads.
- [x] Browser evidence covers version identity, sidebar order, target page, console health, status counts, and representative Excel-field readback.
- [x] Final response states what was actually written, what remained blank because the source Excel was blank, and the next scheduled-sync verification limitation.
