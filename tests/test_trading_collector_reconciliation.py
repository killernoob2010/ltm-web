"""Schema and source-priority contracts for WH6 settlement reconciliation."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db
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
