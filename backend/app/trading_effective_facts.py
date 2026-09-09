"""Read-only projections that combine settlement and provisional WH6 facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    account_ids: Optional[tuple[int, ...]] = None

    def __post_init__(self) -> None:
        if self.account_ids is not None:
            if not self.account_ids:
                raise PermissionError("无可查询账户")
            if any(type(value) is not int or value <= 0 for value in self.account_ids):
                raise ValueError("账户范围无效")
        if self.page_size not in {20, 50, 100}:
            raise ValueError("每页条数只允许 20、50、100")
        if self.fact_status not in FACT_STATUSES:
            raise ValueError("事实状态无效")
        object.__setattr__(self, "page", max(1, int(self.page)))


def _account_scope(column: str, account_ids: Optional[tuple[int, ...]]) -> tuple[str, tuple]:
    if account_ids is None:
        return "", ()
    if not account_ids:
        raise PermissionError("无可查询账户")
    return f" AND {column} IN ({','.join('?' for _ in account_ids)})", account_ids


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


def _max_source_observation(left: object, right: object) -> Optional[str]:
    candidates = []
    for value in (left, right):
        parsed = _parse_datetime(value)
        if parsed is not None:
            candidates.append((parsed, _format_datetime(value)))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _statement_source_type(row: Mapping[str, Any]) -> str:
    statement_type = str(row.get("statement_type") or "").strip().lower()
    if statement_type in {"daily", "monthly"}:
        return statement_type
    start = reconciliation.normalize_trade_date(row.get("range_start"))
    end = reconciliation.normalize_trade_date(row.get("range_end"))
    if start and end and start == end:
        return "daily"
    return "monthly"


def _position_direction(value: object) -> str:
    text = str(value or "").strip().lower()
    return {
        "buy": "买",
        "sell": "卖",
        "long": "买",
        "short": "卖",
        "多": "买",
        "空": "卖",
        "买入": "买",
        "卖出": "卖",
    }.get(text, str(value or "").strip())


def _position_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        reconciliation.normalize_exchange(str(row.get("exchange") or "")),
        str(row.get("contract") or "").strip().lower(),
        str(row.get("asset_type") or "").strip().lower(),
        _position_direction(row.get("direction")),
    )


def _hedge_flag(value: object) -> str:
    return str(value or "").strip()


def _baseline_lot_order(row: Mapping[str, Any], index: int) -> tuple[str, int, str, int, int]:
    opened = _date_key(row.get("open_date") or row.get("snapshot_date"))
    try:
        row_id = int(row.get("id") or row.get("source_record_id") or index)
    except (TypeError, ValueError):
        row_id = index
    return opened, -1, "", row_id, index


def _copy_position_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(row)
    result["exchange"] = str(result.get("exchange") or "").upper()
    result["contract"] = str(result.get("contract") or "").strip().lower()
    result["asset_type"] = str(result.get("asset_type") or "").strip().lower()
    result["direction"] = _position_direction(result.get("direction"))
    result.setdefault(
        "position_key",
        f"{result['exchange']}:{result['contract']}:{result['asset_type']}:{result['direction']}",
    )
    result["quantity"] = _number(result.get("quantity")) or 0.0
    result["average_price"] = _number(result.get("average_price"))
    result.setdefault("source_record_count", 1)
    result.setdefault("fact_status", "settlement_confirmed")
    result.setdefault("formation_method", "settlement_snapshot")
    result.setdefault("source_type", "monthly")
    result.setdefault("source_label", SOURCE_LABELS.get(result["source_type"], "结算单"))
    result.setdefault("can_classify", True)
    return result


def _baseline_position_items(baseline_rows: Iterable[Mapping[str, Any]]) -> Dict[tuple[str, str, str, str], Dict[str, Any]]:
    grouped: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    for raw_row in baseline_rows:
        row = _copy_position_row(raw_row)
        key = _position_key(row)
        previous = grouped.get(key)
        if previous is None:
            grouped[key] = row
            continue
        previous_quantity = float(previous.get("quantity") or 0)
        row_quantity = float(row.get("quantity") or 0)
        total_quantity = previous_quantity + row_quantity
        if total_quantity and previous.get("average_price") is not None and row.get("average_price") is not None:
            previous["average_price"] = (
                previous_quantity * float(previous["average_price"])
                + row_quantity * float(row["average_price"])
            ) / total_quantity
        previous["quantity"] = total_quantity
        if previous.get("margin") is not None or row.get("margin") is not None:
            previous["margin"] = float(previous.get("margin") or 0) + float(row.get("margin") or 0)
        previous["source_record_count"] = int(previous.get("source_record_count") or 0) + int(row.get("source_record_count") or 0)
        previous["source_observed_at"] = _max_source_observation(
            previous.get("source_observed_at"), row.get("source_observed_at")
        )
    return grouped


def _source_kind(item: Mapping[str, Any]) -> str:
    if str(item.get("formation_method") or "") == "inferred_from_settlement_and_fills":
        return "derived"
    source_type = str(item.get("source_type") or "").strip().lower()
    if source_type in {"daily", "monthly"}:
        return "settlement"
    if source_type == "wh6":
        return "wh6"
    return "unknown"


def _position_provenance(
    items: Iterable[Mapping[str, Any]], freshness: Mapping[str, Any]
) -> Dict[str, Any]:
    grouped: Dict[tuple[str, str, Optional[str]], Dict[str, Any]] = {}
    for item in items:
        source_kind = _source_kind(item)
        source_label = str(item.get("source_label") or "未知来源")
        observed_at = _format_datetime(item.get("source_observed_at"))
        key = (source_kind, source_label, observed_at)
        entry = grouped.setdefault(
            key,
            {
                "source_kind": source_kind,
                "source_label": source_label,
                "coverage_dates": set(),
                "observed_at": observed_at,
                "row_count": 0,
                "fact_status": "unknown",
                "freshness_status": "unknown",
                "environment": "unknown",
            },
        )
        for field in ("snapshot_date", "trade_date", "open_date"):
            coverage_date = _date_key(item.get(field))
            if coverage_date:
                entry["coverage_dates"].add(coverage_date)
        if source_kind == "derived" and observed_at:
            observed_date = _parse_datetime(observed_at)
            if observed_date is not None:
                entry["coverage_dates"].add(observed_date.date().isoformat())
        entry["row_count"] += 1
        if source_kind == "settlement":
            entry["fact_status"] = "settlement_confirmed"
        elif source_kind == "derived":
            entry["fact_status"] = "derived"
        elif source_kind == "wh6":
            entry["fact_status"] = "provisional"
            entry["freshness_status"] = str(freshness.get("freshness_status") or "unknown")
    observations = []
    for entry in grouped.values():
        dates = sorted(entry.pop("coverage_dates"))
        entry["coverage_date_start"] = dates[0] if dates else None
        entry["coverage_date_end"] = dates[-1] if dates else None
        observations.append(entry)
    observations.sort(key=lambda item: (
        item["source_kind"], item["source_label"], item.get("observed_at") or "",
        item.get("coverage_date_start") or "",
    ))
    return {
        "data_as_of": None,
        "precision": None,
        "source_observations": observations,
        "overall_freshness": str(freshness.get("freshness_status") or "unknown"),
        "reason": "当前结果可能混合业务观察日、WH6采集时间和推导成交，未形成统一来源水位",
    }


def _group_position_items(items: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    grouped = _baseline_position_items(items)
    return sorted(
        grouped.values(),
        key=lambda row: (
            str(row.get("contract") or ""),
            str(row.get("direction") or ""),
            str(row.get("asset_type") or ""),
        ),
    )


def _fill_order_key(row: Mapping[str, Any]) -> tuple[str, int, str, int]:
    """Order fills by exchange session within the stored business date.

    WH6 stores night-session fills under the business date used by the
    statement, while the clock time is still 21:00 or later.  Sorting only by
    the clock therefore puts the day session before the preceding night
    session and can make a valid close look like an over-close.
    """
    trade_date = _date_key(row.get("trade_date"))
    trade_time = str(row.get("trade_time") or "").strip()
    if trade_time >= "21:00:00":
        session_order = 0
    elif trade_time and trade_time < "05:00:00":
        session_order = 1
    else:
        session_order = 2
    try:
        row_id = int(row.get("id") or 0)
    except (TypeError, ValueError):
        row_id = 0
    return trade_date, session_order, trade_time, row_id


def _position_projection_error(
    baseline_rows: Iterable[Mapping[str, Any]], warning: str
) -> Dict[str, Any]:
    reference_items = list(_baseline_position_items(baseline_rows).values())
    return {
        "status": "projection_error",
        "items": [],
        "reference_items": reference_items,
        "warnings": [warning],
    }


def _position_lots(baseline_rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    lots: List[Dict[str, Any]] = []
    for index, raw_row in enumerate(baseline_rows):
        row = _copy_position_row(raw_row)
        if float(row.get("quantity") or 0) <= 0:
            continue
        row["_lot_order"] = _baseline_lot_order(row, index)
        lots.append(row)
    return lots


def _close_lots(
    lots: List[Dict[str, Any]],
    position_key: tuple[str, str, str, str],
    quantity: float,
    hedge_flag: str,
) -> bool:
    matching = [lot for lot in lots if _position_key(lot) == position_key]
    if hedge_flag:
        same_hedge = [
            lot for lot in matching if _hedge_flag(lot.get("hedge_flag")) == hedge_flag
        ]
        if same_hedge:
            matching = same_hedge
        elif any(_hedge_flag(lot.get("hedge_flag")) for lot in matching):
            return False
    matching.sort(key=lambda lot: lot["_lot_order"])
    if sum(float(lot.get("quantity") or 0) for lot in matching) + 1e-9 < quantity:
        return False
    remaining = float(quantity)
    for lot in matching:
        if remaining <= 1e-9:
            break
        lot_quantity = float(lot.get("quantity") or 0)
        take = min(lot_quantity, remaining)
        lot["quantity"] = lot_quantity - take
        remaining -= take
    return remaining <= 1e-9


def infer_positions_from_fills(
    baseline_rows: Iterable[Mapping[str, Any]],
    fills: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Apply valid WH6 fills to a confirmed position baseline without clipping errors."""
    baseline = list(baseline_rows)
    lots = _position_lots(baseline)
    ordered_fills = sorted(
        list(fills),
        key=_fill_order_key,
    )
    changed_keys: set[tuple[str, str, str, str]] = set()
    latest_fill_observed_at: Optional[str] = None
    for fill in ordered_fills:
        fill_observed_at = _format_datetime(fill.get("trade_timestamp") or fill.get("last_observed_at"))
        latest_fill_observed_at = _max_source_observation(latest_fill_observed_at, fill_observed_at)
        contract = str(fill.get("contract") or "").strip().lower()
        exchange = str(fill.get("exchange") or "").strip()
        asset_type = str(fill.get("asset_type") or "").strip().lower()
        side = _display_side(fill.get("side"))
        open_close = _display_open_close(fill.get("open_close"))
        quantity = _number(fill.get("quantity"))
        price = _number(fill.get("price"))
        if not contract or not exchange or not asset_type or side not in {"买", "卖"}:
            return _position_projection_error(baseline, "成交缺少合约、交易所、资产类型或买卖方向")
        if open_close not in {"开仓", "平仓"} or quantity is None or quantity <= 0 or price is None:
            return _position_projection_error(baseline, "成交缺少有效开平、数量或价格")
        if open_close == "开仓":
            direction = side
            delta = quantity
        elif side == "卖":
            direction = "买"
            delta = -quantity
        else:
            direction = "卖"
            delta = -quantity
        key = (
            reconciliation.normalize_exchange(exchange),
            contract,
            asset_type,
            direction,
        )
        if delta < 0:
            if not _close_lots(lots, key, quantity, _hedge_flag(fill.get("hedge_flag"))):
                return _position_projection_error(baseline, f"成交平仓超过已有持仓：{contract}/{direction}")
        else:
            lots.append(
                {
                    "exchange": exchange.upper(),
                    "contract": contract,
                    "asset_type": asset_type,
                    "direction": direction,
                    "position_key": f"{exchange.upper()}:{contract}:{asset_type}:{direction}",
                    "quantity": quantity,
                    "average_price": price,
                    "open_date": fill.get("trade_date"),
                    "hedge_flag": fill.get("hedge_flag"),
                    "margin": None,
                    "valuation_price": None,
                    "floating_pnl": None,
                    "source_record_count": 0,
                    "fact_status": "provisional",
                    "formation_method": "inferred_from_settlement_and_fills",
                    "source_type": "wh6",
                    "source_label": SOURCE_LABELS["wh6"],
                    "source_observed_at": fill_observed_at,
                    "can_classify": False,
                    "assignment_status": "not_applicable",
                    "_lot_order": (
                        *_fill_order_key(fill),
                        len(lots),
                    ),
                }
            )
        changed_keys.add(key)

    lots = [lot for lot in lots if float(lot.get("quantity") or 0) > 1e-9]
    for lot in lots:
        if _position_key(lot) in changed_keys:
            lot.update(
                {
                    "fact_status": "provisional",
                    "formation_method": "inferred_from_settlement_and_fills",
                    "source_type": "wh6",
                    "source_label": SOURCE_LABELS["wh6"],
                    "margin": None,
                    "valuation_price": None,
                    "floating_pnl": None,
                    "can_classify": False,
                    "assignment_status": "not_applicable",
                    "source_observed_at": latest_fill_observed_at,
                }
            )
            if latest_fill_observed_at:
                lot["source_observed_at"] = latest_fill_observed_at
    items = _group_position_items(lots)
    for item in items:
        item.pop("_lot_order", None)
    return {
        "status": "ok",
        "items": items,
        "warnings": [],
    }


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


def _identity_scope(column: str, identity_ids: Optional[Iterable[int]]) -> tuple[str, tuple[int, ...]]:
    if identity_ids is None:
        return "", ()
    values = tuple(sorted({int(identity_id) for identity_id in identity_ids}))
    if not values:
        return " AND 1 = 0", ()
    return f" AND {column} IN ({','.join('?' for _ in values)})", values


def _assignment_maps(
    cur, identity_ids: Optional[Iterable[int]] = None
) -> tuple[Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]]:
    direct_scope, direct_params = _identity_scope("ba.trade_identity_id", identity_ids)
    direct: Dict[int, Dict[str, Any]] = {}
    rows = db._exec(
        cur,
        f"""
        SELECT ba.trade_identity_id, s.name AS business_subject,
               ba.business_type, st.name AS strategy,
               'classified' AS assignment_status
        FROM trading_business_assignments ba
        LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
        LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
        WHERE 1 = 1{direct_scope}
        """,
        direct_params,
    ).fetchall()
    for row in rows:
        direct[int(row["trade_identity_id"])] = dict(row)

    close_scope, close_params = _identity_scope("l.close_trade_identity_id", identity_ids)
    close: Dict[int, Dict[str, Any]] = {}
    rows = db._exec(
        cur,
        f"""
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
        WHERE 1 = 1{close_scope}
        """,
        close_params,
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


def _close_pnl_map(
    cur, identity_ids: Optional[Iterable[int]] = None
) -> Dict[int, Optional[float]]:
    scope, params = _identity_scope("l.close_trade_identity_id", identity_ids)
    rows = db._exec(
        cur,
        f"""
        SELECT l.close_trade_identity_id,
               SUM(cf.fact_close_pnl * l.matched_quantity / NULLIF(cf.quantity, 0))
                   AS fact_close_pnl
        FROM trading_close_trade_links l
        JOIN trading_close_facts cf ON cf.identity_id = l.close_identity_id
        JOIN trading_import_batches cb
          ON cb.id = cf.batch_id AND cb.status = 'active'
        WHERE cf.is_current = 1{scope}
        GROUP BY l.close_trade_identity_id
        """,
        params,
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


def _ranked_settlement_query(filters: EffectiveFactFilters) -> tuple[str, str, tuple[Any, ...]]:
    scope, scope_params = _account_scope("b.account_id", filters.account_ids)
    normalized_transaction = (
        "LOWER(TRIM(COALESCE(NULLIF(tf.normalized_transaction_no, ''), "
        "tf.transaction_no, '')))"
    )
    normalized_date = "REPLACE(COALESCE(tf.trade_date, ''), '-', '')"
    normalized_exchange = "LOWER(REPLACE(TRIM(COALESCE(tf.exchange, '')), ' ', ''))"
    side = (
        "CASE LOWER(TRIM(COALESCE(side, ''))) "
        "WHEN 'buy' THEN '买' WHEN '买入' THEN '买' "
        "WHEN 'sell' THEN '卖' WHEN '卖出' THEN '卖' "
        "ELSE TRIM(COALESCE(side, '')) END"
    )
    open_close = (
        "CASE LOWER(TRIM(COALESCE(open_close, ''))) "
        "WHEN 'open' THEN '开仓' WHEN '开' THEN '开仓' "
        "WHEN 'close' THEN '平仓' WHEN '平' THEN '平仓' "
        "ELSE TRIM(COALESCE(open_close, '')) END"
    )
    ranked = f"""
        WITH ranked_settlement AS (
            SELECT tf.*, b.statement_type, b.source_priority,
                   b.status AS batch_status, b.range_start, b.range_end,
                   ROW_NUMBER() OVER (
                       PARTITION BY tf.identity_id,
                           CASE WHEN {normalized_transaction} <> ''
                                THEN {normalized_date} ELSE '' END,
                           CASE WHEN {normalized_transaction} <> ''
                                THEN {normalized_exchange} ELSE '' END,
                           {normalized_transaction}
                       ORDER BY b.source_priority DESC, tf.id DESC
                   ) AS effective_rank
            FROM trading_trade_facts tf
            JOIN trading_import_batches b ON b.id = tf.batch_id
            WHERE b.status = 'active' AND tf.is_current = 1{scope}
        )
    """
    conditions = ["effective_rank = 1"]
    params: List[Any] = list(scope_params)
    if filters.contract:
        conditions.append("LOWER(COALESCE(contract, '')) LIKE ?")
        params.append(f"%{filters.contract.lower()}%")
    if filters.direction:
        conditions.append(f"{side} = ?")
        params.append(_display_side(filters.direction))
    if filters.asset_type:
        conditions.append("LOWER(COALESCE(asset_type, '')) = ?")
        params.append(filters.asset_type.lower())
    if filters.open_close:
        conditions.append(f"{open_close} = ?")
        params.append(_display_open_close(filters.open_close))
    if filters.start_date:
        conditions.append("REPLACE(COALESCE(trade_date, ''), '-', '') >= ?")
        params.append(filters.start_date.replace("-", ""))
    if filters.end_date:
        conditions.append("REPLACE(COALESCE(trade_date, ''), '-', '') <= ?")
        params.append(filters.end_date.replace("-", ""))
    return ranked, " AND ".join(conditions), tuple(params)


def _provisional_trade_items(cur, filters: EffectiveFactFilters) -> List[Dict[str, Any]]:
    if filters.fact_status == "settlement_confirmed":
        return []
    scope, params = _account_scope("account_id", filters.account_ids)
    rows = db._exec(
        cur,
        f"""
        SELECT * FROM trading_intraday_fills
        WHERE data_status = 'provisional'{scope}
        ORDER BY trade_date DESC, trade_time DESC, id DESC
        """,
        params,
    ).fetchall()
    items: List[Dict[str, Any]] = []
    coverage_caches: Dict[int, reconciliation.ReconciliationLookupCache] = {}
    for raw_row in rows:
        row = dict(raw_row)
        account_id = int(row["account_id"])
        lookup_cache = coverage_caches.setdefault(
            account_id, reconciliation.ReconciliationLookupCache(account_id)
        )
        if reconciliation.statement_coverage_for_date(
            cur, account_id, row["trade_date"], lookup_cache=lookup_cache
        ):
            continue
        item = _provisional_item(row)
        if _passes_filters(item, filters):
            items.append(item)
    return items


def _query_paged_settlement_rows(
    cur, filters: EffectiveFactFilters
) -> tuple[List[Mapping[str, Any]], Dict[str, float]]:
    if filters.fact_status == "provisional":
        return [], {"record_count": 0.0, "quantity": 0.0, "fee": 0.0, "fact_close_pnl": 0.0}
    ranked, where_clause, params = _ranked_settlement_query(filters)
    summary_row = db._exec(
        cur,
        ranked
        + f"""
        , close_pnl AS (
            SELECT l.close_trade_identity_id,
                   SUM(cf.fact_close_pnl * l.matched_quantity / NULLIF(cf.quantity, 0))
                       AS fact_close_pnl
            FROM trading_close_trade_links l
            JOIN trading_close_facts cf ON cf.identity_id = l.close_identity_id
            JOIN trading_import_batches cb
              ON cb.id = cf.batch_id AND cb.status = 'active'
            WHERE cf.is_current = 1
            GROUP BY l.close_trade_identity_id
        )
        SELECT COUNT(*) AS record_count,
               COALESCE(SUM(quantity), 0) AS quantity,
               COALESCE(SUM(fee), 0) AS fee,
               COALESCE(SUM(close_pnl.fact_close_pnl), 0) AS fact_close_pnl
        FROM ranked_settlement
        LEFT JOIN close_pnl
          ON close_pnl.close_trade_identity_id = ranked_settlement.identity_id
        WHERE {where_clause}
        """,
        params,
    ).fetchone()
    summary = {
        key: float(summary_row[key] or 0)
        for key in ("record_count", "quantity", "fee", "fact_close_pnl")
    }
    limit = filters.page * filters.page_size
    rows = db._exec(
        cur,
        ranked
        + f"""
        SELECT * FROM ranked_settlement
        WHERE {where_clause}
        ORDER BY REPLACE(COALESCE(trade_date, ''), '-', '') DESC,
                 COALESCE(trade_time, '') DESC,
                 COALESCE(source_priority, 0) DESC,
                 id DESC
        LIMIT ?
        """,
        params + (limit,),
    ).fetchall()
    return rows, summary


def _query_effective_trades_paged(cur, filters: EffectiveFactFilters) -> Dict[str, Any]:
    settlement_rows, settlement_summary = _query_paged_settlement_rows(cur, filters)
    identity_ids = [int(row["identity_id"]) for row in settlement_rows]
    direct_assignments, close_assignments = _assignment_maps(cur, identity_ids)
    close_pnl = _close_pnl_map(cur, identity_ids)
    items: List[Dict[str, Any]] = []
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
        if _passes_filters(item, filters):
            items.append(item)

    provisional_items = _provisional_trade_items(cur, filters)
    items.extend(provisional_items)
    items.sort(
        key=lambda item: (
            item["_sort_date"],
            item["_sort_time"],
            item["source_priority"],
            item["_sort_id"],
        ),
        reverse=True,
    )

    provisional_count = len(provisional_items)
    total = int(settlement_summary["record_count"]) + provisional_count
    total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)
    page = min(filters.page, total_pages)
    start = (page - 1) * filters.page_size
    visible = items[start:start + filters.page_size]
    for item in visible:
        for key in ("_sort_date", "_sort_time", "_sort_id", "source_priority"):
            item.pop(key, None)
    return {
        "items": visible,
        "summary": {
            "record_count": total,
            "quantity": settlement_summary["quantity"]
            + sum(float(item["quantity"] or 0) for item in provisional_items),
            "fee": settlement_summary["fee"],
            "fact_close_pnl": settlement_summary["fact_close_pnl"],
            "provisional_count": provisional_count,
            "settlement_confirmed_count": int(settlement_summary["record_count"]),
            "contains_provisional": provisional_count > 0,
        },
        "page": page,
        "page_size": filters.page_size,
        "total_items": total,
        "total_pages": total_pages,
        "data_status": "imported",
    }


def _effective_trade_items(cur, filters: EffectiveFactFilters) -> List[Dict[str, Any]]:
    scope, params = _account_scope("b.account_id", filters.account_ids)
    settlement_rows = db._exec(
        cur,
        f"""
        SELECT tf.*, b.statement_type, b.source_priority, b.status AS batch_status
             , b.range_start, b.range_end
        FROM trading_trade_facts tf
        JOIN trading_import_batches b ON b.id = tf.batch_id
        WHERE b.status = 'active' AND tf.is_current = 1{scope}
        ORDER BY tf.id DESC
        """,
        params,
    ).fetchall()
    identity_ids = [int(row["identity_id"]) for row in settlement_rows]
    direct_assignments, close_assignments = _assignment_maps(cur, identity_ids)
    close_pnl = _close_pnl_map(cur, identity_ids)
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

    scope, params = _account_scope("account_id", filters.account_ids)
    provisional_rows = db._exec(
        cur,
        f"""
        SELECT * FROM trading_intraday_fills
        WHERE data_status = 'provisional'{scope}
        ORDER BY trade_date DESC, trade_time DESC, id DESC
        """,
        params,
    ).fetchall()
    items = list(settlement_by_key.values())
    coverage_caches: Dict[int, reconciliation.ReconciliationLookupCache] = {}
    for raw_row in provisional_rows:
        row = dict(raw_row)
        account_id = int(row["account_id"])
        lookup_cache = coverage_caches.setdefault(
            account_id, reconciliation.ReconciliationLookupCache(account_id)
        )
        if reconciliation.statement_coverage_for_date(
            cur, account_id, row["trade_date"], lookup_cache=lookup_cache
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
    if not filters.classification:
        return _query_effective_trades_paged(cur, filters)
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


def _parse_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_datetime(value: object) -> Optional[str]:
    parsed = _parse_datetime(value)
    return parsed.replace(microsecond=0).isoformat() if parsed else None


def _position_assignment_map(cur, account_ids=None) -> Dict[tuple[str, str, str], Dict[str, Any]]:
    scope, params = _account_scope("b.account_id", account_ids)
    rows = db._exec(
        cur,
        f"""
        SELECT tf.contract, tf.side, tf.asset_type,
               ba.business_type, s.name AS business_subject, st.name AS strategy,
               CASE WHEN ba.id IS NULL THEN 'unclassified' ELSE 'classified' END
                   AS assignment_status
        FROM trading_trade_facts tf
        JOIN trading_import_batches b ON b.id = tf.batch_id AND b.status = 'active'
        LEFT JOIN trading_business_assignments ba ON ba.trade_identity_id = tf.identity_id
        LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
        LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
        WHERE tf.is_current = 1 AND tf.open_close = '开仓'{scope}
        """,
        params,
    ).fetchall()
    grouped: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        key = (
            str(item["contract"] or "").strip().lower(),
            _position_direction(item["side"]),
            str(item["asset_type"] or "").strip().lower(),
        )
        grouped.setdefault(key, []).append(item)
    assignments: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for key, items in grouped.items():
        classified = [item for item in items if item["assignment_status"] == "classified"]
        if items and len(classified) == len(items):
            assignments[key] = {
                "assignment_status": "classified",
                "business_type": next(iter({item["business_type"] for item in classified}))
                if len({item["business_type"] for item in classified}) == 1 else None,
                "business_subject": next(iter({item["business_subject"] for item in classified}))
                if len({item["business_subject"] for item in classified}) == 1 else None,
                "strategy": next(iter({item["strategy"] for item in classified}))
                if len({item["strategy"] for item in classified}) == 1 else None,
            }
        else:
            assignments[key] = {
                "assignment_status": "unclassified",
                "business_type": None,
                "business_subject": None,
                "strategy": None,
            }
    return assignments


def _active_position_batches(cur, end_date: str = "", account_ids=None) -> Dict[int, Dict[str, Any]]:
    scope, params = _account_scope("account_id", account_ids)
    rows = db._exec(
        cur,
        f"""
        SELECT id, account_id, range_start, range_end, position_snapshot_date,
               position_count, status, statement_type, source_priority
        FROM trading_import_batches
        WHERE status = 'active' AND position_snapshot_date IS NOT NULL{scope}
        ORDER BY id DESC
        """,
        params,
    ).fetchall()
    selected: Dict[int, Dict[str, Any]] = {}
    target = _date_key(end_date) if end_date else ""
    for raw_row in rows:
        row = dict(raw_row)
        snapshot_date = _date_key(row.get("position_snapshot_date"))
        if not snapshot_date or (target and snapshot_date != target):
            continue
        source_type = _statement_source_type(row)
        priority = int(row.get("source_priority") or (200 if source_type == "monthly" else 100))
        row["_snapshot_date"] = snapshot_date
        row["_source_type"] = source_type
        row["_source_priority"] = priority
        previous = selected.get(int(row["account_id"]))
        if previous is None or (
            snapshot_date,
            priority,
            int(row["id"]),
        ) > (
            previous["_snapshot_date"],
            previous["_source_priority"],
            int(previous["id"]),
        ):
            selected[int(row["account_id"])] = row
    return selected


def _position_baseline_rows(
    cur,
    batch: Mapping[str, Any],
    assignment_map: Optional[Mapping[tuple[str, str, str], Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    rows = db._exec(
        cur,
        """
        SELECT ps.*, b.statement_type, b.source_priority
        FROM trading_position_snapshots ps
        JOIN trading_import_batches b ON b.id = ps.batch_id
        WHERE ps.batch_id = ? AND ps.is_current = 1
          AND REPLACE(ps.snapshot_date, '-', '') = ?
        ORDER BY ps.contract, ps.direction, ps.id
        """,
        (int(batch["id"]), str(batch["_snapshot_date"]).replace("-", "")),
    ).fetchall()
    result = []
    for raw_row in rows:
        row = dict(raw_row)
        source_type = str(batch["_source_type"])
        assignment = (assignment_map or {}).get(
            (
                str(row.get("contract") or "").strip().lower(),
                _position_direction(row.get("direction")),
                str(row.get("asset_type") or "").strip().lower(),
            ),
            {
                "assignment_status": "unclassified",
                "business_type": None,
                "business_subject": None,
                "strategy": None,
            },
        )
        row.update(
            {
                "snapshot_date": batch["_snapshot_date"].replace("-", ""),
                "source_type": source_type,
                "source_label": SOURCE_LABELS[source_type],
                "formation_method": "settlement_snapshot",
                "fact_status": "settlement_confirmed",
                "can_classify": True,
                "assignment_status": assignment.get("assignment_status", "unclassified"),
                "business_type": assignment.get("business_type"),
                "business_subject": assignment.get("business_subject"),
                "strategy": assignment.get("strategy"),
            }
        )
        result.append(row)
    return result


def _position_account_ids(cur, account_ids=None) -> List[int]:
    scope, params = _account_scope("account_id", account_ids)
    rows = db._exec(
        cur,
        f"""
        SELECT account_id FROM trading_import_batches
        WHERE status = 'active' AND position_snapshot_date IS NOT NULL{scope}
        UNION
        SELECT account_id FROM trading_intraday_position_snapshots
        WHERE complete IS TRUE{scope}
        UNION
        SELECT account_id FROM trading_intraday_fills
        WHERE data_status = 'provisional'{scope}
        """,
        params * 3,
    ).fetchall()
    return sorted({int(row["account_id"]) for row in rows if row["account_id"] is not None})


def _latest_wh6_snapshot(
    cur, account_id: int, baseline_date: str = ""
) -> tuple[Optional[Dict[str, Any]], bool]:
    rows = db._exec(
        cur,
        """
        SELECT * FROM trading_intraday_position_snapshots
        WHERE account_id = ? AND complete IS TRUE
        ORDER BY snapshot_timestamp DESC, id DESC
        """,
        (account_id,),
    ).fetchall()
    selected: Optional[Dict[str, Any]] = None
    persistent_conflict = False
    for raw_row in rows:
        row = dict(raw_row)
        trade_date = _date_key(row.get("trade_date"))
        if not trade_date:
            continue
        if baseline_date and trade_date <= baseline_date:
            if str(row.get("conflict_status") or "none") != "none":
                persistent_conflict = True
            continue
        if str(row.get("conflict_status") or "none") != "none":
            persistent_conflict = True
            continue
        if selected is None or (
            trade_date,
            _parse_datetime(row.get("snapshot_timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
            int(row["id"]),
        ) > (
            _date_key(selected.get("trade_date")),
            _parse_datetime(selected.get("snapshot_timestamp")) or datetime.min.replace(tzinfo=timezone.utc),
            int(selected["id"]),
        ):
            selected = row
    return (None if persistent_conflict else selected), persistent_conflict


def _wh6_snapshot_rows(cur, snapshot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = db._exec(
        cur,
        """
        SELECT * FROM trading_intraday_position_rows
        WHERE snapshot_id = ? AND account_id = ?
        ORDER BY contract, direction, id
        """,
        (int(snapshot["id"]), int(snapshot["account_id"])),
    ).fetchall()
    result = []
    for raw_row in rows:
        row = dict(raw_row)
        row.update(
            {
                "snapshot_date": _display_date(snapshot["trade_date"]),
                "source_type": "wh6",
                "source_label": "WH6完整快照",
                "source_observed_at": _format_datetime(snapshot.get("snapshot_timestamp")),
                "formation_method": "wh6_snapshot",
                "fact_status": "provisional",
                "margin": None,
                "valuation_price": None,
                "floating_pnl": None,
                "can_classify": False,
                "assignment_status": "not_applicable",
                "source_record_count": 1,
            }
        )
        result.append(row)
    return result


def _position_fills_after_baseline(
    cur, account_id: int, baseline_date: str
) -> List[Dict[str, Any]]:
    rows = db._exec(
        cur,
        """
        SELECT * FROM trading_intraday_fills
        WHERE account_id = ? AND data_status = 'provisional'
        ORDER BY trade_date ASC, trade_time ASC, id ASC
        """,
        (account_id,),
    ).fetchall()
    result = []
    for raw_row in rows:
        row = dict(raw_row)
        trade_date = _date_key(row.get("trade_date"))
        if not trade_date or trade_date <= baseline_date:
            continue
        if reconciliation.statement_coverage_for_date(cur, account_id, row["trade_date"]):
            continue
        result.append(row)
    return result


def _collector_freshness(
    cur, account_id: int, now: datetime, snapshot: Optional[Mapping[str, Any]], conflict: bool
) -> Dict[str, Any]:
    devices = db._exec(
        cur,
        """
        SELECT last_seen_at FROM trading_collector_devices
        WHERE account_id = ? AND status = 'active' AND last_seen_at IS NOT NULL
        ORDER BY last_seen_at DESC
        """,
        (account_id,),
    ).fetchall()
    last_seen = _parse_datetime(devices[0]["last_seen_at"]) if devices else None
    if last_seen is None and snapshot is not None:
        last_seen = _parse_datetime(snapshot.get("snapshot_timestamp"))
    age_seconds = max(0, int((now - last_seen).total_seconds())) if last_seen else None
    if conflict:
        status = "conflict"
    elif age_seconds is not None and age_seconds <= 30:
        status = "current"
    else:
        status = "stale"
    return {
        "freshness_status": status,
        "collector_last_seen_at": _format_datetime(last_seen),
        "age_seconds": age_seconds,
    }


def _filter_position_items(items: List[Dict[str, Any]], filters: EffectiveFactFilters) -> List[Dict[str, Any]]:
    filtered = []
    for item in items:
        if filters.fact_status and item["fact_status"] != filters.fact_status:
            continue
        if filters.contract and filters.contract.lower() not in str(item.get("contract") or "").lower():
            continue
        if filters.direction and item["direction"] != _position_direction(filters.direction):
            continue
        if filters.asset_type and item["asset_type"] != filters.asset_type:
            continue
        if filters.classification in {"classified", "unclassified"}:
            if item["fact_status"] != "settlement_confirmed" or item.get("assignment_status") != filters.classification:
                continue
        if filters.start_date and _date_key(item.get("snapshot_date")) < (_date_key(filters.start_date) or _date_key(item.get("snapshot_date"))):
            continue
        filtered.append(item)
    return filtered


def _position_result(
    items: List[Dict[str, Any]],
    filters: EffectiveFactFilters,
    *,
    data_status: str,
    baseline_snapshot_date: Optional[str],
    formation_method: str,
    freshness: Mapping[str, Any],
    warnings: Iterable[str],
    include_all_items: bool = False,
) -> Dict[str, Any]:
    filtered = _filter_position_items(items, filters)
    filtered.sort(key=lambda item: (str(item.get("contract") or ""), str(item.get("direction") or "")))
    total = len(filtered)
    total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)
    page = min(filters.page, total_pages)
    offset = (page - 1) * filters.page_size
    visible = filtered[offset:offset + filters.page_size]
    provisional_count = sum(item["fact_status"] == "provisional" for item in filtered)
    settlement_count = sum(item["fact_status"] == "settlement_confirmed" for item in filtered)
    provenance = _position_provenance(filtered, freshness)
    result = {
        "items": visible,
        "summary": {
            "record_count": total,
            "source_record_count": sum(int(item.get("source_record_count") or 0) for item in filtered),
            "quantity": (
                sum(float(item.get("quantity") or 0) for item in filtered)
                if data_status == "ok"
                else None
            ),
            "margin": (
                sum(float(item.get("margin") or 0) for item in filtered if item.get("margin") is not None)
                if data_status == "ok"
                else None
            ),
            "provisional_count": provisional_count,
            "settlement_confirmed_count": settlement_count,
            "contains_provisional": provisional_count > 0,
        },
        "page": page,
        "page_size": filters.page_size,
        "total_items": total,
        "total_pages": total_pages,
        "data_status": data_status,
        "baseline_snapshot_date": baseline_snapshot_date,
        "formation_method": formation_method,
        "fact_status": (
            "provisional"
            if provisional_count
            else "settlement_confirmed"
            if data_status == "ok"
            else "unavailable"
        ),
        "position_status": "current" if data_status == "ok" else "unavailable",
        "as_of_time": freshness.get("as_of_time"),
        "freshness_status": freshness.get("freshness_status"),
        "collector_last_seen_at": freshness.get("collector_last_seen_at"),
        "age_seconds": freshness.get("age_seconds"),
        "provenance": provenance,
        "risk_eligible": False,
        "warnings": list(warnings),
    }
    if include_all_items:
        result["all_items"] = filtered
    return result


def query_effective_positions(
    cur,
    filters: EffectiveFactFilters,
    *,
    now: Any = None,
    include_all_items: bool = False,
) -> Dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.replace(microsecond=0)
    baseline_batches = _active_position_batches(cur, filters.end_date, filters.account_ids)
    assignment_map = _position_assignment_map(cur, filters.account_ids)
    if filters.end_date:
        all_items: List[Dict[str, Any]] = []
        for batch in baseline_batches.values():
            all_items.extend(_position_baseline_rows(cur, batch, assignment_map))
        all_items = _group_position_items(all_items)
        freshness = {
            "as_of_time": current.isoformat(),
            "freshness_status": "stale",
            "collector_last_seen_at": None,
            "age_seconds": None,
        }
        return _position_result(
            all_items,
            filters,
            data_status="ok" if all_items else "no_position_snapshot",
            baseline_snapshot_date=next(iter(baseline_batches.values()), {}).get("_snapshot_date")
            if baseline_batches else None,
            formation_method="settlement_snapshot",
            freshness=freshness,
            warnings=[],
            include_all_items=include_all_items,
        )

    all_items = []
    warnings: List[str] = []
    overall_status = "ok"
    overall_method = "settlement_snapshot"
    baseline_dates: List[str] = []
    freshness_statuses: List[str] = []
    latest_last_seen: Optional[str] = None
    latest_age: Optional[int] = None
    account_ids = _position_account_ids(cur, filters.account_ids)
    unavailable_accounts = 0
    for account_id in account_ids:
        batch = baseline_batches.get(account_id)
        baseline_date = str(batch["_snapshot_date"]) if batch else ""
        if baseline_date:
            baseline_dates.append(baseline_date.replace("-", ""))
        baseline_rows = _position_baseline_rows(cur, batch, assignment_map) if batch else []
        full_snapshot, persistent_conflict = _latest_wh6_snapshot(cur, account_id, baseline_date)
        freshness = _collector_freshness(cur, account_id, current, full_snapshot, persistent_conflict)
        freshness_statuses.append(str(freshness["freshness_status"]))
        if freshness.get("collector_last_seen_at") and (
            latest_last_seen is None or str(freshness["collector_last_seen_at"]) > latest_last_seen
        ):
            latest_last_seen = str(freshness["collector_last_seen_at"])
            latest_age = freshness.get("age_seconds")
        if persistent_conflict:
            warnings.append("WH6完整持仓快照存在持久冲突，未用于当前持仓")
        if full_snapshot is not None:
            all_items.extend(_wh6_snapshot_rows(cur, full_snapshot))
            overall_method = "wh6_snapshot"
            continue
        if batch is None:
            unavailable_accounts += 1
            warnings.append("缺少结算持仓基线和完整 WH6 持仓快照")
            continue
        fills = _position_fills_after_baseline(cur, account_id, baseline_date)
        projected = infer_positions_from_fills(baseline_rows, fills)
        if projected["status"] == "projection_error":
            overall_status = "projection_error"
            warnings.extend(projected["warnings"])
            all_items.extend(projected["items"])
        else:
            all_items.extend(projected["items"])
            if fills:
                overall_method = "inferred_from_settlement_and_fills"
    if (unavailable_accounts or not account_ids) and not all_items:
        freshness = {
            "as_of_time": current.isoformat(),
            "freshness_status": "unavailable",
            "collector_last_seen_at": None,
            "age_seconds": None,
        }
        return _position_result(
            [],
            filters,
            data_status="unavailable",
            baseline_snapshot_date=None,
            formation_method="",
            freshness=freshness,
            warnings=warnings or ["缺少最近一次结算确认持仓基线"],
            include_all_items=include_all_items,
        )
    all_items = _group_position_items(all_items)
    if "conflict" in freshness_statuses:
        freshness_status = "conflict"
    elif "stale" in freshness_statuses:
        freshness_status = "stale"
    else:
        freshness_status = "current"
    freshness = {
        "as_of_time": current.isoformat(),
        "freshness_status": freshness_status,
        "collector_last_seen_at": latest_last_seen,
        "age_seconds": latest_age,
    }
    return _position_result(
        all_items,
        filters,
        data_status=overall_status,
        baseline_snapshot_date=max(baseline_dates) if baseline_dates else None,
        formation_method=overall_method,
        freshness=freshness,
        warnings=warnings,
        include_all_items=include_all_items,
    )
