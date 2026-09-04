"""CLI safety and offline queue behavior."""

from pathlib import Path
from datetime import datetime
import json
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account, _record, _write_match
from test_wh6_position_parser import _position_row, write_position_json
from wh6_collector.cli import (
    CollectorConfig,
    default_data_dir,
    ensure_staging_url,
    run_once,
)
import wh6_collector.cli as cli
from wh6_collector.local_store import LocalOutbox
from wh6_collector.monitor import scan_source
from wh6_collector.discovery import validate_source


def test_cli_refuses_production_url_and_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData"))
    assert default_data_dir() == tmp_path / "AppData" / "WH6成交采集器"
    assert ensure_staging_url("https://ltm-web-staging.onrender.com") == "https://ltm-web-staging.onrender.com"
    with pytest.raises(ValueError):
        ensure_staging_url("https://ltm-web.onrender.com")


def test_once_offline_keeps_claimed_rows_pending(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    source = source_root / "20260902match.dat"
    _write_match(source, [_record()], size=268)
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        allow_weak_source=True,
    )
    result = run_once(config, upload=lambda token, items: (_ for _ in ()).throw(RuntimeError("offline")))
    assert result["state"] == "offline_queue"
    assert result["queued"] == 1
    assert LocalOutbox(Path(config.data_dir) / "collector.sqlite3").status()["pending"] == 1


def test_once_backfills_every_match_file_under_selected_record_directory(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    _write_match(source_root / "20260901match.dat", [_record(match_id="M-001")], size=268)
    _write_match(source_root / "20260902match.dat", [_record(match_id="M-002")], size=268)
    uploaded = []
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source_root),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        allow_weak_source=True,
    )

    result = run_once(
        config,
        upload=lambda token, items: uploaded.extend(items) or {"accepted": len(items)},
    )
    assert result["state"] == "normal"
    assert result["accepted"] == 2
    assert {item["trade_id"] for item in uploaded} == {"M-001", "M-002"}


def test_once_pauses_before_upload_when_bound_source_account_changes(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    source = source_root / "20260902match.dat"
    _write_match(source, [_record()], size=268)
    metadata = source_root / "account.ini"
    metadata.write_text("broker=宏源期货\naccount=902711111\n", encoding="utf-8")
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source_root),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        source_account_fingerprint=_account().fingerprint,
    )
    first = run_once(config, upload=lambda token, items: (_ for _ in ()).throw(RuntimeError("offline")))
    assert first["state"] == "offline_queue"
    assert first["queued"] == 1

    metadata.write_text("broker=宏源期货\naccount=902711112\n", encoding="utf-8")
    uploaded = []
    changed = run_once(config, upload=lambda token, items: uploaded.extend(items) or {"accepted": len(items)})
    assert changed["state"] == "account_changed"
    assert len(uploaded) == 1


def test_config_round_trip_does_not_store_plain_account_id(tmp_path):
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path="C:/WH6/Record/20260902match.dat",
        account=_account(),
        device_token="secret-token",
        data_dir=str(tmp_path),
    )
    path = tmp_path / "config.json"
    config.save(path)
    payload = json.loads(path.read_text())
    assert payload["account"]["stable_id"] is None
    assert payload["account"]["fingerprint"]
    assert payload["device_token"] == "secret-token"


def test_service_loop_waits_for_next_poll_until_stop_event(tmp_path, monkeypatch):
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path="C:/WH6/Record/20260902match.dat",
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path),
    )
    calls = []
    monkeypatch.setattr(
        cli,
        "run_once",
        lambda value, **kwargs: calls.append(value) or {"state": "path_unavailable"},
    )

    class StopAfterFirstWait:
        def wait(self, seconds):
            assert seconds == 2
            return True

    cli.run_service(config, StopAfterFirstWait())
    assert calls == [config]


def test_service_loop_checks_positions_every_five_seconds(tmp_path, monkeypatch):
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path="C:/WH6/Record/20260902match.dat",
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path),
    )
    clock = [0.0]
    calls = []
    waits = []
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        cli,
        "run_once",
        lambda value, *, scan_positions=True, scan_history=True: calls.append((scan_positions, scan_history)) or {"state": "path_unavailable"},
    )

    class StopAfterFourPolls:
        def wait(self, seconds):
            waits.append(seconds)
            clock[0] += seconds
            return len(waits) >= 4

    cli.run_service(config, StopAfterFourPolls())
    assert calls == [(True, True), (False, False), (False, False), (True, False)]
    assert waits == [2, 2, 1, 2]


def test_service_loop_scans_history_every_ten_seconds(tmp_path, monkeypatch):
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path="C:/WH6/Record/20260902match.dat",
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path),
    )
    clock = [0.0]
    calls = []
    waits = []
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])

    def fake_run_once(value, *, scan_positions=True, scan_history=True):
        calls.append((scan_positions, scan_history))
        return {"state": "normal"}

    monkeypatch.setattr(cli, "run_once", fake_run_once)

    class StopAfterSevenPolls:
        def wait(self, seconds):
            waits.append(seconds)
            clock[0] += seconds
            return len(waits) >= 7

    cli.run_service(config, StopAfterSevenPolls())
    assert [scan_history for _, scan_history in calls] == [True, False, False, False, False, False, True]
    assert waits == [2, 2, 1, 2, 2, 1, 2]


def test_service_loop_rechecks_positions_after_new_realtime_fill(tmp_path, monkeypatch):
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path="C:/WH6/Record/20260902match.dat",
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path),
    )
    clock = [0.0]
    calls = []
    waits = []
    monkeypatch.setattr(cli.time, "monotonic", lambda: clock[0])

    def fake_run_once(value, *, scan_positions=True, scan_history=True):
        calls.append(scan_positions)
        return {"state": "normal", "position_scan_requested": len(calls) == 1}

    monkeypatch.setattr(cli, "run_once", fake_run_once)

    class StopAfterRetry:
        def wait(self, seconds):
            waits.append(seconds)
            clock[0] += seconds
            return len(waits) >= 2

    cli.run_service(config, StopAfterRetry())
    assert calls == [True, True]
    assert waits == [0, 2]


def test_once_uploads_full_asset_fills_and_position_snapshot_with_priority_payload(tmp_path):
    source_root = tmp_path / "Record"
    source_root.mkdir()
    today = datetime.now().astimezone().strftime("%Y%m%d")
    _write_match(source_root / (today + "match.dat"), [_record(contract="i2607", match_id="FUT-001")], size=268)
    write_position_json(source_root / (today + "position.dat"), rows=[_position_row("i2607-C-750")])
    uploaded = []
    config = CollectorConfig(
        staging_url="http://127.0.0.1:8000",
        source_path=str(source_root),
        account=_account(),
        device_token="device-token",
        data_dir=str(tmp_path / "data"),
        allow_weak_source=True,
    )

    def upload(token, fills, position_snapshots):
        uploaded.append((token, list(fills), list(position_snapshots)))
        return {"accepted": len(fills), "position_accepted": len(position_snapshots)}

    result = run_once(config, upload=upload)
    assert result["state"] == "normal"
    assert result["accepted"] == 1
    assert result["positions_accepted"] == 1
    assert uploaded[0][1][0]["asset_type"] == "future"
    assert uploaded[0][2][0]["rows"][0]["asset_type"] == "option"


def test_no_arguments_launch_first_run_setup_when_config_is_missing(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    calls = []
    monkeypatch.setattr(cli, "run_first_setup", lambda path: calls.append(path) or 7)

    assert cli.main(["--config", str(config_path)]) == 7
    assert calls == [config_path]


def test_no_arguments_start_service_when_config_already_exists(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    sentinel = object()
    monkeypatch.setattr(cli.CollectorConfig, "load", lambda path: sentinel)
    calls = []
    monkeypatch.setattr(cli, "run_service", lambda config: calls.append(config))

    assert cli.main(["--config", str(config_path)]) == 0
    assert calls == [sentinel]


def test_successful_first_run_setup_enters_service_without_second_launch(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    sentinel = object()
    calls = []
    monkeypatch.setattr(cli, "run_first_setup", lambda path: 0)
    monkeypatch.setattr(cli.CollectorConfig, "load", lambda path: sentinel)
    monkeypatch.setattr(cli, "run_service", lambda config: calls.append(config))

    assert cli.main(["--config", str(config_path)]) == 0
    assert calls == [sentinel]
