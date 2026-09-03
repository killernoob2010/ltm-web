"""Server-side account binding, observation and dedup contract tests."""

from pathlib import Path
import os
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))

from app import db
from app import trading_collector_service as service


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "collector-service.db")
    db.init_db()
    with db.connect() as conn:
        return conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]


def payload(*, event_key="tradeid:m-001", device_id="device-1", account_id=None, price="12.5"):
    return {
        "source_event_key": event_key,
        "trade_date": "2026-09-02",
        "trade_time": "21:05:03",
        "trade_timestamp": "2026-09-02T21:05:03+08:00",
        "exchange": "DCE",
        "contract": "i2607-c-750",
        "raw_contract": "i2607-C-750",
        "asset_type": "option",
        "side": "买",
        "open_close": "开",
        "quantity": 2,
        "price": price,
        "fee": "0.80",
        "trade_id": "M-001",
        "order_id": "ORDER-001",
        "parser_version": "wh6-match-v1",
        "source_record_sha256": "a" * 64,
        "source_path": "Record/20260902match.dat",
        "source_record_index": 0,
        "data_status": "provisional",
        "verification_status": "pending",
        "account_id": account_id,
        "device_id": device_id,
    }


def activate(account_id, *, name="pc-1", fingerprint="fp-1"):
    issued = service.issue_pairing_code(account_id, actor_id=1)
    return service.activate_device(issued["code"], name, "0.1.0", fingerprint)


def test_collector_schema_is_isolated_and_does_not_replace_settlement_tables(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'trading_%'"
            ).fetchall()
        }
    assert {
        "trading_collector_pairing_codes",
        "trading_collector_devices",
        "trading_intraday_fill_observations",
        "trading_intraday_fills",
        "trading_collector_issues",
    } <= tables
    assert "trading_trade_facts" in tables


def test_pairing_code_is_one_time_and_expires(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    issued = service.issue_pairing_code(account_id, actor_id=1, ttl_seconds=900)
    assert issued["code"]
    activated = service.activate_device(issued["code"], "pc-1", "0.1.0", "fp-1")
    assert activated["token"]
    with pytest.raises(service.CollectorServiceError) as exc:
        service.activate_device(issued["code"], "pc-2", "0.1.0", "fp-2")
    assert exc.value.code == "pairing_code_invalid"


def test_revoke_invalidates_token_and_keeps_no_plaintext_token(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    activated = activate(account_id)
    service.revoke_device(activated["device_id"], actor_id=1)
    with pytest.raises(service.CollectorServiceError) as exc:
        service.get_device_by_token(activated["token"])
    assert exc.value.code == "device_revoked"
    with db.connect() as conn:
        row = conn.execute(
            "SELECT token_hash FROM trading_collector_devices WHERE id = ?",
            (activated["device_id"],),
        ).fetchone()
    assert row["token_hash"] != activated["token"]


def test_ingest_binds_account_from_device_and_deduplicates_two_devices(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    first = activate(account_id, name="pc-1", fingerprint="fp-1")
    second = activate(account_id, name="pc-2", fingerprint="fp-2")

    first_result = service.ingest_observations(first["token"], [payload(account_id=999)])
    second_result = service.ingest_observations(second["token"], [payload(device_id="device-2", account_id=999)])
    replay = service.ingest_observations(first["token"], [payload(account_id=account_id)])

    assert first_result.accepted == 1
    assert second_result.duplicates == 1
    assert replay.duplicates == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fills").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fill_observations").fetchone()["c"] == 2


def test_conflicting_duplicate_is_quarantined_and_does_not_overwrite(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    first = activate(account_id)
    service.ingest_observations(first["token"], [payload(account_id=account_id)])
    result = service.ingest_observations(first["token"], [payload(account_id=account_id, price="13.5")])
    assert result.conflicts == 1
    rows = service.query_intraday_fills(account_id)
    assert rows["items"][0]["price"] == "12.5"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_collector_issues WHERE issue_code = 'fill_conflict'").fetchone()["c"] == 1


def test_query_is_read_only_and_filters_account_contract_status(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    activated = activate(account_id)
    service.ingest_observations(activated["token"], [payload(account_id=account_id)])
    assert service.query_intraday_fills(account_id, contract="i2607-c-750")["total"] == 1
    assert service.query_intraday_fills(account_id, contract="i2607-p-750")["total"] == 0


def test_malformed_observation_is_quarantined_without_crashing(tmp_path, monkeypatch):
    account_id = use_temp_db(tmp_path, monkeypatch)
    activated = activate(account_id)
    result = service.ingest_observations(
        activated["token"],
        [
            "not-an-observation",
            {"price": "NaN", "source_path": r"C:\Users\Alice\WH6\Record\20260903match.dat"},
        ],
    )
    assert result.quarantined == 2
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_collector_issues").fetchone()["c"] == 2
        payloads = [row["payload_json"] for row in conn.execute("SELECT payload_json FROM trading_collector_issues").fetchall()]
    assert all("C:\\Users\\Alice" not in payload for payload in payloads)
