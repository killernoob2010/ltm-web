"""Collection-policy validation, caching, and history suppression tests."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account, _record, _write_match
from wh6_collector.cli import CollectorConfig, run_once
from wh6_collector.local_store import LocalOutbox
from wh6_collector.policy import CollectionPolicy
from wh6_collector.uploader import StagingUploader


CAPABILITIES = [
    "monthly_collection_ranges_v1",
    "per_item_ingest_receipts_v1",
    "future_spread_v1",
    "positions_v2",
]


def policy_payload(months=("2026-06", "2026-08"), *, revision="rev-1"):
    ranges = []
    for month in months:
        year, number = (int(value) for value in month.split("-"))
        if number == 12:
            next_month = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            next_month = datetime(year, number + 1, 1, tzinfo=timezone.utc)
        end = next_month.date().fromordinal(next_month.date().toordinal() - 1)
        ranges.append(
            {
                "month": month,
                "range_start": f"{month}-01",
                "range_end": end.isoformat(),
                "source_batch_id": len(ranges) + 1,
            }
        )
    return {
        "schema_version": 1,
        "policy_revision": revision,
        "minimum_client_version": "0.2.1",
        "capabilities": CAPABILITIES,
        "closed_ranges": ranges,
        "generated_at": "2026-09-04T00:00:00+00:00",
    }


def _fill_payload(trade_date):
    return {
        "source_event_key": f"tradeid:{trade_date}:dce:123",
        "trade_id": "123",
        "trade_date": trade_date,
        "trade_time": "09:31:02",
        "trade_timestamp": f"{trade_date}T09:31:02+08:00",
        "exchange": "DCE",
        "contract": "i2607-c-750",
        "raw_contract": "i2607-C-750",
        "asset_type": "option",
        "side": "买",
        "open_close": "开",
        "quantity": 1,
        "price": "12.5",
    }


def _status_for(path, event_key):
    store = LocalOutbox(path)
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status FROM outbox WHERE event_key = ?", (event_key,)
        ).fetchone()
    return row["status"] if row else None


def test_gap_policy_skips_june_and_august_but_keeps_july():
    policy = CollectionPolicy.from_payload(policy_payload())
    assert policy.covers("2026-06-15") is True
    assert policy.covers("2026-07-15") is False
    assert policy.covers("2026-08-15") is True


def test_policy_rejects_unknown_schema_daily_ranges_and_non_month_ranges():
    unknown = policy_payload()
    unknown["schema_version"] = 2
    with pytest.raises(ValueError):
        CollectionPolicy.from_payload(unknown)

    daily = policy_payload()
    daily["closed_ranges"][0]["statement_type"] = "daily"
    with pytest.raises(ValueError):
        CollectionPolicy.from_payload(daily)

    malformed = policy_payload()
    malformed["closed_ranges"][0]["range_end"] = "2026-07-01"
    with pytest.raises(ValueError):
        CollectionPolicy.from_payload(malformed)


def test_policy_moves_unacked_covered_rows_and_restores_removed_ranges(tmp_path):
    store_path = tmp_path / "collector.sqlite3"
    store = LocalOutbox(store_path)
    june = _fill_payload("2026-06-15")
    august = _fill_payload("2026-08-15")
    for payload in (june, august):
        with store._connect() as connection:
            connection.execute(
                """
                INSERT INTO outbox
                    (event_key, payload_json, item_type, priority, status, attempts,
                     available_at, created_at, updated_at)
                VALUES (?, ?, 'fill', 'history', 'pending', 0, 'now', 'now', 'now')
                """,
                (payload["source_event_key"], json.dumps(payload, ensure_ascii=False)),
            )

    assert store.apply_collection_policy(CollectionPolicy.from_payload(policy_payload())) == 2
    assert _status_for(store_path, june["source_event_key"]) == "covered_by_monthly"
    assert _status_for(store_path, august["source_event_key"]) == "covered_by_monthly"

    store.ack([june["source_event_key"]])
    changed = policy_payload(("2026-06",), revision="rev-2")
    assert store.apply_collection_policy(CollectionPolicy.from_payload(changed)) == 1
    assert _status_for(store_path, june["source_event_key"]) == "acked"
    assert _status_for(store_path, august["source_event_key"]) == "pending"


def test_policy_cache_is_fresh_for_five_minutes_and_then_ignored(tmp_path):
    store = LocalOutbox(tmp_path / "collector.sqlite3")
    store.save_collection_policy(
        CollectionPolicy.from_payload(policy_payload()),
        fetched_at="2026-09-04T00:00:00+00:00",
    )
    assert store.load_collection_policy(
        now=datetime(2026, 9, 4, 0, 4, 59, tzinfo=timezone.utc)
    ) is not None
    assert store.load_collection_policy(
        now=datetime(2026, 9, 4, 0, 5, 1, tzinfo=timezone.utc)
    ) is None


def test_valid_policy_skips_closed_history_and_keeps_open_gap(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    for month, match_id in (("06", "JUNE"), ("07", "JULY"), ("08", "AUGUST")):
        date_text = f"2026-{month}-15"
        _write_match(
            source_root / f"2026{month}15match.dat",
            [_record(timestamp=f"{date_text} 09:31:02", match_id=match_id)],
            size=268,
        )
    uploaded = []
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source_root),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        allow_weak_source=True,
    )
    calls = []

    def fetch_policy():
        calls.append("fetch")
        return policy_payload(("2026-06", "2026-08"))

    def upload(token, items):
        uploaded.extend(items)
        return {
            "accepted": len(items),
            "fill_results": [
                {"event_key": item["source_event_key"], "status": "accepted"}
                for item in items
            ],
        }
    first = run_once(config, upload=upload, policy_fetch=fetch_policy)
    second = run_once(config, upload=upload, policy_fetch=lambda: (_ for _ in ()).throw(RuntimeError("must use cache")))
    assert first["state"] == "normal"
    assert second["state"] == "normal"
    assert calls == ["fetch"]
    assert {item["trade_id"] for item in uploaded} == {"JULY"}


def test_staging_uploader_fetches_policy_with_device_header(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return policy_payload()

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    import wh6_collector.uploader as uploader_module

    monkeypatch.setattr(uploader_module.requests, "get", fake_get)
    uploader = StagingUploader("https://ltm-web-staging.onrender.com", "device-token")
    result = uploader.get_collection_policy()
    assert result["schema_version"] == 1
    assert calls == [
        (
            "https://ltm-web-staging.onrender.com/api/trading-collector/device/collection-policy",
            {"headers": {"X-Collector-Token": "device-token"}, "timeout": (5, 30)},
        )
    ]


def test_first_start_without_policy_scans_today_but_pauses_history(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    today = datetime.now().astimezone().strftime("%Y%m%d")
    _write_match(source_root / f"{today}match.dat", [_record(timestamp=f"{today[:4]}-{today[4:6]}-{today[6:]} 09:31:02")], size=268)
    _write_match(source_root / "20260815match.dat", [_record(timestamp="2026-08-15 09:31:02", match_id="HISTORY")], size=268)
    uploaded = []
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source_root),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        allow_weak_source=True,
    )

    def raise_offline():
        raise RuntimeError("policy offline")

    result = run_once(
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
    assert {item["trade_date"] for item in uploaded} == {f"{today[:4]}-{today[4:6]}-{today[6:]}"}
