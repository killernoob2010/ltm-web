"""Local discovery, checkpoint and outbox tests."""

from datetime import datetime, timezone
from pathlib import Path
import json
import struct
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account, _record, _write_match, _source
from wh6_collector.discovery import discover_wh6_sources, validate_source, validate_sources
from wh6_collector.local_store import LocalOutbox
from wh6_collector.monitor import ScanBatch, scan_source
from wh6_collector.parser import parse_match_records


def test_discovery_finds_explicit_wh6_match_file_and_manual_validation(tmp_path):
    root = tmp_path / "WH6" / "Users" / "u1" / "Record"
    root.mkdir(parents=True)
    path = root / "20260902match.dat"
    _write_match(path, [_record()], size=268)

    candidates = discover_wh6_sources([tmp_path / "WH6"])
    assert [item.path for item in candidates] == [path]
    assert candidates[0].kind == "match"
    assert "match" in candidates[0].validation_reason

    manual = validate_source(root)
    assert manual.path == path
    assert manual.readable is True


def test_discovery_does_not_return_order_cache(tmp_path):
    root = tmp_path / "Record"
    root.mkdir()
    (root / "20260902order.dat").write_bytes(b"order")
    assert discover_wh6_sources([tmp_path]) == []


def test_manual_record_directory_returns_all_historical_match_files(tmp_path):
    root = tmp_path / "Record"
    root.mkdir()
    first = root / "20260901match.dat"
    second = root / "20260902match.dat"
    _write_match(first, [_record(match_id="M-001")], size=268)
    _write_match(second, [_record(match_id="M-002")], size=268)

    sources = validate_sources(root)
    assert [source.path for source in sources] == [first, second]
    # The compatibility helper still returns the first file, while the
    # collector uses validate_sources for complete directory backfill.
    assert validate_source(root).path == first


def test_scan_source_resumes_from_checkpoint_and_handles_rotation(tmp_path):
    path = tmp_path / "20260902match.dat"
    _write_match(path, [_record(match_id="M-001"), _record(match_id="M-002")], size=268)
    source = _source(path)
    first = scan_source(source, None, account=_account())
    assert isinstance(first, ScanBatch)
    assert len(first.fills) == 2
    assert first.checkpoint["record_count"] == 2

    second = scan_source(source, first.checkpoint, account=_account())
    assert second.fills == []

    _write_match(path, [_record(match_id="M-003")], size=268)
    rotated = scan_source(source, first.checkpoint, account=_account())
    assert [fill.trade_id for fill in rotated.fills] == ["M-003"]
    assert any(issue.code == "file_replaced" for issue in rotated.issues)


def test_outbox_is_durable_idempotent_and_atomic(tmp_path):
    path = tmp_path / "20260902match.dat"
    _write_match(path, [_record()], size=268)
    fill = parse_match_records(path, account=_account(), source_file=_source(path))[0][0]
    outbox = LocalOutbox(tmp_path / "collector.sqlite3")
    outbox.put(fill)
    outbox.put(fill)
    assert outbox.status()["pending"] == 1

    claimed = outbox.claim(10)
    assert len(claimed) == 1
    assert claimed[0]["event_key"] == fill.source_event_key
    assert json.loads(claimed[0]["payload_json"])["contract"] == "i2607-c-750"
    assert outbox.status()["claimed"] == 1

    outbox.release([fill.source_event_key], "network down")
    assert outbox.status()["pending"] == 1
    with outbox._connect() as connection:
        connection.execute(
            "UPDATE outbox SET available_at = ? WHERE event_key = ?",
            (datetime.now(timezone.utc).isoformat(), fill.source_event_key),
        )
    claimed_again = outbox.claim(1)
    outbox.ack([claimed_again[0]["event_key"]])
    assert outbox.status()["pending"] == 0
    assert outbox.status()["acked"] == 1


def test_outbox_handles_a_1000_fill_backfill_batch(tmp_path):
    path = tmp_path / "20260902match.dat"
    _write_match(path, [_record(match_id="M-%04d" % index) for index in range(1000)], size=268)
    fills = parse_match_records(path, account=_account(), source_file=_source(path))[0]
    assert len(fills) == 1000
    outbox = LocalOutbox(tmp_path / "collector.sqlite3")
    assert outbox.put_many(fills) == 1000
    assert len(outbox.claim(1000)) == 1000


def test_outbox_checkpoint_and_issue_survive_restart(tmp_path):
    path = tmp_path / "20260902match.dat"
    _write_match(path, [_record()], size=268)
    source = _source(path)
    store_path = tmp_path / "collector.sqlite3"
    store = LocalOutbox(store_path)
    batch = scan_source(source, None, account=_account())
    store.save_checkpoint(str(path), batch.checkpoint)
    store.add_issue(batch.issues[0]) if batch.issues else None
    del store

    reopened = LocalOutbox(store_path)
    assert reopened.load_checkpoint(str(path)) == batch.checkpoint
    assert reopened.status()["issues"] == len(batch.issues)


def test_outbox_reclaims_stale_claim_after_process_restart(tmp_path):
    path = tmp_path / "20260902match.dat"
    _write_match(path, [_record()], size=268)
    fill = parse_match_records(path, account=_account(), source_file=_source(path))[0][0]
    store_path = tmp_path / "collector.sqlite3"
    store = LocalOutbox(store_path)
    store.put(fill)
    assert len(store.claim(1)) == 1
    with store._connect() as connection:
        connection.execute(
            "UPDATE outbox SET updated_at = '2000-01-01T00:00:00+00:00' WHERE event_key = ?",
            (fill.source_event_key,),
        )
    reopened = LocalOutbox(store_path)
    assert len(reopened.claim(1)) == 1
    assert reopened.status()["claimed"] == 1


def test_missing_path_keeps_queued_rows(tmp_path):
    path = tmp_path / "20260902match.dat"
    _write_match(path, [_record()], size=268)
    source = _source(path)
    store = LocalOutbox(tmp_path / "collector.sqlite3")
    fill = scan_source(source, None, account=_account()).fills[0]
    store.put(fill)
    path.unlink()
    missing = scan_source(source, None, account=_account())
    assert missing.fills == []
    assert any(issue.code == "path_unavailable" for issue in missing.issues)
    assert store.status()["pending"] == 1


def test_scan_unknown_format_becomes_quarantined_issue_instead_of_crashing(tmp_path):
    path = tmp_path / "20260902match.dat"
    path.write_bytes(b"unsupported WH6 format")
    batch = scan_source(_source(path), None, account=_account())
    assert batch.fills == []
    assert batch.issues[0].code == "unknown_format"
    assert batch.issues[0].severity == "error"


def _seed_outbox_rows(store, keys, *, attempts=0):
    with store._connect() as connection:
        for key in keys:
            connection.execute(
                """
                INSERT INTO outbox
                    (event_key, payload_json, item_type, priority, status, attempts,
                     available_at, created_at, updated_at)
                VALUES (?, ?, 'fill', 'history', 'pending', ?, '2000-01-01T00:00:00+00:00',
                        '2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00')
                """,
                (key, json.dumps({"source_event_key": key, "trade_date": "2026-09-04"}), attempts),
            )


def test_outbox_ack_results_maps_each_terminal_receipt(tmp_path):
    store = LocalOutbox(tmp_path / "collector.sqlite3")
    _seed_outbox_rows(store, ["a", "b", "c"])
    result = store.ack_results(
        [
            {"event_key": "a", "status": "accepted"},
            {"event_key": "b", "status": "conflict"},
            {"event_key": "c", "status": "quarantined"},
        ],
        expected_event_keys=["a", "b", "c"],
    )
    assert result == {"acked": 1, "covered_by_monthly": 0, "conflict": 1, "quarantined": 1, "policy_rejected": 0, "invalid": 0}
    with store._connect() as connection:
        rows = connection.execute("SELECT event_key, status FROM outbox ORDER BY event_key").fetchall()
    assert {row["event_key"]: row["status"] for row in rows} == {
        "a": "acked",
        "b": "conflict",
        "c": "quarantined",
    }


def test_outbox_releases_missing_receipt_with_bounded_retry_delay(tmp_path):
    store = LocalOutbox(tmp_path / "collector.sqlite3")
    _seed_outbox_rows(store, ["a", "b"], attempts=20)
    claimed = store.claim(2)
    assert {row["event_key"] for row in claimed} == {"a", "b"}
    result = store.ack_results(
        [{"event_key": "a", "status": "accepted"}],
        expected_event_keys=["a", "b"],
    )
    assert result["invalid"] == 1
    with store._connect() as connection:
        row = connection.execute(
            "SELECT status, available_at, attempts FROM outbox WHERE event_key = 'b'"
        ).fetchone()
    assert row["status"] == "pending"
    assert row["attempts"] == 21
    available = datetime.fromisoformat(row["available_at"])
    assert (available - datetime.now(timezone.utc)).total_seconds() >= 295
