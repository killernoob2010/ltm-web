"""Deterministic settlement coverage policy and reconciliation primitives."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional
from zoneinfo import ZoneInfo

from . import db


MINIMUM_CLIENT_VERSION = "0.3.0"
POLICY_SCHEMA_VERSION = 2
COLLECTOR_ENVIRONMENTS = {"staging", "production"}
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
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


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


def _policy_revision(
    *,
    environment: str,
    history_start_date: str,
    current_trade_date: str,
    upload_ranges: Iterable[Mapping[str, object]],
    closed_ranges: Iterable[Mapping[str, object]],
) -> str:
    payload = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "environment": environment,
        "history_start_date": history_start_date,
        "current_trade_date": current_trade_date,
        "minimum_client_version": MINIMUM_CLIENT_VERSION,
        "upload_ranges": [
            {
                "range_start": item["range_start"],
                "range_end": item["range_end"],
            }
            for item in upload_ranges
        ],
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


def _policy_date(value: object) -> Optional[date]:
    return _normalize_date(value)


def _business_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _account_collection_policy(cur, account_id: int) -> Dict[str, object]:
    row = db._exec(
        cur,
        """
        SELECT environment, history_start_date
        FROM trading_collector_account_policies
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    if not row:
        raise ValueError("交易账户尚未配置 WH6 采集日期策略")
    environment = str(row["environment"] or "").strip().lower()
    if environment not in COLLECTOR_ENVIRONMENTS:
        raise ValueError("交易账户采集策略环境无效")
    history_start = _policy_date(row["history_start_date"])
    if not history_start:
        raise ValueError("交易账户采集起始日期无效")
    return {
        "environment": environment,
        "configured_history_start_date": history_start,
    }


def is_date_uploadable(
    cur,
    account_id: int,
    trade_date: object,
    *,
    as_of_date: Optional[object] = None,
) -> bool:
    """Check the server-side positive upload boundary for one business date."""

    parsed_trade_date = _policy_date(trade_date)
    if not parsed_trade_date:
        return False
    account_policy = _account_collection_policy(cur, account_id)
    configured_start = account_policy["configured_history_start_date"]
    assert isinstance(configured_start, date)
    current_trade_date = _policy_date(as_of_date) or _business_today()
    if parsed_trade_date < configured_start or parsed_trade_date > current_trade_date:
        return False
    return _closed_range_for_date(cur, account_id, parsed_trade_date.isoformat()) is None


def _policy_open_ranges(
    start: date,
    end: date,
    closed_ranges: Iterable[Mapping[str, object]],
) -> list[dict[str, str]]:
    closed_days = {
        current
        for item in closed_ranges
        for current in _date_range(
            _policy_date(item["range_start"]),
            _policy_date(item["range_end"]),
        )
    }
    ranges: list[dict[str, str]] = []
    open_start: Optional[date] = None
    current = start
    while current <= end:
        if current in closed_days:
            if open_start is not None:
                ranges.append({"range_start": open_start.isoformat(), "range_end": (current - timedelta(days=1)).isoformat()})
                open_start = None
        elif open_start is None:
            open_start = current
        current += timedelta(days=1)
    if open_start is not None:
        ranges.append({"range_start": open_start.isoformat(), "range_end": end.isoformat()})
    return ranges


def _date_range(start: Optional[date], end: Optional[date]) -> Iterable[date]:
    if not start or not end or start > end:
        return ()
    return (start + timedelta(days=offset) for offset in range((end - start).days + 1))


def build_collection_policy(account_id: int, *, as_of_date: Optional[object] = None) -> dict[str, object]:
    current_trade_date = _policy_date(as_of_date) or _business_today()
    with db.connect() as conn:
        cur = conn.cursor()
        account_policy = _account_collection_policy(cur, account_id)
        closed_ranges = get_active_monthly_ranges(cur, account_id)
    configured_start = account_policy["configured_history_start_date"]
    assert isinstance(configured_start, date)
    if current_trade_date < configured_start:
        effective_start = configured_start
        upload_ranges: list[dict[str, str]] = []
    else:
        upload_ranges = _policy_open_ranges(configured_start, current_trade_date, closed_ranges)
        effective_start = _policy_date(upload_ranges[0]["range_start"]) if upload_ranges else current_trade_date
    effective_start_text = effective_start.isoformat() if effective_start else current_trade_date.isoformat()
    revision = _policy_revision(
        environment=str(account_policy["environment"]),
        history_start_date=effective_start_text,
        current_trade_date=current_trade_date.isoformat(),
        upload_ranges=upload_ranges,
        closed_ranges=closed_ranges,
    )
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "environment": account_policy["environment"],
        "history_start_date": effective_start_text,
        "upload_ranges": upload_ranges,
        "minimum_client_version": MINIMUM_CLIENT_VERSION,
        "capabilities": list(POLICY_CAPABILITIES),
        "closed_ranges": closed_ranges,
        "current_trade_date": current_trade_date.isoformat(),
        "policy_revision": revision,
        "generated_at": _now(),
    }


def get_device_collection_policy(device_id: int) -> dict[str, object]:
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT account_id, environment, status FROM trading_collector_devices WHERE id = ?",
            (device_id,),
        ).fetchone()
    if not row or row["status"] != "active":
        raise ValueError("设备已暂停或撤销")
    policy = build_collection_policy(int(row["account_id"]))
    if str(policy["environment"]) != str(row["environment"] or "staging").strip().lower():
        raise ValueError("设备环境与账户采集策略不一致")
    return policy


def _raw_transaction_no(raw_json: object) -> str:
    try:
        payload = json.loads(str(raw_json or ""))
    except (TypeError, ValueError):
        return ""
    if isinstance(payload, Mapping):
        for key in ("transaction_no", "成交序号", "成交编号", "transaction number"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value).strip()
        columns = payload.get("columns")
        if isinstance(columns, list) and len(columns) > 15:
            return str(columns[15] or "").strip()
    return ""


def backfill_settlement_transaction_numbers(cur, *, account_id: int | None = None) -> int:
    """Backfill only empty settlement keys from retained source-row evidence."""
    sql = """
        SELECT tf.id, sr.raw_json
        FROM trading_trade_facts tf
        JOIN trading_source_rows sr ON sr.id = tf.source_row_id
        JOIN trading_import_batches b ON b.id = tf.batch_id
        WHERE (tf.normalized_transaction_no IS NULL OR tf.normalized_transaction_no = '')
    """
    params: list[object] = []
    if account_id is not None:
        sql += " AND b.account_id = ?"
        params.append(account_id)
    rows = db._exec(cur, sql, tuple(params)).fetchall()
    updates = []
    for row in rows:
        transaction_no = _raw_transaction_no(row["raw_json"])
        normalized = normalize_transaction_no(transaction_no)
        if not normalized:
            continue
        updates.append((transaction_no, normalized, row["id"]))
    if updates:
        db._executemany(
            cur,
            """
            UPDATE trading_trade_facts
            SET transaction_no = ?, normalized_transaction_no = ?
            WHERE id = ? AND (normalized_transaction_no IS NULL OR normalized_transaction_no = '')
            """,
            updates,
        )
    return len(updates)


def finalize_lower_priority_monthly_trades(cur, batch_id: int) -> dict[str, int]:
    """Retire lower-priority current trades absent from a complete monthly set."""
    batch = db._exec(
        cur,
        """
        SELECT id, account_id, range_start, range_end, status, statement_type,
               source_priority
        FROM trading_import_batches WHERE id = ?
        """,
        (batch_id,),
    ).fetchone()
    if not batch or batch["statement_type"] != "monthly":
        raise ValueError("只有月结批次可以收口低优先级成交")
    normalized_range = _complete_month_range(batch["range_start"], batch["range_end"])
    if not normalized_range:
        # A monthly-labelled statement can still be useful for field-level
        # replacement before the complete natural-month close is available.
        return {"retired": 0, "audited": 0}
    range_start, range_end, _month = normalized_range
    monthly_identities = {
        int(row["identity_id"])
        for row in db._exec(
            cur,
            "SELECT identity_id FROM trading_trade_facts WHERE batch_id = ?",
            (batch_id,),
        ).fetchall()
    }
    candidates = db._exec(
        cur,
        """
        SELECT tf.id, tf.identity_id, tf.batch_id, tf.trade_date,
               b.source_priority
        FROM trading_trade_facts tf
        JOIN trading_import_batches b ON b.id = tf.batch_id
        WHERE b.account_id = ? AND b.status = 'active'
          AND b.id <> ? AND tf.is_current = 1
          AND b.source_priority < ?
        ORDER BY tf.id
        """,
        (batch["account_id"], batch_id, int(batch["source_priority"] or 200)),
    ).fetchall()
    retired = audited = 0
    for row in candidates:
        trade_date = _normalize_date(row["trade_date"])
        if not trade_date or not (range_start <= trade_date.isoformat() <= range_end):
            continue
        if int(row["identity_id"]) in monthly_identities:
            continue
        updated = db._exec(
            cur,
            "UPDATE trading_trade_facts SET is_current = 0 WHERE id = ? AND is_current = 1",
            (row["id"],),
        )
        if updated.rowcount != 1:
            continue
        retired += 1
        db._exec(
            cur,
            """
            INSERT INTO trading_fact_source_differences
                (identity_id, fact_type, old_batch_id, new_batch_id, diff_json)
            VALUES (?, 'trade', ?, ?, ?)
            """,
            (
                row["identity_id"],
                row["batch_id"],
                batch_id,
                json.dumps(
                    {
                        "change_type": "absent_from_monthly",
                        "trade_date": row["trade_date"],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        audited += 1
    return {"retired": retired, "audited": audited}


MATCH_STATUSES = {
    "unmatched",
    "ambiguous",
    "matched_daily",
    "corrected_daily",
    "matched_monthly",
    "corrected_monthly",
    "monthly_unmatched",
}
STATEMENT_OWNED_FIELDS = (
    "exchange",
    "contract",
    "asset_type",
    "side",
    "open_close",
    "quantity",
    "price",
    "turnover",
    "fee",
    "hedge_flag",
    "premium_cashflow",
    "close_profit",
)
WH6_ONLY_FIELDS = (
    "trade_date",
    "trade_time",
    "trade_timestamp",
    "raw_contract",
    "trade_id",
    "order_id",
    "option_kind",
    "underlying",
    "expiry_month",
    "strike",
    "source_record_sha256",
    "source_path",
    "source_record_index",
    "parser_version",
)
NUMERIC_FIELDS = {
    "quantity",
    "price",
    "turnover",
    "fee",
    "premium_cashflow",
    "close_profit",
}


@dataclass(frozen=True)
class MatchDecision:
    status: str
    settlement: Optional[Dict[str, Any]] = None
    identity_id: Optional[int] = None
    batch_id: Optional[int] = None
    authority_type: str = "wh6"
    source_priority: int = 0
    candidate_count: int = 0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in MATCH_STATUSES:
            raise ValueError("未知的 WH6 协调状态")


@dataclass(frozen=True)
class ResolvedFill:
    fields: Dict[str, Any]
    field_sources: Dict[str, Optional[str]]
    differences: Dict[str, Dict[str, Any]]
    authority_type: str

    @property
    def resolved_fields(self) -> Dict[str, Any]:
        return self.fields


@dataclass
class ReconciliationSummary:
    scanned: int = 0
    matched_daily: int = 0
    corrected_daily: int = 0
    matched_monthly: int = 0
    corrected_monthly: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    monthly_unmatched: int = 0
    covered: int = 0
    conflicts: int = 0
    changed: int = 0
    unchanged: int = 0

    @property
    def corrected(self) -> int:
        return self.corrected_daily + self.corrected_monthly

    @property
    def matched(self) -> int:
        return self.matched_daily + self.matched_monthly

    def add(self, status: str, *, covered: bool, changed: bool) -> None:
        self.scanned += 1
        if status == "matched_daily":
            self.matched_daily += 1
        elif status == "corrected_daily":
            self.corrected_daily += 1
        elif status == "matched_monthly":
            self.matched_monthly += 1
        elif status == "corrected_monthly":
            self.corrected_monthly += 1
        elif status == "unmatched":
            self.unmatched += 1
        elif status == "ambiguous":
            self.ambiguous += 1
        elif status == "monthly_unmatched":
            self.monthly_unmatched += 1
        if covered:
            self.covered += 1
        if status in {"ambiguous", "monthly_unmatched"}:
            self.conflicts += 1
        if changed:
            self.changed += 1
        else:
            self.unchanged += 1

    def to_dict(self) -> Dict[str, int]:
        return {
            "scanned": self.scanned,
            "matched": self.matched,
            "corrected": self.corrected,
            "matched_daily": self.matched_daily,
            "corrected_daily": self.corrected_daily,
            "matched_monthly": self.matched_monthly,
            "corrected_monthly": self.corrected_monthly,
            "unmatched": self.unmatched,
            "ambiguous": self.ambiguous,
            "monthly_unmatched": self.monthly_unmatched,
            "covered": self.covered,
            "conflicts": self.conflicts,
            "changed": self.changed,
            "unchanged": self.unchanged,
        }


def _row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _row_dict(row: Mapping[str, Any]) -> Dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    keys = row.keys() if hasattr(row, "keys") else ()
    return {key: row[key] for key in keys}


def _normalize_side(value: object) -> str:
    return {
        "buy": "买",
        "sell": "卖",
        "买入": "买",
        "卖出": "卖",
    }.get(str(value or "").strip().lower(), str(value or "").strip())


def _normalize_open_close(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "open": "开",
        "开仓": "开",
        "close": "平",
        "平仓": "平",
    }.get(text, text)


def _normalize_contract(value: object) -> str:
    return str(value or "").strip().lower()


def _decimal(value: object) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _present(value: object) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _values_equal(field: str, left: object, right: object) -> bool:
    if not _present(left) and not _present(right):
        return True
    if field in NUMERIC_FIELDS:
        left_number = _decimal(left)
        right_number = _decimal(right)
        if left_number is not None and right_number is not None:
            return left_number == right_number
    if field == "exchange":
        return normalize_exchange(str(left or "")) == normalize_exchange(str(right or ""))
    if field == "contract":
        return _normalize_contract(left) == _normalize_contract(right)
    if field == "side":
        return _normalize_side(left) == _normalize_side(right)
    if field == "open_close":
        return _normalize_open_close(left) == _normalize_open_close(right)
    return str(left or "").strip() == str(right or "").strip()


def _same_settlement_identity(fill: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    comparisons = (
        ("contract", _normalize_contract),
        ("side", _normalize_side),
        ("open_close", _normalize_open_close),
    )
    for field, normalizer in comparisons:
        if normalizer(_row_value(fill, field)) != normalizer(_row_value(row, field)):
            return False
    for field in ("quantity", "price"):
        left = _decimal(_row_value(fill, field))
        right = _decimal(_row_value(row, field))
        if left is None or right is None or left != right:
            return False
    return True


def _settlement_rows_for_date(cur, account_id: int, trade_date: str) -> List[Dict[str, Any]]:
    rows = db._exec(
        cur,
        """
        SELECT tf.*, fi.account_id AS identity_account_id,
               b.statement_type, b.source_priority, b.status AS batch_status,
               b.range_start, b.range_end
        FROM trading_trade_facts tf
        JOIN trading_fact_identities fi ON fi.id = tf.identity_id
        JOIN trading_import_batches b ON b.id = tf.batch_id
        WHERE fi.account_id = ? AND b.account_id = ? AND b.status = 'active'
          AND b.statement_type IN ('daily', 'monthly') AND tf.is_current = 1
          AND tf.trade_date = ?
        ORDER BY tf.id DESC
        """,
        (account_id, account_id, trade_date),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _settlement_versions(cur, identity_id: int) -> List[Dict[str, Any]]:
    rows = db._exec(
        cur,
        """
        SELECT tf.*, b.statement_type, b.source_priority, b.status AS batch_status,
               b.range_start, b.range_end
        FROM trading_trade_facts tf
        JOIN trading_import_batches b ON b.id = tf.batch_id
        WHERE tf.identity_id = ? AND b.status = 'active'
          AND b.statement_type IN ('daily', 'monthly')
        ORDER BY b.source_priority DESC, b.id DESC, tf.id DESC
        """,
        (identity_id,),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _source_type(row: Mapping[str, Any]) -> str:
    statement_type = str(_row_value(row, "statement_type") or "").strip().lower()
    return statement_type if statement_type in {"daily", "monthly"} else "wh6"


def _closed_range_for_date(cur, account_id: int, trade_date: str) -> Optional[Dict[str, object]]:
    for item in get_active_monthly_ranges(cur, account_id):
        if str(item["range_start"]) <= trade_date <= str(item["range_end"]):
            return item
    return None


def _merged_settlement(versions: Sequence[Mapping[str, Any]]) -> tuple[Dict[str, Any], str, int, int]:
    if not versions:
        raise ValueError("缺少结算事实版本")
    by_source: Dict[str, Mapping[str, Any]] = {}
    for row in versions:
        source = _source_type(row)
        by_source.setdefault(source, row)
    ordered_sources = [source for source in ("monthly", "daily") if source in by_source]
    authority_type = ordered_sources[0] if ordered_sources else "wh6"
    authority = dict(by_source[authority_type]) if authority_type != "wh6" else {}
    fallbacks = [
        (source, dict(by_source[source]))
        for source in ordered_sources
        if source != authority_type
    ]
    authority["_fallback_sources"] = fallbacks
    authority["_authority_type"] = authority_type
    authority_batch_id = int(_row_value(by_source[authority_type], "batch_id"))
    source_priority = int(_row_value(by_source[authority_type], "source_priority") or 0)
    identity_id = int(_row_value(by_source[authority_type], "identity_id"))
    return authority, authority_type, source_priority, identity_id


def match_intraday_fill(cur, fill: Mapping[str, object]) -> MatchDecision:
    """Find one active settlement identity without guessing across candidates."""
    account_id = int(_row_value(fill, "account_id") or 0)
    normalized_date = _normalize_date(_row_value(fill, "trade_date"))
    if not account_id or not normalized_date:
        return MatchDecision("unmatched", reason="invalid_fill_identity")
    trade_date = normalized_date.isoformat()
    normalized_exchange = normalize_exchange(str(_row_value(fill, "exchange") or ""))
    normalized_id = normalize_transaction_no(_row_value(fill, "trade_id"))
    rows = [
        row
        for row in _settlement_rows_for_date(cur, account_id, trade_date)
        if normalize_exchange(str(_row_value(row, "exchange") or "")) == normalized_exchange
    ]
    if normalized_id:
        rows = [
            row
            for row in rows
            if normalize_transaction_no(
                _row_value(row, "normalized_transaction_no")
                or _row_value(row, "transaction_no")
            ) == normalized_id
        ]
    else:
        rows = [row for row in rows if _same_settlement_identity(fill, row)]
    identity_rows: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        identity_id = int(_row_value(row, "identity_id"))
        identity_rows.setdefault(identity_id, row)
    if len(identity_rows) != 1:
        closed = _closed_range_for_date(cur, account_id, trade_date)
        status = "monthly_unmatched" if not identity_rows and closed else "ambiguous" if len(identity_rows) > 1 else "unmatched"
        return MatchDecision(status, candidate_count=len(identity_rows), reason="no_unique_settlement_candidate")

    identity_id, anchor = next(iter(identity_rows.items()))
    versions = _settlement_versions(cur, identity_id)
    settlement, authority_type, source_priority, _ = _merged_settlement(versions)
    closed = _closed_range_for_date(cur, account_id, trade_date)
    if closed and authority_type != "monthly":
        return MatchDecision(
            "monthly_unmatched",
            candidate_count=1,
            reason="monthly_closed_without_monthly_identity",
        )
    resolved = resolve_fill_fields(fill, settlement, authority_type)
    corrected = bool(resolved.differences)
    if authority_type == "monthly":
        status = "corrected_monthly" if corrected else "matched_monthly"
    else:
        status = "corrected_daily" if corrected else "matched_daily"
    return MatchDecision(
        status,
        settlement=settlement,
        identity_id=identity_id,
        batch_id=int(_row_value(anchor, "batch_id")),
        authority_type=authority_type,
        source_priority=source_priority,
        candidate_count=1,
    )


def resolve_fill_fields(
    wh6: Mapping[str, object],
    settlement: Optional[Mapping[str, object]],
    authority_type: str,
) -> ResolvedFill:
    """Project one displayable fill while preserving every source's evidence."""
    authority_type = authority_type if authority_type in {"monthly", "daily"} else "wh6"
    source_records: List[tuple[str, Mapping[str, object]]] = []
    if settlement and authority_type != "wh6":
        source_records.append((authority_type, settlement))
        fallbacks = _row_value(settlement, "_fallback_sources", ())
        for source, record in fallbacks or ():
            if source in {"monthly", "daily"} and source != authority_type:
                source_records.append((source, record))
    source_records.append(("wh6", wh6))

    fields: Dict[str, Any] = {}
    field_sources: Dict[str, Optional[str]] = {}
    differences: Dict[str, Dict[str, Any]] = {}
    for field in STATEMENT_OWNED_FIELDS + WH6_ONLY_FIELDS:
        resolved_value = None
        resolved_source: Optional[str] = None
        records = source_records if field in STATEMENT_OWNED_FIELDS else [("wh6", wh6)]
        for source, record in records:
            candidate = _row_value(record, field)
            if _present(candidate):
                resolved_value = candidate
                resolved_source = source
                break
        fields[field] = resolved_value
        field_sources[field] = resolved_source
        wh6_value = _row_value(wh6, field)
        if resolved_source != "wh6" and not _values_equal(field, wh6_value, resolved_value):
            differences[field] = {
                "wh6": wh6_value,
                "resolved": resolved_value,
                "source": resolved_source,
            }
    return ResolvedFill(fields, field_sources, differences, authority_type)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _persist_resolution(cur, fill: Mapping[str, Any], decision: MatchDecision) -> tuple[bool, bool]:
    settlement = decision.settlement
    resolved = resolve_fill_fields(fill, settlement, decision.authority_type)
    account_id = int(_row_value(fill, "account_id"))
    trade_date = _normalize_date(_row_value(fill, "trade_date"))
    closed = bool(trade_date and _closed_range_for_date(cur, account_id, trade_date.isoformat()))
    is_monthly_status = decision.status in {"matched_monthly", "corrected_monthly"}
    is_conflict = decision.status in {"ambiguous", "monthly_unmatched"} and closed
    data_status = "settlement_conflict" if is_conflict else "settlement_covered" if is_monthly_status and closed else "provisional"
    effective_source = decision.authority_type if settlement else "wh6"
    identity_id = decision.identity_id
    batch_id = decision.batch_id
    source_priority = int(decision.source_priority or 0)
    resolved_json = _json_dumps(resolved.fields)
    source_json = _json_dumps(resolved.field_sources)
    differences_json = _json_dumps(resolved.differences)
    current = db._exec(
        cur,
        """
        SELECT authority_type, source_priority, result_status,
               resolved_fields_json, field_sources_json, differences_json
        FROM trading_intraday_fill_reconciliations
        WHERE intraday_fill_id = ? AND is_current = 1
        """,
        (_row_value(fill, "id"),),
    ).fetchone()
    unchanged = bool(
        current
        and str(_row_value(current, "authority_type")) == effective_source
        and int(_row_value(current, "source_priority") or 0) == source_priority
        and str(_row_value(current, "result_status")) == decision.status
        and str(_row_value(current, "resolved_fields_json")) == resolved_json
        and str(_row_value(current, "field_sources_json")) == source_json
        and str(_row_value(current, "differences_json")) == differences_json
    )
    if not unchanged:
        db._exec(
            cur,
            """
            UPDATE trading_intraday_fill_reconciliations
            SET is_current = 0
            WHERE intraday_fill_id = ? AND is_current = 1
            """,
            (_row_value(fill, "id"),),
        )
        db._exec(
            cur,
            """
            INSERT INTO trading_intraday_fill_reconciliations
                (intraday_fill_id, account_id, settlement_identity_id,
                 settlement_batch_id, authority_type, source_priority,
                 result_status, resolved_fields_json, field_sources_json,
                 differences_json, is_current)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                _row_value(fill, "id"), account_id, identity_id, batch_id,
                effective_source, source_priority, decision.status,
                resolved_json, source_json, differences_json,
            ),
        )
    db._exec(
        cur,
        """
        UPDATE trading_intraday_fills
        SET reconciliation_status = ?, settlement_identity_id = ?,
            settlement_batch_id = ?, effective_source = ?,
            reconciled_at = ?, data_status = ?
        WHERE id = ? AND account_id = ?
        """,
        (
            decision.status, identity_id, batch_id, effective_source, _now(),
            data_status, _row_value(fill, "id"), account_id,
        ),
    )
    return (not unchanged, data_status == "settlement_covered")


def _reconcile_rows(cur, rows: Sequence[Mapping[str, Any]], actor: str) -> ReconciliationSummary:
    del actor
    summary = ReconciliationSummary()
    for fill in rows:
        decision = match_intraday_fill(cur, fill)
        changed, covered = _persist_resolution(cur, fill, decision)
        summary.add(decision.status, covered=covered, changed=changed)
    return summary


def reconcile_intraday_fills_for_batch(
    cur, batch_id: int, actor: str
) -> ReconciliationSummary:
    batch = db._exec(
        cur,
        """
        SELECT id, account_id, range_start, range_end, status
        FROM trading_import_batches WHERE id = ?
        """,
        (batch_id,),
    ).fetchone()
    if not batch or str(_row_value(batch, "status")) != "active":
        raise ValueError("只有已生效结算批次可以协调 WH6 成交")
    start = _normalize_date(_row_value(batch, "range_start"))
    end = _normalize_date(_row_value(batch, "range_end"))
    if not start or not end:
        return ReconciliationSummary()
    rows = db._exec(
        cur,
        """
        SELECT * FROM trading_intraday_fills
        WHERE account_id = ? AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date DESC, trade_time DESC, id DESC
        """,
        (int(_row_value(batch, "account_id")), start.isoformat(), end.isoformat()),
    ).fetchall()
    return _reconcile_rows(cur, rows, actor)


def reconcile_intraday_range(
    cur, account_id: int, start: str, end: str, actor: str
) -> ReconciliationSummary:
    start_date = _normalize_date(start) if start else None
    end_date = _normalize_date(end) if end else None
    if (start and not start_date) or (end and not end_date):
        raise ValueError("协调日期范围无法验证")
    sql = "SELECT * FROM trading_intraday_fills WHERE account_id = ?"
    params: List[object] = [account_id]
    if start_date:
        sql += " AND trade_date >= ?"
        params.append(start_date.isoformat())
    if end_date:
        sql += " AND trade_date <= ?"
        params.append(end_date.isoformat())
    sql += " ORDER BY trade_date DESC, trade_time DESC, id DESC"
    rows = db._exec(cur, sql, tuple(params)).fetchall()
    return _reconcile_rows(cur, rows, actor)
