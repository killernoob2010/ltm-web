"""Deterministic, read-only projections of frozen Agent result snapshots."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from math import ceil
import json
import re
from typing import Any

from . import facts, store
from .answer_contracts import ViewRequest
from .semantic_catalog import allowed_fields as dataset_allowed_fields


PAGE_SIZES = {20, 50, 100}

_LABELS = {
    "account": "账户", "contract": "合约", "asset_type": "资产类型", "direction": "方向",
    "trade_date": "交易日期", "quantity": "手数", "average_price": "开仓均价",
    "price": "成交价格", "valuation_price": "最新成交价", "floating_pnl": "浮盈亏", "realized_close_pnl": "平仓盈亏",
    "fee": "手续费", "market_time": "行情时间", "valuation_status": "估值状态",
    "assignment_status": "归属状态", "basis": "基差", "futures_close": "期货收盘价",
    "wet_spot_price": "湿吨现货价", "business_date": "业务日期", "port": "港口",
    "product": "品种", "data_status": "数据状态", "row_ref": "行号", "futures_series": "期货序列",
    "business_year": "业务年份", "business_week": "业务周次", "week_label": "周次标签",
    "rule_version": "规则版本", "parameter_version": "参数版本", "source_workbook_name": "来源文件",
    "source_workbook_sha256": "来源文件哈希", "standardized_spot_price": "标准化现货价",
    "quality_adjustment": "质量调整", "brand_adjustment": "品牌调整",
    "observation_date": "观察日期", "period_start": "期间开始", "period_end": "期间结束",
    "week_start": "周开始", "metric": "指标", "product_pool": "品种池",
    "source_country": "来源国家", "category": "形态", "mainstream_status": "主流状态",
    "value": "数值", "unit": "单位", "value_state": "数值状态", "scope_type": "范围类型",
    "region": "区域", "summary_metric": "汇总指标", "raw_product": "原表品种",
    "raw_grade": "原始品位", "grade": "品位", "arrival_kind": "到港口径", "method": "方法",
    "slice_type": "切片类型", "dimension": "维度", "current_date": "当前日期", "previous_date": "基准日期",
    "current_value": "当前值", "previous_value": "基准值", "delta": "变化量", "delta_pct": "变化比例",
    "comparison_status": "比较状态", "left_date": "左侧日期", "right_date": "右侧日期",
    "left_port": "左侧港口", "right_port": "右侧港口", "inventory_value": "库存值",
    "left_wet_spot_price": "左侧湿吨现货价", "right_wet_spot_price": "右侧湿吨现货价",
    "relation_status": "关联状态", "covered_rows": "已覆盖行数", "eligible_rows": "纳入行数",
    "status": "状态", "row_count": "行数", "matched_rows": "匹配行数", "missing_previous": "缺少基准行数",
}
_UNITS = {
    "quantity": "手", "price": "元", "average_price": "元", "valuation_price": "元",
    "floating_pnl": "元", "realized_close_pnl": "元", "fee": "元",
    "basis": "元/标准化吨", "futures_close": "元/吨", "wet_spot_price": "元/湿吨",
}
_DATE_FIELDS = {"trade_date", "business_date", "expiry_date"}
_DATETIME_FIELDS = {"market_time", "captured_at", "data_as_of"}
_STATUS_FIELDS = {"valuation_status", "assignment_status", "data_status", "fact_status"}
_DECIMAL_FIELDS = {
    "quantity", "price", "average_price", "valuation_price", "floating_pnl", "realized_close_pnl", "fee",
    "contract_multiplier", "underlying_price", "iv", "delta", "gamma", "theta", "vega", "rho",
    "basis", "futures_close", "wet_spot_price", "standardized_spot_price", "quality_adjustment",
    "brand_adjustment", "value", "count",
}
_DATASET_RESULT_KINDS = {"dataset_rows", "dataset_summary", "dataset_comparison", "dataset_relation"}
_DERIVED_FIELDS = {
    "dataset_summary": {"value", "covered_rows", "eligible_rows", "status", "row_ref"},
    "dataset_comparison": {
        "current_date", "previous_date", "current_value", "previous_value", "delta", "delta_pct",
        "comparison_status", "row_ref",
    },
    "dataset_relation": {
        "left_date", "right_date", "left_port", "right_port", "product", "inventory_value", "basis",
        "wet_spot_price", "left_wet_spot_price", "right_wet_spot_price", "delta", "relation_status", "row_ref",
    },
}
_DATASET_DEFAULT_FIELDS = {
    "spot_series": ["observation_date", "business_year", "business_week", "product", "value", "unit", "value_state"],
    "port_inventory": ["observation_date", "port", "product", "value", "unit", "value_state"],
    "inventory_summary": ["observation_date", "port", "summary_metric", "value", "unit", "value_state"],
    "inventory_grade": ["observation_date", "port", "grade", "value", "unit", "value_state"],
    "source_mainstream_inventory": ["observation_date", "port", "product", "value", "unit", "value_state"],
    "arrival_detail": ["observation_date", "arrival_kind", "port", "slice_type", "dimension", "product", "value", "unit", "value_state"],
    "iron_ore_basis": ["business_date", "port", "product", "basis", "futures_close", "data_status"],
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


def _field_wire(field: str, value: Any):
    value = _wire_value(value)
    if field in _DATETIME_FIELDS and isinstance(value, str):
        return re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", value)
    return value


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
            **{field: _field_wire(field, source.get(field)) for field in fields if field != "row_ref"},
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
    if kind == "trades":
        return ["trade_date", "contract", "direction", "quantity", "price", "fee"]
    if kind == "closes":
        return ["trade_date", "contract", "direction", "quantity", "average_price", "fee", "realized_close_pnl"]
    if kind == "market_series":
        return ["business_date", "port", "product", "basis", "futures_close", "wet_spot_price", "data_status"]
    if kind in _DATASET_RESULT_KINDS:
        if kind == "dataset_rows":
            return list(_DATASET_DEFAULT_FIELDS.get("spot_series", ["row_ref"]))
        if kind == "dataset_summary":
            return ["value", "covered_rows", "eligible_rows", "status"]
        if kind == "dataset_comparison":
            return ["current_date", "previous_date", "current_value", "previous_value", "delta", "delta_pct", "comparison_status"]
        if kind == "dataset_relation":
            return ["left_date", "right_date", "product", "delta", "relation_status"]
    return ["row_ref"]


def _payload(saved: Any) -> dict:
    envelope = getattr(saved, "envelope", None)
    payload = getattr(envelope, "payload", {}) if envelope is not None else {}
    return payload if isinstance(payload, dict) else {}


def allowed_fields_for_saved(saved: Any) -> set[str]:
    """Return the server-owned projection fields for one immutable result kind."""
    payload = _payload(saved)
    kind = payload.get("kind")
    if kind == "dataset_rows":
        dataset = payload.get("dataset")
        if not isinstance(dataset, str):
            return set()
        try:
            return set(dataset_allowed_fields(dataset))
        except ValueError:
            return set()
    if kind in _DERIVED_FIELDS:
        fields = set(_DERIVED_FIELDS[kind])
        fields.update(str(value) for value in payload.get("group_by", []) if isinstance(value, str))
        fields.update(str(value) for value in payload.get("value_fields", []) if isinstance(value, str))
        return fields
    return set(facts.PUBLIC_FIELDS)


def _dataset_fields(saved: Any, fields: list[str]) -> list[str]:
    payload = _payload(saved)
    kind = payload.get("kind")
    if kind == "dataset_rows":
        dataset = payload.get("dataset")
        defaults = _DATASET_DEFAULT_FIELDS.get(dataset, ["row_ref"])
        return [field for field in defaults if field in allowed_fields_for_saved(saved)]
    if kind in _DATASET_RESULT_KINDS:
        allowed = allowed_fields_for_saved(saved)
        return [field for field in _default_fields(kind) if field in allowed]
    return list(fields)


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


def _v2_fallback(reason: str, request: ViewRequest, *, facet_page: int = 1, total_facets: int = 0) -> dict:
    return {
        "version": 2,
        "fallback": "table",
        "reason": reason,
        "layout": request.layout,
        "axis_mode": request.axis_mode,
        "facet_pagination": {
            "page": facet_page, "page_size": 6, "total": total_facets,
            "has_more": facet_page * 6 < total_facets,
        },
        "facets": [],
        "warnings": ["图谱超出安全展示范围，请缩小品种、年份或日期范围。"],
    }


def _date_only(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if isinstance(value, datetime):
        return value.date().isoformat()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _chart_dimension_key(row: dict, fields: list[str], *, separator: str = "|") -> tuple[str, str]:
    values = ["未知" if row.get(field) is None else str(row.get(field)) for field in fields]
    key = separator.join(values) if values else "全部"
    label = " · ".join(values) if values else "全部"
    return key, label


def _chart_x(row: dict, x_field: str, axis_mode: str) -> tuple[Any, str] | None:
    if axis_mode == "business_week":
        value = row.get("business_week") if row.get("business_week") is not None else row.get(x_field)
        try:
            week = int(value)
        except (TypeError, ValueError):
            return None
        if not 1 <= week <= 53:
            return None
        return week, str(week)
    if axis_mode == "month_day":
        observed = _date_only(row.get("observation_date") or row.get("business_date") or row.get(x_field))
        if observed is None:
            return None
        return observed[5:], observed[5:]
    observed = _date_only(row.get(x_field) or row.get("observation_date") or row.get("business_date"))
    if observed is None:
        return None
    return observed, observed


def _dataset_measure_field(saved: Any, request: ViewRequest, fields: list[str], x_field: str) -> str | None:
    excluded = {x_field, *request.series_by, *request.facet_by}
    candidates = [field for field in fields if field not in excluded and field not in {"unit", "value_state", "data_status"}]
    preferred = ["value", "delta", "basis", "current_value", "standardized_spot_price", "wet_spot_price", "futures_close"]
    for field in preferred:
        if field in candidates:
            return field
    return candidates[0] if len(candidates) == 1 else None


def build_dataset_chart(saved: Any, request: ViewRequest, *, facet_page: int = 1, facet_page_size: int = 6) -> dict:
    """Build the bounded multi-series chart DTO for a dataset snapshot.

    All keys and values are derived from fields already present in the immutable
    snapshot.  This function never samples rows or invents missing observations.
    """
    if not isinstance(request, ViewRequest):
        request = ViewRequest.model_validate(request)
    if facet_page < 1 or facet_page_size != 6:
        raise ValueError("图谱分页无效")
    payload = _payload(saved)
    kind = payload.get("kind")
    if kind not in _DATASET_RESULT_KINDS:
        raise ValueError("图谱仅支持数据集结果")
    allowed = allowed_fields_for_saved(saved)
    fields = list(request.fields) or _dataset_fields(saved, [])
    if any(field not in allowed for field in fields):
        raise ValueError("字段不在当前数据集目录中")
    controls = [request.x_field, *request.series_by, *request.facet_by]
    if any(field is not None and field not in allowed for field in controls):
        raise ValueError("图谱维度不在当前数据集目录中")
    if len(set(request.series_by)) != len(request.series_by) or len(set(request.facet_by)) != len(request.facet_by):
        raise ValueError("图谱维度重复")
    x_field = request.x_field or next((field for field in fields if field in {"observation_date", "business_date", "period_start", "week_start", "business_week"}), None)
    if x_field is None:
        raise ValueError("图谱缺少横轴字段")
    y_field = _dataset_measure_field(saved, request, fields, x_field)
    if y_field is None or y_field not in allowed:
        raise ValueError("图谱缺少数值字段")

    groups: dict[str, dict[str, Any]] = {}
    units: set[str | None] = set()
    for row in saved.rows or []:
        if not isinstance(row, dict):
            raise ValueError("图谱行格式无效")
        x = _chart_x(row, x_field, request.axis_mode)
        if x is None:
            return _v2_fallback("missing_x", request, facet_page=facet_page)
        facet_key, facet_title = _chart_dimension_key(row, request.facet_by)
        series_key, series_label = _chart_dimension_key(row, request.series_by)
        unit = row.get("unit") if y_field == "value" and row.get("unit") is not None else _column(y_field).get("unit") or payload.get("unit")
        units.add(unit)
        facet = groups.setdefault(facet_key, {"key": facet_key, "title": facet_title, "unit": unit, "series": {}})
        if facet.get("unit") is None and unit is not None:
            facet["unit"] = unit
        series = facet["series"].setdefault(series_key, {
            "key": series_key,
            "label": series_label,
            "color_key": str(row.get("business_year")) if "business_year" in request.series_by else series_key,
            "points": [],
            "_x_seen": set(),
        })
        if x[0] in series["_x_seen"]:
            return _v2_fallback("duplicate_point", request, facet_page=facet_page, total_facets=len(groups))
        series["_x_seen"].add(x[0])
        raw_value = row.get(y_field)
        if raw_value is None:
            value = None
        else:
            value = str(_wire_value(raw_value))
            if _decimal(value) is None:
                return _v2_fallback("invalid_value", request, facet_page=facet_page, total_facets=len(groups))
        series["points"].append({
            "x": x[0], "y": value,
            "observation_date": _date_only(row.get("observation_date") or row.get("business_date") or row.get(x_field)),
            "row_ref": _row_ref(row, len(series["points"])),
            "_sort": (x[0], _date_only(row.get("observation_date") or row.get("business_date")) or ""),
        })

    if len(units) > 1:
        return _v2_fallback("unit_mismatch", request, facet_page=facet_page, total_facets=len(groups))
    ordered_facets = sorted(groups.values(), key=lambda item: str(item["key"]))
    total_facets = len(ordered_facets)
    start = (facet_page - 1) * facet_page_size
    visible_facets = ordered_facets[start:start + facet_page_size]
    if not visible_facets and total_facets:
        raise ValueError("图谱分页超出范围")
    total_points = 0
    response_facets = []
    for facet in visible_facets:
        series_values = sorted(facet["series"].values(), key=lambda item: str(item["key"]))
        if len(series_values) > 18:
            return _v2_fallback("series_limit", request, facet_page=facet_page, total_facets=total_facets)
        response_series = []
        for series in series_values:
            points = sorted(series["points"], key=lambda point: point["_sort"])
            if len(points) > 366:
                return _v2_fallback("series_point_limit", request, facet_page=facet_page, total_facets=total_facets)
            total_points += len(points)
            response_series.append({key: value for key, value in series.items() if not key.startswith("_")})
        response_facets.append({
            "key": facet["key"], "title": facet["title"], "unit": facet["unit"], "series": response_series,
        })
    chart = {
        "version": 2,
        "kind": request.kind,
        "layout": request.layout,
        "axis_mode": request.axis_mode,
        "x_field": x_field,
        "y_field": y_field,
        "facet_pagination": {
            "page": facet_page, "page_size": facet_page_size, "total": total_facets,
            "has_more": start + facet_page_size < total_facets,
        },
        "facets": response_facets,
        "warnings": ["缺失值保留为断点；图中横轴标签不反推观察日期。"],
    }
    if total_points > 8192:
        return _v2_fallback("point_limit", request, facet_page=facet_page, total_facets=total_facets)
    if len(json.dumps(chart, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 2 * 1024 * 1024:
        return _v2_fallback("payload_limit", request, facet_page=facet_page, total_facets=total_facets)
    return chart


def build_matrix(saved: Any, request: ViewRequest, *, matrix_column_page: int = 1) -> dict:
    """Build a deterministic, paginated matrix while retaining the long table."""
    if matrix_column_page < 1:
        raise ValueError("矩阵分页无效")
    payload = _payload(saved)
    if payload.get("kind") not in _DATASET_RESULT_KINDS:
        raise ValueError("矩阵仅支持数据集结果")
    allowed = allowed_fields_for_saved(saved)
    row_dimensions = list(request.facet_by)
    column_dimensions = list(request.series_by)
    if not row_dimensions or not column_dimensions or set(row_dimensions) & set(column_dimensions):
        raise ValueError("矩阵需要两个不重复的登记维度")
    if len(row_dimensions) != len(set(row_dimensions)) or len(column_dimensions) != len(set(column_dimensions)):
        raise ValueError("矩阵维度重复")
    if any(field not in allowed for field in row_dimensions + column_dimensions):
        raise ValueError("矩阵维度不在当前数据集目录中")
    candidates = [field for field in request.fields if field not in row_dimensions + column_dimensions]
    value_field = next((field for field in ("delta", "value", "basis", "current_value") if field in candidates), None)
    value_field = value_field or (candidates[0] if len(candidates) == 1 else None)
    if value_field is None or value_field not in allowed:
        raise ValueError("矩阵缺少数值字段")
    row_groups: dict[tuple, dict] = {}
    column_values: dict[tuple, dict] = {}
    cells: dict[tuple[tuple, tuple], dict] = {}
    for index, row in enumerate(saved.rows or []):
        if not isinstance(row, dict):
            raise ValueError("矩阵行格式无效")
        row_key = tuple(row.get(field) for field in row_dimensions)
        column_key = tuple(row.get(field) for field in column_dimensions)
        row_groups.setdefault(row_key, {field: row.get(field) for field in row_dimensions})
        column_values.setdefault(column_key, {field: row.get(field) for field in column_dimensions})
        identity = (row_key, column_key)
        if identity in cells:
            raise ValueError("duplicate_matrix_cell")
        cells[identity] = {
            "value": _field_wire(value_field, row.get(value_field)),
            "source_row_ref": _row_ref(row, index),
        }
    ordered_columns = sorted(column_values, key=lambda key: tuple(str(value) for value in key))
    total = len(ordered_columns)
    start = (matrix_column_page - 1) * 12
    selected = ordered_columns[start:start + 12]
    columns = []
    for index, key in enumerate(selected, start=start + 1):
        values = ["未知" if value is None else str(value) for value in key]
        columns.append({"key": f"c{index}", "label": " · ".join(values), "unit": _column(value_field).get("unit") or payload.get("unit")})
    matrix_rows = []
    for row_key in sorted(row_groups, key=lambda key: tuple(str(value) for value in key)):
        dimensions = row_groups[row_key]
        row_cells = {}
        for index, column_key in enumerate(selected, start=start + 1):
            cell = cells.get((row_key, column_key))
            if cell is not None:
                row_cells[f"c{index}"] = cell
        matrix_rows.append({"dimensions": dimensions, "cells": row_cells})
    return {
        "row_dimensions": row_dimensions,
        "columns": columns,
        "rows": matrix_rows,
        "column_pagination": {"page": matrix_column_page, "page_size": 12, "total": total, "has_more": start + 12 < total},
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
    payload = envelope.payload if hasattr(envelope, "payload") and isinstance(envelope.payload, dict) else {}
    if kind in _DATASET_RESULT_KINDS:
        dates = sorted({value for row in rows for value in [
            _date_only(row.get("observation_date") or row.get("business_date") or row.get("period_start"))
        ] if value})
        if kind == "dataset_comparison":
            coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
            return {
                "row_count": len(rows), "eligible_rows": len(rows), "matched_rows": coverage.get("matched_rows", 0),
                "missing_previous": coverage.get("missing_previous", 0), "unit": payload.get("unit"),
                "data_period": payload.get("periods") or None,
            }
        if kind == "dataset_relation":
            coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
            return {
                "row_count": len(rows), "matched_rows": coverage.get("matched_rows", len(rows)),
                "unmatched_rows": coverage.get("unmatched_rows", 0), "unit": payload.get("unit"),
            }
        value_field = payload.get("measure") or "value"
        covered_rows = sum(1 for row in rows if row.get(value_field) is not None and row.get("value_state", "observed") not in {"missing", "invalid"})
        units = sorted({str(row.get("unit")) for row in rows if row.get("unit") not in (None, "")})
        return {
            "row_count": len(rows), "covered_rows": covered_rows, "eligible_rows": len(rows),
            "data_period": {"start": dates[0], "end": dates[-1]} if dates else None,
            "unit": units[0] if len(units) == 1 else payload.get("unit"), "units": units,
        }
    for field in fields:
        metric = metrics.get(field) if hasattr(metrics, "get") else None
        if kind != "market_series" and metric is None and field not in {"quantity", "floating_pnl", "realized_close_pnl", "fee"}:
            continue
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
    if kind in _DATASET_RESULT_KINDS:
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        dates = sorted({value for row in rows for value in [
            _date_only(row.get("observation_date") or row.get("business_date") or row.get("period_start"))
        ] if value})
        covered_rows = sum(1 for row in rows if any(
            row.get(field) is not None for field in ("value", "delta", "basis", "current_value", "inventory_value")
        ))
        result = {
            "eligible_rows": eligible_rows, "covered_rows": covered_rows,
            "data_period_start": dates[0] if dates else None, "data_period_end": dates[-1] if dates else None,
            "units": sorted({str(row.get("unit")) for row in rows if row.get("unit") not in (None, "")}),
        }
        if kind == "dataset_comparison":
            result.update(payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {})
        if kind == "dataset_relation":
            result.update(payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {})
        return result
    quantities = [(row, _decimal(row.get("quantity"))) for row in rows]
    eligible_quantity = sum((value for _, value in quantities if value is not None), Decimal(0))
    if kind == "positions":
        covered_rows = sum(1 for row in rows if row.get("valuation_price") is not None)
        covered = [value for row, value in quantities if row.get("valuation_price") is not None and value is not None]
        covered_contract_keys = {
            row.get("contract")
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
            row.get("contract")
            for row in rows if row.get("contract") is not None
        }
    eligible_contract_keys = {
        row.get("contract")
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
    facet_page: int = 1,
    matrix_column_page: int = 1,
    saved: Any = None,
) -> dict:
    """Build one immutable view descriptor from a saved result snapshot."""
    if not isinstance(request, ViewRequest):
        request = ViewRequest.model_validate(request)
    saved = saved or store_api.load_result(principal, request.result_ref)
    payload = saved.envelope.payload if isinstance(saved.envelope.payload, dict) else {}
    kind = payload.get("kind", "positions")
    allowed = allowed_fields_for_saved(saved)
    fields = list(request.fields) or (_dataset_fields(saved, []) if kind in _DATASET_RESULT_KINDS else _default_fields(kind))
    if any(field not in allowed for field in fields):
        raise ValueError("字段不在当前结果目录中")
    if request.sort_by is not None and request.sort_by not in fields:
        raise ValueError("排序字段无效")
    controls = [request.x_field, *request.series_by, *request.facet_by]
    if any(field is not None and field not in allowed for field in controls):
        raise ValueError("展示维度不在当前结果目录中")
    if request.x_field is not None and request.x_field not in fields and request.layout == "standard":
        raise ValueError("横轴字段必须在展示字段中")
    if request.layout == "matrix" and request.kind != "table":
        raise ValueError("矩阵必须使用表格视图")
    if request.layout != "standard" and kind not in _DATASET_RESULT_KINDS:
        raise ValueError("新布局仅支持数据集结果")
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
    if request.layout == "matrix":
        result["matrix"] = build_matrix(saved, request, matrix_column_page=matrix_column_page)
    elif request.kind in {"bar", "line"}:
        use_dataset_chart = kind in _DATASET_RESULT_KINDS and (
            request.layout != "standard" or request.x_field is not None or request.series_by or request.facet_by
        )
        chart = build_dataset_chart(saved, request, facet_page=facet_page) if use_dataset_chart else build_chart_series({
            "kind": request.kind, "fields": fields, "columns": result["columns"],
            "rows": [_clean_projected(item) for item in projected],
        })
        result["chart"] = chart
        if chart.get("fallback"):
            result["warnings"] = list(dict.fromkeys(result["warnings"] + [
                "图表数据不满足展示限制，已保留完整数据表。"
            ]))
    return result


__all__ = [
    "allowed_fields_for_saved", "build_chart_series", "build_dataset_chart", "build_matrix",
    "build_view", "project_page",
]
