"""Schema and source-priority contracts for WH6 settlement reconciliation."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "collector-reconciliation.db")
    db.init_db()


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
