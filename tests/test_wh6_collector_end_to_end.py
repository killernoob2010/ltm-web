"""Local vertical-slice acceptance test for two-device collection."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from app import db
from app import trading_collector_service as service
from test_wh6_collector_core import _account, _record, _source, _write_match
from wh6_collector.local_store import LocalOutbox
from wh6_collector.monitor import scan_source


def test_two_devices_produce_one_fact_and_same_signature_occurrences_are_retained(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "db")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db" / "collector.db")
    db.init_db()
    with db.connect() as conn:
        account_id = conn.execute("SELECT id FROM trading_accounts WHERE account_code = 'hongyuan_futures'").fetchone()["id"]

    root_a = tmp_path / "pc-a" / "Record"
    root_b = tmp_path / "pc-b" / "Record"
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)
    path_a = root_a / "20260902match.dat"
    path_b = root_b / "20260902match.dat"
    _write_match(path_a, [_record(match_id="M-E2E")], size=268)
    _write_match(path_b, [_record(match_id="M-E2E")], size=268)

    first = scan_source(_source(path_a), None, account=_account())
    second = scan_source(_source(path_b), None, account=_account())
    outbox_a = LocalOutbox(tmp_path / "pc-a" / "collector.sqlite3")
    outbox_b = LocalOutbox(tmp_path / "pc-b" / "collector.sqlite3")
    outbox_a.put_many(first.fills)
    outbox_b.put_many(second.fills)
    device_a = service.activate_device(service.issue_pairing_code(account_id, 1)["code"], "pc-a", "0.1.0", "fp-a")
    device_b = service.activate_device(service.issue_pairing_code(account_id, 1)["code"], "pc-b", "0.1.0", "fp-b")

    rows_a = outbox_a.claim(10)
    rows_b = outbox_b.claim(10)
    result_a = service.ingest_observations(device_a["token"], [{**__import__("json").loads(row["payload_json"])} for row in rows_a])
    result_b = service.ingest_observations(device_b["token"], [{**__import__("json").loads(row["payload_json"])} for row in rows_b])
    assert result_a.accepted == 1
    assert result_b.duplicates == 1

    same_path = tmp_path / "pc-a" / "20260903match.dat"
    _write_match(same_path, [_record(match_id=""), _record(match_id="")], size=268)
    same = scan_source(_source(same_path), None, account=_account())
    assert [fill.source_event_key.rsplit(":", 1)[-1] for fill in same.fills] == ["0", "1"]
    result_same = service.ingest_observations(device_a["token"], [fill.to_payload() for fill in same.fills])
    assert result_same.accepted == 2

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fills WHERE account_id = ?", (account_id,)).fetchone()["c"] == 3
        assert conn.execute("SELECT COUNT(*) AS c FROM trading_intraday_fill_observations WHERE account_id = ?", (account_id,)).fetchone()["c"] == 4
