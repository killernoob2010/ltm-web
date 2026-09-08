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


def dated_payload(event_key, trade_date, *, price="12.5", trade_id="M-001"):
    data = payload(event_key=event_key, price=price)
    data.update(
        {
            "trade_date": trade_date,
            "trade_timestamp": trade_date + "T21:05:03+08:00",
            "trade_id": trade_id,
        }
    )
    return data


def activate(account_id, *, name="pc-1", fingerprint="fp-1"):
    issued = service.issue_pairing_code(account_id, actor_id=1)
    return service.activate_device(issued["code"], name, "0.3.0", fingerprint)


def active_monthly(account_id, start="20260801", end="20260831"):
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, status, statement_type,
                 source_priority)
            VALUES (?, ?, ?, 'active', 'monthly', 200)
            """,
            (account_id, start, end),
        )


def active_daily(account_id, start="20260901", end="20260904"):
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, status, statement_type,
                 source_priority)
            VALUES (?, ?, ?, 'active', 'daily', 100)
            """,
            (account_id, start, end),
        )


def insert_intraday_rows(account_id, count, *, data_status="provisional", asset_type="option"):
    with db.connect() as conn:
        cur = conn.cursor()
        for index in range(count):
            contract = "i2607-c-750" if asset_type == "option" else "i2607"
            db._exec(
                cur,
                """
                INSERT INTO trading_intraday_fills
                    (account_id, source_event_key, trade_date, trade_time,
                     trade_timestamp, exchange, contract, raw_contract, asset_type,
                     side, open_close, quantity, price, parser_version,
                     source_record_sha256, canonical_hash, data_status,
                     verification_status)
                VALUES (?, ?, '2026-09-04', ?, '2026-09-04T09:00:00+08:00',
                        'DCE', ?, ?, ?, '买', '开', 1, '785', 'test', ?, ?,
                        ?, 'pending')
                """,
                (
                    account_id,
                    "tradeid:%s-%s-%04d" % (data_status, asset_type, index),
                    "09:%02d:%02d" % (index // 60, index % 60),
                    contract,
                    contract,
                    asset_type,
                    ("a" * 63) + str(index % 10),
                    ("b" * 63) + str(index % 10),
                    data_status,
                ),
            )


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
    activated = service.activate_device(issued["code"], "pc-1", "0.3.0", "fp-1")
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


def test_ingest_returns_terminal_result_for_every_event(tmp_path, monkeypatch):
    account = use_temp_db(tmp_path, monkeypatch)
    device = activate(account)
    active_monthly(account)
    august = dated_payload("tradeid:august-1", "2026-08-12", trade_id="AUG-1")
    september = dated_payload("tradeid:september-1", "2026-09-04", trade_id="SEP-1")
    malformed = {"source_event_key": "bad-event", "price": "NaN"}

    result = service.ingest_observations(
        device["token"], [august, september, malformed]
    ).to_dict()

    assert result["fill_results"] == [
        {"event_key": "tradeid:august-1", "status": "settlement_covered"},
        {"event_key": "tradeid:september-1", "status": "accepted"},
        {"event_key": "bad-event", "status": "quarantined"},
    ]
    assert result["accepted"] == 1
    assert result["quarantined"] == 1
    with db.connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM trading_intraday_fills"
        ).fetchone()["c"] == 1
        assert conn.execute(
            "SELECT status FROM trading_intraday_fill_observations"
        ).fetchone() is not None


def test_ingest_receipts_distinguish_duplicate_and_conflict(tmp_path, monkeypatch):
    account = use_temp_db(tmp_path, monkeypatch)
    device = activate(account)
    event = dated_payload("tradeid:receipt-1", "2026-09-04")
    assert service.ingest_observations(device["token"], [event]).to_dict()["fill_results"] == [
        {"event_key": "tradeid:receipt-1", "status": "accepted"}
    ]
    assert service.ingest_observations(device["token"], [event]).to_dict()["fill_results"] == [
        {"event_key": "tradeid:receipt-1", "status": "duplicate"}
    ]
    conflicting = dict(event, price="13.5")
    assert service.ingest_observations(device["token"], [conflicting]).to_dict()["fill_results"] == [
        {"event_key": "tradeid:receipt-1", "status": "conflict"}
    ]


def test_server_guard_rechecks_monthly_policy_after_client_fetch(tmp_path, monkeypatch):
    account = use_temp_db(tmp_path, monkeypatch)
    device = activate(account)
    assert service.get_device_collection_policy(device["device_id"])["closed_ranges"] == []
    active_monthly(account)

    result = service.ingest_observations(
        device["token"], [dated_payload("tradeid:race-1", "2026-08-12")]
    ).to_dict()

    assert result["fill_results"] == [
        {"event_key": "tradeid:race-1", "status": "settlement_covered"}
    ]


def test_ingest_rejects_more_than_five_hundred_fill_rows(tmp_path, monkeypatch):
    account = use_temp_db(tmp_path, monkeypatch)
    device = activate(account)
    with pytest.raises(service.CollectorServiceError) as exc:
        service.ingest_observations(device["token"], [payload()] * 501)
    assert exc.value.code == "batch_too_large"


def test_fill_query_uses_stable_server_pagination(tmp_path, monkeypatch):
    account = use_temp_db(tmp_path, monkeypatch)
    device = activate(account)
    events = [
        dated_payload("tradeid:page-%02d" % index, "2026-09-04", trade_id="P-%02d" % index)
        for index in range(45)
    ]
    result = service.ingest_observations(device["token"], events)
    assert result.accepted == 45

    first = service.query_intraday_fills(account, page=1, page_size=20, asset_type="option")
    third = service.query_intraday_fills(account, page=3, page_size=20, asset_type="option")

    assert (first["total_items"], first["total_pages"], len(first["items"])) == (45, 3, 20)
    assert (third["page"], len(third["items"])) == (3, 5)
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in third["items"]}
    )


def test_fill_query_rejects_unsupported_page_size(tmp_path, monkeypatch):
    account = use_temp_db(tmp_path, monkeypatch)
    with pytest.raises(service.CollectorServiceError) as exc:
        service.query_intraday_fills(account, page_size=30)
    assert exc.value.code == "invalid_page_size"


def test_option_volume_aggregates_all_eligible_rows_not_current_page(tmp_path, monkeypatch):
    account = use_temp_db(tmp_path, monkeypatch)
    insert_intraday_rows(account, 501)
    insert_intraday_rows(account, 1, data_status="settlement_covered")
    insert_intraday_rows(account, 1, data_status="settlement_conflict")
    insert_intraday_rows(account, 1, asset_type="future")

    result = service.query_option_volume(account, trade_date="2026-09-04")

    assert result["total_quantity"] == 501
    assert sum(result["by_contract"].values()) == 501
