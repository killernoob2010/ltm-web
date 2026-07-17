"""Protected iron-ore basis snapshots for Staging-to-Production following."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
import requests

from . import db
from .iron_ore_basis_import import DETAIL_COLUMNS, RESULT_COLUMNS
from .iron_ore_basis_sync import (
    API_START_DATE,
    SHANGHAI_TZ,
    auto_sync_enabled,
    start_iron_ore_basis_source_scheduler,
)


router = APIRouter()
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_PATH = "/api/internal/iron-ore-basis/snapshot"
REQUEST_TIMEOUT = (10, 60)
RESULT_FIELDS = tuple(RESULT_COLUMNS)
DETAIL_FIELDS = tuple(
    column for column in DETAIL_COLUMNS if column != "result_id"
)
logger = logging.getLogger(__name__)
_follower_scheduler_start_lock = threading.Lock()
_follower_scheduler_started = False


class IronOreBasisSnapshotSyncError(RuntimeError):
    def __init__(self, stage: str, status_code: Optional[int] = None):
        self.stage = stage
        self.status_code = status_code
        suffix = f" status={status_code}" if status_code is not None else ""
        super().__init__(f"iron_ore_basis_snapshot_sync_failed stage={stage}{suffix}")


def _snapshot_shared_secret() -> str:
    return (
        os.getenv("IRON_ORE_BASIS_SNAPSHOT_SHARED_SECRET")
        or os.getenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET")
        or ""
    ).strip()


@dataclass(frozen=True)
class SnapshotFollowerConfig:
    upstream_url: str
    shared_secret: str

    @classmethod
    def from_env(cls) -> "SnapshotFollowerConfig":
        upstream_url = (
            os.getenv("IRON_ORE_BASIS_SNAPSHOT_UPSTREAM_URL") or ""
        ).strip().rstrip("/")
        shared_secret = _snapshot_shared_secret()
        if not upstream_url or not shared_secret or not upstream_url.startswith("https://"):
            raise IronOreBasisSnapshotSyncError("snapshot_config")
        return cls(upstream_url=upstream_url, shared_secret=shared_secret)


class IronOreBasisSnapshotClient:
    def __init__(
        self,
        config: SnapshotFollowerConfig,
        http: Any = requests,
    ):
        self.config = config
        self.http = http

    def fetch_snapshot(self) -> dict:
        try:
            response = self.http.request(
                "GET",
                f"{self.config.upstream_url}{SNAPSHOT_PATH}",
                headers={"Authorization": f"Bearer {self.config.shared_secret}"},
                timeout=REQUEST_TIMEOUT,
            )
        except Exception as exc:
            raise IronOreBasisSnapshotSyncError("snapshot_download") from exc
        if not 200 <= int(response.status_code) < 300:
            raise IronOreBasisSnapshotSyncError(
                "snapshot_download", int(response.status_code)
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise IronOreBasisSnapshotSyncError("snapshot_decode") from exc
        if not isinstance(payload, dict):
            raise IronOreBasisSnapshotSyncError("snapshot_decode")
        return payload


def _require_snapshot_secret(authorization: Optional[str]) -> None:
    expected = _snapshot_shared_secret()
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix):].strip()
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if (
        not expected
        or not supplied
        or not hmac.compare_digest(expected, supplied)
    ):
        raise HTTPException(status_code=404, detail="Not Found")


def basis_snapshot_hash(records: list[dict]) -> str:
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_records() -> list[dict]:
    with db.connect() as conn:
        cur = conn.cursor()
        result_rows = db._exec(
            cur,
            f"""SELECT {', '.join(RESULT_FIELDS)}
                FROM iron_ore_basis_results
                WHERE business_date >= ? AND source_workbook_name = 'API:EBC+Sina'
                ORDER BY business_date, port, product, business_key""",
            (API_START_DATE.isoformat(),),
        ).fetchall()
        detail_rows = db._exec(
            cur,
            f"""SELECT {', '.join(DETAIL_FIELDS)}
                FROM iron_ore_basis_details
                WHERE business_date >= ? AND source_workbook_name = 'API:EBC+Sina'""",
            (API_START_DATE.isoformat(),),
        ).fetchall()
    details = {row["business_key"]: dict(row) for row in detail_rows}
    records = []
    for row in result_rows:
        business_key = row["business_key"]
        detail = details.get(business_key)
        if detail is None:
            raise HTTPException(status_code=503, detail="Snapshot unavailable")
        records.append({"result": dict(row), "detail": detail})
    if len(details) != len(records):
        raise HTTPException(status_code=503, detail="Snapshot unavailable")
    return records


def build_iron_ore_basis_snapshot() -> dict:
    if (os.getenv("IRON_ORE_BASIS_SYNC_MODE") or "").strip().lower() != "source":
        raise HTTPException(status_code=503, detail="Snapshot unavailable")
    with db.connect() as conn:
        run = db._exec(
            conn.cursor(),
            """SELECT slot_key, target_end_date, status, finished_at
               FROM iron_ore_basis_sync_runs
               WHERE status IN ('success', 'partial')
                 AND trigger_type <> 'snapshot_follower'
               ORDER BY finished_at DESC, id DESC
               LIMIT 1""",
        ).fetchone()
    records = _snapshot_records()
    if not run or not records:
        raise HTTPException(status_code=503, detail="Snapshot unavailable")
    latest_business_date = max(
        record["result"]["business_date"] for record in records
    )
    if str(run["target_end_date"]) < str(latest_business_date):
        raise HTTPException(status_code=503, detail="Snapshot unavailable")
    snapshot_hash = basis_snapshot_hash(records)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source_version": snapshot_hash,
        "source_slot_key": run["slot_key"],
        "source_status": run["status"],
        "approved_at": run["finished_at"],
        "latest_business_date": latest_business_date,
        "snapshot_hash": snapshot_hash,
        "record_count": len(records),
        "records": records,
    }


@router.get("/internal/iron-ore-basis/snapshot")
def get_iron_ore_basis_snapshot(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _require_snapshot_secret(authorization)
    return build_iron_ore_basis_snapshot()


def _validate_snapshot_payload(payload: dict) -> list[dict]:
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise IronOreBasisSnapshotSyncError("snapshot_schema")
    if (
        payload.get("source_status") not in {"success", "partial"}
        or not str(payload.get("source_slot_key") or "").strip()
        or not str(payload.get("approved_at") or "").strip()
    ):
        raise IronOreBasisSnapshotSyncError("snapshot_validation")
    records = payload.get("records")
    try:
        record_count = int(payload.get("record_count"))
    except (TypeError, ValueError) as exc:
        raise IronOreBasisSnapshotSyncError("snapshot_validation") from exc
    if not isinstance(records, list) or not records or record_count != len(records):
        raise IronOreBasisSnapshotSyncError("snapshot_validation")
    business_keys = []
    business_dates = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"result", "detail"}:
            raise IronOreBasisSnapshotSyncError("snapshot_validation")
        result = record["result"]
        detail = record["detail"]
        if (
            not isinstance(result, dict)
            or not isinstance(detail, dict)
            or set(result) != set(RESULT_FIELDS)
            or set(detail) != set(DETAIL_FIELDS)
        ):
            raise IronOreBasisSnapshotSyncError("snapshot_validation")
        result_key = str(result.get("business_key") or "").strip()
        detail_key = str(detail.get("business_key") or "").strip()
        if not result_key or result_key != detail_key:
            raise IronOreBasisSnapshotSyncError("snapshot_validation")
        business_keys.append(result_key)
        business_dates.append(str(result.get("business_date") or ""))
    if len(set(business_keys)) != len(business_keys):
        raise IronOreBasisSnapshotSyncError("snapshot_validation")
    if str(payload.get("latest_business_date") or "") != max(business_dates):
        raise IronOreBasisSnapshotSyncError("snapshot_validation")
    supplied_hash = str(payload.get("snapshot_hash") or "").strip()
    source_version = str(payload.get("source_version") or "").strip()
    calculated_hash = basis_snapshot_hash(records)
    if (
        not supplied_hash
        or not source_version
        or not hmac.compare_digest(supplied_hash, calculated_hash)
        or not hmac.compare_digest(source_version, supplied_hash)
    ):
        raise IronOreBasisSnapshotSyncError("snapshot_hash")
    return records


def _row_matches(row: Any, expected: dict, fields: tuple[str, ...]) -> bool:
    return row is not None and all(row[field] == expected[field] for field in fields)


def _apply_snapshot_records(records: list[dict], payload: dict) -> int:
    with db.connect() as conn:
        cur = conn.cursor()
        missing = []
        for record in records:
            result = record["result"]
            detail = record["detail"]
            existing_result = db._exec(
                cur,
                f"""SELECT {', '.join(RESULT_FIELDS)}
                    FROM iron_ore_basis_results WHERE business_key = ?""",
                (result["business_key"],),
            ).fetchone()
            if existing_result is None:
                missing.append(record)
                continue
            existing_detail = db._exec(
                cur,
                f"""SELECT {', '.join(DETAIL_FIELDS)}
                    FROM iron_ore_basis_details WHERE business_key = ?""",
                (result["business_key"],),
            ).fetchone()
            if not _row_matches(existing_result, result, RESULT_FIELDS) or not _row_matches(
                existing_detail, detail, DETAIL_FIELDS
            ):
                raise IronOreBasisSnapshotSyncError("snapshot_difference")

        for record in missing:
            result = record["result"]
            detail = record["detail"]
            db._exec(
                cur,
                f"""INSERT INTO iron_ore_basis_results ({', '.join(RESULT_FIELDS)})
                    VALUES ({', '.join('?' for _ in RESULT_FIELDS)})""",
                tuple(result[field] for field in RESULT_FIELDS),
            )
            result_id = db.last_insert_id(cur.connection)
            detail_values = {"result_id": result_id, **detail}
            db._exec(
                cur,
                f"""INSERT INTO iron_ore_basis_details ({', '.join(DETAIL_COLUMNS)})
                    VALUES ({', '.join('?' for _ in DETAIL_COLUMNS)})""",
                tuple(detail_values[field] for field in DETAIL_COLUMNS),
            )
        source_version = str(payload["source_version"])
        latest_date = str(payload["latest_business_date"])
        db._exec(
            cur,
            """INSERT INTO iron_ore_basis_sync_runs
               (slot_key, trigger_type, target_start_date, target_end_date, status,
                combinations_written, finished_at)
               VALUES (?, 'snapshot_follower', ?, ?, 'success', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(slot_key) DO NOTHING""",
            (
                f"snapshot:{source_version}",
                API_START_DATE.isoformat(),
                latest_date,
                len(missing),
            ),
        )
    return len(missing)


def run_iron_ore_basis_snapshot_follow(
    slot_key: str,
    *,
    client: Optional[IronOreBasisSnapshotClient] = None,
) -> dict:
    del slot_key
    active_client = client or IronOreBasisSnapshotClient(
        SnapshotFollowerConfig.from_env()
    )
    payload = active_client.fetch_snapshot()
    records = _validate_snapshot_payload(payload)
    db.init_db()
    inserted = _apply_snapshot_records(records, payload)
    return {"status": "success", "inserted": inserted}


def _snapshot_follower_loop(
    interval_seconds: int,
    client: IronOreBasisSnapshotClient,
) -> None:
    while True:
        current = datetime.now(SHANGHAI_TZ)
        try:
            run_iron_ore_basis_snapshot_follow(
                f"poll:{current.isoformat(timespec='minutes')}",
                client=client,
            )
        except Exception as exc:
            logger.error(
                "iron_ore_basis_snapshot_follow_failed",
                extra={
                    "sync_stage": getattr(exc, "stage", "unknown"),
                    "http_status": getattr(exc, "status_code", None),
                    "error_class": type(exc).__name__,
                },
            )
        time.sleep(interval_seconds)


def _start_snapshot_follower_scheduler(interval_seconds: int = 300) -> bool:
    global _follower_scheduler_started
    try:
        config = SnapshotFollowerConfig.from_env()
    except IronOreBasisSnapshotSyncError as exc:
        logger.error(
            "iron_ore_basis_snapshot_scheduler_not_started",
            extra={"sync_stage": exc.stage, "error_class": type(exc).__name__},
        )
        return False
    with _follower_scheduler_start_lock:
        if _follower_scheduler_started:
            return False
        thread = threading.Thread(
            target=_snapshot_follower_loop,
            args=(interval_seconds, IronOreBasisSnapshotClient(config)),
            name="iron-ore-basis-snapshot-follower",
            daemon=True,
        )
        thread.start()
        _follower_scheduler_started = True
    return True


def start_iron_ore_basis_sync_scheduler(interval_seconds: int = 300) -> bool:
    if not auto_sync_enabled():
        return False
    mode = (os.getenv("IRON_ORE_BASIS_SYNC_MODE") or "").strip().lower()
    if mode == "source":
        return start_iron_ore_basis_source_scheduler(interval_seconds)
    if mode == "snapshot_follower":
        return _start_snapshot_follower_scheduler(interval_seconds)
    logger.error(
        "iron_ore_basis_sync_scheduler_not_started",
        extra={"sync_stage": "sync_mode"},
    )
    return False
