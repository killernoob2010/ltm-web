from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, order_finance
from app import order_finance_wps_sync as sync
from app.order_finance_wps_sync import (
    WpsDownloadResult,
    OrderFinanceWpsSyncError,
    WpsOrderFinanceClient,
    WpsOrderFinanceConfig,
    due_order_finance_sync_slots,
    run_order_finance_wps_sync,
)


@dataclass
class FakeResponse:
    status_code: int = 200
    payload: dict | None = None
    content: bytes = b""

    def json(self):
        return self.payload or {}

    def iter_content(self, chunk_size=64 * 1024):
        del chunk_size
        yield self.content


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def fake_config():
    return WpsOrderFinanceConfig(
        app_id="app-id",
        app_secret="app-secret",
        refresh_token="refresh-token",
        drive_id="drive-id",
        file_id="file-id",
    )


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "order_finance_wps.db")
    db.init_db()


def snapshot_record(item_no):
    return {
        "business_key": f"ITEM|{item_no}|1",
        "subsidiary": "北满",
        "source_file": "台账.xlsx",
        "source_sheet": "订单",
        "purchase_contract_no": f"C-{item_no}",
        "finance_amount_actual": 10_000_000,
        "finance_drawdown_date": "2026-06-01",
        "finance_due_date": "2026-08-01",
        "business_status": "存续",
        "source_json": json.dumps({"item_no": item_no}, ensure_ascii=False),
    }


def test_wps_config_requires_all_secret_and_file_identifiers(monkeypatch):
    for name in (
        "WPS_APP_ID", "WPS_APP_SECRET", "WPS_USER_REFRESH_TOKEN",
        "ORDER_FINANCE_WPS_DRIVE_ID", "ORDER_FINANCE_WPS_FILE_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(OrderFinanceWpsSyncError, match="wps_config"):
        WpsOrderFinanceConfig.from_env()


def test_wps_client_refreshes_user_token_and_downloads_source_xlsx(tmp_path):
    http = FakeHttp([
        FakeResponse(payload={"access_token": "short-lived", "expires_in": 7200}),
        FakeResponse(payload={"data": {"name": "台账.xlsx", "version": 8}}),
        FakeResponse(payload={"data": {"url": "https://download.invalid/source"}}),
        FakeResponse(content=b"PK\x03\x04xlsx"),
    ])
    client = WpsOrderFinanceClient(config=fake_config(), http=http)
    target = tmp_path / "source.xlsx"

    result = client.download_workbook(target)

    assert result.file_name == "台账.xlsx"
    assert result.source_version == "8"
    assert target.read_bytes().startswith(b"PK\x03\x04")
    assert [call["method"] for call in http.calls] == ["POST", "GET", "GET", "GET"]
    assert all(call["method"] not in {"PUT", "PATCH", "DELETE"} for call in http.calls)
    assert http.calls[0]["data"] == {
        "grant_type": "refresh_token",
        "client_id": "app-id",
        "client_secret": "app-secret",
        "refresh_token": "refresh-token",
    }
    assert http.calls[1]["headers"]["Authorization"] == "Bearer short-lived"


def test_wps_client_reuses_cached_access_token(tmp_path):
    http = FakeHttp([
        FakeResponse(payload={"access_token": "short-lived", "expires_in": 7200}),
        FakeResponse(payload={"data": {"name": "台账.xlsx", "version": 8}}),
        FakeResponse(payload={"data": {"url": "https://download.invalid/one"}}),
        FakeResponse(content=b"PK\x03\x04one"),
        FakeResponse(payload={"data": {"name": "台账.xlsx", "version": 9}}),
        FakeResponse(payload={"data": {"url": "https://download.invalid/two"}}),
        FakeResponse(content=b"PK\x03\x04two"),
    ])
    client = WpsOrderFinanceClient(config=fake_config(), http=http)

    client.download_workbook(tmp_path / "one.xlsx")
    client.download_workbook(tmp_path / "two.xlsx")

    assert len([call for call in http.calls if call["method"] == "POST"]) == 1


def test_wps_client_redacts_credentials_tokens_and_download_url(tmp_path):
    http = FakeHttp([
        FakeResponse(payload={"access_token": "short-lived", "expires_in": 7200}),
        FakeResponse(payload={"data": {"name": "台账.xlsx", "version": 8}}),
        FakeResponse(payload={"data": {"url": "https://download.invalid/private-signature"}}),
        FakeResponse(status_code=503, content=b"upstream body"),
    ])
    client = WpsOrderFinanceClient(config=fake_config(), http=http)

    with pytest.raises(OrderFinanceWpsSyncError) as captured:
        client.download_workbook(tmp_path / "source.xlsx")

    message = str(captured.value)
    assert "source_download" in message
    for secret in (
        "app-secret", "refresh-token", "short-lived",
        "https://download.invalid/private-signature", "upstream body",
    ):
        assert secret not in message


@pytest.mark.parametrize(("clock", "expected"), [
    ("2026-07-18T08:59:00+08:00", []),
    ("2026-07-18T09:00:00+08:00", ["2026-07-18T09:00+08:00"]),
    ("2026-07-18T16:59:00+08:00", ["2026-07-18T09:00+08:00"]),
    ("2026-07-18T17:00:00+08:00", ["2026-07-18T17:00+08:00"]),
])
def test_due_slots_include_weekends_and_two_shanghai_times(clock, expected):
    assert due_order_finance_sync_slots(datetime.fromisoformat(clock), None) == expected


def test_due_slots_exclude_the_last_attempted_slot():
    now = datetime.fromisoformat("2026-07-15T17:00:00+08:00")

    assert due_order_finance_sync_slots(now, "2026-07-15T09:00+08:00") == [
        "2026-07-15T17:00+08:00",
    ]
    assert due_order_finance_sync_slots(now, "2026-07-15T17:00+08:00") == []


class SuccessfulDownloadClient:
    def __init__(self):
        self.target = None

    def download_workbook(self, target):
        self.target = target
        target.write_bytes(b"PK\x03\x04test")
        return WpsDownloadResult(file_name="线上台账.xlsx", source_version="v10")


class FailingDownloadClient:
    def download_workbook(self, target):
        del target
        raise OrderFinanceWpsSyncError("source_download", 503)


def test_successful_sync_updates_rows_status_and_removes_temp_file(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    client = SuccessfulDownloadClient()
    monkeypatch.setattr(
        sync,
        "parse_order_finance_directory",
        lambda path: {"records": [snapshot_record("LIVE")], "summary": {"record_count": 1}},
    )

    result = run_order_finance_wps_sync(
        "2026-07-15T09:00+08:00",
        now=datetime.fromisoformat("2026-07-15T09:02:00+08:00"),
        client=client,
    )

    assert result["status"] == "success"
    assert result["changed_count"] == 1
    assert [row["business_key"] for row in order_finance.list_order_finance_records()] == [
        "ITEM|LIVE|1",
    ]
    assert order_finance.get_order_finance_sync_status()["last_success_at"] == (
        "2026-07-15T09:02:00+08:00"
    )
    assert client.target is not None and not client.target.exists()


def test_unchanged_source_version_skips_parse_and_reports_zero(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("UNCHANGED")],
        sync_success_at="2026-07-14T17:02:00+08:00",
        source_version="v10",
        attempt_slot="2026-07-14T17:00+08:00",
    )
    client = SuccessfulDownloadClient()
    monkeypatch.setattr(
        sync,
        "parse_order_finance_directory",
        lambda path: (_ for _ in ()).throw(AssertionError(f"should not parse {path}")),
    )

    result = run_order_finance_wps_sync(
        "2026-07-15T09:00+08:00",
        now=datetime.fromisoformat("2026-07-15T09:02:00+08:00"),
        client=client,
    )

    assert result == {
        "status": "success", "inserted": 0, "updated": 0,
        "archived": 0, "changed_count": 0,
    }
    assert order_finance.get_order_finance_sync_status()["last_success_at"] == (
        "2026-07-15T09:02:00+08:00"
    )
    assert [row["business_key"] for row in order_finance.list_order_finance_records()] == [
        "ITEM|UNCHANGED|1",
    ]


def test_failed_sync_preserves_rows_and_last_success(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    order_finance.apply_order_finance_snapshot(
        [snapshot_record("OLD")],
        sync_success_at="2026-07-14T17:00:00+08:00",
        source_version="v1",
        attempt_slot="2026-07-14T17:00+08:00",
    )

    with pytest.raises(OrderFinanceWpsSyncError, match="source_download"):
        run_order_finance_wps_sync(
            "2026-07-15T09:00+08:00",
            now=datetime.fromisoformat("2026-07-15T09:02:00+08:00"),
            client=FailingDownloadClient(),
        )

    assert [row["business_key"] for row in order_finance.list_order_finance_records()] == [
        "ITEM|OLD|1",
    ]
    status = order_finance.get_order_finance_sync_status()
    assert status["last_success_at"] == "2026-07-14T17:00:00+08:00"
    assert status["changed_count"] == 1


def test_disabled_scheduler_does_not_start(monkeypatch):
    monkeypatch.setenv("ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED", "false")

    assert sync.start_order_finance_wps_sync_scheduler() is False


def test_fastapi_startup_starts_order_finance_scheduler():
    main_source = (
        Path(__file__).resolve().parents[1] / "backend" / "app" / "main.py"
    ).read_text(encoding="utf-8")

    assert "from .order_finance_wps_sync import start_order_finance_wps_sync_scheduler" in main_source
    assert "start_order_finance_wps_sync_scheduler()" in main_source
