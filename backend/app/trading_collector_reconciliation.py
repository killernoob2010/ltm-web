"""Deterministic settlement coverage policy and reconciliation primitives."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import db


MINIMUM_CLIENT_VERSION = "0.2.1"
POLICY_SCHEMA_VERSION = 1
POLICY_CAPABILITIES = [
    "monthly_collection_ranges_v1",
    "per_item_ingest_receipts_v1",
    "future_spread_v1",
    "positions_v2",
]

EXCHANGE_ALIASES = {
    "dce": "dce",
    "大商所": "dce",
    "大连商品交易所": "dce",
    "shfe": "shfe",
    "上期所": "shfe",
    "上海期货交易所": "shfe",
    "czce": "czce",
    "郑商所": "czce",
    "郑州商品交易所": "czce",
    "cffex": "cffex",
    "中金所": "cffex",
    "中国金融期货交易所": "cffex",
    "ine": "ine",
    "能源中心": "ine",
    "上海国际能源交易中心": "ine",
    "gfex": "gfex",
    "广期所": "gfex",
    "广州期货交易所": "gfex",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_exchange(value: str) -> str:
    """Return one stable internal exchange code for Chinese or English aliases."""
    text = str(value or "").strip().lower().replace(" ", "")
    return EXCHANGE_ALIASES.get(text, text)


def normalize_transaction_no(value: object) -> str:
    text = str(value or "").strip().lower()
    return str(int(text)) if text.isdigit() else text


def _normalize_date(value: object) -> Optional[date]:
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text[:8], fmt).date()
        except ValueError:
            continue
    return None


def _complete_month_range(start: object, end: object) -> Optional[tuple[str, str, str]]:
    range_start = _normalize_date(start)
    range_end = _normalize_date(end)
    if not range_start or not range_end:
        return None
    if range_start.day != 1 or range_start.year != range_end.year or range_start.month != range_end.month:
        return None
    if range_end.day != calendar.monthrange(range_end.year, range_end.month)[1]:
        return None
    return (
        range_start.isoformat(),
        range_end.isoformat(),
        "%04d-%02d" % (range_start.year, range_start.month),
    )


def get_active_monthly_ranges(cur, account_id: int) -> list[dict[str, object]]:
    rows = db._exec(
        cur,
        """
        SELECT id, range_start, range_end
        FROM trading_import_batches
        WHERE account_id = ? AND status = 'active' AND statement_type = 'monthly'
        ORDER BY range_start, range_end, id
        """,
        (account_id,),
    ).fetchall()
    ranges: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        normalized = _complete_month_range(row["range_start"], row["range_end"])
        if not normalized:
            continue
        range_start, range_end, month = normalized
        ranges.setdefault(
            (range_start, range_end),
            {
                "month": month,
                "range_start": range_start,
                "range_end": range_end,
                "source_batch_id": row["id"],
            },
        )
    return sorted(ranges.values(), key=lambda item: (str(item["range_start"]), str(item["range_end"])))


def _policy_revision(closed_ranges: Iterable[Mapping[str, object]]) -> str:
    payload = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "minimum_client_version": MINIMUM_CLIENT_VERSION,
        "closed_ranges": [
            {
                "month": item["month"],
                "range_start": item["range_start"],
                "range_end": item["range_end"],
                "source_batch_id": item["source_batch_id"],
            }
            for item in closed_ranges
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_collection_policy(account_id: int) -> dict[str, object]:
    with db.connect() as conn:
        ranges = get_active_monthly_ranges(conn.cursor(), account_id)
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "minimum_client_version": MINIMUM_CLIENT_VERSION,
        "capabilities": list(POLICY_CAPABILITIES),
        "closed_ranges": ranges,
        "policy_revision": _policy_revision(ranges),
        "generated_at": _now(),
    }


def get_device_collection_policy(device_id: int) -> dict[str, object]:
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT account_id, status FROM trading_collector_devices WHERE id = ?",
            (device_id,),
        ).fetchone()
    if not row or row["status"] != "active":
        raise ValueError("设备已暂停或撤销")
    return build_collection_policy(int(row["account_id"]))
