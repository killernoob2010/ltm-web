"""Read-only WPS source adapter for the order-finance workbook."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Optional

import requests


WPS_API_BASE = "https://openapi.wps.cn"
TOKEN_URL = f"{WPS_API_BASE}/oauth2/token"
REQUEST_TIMEOUT = (10, 60)


class OrderFinanceWpsSyncError(RuntimeError):
    def __init__(self, stage: str, status_code: Optional[int] = None):
        self.stage = stage
        self.status_code = status_code
        suffix = f" status={status_code}" if status_code is not None else ""
        super().__init__(f"order_finance_wps_sync_failed stage={stage}{suffix}")


@dataclass(frozen=True)
class WpsOrderFinanceConfig:
    app_id: str
    app_secret: str
    refresh_token: str
    drive_id: str
    file_id: str

    @classmethod
    def from_env(cls) -> "WpsOrderFinanceConfig":
        names = {
            "app_id": "WPS_APP_ID",
            "app_secret": "WPS_APP_SECRET",
            "refresh_token": "WPS_USER_REFRESH_TOKEN",
            "drive_id": "ORDER_FINANCE_WPS_DRIVE_ID",
            "file_id": "ORDER_FINANCE_WPS_FILE_ID",
        }
        values = {field: (os.getenv(name) or "").strip() for field, name in names.items()}
        if any(not value for value in values.values()):
            raise OrderFinanceWpsSyncError("wps_config")
        return cls(**values)


@dataclass(frozen=True)
class WpsDownloadResult:
    file_name: str
    source_version: str


class WpsOrderFinanceClient:
    def __init__(self, config: WpsOrderFinanceConfig, http: Any = requests):
        self.config = config
        self.http = http
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._refresh_token = config.refresh_token

    def _json_request(self, stage: str, method: str, url: str, **kwargs) -> dict:
        try:
            response = self.http.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except Exception as exc:
            raise OrderFinanceWpsSyncError(stage) from exc
        if not 200 <= int(response.status_code) < 300:
            raise OrderFinanceWpsSyncError(stage, int(response.status_code))
        try:
            payload = response.json()
        except Exception as exc:
            raise OrderFinanceWpsSyncError(stage) from exc
        if not isinstance(payload, dict) or payload.get("code", 0) not in (0, None):
            raise OrderFinanceWpsSyncError(stage)
        return payload

    def _user_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token
        payload = self._json_request(
            "token_refresh",
            "POST",
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": self.config.app_id,
                "client_secret": self.config.app_secret,
                "refresh_token": self._refresh_token,
            },
        )
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise OrderFinanceWpsSyncError("token_refresh")
        expires_in = max(60, int(payload.get("expires_in") or 7200))
        self._access_token = access_token
        self._access_token_expires_at = now + expires_in - 60
        if payload.get("refresh_token"):
            self._refresh_token = str(payload["refresh_token"])
        return access_token

    def _authorization_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._user_access_token()}"}

    def download_workbook(self, target: Path) -> WpsDownloadResult:
        file_url = (
            f"{WPS_API_BASE}/v7/drives/{self.config.drive_id}"
            f"/files/{self.config.file_id}"
        )
        metadata = self._json_request(
            "file_metadata",
            "GET",
            f"{file_url}/meta",
            headers=self._authorization_headers(),
        ).get("data") or {}
        download = self._json_request(
            "download_info",
            "GET",
            f"{file_url}/download",
            headers=self._authorization_headers(),
            params={"with_hash": "true"},
        ).get("data") or {}
        download_url = str(download.get("url") or "").strip()
        if not download_url:
            raise OrderFinanceWpsSyncError("download_info")
        try:
            response = self.http.request(
                "GET",
                download_url,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
        except Exception as exc:
            raise OrderFinanceWpsSyncError("source_download") from exc
        if not 200 <= int(response.status_code) < 300:
            raise OrderFinanceWpsSyncError("source_download", int(response.status_code))
        with target.open("wb") as output:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    output.write(chunk)
        try:
            signature = target.read_bytes()[:4]
        except OSError as exc:
            raise OrderFinanceWpsSyncError("source_validation") from exc
        if signature != b"PK\x03\x04":
            raise OrderFinanceWpsSyncError("source_validation")
        hashes = download.get("hashes") or []
        source_hash = next(
            (str(item.get("sum")) for item in hashes if isinstance(item, dict) and item.get("sum")),
            "",
        )
        source_version = str(metadata.get("version") or source_hash or metadata.get("mtime") or "")
        return WpsDownloadResult(
            file_name=str(metadata.get("name") or target.name),
            source_version=source_version,
        )
