# Trading Settlement Statement Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace new three-workbook uploads with one auto-detected daily or monthly GB18030 TXT settlement statement while preserving source lineage, idempotency, monthly precedence, and opening-position continuity.

**Architecture:** Add a focused statement parser that returns normalized metadata, summaries, trades, closes, positions, cash movements, and exercise events. Extend the existing trading import batch/fact model with statement metadata and per-fact current-version flags so several daily statements and one monthly statement can coexist. Keep business assignments attached to stable identities, expose one-file preview/confirm through the existing API paths, and reuse the existing import drawer.

**Tech Stack:** Python 3.9, FastAPI/Pydantic, SQLite and PostgreSQL through `backend/app/db.py`, vanilla JavaScript, pytest, Node test runner.

## Global Constraints

- Normal development, database writes, deployment, and acceptance are Staging-only.
- Production, `main`, Production Render, Production Supabase, real trading operations, and real fund movements remain untouched.
- Use one Agent and the existing isolated worktree `/private/tmp/ltm-web-settlement-import-20260720`.
- Write every behavior test first, run it to observe the expected failure, then add the minimum implementation.
- Do not commit real statement files, full account identifiers, customer names, or raw statement bodies.
- Real files may be read only in local isolated tests; Staging uses sanitized fixtures.
- Old Excel batches and business assignment identities remain readable and must not be deleted.

---

### Task 1: Parse and validate daily/monthly settlement statements

**Files:**
- Create: `backend/app/trading_settlement.py`
- Create: `tests/test_trading_settlement.py`

**Interfaces:**
- Produces: `parse_settlement_statement(content: bytes, filename: str) -> dict[str, Any]`
- Produces keys: `metadata`, `account_summary`, `cash_movements`, `trades`, `exercises`, `closes`, `positions`, `position_summary`, `counts`, `warnings`
- Produces normalized trade/close/position dictionaries compatible with the existing fact insertion fields.

- [ ] **Step 1: Write failing parser tests**

```python
def test_parse_daily_statement_detects_scope_and_sections():
    result = parse_settlement_statement(daily_fixture().encode("gb18030"), "daily.txt")
    assert result["metadata"]["statement_type"] == "daily"
    assert result["metadata"]["range_start"] == "20260529"
    assert result["counts"] == {"trade": 1, "close": 1, "exercise": 0, "position": 1}

def test_parse_monthly_statement_detects_range_and_abandonment():
    result = parse_settlement_statement(monthly_fixture().encode("gb18030"), "monthly.txt")
    assert result["metadata"]["statement_type"] == "monthly"
    assert result["metadata"]["range_end"] == "20260630"
    assert result["exercises"][0]["event_type"] == "abandon"

def test_statement_totals_must_match_detail_rows():
    with pytest.raises(ValueError, match="手续费汇总不一致"):
        parse_settlement_statement(bad_fee_fixture().encode("gb18030"), "bad.txt")
```

- [ ] **Step 2: Run RED**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_settlement.py -q
```

Expected: collection fails because `app.trading_settlement` does not exist.

- [ ] **Step 3: Implement the focused parser**

Create immutable section specifications and parse pipe-delimited rows without writing decoded files:

```python
SECTION_HEADERS = {
    "trades": ("成交记录 Transaction Record", "行权明细"),
    "exercises": ("行权明细", "平仓明细 Position Closed"),
    "closes": ("平仓明细 Position Closed", "持仓明细 Positions Detail"),
    "positions": ("持仓明细 Positions Detail", "持仓汇总 Positions"),
}

def parse_settlement_statement(content: bytes, filename: str) -> dict[str, Any]:
    text = _decode_statement(content)
    metadata = _parse_metadata(text, filename)
    parsed = {
        "metadata": metadata,
        "account_summary": _parse_account_summary(text),
        "cash_movements": _parse_cash_movements(text),
        "trades": _parse_trades(text),
        "exercises": _parse_exercises(text),
        "closes": _parse_closes(text),
        "positions": _parse_positions(text, metadata["range_end"]),
        "position_summary": _parse_position_summary(text),
        "warnings": [],
    }
    parsed["counts"] = {
        "trade": len(parsed["trades"]),
        "close": len(parsed["closes"]),
        "exercise": len(parsed["exercises"]),
        "position": len(parsed["positions"]),
    }
    _validate_statement(parsed)
    return parsed
```

Normalize exchange/contract, open/close labels, numeric fields, source line numbers, transaction numbers, raw data, mark PnL, calculated full-close PnL inputs, margin, hedge flag, and option market value.

- [ ] **Step 4: Run GREEN and parser regressions**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_settlement.py tests/test_trading_management.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/trading_settlement.py tests/test_trading_settlement.py
git commit -m "feat: parse trading settlement statements"
```

### Task 2: Add statement lineage and current fact versions

**Files:**
- Modify: `backend/app/db.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Adds nullable statement metadata to `trading_import_batches`.
- Adds `statement_account_code` to `trading_accounts`.
- Adds `is_current` to trade, close, and position fact tables.
- Creates `trading_statement_account_summaries`, `trading_statement_cash_movements`, `trading_statement_exercises`, `trading_statement_position_summaries`, and `trading_fact_source_differences`.

- [ ] **Step 1: Write failing schema and migration tests**

```python
def test_statement_schema_supports_lineage_and_one_current_fact(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    assert required_columns("trading_import_batches") >= {
        "statement_type", "statement_file_name", "statement_file_sha256",
        "statement_account_code_masked", "source_priority",
    }
    assert "is_current" in required_columns("trading_trade_facts")
    assert table_names() >= {
        "trading_statement_account_summaries",
        "trading_statement_cash_movements",
        "trading_statement_exercises",
        "trading_statement_position_summaries",
        "trading_fact_source_differences",
    }
```

- [ ] **Step 2: Run RED**

Run the named test and verify missing columns/tables cause the failure.

- [ ] **Step 3: Implement additive PostgreSQL and SQLite migrations**

Add columns with `ADD COLUMN IF NOT EXISTS` for PostgreSQL and `PRAGMA table_info` guards for SQLite. Backfill `is_current=1` only for facts whose legacy batch is `active`; leave preview/superseded legacy facts non-current. Add indexes for statement hash, account/date/type, and current fact lookup. Add the new tables to `TRADING_MANAGEMENT_TABLES` so PostgreSQL RLS and direct-role revocation remain enforced.

- [ ] **Step 4: Run GREEN**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_management.py -q
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py tests/test_trading_management.py
git commit -m "feat: version settlement statement facts"
```

### Task 3: Implement preview, confirm, precedence, and continuity

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`
- Modify: `tests/test_trading_management_real_sample.py`

**Interfaces:**
- Replaces new-upload payload with `statement_file: TradingUploadFile`.
- Produces `preview_settlement_import(account_id: int, filename: str, content: bytes, actor: str) -> dict`.
- Produces `confirm_settlement_import(preview_batch_id: int, actor: str) -> dict`.
- Keeps legacy workbook functions callable for existing tests and history.

- [ ] **Step 1: Write failing service tests**

Cover:

```python
def test_same_statement_hash_is_idempotent(...):
    first = preview_and_confirm(daily_bytes)
    second = preview_settlement_import(account_id, "copy.txt", daily_bytes, "tester")
    assert second["duplicate_batch_id"] == first["batch_id"]

def test_monthly_fact_replaces_conflicting_daily_version_but_keeps_difference(...):
    preview_and_confirm(daily_bytes)
    result = preview_and_confirm(monthly_conflict_bytes)
    assert result["monthly_replacements"] == 1
    assert current_trade()["price"] == 101
    assert difference_count() == 1

def test_month_end_position_initializes_next_month_opening(...):
    preview_and_confirm(previous_day_bytes)
    preview = preview_settlement_import(account_id, "month.txt", monthly_bytes, "tester")
    assert preview["continuity"]["status"] == "passed"
    assert preview["continuity"]["difference_lots"] == 0
```

Also test first-account binding, masked output, mismatch blocking, identical multiset occurrences, monthly-after-daily, daily-after-monthly, preview invalidation, missing contract multiplier, and legacy business assignments retained by stable identity.

- [ ] **Step 2: Run RED**

Run the new named tests and verify failures are caused by missing settlement import functions.

- [ ] **Step 3: Implement statement preview**

Decode the uploaded base64 at the API edge, call `parse_settlement_statement`, validate/bind the selected account, calculate stable identity and content hashes, compare existing source versions, and store a preview batch plus a bounded JSON summary. Do not store full statement content in `parse_summary`.

- [ ] **Step 4: Implement transactional confirmation**

Within one transaction:

```python
priority = 200 if statement["metadata"]["statement_type"] == "monthly" else 100
for fact_type, rows in normalized_rows.items():
    for row in _rows_with_statement_keys(fact_type, account_code, rows):
        current = _current_fact_version(cur, fact_type, row["stable_key"])
        decision = _choose_statement_version(current, row, priority)
        _insert_source_and_fact_version(cur, batch_id, fact_type, row, decision.is_current)
        if decision.replace_current:
            _mark_fact_noncurrent(cur, fact_type, current["id"])
            _record_fact_difference(cur, current, row, batch_id)
```

Persist cash movements, exercises, position summaries, account summary, overlap counts, and continuity result. Rebuild fact matching/default allocations only for identities whose current versions changed. Preserve `trading_business_assignments` because they reference identity IDs.

- [ ] **Step 5: Update every business query**

Replace batch-wide `status='active'` assumptions for fact visibility with `fact.is_current=1`. Keep legacy batch status in import-history displays only. Add a regression assertion that each overview/business query contains or semantically applies current-version filtering.

- [ ] **Step 6: Run GREEN and real local acceptance**

Run focused tests, then run the real-sample test with both provided local statements. The test must skip if either file is absent and must print no identifiers. Expected baselines are those recorded in the approved design.

- [ ] **Step 7: Commit**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py tests/test_trading_management_real_sample.py
git commit -m "feat: merge daily and monthly settlement facts"
```

### Task 4: Replace the three-file UI with one settlement file

**Files:**
- Modify: `frontend/trading_management.js`
- Modify: `frontend/index.html`
- Modify: `tests/trading_management_frontend.test.mjs`
- Modify: `backend/app/trading_management.py`

**Interfaces:**
- Keeps `POST /api/trading-management/imports/preview`.
- Keeps `POST /api/trading-management/imports/{preview_batch_id}/confirm`.
- Preview JSON changes from three file objects to `statement_file`.

- [ ] **Step 1: Write failing frontend and API tests**

```javascript
test("settlement import uses one txt file and renders detected scope", () => {
  assert.match(tradingJs, /导入结算单/);
  assert.match(tradingJs, /id="tmStatementFile"[^>]+accept="\\.txt"/);
  assert.doesNotMatch(tradingJs, /id="tmTradeFile"|id="tmCloseFile"|id="tmPositionFile"/);
  assert.match(tradingJs, /账单类型|重复|冲突|连续性/);
});
```

Add API model tests asserting `statement_file` is required and legacy three-file JSON is rejected by the new endpoint.

- [ ] **Step 2: Run RED**

Run Node and named Python API tests; verify they fail on the old three-file interface.

- [ ] **Step 3: Implement the one-file drawer flow**

Reuse existing `filePayload`, `setImportBusy`, invalidation, preview, confirm, toast, drawer layout, and permission checks. Render detected account mask, scope, counts, totals, duplicate/supplement/conflict/replacement counts, warnings, and continuity status. Do not display a full statement account identifier.

- [ ] **Step 4: Bump trading static asset versions**

Update the existing `trading_management.js` cache-busting query in `frontend/index.html` to a unique `20260720` settlement-import version.

- [ ] **Step 5: Run GREEN**

Run:

```bash
node --test tests/trading_management_frontend.test.mjs
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_management.py -q
```

- [ ] **Step 6: Commit**

```bash
git add frontend/trading_management.js frontend/index.html tests/trading_management_frontend.test.mjs backend/app/trading_management.py
git commit -m "feat: import one settlement statement"
```

### Task 5: Full verification, documentation, and Staging acceptance

**Files:**
- Modify: `README.md`
- Modify after successful deployment: `版本更新记录.md`
- Modify: `docs/superpowers/plans/2026-07-20-trading-settlement-statement-import.md`

**Interfaces:**
- No new runtime interface beyond Tasks 1-4.

- [ ] **Step 1: Run the complete local machine gate**

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
/Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m compileall -q backend
node --check frontend/trading_management.js
git diff --check
```

- [ ] **Step 2: Update setup documentation**

Document month-first usage, optional daily statements, one-file preview, precedence, opening-position continuity, and the fact that Production remains unchanged.

- [ ] **Step 3: Commit and push the feature branch**

```bash
git add README.md docs/superpowers/plans/2026-07-20-trading-settlement-statement-import.md
git commit -m "docs: explain settlement statement imports"
git push -u origin codex/settlement-statement-import
```

- [ ] **Step 4: Integrate into `staging` and deploy**

Fast-forward or merge the tested feature commit into `staging`, push `staging`, and wait for Render `ltm-web-staging`. Do not touch `main`.

- [ ] **Step 5: Verify the real Staging surface**

Open `https://ltm-web-staging.onrender.com/?codex=<commit>` in a fresh in-app browser tab. Verify title, exact static asset version, console health, one-file drawer, sanitized daily preview, confirmation, repeated upload, sanitized monthly replacement, continuity display, and resulting fact counts. Clean only the explicitly identified sanitized test batches.

- [ ] **Step 6: Record Staging result**

After deployment and browser acceptance, add the commit/deploy, test totals, sanitized-data actions, database impact, rollback commit, and explicit “Production 未发布” to `版本更新记录.md`; commit and push `staging`.

- [ ] **Step 7: Record AI SDLC tests and transition**

Record focused tests, full local gate, migration evidence, and Staging T3 acceptance under `LIGHTWEIGHT-TRADING-MANAGEMENT-WEB-20260720-001`, then transition to `release_close`/Gate B waiting state.
