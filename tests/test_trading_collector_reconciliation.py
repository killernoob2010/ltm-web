"""Schema and source-priority contracts for WH6 settlement reconciliation."""

from pathlib import Path
import json
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db
from app import trading_management
from test_trading_settlement import statement_fixture
from app import trading_collector_reconciliation as reconciliation


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "collector-reconciliation.db")
    db.init_db()


def account_id():
    with db.connect() as conn:
        return conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]


def insert_batch(account, start, end, status, statement_type):
    with db.connect() as conn:
        cursor = conn.cursor()
        db._exec(
            cursor,
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, status, statement_type,
                 source_priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (account, start, end, status, statement_type,
             200 if statement_type == "monthly" else 100),
        )
        return db.last_insert_id(conn)


def insert_wh6_fill(
    account,
    *,
    event_key="tradeid:000123",
    trade_date="2026-05-10",
    trade_time="09:31:02",
    exchange="DCE",
    contract="i2609-c-750",
    asset_type="option",
    side="买",
    open_close="开",
    quantity=1,
    price="785",
    fee=None,
    trade_id="000123",
):
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            """
            INSERT INTO trading_intraday_fills
                (account_id, source_event_key, trade_date, trade_time,
                 trade_timestamp, exchange, contract, raw_contract, asset_type,
                 side, open_close, quantity, price, fee, trade_id, order_id,
                 parser_version, source_record_sha256, source_path,
                 source_record_index, data_status, verification_status,
                 canonical_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, 'provisional', 'pending', ?)
            """,
            (
                account, event_key, trade_date, trade_time,
                trade_date + "T" + trade_time + "+08:00", exchange, contract,
                contract, asset_type, side, open_close, quantity, price, fee,
                trade_id, "order-" + event_key, "wh6-match-v2", "a" * 64,
                "Record/match.dat", 1, "b" * 64,
            ),
        )
        return db.last_insert_id(conn)


def insert_settlement_trade(
    account,
    batch_id,
    *,
    identity_key,
    transaction_no=None,
    trade_date="2026-05-10",
    trade_time="08:00:00",
    exchange="DCE",
    contract="i2609-c-750",
    asset_type="option",
    side="买",
    open_close="开",
    quantity=1,
    price=786,
    fee=1.5,
    turnover=None,
    hedge_flag=None,
    premium_cashflow=None,
):
    with db.connect() as conn:
        cur = conn.cursor()
        source_id = db._last_insert_id(
            cur,
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no,
                 raw_hash, raw_json)
            VALUES (?, 'trade', 'statement.txt', '成交记录', ?, ?, ?)
            """,
            (batch_id, batch_id, "c" * 64, json.dumps({"成交编号": transaction_no})),
        )
        existing_identity = db._exec(
            cur,
            """
            SELECT id FROM trading_fact_identities
            WHERE account_id = ? AND fact_type = 'trade' AND stable_key = ?
            """,
            (account, identity_key),
        ).fetchone()
        identity_id = existing_identity["id"] if existing_identity else db._last_insert_id(
            cur,
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (?, 'trade', ?)
            """,
            (account, identity_key),
        )
        db._exec(
            cur,
            """
            INSERT INTO trading_trade_facts
                (identity_id, batch_id, source_row_id, trade_date, trade_time,
                 exchange, contract, asset_type, side, open_close, quantity, price,
                 turnover, fee, hedge_flag, premium_cashflow, transaction_no,
                 normalized_transaction_no, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                identity_id, batch_id, source_id, trade_date, trade_time, exchange,
                contract, asset_type, side, open_close, quantity, price, turnover,
                fee, hedge_flag, premium_cashflow, transaction_no,
                reconciliation.normalize_transaction_no(transaction_no),
            ),
        )
        return identity_id


def current_resolution(fill_id):
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT result_status, resolved_fields_json, field_sources_json,
                   differences_json
            FROM trading_intraday_fill_reconciliations
            WHERE intraday_fill_id = ? AND is_current = 1
            """,
            (fill_id,),
        ).fetchone()
        fill = conn.execute(
            """
            SELECT data_status, reconciliation_status, effective_source
            FROM trading_intraday_fills WHERE id = ?
            """,
            (fill_id,),
        ).fetchone()
    assert row is not None
    return {
        "result_status": row["result_status"],
        "resolved_fields": json.loads(row["resolved_fields_json"]),
        "field_sources": json.loads(row["field_sources_json"]),
        "differences": json.loads(row["differences_json"]),
        "data_status": fill["data_status"],
        "reconciliation_status": fill["reconciliation_status"],
        "effective_source": fill["effective_source"],
    }


def reconcile_batch(batch_id):
    with db.connect() as conn:
        result = reconciliation.reconcile_intraday_fills_for_batch(
            conn.cursor(), batch_id, "tester"
        )
        conn.commit()
    return result


def test_reconciliation_schema_is_forward_only_and_auditable(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        trade_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(trading_trade_facts)")
        }
        intraday_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(trading_intraday_fills)")
        }
        assert {"transaction_no", "normalized_transaction_no"} <= trade_columns
        assert {
            "reconciliation_status",
            "settlement_identity_id",
            "settlement_batch_id",
            "effective_source",
            "reconciled_at",
        } <= intraday_columns
        assert conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='trading_intraday_fill_reconciliations'"
        ).fetchone()
    assert "trading_intraday_fill_reconciliations" in db.TRADING_COLLECTOR_TABLES


def test_existing_source_and_fact_rows_survive_repeated_initialization(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        batch_id = db._last_insert_id(
            conn.cursor(),
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, status, statement_type)
            VALUES (?, ?, ?, 'active', 'daily')
            """,
            (1, "2026-09-04", "2026-09-04"),
        )
        source_id = db._last_insert_id(
            conn.cursor(),
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet,
                 source_row_no, raw_hash, raw_json)
            VALUES (?, 'trade', 'statement.txt', '成交记录', 1, ?, ?)
            """,
            (batch_id, "a" * 64, '{"成交编号":"000123"}'),
        )
        identity_id = db._last_insert_id(
            conn.cursor(),
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (1, 'trade', 'identity-1')
            """,
            (),
        )
        db._exec(
            conn.cursor(),
            """
            INSERT INTO trading_trade_facts
                (identity_id, batch_id, source_row_id, trade_date, trade_time,
                 exchange, contract, asset_type, side, open_close, quantity, price)
            VALUES (?, ?, ?, '2026-09-04', '09:01:02', 'DCE', 'i2609',
                    'future', '买', '开仓', 1, 700)
            """,
            (identity_id, batch_id, source_id),
        )
        conn.commit()
    db.init_db()
    with db.connect() as conn:
        assert conn.execute(
            "SELECT raw_json FROM trading_source_rows WHERE id = ?", (source_id,)
        ).fetchone()["raw_json"] == '{"成交编号":"000123"}'
        row = conn.execute(
            "SELECT trade_date, contract FROM trading_trade_facts WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()
        assert (row["trade_date"], row["contract"]) == ("2026-09-04", "i2609")


def test_only_active_complete_monthly_batches_close_collection(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    insert_batch(account, "20260601", "20260630", "active", "monthly")
    insert_batch(account, "20260701", "20260731", "active", "daily")
    insert_batch(account, "20260801", "20260831", "preview", "monthly")
    insert_batch(account, "20260902", "20260930", "active", "monthly")

    policy = reconciliation.build_collection_policy(account)

    assert [
        (item["range_start"], item["range_end"])
        for item in policy["closed_ranges"]
    ] == [("2026-06-01", "2026-06-30")]


def test_collection_policy_api_timestamp_is_truncated_to_seconds(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    policy = reconciliation.build_collection_policy(account_id())
    generated_at = str(policy["generated_at"])
    assert "." not in generated_at.split("+", 1)[0]


def test_policy_preserves_month_gap_instead_of_using_max_date(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    insert_batch(account, "20260601", "20260630", "active", "monthly")
    insert_batch(account, "20260801", "20260831", "active", "monthly")

    policy = reconciliation.build_collection_policy(account)

    assert [item["month"] for item in policy["closed_ranges"]] == [
        "2026-06",
        "2026-08",
    ]
    assert "settled_through" not in policy


def test_monthly_confirmation_persists_normalized_transaction_number(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    content = statement_fixture().replace("|100001|TEST001|", "|000123|TEST001|")
    preview = trading_management.preview_settlement_import(
        account_id(), "daily.txt", content.encode("gb18030"), actor="tester"
    )
    result = trading_management.confirm_settlement_import(
        preview["preview_batch_id"], actor="tester"
    )

    with db.connect() as conn:
        row = conn.execute(
            "SELECT transaction_no, normalized_transaction_no "
            "FROM trading_trade_facts WHERE batch_id = ?",
            (result["batch_id"],),
        ).fetchone()
    assert row["transaction_no"] == "000123"
    assert row["normalized_transaction_no"] == "123"


def test_existing_source_rows_backfill_transaction_number_once(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    batch_id = insert_batch(account, "20260501", "20260531", "active", "daily")
    with db.connect() as conn:
        cur = conn.cursor()
        source_id = db._last_insert_id(
            cur,
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no,
                 raw_hash, raw_json)
            VALUES (?, 'trade', 'statement.txt', '成交记录', 1, ?, ?)
            """,
            (batch_id, "d" * 64, json.dumps({"columns": [""] * 15 + ["000123"]})),
        )
        identity_id = db._last_insert_id(
            cur,
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (?, 'trade', ?)
            """,
            (account, "backfill-identity"),
        )
        db._exec(
            cur,
            """
            INSERT INTO trading_trade_facts
                (identity_id, batch_id, source_row_id, trade_date, trade_time,
                 exchange, contract, asset_type, side, open_close, quantity, price)
            VALUES (?, ?, ?, '2026-05-10', '09:01:02', 'DCE', 'i2609',
                    'future', '买', '开', 1, 700)
            """,
            (identity_id, batch_id, source_id),
        )
        assert reconciliation.backfill_settlement_transaction_numbers(cur, account_id=account) == 1
        conn.commit()

    with db.connect() as conn:
        row = conn.execute(
            "SELECT transaction_no, normalized_transaction_no FROM trading_trade_facts WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
        assert (row["transaction_no"], row["normalized_transaction_no"]) == ("000123", "123")
        assert reconciliation.backfill_settlement_transaction_numbers(conn.cursor(), account_id=account) == 0


def test_monthly_absence_retires_lower_priority_trade_without_deleting_history(
    tmp_path, monkeypatch
):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    daily_batch = insert_batch(account, "20260501", "20260531", "active", "daily")
    monthly_batch = insert_batch(account, "20260501", "20260531", "preview", "monthly")

    with db.connect() as conn:
        source_id = db._last_insert_id(
            conn.cursor(),
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no,
                 raw_hash, raw_json)
            VALUES (?, 'trade', 'daily.txt', '成交记录', 1, ?, ?)
            """,
            (daily_batch, "a" * 64, "{}"),
        )
        identity_id = db._last_insert_id(
            conn.cursor(),
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (?, 'trade', 'daily-only')
            """,
            (account,),
        )
        db._exec(
            conn.cursor(),
            """
            INSERT INTO trading_trade_facts
                (identity_id, batch_id, source_row_id, trade_date, trade_time,
                 exchange, contract, asset_type, side, open_close, quantity, price,
                 is_current)
            VALUES (?, ?, ?, '2026-05-10', '09:01:02', 'DCE', 'i2609', 'future',
                    '买', '开仓', 1, 785, 1)
            """,
            (identity_id, daily_batch, source_id),
        )
        monthly_result = reconciliation.finalize_lower_priority_monthly_trades(
            conn.cursor(), monthly_batch
        )
        conn.commit()
        fact = conn.execute(
            "SELECT is_current FROM trading_trade_facts WHERE identity_id = ?",
            (identity_id,),
        ).fetchone()
        diff = conn.execute(
            "SELECT diff_json FROM trading_fact_source_differences "
            "WHERE identity_id = ? ORDER BY id DESC LIMIT 1",
            (identity_id,),
        ).fetchone()

    assert monthly_result == {"retired": 1, "audited": 1}
    assert fact["is_current"] == 0
    assert json.loads(diff["diff_json"])["change_type"] == "absent_from_monthly"


def test_daily_corrects_present_fields_but_does_not_close_month(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    fill_id = insert_wh6_fill(account, fee=None, price="785")
    daily_batch = insert_batch(account, "20260501", "20260531", "active", "daily")
    insert_settlement_trade(
        account,
        daily_batch,
        identity_key="daily-123",
        transaction_no="000123",
        exchange="大商所",
        price=786,
        fee=1.5,
    )

    summary = reconcile_batch(daily_batch)
    result = current_resolution(fill_id)

    assert summary.matched_daily == 0
    assert summary.corrected_daily == 1
    assert result["result_status"] == "corrected_daily"
    assert result["resolved_fields"]["price"] == 786
    assert result["resolved_fields"]["fee"] == 1.5
    assert result["resolved_fields"]["trade_time"] == "09:31:02"
    assert result["field_sources"]["trade_time"] == "wh6"
    assert reconciliation.build_collection_policy(account)["closed_ranges"] == []


def test_monthly_overrides_daily_without_blank_overwrite(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    fill_id = insert_wh6_fill(account, trade_time="09:31:02", price="785")
    daily_batch = insert_batch(account, "20260501", "20260531", "active", "daily")
    identity_id = insert_settlement_trade(
        account,
        daily_batch,
        identity_key="same-trade",
        transaction_no="123",
        price=786,
        fee=1.5,
    )
    monthly_batch = insert_batch(account, "20260501", "20260531", "active", "monthly")
    with db.connect() as conn:
        monthly_identity = insert_settlement_trade(
            account,
            monthly_batch,
            identity_key="same-trade",
            transaction_no="000123",
            price=787,
            fee=None,
        )
        assert monthly_identity == identity_id
        conn.execute(
            "UPDATE trading_trade_facts SET is_current = 0 "
            "WHERE identity_id = ? AND batch_id = ?",
            (identity_id, daily_batch),
        )
        conn.commit()

    reconcile_batch(monthly_batch)
    result = current_resolution(fill_id)

    assert result["result_status"] == "corrected_monthly"
    assert result["resolved_fields"]["price"] == 787
    assert result["resolved_fields"]["fee"] == 1.5
    assert result["resolved_fields"]["trade_time"] == "09:31:02"
    assert result["field_sources"]["price"] == "monthly"
    assert result["field_sources"]["fee"] == "daily"
    assert result["field_sources"]["trade_time"] == "wh6"


def test_alias_and_leading_zero_transaction_number_match(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    fill_id = insert_wh6_fill(
        account, exchange="大商所", trade_id="000123", price="786", fee=None
    )
    daily_batch = insert_batch(account, "20260501", "20260531", "active", "daily")
    insert_settlement_trade(
        account,
        daily_batch,
        identity_key="alias-123",
        transaction_no="123",
        exchange="DCE",
        fee=None,
    )

    reconcile_batch(daily_batch)
    assert current_resolution(fill_id)["result_status"] == "matched_daily"


def test_no_id_fallback_requires_unique_candidate(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    daily_batch = insert_batch(account, "20260501", "20260531", "active", "daily")
    insert_settlement_trade(
        account,
        daily_batch,
        identity_key="fallback-unique",
        transaction_no=None,
        fee=None,
    )
    fill = {
        "account_id": account,
        "trade_date": "2026-05-10",
        "exchange": "dce",
        "contract": "i2609-c-750",
        "asset_type": "option",
        "side": "买",
        "open_close": "开",
        "quantity": 1,
        "price": "786.000",
        "trade_id": None,
    }
    with db.connect() as conn:
        decision = reconciliation.match_intraday_fill(conn.cursor(), fill)
    assert decision.status == "matched_daily"


def test_no_id_fallback_marks_multiple_candidates_ambiguous(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    daily_batch = insert_batch(account, "20260501", "20260531", "active", "daily")
    insert_settlement_trade(account, daily_batch, identity_key="ambiguous-1", transaction_no=None)
    insert_settlement_trade(account, daily_batch, identity_key="ambiguous-2", transaction_no=None)
    fill = {
        "account_id": account,
        "trade_date": "2026-05-10",
        "exchange": "DCE",
        "contract": "i2609-c-750",
        "asset_type": "option",
        "side": "买",
        "open_close": "开",
        "quantity": 1,
        "price": "786",
        "trade_id": None,
    }
    with db.connect() as conn:
        decision = reconciliation.match_intraday_fill(conn.cursor(), fill)
    assert decision.status == "ambiguous"


def test_closed_month_without_settlement_match_is_conflict_not_volume(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    fill_id = insert_wh6_fill(account, trade_date="2026-05-10")
    monthly_batch = insert_batch(account, "20260501", "20260531", "active", "monthly")

    reconcile_batch(monthly_batch)
    result = current_resolution(fill_id)

    assert result["result_status"] == "monthly_unmatched"
    assert result["data_status"] == "settlement_conflict"
    assert result["reconciliation_status"] == "monthly_unmatched"


def test_daily_arriving_after_monthly_does_not_replace_monthly(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    fill_id = insert_wh6_fill(account, price="785")
    monthly_batch = insert_batch(account, "20260501", "20260531", "active", "monthly")
    identity_id = insert_settlement_trade(
        account,
        monthly_batch,
        identity_key="monthly-wins",
        transaction_no="123",
        price=787,
        fee=1.5,
    )
    daily_batch = insert_batch(account, "20260501", "20260531", "active", "daily")
    insert_settlement_trade(
        account,
        daily_batch,
        identity_key="monthly-wins",
        transaction_no="000123",
        price=786,
        fee=1.0,
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE trading_trade_facts SET is_current = 0 "
            "WHERE identity_id = ? AND batch_id = ?",
            (identity_id, daily_batch),
        )
        conn.commit()

    reconcile_batch(daily_batch)
    result = current_resolution(fill_id)

    assert result["result_status"] == "corrected_monthly"
    assert result["resolved_fields"]["price"] == 787
    assert result["field_sources"]["price"] == "monthly"
