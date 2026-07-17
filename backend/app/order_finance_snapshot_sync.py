"""Protected order-finance fact snapshots for cross-environment following."""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
import requests

from .order_finance import (
    FACT_FIELDS,
    apply_order_finance_snapshot,
    get_active_synced_business_keys,
    get_order_finance_sync_status,
    list_order_finance_fact_snapshot_records,
    order_finance_facts_hash,
    record_pending_order_finance_shrink,
    record_unchanged_order_finance_sync,
    snapshot_business_keys_hash,
)
from .order_finance_wps_sync import (
    SHANGHAI_TZ,
    due_order_finance_sync_slots,
    start_order_finance_wps_sync_scheduler,
)


router = APIRouter()
SNAPSHOT_SCHEMA_VERSION = 1
SNAPSHOT_PATH = "/api/internal/order-finance/snapshot"
REQUEST_TIMEOUT = (10, 60)
logger = logging.getLogger(__name__)
_follower_scheduler_start_lock = threading.Lock()
_follower_scheduler_started = False


class OrderFinanceSnapshotSyncError(RuntimeError):
    def __init__(self, stage: str, status_code: Optional[int] = None):
        self.stage = stage
        self.status_code = status_code
        suffix = f" status={status_code}" if status_code is not None else ""
        super().__init__(f"order_finance_snapshot_sync_failed stage={stage}{suffix}")


@dataclass(frozen=True)
class SnapshotFollowerConfig:
    upstream_url: str
    shared_secret: str

    @classmethod
    def from_env(cls) -> "SnapshotFollowerConfig":
        upstream_url = (
            os.getenv("ORDER_FINANCE_SNAPSHOT_UPSTREAM_URL") or ""
        ).strip().rstrip("/")
        shared_secret = (
            os.getenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET") or ""
        ).strip()
        if not upstream_url or not shared_secret:
            raise OrderFinanceSnapshotSyncError("snapshot_config")
        if not upstream_url.startswith("https://"):
            raise OrderFinanceSnapshotSyncError("snapshot_config")
        return cls(upstream_url=upstream_url, shared_secret=shared_secret)


class OrderFinanceSnapshotClient:
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
                headers={
                    "Authorization": f"Bearer {self.config.shared_secret}",
                },
                timeout=REQUEST_TIMEOUT,
            )
        except Exception as exc:
            raise OrderFinanceSnapshotSyncError("snapshot_download") from exc
        if not 200 <= int(response.status_code) < 300:
            raise OrderFinanceSnapshotSyncError(
                "snapshot_download", int(response.status_code)
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise OrderFinanceSnapshotSyncError("snapshot_decode") from exc
        if not isinstance(payload, dict):
            raise OrderFinanceSnapshotSyncError("snapshot_decode")
        return payload


def _require_snapshot_secret(authorization: Optional[str]) -> None:
    expected = (os.getenv("ORDER_FINANCE_SNAPSHOT_SHARED_SECRET") or "").strip()
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


def build_order_finance_snapshot() -> dict:
    status = get_order_finance_sync_status()
    records = list_order_finance_fact_snapshot_records()
    if not status.get("source_version") or not status.get("last_success_at") or not records:
        raise HTTPException(status_code=503, detail="Snapshot unavailable")
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source_version": status["source_version"],
        "source_success_at": status["last_success_at"],
        "facts_hash": order_finance_facts_hash(records),
        "record_count": len(records),
        "records": records,
    }


@router.get("/internal/order-finance/snapshot")
def get_order_finance_snapshot(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _require_snapshot_secret(authorization)
    return build_order_finance_snapshot()


def _parse_snapshot_time(value: object, stage: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError) as exc:
        raise OrderFinanceSnapshotSyncError(stage) from exc
    if parsed.tzinfo is None:
        raise OrderFinanceSnapshotSyncError(stage)
    return parsed


def _validate_snapshot_payload(payload: dict) -> list[dict]:
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise OrderFinanceSnapshotSyncError("snapshot_schema")
    source_version = str(payload.get("source_version") or "").strip()
    records = payload.get("records")
    if not source_version or not isinstance(records, list) or not records:
        raise OrderFinanceSnapshotSyncError("snapshot_validation")
    try:
        record_count = int(payload.get("record_count"))
    except (TypeError, ValueError) as exc:
        raise OrderFinanceSnapshotSyncError("snapshot_validation") from exc
    if record_count != len(records):
        raise OrderFinanceSnapshotSyncError("snapshot_validation")
    expected_fields = set(FACT_FIELDS)
    if any(not isinstance(record, dict) or set(record) != expected_fields for record in records):
        raise OrderFinanceSnapshotSyncError("snapshot_validation")
    business_keys = [str(record.get("business_key") or "").strip() for record in records]
    if any(not key for key in business_keys) or len(set(business_keys)) != len(business_keys):
        raise OrderFinanceSnapshotSyncError("snapshot_validation")
    supplied_hash = str(payload.get("facts_hash") or "").strip()
    if not supplied_hash or not hmac.compare_digest(
        supplied_hash,
        order_finance_facts_hash(records),
    ):
        raise OrderFinanceSnapshotSyncError("snapshot_hash")
    _parse_snapshot_time(payload.get("source_success_at"), "snapshot_validation")
    return records


def run_order_finance_snapshot_follow(
    slot_key: str,
    now: Optional[datetime] = None,
    client: Optional[OrderFinanceSnapshotClient] = None,
) -> dict:
    del now
    active_client = client or OrderFinanceSnapshotClient(
        SnapshotFollowerConfig.from_env()
    )
    payload = active_client.fetch_snapshot()
    records = _validate_snapshot_payload(payload)
    source_success_at = str(payload["source_success_at"])
    if _parse_snapshot_time(source_success_at, "snapshot_validation") < _parse_snapshot_time(
        slot_key, "slot_validation"
    ):
        return {"status": "deferred", "reason": "source_not_ready"}

    source_version = str(payload["source_version"])
    facts_hash = str(payload["facts_hash"])
    previous_status = get_order_finance_sync_status()
    current_records = list_order_finance_fact_snapshot_records()
    if (
        previous_status.get("source_version") == source_version
        and current_records
        and hmac.compare_digest(
            order_finance_facts_hash(current_records),
            facts_hash,
        )
    ):
        record_unchanged_order_finance_sync(
            source_success_at,
            source_version,
            slot_key,
        )
        return {
            "status": "success",
            "inserted": 0,
            "updated": 0,
            "archived": 0,
            "changed_count": 0,
        }

    incoming_keys = {
        str(record["business_key"]).strip()
        for record in records
    }
    active_keys = get_active_synced_business_keys()
    business_keys_hash = snapshot_business_keys_hash(records)
    if active_keys - incoming_keys and not (
        previous_status.get("pending_source_version") == source_version
        and previous_status.get("pending_business_keys_hash") == business_keys_hash
    ):
        record_pending_order_finance_shrink(
            source_version,
            business_keys_hash,
            len(records),
            None,
        )
        return {
            "status": "deferred",
            "reason": "source_shrink_confirmation",
            "changed_count": 0,
        }

    changes = apply_order_finance_snapshot(
        records,
        imported_by="WPS快照跟随",
        sync_success_at=source_success_at,
        source_version=source_version,
        attempt_slot=slot_key,
    )
    return {"status": "success", **changes}


def _snapshot_follower_loop(
    interval_seconds: int,
    client: OrderFinanceSnapshotClient,
) -> None:
    while True:
        try:
            current = datetime.now(SHANGHAI_TZ)
            status = get_order_finance_sync_status()
            slots = due_order_finance_sync_slots(
                current,
                status.get("last_attempt_slot"),
            )
            for slot_key in slots:
                try:
                    run_order_finance_snapshot_follow(
                        slot_key,
                        now=current,
                        client=client,
                    )
                except Exception as exc:
                    logger.error(
                        "order_finance_snapshot_follow_failed",
                        extra={
                            "sync_stage": getattr(exc, "stage", "unknown"),
                            "http_status": getattr(exc, "status_code", None),
                            "error_class": type(exc).__name__,
                        },
                    )
        except Exception as exc:
            logger.error(
                "order_finance_snapshot_scheduler_failed",
                extra={"error_class": type(exc).__name__},
            )
        time.sleep(interval_seconds)


def _start_snapshot_follower_scheduler(interval_seconds: int = 300) -> bool:
    global _follower_scheduler_started
    try:
        config = SnapshotFollowerConfig.from_env()
    except OrderFinanceSnapshotSyncError as exc:
        logger.error(
            "order_finance_snapshot_scheduler_not_started",
            extra={
                "sync_stage": exc.stage,
                "error_class": type(exc).__name__,
            },
        )
        return False
    with _follower_scheduler_start_lock:
        if _follower_scheduler_started:
            return False
        thread = threading.Thread(
            target=_snapshot_follower_loop,
            args=(interval_seconds, OrderFinanceSnapshotClient(config)),
            name="order-finance-snapshot-follower",
            daemon=True,
        )
        thread.start()
        _follower_scheduler_started = True
    return True


def start_order_finance_sync_scheduler(interval_seconds: int = 300) -> bool:
    enabled = (
        os.getenv("ORDER_FINANCE_WPS_AUTO_SYNC_ENABLED") or ""
    ).strip().lower()
    if enabled != "true":
        return False
    mode = (os.getenv("ORDER_FINANCE_SYNC_MODE") or "").strip().lower()
    if mode == "wps_source":
        return start_order_finance_wps_sync_scheduler(interval_seconds)
    if mode == "snapshot_follower":
        return _start_snapshot_follower_scheduler(interval_seconds)
    logger.error(
        "order_finance_sync_scheduler_not_started",
        extra={"sync_stage": "sync_mode"},
    )
    return False
