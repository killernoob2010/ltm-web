"""V2 local outbox priority, snapshot scanning, and scheduling tests."""

from pathlib import Path
import json
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account, _record, _source, _write_match
from test_wh6_position_parser import _position_row, _position_source, write_position_json
from wh6_collector.local_store import LocalOutbox
from wh6_collector.models import PositionSnapshot
from wh6_collector.monitor import DualChannelScheduler, scan_source
from wh6_collector.parser import parse_match_records, parse_position_snapshot
from wh6_collector.uploader import StagingUploader, UploadError


def test_realtime_claim_preempts_history_backlog(tmp_path):
    history_path = tmp_path / "20260903-history-match.dat"
    realtime_path = tmp_path / "20260903-realtime-match.dat"
    _write_match(history_path, [_record(contract="i2607", match_id="history-1")], size=268)
    _write_match(realtime_path, [_record(match_id="realtime-1")], size=268)
    history = parse_match_records(history_path, account=_account(), source_file=_source(history_path))[0][0]
    realtime = parse_match_records(realtime_path, account=_account(), source_file=_source(realtime_path))[0][0]

    store = LocalOutbox(tmp_path / "collector.sqlite3")
    store.put(history, priority="history")
    store.put(realtime, priority="realtime")
    claimed = store.claim(1, priority=None)
    assert claimed[0]["event_key"] == realtime.source_event_key
    assert claimed[0]["priority"] == "realtime"
    assert claimed[0]["item_type"] == "fill"


def test_position_queue_entry_is_durable_and_typed(tmp_path):
    path = tmp_path / "20260903position.dat"
    write_position_json(path, rows=[_position_row()])
    snapshot = parse_position_snapshot(path, account=_account(), source_file=_position_source(path))[0]
    assert snapshot is not None
    store_path = tmp_path / "collector.sqlite3"
    store = LocalOutbox(store_path)
    assert store.put_position(snapshot) is True
    del store

    reopened = LocalOutbox(store_path)
    claimed = reopened.claim(1, priority="realtime")
    assert len(claimed) == 1
    assert claimed[0]["item_type"] == "position_snapshot"
    payload = json.loads(claimed[0]["payload_json"])
    assert payload["source_snapshot_key"] == snapshot.source_snapshot_key
    assert payload["rows"][0]["contract"] == "i2607-c-750"


def test_position_checkpoint_advances_only_after_complete_snapshot(tmp_path):
    path = tmp_path / "20260903position.dat"
    write_position_json(path, rows=[_position_row()], complete=True)
    source = _position_source(path)
    first = scan_source(source, None, account=_account())
    assert first.position_snapshot is not None
    assert first.checkpoint["complete"] is True

    write_position_json(source.path, rows=[], complete=False)
    second = scan_source(source, first.checkpoint, account=_account())
    assert second.position_snapshot is None
    assert second.checkpoint == first.checkpoint
    assert any(issue.code == "incomplete_position_snapshot" for issue in second.issues)


def test_scheduler_uses_two_second_realtime_and_ten_second_history_intervals():
    scheduler = DualChannelScheduler(realtime_interval=2, position_interval=5, history_interval=10)
    scheduler.enqueue_history("history-1")
    scheduler.enqueue_realtime("realtime-1")
    scheduler.tick(100.0)
    assert scheduler.next_task() == ("realtime", "realtime-1")
    assert scheduler.next_task() == ("history", "history-1")

    scheduler.enqueue_realtime("realtime-2")
    scheduler.tick(101.9)
    assert scheduler.next_task() is None
    scheduler.tick(102.0)
    assert scheduler.next_task() == ("realtime", "realtime-2")


def test_position_scheduler_waits_five_seconds_and_history_does_not_starve():
    scheduler = DualChannelScheduler(realtime_interval=2, position_interval=5, history_interval=10)
    scheduler.enqueue_realtime(type("Source", (), {"kind": "position"})())
    scheduler.enqueue_history("old-history")
    scheduler.tick(0.0)
    assert scheduler.next_task()[0] == "realtime"
    assert scheduler.next_task() == ("history", "old-history")
    scheduler.enqueue_realtime(type("Source", (), {"kind": "position"})())
    scheduler.tick(4.9)
    assert scheduler.next_task() is None
    scheduler.tick(5.0)
    assert scheduler.next_task()[0] == "realtime"


def test_staging_uploader_sends_fills_and_position_snapshots_together(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"accepted": 1, "position_accepted": 1}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    import wh6_collector.uploader as uploader_module

    monkeypatch.setattr(uploader_module.requests, "post", fake_post)
    uploader = StagingUploader("https://ltm-web-staging.onrender.com", "device-token")
    result = uploader.send("device-token", [{"source_event_key": "fill-1"}], [{"source_snapshot_key": "snapshot-1"}])
    assert result["position_accepted"] == 1
    assert calls[0][0].endswith("/api/trading-collector/device/ingest")
    assert calls[0][1]["json"] == {
        "observations": [{"source_event_key": "fill-1"}],
        "position_snapshots": [{"source_snapshot_key": "snapshot-1"}],
    }
    assert calls[0][1]["timeout"] == (5, 30)


def test_staging_uploader_preserves_authentication_status_for_pause(monkeypatch):
    class Response:
        status_code = 401

    import wh6_collector.uploader as uploader_module

    monkeypatch.setattr(uploader_module.requests, "post", lambda *args, **kwargs: Response())
    uploader = StagingUploader("https://ltm-web-staging.onrender.com", "device-token")
    with pytest.raises(UploadError) as error:
        uploader.send("device-token", [{"source_event_key": "fill-1"}])
    assert error.value.status_code == 401
