from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.order_finance_wps_sync import (
    OrderFinanceWpsSyncError,
    WpsOrderFinanceClient,
    WpsOrderFinanceConfig,
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
