# Option Lifecycle Close Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show option exercise, assignment, and expiry abandonment in the existing close-record page with a `0` calculation close price, while retaining raw statement evidence and linking exercise-created futures trades.

**Architecture:** Keep `trading_statement_exercises` as the immutable raw statement event and create a current-version close projection in `trading_close_facts` using the same `option_event` identity. Reuse the existing fact/business close allocation tables so option events reduce remaining business positions and appear in all existing close queries without a new page. Store exercise-to-underlying-trade links separately so option-leg PnL and futures PnL remain independently auditable and are never double counted.

**Tech Stack:** Python 3.9, FastAPI/Pydantic, SQLite and PostgreSQL through `backend/app/db.py`, vanilla JavaScript, pytest, Node test runner.

## Global Constraints

- Normal development, database writes, deployment, and acceptance are Staging-only.
- Production, `main`, Production Render, Production Supabase, real trading operations, and real fund movements remain untouched.
- Use the existing isolated worktree `/private/tmp/ltm-web-settlement-import-20260720` and branch `codex/settlement-statement-import`.
- 页面不新增任何页面或页签。复用现有“平仓记录”表格，只增加一个可见列“了结类型”。
- Preserve the raw event separately; an option-event close projection is a lifecycle fact, not a fabricated market transaction.
- Ordinary close uses its real close price. Exercise, assignment, and expiry abandonment use calculation close price `0` for the option leg.
- Do not create a synthetic futures trade. Exercise/assignment may link only to a real imported underlying futures opening trade.
- If the option opening trade, contract multiplier, or exercise-created futures trade cannot be matched reliably, preserve the event and show “待核验”; do not guess.
- Keep `statement_event_pnl` separate from `fact_close_pnl`. The former is the statement field; the latter is the calculated option-leg result.
- Write every behavior test first, run it to observe the expected failure, then add the minimum implementation.
- Do not commit real statement files, full account identifiers, customer names, or raw statement bodies.

---

### Task 1: Normalize lifecycle event types and extend additive schema

**Files:**
- Modify: `backend/app/trading_settlement.py:178-216`
- Modify: `backend/app/db.py:1399-1434`
- Modify: `backend/app/db.py:1528-1553`
- Modify: `backend/app/db.py:1670-1689`
- Modify: `backend/app/db.py:1754-1779`
- Modify: `backend/app/db.py:1862-1881`
- Modify: `tests/test_trading_settlement.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- Produces event types `exercise`, `assignment`, and `expiry_abandon` from `_exercise_rows(text)`.
- Adds close projection columns: `settlement_type`, `event_type_raw`, `exercise_price`, `exercise_amount`, `statement_event_pnl`, `underlying_link_status`.
- Adds raw event columns: `identity_id`, `source_row_id`, `exchange`, `product`, `event_type_raw`, `exercise_amount`, `is_current`.
- Creates `trading_option_event_underlying_links(event_identity_id, underlying_trade_identity_id, matched_quantity, rule_version)`.

- [ ] **Step 1: Write failing parser normalization tests**

Extend `tests/test_trading_settlement.py` with explicit raw labels by reusing the existing one-event fixture:

```python
@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [
        ("期权放弃", "expiry_abandon"),
        ("期权执行", "exercise"),
        ("期权履约", "assignment"),
    ],
)
def test_option_lifecycle_event_types_are_normalized(raw_type, expected):
    content = statement_fixture(
        "20260601-20260630", exercise=True
    ).replace("期权放弃", raw_type)
    parsed = parse_settlement_statement(
        content.encode("gb18030"),
        "monthly.txt",
    )

    assert parsed["exercises"][0]["event_type"] == expected
    assert parsed["exercises"][0]["event_type_raw"] == raw_type
```

- [ ] **Step 2: Run the parser test to verify RED**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_settlement.py::test_option_lifecycle_event_types_are_normalized -q
```

Expected: FAIL because abandonment currently normalizes to `abandon` and assignment is not normalized.

- [ ] **Step 3: Implement the three explicit event types**

Replace the event-type expression in `_exercise_rows` with:

```python
def _option_event_type(raw_type: str) -> str:
    if "放弃" in raw_type:
        return "expiry_abandon"
    if "履约" in raw_type:
        return "assignment"
    if "执行" in raw_type or "行权" in raw_type:
        return "exercise"
    return raw_type
```

Use `event_type = _option_event_type(raw_type)` and keep `event_type_raw` unchanged.
Update the existing monthly parser assertion from `abandon` to `expiry_abandon`.

- [ ] **Step 4: Write failing PostgreSQL/SQLite schema tests**

Add to `tests/test_trading_management.py`:

```python
def test_option_event_close_projection_schema(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        close_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(trading_close_facts)").fetchall()
        }
        event_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(trading_statement_exercises)"
            ).fetchall()
        }
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert close_columns >= {
        "settlement_type",
        "event_type_raw",
        "exercise_price",
        "exercise_amount",
        "statement_event_pnl",
        "underlying_link_status",
    }
    assert event_columns >= {
        "identity_id",
        "source_row_id",
        "exchange",
        "product",
        "event_type_raw",
        "exercise_amount",
        "is_current",
    }
    assert "trading_option_event_underlying_links" in tables
```

- [ ] **Step 5: Run the schema test to verify RED**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_management.py::test_option_event_close_projection_schema -q
```

Expected: FAIL with the first missing column or table.

- [ ] **Step 6: Add the schema in both database definitions**

Add these columns to both PostgreSQL and SQLite `trading_close_facts` definitions:

```sql
settlement_type TEXT NOT NULL DEFAULT 'trade_close',
event_type_raw TEXT,
exercise_price DOUBLE PRECISION,
exercise_amount DOUBLE PRECISION,
statement_event_pnl DOUBLE PRECISION,
underlying_link_status TEXT,
```

Use `REAL` instead of `DOUBLE PRECISION` in the SQLite definition. Extend both `trading_statement_exercises` definitions with:

```sql
identity_id INTEGER,
source_row_id INTEGER,
exchange TEXT,
product TEXT,
event_type_raw TEXT,
exercise_amount DOUBLE PRECISION,
is_current INTEGER NOT NULL DEFAULT 1,
```

Create the link table in both database variants:

```sql
CREATE TABLE IF NOT EXISTS trading_option_event_underlying_links (
    id SERIAL PRIMARY KEY,
    event_identity_id INTEGER NOT NULL,
    underlying_trade_identity_id INTEGER NOT NULL,
    matched_quantity DOUBLE PRECISION NOT NULL,
    rule_version TEXT NOT NULL,
    UNIQUE(event_identity_id, underlying_trade_identity_id),
    FOREIGN KEY (event_identity_id) REFERENCES trading_fact_identities(id),
    FOREIGN KEY (underlying_trade_identity_id) REFERENCES trading_fact_identities(id)
);
```

Use `INTEGER PRIMARY KEY AUTOINCREMENT` and `REAL` in SQLite. Add the new table to `TRADING_MANAGEMENT_TABLES`.

- [ ] **Step 7: Extend compatibility migration**

Add the same columns to `_ensure_trading_statement_columns`. For SQLite, retain the existing `PRAGMA table_info` guard. For PostgreSQL, retain `ADD COLUMN IF NOT EXISTS`. Backfill ordinary close rows:

```sql
UPDATE trading_close_facts
SET settlement_type = 'trade_close'
WHERE settlement_type IS NULL OR settlement_type = '';
```

Create the link table with `CREATE TABLE IF NOT EXISTS` so an existing Staging database upgrades without rebuilding any fact table.

- [ ] **Step 8: Run GREEN for parser and schema**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_settlement.py tests/test_trading_management.py::test_option_event_close_projection_schema -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/app/trading_settlement.py backend/app/db.py tests/test_trading_settlement.py tests/test_trading_management.py
git commit -m "feat: model option lifecycle close facts"
```

### Task 2: Create option-event close projections and allocate opening premium

**Files:**
- Modify: `backend/app/trading_management.py:1018-1140`
- Modify: `backend/app/trading_management.py:1163-1291`
- Modify: `backend/app/trading_management.py:2100-2180`
- Modify: `tests/test_trading_management.py`
- Modify: `tests/test_trading_management_real_sample.py`

**Interfaces:**
- Produces `_match_option_event_allocations(cur, batch_id: int) -> dict[str, int]`.
- Stores each lifecycle event as raw `trading_statement_exercises` evidence and a `trading_close_facts` projection whose identity type is `option_event`.

- [ ] **Step 1: Write a failing abandonment projection test**

Add this concrete sanitized fixture helper to `tests/test_trading_management.py`. It reuses the existing valid statement fixture, replaces its one opening trade with an option opening trade, and optionally appends the real underlying opening trade required by an exercise:

```python
def option_event_statement(
    *,
    event_type="期权放弃",
    option_contract="i2607-p-750",
    option_side="买",
    option_open_price=12.5,
    option_quantity=2,
    event_date="20260529",
    exercise_price=750,
    include_option_open=True,
    underlying_contract=None,
    underlying_side=None,
):
    text = statement_fixture(f"20260501-{event_date}", exercise=True)
    original_trade = (
        "|20260529|TEST001|大商所|TESTCODE|铁矿石|i2609|卖|套保|785.000|1|"
        "78500.00|开|1.01|10.00|0.00|100001|TEST001|"
    )
    option_trade = (
        f"|{event_date}|TEST001|大商所|TESTCODE|铁矿石期权|{option_contract}|"
        f"{option_side}|套保|{option_open_price:.3f}|{option_quantity}|"
        f"{option_open_price * option_quantity * 100:.2f}|开|1.01|0.00|"
        f"{-option_open_price * option_quantity * 100 if option_side == '买' else option_open_price * option_quantity * 100:.2f}|"
        "100001|TEST001|"
    )
    replacement_trade = option_trade if include_option_open else original_trade
    trade_count = 1
    trade_quantity = option_quantity if include_option_open else 1
    trade_turnover = (
        option_open_price * option_quantity * 100 if include_option_open else 78500
    )
    fee_total = 1.01
    if underlying_contract and underlying_side:
        replacement_trade += (
            f"\n|{event_date}|TEST001|大商所|TESTCODE|铁矿石|{underlying_contract}|"
            f"{underlying_side}|套保|{exercise_price:.3f}|{option_quantity}|"
            f"{exercise_price * option_quantity * 100:.2f}|开|1.01|0.00|0.00|"
            "100002|TEST001|"
        )
        trade_count = 2
        trade_quantity += option_quantity
        trade_turnover += exercise_price * option_quantity * 100
        fee_total = 2.02
    text = text.replace(original_trade, replacement_trade)
    text = text.replace(
        "|共 1条|||||||||1|78500.00||1.01|10.00|0.00|||",
        f"|共 {trade_count}条|||||||||{trade_quantity}|{trade_turnover:.2f}||"
        f"{fee_total:.2f}|0.00|0.00|||",
    )
    text = text.replace("手 续 费 Commission：1.01", f"手 续 费 Commission：{fee_total:.2f}")
    original_event = (
        "|20260529|TEST001|大商所|TESTCODE|铁矿石期权|i2607-P-750|套保|买|"
        "期权放弃|1|750.000|75000.00|0.00|0.00|TEST001|"
    )
    new_event = (
        f"|{event_date}|TEST001|大商所|TESTCODE|铁矿石期权|{option_contract}|"
        f"套保|{option_side}|{event_type}|{option_quantity}|{exercise_price:.3f}|"
        f"{exercise_price * option_quantity * 100:.2f}|0.00|0.00|TEST001|"
    )
    text = text.replace(original_event, new_event)
    text = text.replace(
        "|共   1条|||||||||1||75000.00|0.00|0.00||",
        f"|共   1条|||||||||{option_quantity}||"
        f"{exercise_price * option_quantity * 100:.2f}|0.00|0.00||",
    )
    return text.encode("gb18030")


def confirm_option_event_statement(content, tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    preview = trading_management.preview_settlement_import(
        1, "monthly.txt", content, actor="tester"
    )
    return trading_management.confirm_settlement_import(
        preview["preview_batch_id"], actor="tester"
    )
```

Then add the abandonment test:

```python
def test_expiry_abandonment_becomes_zero_price_close_without_trade(tmp_path, monkeypatch):
    confirmed = confirm_option_event_statement(
        option_event_statement(
            option_contract="i2607-p-750",
            option_side="买",
            option_open_price=12.5,
            option_quantity=2,
            event_type="期权放弃",
            event_date="20260616",
        ),
        tmp_path,
        monkeypatch,
    )

    closes = trading_management.query_fact_rows(
        "closes",
        trading_management.FactFilters(contract="i2607-p-750", page=1, page_size=20),
    )
    event = closes["items"][0]
    assert event["settlement_type"] == "expiry_abandon"
    assert event["close_price"] == 0
    assert event["open_price"] == 12.5
    assert event["fact_close_pnl"] == -2500
    assert event["verification_status"] == "matched"
    assert closes["summary"]["settlement_quantity"] == 2
    assert closes["summary"]["transaction_close_quantity"] == 0

    with db.connect() as conn:
        assert conn.execute(
            """SELECT COUNT(*) AS c FROM trading_trade_facts
               WHERE contract = 'i2607-p-750' AND open_close = '平仓'"""
        ).fetchone()["c"] == 0
```

The fixture must insert an option multiplier of `100` in `trading_contract_specs`, making `(0 - 12.5) × 2 × 100 = -2500`.

- [ ] **Step 2: Run the abandonment test to verify RED**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_management.py::test_expiry_abandonment_becomes_zero_price_close_without_trade -q
```

Expected: FAIL because the event exists only in `trading_statement_exercises`.

- [ ] **Step 3: Prepare stable option-event identities during confirmation**

Before inserting raw exercise rows in `confirm_settlement_import`, normalize them through:

```python
events = _rows_with_statement_keys(
    "option_event",
    account_code,
    statement["exercises"],
)
prepared_events = _prepare_import_rows(
    cur,
    preview_batch_id,
    batch["account_id"],
    "option_event",
    batch["statement_file_name"],
    "行权明细",
    events,
)
```

Extend `_statement_stable_payload` so an `option_event` key contains:

```python
{
    "event_date": row["event_date"],
    "exchange": row["exchange"],
    "contract": row["contract"],
    "side": row["side"],
    "event_type": row["event_type"],
    "quantity": row["quantity"],
    "exercise_price": row["exercise_price"],
}
```

Call `_statement_current_decision` against `trading_close_facts` with this identity and the statement priority, so daily/monthly overlap has one current close projection.
When a new event version becomes current, set the older `trading_statement_exercises.is_current` row for the same identity to `0` in the same transaction, matching the close projection’s version switch.

- [ ] **Step 4: Insert raw evidence and pending close projection**

For each prepared event, insert the raw row with `identity_id`, `source_row_id`, all parsed event fields, and the chosen `is_current`. Insert its close projection using:

```python
close_side = "卖" if event["side"] == "买" else "买"
db._exec(
    cur,
    """
    INSERT INTO trading_close_facts
        (identity_id, batch_id, source_row_id, open_date, close_date, exchange,
         contract, asset_type, open_side, close_side, quantity, open_price,
         close_price, fact_close_pnl, matched_fee, is_current, fee_status,
         data_status, verification_status, settlement_type, event_type_raw,
         exercise_price, exercise_amount, statement_event_pnl,
         underlying_link_status)
    VALUES (?, ?, ?, NULL, ?, ?, ?, 'option', ?, ?, ?, 0, 0, 0, ?, ?, 
            'statement_event', 'file_imported', 'pending_event_match', ?, ?,
            ?, ?, ?, ?)
    """,
    (
        identity_id,
        preview_batch_id,
        source_row_id,
        event["event_date"],
        event["exchange"],
        event["contract"],
        event["side"],
        close_side,
        event["quantity"],
        event["fee"],
        is_current,
        event["event_type"],
        event["event_type_raw"],
        event["exercise_price"],
        event["exercise_amount"],
        event["exercise_pnl"],
        "not_required" if event["event_type"] == "expiry_abandon" else "pending",
    ),
)
```

Do not insert a row into `trading_close_trade_links`: no reverse market trade occurred.

- [ ] **Step 5: Allocate each event against real option openings**

Extend `match_imported_facts` by calling `_match_option_event_allocations(cur, batch_id)`. The helper must:

1. Select current event projections for the batch ordered by event date and source row.
2. Select all current opening option trades for the same account and contract with `trade_date <= event_date`.
3. Calculate remaining open quantity after all current `trading_fact_close_allocations`, including earlier ordinary closes and option events.
4. Allocate FIFO by trade date, trade time, source row, and ID.
5. Insert the allocations into `trading_fact_close_allocations` with `match_rule_version='option-event-fifo-v1'`.
6. Calculate `fact_close_pnl` per allocation using the real opening price and active option multiplier.
7. Update the event projection with weighted opening price, earliest opening date, total calculated PnL, and `verification_status='matched'`.
8. Leave `fact_close_pnl=0` and `verification_status='pending_event_match'` if quantity or multiplier is insufficient.

Use the existing function for each allocation:

```python
allocation_pnl = calculate_business_pnl(
    open_price=float(open_trade["price"]),
    close_price=0.0,
    side=event["open_side"],
    quantity=matched_quantity,
    multiplier=multiplier,
)
```

- [ ] **Step 6: Use the allocated opening price in default business PnL**

In `rebuild_default_business_allocations`, join the opening trade:

```sql
JOIN trading_trade_facts ot
  ON ot.identity_id = fa.open_trade_identity_id
 AND ot.is_current = 1
```

For `settlement_type != 'trade_close'`, calculate each allocation from `ot.price` and close price `0`; retain the existing ordinary-close result for `trade_close`. This prevents a weighted display price from being reused for every allocation.

- [ ] **Step 7: Write and run an unmatched-event warning test**

```python
def test_expired_short_option_keeps_full_opening_premium(tmp_path, monkeypatch):
    confirm_option_event_statement(
        option_event_statement(option_side="卖", option_open_price=12.5),
        tmp_path,
        monkeypatch,
    )
    event = trading_management.query_fact_rows(
        "closes",
        trading_management.FactFilters(contract="i2607-p-750"),
    )["items"][0]

    assert event["settlement_type"] == "expiry_abandon"
    assert event["open_side"] == "卖"
    assert event["close_price"] == 0
    assert event["fact_close_pnl"] == 2500
    assert event["verification_status"] == "matched"


def test_option_event_without_open_trade_is_visible_but_pending(tmp_path, monkeypatch):
    confirm_option_event_statement(
        option_event_statement(include_option_open=False),
        tmp_path,
        monkeypatch,
    )
    event = trading_management.query_fact_rows(
        "closes",
        trading_management.FactFilters(contract="i2607-p-750"),
    )["items"][0]

    assert event["settlement_type"] == "expiry_abandon"
    assert event["verification_status"] == "pending_event_match"
    assert event["fact_close_pnl"] == 0
```

Run all three Task 2 tests. Expected: PASS; the buyer loses the full premium, the seller keeps the full premium, and no PnL is guessed for the unmatched event.

- [ ] **Step 8: Add and run the real-sample regression**

Extend `tests/test_trading_management_real_sample.py` after confirming the daily and monthly files:

```python
event_rows = trading_management.query_fact_rows(
    "closes",
    trading_management.FactFilters(contract="i2607-p-750", page=1, page_size=20),
)
event = next(row for row in event_rows["items"] if row["settlement_type"] == "expiry_abandon")

assert event["close_date"] == "20260616"
assert event["open_side"] == "买"
assert event["quantity"] == 100
assert event["close_price"] == 0
assert event["statement_event_pnl"] == 0
assert event["verification_status"] == "matched"
assert event_rows["summary"]["transaction_close_quantity"] == 0
```

Also query with `start_date="20260601"` and `end_date="20260630"` and assert the unified June close count is `2352`, while `trade_close_record_count` remains `2351`.

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_management.py tests/test_trading_management_real_sample.py -q
```

Expected: all selected tests pass; the real test skips only when either local statement file is absent.

- [ ] **Step 9: Commit**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py tests/test_trading_management_real_sample.py
git commit -m "feat: project option events into close facts"
```

### Task 3: Link exercise/assignment to real underlying futures openings

**Files:**
- Modify: `backend/app/trading_management.py`
- Modify: `tests/test_trading_management.py`

**Interfaces:**
- `_option_contract_parts("i2607-p-750")` returns `("i2607", "put")`.
- `_option_event_underlying_side("call", "买")` returns `"买"`.
- `_option_event_underlying_side("call", "卖")` returns `"卖"`.
- `_option_event_underlying_side("put", "买")` returns `"卖"`.
- `_option_event_underlying_side("put", "卖")` returns `"买"`.
- `_link_option_event_underlying_trades(cur, batch_id: int) -> dict[str, int]` writes only links to imported futures opening trades.

- [ ] **Step 1: Write failing direction and contract parsing tests**

```python
@pytest.mark.parametrize(
    ("contract", "expected"),
    [
        ("i2607-c-750", ("i2607", "call")),
        ("i2607-p-750", ("i2607", "put")),
        ("rb2610C3500", None),
    ],
)
def test_option_contract_parts(contract, expected):
    assert trading_management._option_contract_parts(contract) == expected


@pytest.mark.parametrize(
    ("option_kind", "open_side", "expected"),
    [
        ("call", "买", "买"),
        ("call", "卖", "卖"),
        ("put", "买", "卖"),
        ("put", "卖", "买"),
    ],
)
def test_option_event_underlying_side(option_kind, open_side, expected):
    assert trading_management._option_event_underlying_side(option_kind, open_side) == expected
```

- [ ] **Step 2: Run helper tests to verify RED**

Run the two parametrized tests. Expected: FAIL because both helpers are missing.

- [ ] **Step 3: Implement exact DCE-style contract parsing and direction mapping**

```python
OPTION_CONTRACT_RE = re.compile(
    r"^(?P<underlying>[a-z]+[0-9]+)-(?P<kind>c|p)-(?P<strike>[0-9]+(?:\.[0-9]+)?)$",
    re.IGNORECASE,
)


def _option_contract_parts(contract: str) -> tuple[str, str] | None:
    match = OPTION_CONTRACT_RE.match((contract or "").strip())
    if not match:
        return None
    return (
        match.group("underlying").lower(),
        "call" if match.group("kind").lower() == "c" else "put",
    )


def _option_event_underlying_side(option_kind: str, open_side: str) -> str:
    if option_kind == "call":
        return open_side
    return "卖" if open_side == "买" else "买"
```

Unsupported contract formats remain pending rather than being guessed.

- [ ] **Step 4: Write a failing exercise-link test**

```python
def test_exercise_links_real_underlying_open_without_synthesizing_trade(tmp_path, monkeypatch):
    confirmed = confirm_option_event_statement(
        option_event_statement(
            event_type="期权执行",
            option_contract="i2607-c-750",
            option_side="买",
            option_quantity=2,
            exercise_price=750,
            underlying_contract="i2607",
            underlying_side="买",
        ),
        tmp_path,
        monkeypatch,
    )

    with db.connect() as conn:
        event = conn.execute(
            """SELECT identity_id, underlying_link_status
               FROM trading_close_facts
               WHERE settlement_type = 'exercise' AND is_current = 1"""
        ).fetchone()
        link = conn.execute(
            """SELECT matched_quantity
               FROM trading_option_event_underlying_links
               WHERE event_identity_id = ?""",
            (event["identity_id"],),
        ).fetchone()
        generated_trade_count = conn.execute(
            """SELECT COUNT(*) AS c FROM trading_trade_facts
               WHERE contract = 'i2607' AND batch_id = ?""",
            (confirmed["batch_id"],),
        ).fetchone()["c"]

    assert event["underlying_link_status"] == "matched"
    assert link["matched_quantity"] == 2
    assert generated_trade_count == 1
```

The last assertion proves only the one trade present in the fixture exists; the linker added no second synthetic trade.

- [ ] **Step 5: Run the exercise-link test to verify RED**

Expected: FAIL because there is no underlying link.

- [ ] **Step 6: Implement underlying trade linking**

After all statement trades and event projections are inserted, `_link_option_event_underlying_trades` must:

1. Ignore `expiry_abandon` and set its status to `not_required`.
2. Parse the option contract with `_option_contract_parts`.
3. Derive the expected futures side with `_option_event_underlying_side`.
4. Match current `asset_type='future'`, `open_close='开仓'` trades on event date, underlying contract, expected side, and exercise price.
5. Allocate deterministically by trade time, source row, and ID until event quantity is covered.
6. Insert link rows with `rule_version='option-exercise-underlying-v1'`.
7. Set `underlying_link_status='matched'` only when the entire quantity is linked; otherwise set `pending`.

Price comparison must use the existing `1e-8` tolerance:

```python
abs(float(trade["price"]) - float(event["exercise_price"])) < 1e-8
```

- [ ] **Step 7: Add a no-real-trade pending test**

```python
def test_exercise_without_imported_underlying_trade_stays_pending(tmp_path, monkeypatch):
    confirm_option_event_statement(
        option_event_statement(
            event_type="期权执行",
            option_contract="i2607-c-750",
            option_side="买",
            option_quantity=2,
            exercise_price=750,
        ),
        tmp_path,
        monkeypatch,
    )
    with db.connect() as conn:
        event = conn.execute(
            """SELECT underlying_link_status
               FROM trading_close_facts
               WHERE settlement_type = 'exercise' AND is_current = 1"""
        ).fetchone()
        trade_count = conn.execute(
            "SELECT COUNT(*) AS c FROM trading_trade_facts WHERE asset_type = 'future'"
        ).fetchone()["c"]

    assert event["underlying_link_status"] == "pending"
    assert trade_count == 0
```

- [ ] **Step 8: Run Task 3 tests**

Run:

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest \
  tests/test_trading_management.py::test_option_contract_parts \
  tests/test_trading_management.py::test_option_event_underlying_side \
  tests/test_trading_management.py::test_exercise_links_real_underlying_open_without_synthesizing_trade \
  tests/test_trading_management.py::test_exercise_without_imported_underlying_trade_stays_pending -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/app/trading_management.py tests/test_trading_management.py
git commit -m "feat: link exercised options to futures openings"
```

### Task 4: Add one close-type column and separated close quantities

**Files:**
- Modify: `backend/app/trading_management.py:1425-1535`
- Modify: `backend/app/trading_management.py:1725-1800`
- Modify: `backend/app/trading_management.py:2226-2390`
- Modify: `frontend/trading_management.js:122-142`
- Modify: `frontend/trading_management.js:205-225`
- Modify: `frontend/trading_management.js:356-395`
- Modify: `frontend/index.html`
- Modify: `tests/test_trading_management.py`
- Modify: `tests/trading_management_frontend.test.mjs`

**Interfaces:**
- Every close item exposes `settlement_type`.
- Close summaries expose `record_count`, `trade_close_record_count`, `quantity`, `settlement_quantity`, `transaction_close_quantity`, `fact_close_pnl`, and `fee`.
- Frontend label mapping is `trade_close → 普通平仓`, `exercise → 行权`, `assignment → 履约`, `expiry_abandon → 到期放弃`.

- [ ] **Step 1: Write failing fact/business query summary tests**

```python
def test_close_summary_separates_settlement_and_transaction_quantity(
    tmp_path, monkeypatch
):
    confirm_option_event_statement(
        option_event_statement(),
        tmp_path,
        monkeypatch,
    )

    facts = trading_management.query_fact_rows(
        "closes", trading_management.FactFilters(page=1, page_size=20)
    )
    options = trading_management.query_business_rows(
        "options", "closes", trading_management.FactFilters(page=1, page_size=20)
    )

    assert facts["summary"]["record_count"] == 2
    assert facts["summary"]["trade_close_record_count"] == 1
    assert facts["summary"]["settlement_quantity"] == 3
    assert facts["summary"]["transaction_close_quantity"] == 1
    assert {row["settlement_type"] for row in options["items"]} >= {"expiry_abandon"}
```

- [ ] **Step 2: Run query test to verify RED**

Expected: FAIL because the two separated summary fields do not exist.

- [ ] **Step 3: Extend the existing close queries**

Keep `trading_close_facts` as the only close-page query source. Add these aggregates to `_query_close_rows_paged`:

```sql
COUNT(CASE WHEN cf.settlement_type = 'trade_close' THEN 1 END)
    AS trade_close_record_count,
COALESCE(SUM(cf.quantity), 0) AS settlement_quantity,
COALESCE(SUM(CASE WHEN cf.settlement_type = 'trade_close'
                  THEN cf.quantity ELSE 0 END), 0)
    AS transaction_close_quantity
```

Set `summary["quantity"] = summary["settlement_quantity"]` for backward compatibility. Apply the same fields to the non-paged/business close summaries. Existing contract, direction, asset type, date, and classification filters apply equally to lifecycle rows.

The overview daily PnL query remains based on `trading_close_facts`, so matched lifecycle PnL is included once. Do not join the underlying futures trade into option-leg PnL.

- [ ] **Step 4: Ensure business positions are reduced by lifecycle allocations**

Keep the current business-position expression:

```sql
tf.quantity - COALESCE((
    SELECT SUM(a.matched_quantity)
    FROM trading_business_close_allocations a
    WHERE a.open_trade_identity_id = tf.identity_id
), 0) AS remaining_quantity
```

Add this assertion to the Task 4 Python test to verify reuse of the existing allocation path rather than adding a second subtraction:

```python
positions = trading_management.query_business_rows(
    "options",
    "positions",
    trading_management.FactFilters(contract="i2607-p-750", page=1, page_size=20),
)
assert positions["items"] == []
assert positions["summary"]["quantity"] == 0
```

- [ ] **Step 5: Write failing frontend contract test**

Add to `tests/trading_management_frontend.test.mjs`:

```javascript
test("option lifecycle events reuse close records with one type column", () => {
  assert.match(tradingJs, /\["settlement_type","了结类型"\]/);
  assert.match(tradingJs, /trade_close:\s*"普通平仓"/);
  assert.match(tradingJs, /exercise:\s*"行权"/);
  assert.match(tradingJs, /assignment:\s*"履约"/);
  assert.match(tradingJs, /expiry_abandon:\s*"到期放弃"/);
  assert.match(tradingJs, /成交平仓手数/);
  assert.doesNotMatch(tradingJs, /行权与到期.*tm-tab-button/);
});
```

- [ ] **Step 6: Run the frontend test to verify RED**

Run:

```bash
node --test tests/trading_management_frontend.test.mjs
```

Expected: the new test fails on the missing column and label mapping.

- [ ] **Step 7: Add the minimal frontend display**

Define:

```javascript
const SETTLEMENT_TYPE_LABELS = {
  trade_close: "普通平仓",
  exercise: "行权",
  assignment: "履约",
  expiry_abandon: "到期放弃",
};
```

Insert `["settlement_type","了结类型"]` after the close-date column in both `FACT_COLUMNS.closes` and `BUSINESS_COLUMNS.closes`. In `valueCell`:

```javascript
if (key === "settlement_type") {
  return esc(SETTLEMENT_TYPE_LABELS[row[key]] || row[key] || "普通平仓");
}
if (
  ["open_price", "fact_close_pnl"].includes(key)
  && row.settlement_type !== "trade_close"
  && row.verification_status !== "matched"
) {
  return '<span class="tm-tag amber">待核验</span>';
}
```

Keep the current tabs unchanged. In close summaries, label `summary.settlement_quantity` as “了结手数” and `summary.transaction_close_quantity` as “成交平仓手数”.

- [ ] **Step 8: Update overview wording and asset version**

Change “文华逐笔平仓盈亏” to “普通平仓及期权了结盈亏”, and “平仓与手续费” to “平仓与期权了结”. Bump only the existing `trading_management.js` cache-busting query in `frontend/index.html` to a new unique `20260720` suffix.

- [ ] **Step 9: Run Task 4 GREEN**

Run:

```bash
node --test tests/trading_management_frontend.test.mjs
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest tests/test_trading_management.py -q
node --check frontend/trading_management.js
```

Expected: all selected tests pass and JavaScript syntax validation exits `0`.

- [ ] **Step 10: Commit**

```bash
git add backend/app/trading_management.py frontend/trading_management.js frontend/index.html tests/test_trading_management.py tests/trading_management_frontend.test.mjs
git commit -m "feat: show option events in close records"
```

### Task 5: Full regression, Staging migration, and visible acceptance

**Files:**
- Modify after successful Staging deployment: `版本更新记录.md`
- Modify only if setup/behavior text is stale: `README.md`
- Modify: `docs/superpowers/plans/2026-07-20-option-lifecycle-close-integration.md`

**Interfaces:**
- No new endpoint or page.
- Existing `GET /api/trading-management/facts/closes`, overview, and business-ledger APIs return the added close fields.
- Existing settlement preview/confirm endpoints remain unchanged.

- [ ] **Step 1: Run the complete local gate**

```bash
env -u DATABASE_URL /Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m pytest -q
node --test tests/*.test.mjs
/Users/wangjingze/Documents/轻量化交易管理系统WEB/.venv/bin/python -m compileall -q backend
node --check frontend/trading_management.js
git diff --check
```

Expected: all Python and Node tests pass; compile, syntax, and diff checks exit `0`.

- [ ] **Step 2: Verify the local real-statement acceptance without exposing identifiers**

Run only `tests/test_trading_management_real_sample.py` with `DATABASE_URL` unset. Verify:

- daily and monthly statement counts retain the approved baselines;
- June has `2351` ordinary closes and `2352` unified close records;
- `i2607-p-750` has one `expiry_abandon` close projection for `100` lots on `20260616`;
- its calculation close price is `0`;
- it has no reverse close trade;
- its opening allocation is matched and the option business position becomes zero;
- statement event PnL `0` remains separate from calculated option-leg PnL.

- [ ] **Step 3: Commit and push the tested feature branch**

```bash
git add docs/superpowers/plans/2026-07-20-option-lifecycle-close-integration.md README.md
git commit -m "docs: record option lifecycle close behavior"
git push origin codex/settlement-statement-import
```

If `README.md` did not require a change, omit it from `git add`. Do not amend or push `main`.

- [ ] **Step 4: Integrate into `staging` and deploy**

Merge or fast-forward the tested feature commits into `staging`, push `staging`, and wait for Render Staging. Before the Staging migration, take the project-standard Staging database recovery point. Do not touch Production.

- [ ] **Step 5: Verify the real Staging surface in the in-app browser**

Open:

```text
https://ltm-web-staging.onrender.com/?codex=<staging-commit>
```

Verify:

- page title and the exact new `trading_management.js` asset version;
- no console errors;
- no new tab appears;
- “平仓记录” contains the “了结类型” column;
- a sanitized abandonment event displays “到期放弃”, close price `0`, and no reverse trade;
- “了结手数” includes the event while “成交平仓手数” excludes it;
- current option position is reduced after the event;
- a sanitized exercise event links to one real underlying futures opening trade;
- an exercise event without a real underlying trade displays “待核验” and creates no trade.

Clean only the explicitly identified sanitized Staging test batches after acceptance.

- [ ] **Step 6: Record Staging evidence**

After deployment and browser acceptance, update `版本更新记录.md` with:

- feature and Staging commit IDs;
- migration result and recovery point;
- Python/Node test totals;
- browser URL and static asset version;
- sanitized test-data creation and cleanup;
- rollback commit;
- explicit statement that Production was not released.

Commit and push the release record on `staging`.

- [ ] **Step 7: Record AI SDLC evidence and stop at Gate B**

Record focused tests, full local gate, migration evidence, and browser-visible Staging T3 acceptance under task `LIGHTWEIGHT-TRADING-MANAGEMENT-WEB-20260720-001`. Transition only to the Production confirmation gate. Do not merge `main`, push `main`, deploy Production, or modify Production data without a separate explicit user confirmation.
