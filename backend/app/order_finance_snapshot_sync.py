"""Protected order-finance fact snapshots for cross-environment following."""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from .order_finance import (
    get_order_finance_sync_status,
    list_order_finance_fact_snapshot_records,
    order_finance_facts_hash,
)


router = APIRouter()
SNAPSHOT_SCHEMA_VERSION = 1


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
