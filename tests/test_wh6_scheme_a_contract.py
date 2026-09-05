"""First failing contract tests for the WH6 scheme A rollout."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from app import db
from app import trading_collector_reconciliation as reconciliation
from app import trading_collector_service as service
from test_wh6_collector_core import _account, _record, _write_match
from wh6_collector import cli
from wh6_collector.local_store import LocalOutbox
from wh6_collector.policy import CollectionPolicy


CAPABILITIES = [
    "monthly_collection_ranges_v1",
    "per_item_ingest_receipts_v1",
    "future_spread_v1",
    "positions_v2",
    "open_ended_upload_v1",
]


def scheme_a_policy_payload(*, revision="scheme-a-1"):
    return {
        "schema_version": 2,
        "environment": "staging",
        "history_start_date": "2026-09-01",
        "upload_ranges": [
            {"range_start": "2026-09-01", "range_end": "2026-09-04"},
        ],
        "closed_ranges": [
            {
                "month": "2026-06",
                "range_start": "2026-06-01",
                "range_end": "2026-06-30",
                "source_batch_id": 6,
            },
            {
                "month": "2026-07",
                "range_start": "2026-07-01",
                "range_end": "2026-07-31",
                "source_batch_id": 7,
            },
            {
                "month": "2026-08",
                "range_start": "2026-08-01",
                "range_end": "2026-08-31",
                "source_batch_id": 8,
            },
        ],
        "current_trade_date": "2026-09-04",
        "minimum_client_version": "0.3.0",
        "policy_revision": revision,
        "capabilities": CAPABILITIES,
        "generated_at": "2026-09-04T00:00:00+00:00",
    }


def _use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "scheme-a.db")
    db.init_db()
    with db.connect() as conn:
        return conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]


def _fill_payload(*, event_key, trade_id="M-001", source_path="Record/20260904match.dat", source_index=0, source_sha=None):
    return {
        "source_event_key": event_key,
        "trade_date": "2026-09-04",
        "trade_time": "09:31:02",
        "trade_timestamp": "2026-09-04T09:31:02+08:00",
        "exchange": "DCE",
        "contract": "i2607-c-750",
        "raw_contract": "i2607-C-750",
        "asset_type": "option",
        "side": "买",
        "open_close": "开",
        "quantity": 2,
        "price": "12.5",
        "fee": "0.80",
        "trade_id": trade_id,
        "order_id": "ORDER-001",
        "parser_version": "wh6-match-v2",
        "source_record_sha256": source_sha or ("a" * 64),
        "source_path": source_path,
        "source_record_index": source_index,
        "data_status": "provisional",
        "verification_status": "pending",
    }


def _activate(account_id, *, name, fingerprint):
    issued = service.issue_pairing_code(account_id, actor_id=1)
    return service.activate_device(issued["code"], name, "0.3.0", fingerprint)


def test_wh6_a001_pairing_code_and_cli_route_are_environment_bound(monkeypatch):
    route = getattr(cli, "resolve_collector_route", None)
    assert callable(route)
    assert route("LTM1-S-012345678901")["environment"] == "staging"
    assert route("LTM1-P-012345678901")["environment"] == "production"
    with pytest.raises(ValueError):
        route("LTM1-X-012345678901")

    parser = cli.build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }
    assert "--staging-url" not in option_strings
    assert "--environment" not in option_strings


def test_wh6_a001_config_cannot_claim_a_different_public_environment():
    with pytest.raises(ValueError):
        cli.CollectorConfig(
            collector_url="https://ltm-web-staging.onrender.com",
            environment="production",
            source_path="Record",
            account=_account(),
            device_token="device-token",
            data_dir="data",
        )
    with pytest.raises(ValueError):
        cli.CollectorConfig(
            collector_url="https://ltm-web-staging.onrender.com:444",
            source_path="Record",
            account=_account(),
            device_token="device-token",
            data_dir="data",
        )


def test_wh6_a002_policy_v2_keeps_closed_months_and_allows_dates_after_server_today():
    policy = CollectionPolicy.from_payload(scheme_a_policy_payload())
    assert policy.schema_version == 2
    assert policy.history_start_date == "2026-09-01"
    assert policy.is_closed("2026-06-15") is True
    assert policy.allows_upload("2026-06-15") is False
    assert policy.allows_upload("2026-09-03") is True
    assert policy.allows_upload("2026-09-05") is True
    assert policy.allows_upload("2026-09-07") is True
    assert policy.allows_upload("2026-08-31") is False


def test_wh6_a002_server_policy_exposes_active_month_closures_and_open_september(tmp_path, monkeypatch):
    account_id = _use_temp_db(tmp_path, monkeypatch)
    with db.connect() as conn:
        for start, end, batch_id in (
            ("20260601", "20260630", 6),
            ("20260701", "20260731", 7),
            ("20260801", "20260831", 8),
        ):
            db._exec(
                conn.cursor(),
                """
                INSERT INTO trading_import_batches
                    (id, account_id, range_start, range_end, status, statement_type,
                     source_priority)
                VALUES (?, ?, ?, ?, 'active', 'monthly', 200)
                """,
                (batch_id, account_id, start, end),
            )
    issued = service.issue_pairing_code(account_id, actor_id=1)
    device = service.activate_device(issued["code"], "pc-1", "0.3.0", "fp-1")
    policy = service.get_device_collection_policy(device["device_id"])
    assert policy["schema_version"] == 2
    assert policy["history_start_date"] == "2026-09-01"
    assert {item["month"] for item in policy["closed_ranges"]} == {"2026-06", "2026-07", "2026-08"}
    assert any(
        item["range_start"] <= "2026-09-04" <= item["range_end"]
        for item in policy["upload_ranges"]
    )
    assert "open_ended_upload_v1" in policy["capabilities"]
    with db.connect() as conn:
        cur = conn.cursor()
        assert reconciliation.is_date_uploadable(
            cur,
            account_id,
            "2026-09-07",
            as_of_date="2026-09-05",
        ) is True
        assert reconciliation.is_date_uploadable(
            cur,
            account_id,
            "2026-08-15",
            as_of_date="2026-09-05",
        ) is False


def test_wh6_a002_policy_does_not_skip_a_missing_month_and_advances_after_later_closures(tmp_path, monkeypatch):
    account_id = _use_temp_db(tmp_path, monkeypatch)

    def close_month(month, batch_id):
        year, month_number = (int(value) for value in month.split("-"))
        if month_number == 12:
            next_month = datetime(year + 1, 1, 1)
        else:
            next_month = datetime(year, month_number + 1, 1)
        end = (next_month.date().fromordinal(next_month.date().toordinal() - 1)).isoformat()
        with db.connect() as conn:
            db._exec(
                conn.cursor(),
                """
                INSERT INTO trading_import_batches
                    (id, account_id, range_start, range_end, status, statement_type,
                     source_priority)
                VALUES (?, ?, ?, ?, 'active', 'monthly', 200)
                """,
                (batch_id, account_id, month + "-01", end),
            )

    open_policy = reconciliation.build_collection_policy(account_id, as_of_date="2026-10-31")
    assert open_policy["history_start_date"] == "2026-09-01"
    assert open_policy["upload_ranges"] == [
        {"range_start": "2026-09-01", "range_end": "2026-10-31"}
    ]

    close_month("2026-10", 10)
    october_closed = reconciliation.build_collection_policy(account_id, as_of_date="2026-11-01")
    assert october_closed["history_start_date"] == "2026-09-01"
    assert {tuple(item.values()) for item in october_closed["upload_ranges"]} == {
        ("2026-09-01", "2026-09-30"),
        ("2026-11-01", "2026-11-01"),
    }

    close_month("2026-09", 9)
    both_closed = reconciliation.build_collection_policy(account_id, as_of_date="2026-11-01")
    assert both_closed["history_start_date"] == "2026-11-01"
    assert both_closed["upload_ranges"] == [
        {"range_start": "2026-11-01", "range_end": "2026-11-01"}
    ]


def test_wh6_a001_server_rejects_a_pairing_code_for_the_other_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("LTM_RUNTIME_ENVIRONMENT", "staging")
    account_id = _use_temp_db(tmp_path, monkeypatch)
    issued = service.issue_pairing_code(account_id, actor_id=1, environment="production")
    with pytest.raises(service.CollectorServiceError) as exc:
        service.activate_device(issued["code"], "pc-1", "0.3.0", "fp-1")
    assert exc.value.code == "environment_mismatch"


def test_wh6_a003_policy_failure_queues_current_day_but_does_not_upload_history_or_current(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    today = datetime.now().astimezone().strftime("%Y%m%d")
    _write_match(
        source_root / f"{today}match.dat",
        [_record(timestamp=f"{today[:4]}-{today[4:6]}-{today[6:]} 09:31:02", match_id="TODAY")],
        size=268,
    )
    _write_match(
        source_root / "20260815match.dat",
        [_record(timestamp="2026-08-15 09:31:02", match_id="HISTORY")],
        size=268,
    )
    uploaded = []
    config = cli.CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source_root),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        allow_weak_source=True,
    )

    def raise_offline():
        raise RuntimeError("policy offline")

    result = cli.run_once(
        config,
        upload=lambda token, items: uploaded.extend(items) or {
            "accepted": len(items),
            "fill_results": [
                {"event_key": item["source_event_key"], "status": "accepted"}
                for item in items
            ],
        },
        policy_fetch=raise_offline,
    )
    assert result["state"] == "policy_unavailable_history_paused"
    assert uploaded == []
    assert LocalOutbox(Path(config.data_dir) / "collector.sqlite3").status()["pending"] == 1


def test_wh6_a003_mixed_source_file_only_uploads_whitelisted_trade_dates(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    today = datetime.now().astimezone().strftime("%Y%m%d")
    _write_match(
        source_root / f"{today}match.dat",
        [
            _record(timestamp=f"{today[:4]}-{today[4:6]}-{today[6:]} 09:31:02", match_id="TODAY"),
            _record(timestamp="2026-08-15 09:32:02", match_id="CLOSED"),
        ],
        size=268,
    )
    uploaded = []
    config = cli.CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source_root),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        allow_weak_source=True,
    )

    result = cli.run_once(
        config,
        upload=lambda token, items: uploaded.extend(items) or {
            "accepted": len(items),
            "fill_results": [
                {"event_key": item["source_event_key"], "status": "accepted"}
                for item in items
            ],
        },
        policy_fetch=scheme_a_policy_payload,
    )

    assert result["state"] == "normal"
    assert [item["trade_id"] for item in uploaded] == ["TODAY"]
    assert LocalOutbox(Path(config.data_dir) / "collector.sqlite3").status()["pending"] == 0


def test_wh6_a004_server_recomputes_key_and_concurrent_devices_share_one_fact(tmp_path, monkeypatch):
    account_id = _use_temp_db(tmp_path, monkeypatch)
    first = _activate(account_id, name="pc-1", fingerprint="fp-1")
    second = _activate(account_id, name="pc-2", fingerprint="fp-2")
    first_payload = _fill_payload(event_key="spoofed-key-a")
    second_payload = _fill_payload(event_key="spoofed-key-b")

    def ingest(device, item):
        return service.ingest_observations(device["token"], [item]).to_dict()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                ingest,
                (first, second),
                (first_payload, second_payload),
            )
        )

    assert sorted(result["fill_results"][0]["status"] for result in results) == [
        "accepted",
        "duplicate",
    ]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fills").fetchone()["c"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM trading_intraday_fill_observations"
        ).fetchone()["c"] == 2


def test_wh6_a004_same_fields_without_trade_id_keep_distinct_source_occurrences(tmp_path, monkeypatch):
    account_id = _use_temp_db(tmp_path, monkeypatch)
    device = _activate(account_id, name="pc-1", fingerprint="fp-1")
    first = _fill_payload(
        event_key="same-client-key",
        trade_id=None,
        source_path="Record/A/20260904match.dat",
        source_sha="a" * 64,
        source_index=7,
    )
    second = _fill_payload(
        event_key="same-client-key",
        trade_id=None,
        source_path="Record/B/20260904match.dat",
        source_sha="b" * 64,
        source_index=7,
    )
    result = service.ingest_observations(device["token"], [first, second]).to_dict()
    assert result["accepted"] == 2
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fills").fetchone()["c"] == 2


def test_wh6_a004_existing_legacy_trade_key_is_upgraded_without_duplicate_fact(tmp_path, monkeypatch):
    account_id = _use_temp_db(tmp_path, monkeypatch)
    device = _activate(account_id, name="pc-1", fingerprint="fp-1")
    payload = _fill_payload(event_key="new-client-key", trade_id="M-001")
    legacy_key = "tradeid:2026-09-04:dce:m-001"
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            """
            INSERT INTO trading_intraday_fills
                (account_id, source_event_key, canonical_event_key, trade_date, trade_time,
                 trade_timestamp, exchange, contract, raw_contract, asset_type, side,
                 open_close, quantity, price, fee, trade_id, order_id, parser_version,
                 source_record_sha256, source_path, source_record_index, canonical_hash,
                 data_status, verification_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                legacy_key,
                legacy_key,
                payload["trade_date"],
                payload["trade_time"],
                payload["trade_timestamp"],
                payload["exchange"],
                payload["contract"],
                payload["raw_contract"],
                payload["asset_type"],
                payload["side"],
                payload["open_close"],
                payload["quantity"],
                payload["price"],
                payload["fee"],
                payload["trade_id"],
                payload["order_id"],
                payload["parser_version"],
                payload["source_record_sha256"],
                payload["source_path"],
                payload["source_record_index"],
                service._hash_json(service._canonical_fields(payload)),
                "provisional",
                "pending",
            ),
        )
    result = service.ingest_observations(device["token"], [payload]).to_dict()
    assert result["duplicates"] == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fills").fetchone()["c"] == 1
        row = conn.execute(
            "SELECT source_event_key, canonical_event_key FROM trading_intraday_fills"
        ).fetchone()
    assert row["source_event_key"] == legacy_key
    assert row["canonical_event_key"].startswith(f"tradeid:{account_id}:2026-09-04:dce:m-001")


def test_wh6_a001_device_token_cannot_be_used_after_runtime_environment_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("LTM_RUNTIME_ENVIRONMENT", "staging")
    account_id = _use_temp_db(tmp_path, monkeypatch)
    device = _activate(account_id, name="pc-1", fingerprint="fp-1")
    monkeypatch.setenv("LTM_RUNTIME_ENVIRONMENT", "production")
    with pytest.raises(service.CollectorServiceError) as exc:
        service.get_device_by_token(device["token"])
    assert exc.value.code == "environment_mismatch"
