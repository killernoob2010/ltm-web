from datetime import date

import pytest
from fastapi import HTTPException

from app import db
from app.trading_agent import market_data, store
from test_store import queued


def args(**overrides):
    values = {
        "dataset": "iron_ore_basis",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 10),
        "ports": [],
        "products": [],
        "metrics": ["basis"],
    }
    values.update(overrides)
    return market_data.MarketSeriesArgs(**values)


def grant_display(uid):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO module_permissions(user_id,module_code,can_view,can_edit) VALUES (?, 'data_visualization_chart', 1, 0)",
            (uid,),
        )


def insert_basis(cur, **overrides):
    row = {
        "business_key": "agent-market|2026-09-05|日照港|PB粉",
        "business_date": "2026-09-05",
        "business_week": 36,
        "week_label": "2026 W36",
        "business_year": 2026,
        "port": "日照港",
        "product": "PB粉",
        "wet_spot_price": 680.0,
        "quality_adjustment": 4.0,
        "brand_adjustment": 15.0,
        "standardized_spot_price": 719.5,
        "futures_series": "I0",
        "futures_close": 700.0,
        "basis": 123.45,
        "data_status": "有效",
        "rule_version": "test-rule",
        "parameter_version": "test-parameters",
        "source_workbook_name": "test.xlsx",
        "source_workbook_sha256": "a" * 64,
    }
    row.update(overrides)
    columns = list(row)
    db._exec(cur, f"INSERT INTO iron_ore_basis_results ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", tuple(row[column] for column in columns))


def test_market_query_is_bounded():
    with pytest.raises(ValueError):
        args(start_date="2020-01-01", end_date="2026-01-01")
    with pytest.raises(ValueError):
        args(dataset="users", metrics=["password_hash"])
    with pytest.raises(ValueError):
        args(ports=[f"港口{i}" for i in range(9)])
    with pytest.raises(ValueError):
        args(metrics=["basis", "password_hash"])


def test_market_query_requires_agent_facts_and_display_permissions(queued):
    uid, _, task_id = queued
    principal = store.principal_for_task(store.claim_next("market-permission-worker"))
    with pytest.raises(HTTPException) as error:
        market_data.capture_market_series(principal, args())
    assert error.value.status_code == 403

    grant_display(uid)
    result = market_data.capture_market_series(principal, args())
    assert result.payload["dataset"] == "iron_ore_basis"

    with db.connect() as conn:
        conn.execute("UPDATE module_permissions SET can_view=0 WHERE user_id=? AND module_code='data_visualization_chart'", (uid,))
    with pytest.raises(HTTPException) as error:
        market_data.capture_market_series(principal, args())
    assert error.value.status_code == 403


def test_market_query_preserves_source_values_and_invalid_rows(queued):
    uid, _, _ = queued
    grant_display(uid)
    task = store.claim_next("market-source-worker")
    principal = store.principal_for_task(task)
    with db.connect() as conn:
        insert_basis(conn.cursor())
        insert_basis(conn.cursor(), business_key="agent-market|2026-09-06|日照港|PB粉", business_date="2026-09-06", basis=999.0, data_status="异常")

    result = market_data.capture_market_series(principal, args(end_date=date(2026, 9, 6), metrics=["basis", "futures_close", "wet_spot_price"]))
    assert result.payload["source"] == "iron_ore_basis_results"
    assert result.payload["count"] == 2
    assert result.payload["latest_observed_date"] == "2026-09-06"
    assert result.data_as_of is None
    assert result.payload["missing_count"] == 1
    assert result.payload["field_catalog"]["basis"]["unit"] == "元/标准化吨"
    saved = store.load_result(principal, result.result_ref)
    assert saved.rows[0]["basis"] == 123.45
    assert saved.rows[1]["basis"] == 999.0
    assert saved.rows[1]["data_status"] == "异常"


def test_market_query_fails_closed_on_ambiguous_source_versions(queued):
    uid, _, _ = queued
    grant_display(uid)
    task = store.claim_next("market-duplicate-worker")
    principal = store.principal_for_task(task)
    with db.connect() as conn:
        insert_basis(conn.cursor())
        insert_basis(
            conn.cursor(),
            business_key="agent-market|2026-09-05|日照港|PB粉|revised",
            rule_version="revised-rule",
        )

    with pytest.raises(ValueError, match="选择语义不明确"):
        market_data.capture_market_series(principal, args())


def test_market_query_stops_before_persisting_oversized_result(queued, monkeypatch):
    uid, _, _ = queued
    grant_display(uid)
    task = store.claim_next("market-limit-worker")
    principal = store.principal_for_task(task)
    monkeypatch.setattr(
        market_data,
        "_read_rows",
        lambda query: [{"business_date": "2026-09-05", "port": "日照港", "product": "PB粉", "basis": 1.0, "data_status": "有效"}]
        * (market_data.MAX_ROWS + 1),
    )
    monkeypatch.setattr(market_data, "_duplicate_keys", lambda rows: set())

    result = market_data.capture_market_series(principal, args())

    assert result.status == "limit_exceeded"
    assert result.result_ref is None
    assert result.payload["count"] == market_data.MAX_ROWS + 1
