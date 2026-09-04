"""V2 local-to-service vertical slice; no Windows or Staging claim is implied."""

from datetime import datetime, timezone
from pathlib import Path
import json
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from app import db
from app import trading_collector_service as service
from test_wh6_collector_core import _account, _record, _source, _write_match
from test_wh6_position_parser import _position_row, _position_source, write_position_json
from wh6_collector.local_store import LocalOutbox
from wh6_collector.monitor import scan_source


def _account_id(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db" / "collector.db")
    db.init_db()
    with db.connect() as conn:
        return conn.execute(
            "SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'"
        ).fetchone()["id"]


def _activate(account_id, name, fingerprint):
    issued = service.issue_pairing_code(account_id, actor_id=1)
    return service.activate_device(issued["code"], name, "0.2.0", fingerprint)


def test_v2_local_outbox_to_service_keeps_realtime_first_and_snapshot_non_additive(tmp_path, monkeypatch):
    account_id = _account_id(tmp_path, monkeypatch)
    with db.connect() as conn:
        before_settlement = conn.execute("SELECT COUNT(*) AS c FROM trading_trade_facts").fetchone()["c"]

    historical_path = tmp_path / "pc-a" / "Record" / "20260902match.dat"
    historical_path.parent.mkdir(parents=True)
    _write_match(historical_path, [_record(timestamp="2026-09-02 09:05:03", match_id="HIST-001")], size=268)
    current_path = tmp_path / "pc-a" / "Record" / "20260903match.dat"
    _write_match(
        current_path,
        [
            _record(contract="i2607", timestamp="2026-09-03 09:05:02", match_id="FUT-001"),
            _record(contract="i2607-C-750", timestamp="2026-09-03 09:05:03", match_id="OPT-001"),
        ],
        size=268,
    )
    position_path = current_path.parent / "20260903position.dat"
    write_position_json(position_path, rows=[_position_row("i2607"), _position_row("i2607-C-750")])

    historical = scan_source(_source(historical_path), None, account=_account())
    current = scan_source(_source(current_path), None, account=_account())
    positions = scan_source(_position_source(position_path), None, account=_account())
    assert {fill.asset_type for fill in current.fills} == {"future", "option"}
    assert positions.position_snapshot is not None

    outbox = LocalOutbox(tmp_path / "pc-a" / "collector.sqlite3")
    outbox.put_many(historical.fills, priority="history")
    outbox.put_many(current.fills, priority="realtime")
    outbox.put_position(positions.position_snapshot, priority="realtime")
    first_claim = outbox.claim(10, priority="realtime")
    assert {row["item_type"] for row in first_claim} == {"fill", "position_snapshot"}
    assert any(json.loads(row["payload_json"]).get("asset_type") == "future" for row in first_claim if row["item_type"] == "fill")

    device_a = _activate(account_id, "pc-a", "fp-a")
    result_a = service.ingest_observations(
        device_a["token"],
        [json.loads(row["payload_json"]) for row in first_claim if row["item_type"] == "fill"],
        [json.loads(row["payload_json"]) for row in first_claim if row["item_type"] == "position_snapshot"],
    )
    assert result_a.accepted == 2
    assert result_a.positions_accepted == 1
    outbox.ack([row["event_key"] for row in first_claim])

    history_claim = outbox.claim(10, priority="history")
    assert len(history_claim) == 1
    history_result = service.ingest_observations(
        device_a["token"],
        [json.loads(history_claim[0]["payload_json"])],
    )
    assert history_result.accepted == 1
    outbox.ack([history_claim[0]["event_key"]])

    # A second device repeats the same complete snapshot; it adds an observation,
    # never another quantity-bearing canonical snapshot.
    device_b = _activate(account_id, "pc-b", "fp-b")
    repeated = service.ingest_observations(device_b["token"], [], [positions.position_snapshot.to_payload()])
    assert repeated.position_duplicates == 1
    current = service.query_current_option_positions(
        account_id,
        now=datetime.fromisoformat("2026-09-03T09:05:10+08:00"),
    )
    assert [item["quantity"] for item in current["items"]] == [3]
    assert current["source_status"] == "ok"

    # Different content under the same snapshot identity is retained as a
    # conflict and cannot overwrite the first snapshot rows.
    conflict = positions.position_snapshot.to_payload()
    conflict["source_snapshot_sha256"] = "f" * 64
    conflict["rows"][0]["quantity"] = 99
    conflict_result = service.ingest_observations(device_b["token"], [], [conflict])
    assert conflict_result.position_conflicts == 1
    conflicted = service.query_current_option_positions(
        account_id,
        now=datetime.fromisoformat("2026-09-03T09:05:45+08:00"),
    )
    assert conflicted["source_status"] == "multi_device_conflict"
    assert conflicted["items"][0]["quantity"] == 3

    # A released claim survives close/reopen and can be retried after an offline
    # interval; no event is silently dropped.
    retry_path = tmp_path / "pc-a" / "Record" / "20260903-retry-match.dat"
    _write_match(retry_path, [_record(timestamp="2026-09-03 09:06:03", match_id="RETRY-001")], size=268)
    retry = scan_source(_source(retry_path), None, account=_account()).fills[0]
    outbox.put(retry, priority="realtime")
    retry_claim = outbox.claim(1, priority="realtime")
    outbox.release([retry_claim[0]["event_key"]], "offline")
    del outbox
    reopened = LocalOutbox(tmp_path / "pc-a" / "collector.sqlite3")
    with reopened._connect() as connection:
        connection.execute(
            "UPDATE outbox SET available_at = ? WHERE event_key = ?",
            (datetime.now(timezone.utc).isoformat(), retry.source_event_key),
        )
    assert reopened.claim(1, priority="realtime")[0]["event_key"] == retry.source_event_key

    volume = service.query_option_volume(account_id, trade_date="2026-09-03")
    assert volume["total_quantity"] == 2
    assert all(item["asset_type"] == "option" for item in volume["items"])
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fills WHERE account_id = ?", (account_id,)).fetchone()["c"] == 3
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_position_snapshots WHERE account_id = ?", (account_id,)).fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_trade_facts").fetchone()["c"] == before_settlement
