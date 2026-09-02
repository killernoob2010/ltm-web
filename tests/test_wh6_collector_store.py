"""Local discovery, checkpoint and outbox tests."""

from pathlib import Path
import json
import struct
import sys


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account, _record, _write_match, _source
from wh6_collector.discovery import discover_wh6_sources, validate_source
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
    claimed_again = outbox.claim(1)
    outbox.ack([claimed_again[0]["event_key"]])
    assert outbox.status()["pending"] == 0
    assert outbox.status()["acked"] == 1


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
