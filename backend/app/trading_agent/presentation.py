"""Deterministic, read-only projections of frozen Agent result snapshots."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from math import ceil
from typing import Any

from . import facts, store
from .answer_contracts import ViewRequest


PAGE_SIZES = {20, 50, 100}

_LABELS = {
    "account": "账户", "contract": "合约", "asset_type": "资产类型", "direction": "方向",
    "trade_date": "交易日期", "quantity": "手数", "average_price": "开仓均价",
    "valuation_price": "最新成交价", "floating_pnl": "浮盈亏", "realized_close_pnl": "平仓盈亏",
    "fee": "手续费", "market_time": "行情时间", "valuation_status": "估值状态",
    "assignment_status": "归属状态", "basis": "基差", "futures_close": "期货收盘价",
    "wet_spot_price": "湿吨现货价", "business_date": "业务日期", "port": "港口",
    "product": "品种", "data_status": "数据状态", "row_ref": "行号", "futures_series": "期货序列",
    "business_year": "业务年份", "business_week": "业务周次", "week_label": "周次标签",
    "rule_version": "规则版本", "parameter_version": "参数版本", "source_workbook_name": "来源文件",
    "source_workbook_sha256": "来源文件哈希", "standardized_spot_price": "标准化现货价",
    "quality_adjustment": "质量调整", "brand_adjustment": "品牌调整",
}
_UNITS = {
    "quantity": "手", "average_price": "元", "valuation_price": "元",
    "floating_pnl": "元", "realized_close_pnl": "元", "fee": "元",
    "basis": "元/标准化吨", "futures_close": "元/吨", "wet_spot_price": "元/湿吨",
}
_DATE_FIELDS = {"trade_date", "business_date", "expiry_date"}
_DATETIME_FIELDS = {"market_time", "captured_at", "data_as_of"}
_STATUS_FIELDS = {"valuation_status", "assignment_status", "data_status", "fact_status"}
_DECIMAL_FIELDS = {
    "quantity", "average_price", "valuation_price", "floating_pnl", "realized_close_pnl", "fee",
    "contract_multiplier", "underlying_price", "iv", "delta", "gamma", "theta", "vega", "rho",
    "basis", "futures_close", "wet_spot_price", "standardized_spot_price", "quality_adjustment",
    "brand_adjustment", "value", "count",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() else None


def _wire_value(value: Any):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return str(value) if _decimal(value) is not None else None
    if isinstance(value, (date, datetime)):
        return value.isoformat(timespec="seconds") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def _row_ref(row: dict, index: int) -> str:
    value = row.get("row_ref")
    return str(value) if value not in (None, "") else f"r{index + 1}"


def _validate_projection_args(fields, *, page, page_size, sort_by):
    if type(page) is not int or page < 1 or page_size not in PAGE_SIZES:
        raise ValueError("分页无效")
    if not isinstance(fields, list) or len(fields) > 16 or len(set(fields)) != len(fields):
        raise ValueError("字段选择无效")
    if sort_by is not None and sort_by not in fields:
        raise ValueError("排序字段无效")


def _sort_key(value: Any):
    number = _decimal(value)
    if number is not None:
        return (0, number)
    return (1, str(value).casefold())


def project_page(
    rows: list[dict],
    fields: list[str],
    *,
    page: int,
    page_size: int,
    sort_by: str | None = None,
    descending: bool = False,
) -> dict:
    """Project and page a complete frozen row set.

    Sorting is deliberately performed before slicing.  Nulls are kept in a
    separate tail so descending order cannot move them to the first page.
    """
    _validate_projection_args(fields, page=page, page_size=page_size, sort_by=sort_by)

    projected = _project_rows(rows, fields, sort_by=sort_by, descending=descending)
    return _page_projected(projected, page=page, page_size=page_size)


def _project_rows(rows: list[dict], fields: list[str], *, sort_by: str | None, descending: bool) -> list[dict]:
    projected = []
    for index, source in enumerate(rows):
        source = source if isinstance(source, dict) else {}
        projected.append({
            "row_ref": _row_ref(source, index),
            **{field: _wire_value(source.get(field)) for field in fields if field != "row_ref"},
            "_sort_source": source,
            "_sort_index": index,
        })

    if sort_by is not None:
        populated = []
        nulls = []
        for item in projected:
            value = item["_sort_source"].get(sort_by)
            if value is None or (sort_by in _DECIMAL_FIELDS and _decimal(value) is None):
                nulls.append(item)
            else:
                populated.append(item)
        populated.sort(key=lambda item: (item["row_ref"], item["_sort_index"]))
        populated.sort(key=lambda item: _sort_key(item["_sort_source"].get(sort_by)), reverse=descending)
        projected = populated + nulls
    return projected


def _clean_projected(item: dict) -> dict:
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _page_projected(projected: list[dict], *, page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    visible = projected[start:start + page_size]
    visible = [_clean_projected(item) for item in visible]
    total_rows = len(projected)
    return {
        "rows": visible,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_rows": total_rows,
            "total_pages": max(1, ceil(total_rows / page_size)),
        },
    }


def _default_fields(kind: str) -> list[str]:
    if kind == "positions":
        return ["contract", "direction", "quantity", "average_price", "valuation_price",
                "floating_pnl", "market_time", "valuation_status"]
    if kind in {"trades", "closes"}:
        return ["trade_date", "contract", "direction", "quantity", "average_price", "fee", "realized_close_pnl"]
    if kind == "market_series":
        return ["business_date", "port", "product", "basis", "futures_close", "wet_spot_price", "data_status"]
    return ["row_ref"]


def _column(field: str) -> dict:
    if field in _DATE_FIELDS:
        data_type = "date"
    elif field in _DATETIME_FIELDS:
        data_type = "datetime"
    elif field in _STATUS_FIELDS or field.endswith("_status"):
        data_type = "status"
    elif field in _DECIMAL_FIELDS:
        data_type = "decimal"
    else:
        data_type = "text"
    scale = 0 if field in {"quantity", "count"} else 2 if data_type == "decimal" else None
    return {
        "key": field,
        "label": _LABELS.get(field, field),
        "unit": _UNITS.get(field),
        "type": data_type,
        "scale": scale,
    }


def _chart_fallback(reason: str, x_key: str | None = None) -> dict:
    return {
        "fallback": "table",
        "reason": reason,
        "x_key": x_key,
        "labels": [],
        "unit": None,
        "series": [],
    }


def build_chart_series(view_page: dict) -> dict:
    """Build bounded chart data from an already validated view projection.

    The function never combines duplicate x values or samples an oversized
    result.  Callers can render the returned table fallback instead.
    """
    if not isinstance(view_page, dict):
        raise ValueError("图表视图格式无效")
    kind = view_page.get("kind")
    fields = view_page.get("fields")
    columns = view_page.get("columns")
    rows = view_page.get("rows")
    if kind not in {"bar", "line"} or not isinstance(fields, list) or len(fields) < 2:
        raise ValueError("图表字段无效")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("图表视图格式无效")
    x_key = fields[0]
    column_map = {}
    for column in columns:
        if not isinstance(column, dict) or not isinstance(column.get("key"), str):
            raise ValueError("图表列定义无效")
        if column["key"] in column_map:
            raise ValueError("图表列定义重复")
        column_map[column["key"]] = column
    if any(field not in column_map for field in fields):
        raise ValueError("图表字段未登记")
    y_keys = fields[1:]
    if len(y_keys) > 4:
        return _chart_fallback("series_limit", x_key)
    if len(rows) > 500:
        return _chart_fallback("point_limit", x_key)
    if not y_keys:
        return _chart_fallback("empty_series", x_key)
    if any(column_map[key].get("type") != "decimal" for key in y_keys):
        return _chart_fallback("series_type", x_key)
    units = [column_map[key].get("unit") for key in y_keys]
    if len(set(units)) != 1:
        return _chart_fallback("unit_mismatch", x_key)

    labels = []
    values_by_key = {key: [] for key in y_keys}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("图表行格式无效")
        label = row.get(x_key)
        if label is None:
            return _chart_fallback("missing_x", x_key)
        label = _wire_value(label)
        labels.append(str(label))
        for key in y_keys:
            value = row.get(key)
            if value is None:
                values_by_key[key].append(None)
                continue
            value = _wire_value(value)
            value = str(value)
            if _decimal(value) is None:
                return _chart_fallback("invalid_value", x_key)
            values_by_key[key].append(value)
    if len(labels) != len(set(labels)):
        return _chart_fallback("duplicate_x", x_key)
    return {
        "x_key": x_key,
        "labels": labels,
        "unit": units[0],
        "series": [
            {"key": key, "label": column_map[key].get("label") or key, "values": values_by_key[key]}
            for key in y_keys
        ],
    }


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat(timespec="seconds")


def _metric_wire(metric: Any):
    if metric is None:
        return None
    value = metric.get("value") if isinstance(metric, dict) else getattr(metric, "value", None)
    status = metric.get("status") if isinstance(metric, dict) else getattr(metric, "status", None)
    return _wire_value(value) if status in {"complete", "partial"} else None


def _summary(saved: Any, rows: list[dict], fields: list[str]) -> dict:
    envelope = saved.envelope
    metrics = getattr(envelope, "metrics", {}) or {}
    result = {}
    kind = (envelope.payload or {}).get("kind") if hasattr(envelope, "payload") else None
    for field in fields:
        metric = metrics.get(field) if hasattr(metrics, "get") else None
        value = _metric_wire(metric)
        if value is None and metric is None and kind != "market_series" and field in _DECIMAL_FIELDS:
            values = [_decimal(row.get(field)) for row in rows]
            values = [item for item in values if item is not None]
            value = str(sum(values, Decimal(0))) if values else None
        result[field] = value
    if "floating_pnl" in fields:
        metric = metrics.get("floating_pnl") if hasattr(metrics, "get") else None
        status = metric.get("status") if isinstance(metric, dict) else getattr(metric, "status", None)
        if status == "complete":
            result["pnl_label"] = "浮盈亏已覆盖"
            result["pnl_scope"] = "full"
        elif status == "partial":
            result["pnl_label"] = "部分持仓可估值"
            result["pnl_scope"] = "covered_only"
        else:
            result["pnl_label"] = "暂无可估值持仓"
            result["pnl_scope"] = "unavailable"
    return result


def _coverage(saved: Any, rows: list[dict]) -> dict:
    envelope = saved.envelope
    kind = (envelope.payload or {}).get("kind") if hasattr(envelope, "payload") else None
    eligible_rows = len(rows)
    quantities = [(row, _decimal(row.get("quantity"))) for row in rows]
    eligible_quantity = sum((value for _, value in quantities if value is not None), Decimal(0))
    if kind == "positions":
        covered_rows = sum(1 for row in rows if row.get("valuation_price") is not None)
        covered = [value for row, value in quantities if row.get("valuation_price") is not None and value is not None]
        covered_contract_keys = {
            (row.get("contract"), row.get("direction"))
            for row in rows if row.get("valuation_price") is not None and row.get("contract") is not None
        }
    elif kind == "market_series":
        covered_rows = sum(1 for row in rows if row.get("data_status") == "有效")
        covered = [value for row, value in quantities if row.get("data_status") == "有效" and value is not None]
        covered_contract_keys = set()
    else:
        covered_rows = eligible_rows
        covered = [value for _, value in quantities if value is not None]
        covered_contract_keys = {
            (row.get("contract"), row.get("direction"))
            for row in rows if row.get("contract") is not None
        }
    eligible_contract_keys = {
        (row.get("contract"), row.get("direction"))
        for row in rows if row.get("contract") is not None
    }
    return {
        "eligible_rows": eligible_rows,
        "covered_rows": covered_rows,
        "eligible_quantity": str(eligible_quantity) if any(value is not None for _, value in quantities) else None,
        "covered_quantity": str(sum(covered, Decimal(0))) if covered else "0",
        "eligible_contracts": None if kind == "market_series" else len(eligible_contract_keys),
        "covered_contracts": None if kind == "market_series" else len(covered_contract_keys),
    }


def build_view(
    principal: Any,
    request: ViewRequest,
    store_api=store,
    *,
    page: int = 1,
    page_size: int = 20,
    saved: Any = None,
) -> dict:
    """Build one immutable view descriptor from a saved result snapshot."""
    if not isinstance(request, ViewRequest):
        request = ViewRequest.model_validate(request)
    saved = saved or store_api.load_result(principal, request.result_ref)
    payload = saved.envelope.payload if isinstance(saved.envelope.payload, dict) else {}
    kind = payload.get("kind", "positions")
    fields = list(request.fields) or _default_fields(kind)
    if any(field not in facts.PUBLIC_FIELDS for field in fields):
        raise ValueError("字段不在公开业务目录中")
    if request.sort_by is not None and request.sort_by not in fields:
        raise ValueError("排序字段无效")
    _validate_projection_args(fields, page=page, page_size=page_size, sort_by=request.sort_by)
    projected = _project_rows(saved.rows, fields, sort_by=request.sort_by, descending=request.descending)
    page_result = _page_projected(projected, page=page, page_size=page_size)
    warnings = list(saved.envelope.warnings or [])
    for field in fields:
        if saved.rows and not any(row.get(field) is not None for row in saved.rows):
            warnings.append(f"字段 {field} 在当前结果中无可用值")
    result = {
        "view_id": request.id,
        "kind": request.kind,
        "result_ref": str(request.result_ref),
        "columns": [_column(field) for field in fields],
        "rows": page_result["rows"],
        "pagination": page_result["pagination"],
        "coverage": _coverage(saved, saved.rows),
        "summary": _summary(saved, saved.rows, fields),
        "data_as_of": _timestamp(getattr(saved.envelope, "data_as_of", None)),
        "captured_at": _timestamp(getattr(saved.envelope, "captured_at", None)),
        "warnings": list(dict.fromkeys(warnings)),
    }
    if request.kind in {"bar", "line"}:
        chart = build_chart_series({
            "kind": request.kind,
            "fields": fields,
            "columns": result["columns"],
            "rows": [_clean_projected(item) for item in projected],
        })
        result["chart"] = chart
        if chart.get("fallback"):
            result["warnings"] = list(dict.fromkeys(result["warnings"] + [
                "图表数据不满足展示限制，已保留完整数据表。"
            ]))
    return result


__all__ = ["build_chart_series", "build_view", "project_page"]
