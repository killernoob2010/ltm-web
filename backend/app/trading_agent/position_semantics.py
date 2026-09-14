"""Deterministic position-domain semantics used by the Agent.

This module deliberately has no database, model, or network dependency.  It
turns the heterogeneous rows returned by the effective-facts adapter into a
small vocabulary that can be filtered and aggregated without asking the model
to infer contract attributes or arithmetic.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Iterable, Mapping, Sequence

from .contracts import MetricValue


_OPTION_RE = re.compile(
    r"^(?P<product>[a-z]+)(?P<month>\d{3,4})-(?P<kind>[cp])-(?P<strike>\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
_FUTURE_RE = re.compile(r"^(?P<product>[a-z]+)(?P<month>\d{3,4})$", re.IGNORECASE)

_PRODUCT_ALIASES = {
    "i": "i",
    "ironore": "i",
    "iron_ore": "i",
    "iron-ore": "i",
    "iron ore": "i",
    "铁矿": "i",
    "铁矿石": "i",
}
_OPTION_ALIASES = {
    "call": "call", "c": "call", "看涨": "call", "看涨期权": "call",
    "put": "put", "p": "put", "看跌": "put", "看跌期权": "put",
}
_DIRECTION_ALIASES = {
    "buy": "买", "long": "买", "买": "买", "买入": "买", "多": "买",
    "sell": "卖", "short": "卖", "卖": "卖", "卖出": "卖", "空": "卖",
}
_DECIMAL_ZERO = Decimal("0")
_ALLOWED_GROUPS = {
    "account", "contract", "product", "exchange", "asset_type", "direction", "trade_date",
    "fact_status", "assignment_status", "contract_month", "option_type", "strike_price",
}
_ALLOWED_METRICS = {
    "count", "quantity", "floating_pnl", "gross_quantity", "gross_buy_quantity", "gross_sell_quantity",
    "net_quantity", "net_sell_quantity", "net_tons", "net_signed_tons", "net_wan_tons",
    "strike_min", "strike_max", "covered_rows", "eligible_rows",
}


@dataclass(frozen=True)
class ContractParts:
    """Reliable attributes parsed from one normalized contract code."""

    contract: str
    product: str | None
    contract_month: str | None
    option_type: str | None
    strike_price: Decimal | None
    parse_status: str

    @property
    def is_option(self) -> bool:
        return self.option_type in {"call", "put"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def normalize_product(value: Any) -> str:
    text = _text(value).lower().replace(" ", "")
    return _PRODUCT_ALIASES.get(text, text)


def normalize_option_type(value: Any) -> str | None:
    text = _text(value).lower()
    return _OPTION_ALIASES.get(text)


def normalize_direction(value: Any) -> str:
    text = _text(value).lower()
    return _DIRECTION_ALIASES.get(text, _text(value))


def parse_contract(value: Any) -> ContractParts:
    contract = _text(value).lower()
    option = _OPTION_RE.fullmatch(contract)
    if option:
        strike = _decimal(option.group("strike"))
        if strike is None:
            return ContractParts(contract, None, None, None, None, "invalid_strike")
        return ContractParts(
            contract=contract,
            product=normalize_product(option.group("product")),
            contract_month=option.group("month"),
            option_type="call" if option.group("kind").lower() == "c" else "put",
            strike_price=strike,
            parse_status="ok",
        )
    future = _FUTURE_RE.fullmatch(contract)
    if future:
        return ContractParts(
            contract=contract,
            product=normalize_product(future.group("product")),
            contract_month=future.group("month"),
            option_type=None,
            strike_price=None,
            parse_status="ok",
        )
    return ContractParts(contract, None, None, None, None, "unparsed")


def _stable_position_identity(row: Mapping[str, Any], parts: ContractParts) -> str:
    account = _text(row.get("account_id")) or "unknown-account"
    exchange = _text(row.get("exchange")).lower()
    asset_type = _text(row.get("asset_type")).lower()
    direction = normalize_direction(row.get("direction"))
    snapshot = _text(row.get("snapshot_date") or row.get("trade_date"))
    return ":".join((account, snapshot, exchange, parts.contract, asset_type, direction))


def normalize_position(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy one source row and attach canonical contract/domain attributes."""
    result = dict(row)
    result["contract"] = _text(result.get("contract")).lower()
    parts = parse_contract(result.get("contract"))
    result["product"] = parts.product or normalize_product(result.get("product")) or None
    result["contract_month"] = parts.contract_month
    result["option_type"] = parts.option_type
    result["strike_price"] = str(parts.strike_price) if parts.strike_price is not None else None
    result["contract_parse_status"] = parts.parse_status
    result["direction"] = normalize_direction(result.get("direction"))
    result["position_identity"] = _stable_position_identity(result, parts)
    return result


def normalize_positions(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_position(row) for row in rows]


def _matches_product(value: Any, requested: Sequence[str]) -> bool:
    if not requested:
        return True
    actual = normalize_product(value)
    return actual in {normalize_product(item) for item in requested}


def _matches_contract(row: Mapping[str, Any], contracts: Sequence[str]) -> bool:
    if not contracts:
        return True
    actual = _text(row.get("contract")).lower()
    wanted = {_text(item).lower() for item in contracts if _text(item)}
    return actual in wanted


def _matches_strike(value: Any, minimum: Decimal | None, maximum: Decimal | None,
                    minimum_inclusive: bool, maximum_inclusive: bool) -> bool:
    strike = _decimal(value)
    if minimum is not None:
        if strike is None or (strike < minimum or (strike == minimum and not minimum_inclusive)):
            return False
    if maximum is not None:
        if strike is None or (strike > maximum or (strike == maximum and not maximum_inclusive)):
            return False
    return True


def select_positions(
    rows: Iterable[Mapping[str, Any]],
    query: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the domain filters after the authorized effective-facts query.

    The second return value contains rows whose requested domain attributes
    cannot be determined.  Such rows are never silently discarded when a
    semantic filter is present; the caller must surface the resulting scope
    limitation in the envelope.
    """
    normalized = normalize_positions(rows)
    filters = getattr(query, "filters", None)
    products = list(getattr(filters, "products", []) or [])
    months = {_text(item) for item in (getattr(filters, "contract_months", []) or [])}
    option_type = _text(getattr(filters, "option_type", "all")).lower() or "all"
    strike_min = _decimal(getattr(filters, "strike_min", None))
    strike_max = _decimal(getattr(filters, "strike_max", None))
    min_inclusive = bool(getattr(filters, "strike_min_inclusive", True))
    max_inclusive = bool(getattr(filters, "strike_max_inclusive", True))
    semantic_filter = bool(products or months or option_type != "all" or strike_min is not None or strike_max is not None)
    selected: list[dict[str, Any]] = []
    uncertain: list[dict[str, Any]] = []
    asset_type = _text(getattr(query, "asset_type", "all")).lower()
    direction = _text(getattr(query, "direction", "all")).lower()
    for row in normalized:
        row_asset = _text(row.get("asset_type")).lower()
        row_direction = normalize_direction(row.get("direction"))
        if asset_type != "all" and row_asset != asset_type:
            continue
        if direction != "all" and row_direction != normalize_direction(direction):
            continue
        if not _matches_contract(row, getattr(query, "contracts", []) or []):
            continue
        if not semantic_filter:
            selected.append(row)
            continue
        if row.get("contract_parse_status") != "ok":
            uncertain.append({"row_ref": row.get("row_ref"), "contract": row.get("contract"),
                              "reason": "contract_attributes_unavailable"})
            continue
        if not _matches_product(row.get("product"), products):
            continue
        if months and row.get("contract_month") not in months:
            continue
        if option_type != "all" and row.get("option_type") != option_type:
            continue
        if strike_min is not None or strike_max is not None:
            if not row.get("option_type") or not _matches_strike(
                row.get("strike_price"), strike_min, strike_max, min_inclusive, max_inclusive
            ):
                continue
        selected.append(row)
    return selected, uncertain


def _metric_value(value: Decimal | None, unit: str, *, covered: int, eligible: int,
                  empty_status: str = "unavailable") -> MetricValue:
    if eligible == 0:
        if empty_status == "complete":
            return MetricValue(value="0", unit=unit, status="complete", covered_rows=0, eligible_rows=0)
        return MetricValue(value=None, unit=unit, status="unavailable", covered_rows=0, eligible_rows=0)
    if value is None or covered == 0:
        return MetricValue(value=None, unit=unit, status="unavailable", covered_rows=0, eligible_rows=eligible)
    status = "complete" if covered == eligible else "partial"
    return MetricValue(value=str(value), unit=unit, status=status,
                       covered_rows=covered, eligible_rows=eligible)


def _sum_decimal(rows: Sequence[Mapping[str, Any]], field: str) -> tuple[Decimal | None, int]:
    values: list[Decimal] = []
    for row in rows:
        value = _decimal(row.get(field))
        if value is not None:
            values.append(value)
    return (sum(values, _DECIMAL_ZERO) if values else None, len(values))


def _signed_quantity(rows: Sequence[Mapping[str, Any]]) -> tuple[Decimal | None, int]:
    values: list[Decimal] = []
    for row in rows:
        quantity = _decimal(row.get("quantity"))
        direction = normalize_direction(row.get("direction"))
        if quantity is None or direction not in {"买", "卖"}:
            continue
        values.append(quantity if direction == "卖" else -quantity)
    return (sum(values, _DECIMAL_ZERO) if values else None, len(values))


def _net_tons(rows: Sequence[Mapping[str, Any]]) -> tuple[Decimal | None, int]:
    values: list[Decimal] = []
    for row in rows:
        quantity = _decimal(row.get("quantity"))
        multiplier = _decimal(row.get("contract_multiplier"))
        direction = normalize_direction(row.get("direction"))
        if quantity is None or multiplier is None or direction not in {"买", "卖"}:
            continue
        signed = quantity if direction == "卖" else -quantity
        values.append(signed * multiplier)
    return (sum(values, _DECIMAL_ZERO) if values else None, len(values))


def _strike_metric(rows: Sequence[Mapping[str, Any]], operation: str) -> tuple[Decimal | None, int]:
    values = [_decimal(row.get("strike_price")) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None, 0
    return (min(values) if operation == "min" else max(values), len(values))


def metric_for_positions(rows: Sequence[Mapping[str, Any]], name: str, *, empty_status: str = "unavailable") -> MetricValue:
    """Calculate one registered position metric with row-level coverage."""
    eligible = len(rows)
    if name == "count":
        return _metric_value(Decimal(eligible), "条", covered=eligible, eligible=eligible, empty_status=empty_status)
    if name in {"quantity", "gross_quantity"}:
        value, covered = _sum_decimal(rows, "quantity")
        return _metric_value(value, "手", covered=covered, eligible=eligible, empty_status=empty_status)
    if name == "gross_buy_quantity":
        side_rows = [row for row in rows if normalize_direction(row.get("direction")) == "买"]
        eligible_side = sum(1 for row in rows if normalize_direction(row.get("direction")) == "买")
        if not side_rows and rows and all(normalize_direction(row.get("direction")) in {"买", "卖"} for row in rows):
            return MetricValue(value="0", unit="手", status="complete", covered_rows=len(rows), eligible_rows=len(rows))
        value, covered = _sum_decimal(side_rows, "quantity")
        return _metric_value(value, "手", covered=covered, eligible=eligible_side, empty_status=empty_status)
    if name == "gross_sell_quantity":
        side_rows = [row for row in rows if normalize_direction(row.get("direction")) == "卖"]
        eligible_side = sum(1 for row in rows if normalize_direction(row.get("direction")) == "卖")
        if not side_rows and rows and all(normalize_direction(row.get("direction")) in {"买", "卖"} for row in rows):
            return MetricValue(value="0", unit="手", status="complete", covered_rows=len(rows), eligible_rows=len(rows))
        value, covered = _sum_decimal(side_rows, "quantity")
        return _metric_value(value, "手", covered=covered, eligible=eligible_side, empty_status=empty_status)
    if name in {"net_quantity", "net_sell_quantity"}:
        value, covered = _signed_quantity(rows)
        return _metric_value(value, "手", covered=covered, eligible=eligible, empty_status=empty_status)
    if name in {"net_tons", "net_signed_tons"}:
        value, covered = _net_tons(rows)
        return _metric_value(value, "吨", covered=covered, eligible=eligible, empty_status=empty_status)
    if name == "net_wan_tons":
        value, covered = _net_tons(rows)
        if value is not None:
            value = (value / Decimal("10000")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        return _metric_value(value, "万吨", covered=covered, eligible=eligible, empty_status=empty_status)
    if name == "floating_pnl":
        value, covered = _sum_decimal(rows, "floating_pnl")
        return _metric_value(value, "CNY", covered=covered, eligible=eligible, empty_status=empty_status)
    if name == "covered_rows":
        return _metric_value(Decimal(sum(1 for row in rows if row.get("floating_pnl") is not None)), "条",
                             covered=eligible, eligible=eligible, empty_status=empty_status)
    if name == "eligible_rows":
        return _metric_value(Decimal(eligible), "条", covered=eligible, eligible=eligible, empty_status=empty_status)
    if name == "strike_min":
        value, covered = _strike_metric(rows, "min")
        return _metric_value(value, "点", covered=covered, eligible=eligible, empty_status=empty_status)
    if name == "strike_max":
        value, covered = _strike_metric(rows, "max")
        return _metric_value(value, "点", covered=covered, eligible=eligible, empty_status=empty_status)
    raise ValueError(f"不支持的持仓指标: {name}")


def aggregate_positions(
    rows: Iterable[Mapping[str, Any]],
    group_by: Sequence[str] = (),
    metrics: Sequence[str] = ("quantity",),
    *,
    empty_status: str = "unavailable",
) -> dict[str, Any]:
    """Aggregate normalized rows once, retaining group coverage and identity."""
    group_by = tuple(group_by)
    metrics = tuple(metrics)
    if any(field not in _ALLOWED_GROUPS for field in group_by):
        raise ValueError("该属性尚无可查询的可靠来源")
    if any(metric not in _ALLOWED_METRICS for metric in metrics):
        raise ValueError("指标不适用于持仓事实")
    normalized = normalize_positions(rows)
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        key = tuple(
            (row.get("account_id"), row.get(field)) if field == "account" else row.get(field)
            for field in group_by
        )
        groups[key].append(row)
    ordered = sorted(groups.items(), key=lambda item: tuple(str(value or "") for value in item[0]))
    group_rows = []
    for key, items in ordered:
        metric_values = {name: metric_for_positions(items, name, empty_status=empty_status) for name in metrics}
        dimensions = {
            field: (items[0].get(field) if field == "account" else key[index])
            for index, field in enumerate(group_by)
        }
        group_rows.append({
            "dimensions": dimensions,
            "metrics": {name: value.model_dump(mode="json") for name, value in metric_values.items()},
            "row_refs": [str(item.get("row_ref")) for item in items if item.get("row_ref") is not None],
        })
    return {
        "metrics": {name: metric_for_positions(normalized, name, empty_status=empty_status) for name in metrics},
        "groups": group_rows,
        "group_count": len(group_rows),
        "row_count": len(normalized),
    }


def presentation_preference(text: Any) -> str:
    """Resolve an explicit user display request without guessing a default."""
    value = _text(text)
    if re.search(r"纯文字|纯文本|只要文字|只用文字|不要表格|不用表格|不需要表格|文字展示", value):
        return "text"
    if re.search(r"表格|列表", value):
        return "table"
    if re.search(r"图表|柱状图|折线图|曲线|画图|绘图", value):
        return "chart"
    return "auto"


__all__ = [
    "ContractParts", "aggregate_positions", "metric_for_positions", "normalize_direction",
    "normalize_option_type", "normalize_position", "normalize_positions", "normalize_product",
    "parse_contract", "presentation_preference", "select_positions",
]
