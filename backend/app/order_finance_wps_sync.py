"""Read-only WPS source adapter for the order-finance workbook."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as day_time
import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken

from . import db
from .order_finance import (
    apply_order_finance_snapshot,
    claim_order_finance_sync_slot,
    get_order_finance_sync_status,
    parse_order_finance_directory,
    record_unchanged_order_finance_sync,
)


WPS_API_BASE = "https://openapi.wps.cn"
TOKEN_URL = f"{WPS_API_BASE}/oauth2/token"
REQUEST_TIMEOUT = (10, 60)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SYNC_TIMES = (day_time(9, 0), day_time(17, 0))
logger = logging.getLogger(__name__)
_scheduler_start_lock = threading.Lock()
_scheduler_started = False


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
    def __init__(
        self,
        config: WpsOrderFinanceConfig,
        http: Any = requests,
        persist_rotated_token: bool = False,
    ):
        self.config = config
        self.http = http
        self._access_token = ""
        self._access_token_expires_at = 0.0
        self._persist_rotated_token = persist_rotated_token
        self._refresh_token = (
            _load_persisted_refresh_token(config.app_secret)
            if persist_rotated_token
            else ""
        ) or config.refresh_token

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
            rotated_refresh_token = str(payload["refresh_token"])
            if self._persist_rotated_token:
                _store_persisted_refresh_token(
                    self.config.app_secret,
                    rotated_refresh_token,
                )
            self._refresh_token = rotated_refresh_token
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
                headers=self._authorization_headers(),
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


def _refresh_token_cipher(app_secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(app_secret.encode("utf-8")).digest())
    return Fernet(key)


def _load_persisted_refresh_token(app_secret: str) -> str:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT wps_refresh_token_ciphertext "
            "FROM order_finance_sync_status WHERE id = 1",
        ).fetchone()
    ciphertext = (dict(row).get("wps_refresh_token_ciphertext") if row else "") or ""
    if not ciphertext:
        return ""
    try:
        return _refresh_token_cipher(app_secret).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        return ""


def _store_persisted_refresh_token(app_secret: str, refresh_token: str) -> None:
    ciphertext = _refresh_token_cipher(app_secret).encrypt(refresh_token.encode("utf-8")).decode("ascii")
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            "UPDATE order_finance_sync_status "
            "SET wps_refresh_token_ciphertext = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = 1",
            (ciphertext,),
        )


def due_order_finance_sync_slots(
    now: datetime,
    last_attempt_slot: Optional[str],
) -> list[str]:
    current = now.astimezone(SHANGHAI_TZ) if now.tzinfo else now.replace(tzinfo=SHANGHAI_TZ)
    due = [
        datetime.combine(current.date(), slot_time, SHANGHAI_TZ)
        for slot_time in SYNC_TIMES
        if datetime.combine(current.date(), slot_time, SHANGHAI_TZ) <= current
    ]
    if not due:
        return []
    latest_slot = due[-1].isoformat(timespec="minutes")
    if last_attempt_slot == latest_slot:
        return []
    return [latest_slot]


def _set_remote_source_name(records: list[dict], file_name: str) -> None:
    for record in records:
        record["source_file"] = file_name
        raw_source = record.get("source_json")
        try:
            source = json.loads(raw_source) if isinstance(raw_source, str) else dict(raw_source or {})
        except (TypeError, ValueError, json.JSONDecodeError):
            source = {}
        source["remote_file_name"] = file_name
        record["source_json"] = json.dumps(source, ensure_ascii=False, default=str)


def run_order_finance_wps_sync(
    slot_key: str,
    now: Optional[datetime] = None,
    client: Optional[WpsOrderFinanceClient] = None,
) -> dict:
    if not claim_order_finance_sync_slot(slot_key):
        return {"status": "skipped", "reason": "slot_already_attempted"}
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    active_client = client or WpsOrderFinanceClient(
        WpsOrderFinanceConfig.from_env(),
        persist_rotated_token=True,
    )
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as temp_file:
            temp_path = Path(temp_file.name)
        download = active_client.download_workbook(temp_path)
        source_version = download.source_version
        if not source_version:
            source_version = hashlib.sha256(temp_path.read_bytes()).hexdigest()
        success_at = current.astimezone(SHANGHAI_TZ).isoformat(timespec="seconds")
        previous_status = get_order_finance_sync_status()
        if source_version and previous_status.get("source_version") == source_version:
            record_unchanged_order_finance_sync(success_at, source_version, slot_key)
            return {
                "status": "success", "inserted": 0, "updated": 0,
                "archived": 0, "changed_count": 0,
            }
        parsed = parse_order_finance_directory(temp_path)
        records = parsed["records"]
        _set_remote_source_name(records, download.file_name)
        changes = apply_order_finance_snapshot(
            records,
            imported_by="WPS自动同步",
            sync_success_at=success_at,
            source_version=source_version,
            attempt_slot=slot_key,
        )
        return {"status": "success", **changes}
    except OrderFinanceWpsSyncError:
        raise
    except Exception as exc:
        raise OrderFinanceWpsSyncError("workbook_import") from exc
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def _scheduler_loop(interval_seconds: int, client: WpsOrderFinanceClient) -> None:
    while True:
        try:
            current = datetime.now(SHANGHAI_TZ)
            status = get_order_finance_sync_status()
            for slot_key in due_order_finance_sync_slots(current, status.get("last_attempt_slot")):
                try:
                    run_order_finance_wps_sync(slot_key, now=current, client=client)
                except Exception as exc:
                    logger.error(
                        "order_finance_wps_sync_failed",
                        extra={
                            "sync_stage": getattr(exc, "stage", "unknown"),
                            "error_class": type(exc).__name__,
                        },
                    )
        except Exception as exc:
            logger.error(
                "order_finance_wps_scheduler_failed",
                extra={"error_class": type(exc).__name__},
            )
        time.sleep(interval_seconds)


def start_order_finance_wps_sync_scheduler(interval_seconds: int = 300) -> bool:
    global _scheduler_started
    if (os.getenv("ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED") or "").strip().lower() != "true":
        return False
    try:
        config = WpsOrderFinanceConfig.from_env()
    except OrderFinanceWpsSyncError as exc:
        logger.error(
            "order_finance_wps_scheduler_not_started",
            extra={"sync_stage": exc.stage, "error_class": type(exc).__name__},
        )
        return False
    with _scheduler_start_lock:
        if _scheduler_started:
            return False
        thread = threading.Thread(
            target=_scheduler_loop,
            args=(
                interval_seconds,
                WpsOrderFinanceClient(config, persist_rotated_token=True),
            ),
            name="order-finance-wps-sync",
            daemon=True,
        )
        thread.start()
        _scheduler_started = True
    return True
