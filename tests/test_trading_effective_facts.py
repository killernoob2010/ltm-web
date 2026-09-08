"""Tests for the single effective settlement/provisional trade projection."""

from pathlib import Path
from datetime import datetime, timezone
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).parent))

from app import db
from app import trading_collector_reconciliation as reconciliation
from app import trading_effective_facts
from app import trading_management
from test_trading_collector_reconciliation import (
    account_id,
    insert_batch,
    insert_settlement_trade,
    insert_wh6_fill,
    reconcile_batch,
    use_temp_db,
)


def effective_filters(**overrides):
    values = {
        "page": 1,
        "page_size": 20,
    }
    values.update(overrides)
    return trading_effective_facts.EffectiveFactFilters(**values)


def test_daily_settlement_replaces_matched_wh6_without_double_counting(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    insert_wh6_fill(account, trade_date="2026-05-10", trade_id="000123", price="785")
    daily_batch = insert_batch(account, "2026-05-10", "2026-05-10", "active", "daily")
    insert_settlement_trade(
        account,
        daily_batch,
        identity_key="effective-daily",
        transaction_no="000123",
        trade_date="2026-05-10",
        price=785,
        fee=None,
    )
    reconcile_batch(daily_batch)

    with db.connect() as conn:
        result = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters()
        )

    assert result["total_items"] == 1
    assert result["items"][0]["fact_status"] == "settlement_confirmed"
    assert result["items"][0]["source_type"] == "daily"
    assert result["summary"]["settlement_confirmed_count"] == 1
    assert result["summary"]["provisional_count"] == 0


def test_monthly_settlement_wins_over_daily_without_duplicate(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    daily_batch = insert_batch(account, "2026-05-01", "2026-05-31", "active", "daily")
    identity_id = insert_settlement_trade(
        account,
        daily_batch,
        identity_key="effective-monthly",
        transaction_no="123",
        trade_date="2026-05-10",
        price=786,
        fee=None,
    )
    monthly_batch = insert_batch(account, "20260501", "20260531", "active", "monthly")
    insert_settlement_trade(
        account,
        monthly_batch,
        identity_key="effective-monthly",
        transaction_no="000123",
        trade_date="20260510",
        price=787,
        fee=None,
    )
    with db.connect() as conn:
        conn.execute(
            "UPDATE trading_trade_facts SET is_current = 0 "
            "WHERE identity_id = ? AND batch_id = ?",
            (identity_id, daily_batch),
        )
        conn.commit()

        result = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters()
        )

    assert result["total_items"] == 1
    assert result["items"][0]["source_type"] == "monthly"
    assert result["items"][0]["price"] == 787


def test_uncovered_wh6_trade_is_provisional_and_not_unclassified(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    fill_id = insert_wh6_fill(
        account,
        event_key="tradeid:provisional",
        trade_id="provisional",
        trade_date="2026-09-06",
        fee="9.5",
    )
    with db.connect() as conn:
        reconciliation.reconcile_intraday_range(
            conn.cursor(), account, "2026-09-06", "2026-09-06", "tester"
        )
        conn.commit()
        all_rows = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters()
        )
        unclassified = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters(classification="unclassified")
        )
        selected = trading_effective_facts.query_effective_trade_selection_ids(
            conn.cursor(), effective_filters()
        )

    item = all_rows["items"][0]
    assert item["record_key"] == f"wh6:{fill_id}"
    assert item["fact_status"] == "provisional"
    assert item["source_type"] == "wh6"
    assert item["can_classify"] is False
    assert item["assignment_status"] == "not_applicable"
    assert unclassified["items"] == []
    assert selected["identity_ids"] == []


def test_provisional_trade_does_not_fabricate_settlement_amounts(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    insert_wh6_fill(
        account,
        event_key="tradeid:missing-amounts",
        trade_id="missing-amounts",
        trade_date="2026-09-06",
        fee="9.5",
    )
    with db.connect() as conn:
        reconciliation.reconcile_intraday_range(
            conn.cursor(), account, "2026-09-06", "2026-09-06", "tester"
        )
        conn.commit()
        result = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters()
        )

    item = result["items"][0]
    assert item["turnover"] is None
    assert item["fee"] is None
    assert item["premium_cashflow"] is None
    assert item["fact_close_pnl"] is None


def test_fact_status_filter_and_server_pagination_cover_both_sources(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    for index in range(21):
        insert_wh6_fill(
            account,
            event_key=f"tradeid:provisional-{index}",
            trade_id=f"provisional-{index}",
            trade_date="2026-09-06",
        )
    with db.connect() as conn:
        reconciliation.reconcile_intraday_range(
            conn.cursor(), account, "2026-09-06", "2026-09-06", "tester"
        )
        conn.commit()
        result = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters(fact_status="provisional", page=2, page_size=20)
        )

    assert result["total_items"] == 21
    assert result["total_pages"] == 2
    assert len(result["items"]) == 1
    assert result["summary"]["contains_provisional"] is True


def test_effective_trade_projection_reuses_statement_coverage_lookup(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    for index in range(2):
        insert_wh6_fill(
            account,
            event_key=f"tradeid:coverage-cache-{index}",
            trade_id=f"coverage-cache-{index}",
            trade_date=f"2026-09-0{index + 1}",
        )

    original_lookup = reconciliation.get_active_statement_coverages
    lookup_accounts = []

    def counted_lookup(cur, account_id_value):
        lookup_accounts.append(account_id_value)
        return original_lookup(cur, account_id_value)

    monkeypatch.setattr(reconciliation, "get_active_statement_coverages", counted_lookup)
    with db.connect() as conn:
        reconciliation.reconcile_intraday_range(
            conn.cursor(), account, "2026-09-01", "2026-09-02", "tester"
        )
        conn.commit()
        lookup_accounts.clear()
        result = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters()
        )

    assert result["total_items"] == 2
    assert lookup_accounts == [account]


def test_invalid_fact_status_is_rejected():
    with pytest.raises(ValueError, match="事实状态"):
        effective_filters(fact_status="unknown")


def test_management_query_fact_rows_uses_effective_provisional_projection(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    insert_wh6_fill(
        account,
        event_key="tradeid:management-provisional",
        trade_id="management-provisional",
        trade_date="2026-09-06",
    )
    with db.connect() as conn:
        reconciliation.reconcile_intraday_range(
            conn.cursor(), account, "2026-09-06", "2026-09-06", "tester"
        )
        conn.commit()

    result = trading_management.query_fact_rows(
        "trades",
        trading_management.FactFilters(fact_status="provisional", page=1, page_size=20),
    )

    assert result["total_items"] == 1
    assert result["items"][0]["fact_status"] == "provisional"
    assert result["items"][0]["can_classify"] is False


def test_management_api_filters_accept_fact_status():
    filters = trading_management._api_filters(fact_status="settlement_confirmed")

    assert filters.fact_status == "settlement_confirmed"


def _baseline_row(contract, direction, quantity, average_price, *, margin=100):
    return {
        "exchange": "DCE",
        "contract": contract,
        "asset_type": "future",
        "direction": direction,
        "quantity": quantity,
        "average_price": average_price,
        "margin": margin,
        "valuation_price": average_price + 10,
        "floating_pnl": 20,
        "snapshot_date": "20260831",
        "source_record_count": 1,
    }


def _fill(
    fill_id,
    contract,
    side,
    open_close,
    quantity,
    price,
    *,
    trade_date="20260901",
    trade_time=None,
):
    return {
        "id": fill_id,
        "trade_date": trade_date,
        "trade_time": trade_time or f"09:00:{fill_id:02d}",
        "exchange": "DCE",
        "contract": contract,
        "asset_type": "future",
        "side": side,
        "open_close": open_close,
        "quantity": quantity,
        "price": price,
    }


def _insert_settlement_position_batch(
    account,
    snapshot_date,
    rows=(),
    *,
    statement_type="monthly",
    range_start="20260801",
    range_end="20260831",
):
    batch_id = insert_batch(account, range_start, range_end, "active", statement_type)
    with db.connect() as conn:
        conn.execute(
            "UPDATE trading_import_batches SET position_snapshot_date = ?, position_count = ? WHERE id = ?",
            (snapshot_date, len(rows), batch_id),
        )
        cur = conn.cursor()
        for index, row in enumerate(rows, start=1):
            source_row_id = db._last_insert_id(
                cur,
                """
                INSERT INTO trading_source_rows
                    (batch_id, source_type, source_file, source_sheet, source_row_no, raw_hash, raw_json)
                VALUES (?, 'position', 'statement.txt', '持仓', ?, ?, '{}')
                """,
                (batch_id, index, f"position-{batch_id}-{index}"),
            )
            identity_id = db._last_insert_id(
                cur,
                "INSERT INTO trading_fact_identities (account_id, fact_type, stable_key) VALUES (?, 'position', ?)",
                (account, f"position-{batch_id}-{index}"),
            )
            db._exec(
                cur,
                """
                INSERT INTO trading_position_snapshots
                    (identity_id, batch_id, source_row_id, snapshot_date, snapshot_time,
                     exchange, contract, asset_type, direction, open_date, quantity,
                     average_price, margin, valuation_price, floating_pnl, market_time,
                     is_current, valuation_status, data_status, verification_status)
                VALUES (?, ?, ?, ?, '15:00:00', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                        1, 'settlement_reference', 'file_imported', 'verified')
                """,
                (
                    identity_id,
                    batch_id,
                    source_row_id,
                    snapshot_date,
                    row["exchange"],
                    row["contract"],
                    row["asset_type"],
                    row["direction"],
                    row.get("open_date", snapshot_date),
                    row["quantity"],
                    row["average_price"],
                    row.get("margin", 100),
                    row.get("valuation_price", row["average_price"]),
                    row.get("floating_pnl", 0),
                ),
            )
        conn.commit()
    return batch_id


def _insert_wh6_snapshot(
    account,
    trade_date="20260902",
    snapshot_timestamp="2026-09-02T09:00:00+08:00",
    rows=(),
    *,
    conflict_status="none",
):
    with db.connect() as conn:
        cur = conn.cursor()
        snapshot_id = db._last_insert_id(
            cur,
            """
            INSERT INTO trading_intraday_position_snapshots
                (account_id, source_snapshot_key, trade_date, snapshot_time, snapshot_timestamp,
                 complete, source_snapshot_sha256, parser_version, data_status, verification_status,
                 conflict_status, canonical_hash)
            VALUES (?, ?, ?, '09:00:00', ?, 1, ?, 'wh6-match-v1', 'provisional', 'pending', ?, ?)
            """,
            (
                account,
                f"snapshot-{trade_date}-{snapshot_timestamp}",
                trade_date,
                snapshot_timestamp,
                "a" * 64,
                conflict_status,
                "b" * 64,
            ),
        )
        for index, row in enumerate(rows):
            db._exec(
                cur,
                """
                INSERT INTO trading_intraday_position_rows
                    (snapshot_id, account_id, contract, raw_contract, asset_type, exchange,
                     direction, quantity, average_price, source_record_index, source_record_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    account,
                    row["contract"],
                    row["contract"],
                    row["asset_type"],
                    row["exchange"],
                    row["direction"],
                    row["quantity"],
                    row["average_price"],
                    index,
                    f"c{index}" * 64,
                ),
            )
        conn.commit()
    return snapshot_id


def test_infer_positions_applies_open_close_direction_and_weighted_average():
    result = trading_effective_facts.infer_positions_from_fills(
        [
            _baseline_row("i2609", "买", 2, 700),
            _baseline_row("i2509", "卖", 3, 900),
            _baseline_row("i2409", "买", 1, 600),
        ],
        [
            _fill(1, "i2609", "买", "开", 1, 730),
            _fill(2, "i2609", "卖", "平", 1, 740),
            _fill(3, "i2509", "卖", "开", 2, 920),
            _fill(4, "i2509", "买", "平", 1, 930),
            _fill(5, "i2701", "买", "开", 2, 100),
        ],
    )

    assert result["status"] == "ok"
    rows = {(row["contract"], row["direction"]): row for row in result["items"]}
    assert rows[("i2609", "买")]["quantity"] == 2
    assert rows[("i2609", "买")]["average_price"] == pytest.approx(710)
    assert rows[("i2509", "卖")]["quantity"] == 4
    assert rows[("i2509", "卖")]["average_price"] == pytest.approx(908)
    assert rows[("i2701", "买")]["quantity"] == 2
    assert all(row["fact_status"] == "provisional" for row in rows.values() if row["contract"] != "i2409")
    assert rows[("i2409", "买")]["fact_status"] == "settlement_confirmed"


def test_infer_positions_removes_zero_quantity_rows_and_preserves_unaffected_values():
    result = trading_effective_facts.infer_positions_from_fills(
        [_baseline_row("i2609", "买", 1, 700), _baseline_row("i2509", "买", 2, 900)],
        [_fill(1, "i2609", "卖", "平", 1, 710)],
    )

    contracts = {(row["contract"], row["direction"]): row for row in result["items"]}
    assert ("i2609", "买") not in contracts
    assert contracts[("i2509", "买")]["fact_status"] == "settlement_confirmed"
    assert contracts[("i2509", "买")]["margin"] == 100


def test_infer_positions_returns_projection_error_without_clipping_negative_quantity():
    result = trading_effective_facts.infer_positions_from_fills(
        [_baseline_row("i2609", "买", 1, 700)],
        [_fill(1, "i2609", "卖", "平", 2, 710)],
    )

    assert result["status"] == "projection_error"
    assert result["items"] == []
    assert result["reference_items"][0]["quantity"] == 1
    assert result["warnings"]


def test_infer_positions_rejects_incomplete_fill_without_guessing():
    result = trading_effective_facts.infer_positions_from_fills(
        [_baseline_row("i2609", "买", 1, 700)],
        [_fill(1, "i2609", "卖", "平", 1, None)],
    )

    assert result["status"] == "projection_error"
    assert "价格" in result["warnings"][0]


def test_infer_positions_orders_night_session_before_day_session_on_same_trade_date():
    result = trading_effective_facts.infer_positions_from_fills(
        [],
        [
            _fill(
                1,
                "i2609",
                "买",
                "开",
                1,
                700,
                trade_time="21:30:00",
            ),
            _fill(
                2,
                "i2609",
                "卖",
                "平",
                1,
                710,
                trade_time="09:30:00",
            ),
        ],
    )

    assert result["status"] == "ok"
    assert result["items"] == []


def test_effective_positions_use_latest_settlement_baseline_and_provisional_fills(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [
            _baseline_row("i2609", "买", 2, 700),
            _baseline_row("i2509", "卖", 3, 900),
        ],
    )
    for fill_id, contract, side, open_close, quantity, price in [
        (1, "i2609", "买", "开", 1, 730),
        (2, "i2609", "卖", "平", 1, 740),
        (3, "i2509", "卖", "开", 2, 920),
        (4, "i2509", "买", "平", 1, 930),
    ]:
        insert_wh6_fill(
            account,
            event_key=f"tradeid:position-{fill_id}",
            trade_id=f"position-{fill_id}",
            trade_date="2026-09-01",
            contract=contract,
            asset_type="future",
            side=side,
            open_close=open_close,
            quantity=quantity,
            price=str(price),
        )

    with db.connect() as conn:
        result = trading_effective_facts.query_effective_positions(
            conn.cursor(), effective_filters(), now=datetime(2026, 9, 1, 9, 1, tzinfo=timezone.utc)
        )

    rows = {(row["contract"], row["direction"]): row for row in result["items"]}
    assert result["baseline_snapshot_date"] == "20260831"
    assert rows[("i2609", "买")]["quantity"] == 2
    assert rows[("i2609", "买")]["average_price"] == pytest.approx(710)
    assert rows[("i2509", "卖")]["quantity"] == 4
    assert rows[("i2509", "卖")]["average_price"] == pytest.approx(908)
    assert all(row["fact_status"] == "provisional" for row in rows.values())


def test_zero_quantity_settlement_batch_is_a_valid_empty_baseline(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(account, "20260831", [])
    insert_wh6_fill(
        account,
        event_key="tradeid:empty-baseline",
        trade_id="empty-baseline",
        trade_date="2026-09-01",
        contract="i2701",
        quantity=2,
        price="100",
    )

    with db.connect() as conn:
        result = trading_effective_facts.query_effective_positions(
            conn.cursor(), effective_filters()
        )

    assert result["baseline_snapshot_date"] == "20260831"
    assert result["items"][0]["contract"] == "i2701"
    assert result["items"][0]["quantity"] == 2


def test_no_settlement_baseline_does_not_guess_current_position_from_fills(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    insert_wh6_fill(
        account,
        event_key="tradeid:no-baseline",
        trade_id="no-baseline",
        trade_date="2026-09-01",
        contract="i2701",
    )

    with db.connect() as conn:
        result = trading_effective_facts.query_effective_positions(
            conn.cursor(), effective_filters()
        )

    assert result["freshness_status"] == "unavailable"
    assert result["items"] == []


def test_effective_positions_uses_postgres_boolean_expression(monkeypatch):
    class Result:
        def __init__(self, rows=()):
            self.rows = list(rows)

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

    def postgres_strict_exec(_cursor, sql, _params=()):
        normalized = " ".join(sql.split())
        if "complete = 1" in normalized:
            raise AssertionError("PostgreSQL does not compare boolean columns to integers")
        if normalized.startswith("SELECT account_id FROM trading_import_batches"):
            return Result([{"account_id": 1}])
        return Result()

    monkeypatch.setattr(trading_effective_facts.db, "_exec", postgres_strict_exec)

    result = trading_effective_facts.query_effective_positions(
        None,
        effective_filters(),
    )

    assert result["freshness_status"] == "unavailable"


def test_effective_trades_does_not_send_negative_postgres_limit(monkeypatch):
    class Result:
        def fetchall(self):
            return []

        def fetchone(self):
            return {
                "record_count": 0,
                "quantity": 0,
                "fee": 0,
                "fact_close_pnl": 0,
            }

    def postgres_strict_exec(_cursor, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM trading_trade_facts tf" in normalized and params == (-1, 0):
            raise AssertionError("PostgreSQL does not accept LIMIT -1")
        return Result()

    monkeypatch.setattr(trading_effective_facts.db, "_exec", postgres_strict_exec)

    result = trading_effective_facts.query_effective_trades(
        None,
        effective_filters(),
    )

    assert result["total_items"] == 0


def test_first_trade_page_does_not_materialize_entire_settlement_history(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    batch_id = insert_batch(account, "2026-05-01", "2026-05-31", "active", "monthly")
    for index in range(40):
        insert_settlement_trade(
            account,
            batch_id,
            identity_key=f"bounded-page-{index}",
            transaction_no=str(index + 1),
            trade_date="2026-05-10",
            price=700 + index,
            fee=1,
        )

    original_exec = trading_effective_facts.db._exec

    class BoundedSettlementResult:
        def __init__(self, result):
            self._result = result

        def fetchall(self):
            rows = self._result.fetchall()
            if len(rows) > 20:
                raise AssertionError("first page materialized more than one page of settlement rows")
            return rows

        def fetchone(self):
            return self._result.fetchone()

    def bounded_exec(cursor, sql, params=None):
        result = original_exec(cursor, sql, params)
        normalized = " ".join(sql.split())
        if "SELECT tf.*" in normalized and "FROM trading_trade_facts tf" in normalized:
            return BoundedSettlementResult(result)
        return result

    monkeypatch.setattr(trading_effective_facts.db, "_exec", bounded_exec)
    with db.connect() as conn:
        result = trading_effective_facts.query_effective_trades(
            conn.cursor(), effective_filters(page=1, page_size=20)
        )

    assert result["total_items"] == 40
    assert len(result["items"]) == 20


def test_complete_wh6_snapshot_can_supply_current_position_without_settlement_baseline(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_wh6_snapshot(
        account,
        trade_date="20260902",
        rows=[
            {
                "contract": "i2701",
                "asset_type": "future",
                "exchange": "DCE",
                "direction": "long",
                "quantity": 2,
                "average_price": "720",
            }
        ],
    )

    with db.connect() as conn:
        result = trading_effective_facts.query_effective_positions(
            conn.cursor(), effective_filters()
        )

    assert result["baseline_snapshot_date"] is None
    assert result["formation_method"] == "wh6_snapshot"
    assert result["items"][0]["contract"] == "i2701"
    assert result["items"][0]["quantity"] == 2
    assert result["items"][0]["fact_status"] == "provisional"


def test_overclose_returns_projection_error_and_confirmed_baseline(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [_baseline_row("i2609", "买", 1, 700)],
    )
    insert_wh6_fill(
        account,
        event_key="tradeid:overclose",
        trade_id="overclose",
        trade_date="2026-09-01",
        contract="i2609",
        side="卖",
        open_close="平",
        quantity=2,
    )

    with db.connect() as conn:
        result = trading_effective_facts.query_effective_positions(
            conn.cursor(), effective_filters()
        )

    assert result["data_status"] == "projection_error"
    assert result["items"] == []
    assert result["summary"]["quantity"] is None
    assert result["position_status"] == "unavailable"
    assert result["warnings"]


def test_newer_complete_wh6_snapshot_wins_without_replaying_fills(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [_baseline_row("i2609", "买", 1, 700)],
    )
    insert_wh6_fill(
        account,
        event_key="tradeid:after-baseline",
        trade_id="after-baseline",
        trade_date="2026-09-01",
        contract="i2609",
        quantity=2,
        price="730",
    )
    _insert_wh6_snapshot(
        account,
        trade_date="20260902",
        rows=[{"contract": "i2609", "asset_type": "future", "exchange": "DCE", "direction": "long", "quantity": 5, "average_price": "720"}],
    )

    with db.connect() as conn:
        result = trading_effective_facts.query_effective_positions(
            conn.cursor(), effective_filters()
        )

    assert result["formation_method"] == "wh6_snapshot"
    assert result["items"][0]["quantity"] == 5
    assert result["items"][0]["fact_status"] == "provisional"


def test_persistent_snapshot_conflict_and_stale_device_are_visible(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [_baseline_row("i2609", "买", 1, 700)],
    )
    _insert_wh6_snapshot(
        account,
        trade_date="20260902",
        rows=[{"contract": "i2609", "asset_type": "future", "exchange": "DCE", "direction": "long", "quantity": 5, "average_price": "720"}],
        conflict_status="persistent",
    )
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO trading_collector_devices
                (account_id, environment, device_name, client_version, fingerprint,
                 token_hash, status, last_seen_at)
            VALUES (?, 'staging', 'test-device', '0.3.2', 'fingerprint', 'token-hash', 'active', ?)
            """,
            (account, "2026-09-01T08:00:00+00:00"),
        )
        conn.commit()
        result = trading_effective_facts.query_effective_positions(
            conn.cursor(),
            effective_filters(),
            now=datetime(2026, 9, 2, 9, 1, tzinfo=timezone.utc),
        )

    assert result["freshness_status"] == "conflict"
    assert result["items"][0]["quantity"] == 1
    assert result["age_seconds"] >= 86400


def test_management_query_fact_rows_uses_effective_position_projection(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [_baseline_row("i2609", "买", 1, 700)],
    )
    insert_wh6_fill(
        account,
        event_key="tradeid:management-position",
        trade_id="management-position",
        trade_date="2026-09-01",
        contract="i2609",
        asset_type="future",
        quantity=1,
        price="730",
    )

    result = trading_management.query_fact_rows(
        "positions",
        trading_management.FactFilters(page=1, page_size=20),
    )

    assert result["formation_method"] == "inferred_from_settlement_and_fills"
    assert result["items"][0]["fact_status"] == "provisional"
    assert result["items"][0]["quantity"] == 2


def test_fact_position_valuation_uses_latest_trade_price_for_provisional_rows(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [_baseline_row("i2609", "买", 2, 700)],
    )
    insert_wh6_fill(
        account,
        event_key="tradeid:live-valuation",
        trade_id="live-valuation",
        trade_date="2026-09-01",
        contract="i2609",
        asset_type="future",
        quantity=1,
        price="730",
    )
    monkeypatch.setattr(
        trading_management,
        "get_quote_snapshots",
        lambda requests: {
            request.contract: trading_management.QuoteSnapshot(
                last_price=750,
                settlement_price=680,
                multiplier=100,
                market_time="2026-09-01T09:05:00+08:00",
                market_data_status="live",
            )
            for request in requests
        },
    )

    result = trading_management.query_fact_position_valuation(
        trading_management.FactFilters(page=1, page_size=20)
    )

    row = result["items"][0]
    assert row["fact_status"] == "provisional"
    assert row["quantity"] == 3
    assert row["average_price"] == pytest.approx(710)
    assert row["valuation_price"] == 750
    assert row["valuation_source"] == "last_trade"
    assert row["market_time"] == "2026-09-01 09:05:00"
    assert row["floating_pnl"] == pytest.approx(12000)
    assert result["summary"]["floating_pnl"] == pytest.approx(12000)
    assert result["summary"]["floating_pnl_status"] == "live"


def test_fact_position_valuation_keeps_stale_last_trade_pnl_visible(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [_baseline_row("i2609", "买", 1, 700)],
    )
    monkeypatch.setattr(
        trading_management,
        "get_quote_snapshots",
        lambda requests: {
            request.contract: trading_management.QuoteSnapshot(
                last_price=750,
                multiplier=100,
                market_data_status="stale",
                market_data_message="行情读取失败，沿用上次行情",
            )
            for request in requests
        },
    )

    result = trading_management.query_fact_position_valuation(
        trading_management.FactFilters(page=1, page_size=20)
    )

    row = result["items"][0]
    assert row["valuation_price"] == 750
    assert row["floating_pnl"] == pytest.approx(5000)
    assert row["floating_pnl_status"] == "stale"
    assert result["summary"]["floating_pnl"] == pytest.approx(5000)
    assert result["summary"]["floating_pnl_status"] == "stale"


def test_fact_position_valuation_does_not_fallback_to_quotes_or_settlement(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    _insert_settlement_position_batch(
        account,
        "20260831",
        [_baseline_row("i2609", "买", 1, 700)],
    )
    monkeypatch.setattr(
        trading_management,
        "get_quote_snapshots",
        lambda requests: {
            request.contract: trading_management.QuoteSnapshot(
                bid_price=749,
                ask_price=751,
                settlement_price=680,
                multiplier=100,
                market_data_status="provider_error",
            )
            for request in requests
        },
    )

    result = trading_management.query_fact_position_valuation(
        trading_management.FactFilters(page=1, page_size=20)
    )

    row = result["items"][0]
    assert row["valuation_price"] is None
    assert row["floating_pnl"] is None
    assert row["valuation_status"] == "unavailable"
    assert result["summary"]["floating_pnl"] is None
    assert result["summary"]["floating_pnl_status"] == "unavailable"


def test_overview_excludes_provisional_trade_facts_from_formal_totals(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    account = account_id()
    insert_wh6_fill(
        account,
        event_key="tradeid:overview-provisional",
        trade_id="overview-provisional",
        trade_date="2026-09-01",
    )

    overview = trading_management.build_overview(
        trading_management.FactFilters(page=1, page_size=20)
    )

    assert overview["trades"]["record_count"] == 0
    assert overview["trades"]["contains_provisional"] is False
