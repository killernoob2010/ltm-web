"""V2 deterministic fill/position ingest and option-only read contract tests."""

from datetime import datetime
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db
from app import trading_collector_service as service
from test_trading_collector_service import activate, payload, use_temp_db


def position_payload(
    *,
    quantity=3,
    snapshot_key="snapshot:2026-09-03T09:05:00+08:00",
    snapshot_at="2026-09-03T09:05:00+08:00",
    source_hash="c" * 64,
    contract="i2607-C-750",
    asset_type="option",
    rows=None,
):
    return {
        "source_snapshot_key": snapshot_key,
        "trade_date": "2026-09-03",
        "snapshot_time": "09:05:00",
        "snapshot_timestamp": snapshot_at,
        "complete": True,
        "rows": rows if rows is not None else [{
            "contract": contract,
            "raw_contract": contract,
            "asset_type": asset_type,
            "exchange": "DCE",
            "direction": "long",
            "quantity": quantity,
            "today_quantity": 1,
            "yesterday_quantity": max(0, quantity - 1),
            "average_price": "12.50",
            "hedge_flag": "投机",
        }],
        "source_snapshot_sha256": source_hash,
        "parser_version": "wh6-position-json-v1",
        "source_path": r"C:\Users\Alice\WH6\Record\20260903position.dat",
        "data_status": "provisional",
        "verification_status": "pending",
        "account_id": 999,
    }


def test_collector_schema_adds_isolated_position_tables(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'trading_%'"
            ).fetchall()
        }
    assert {
        "trading_intraday_position_observations",
        "trading_intraday_position_snapshots",
        "trading_intraday_position_rows",
    } <= tables
    assert "trading_trade_facts" in tables


def test_futures_are_stored_but_option_volume_is_option_only(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    device = activate(account_id)
    future = payload(event_key="tradeid:future-001", account_id=999)
    future.update({
        "contract": "i2607",
        "raw_contract": "i2607",
        "asset_type": "future",
        "trade_id": "future-001",
        "source_record_sha256": "d" * 64,
    })
    option = payload(event_key="tradeid:option-001", account_id=999)
    option.update({"trade_id": "option-001", "source_record_sha256": "e" * 64})
    result = service.ingest_observations(device["token"], [future, option])
    assert result.accepted == 2
    assert {item["asset_type"] for item in service.query_intraday_fills(account_id)["items"]} == {"future", "option"}
    volume = service.query_option_volume(account_id, trade_date="2026-09-02")
    assert volume["total_quantity"] == 2
    assert all(item["asset_type"] == "option" for item in volume["items"])


def test_two_devices_same_complete_snapshot_is_one_snapshot_not_sum(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    first = activate(account_id, name="pc-a", fingerprint="fp-a")
    second = activate(account_id, name="pc-b", fingerprint="fp-b")
    snapshot = position_payload(quantity=3)
    assert service.ingest_observations(first["token"], [], [snapshot]).positions_accepted == 1
    result = service.ingest_observations(second["token"], [], [snapshot])
    assert result.position_duplicates == 1
    current = service.query_current_option_positions(
        account_id,
        now=datetime.fromisoformat("2026-09-03T09:05:20+08:00"),
    )
    assert current["items"][0]["quantity"] == 3
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_position_snapshots").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_position_observations").fetchone()["c"] == 2


def test_position_conflict_is_transient_then_persistent_without_overwriting_rows(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    first = activate(account_id, name="pc-a", fingerprint="fp-a")
    second = activate(account_id, name="pc-b", fingerprint="fp-b")
    current_now = ["2026-09-03T09:05:00+08:00"]
    monkeypatch.setattr(service, "_now", lambda: current_now[0])
    original = position_payload(quantity=3)
    changed = position_payload(quantity=5, source_hash="f" * 64)
    service.ingest_observations(first["token"], [], [original])
    current_now[0] = "2026-09-03T09:05:05+08:00"
    result = service.ingest_observations(second["token"], [], [changed])
    assert result.position_conflicts == 1
    transient = service.query_current_option_positions(
        account_id,
        now=datetime.fromisoformat("2026-09-03T09:05:20+08:00"),
    )
    assert transient["source_status"] == "multi_device_conflict"
    assert transient["conflict_age_seconds"] < 30
    assert transient["items"][0]["quantity"] == 3

    persistent = service.query_current_option_positions(
        account_id,
        now=datetime.fromisoformat("2026-09-03T09:05:40+08:00"),
    )
    assert persistent["source_status"] == "multi_device_conflict"
    assert persistent["conflict_age_seconds"] >= 30
    assert persistent["items"][0]["quantity"] == 3


def test_current_option_positions_exclude_futures_and_mark_expired(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    device = activate(account_id)
    snapshot = position_payload(
        snapshot_key="snapshot:2026-09-03T08:00:00+08:00",
        snapshot_at="2026-09-03T08:00:00+08:00",
        rows=[
            position_payload()["rows"][0],
            {
                "contract": "i2607",
                "raw_contract": "i2607",
                "asset_type": "future",
                "exchange": "DCE",
                "direction": "long",
                "quantity": 7,
            },
        ],
    )
    service.ingest_observations(device["token"], [], [snapshot])
    current = service.query_current_option_positions(
        account_id,
        now=datetime.fromisoformat("2026-09-03T08:01:00+08:00"),
    )
    assert len(current["items"]) == 1
    assert current["items"][0]["asset_type"] == "option"
    assert current["is_expired"] is True
    assert current["source_status"] == "expired"


def test_malformed_position_is_quarantined_and_account_override_is_ignored(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    device = activate(account_id)
    malformed = position_payload(rows=[{**position_payload()["rows"][0], "contract": "stock-600000"}])
    result = service.ingest_observations(device["token"], [], [malformed])
    assert result.position_quarantined == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_position_snapshots").fetchone()["c"] == 0
        issue = conn.execute(
            "SELECT issue_code, payload_json FROM trading_collector_issues WHERE issue_code = 'invalid_position_snapshot'"
        ).fetchone()
    assert issue is not None
    assert '"account_id": 999' in issue["payload_json"]
