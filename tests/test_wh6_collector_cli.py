"""CLI safety and offline queue behavior."""

from pathlib import Path
import json
import sys

import pytest


sys.path.insert(0, str(Path(__file__).parents[1] / "collector"))
sys.path.insert(0, str(Path(__file__).parent))

from test_wh6_collector_core import _account, _record, _write_match
from wh6_collector.cli import (
    CollectorConfig,
    default_data_dir,
    ensure_staging_url,
    run_once,
)
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
    )
    result = run_once(config, upload=lambda token, items: (_ for _ in ()).throw(RuntimeError("offline")))
    assert result["state"] == "offline_queue"
    assert result["queued"] == 1
    assert LocalOutbox(Path(config.data_dir) / "collector.sqlite3").status()["pending"] == 1


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
