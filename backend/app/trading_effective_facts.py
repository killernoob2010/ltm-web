"""Read-only projections that combine settlement and provisional WH6 facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import db
from . import trading_collector_reconciliation as reconciliation


SOURCE_LABELS = {
    "daily": "日结单",
    "monthly": "月结单",
    "wh6": "WH6采集",
}
FACT_STATUSES = {"", "provisional", "settlement_confirmed"}


@dataclass(frozen=True)
class EffectiveFactFilters:
    contract: str = ""
    direction: str = ""
    asset_type: str = ""
    open_close: str = ""
    classification: str = ""
    fact_status: str = ""
    start_date: str = ""
    end_date: str = ""
    page: int = 1
    page_size: int = 20

    def __post_init__(self) -> None:
        if self.page_size not in {20, 50, 100}:
            raise ValueError("每页条数只允许 20、50、100")
        if self.fact_status not in FACT_STATUSES:
            raise ValueError("事实状态无效")
        object.__setattr__(self, "page", max(1, int(self.page)))


def _number(value: object) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_side(value: object) -> str:
    return {
        "buy": "买",
        "sell": "卖",
        "买入": "买",
        "卖出": "卖",
    }.get(str(value or "").strip().lower(), str(value or "").strip())


def _display_open_close(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"open", "开", "开仓"}:
        return "开仓"
    if text in {"close", "平", "平仓"}:
        return "平仓"
    return str(value or "").strip()


def _date_key(value: object) -> str:
    return reconciliation.iso_trade_date(value)


def _display_date(value: object) -> str:
    return reconciliation.compact_trade_date(value) or str(value or "").strip()


def _statement_source_type(row: Mapping[str, Any]) -> str:
    statement_type = str(row.get("statement_type") or "").strip().lower()
    if statement_type in {"daily", "monthly"}:
        return statement_type
    start = reconciliation.normalize_trade_date(row.get("range_start"))
    end = reconciliation.normalize_trade_date(row.get("range_end"))
    if start and end and start == end:
        return "daily"
    return "monthly"


def _in_date_range(item: Mapping[str, Any], filters: EffectiveFactFilters) -> bool:
    item_date = _date_key(item.get("trade_date"))
    if filters.start_date:
        start = _date_key(filters.start_date)
        if start and (not item_date or item_date < start):
            return False
    if filters.end_date:
        end = _date_key(filters.end_date)
        if end and (not item_date or item_date > end):
            return False
    return True


def _assignment_maps(cur) -> tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    direct: Dict[int, Dict[str, Any]] = {}
    rows = db._exec(
        cur,
        """
        SELECT ba.trade_identity_id, s.name AS business_subject,
               ba.business_type, st.name AS strategy,
               'classified' AS assignment_status
        FROM trading_business_assignments ba
        LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
        LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
        """,
    ).fetchall()
    for row in rows:
        direct[int(row["trade_identity_id"])] = dict(row)

    close: Dict[int, Dict[str, Any]] = {}
    rows = db._exec(
        cur,
        """
        SELECT l.close_trade_identity_id,
               ba.business_type, s.name AS business_subject, st.name AS strategy,
               CASE WHEN ba.id IS NULL THEN 'unclassified' ELSE 'classified' END
                   AS assignment_status
        FROM trading_close_trade_links l
        JOIN trading_business_close_allocations a
          ON a.close_identity_id = l.close_identity_id
        LEFT JOIN trading_business_assignments ba
          ON ba.trade_identity_id = a.open_trade_identity_id
        LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
        LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
        """,
    ).fetchall()
    grouped: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        grouped.setdefault(int(item["close_trade_identity_id"]), []).append(item)
    for identity_id, items in grouped.items():
        classified = [item for item in items if item["assignment_status"] == "classified"]
        if items and len(classified) == len(items):
            values = {
                key: {item.get(key) for item in classified}
                for key in ("business_subject", "business_type", "strategy")
            }
            close[identity_id] = {
                key: next(iter(value)) if len(value) == 1 else None
                for key, value in values.items()
            }
            close[identity_id]["assignment_status"] = "classified"
        else:
            close[identity_id] = {
                "business_subject": None,
                "business_type": None,
                "strategy": None,
                "assignment_status": "unclassified",
            }
    return direct, close


def _close_pnl_map(cur) -> Dict[int, Optional[float]]:
    rows = db._exec(
        cur,
        """
        SELECT l.close_trade_identity_id,
               SUM(cf.fact_close_pnl * l.matched_quantity / NULLIF(cf.quantity, 0))
                   AS fact_close_pnl
        FROM trading_close_trade_links l
        JOIN trading_close_facts cf ON cf.identity_id = l.close_identity_id
        JOIN trading_import_batches cb
          ON cb.id = cf.batch_id AND cb.status = 'active'
        WHERE cf.is_current = 1
        GROUP BY l.close_trade_identity_id
        """,
    ).fetchall()
    return {int(row["close_trade_identity_id"]): _number(row["fact_close_pnl"]) for row in rows}


def _settlement_item(
    row: Mapping[str, Any],
    *,
    assignment: Optional[Mapping[str, Any]],
    fact_close_pnl: Optional[float],
) -> Dict[str, Any]:
    identity_id = int(row["identity_id"])
    source_type = _statement_source_type(row)
    open_close = _display_open_close(row["open_close"])
    assignment_status = str((assignment or {}).get("assignment_status") or "unclassified")
    return {
        "record_key": f"settlement:{int(row['id'])}",
        "identity_id": identity_id,
        "source_record_id": int(row["id"]),
        "trade_date": _display_date(row["trade_date"]),
        "trade_time": row["trade_time"],
        "exchange": row["exchange"],
        "contract": row["contract"],
        "asset_type": row["asset_type"],
        "side": _display_side(row["side"]),
        "open_close": open_close,
        "quantity": _number(row["quantity"]) or 0.0,
        "price": _number(row["price"]),
        "turnover": _number(row["turnover"]),
        "fee": _number(row["fee"]),
        "hedge_flag": row["hedge_flag"],
        "premium_cashflow": _number(row["premium_cashflow"]),
        "fact_close_pnl": fact_close_pnl,
        "fact_status": "settlement_confirmed",
        "source_type": source_type,
        "source_label": SOURCE_LABELS[source_type],
        "reconciliation_status": None,
        "can_classify": open_close == "开仓",
        "assignment_status": assignment_status,
        "business_subject": (assignment or {}).get("business_subject"),
        "business_type": (assignment or {}).get("business_type"),
        "strategy": (assignment or {}).get("strategy"),
        "source_priority": int(row["source_priority"] or 0),
        "_sort_date": _date_key(row["trade_date"]),
        "_sort_time": str(row["trade_time"] or ""),
        "_sort_id": int(row["id"]),
    }


def _provisional_item(row: Mapping[str, Any]) -> Dict[str, Any]:
    open_close = _display_open_close(row["open_close"])
    return {
        "record_key": f"wh6:{int(row['id'])}",
        "identity_id": None,
        "source_record_id": int(row["id"]),
        "trade_date": _display_date(row["trade_date"]),
        "trade_time": row["trade_time"],
        "exchange": row["exchange"],
        "contract": row["contract"],
        "asset_type": row["asset_type"],
        "side": _display_side(row["side"]),
        "open_close": open_close,
        "quantity": _number(row["quantity"]) or 0.0,
        "price": _number(row["price"]),
        "turnover": None,
        "fee": None,
        "hedge_flag": None,
        "premium_cashflow": None,
        "fact_close_pnl": None,
        "fact_status": "provisional",
        "source_type": "wh6",
        "source_label": SOURCE_LABELS["wh6"],
        "reconciliation_status": row["reconciliation_status"],
        "can_classify": False,
        "assignment_status": "not_applicable",
        "business_subject": None,
        "business_type": None,
        "strategy": None,
        "source_priority": 0,
        "_sort_date": _date_key(row["trade_date"]),
        "_sort_time": str(row["trade_time"] or ""),
        "_sort_id": int(row["id"]),
    }


def _settlement_dedupe_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    transaction_no = reconciliation.normalize_transaction_no(
        row["normalized_transaction_no"] or row["transaction_no"]
    )
    if transaction_no:
        return (
            "transaction",
            int(row["identity_id"]),
            _date_key(row["trade_date"]),
            reconciliation.normalize_exchange(str(row["exchange"] or "")),
            transaction_no,
        )
    return ("identity", int(row["identity_id"]))


def _passes_filters(item: Mapping[str, Any], filters: EffectiveFactFilters) -> bool:
    if filters.fact_status and item["fact_status"] != filters.fact_status:
        return False
    if filters.contract and filters.contract.lower() not in str(item["contract"] or "").lower():
        return False
    if filters.direction and item["side"] != _display_side(filters.direction):
        return False
    if filters.asset_type and item["asset_type"] != filters.asset_type:
        return False
    if filters.open_close and item["open_close"] != _display_open_close(filters.open_close):
        return False
    if not _in_date_range(item, filters):
        return False
    if filters.classification in {"classified", "unclassified"}:
        if item["fact_status"] != "settlement_confirmed":
            return False
        if item["assignment_status"] != filters.classification:
            return False
    return True


def _page_result(items: List[Dict[str, Any]], filters: EffectiveFactFilters) -> Dict[str, Any]:
    total = len(items)
    total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)
    page = min(filters.page, total_pages)
    start = (page - 1) * filters.page_size
    visible = items[start:start + filters.page_size]
    provisional_count = sum(item["fact_status"] == "provisional" for item in items)
    settlement_count = sum(item["fact_status"] == "settlement_confirmed" for item in items)
    return {
        "items": visible,
        "summary": {
            "record_count": total,
            "quantity": sum(float(item["quantity"] or 0) for item in items),
            "fee": sum(float(item["fee"] or 0) for item in items if item["fee"] is not None),
            "fact_close_pnl": sum(
                float(item["fact_close_pnl"] or 0)
                for item in items
                if item["fact_close_pnl"] is not None
            ),
            "provisional_count": provisional_count,
            "settlement_confirmed_count": settlement_count,
            "contains_provisional": provisional_count > 0,
        },
        "page": page,
        "page_size": filters.page_size,
        "total_items": total,
        "total_pages": total_pages,
        "data_status": "imported",
    }


def _effective_trade_items(cur, filters: EffectiveFactFilters) -> List[Dict[str, Any]]:
    direct_assignments, close_assignments = _assignment_maps(cur)
    close_pnl = _close_pnl_map(cur)
    settlement_rows = db._exec(
        cur,
        """
        SELECT tf.*, b.statement_type, b.source_priority, b.status AS batch_status
             , b.range_start, b.range_end
        FROM trading_trade_facts tf
        JOIN trading_import_batches b ON b.id = tf.batch_id
        WHERE b.status = 'active' AND tf.is_current = 1
        ORDER BY tf.id DESC
        LIMIT ? OFFSET ?
        """,
        (-1, 0),
    ).fetchall()
    settlement_by_key: Dict[tuple[Any, ...], Dict[str, Any]] = {}
    for row in settlement_rows:
        row_dict = dict(row)
        identity_id = int(row_dict["identity_id"])
        assignment = direct_assignments.get(identity_id)
        if _display_open_close(row_dict["open_close"]) != "开仓":
            assignment = close_assignments.get(identity_id, assignment)
        item = _settlement_item(
            row_dict,
            assignment=assignment,
            fact_close_pnl=close_pnl.get(identity_id),
        )
        key = _settlement_dedupe_key(row_dict)
        previous = settlement_by_key.get(key)
        if previous is None or (
            item["source_priority"], item["_sort_id"]
        ) > (previous["source_priority"], previous["_sort_id"]):
            settlement_by_key[key] = item

    provisional_rows = db._exec(
        cur,
        """
        SELECT * FROM trading_intraday_fills
        WHERE data_status = 'provisional'
        ORDER BY trade_date DESC, trade_time DESC, id DESC
        """,
    ).fetchall()
    items = list(settlement_by_key.values())
    for raw_row in provisional_rows:
        row = dict(raw_row)
        if reconciliation.statement_coverage_for_date(
            cur, int(row["account_id"]), row["trade_date"]
        ):
            continue
        items.append(_provisional_item(row))

    filtered = [item for item in items if _passes_filters(item, filters)]
    filtered.sort(
        key=lambda item: (
            item["_sort_date"],
            item["_sort_time"],
            item["source_priority"],
            item["_sort_id"],
        ),
        reverse=True,
    )
    for item in filtered:
        for key in ("_sort_date", "_sort_time", "_sort_id", "source_priority"):
            item.pop(key, None)
    return filtered


def query_effective_trades(cur, filters: EffectiveFactFilters) -> Dict[str, Any]:
    """Return one filtered, deduplicated projection of effective trade facts."""
    return _page_result(_effective_trade_items(cur, filters), filters)


def query_effective_trade_selection_ids(
    cur, filters: EffectiveFactFilters
) -> Dict[str, Any]:
    """Return only settlement-confirmed opening identities eligible for assignment."""
    items = _effective_trade_items(cur, filters)
    identity_ids = [
        int(item["identity_id"])
        for item in items
        if item["can_classify"] and item["open_close"] == "开仓" and item["identity_id"] is not None
    ]
    return {"identity_ids": identity_ids, "total_items": len(identity_ids)}


def query_effective_positions(
    cur,
    filters: EffectiveFactFilters,
    *,
    now: Any = None,
) -> Dict[str, Any]:
    """Position projection is completed by the current-position implementation."""
    del cur, filters, now
    raise NotImplementedError("当前持仓投影尚未实现")
