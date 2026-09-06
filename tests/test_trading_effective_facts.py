"""Tests for the single effective settlement/provisional trade projection."""

from pathlib import Path
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
