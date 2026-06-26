"""
数据可视化管理 — 卡粉 MVP。
Excel 解析、业务周计算、表需计算、导入预检/确认、数据查询与编辑、图表数据。
"""
from __future__ import annotations

import base64
import io
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from . import db

router = APIRouter()

# ── 常量 ──────────────────────────────────────────────────────────────
PRODUCT = "卡粉"
DV_PRODUCTS = ["卡粉", "纽曼粉", "麦克粉", "PB粉", "金布巴粉", "超特粉", "混合粉", "杨迪粉", "罗伊山粉", "巴西粗粉", "乌克兰精粉", "俄罗斯精粉", "IOC6"]
SHIPMENT_SHEET = "发运"
INVENTORY_SHEET = "卡粉库存"
SHIPMENT_DATE_COL = 2   # B 列
SHIPMENT_VAL_COL = 51   # AY 列
INVENTORY_DATE_COL = 1  # A 列
INVENTORY_VAL_COL = 2   # B 列
LOCAL_MYSTEEL_DIR = Path("/Users/wangjingze/建龙/期货组/本地数据库")


_INTEGRATED_FIELDS = {
    "统计周一": "week_start", "统计周日": "week_end",
    "业务年份": "business_year", "业务周次": "business_week",
    "周次标签": "week_label", "展示日期": "display_date",
    "数据类型": "metric_type", "来源/国家": "source_country",
    "品种": "product", "种类": "category",
    "主流/非主流": "mainstream_status", "数值": "value",
    "单位": "unit", "来源文件": "source_file",
    "来源Sheet": "source_sheet", "来源区域": "source_section",
    "是否参与表需": "is_calculable", "校验状态": "validation_status",
    "备注": "note",
}

_METRIC_TYPE_CN = {
    "库存": "inventory", "发运": "shipment", "发运/到港": "shipment",
    "到港": "arrival", "表需": "apparent_demand",
}
_VALID_INTEGRATED_METRIC_TYPES = {"inventory", "shipment", "arrival", "apparent_demand"}

_REQUIRED_INTEGRATED_FIELDS = [
    "week_start", "display_date", "metric_type",
    "source_country", "product", "category", "mainstream_status",
]

class ImportRequest(BaseModel):
    file_data: str
    file_name: str
    overwrite_manual_ids: List[int] = []


class ManualEditRequest(BaseModel):
    data_point_id: int
    new_value: float


class IntegrationUploadFile(BaseModel):
    file_name: str
    file_data: str


class IntegrationFilesRequest(BaseModel):
    files: List[IntegrationUploadFile]

# ── 权限（自包含，避免循环导入 main.py）───────────────────────────────


async def dv_current_user(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def dv_require_edit(module_code: str, user: dict):
    if user["role"] == "管理员":
        return
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT can_edit FROM module_permissions WHERE user_id = ? AND module_code = ?",
            (user["id"], module_code),
        ).fetchone()
    if not row or not row["can_edit"]:
        raise HTTPException(status_code=403, detail="没有编辑权限")


# ── 工具函数 ──────────────────────────────────────────────────────────


def _to_date(val: Any) -> Optional[date]:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return None


def _to_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_end(d: date) -> date:
    return _week_start(d) + timedelta(days=6)


def _parse_period_start(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        left = str(value).split("-")[0].strip()
        return datetime.strptime(left, "%Y/%m/%d").date()
    except (ValueError, TypeError):
        return None


def _clean_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


MAINSTREAM_PRODUCTS = {
    "PB粉", "纽曼粉", "麦克粉", "金布巴粉",
    "巴混", "卡粉", "超特粉", "混合粉",
    "PB块", "纽曼块", "澳大利亚球团",
}

AUSTRALIA_ARRIVAL_HEADER_ROW = 20
AUSTRALIA_ARRIVAL_START_ROW = 21
AUSTRALIA_ARRIVAL_END_ROW = 27
BRAZIL_DESTINATION_HEADER_ROW = 39
BRAZIL_DESTINATION_START_ROW = 40
BRAZIL_DESTINATION_END_ROW = 45


AUSTRALIA_PRODUCT_MAP: Dict[str, Optional[tuple]] = {
    "PB粉": ("PB粉", "粉矿", "粗粉:PB粉"),
    "PB块": ("PB块", "块矿", "块矿:PB块"),
    "杨迪粉": ("杨迪粉", "粉矿", "粗粉:杨迪粉"),
    "大杨迪": ("杨迪粉", "粉矿", "粗粉:杨迪粉"),
    "混合粉": ("混合粉", "粉矿", "粗粉:混合粉"),
    "纽曼块": ("纽曼块", "块矿", "块矿:纽曼块"),
    "超特粉": ("超特粉", "粉矿", "粗粉:超特粉"),
    "纽曼粉": ("纽曼粉", "粉矿", "粗粉:纽曼粉"),
    "麦克粉": ("麦克粉", "粉矿", "粗粉:麦克粉"),
    "金布巴粉": ("金布巴粉", "粉矿", "粗粉:金布巴粉"),
    "罗布河粉": ("罗布河粉", "粉矿", "粗粉:罗布河粉"),
    "国王粉": ("国王粉", "粉矿", "粗粉:国王粉"),
    "Roy Hill粉": ("罗伊山粉", "粉矿", "粗粉:罗伊山粉"),
    "ATLAS粉": ("Atlas粉", "粉矿", "粗粉:Atlas粉"),
    "罗布河块": ("罗布河块", "块矿", "块矿:罗布河块"),
    "卡拉拉粉": ("卡拉拉精粉", "精粉", "精粉:卡拉拉精粉"),
    "Roy Hill块": ("罗伊山块", "块矿", "块矿:罗伊山块"),
    "球团": ("澳大利亚球团", "球团", "球团:澳大利亚"),
    "FMG 块矿": ("FMG块", "块矿", "块矿:FMG块"),
    "西皮尔巴拉粉": ("西皮尔巴拉粉", "粉矿", "粗粉:西皮尔巴拉粉"),
    "SP10粉": ("SP10粉", "粉矿", "粗粉:RTX粉(SP10粉)"),
    "SP10块": ("SP10块", "块矿", "块矿:SP10块"),
    "库宾块": ("库宾块", "块矿", "块矿:库宾块"),
    "库兰精粉": ("库兰粉", "粉矿", "粗粉:库兰粉"),
    "一刚粉": ("一钢粉", "粉矿", "粗粉:一钢粉"),
    "一刚块": ("一钢块", "块矿", "块矿:一钢块"),
    "中信泰富精粉": ("泰富精粉", "精粉", "精粉:泰富精粉"),
    "铁桥精粉": ("铁桥精粉", "精粉", "精粉:铁桥精粉"),
    "丝路粉": ("丝路粉", "粉矿", "粗粉:丝路粉"),
    "RTX": None,
    "RTX块": None,
    "高锰粉矿": None,
    "未知": None,
    "库宾粉": None,
}


INVENTORY_RENAME = {
    ("粗粉", "RTX粉(SP10粉)"): ("SP10粉", "粉矿", "澳洲"),
    ("粗粉", "库兰粉"): ("库兰粉", "粉矿", "澳洲"),
    ("粗粉", "一钢粉"): ("一钢粉", "粉矿", "澳洲"),
    ("精粉", "泰富精粉"): ("泰富精粉", "精粉", "澳洲"),
    ("球团", "澳大利亚"): ("澳大利亚球团", "球团", "澳洲"),
}


GLOBAL_SKIP_COUNTRIES = {"澳大利亚", "巴西", "总计"}


def _category_for_inventory(sheet_name: str, header: str) -> str:
    if sheet_name == "粗粉":
        return "粉矿"
    if sheet_name == "块矿":
        return "块矿"
    if sheet_name == "球团":
        return "球团"
    if sheet_name == "精粉":
        return "精粉"
    if "块" in header:
        return "块矿"
    if "球" in header:
        return "球团"
    if "精粉" in header:
        return "精粉"
    return "粉矿"


def _source_country_for_product(product: str, category: str, sheet_name: str = "") -> str:
    australia_tokens = (
        "PB", "纽曼", "麦克", "金布巴", "超特", "混合", "杨迪", "罗布河",
        "国王", "罗伊山", "Atlas", "ATLAS", "FMG", "SP10", "库宾",
        "库兰", "一钢", "泰富", "铁桥", "丝路", "卡拉拉", "西皮尔巴拉", "澳大利亚",
    )
    brazil_tokens = ("卡粉", "巴混", "巴粗", "CSN", "托克", "米纳斯")
    if any(token in product for token in australia_tokens):
        return "澳洲"
    if any(token in product for token in brazil_tokens):
        return "巴西"
    return product if sheet_name in {"球团", "精粉"} and len(product) <= 8 else "其他"


def _mainstream_status(product: str) -> str:
    return "主流" if product in MAINSTREAM_PRODUCTS else "非主流"


def _make_point(
    *,
    week_start: date,
    display_date: date,
    metric_type: str,
    source_country: str,
    product: str,
    category: str,
    value: float,
    source_file: str,
    source_sheet: str,
    source_section: str,
    is_calculable: bool,
    validation_status: str = "ok",
    note: str = "",
) -> Dict[str, Any]:
    business_week = compute_business_week(week_start)
    week_end = _week_end(week_start)
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "business_year": business_week["year"],
        "business_week": business_week["week_no"],
        "week_label": f"{business_week['year']} W{business_week['week_no']:02d}",
        "display_date": display_date.isoformat(),
        "metric_type": metric_type,
        "source_country": source_country,
        "product": product,
        "category": category,
        "mainstream_status": _mainstream_status(product),
        "value": value,
        "unit": "万吨",
        "source_file": source_file,
        "source_sheet": source_sheet,
        "source_section": source_section,
        "is_calculable": 1 if is_calculable else 0,
        "validation_status": validation_status,
        "note": note,
    }


def _find_col(ws, header_row: int, header_name: str) -> Optional[int]:
    for col in range(1, ws.max_column + 1):
        if str(ws.cell(header_row, col).value or "").strip() == header_name:
            return col
    return None


def _iter_total_rows(ws) -> List[int]:
    return [row for row in range(1, ws.max_row + 1) if ws.cell(row, 2).value == "总计"]


def _extract_australia_arrivals(path: Path) -> List[Dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    if "澳洲预计到达中国锚地量" not in wb.sheetnames:
        wb.close()
        return []
    ws = wb["澳洲预计到达中国锚地量"]
    points: List[Dict[str, Any]] = []
    headers = {col: str(ws.cell(AUSTRALIA_ARRIVAL_HEADER_ROW, col).value or "").strip() for col in range(2, ws.max_column + 1)}
    for row in range(AUSTRALIA_ARRIVAL_START_ROW, AUSTRALIA_ARRIVAL_END_ROW + 1):
        period_start = _parse_period_start(ws.cell(row, 1).value)
        if period_start is None:
            continue
        for col, raw_product in headers.items():
            if not raw_product or raw_product == "总计":
                continue
            mapping = AUSTRALIA_PRODUCT_MAP.get(raw_product)
            if mapping is None:
                continue
            value = _clean_number(ws.cell(row, col).value)
            if value is None:
                continue
            product, category, _inventory_key = mapping
            points.append(_make_point(
                week_start=period_start,
                display_date=period_start,
                metric_type="arrival",
                source_country="澳洲",
                product=product,
                category=category,
                value=value,
                source_file=path.name,
                source_sheet="澳洲预计到达中国锚地量",
                source_section="预计到中国锚地量（品种）",
                is_calculable=True,
                note="澳洲直接采用预计到中国锚地量，归类为到港",
            ))
    wb.close()
    return points


def _extract_brazil_estimated_arrivals(path: Path) -> List[Dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    if "巴西发货量" not in wb.sheetnames:
        wb.close()
        return []
    ws = wb["巴西发货量"]
    china_col = _find_col(ws, BRAZIL_DESTINATION_HEADER_ROW, "中国大陆")
    points: List[Dict[str, Any]] = []
    if china_col is None:
        wb.close()
        return points
    for row in range(BRAZIL_DESTINATION_START_ROW, BRAZIL_DESTINATION_END_ROW + 1):
        period_start = _parse_period_start(ws.cell(row, 1).value)
        value = _clean_number(ws.cell(row, china_col).value)
        if period_start is None or value is None:
            continue
        arrival_week = period_start + timedelta(weeks=6)
        points.append(_make_point(
            week_start=arrival_week,
            display_date=arrival_week,
            metric_type="arrival",
            source_country="巴西",
            product="卡粉",
            category="粉矿",
            value=value * 0.75,
            source_file=path.name,
            source_sheet="巴西发货量",
            source_section="中国大陆发运量×75%（6周后到港估算）",
            is_calculable=True,
            note="巴西卡粉按中国大陆发运量的75%折算到港，滞后6周",
        ))
    wb.close()
    return points


def _extract_global_shipments(path: Path) -> List[Dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    if "全球铁矿石发运量" not in wb.sheetnames:
        wb.close()
        return []
    ws = wb["全球铁矿石发运量"]
    points: List[Dict[str, Any]] = []
    headers = {col: str(ws.cell(3, col).value or "").strip() for col in range(2, ws.max_column + 1)}
    for row in range(4, 10):
        period_start = _parse_period_start(ws.cell(row, 1).value)
        if period_start is None:
            continue
        for col, country in headers.items():
            if not country or country in GLOBAL_SKIP_COUNTRIES:
                continue
            value = _clean_number(ws.cell(row, col).value)
            if value is None:
                continue
            points.append(_make_point(
                week_start=period_start,
                display_date=period_start,
                metric_type="shipment",
                source_country=country,
                product=country,
                category="全品种",
                value=value,
                source_file=path.name,
                source_sheet="全球铁矿石发运量",
                source_section="铁矿石全球发运量",
                is_calculable=False,
                validation_status="record_only",
                note="非澳巴全球发运只记录，不参与表需计算",
            ))
    wb.close()
    return points


def _inventory_product(sheet_name: str, header: str) -> Optional[tuple]:
    if not header or "总计" in header:
        return None
    renamed = INVENTORY_RENAME.get((sheet_name, header))
    if renamed:
        return renamed
    category = _category_for_inventory(sheet_name, header)
    source_country = _source_country_for_product(header, category, sheet_name)
    return header, category, source_country


def _extract_inventory(path: Path) -> List[Dict[str, Any]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    points: List[Dict[str, Any]] = []
    for sheet_name in ["粗粉", "块矿", "球团", "精粉"]:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        headers = {col: str(ws.cell(1, col).value or "").strip() for col in range(4, ws.max_column + 1)}
        for row in _iter_total_rows(ws):
            raw_date = _to_date(ws.cell(row, 1).value)
            if raw_date is None:
                continue
            week_start = _week_start(raw_date)
            for col, header in headers.items():
                product_info = _inventory_product(sheet_name, header)
                if product_info is None:
                    continue
                value = _clean_number(ws.cell(row, col).value)
                if value is None:
                    continue
                product, category, source_country = product_info
                points.append(_make_point(
                    week_start=week_start,
                    display_date=raw_date,
                    metric_type="inventory",
                    source_country=source_country,
                    product=product,
                    category=category,
                    value=value,
                    source_file=path.name,
                    source_sheet=sheet_name,
                    source_section="总计行",
                    is_calculable=source_country in {"澳洲", "巴西"},
                    validation_status="ok",
                ))
    wb.close()
    return points


def _summarize_points(points: List[Dict[str, Any]], warnings: List[str]) -> Dict[str, Any]:
    metrics: Dict[str, int] = {}
    products: set = set()
    weeks: set = set()
    for point in points:
        metrics[point["metric_type"]] = metrics.get(point["metric_type"], 0) + 1
        products.add(point["product"])
        weeks.add(point["week_start"])
    return {
        "total_points": len(points),
        "metrics": metrics,
        "product_count": len(products),
        "week_count": len(weeks),
        "warnings": warnings,
        "samples": points[:20],
    }


def _add_apparent_demand(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = list(points)
    inv: Dict[tuple, Dict[str, Any]] = {}
    ship: Dict[tuple, Dict[str, Any]] = {}
    for point in points:
        key = (point["week_start"], point["source_country"], point["product"], point["category"])
        if point["metric_type"] == "inventory":
            inv[key] = point
        elif point["metric_type"] == "arrival" and point["is_calculable"]:
            ship[key] = point

    product_keys = sorted({(p["source_country"], p["product"], p["category"]) for p in points if p["is_calculable"]})
    for source_country, product, category in product_keys:
        week_starts = sorted({
            p["week_start"] for p in points
            if p["source_country"] == source_country and p["product"] == product and p["category"] == category
        })
        for index, week_start in enumerate(week_starts):
            cur_key = (week_start, source_country, product, category)
            if cur_key not in ship or cur_key not in inv or index == 0:
                continue
            prev_week = week_starts[index - 1]
            prev_key = (prev_week, source_country, product, category)
            if prev_key not in inv:
                continue
            shipment_value = float(ship[cur_key]["value"] or 0)
            inv_prev = float(inv[prev_key]["value"] or 0)
            inv_cur = float(inv[cur_key]["value"] or 0)
            value = shipment_value + inv_prev - inv_cur
            base = ship[cur_key]
            result.append(_make_point(
                week_start=date.fromisoformat(week_start),
                display_date=date.fromisoformat(week_start),
                metric_type="apparent_demand",
                source_country=source_country,
                product=product,
                category=category,
                value=value,
                source_file=base["source_file"],
                source_sheet="系统计算",
                source_section="表需",
                is_calculable=True,
                validation_status="ok",
                note="表需=到港/估算到港+上周库存-本周库存",
            ))
    return result


def integrate_mysteel_files(file_paths: List[Path]) -> Dict[str, Any]:
    points: List[Dict[str, Any]] = []
    warnings: List[str] = []
    used = {"australia": False, "brazil": False, "global": False, "inventory": False}
    for path in file_paths:
        if not path.exists():
            warnings.append(f"文件不存在: {path.name}")
            continue
        australia = _extract_australia_arrivals(path)
        brazil = _extract_brazil_estimated_arrivals(path)
        global_points = _extract_global_shipments(path)
        inventory = _extract_inventory(path)
        if australia:
            used["australia"] = True
        if brazil:
            used["brazil"] = True
        if global_points:
            used["global"] = True
        if inventory:
            used["inventory"] = True
        points.extend(australia)
        points.extend(brazil)
        points.extend(global_points)
        points.extend(inventory)
    for key, label in [
        ("australia", "澳洲到港"),
        ("brazil", "巴西中国大陆发运"),
        ("global", "全球其他国家发运"),
        ("inventory", "库存明细"),
    ]:
        if not used[key]:
            warnings.append(f"未识别到{label}数据")
    points = _add_apparent_demand(points)
    return {"points": points, "summary": _summarize_points(points, warnings)}


def _local_mysteel_files() -> List[Path]:
    return sorted(LOCAL_MYSTEEL_DIR.glob("Mysteel*.xlsx"))


def _write_uploads_to_tmp(files: List[IntegrationUploadFile]) -> List[Path]:
    paths: List[Path] = []
    for item in files:
        suffix = ".xlsx" if item.file_name.lower().endswith(".xlsx") else ".xls"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(base64.b64decode(item.file_data))
            paths.append(Path(tmp.name))
    return paths


def _cleanup_tmp(paths: List[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def _split_filter_values(value: str) -> List[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_integrated_excel(file_path):
    import openpyxl
    wb = openpyxl.load_workbook(file_path, data_only=True)
    sheet_name = '整合明细'
    if sheet_name not in wb.sheetnames:
        wb.close()
        return {'rows': [], 'errors': [{'row': 0, 'message': f'未找到「{sheet_name}」sheet'}], 'summary': {}}

    ws = wb[sheet_name]
    headers = []
    for col in range(1, ws.max_column + 1):
        hval = ws.cell(row=1, column=col).value
        headers.append(str(hval).strip() if hval is not None else '')

    col_map = {}
    for idx, header in enumerate(headers):
        field = _INTEGRATED_FIELDS.get(header)
        if field:
            col_map[idx + 1] = field

    if not col_map:
        wb.close()
        return {'rows': [], 'errors': [{'row': 0, 'message': '整合明细表头无法识别'}], 'summary': {}}

    rows = []
    errors = []
    seen_keys = set()
    duplicate_key_count = 0
    for row_idx in range(2, ws.max_row + 1):
        row_data = {}
        value_parse_error = False
        for col_idx, field in col_map.items():
            raw = ws.cell(row=row_idx, column=col_idx).value
            if field == 'business_year':
                try:
                    row_data[field] = int(raw) if raw is not None else None
                except (ValueError, TypeError):
                    row_data[field] = None
            elif field == 'business_week':
                try:
                    row_data[field] = int(raw) if raw is not None else None
                except (ValueError, TypeError):
                    row_data[field] = None
            elif field == 'is_calculable':
                val = str(raw).strip() if raw is not None else ''
                row_data[field] = 1 if val == '是' else 0
            elif field == 'value':
                try:
                    row_data[field] = float(raw) if raw is not None else None
                except (ValueError, TypeError):
                    row_data[field] = None
                    value_parse_error = True
            elif field in ('week_start', 'week_end', 'display_date'):
                if isinstance(raw, (__import__('datetime').date, __import__('datetime').datetime)):
                    if hasattr(raw, 'isoformat'):
                        row_data[field] = raw.isoformat()
                    else:
                        row_data[field] = raw.strftime('%Y-%m-%d')
                else:
                    row_data[field] = str(raw).strip() if raw else ''
            elif field == 'metric_type':
                val = str(raw).strip() if raw else ''
                row_data[field] = _METRIC_TYPE_CN.get(val, val)
            else:
                row_data[field] = str(raw).strip() if raw else ''

        missing = [f for f in _REQUIRED_INTEGRATED_FIELDS if not row_data.get(f)]
        if missing:
            errors.append({'row': row_idx, 'message': f'缺少必填字段: {", ".join(missing)}'})
            continue
        if row_data.get('metric_type') not in _VALID_INTEGRATED_METRIC_TYPES:
            errors.append({'row': row_idx, 'message': f'数据类型无效: {row_data.get("metric_type")}'})
            continue
        if value_parse_error or row_data.get('value') is None:
            errors.append({'row': row_idx, 'message': '数值不是数字'})
            continue
        business_key = (
            row_data.get('week_start'),
            row_data.get('metric_type'),
            row_data.get('source_country'),
            row_data.get('product'),
            row_data.get('category'),
        )
        if business_key in seen_keys:
            duplicate_key_count += 1
        else:
            seen_keys.add(business_key)
        rows.append(row_data)

    summary = _summarize_integrated_rows(rows)
    summary['duplicate_key_count'] = duplicate_key_count
    wb.close()
    return {'rows': rows, 'errors': errors, 'summary': summary}


def _summarize_integrated_rows(rows):
    if not rows:
        return {
            'total_points': 0, 'inventory_count': 0, 'shipment_count': 0,
            'arrival_count': 0, 'apparent_demand_count': 0, 'product_count': 0, 'category_count': 0,
            'country_count': 0, 'week_count': 0, 'null_count': 0,
            'date_min': '', 'date_max': '', 'years': [],
        }
    products = set()
    categories = set()
    countries = set()
    weeks = set()
    metric_counts = {'inventory': 0, 'shipment': 0, 'arrival': 0, 'apparent_demand': 0}
    null_count = 0
    dates = []
    for row in rows:
        products.add(row.get('product', ''))
        categories.add(row.get('category', ''))
        countries.add(row.get('source_country', ''))
        weeks.add(row.get('week_start', ''))
        mt = row.get('metric_type', '')
        if mt in metric_counts:
            metric_counts[mt] += 1
        if row.get('value') is None:
            null_count += 1
        d = row.get('display_date', '')
        if d:
            dates.append(d)
    years = sorted(set(w[:4] for w in weeks if w and len(w) >= 4))
    return {
        'total_points': len(rows),
        'inventory_count': metric_counts['inventory'],
        'shipment_count': metric_counts['shipment'],
        'arrival_count': metric_counts['arrival'],
        'apparent_demand_count': metric_counts['apparent_demand'],
        'product_count': len([p for p in products if p]),
        'category_count': len([c for c in categories if c]),
        'country_count': len([c for c in countries if c]),
        'week_count': len([w for w in weeks if w]),
        'null_count': null_count,
        'date_min': min(dates) if dates else '',
        'date_max': max(dates) if dates else '',
        'years': years,
    }



def _import_integrated_points(rows, file_name, user_name):
    """Replace integrated data with the uploaded standard Excel in one batch."""
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "DELETE FROM dv_integrated_points")
        db._exec(cur, "DELETE FROM dv_integration_batches")
        batch_id = db._last_insert_id(
            cur,
            """INSERT INTO dv_integration_batches
               (file_names, status, point_count, apparent_demand_count, validation_summary, created_by)
               VALUES (?, 'committed', ?, ?, ?, ?)""",
            (
                file_name,
                len(rows),
                sum(1 for r in rows if r.get('metric_type') == 'apparent_demand'),
                json.dumps({"source": "integrated_import", "inserted": len(rows)}, ensure_ascii=False),
                user_name,
            ),
        )
        values = [
            (
                batch_id,
                row['week_start'],
                row.get('week_end', ''),
                row.get('business_year'),
                row.get('business_week'),
                row.get('week_label', ''),
                row.get('display_date', ''),
                row['metric_type'],
                row['source_country'],
                row['product'],
                row['category'],
                row.get('mainstream_status', ''),
                row.get('value'),
                row.get('unit', '万吨'),
                row.get('source_file', ''),
                row.get('source_sheet', ''),
                row.get('source_section', ''),
                row.get('is_calculable', 0),
                row.get('validation_status', ''),
                row.get('note', ''),
            )
            for row in rows
        ]
        insert_sql = """INSERT INTO dv_integrated_points
           (batch_id, week_start, week_end, business_year, business_week,
            week_label, display_date, metric_type, source_country, product,
            category, mainstream_status, value, unit, source_file,
            source_sheet, source_section, is_calculable, validation_status, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        if values:
            if db._is_pg():
                from psycopg2.extras import execute_values
                execute_values(
                    cur,
                    insert_sql.replace("?", "%s").replace(
                        "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        "%s",
                    ),
                    values,
                    page_size=1000,
                )
            else:
                db._executemany(cur, insert_sql, values)
        conn.commit()
    return batch_id


def _save_integrated_points(points: List[Dict[str, Any]], file_names: List[str], user_name: str) -> int:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "DELETE FROM dv_integrated_points")
        db._exec(cur, "DELETE FROM dv_integration_batches")
        batch_id = db._last_insert_id(
            cur,
            """INSERT INTO dv_integration_batches
               (file_names, status, point_count, apparent_demand_count, validation_summary, created_by)
               VALUES (?, 'completed', ?, ?, ?, ?)""",
            (
                ", ".join(file_names),
                len(points),
                sum(1 for point in points if point["metric_type"] == "apparent_demand"),
                json.dumps({"source": "mysteel"}, ensure_ascii=False),
                user_name,
            ),
        )
        for point in points:
            db._exec(
                cur,
                """INSERT INTO dv_integrated_points
                   (batch_id, week_start, week_end, business_year, business_week, week_label,
                    display_date, metric_type, source_country, product,
                    category, mainstream_status, value, unit, source_file, source_sheet,
                    source_section, is_calculable, validation_status, note)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch_id,
                    point["week_start"],
                    point["week_end"],
                    point["business_year"],
                    point["business_week"],
                    point["week_label"],
                    point["display_date"],
                    point["metric_type"],
                    point["source_country"],
                    point["product"],
                    point["category"],
                    point["mainstream_status"],
                    point["value"],
                    point["unit"],
                    point["source_file"],
                    point["source_sheet"],
                    point["source_section"],
                    point["is_calculable"],
                    point["validation_status"],
                    point["note"],
                ),
            )
    return batch_id


INTEGRATED_EXPORT_COLUMNS = [
    ("week_start", "统计周一"),
    ("week_end", "统计周日"),
    ("business_year", "业务年份"),
    ("business_week", "业务周次"),
    ("week_label", "周次标签"),
    ("display_date", "展示日期"),
    ("metric_type", "数据类型"),
    ("source_country", "来源/国家"),
    ("product", "品种"),
    ("category", "种类"),
    ("mainstream_status", "主流/非主流"),
    ("value", "数值"),
    ("unit", "单位"),
    ("source_file", "来源文件"),
    ("source_sheet", "来源Sheet"),
    ("source_section", "来源区域"),
    ("is_calculable", "是否参与表需"),
    ("validation_status", "校验状态"),
    ("note", "备注"),
]

METRIC_SHEETS = [
    ("shipment", "发运"),
    ("arrival", "到港"),
    ("inventory", "库存"),
    ("apparent_demand", "表需"),
]


def _metric_label(metric: str) -> str:
    if metric == "inventory":
        return "库存"
    if metric == "shipment":
        return "发运"
    if metric == "arrival":
        return "到港"
    if metric == "apparent_demand":
        return "表需"
    return metric


def _export_cell_value(item: Dict[str, Any], key: str) -> Any:
    if key == "metric_type":
        return _metric_label(item[key])
    if key == "is_calculable":
        return "是" if item[key] else "否"
    return item[key]


def _ensure_week_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    if item.get("business_year") and item.get("business_week") and item.get("week_label") and item.get("week_end"):
        return item
    week_start_value = item.get("week_start")
    if week_start_value:
        week_start_date = date.fromisoformat(week_start_value)
        business_week = compute_business_week(week_start_date)
        item["week_end"] = item.get("week_end") or _week_end(week_start_date).isoformat()
        item["business_year"] = item.get("business_year") or business_week["year"]
        item["business_week"] = item.get("business_week") or business_week["week_no"]
        item["week_label"] = item.get("week_label") or f"{business_week['year']} W{business_week['week_no']:02d}"
    return item


def _append_integrated_rows(sheet, rows: List[Any], include_metric_type: bool = True) -> None:
    columns = INTEGRATED_EXPORT_COLUMNS if include_metric_type else [
        (key, label) for key, label in INTEGRATED_EXPORT_COLUMNS if key != "metric_type"
    ]
    sheet.append([label for _key, label in columns])
    for row in rows:
        item = _ensure_week_fields(_row_to_dict(row))
        sheet.append([_export_cell_value(item, key) for key, _label in columns])


def build_integrated_workbook_bytes() -> bytes:
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    with db.connect() as conn:
        cur = conn.cursor()
        batch = db._exec(
            cur,
            "SELECT * FROM dv_integration_batches ORDER BY created_at DESC, id DESC LIMIT 1",
        ).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="暂无可导出的整合结果")
        rows = db._exec(
            cur,
            """SELECT *
               FROM dv_integrated_points
               WHERE batch_id = ?
               ORDER BY week_start, metric_type, source_country, category, product, id""",
            (batch["id"],),
        ).fetchall()
        metrics = db._exec(
            cur,
            """SELECT metric_type, COUNT(*) AS c
               FROM dv_integrated_points
               WHERE batch_id = ?
               GROUP BY metric_type""",
            (batch["id"],),
        ).fetchall()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "整合明细"
    _append_integrated_rows(ws, rows, include_metric_type=True)

    metric_sheets = []
    for metric_type, sheet_name in METRIC_SHEETS:
        sheet = wb.create_sheet(sheet_name)
        metric_rows = [row for row in rows if row["metric_type"] == metric_type]
        _append_integrated_rows(sheet, metric_rows, include_metric_type=False)
        metric_sheets.append(sheet)

    info = wb.create_sheet("批次信息")
    metric_counts = {row["metric_type"]: row["c"] for row in metrics}
    info_rows = [
        ("批次ID", batch["id"]),
        ("来源文件", batch["file_names"]),
        ("状态", batch["status"]),
        ("数据点总数", batch["point_count"]),
        ("库存条数", metric_counts.get("inventory", 0)),
        ("发运条数", metric_counts.get("shipment", 0)),
        ("到港条数", metric_counts.get("arrival", 0)),
        ("表需条数", metric_counts.get("apparent_demand", 0)),
        ("创建人", batch["created_by"]),
        ("创建时间", batch["created_at"]),
    ]
    for item in info_rows:
        info.append(item)

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    for sheet in [ws, *metric_sheets, info]:
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
        sheet.freeze_panes = "A2"
        for col_idx, column_cells in enumerate(sheet.columns, start=1):
            max_len = max(len(str(cell.value or "")) for cell in column_cells)
            sheet.column_dimensions[get_column_letter(col_idx)].width = min(max(max_len + 2, 10), 36)

    output = io.BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


# ── 业务周计算 ────────────────────────────────────────────────────────
# 规则：周一为起点；1 月 1 日所在周为第 1 周


def compute_business_week(d: date) -> Dict[str, Any]:
    """计算业务周：周一起始，1月1日所在周为 W01。

    修正跨年规则：若该周包含下一年的 1 月 1 日，则归属下一年 W01。
    例：2024-12-30 → 2025 W01；2022-12-26 → 2023 W01。
    """
    weekday = d.weekday()  # 0=Mon … 6=Sun
    week_start = d - timedelta(days=weekday)
    week_end = week_start + timedelta(days=6)

    jan1 = date(d.year, 1, 1)
    jan1_weekday = jan1.weekday()
    jan1_week_start = jan1 - timedelta(days=jan1_weekday)

    if week_start < jan1_week_start:
        prev_jan1 = date(d.year - 1, 1, 1)
        prev_jan1_weekday = prev_jan1.weekday()
        prev_jan1_week_start = prev_jan1 - timedelta(days=prev_jan1_weekday)
        week_no = ((week_start - prev_jan1_week_start).days // 7) + 1
        result = {
            "year": d.year - 1,
            "week_no": week_no,
            "week_start_date": week_start.isoformat(),
            "week_end_date": week_end.isoformat(),
        }
    else:
        week_no = ((week_start - jan1_week_start).days // 7) + 1
        result = {
            "year": d.year,
            "week_no": week_no,
            "week_start_date": week_start.isoformat(),
            "week_end_date": week_end.isoformat(),
        }

    # 跨年修正：若该周包含下一年的 1 月 1 日，则归属下一年 W01
    next_jan1 = date(d.year + 1, 1, 1)
    if week_end >= next_jan1:
        result["year"] = d.year + 1
        result["week_no"] = 1

    return result

# ── Excel 解析 ────────────────────────────────────────────────────────


def parse_excel(file_path: str) -> Dict[str, List[Dict]]:
    """读取发运 sheet 和卡粉库存 sheet，返回 {shipment: [...], inventory: [...]}。"""
    import openpyxl

    wb = openpyxl.load_workbook(file_path, data_only=True)
    result: Dict[str, List[Dict]] = {"shipment": [], "inventory": []}

    if SHIPMENT_SHEET in wb.sheetnames:
        ws = wb[SHIPMENT_SHEET]
        for row_idx in range(2, ws.max_row + 1):
            d = _to_date(ws.cell(row=row_idx, column=SHIPMENT_DATE_COL).value)
            v = _to_float(ws.cell(row=row_idx, column=SHIPMENT_VAL_COL).value)
            if d:
                result["shipment"].append({"date": d.isoformat(), "value": v})

    if INVENTORY_SHEET in wb.sheetnames:
        ws = wb[INVENTORY_SHEET]
        for row_idx in range(2, ws.max_row + 1):
            d = _to_date(ws.cell(row=row_idx, column=INVENTORY_DATE_COL).value)
            v = _to_float(ws.cell(row=row_idx, column=INVENTORY_VAL_COL).value)
            if d:
                result["inventory"].append({"date": d.isoformat(), "value": v})

    wb.close()
    return result


# ── 周匹配合并 ────────────────────────────────────────────────────────
# 同周且日期差 ≤ 4 天 → 共享 week_key


def _upsert_week_key(cur, year: int, week_no: int, week_start_date: str,
                     week_end_date: str, shipment_date: Optional[str],
                     inventory_date: Optional[str], display_date: str) -> int:
    sd = shipment_date or ""
    id_ = inventory_date or ""
    row = db._exec(
        cur,
        """SELECT id FROM dv_week_keys
           WHERE year = ? AND week_no = ? AND shipment_date = ? AND inventory_date = ?""",
        (year, week_no, sd, id_),
    ).fetchone()
    if row:
        return row["id"]
    return db._last_insert_id(
        cur,
        """INSERT INTO dv_week_keys
           (year, week_no, week_start_date, week_end_date, shipment_date, inventory_date, display_date)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (year, week_no, week_start_date, week_end_date, sd, id_, display_date),
    )


def match_and_merge_weeks(parsed: Dict[str, List[Dict]], conn) -> Dict[str, Any]:
    shipments = parsed["shipment"]
    inventories = parsed["inventory"]

    for row in shipments:
        row["_bw"] = compute_business_week(date.fromisoformat(row["date"]))
        row["_date_obj"] = date.fromisoformat(row["date"])
    for row in inventories:
        row["_bw"] = compute_business_week(date.fromisoformat(row["date"]))

    week_key_map: Dict[str, int] = {}
    week_keys: List[Dict] = []
    pairs: List[Dict] = []
    bw_wk_map: Dict[tuple, int] = {}  # (year, week_no) -> week_key_id, 防同 ISO 周重复
    cur = conn.cursor()

    used_shipment: set = set()
    used_inventory: set = set()

    for si, s_row in enumerate(shipments):
        if si in used_shipment:
            continue
        best_match = _find_nearest_inventory(s_row, inventories, used_inventory, max_gap_days=4)
        if best_match is not None:
            ii, i_row = best_match
            used_shipment.add(si)
            used_inventory.add(ii)
            s_bw = s_row["_bw"]
            s_date = s_row["_date_obj"]
            i_date = date.fromisoformat(i_row["date"])
            key_str = f"{s_bw['year']}_{s_bw['week_no']}_{s_row['date']}_{i_row['date']}"
            wk_id = week_key_map.get(key_str)
            if wk_id is None:
                # 合并周使用发运的 ISO 周作为 week_no（发运是财务报告周基准）
                wk_id = _upsert_week_key(
                    cur, s_bw["year"], s_bw["week_no"],
                    s_bw["week_start_date"], s_bw["week_end_date"],
                    s_row["date"], i_row["date"], s_row["date"],
                )
                week_key_map[key_str] = wk_id
                week_keys.append({
                    "id": wk_id, "year": s_bw["year"], "week_no": s_bw["week_no"],
                    "display_date": s_row["date"], "shipment_date": s_row["date"],
                    "inventory_date": i_row["date"],
                })
                bw_wk_map[(s_bw["year"], s_bw["week_no"])] = wk_id
            pairs.append({"shipment_row": s_row, "inventory_row": i_row, "week_key_id": wk_id})

    for si, s_row in enumerate(shipments):
        if si in used_shipment:
            continue
        s_bw = s_row["_bw"]
        key_str = f"{s_bw['year']}_{s_bw['week_no']}_{s_row['date']}_none"
        wk_id = week_key_map.get(key_str)
        if wk_id is None:
            # 已有合并周覆盖了此 ISO 周，复用其 week_key_id 避免重复
            existing_wk = bw_wk_map.get((s_bw["year"], s_bw["week_no"]))
            if existing_wk is not None:
                continue
            wk_id = _upsert_week_key(
                cur, s_bw["year"], s_bw["week_no"],
                s_bw["week_start_date"], s_bw["week_end_date"],
                s_row["date"], None, s_row["date"],
            )
            week_key_map[key_str] = wk_id
            week_keys.append({
                "id": wk_id, "year": s_bw["year"], "week_no": s_bw["week_no"],
                "display_date": s_row["date"], "shipment_date": s_row["date"],
                "inventory_date": None,
            })
        pairs.append({"shipment_row": s_row, "inventory_row": None, "week_key_id": wk_id})

    for ii, i_row in enumerate(inventories):
        if ii in used_inventory:
            continue
        i_bw = i_row["_bw"]
        key_str = f"{i_bw['year']}_{i_bw['week_no']}_none_{i_row['date']}"
        wk_id = week_key_map.get(key_str)
        if wk_id is None:
            # 已有合并周覆盖了此 ISO 周，复用其 week_key_id 避免重复
            existing_wk = bw_wk_map.get((i_bw["year"], i_bw["week_no"]))
            if existing_wk is not None:
                continue
            wk_id = _upsert_week_key(
                cur, i_bw["year"], i_bw["week_no"],
                i_bw["week_start_date"], i_bw["week_end_date"],
                None, i_row["date"], i_row["date"],
            )
            week_key_map[key_str] = wk_id
            week_keys.append({
                "id": wk_id, "year": i_bw["year"], "week_no": i_bw["week_no"],
                "display_date": i_row["date"], "shipment_date": None,
                "inventory_date": i_row["date"],
            })
        pairs.append({"shipment_row": None, "inventory_row": i_row, "week_key_id": wk_id})

    conn.commit()
    return {"week_keys": week_keys, "pairs": pairs}


def _find_nearest_inventory(s_row, inventories, used_inventory, max_gap_days=4):
    """在未匹配库存中找到最接近发运日期的记录，不超过 max_gap_days 天。

    优先匹配发运日期之后 0~max_gap_days 天内的库存（常规 周日→周二=2天）；
    若无，取之前最近的。
    返回 (inventory_index, inventory_row) 或 None。
    """
    s_date = date.fromisoformat(s_row["date"])
    best_after = None   # (idx, row, gap)
    best_before = None  # (idx, row, gap)

    for ii, i_row in enumerate(inventories):
        if ii in used_inventory:
            continue
        i_date = date.fromisoformat(i_row["date"])
        gap = (i_date - s_date).days
        if abs(gap) <= max_gap_days:
            if gap >= 0 and (best_after is None or gap < best_after[2]):
                best_after = (ii, i_row, gap)
            elif gap < 0 and (best_before is None or abs(gap) < best_before[2]):
                best_before = (ii, i_row, abs(gap))

    if best_after:
        return (best_after[0], best_after[1])
    if best_before:
        return (best_before[0], best_before[1])
    return None


# ── 表需重算 ──────────────────────────────────────────────────────────
# 表需(t) = 发运(t-2) + 库存(t-1) - 库存(t)


def recalc_apparent_demand(conn) -> None:
    """重新计算所有表需数据点。

    表需(t) = 发运(t-2) + 库存(t-1) - 库存(t)

    使用 (year, week_no) 二维索引替代位置偏移，避免 weeks 数组中
    合并周/纯发运周/纯库存周交错排列导致的「上一周」指错行问题。
    """
    cur = conn.cursor()
    weeks = db._exec(
        cur,
        """SELECT wk.id, wk.year, wk.week_no, wk.display_date
           FROM dv_week_keys wk ORDER BY wk.display_date""",
    ).fetchall()
    if not weeks:
        return

    all_points = db._exec(
        cur,
        """SELECT id, week_key_id, metric_type, display_value, is_manual_override
           FROM dv_data_points
           WHERE product = ? AND metric_type IN ('shipment','inventory','apparent_demand')""",
        (PRODUCT,),
    ).fetchall()

    # week_key_id -> (year, week_no)
    week_id_info: Dict[int, tuple] = {wk["id"]: (wk["year"], wk["week_no"]) for wk in weeks}

    # 按 (year, week_no) 索引数据点，每种指标独立
    metric_by_week: Dict[str, Dict[tuple, dict]] = {
        "shipment": {}, "inventory": {}, "apparent_demand": {},
    }
    for pt in all_points:
        key = week_id_info.get(pt["week_key_id"])
        if key is not None:
            metric_by_week[pt["metric_type"]][key] = dict(pt)

    sorted_ship_keys: List[tuple] = sorted(metric_by_week["shipment"].keys())
    sorted_inv_keys: List[tuple] = sorted(metric_by_week["inventory"].keys())

    def _nth_prev_in_sorted(cur: tuple, sorted_keys: List[tuple], n: int = 1) -> tuple | None:
        """返回 sorted_keys 中小于 cur 的第 n 个 key（跳过缺口）。"""
        import bisect
        idx = bisect.bisect_left(sorted_keys, cur)
        target = idx - n
        return sorted_keys[target] if target >= 0 else None

    for wk in weeks:
        wid = wk["id"]
        cur_key = (wk["year"], wk["week_no"])
        ad_pt = metric_by_week["apparent_demand"].get(cur_key, {})
        if ad_pt.get("is_manual_override"):
            continue

        # 按排序位置查找前驱数据点（跳过 ISO 周号缺口）
        sp_key = _nth_prev_in_sorted(cur_key, sorted_ship_keys, 2)
        shipment_val = float(metric_by_week["shipment"][sp_key]["display_value"]) if sp_key else 0.0

        ip_key = _nth_prev_in_sorted(cur_key, sorted_inv_keys, 1)
        inv_prev = float(metric_by_week["inventory"][ip_key]["display_value"]) if ip_key else 0.0

        ip_t = metric_by_week["inventory"].get(cur_key)
        inv_t = float(ip_t["display_value"]) if (ip_t and ip_t.get("display_value") is not None) else 0.0

        demand = shipment_val + inv_prev - inv_t

        # 缺少任一输入数据点则标记为缺失
        is_missing = sp_key is None or ip_key is None or ip_t is None

        if not ad_pt:
            db._exec(
                cur,
                """INSERT INTO dv_data_points
                   (week_key_id, product, metric_type, imported_value, calculated_value,
                    manual_value, display_value, is_manual_override, is_missing_filled,
                    source, created_at)
                   VALUES (?, ?, 'apparent_demand', NULL, ?, NULL, ?, 0, ?, '自动计算', CURRENT_TIMESTAMP)""",
                (wid, PRODUCT, demand, demand, 1 if is_missing else 0),
            )
        else:
            db._exec(
                cur,
                """UPDATE dv_data_points
                   SET calculated_value = ?, display_value = ?, is_missing_filled = ?,
                       source = '自动计算', updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (demand, demand, 1 if is_missing else 0, ad_pt["id"]),
            )
def _now_expr() -> str:
    """返回 cross-db 的当前时间表达式。"""
    return "CURRENT_TIMESTAMP"



# ── GET /api/data-visualization/years ──────────────────────────────────


@router.get("/data-visualization/integration/local-preview")
async def integration_local_preview(user=Depends(dv_current_user)):
    file_paths = _local_mysteel_files()
    result = integrate_mysteel_files(file_paths)
    return {
        "files": [path.name for path in file_paths],
        "summary": result["summary"],
    }


@router.post("/data-visualization/integration/local-commit")
async def integration_local_commit(user=Depends(dv_current_user)):
    dv_require_edit("data_visualization_integration", user)
    file_paths = _local_mysteel_files()
    result = integrate_mysteel_files(file_paths)
    batch_id = _save_integrated_points(result["points"], [path.name for path in file_paths], user["name"])
    return {
        "ok": True,
        "batch_id": batch_id,
        "files": [path.name for path in file_paths],
        "summary": result["summary"],
    }


@router.post("/data-visualization/integration/preview")
async def integration_upload_preview(payload: IntegrationFilesRequest, user=Depends(dv_current_user)):
    tmp_paths = _write_uploads_to_tmp(payload.files)
    try:
        result = integrate_mysteel_files(tmp_paths)
        source_names = {path.name: item.file_name for path, item in zip(tmp_paths, payload.files)}
        for point in result["summary"]["samples"]:
            point["source_file"] = source_names.get(point["source_file"], point["source_file"])
    finally:
        _cleanup_tmp(tmp_paths)
    return {
        "files": [item.file_name for item in payload.files],
        "summary": result["summary"],
    }


@router.post("/data-visualization/integration/commit")
async def integration_upload_commit(payload: IntegrationFilesRequest, user=Depends(dv_current_user)):
    dv_require_edit("data_visualization_integration", user)
    tmp_paths = _write_uploads_to_tmp(payload.files)
    try:
        result = integrate_mysteel_files(tmp_paths)
        source_names = {path.name: item.file_name for path, item in zip(tmp_paths, payload.files)}
        for point in result["points"]:
            point["source_file"] = source_names.get(point["source_file"], point["source_file"])
        for point in result["summary"]["samples"]:
            point["source_file"] = source_names.get(point["source_file"], point["source_file"])
        batch_id = _save_integrated_points(result["points"], [item.file_name for item in payload.files], user["name"])
    finally:
        _cleanup_tmp(tmp_paths)
    return {
        "ok": True,
        "batch_id": batch_id,
        "files": [item.file_name for item in payload.files],
        "summary": result["summary"],
    }


@router.get("/data-visualization/integration/latest")
async def integration_latest(user=Depends(dv_current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        batch = db._exec(
            cur,
            "SELECT * FROM dv_integration_batches ORDER BY created_at DESC, id DESC LIMIT 1",
        ).fetchone()
        rows = db._exec(
            cur,
            """SELECT metric_type, COUNT(*) AS c
               FROM dv_integrated_points GROUP BY metric_type ORDER BY metric_type""",
        ).fetchall()
    return {
        "batch": _row_to_dict(batch),
        "metrics": [_row_to_dict(row) for row in rows],
    }


@router.get("/data-visualization/integration/export")
async def integration_export(user=Depends(dv_current_user)):
    content = build_integrated_workbook_bytes()
    filename = f"iron_ore_integrated_{date.today().isoformat()}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/data-visualization/years")
async def get_years(user=Depends(dv_current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        integrated_rows = db._exec(
            cur,
            "SELECT DISTINCT SUBSTR(week_start, 1, 4) AS year FROM dv_integrated_points ORDER BY year"
        ).fetchall()
        if integrated_rows:
            return {"years": [int(r["year"]) for r in integrated_rows if r["year"]]}
        rows = db._exec(
            cur,
            "SELECT DISTINCT year FROM dv_week_keys ORDER BY year"
        ).fetchall()
    return {"years": [r["year"] for r in rows]}


# ── POST /api/data-visualization/import/preview ───────────────────────

@router.get("/data-visualization/filters")
async def get_filters(user=Depends(dv_current_user)):
    """返回整合结果中可用的筛选选项。"""
    with db.connect() as conn:
        cur = conn.cursor()
        integrated_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_integrated_points").fetchone()["c"]
        if integrated_count:
            products = [r["val"] for r in db._exec(cur,
                "SELECT DISTINCT product AS val FROM dv_integrated_points ORDER BY val").fetchall()]
            categories = [r["val"] for r in db._exec(cur,
                "SELECT DISTINCT category AS val FROM dv_integrated_points ORDER BY val").fetchall()]
            countries = [r["val"] for r in db._exec(cur,
                "SELECT DISTINCT source_country AS val FROM dv_integrated_points ORDER BY val").fetchall()]
            mainstreams = [r["val"] for r in db._exec(cur,
                "SELECT DISTINCT mainstream_status AS val FROM dv_integrated_points ORDER BY val").fetchall()]
            mainstream_products = [r["val"] for r in db._exec(cur,
                """SELECT DISTINCT product AS val FROM dv_integrated_points
                   WHERE mainstream_status = '主流' ORDER BY val""").fetchall()]
            non_mainstream_products = [r["val"] for r in db._exec(cur,
                """SELECT DISTINCT product AS val FROM dv_integrated_points
                   WHERE mainstream_status = '非主流' ORDER BY val""").fetchall()]
            years = [r["year"] for r in db._exec(cur,
                "SELECT DISTINCT business_year AS year FROM dv_integrated_points ORDER BY year").fetchall() if r["year"]]
            return {
                "products": products,
                "categories": categories,
                "source_countries": countries,
                "mainstream_statuses": mainstreams,
                "years": years,
                "product_pools": {
                    "mainstream": mainstream_products,
                    "non_mainstream": non_mainstream_products,
                    "aggregate": ["主流矿合计", "非主流矿合计"],
                    "custom": products,
                },
            }
        # fallback to old dv_data_points
        products = [r["product"] for r in db._exec(cur,
            "SELECT DISTINCT product FROM dv_data_points ORDER BY product").fetchall()]
        years = [r["year"] for r in db._exec(cur,
            "SELECT DISTINCT year FROM dv_week_keys ORDER BY year").fetchall()]
        return {
            "products": products or list(DV_PRODUCTS),
            "categories": [],
            "source_countries": [],
            "mainstream_statuses": [],
            "years": years,
            "product_pools": {
                "mainstream": products or list(DV_PRODUCTS),
                "non_mainstream": [],
                "aggregate": [],
                "custom": products or list(DV_PRODUCTS),
            },
        }


# ── POST /api/data-visualization/import/integrated/preview ─────────────
@router.post('/data-visualization/import/integrated/preview')

async def import_integrated_preview(
    payload: ImportRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit('data_visualization_data', user)

    import base64
    file_bytes = base64.b64decode(payload.file_data)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = _parse_integrated_excel(tmp_path)
    finally:
        os.unlink(tmp_path)

    return {
        'file_name': payload.file_name,
        'summary': result['summary'],
        'errors': result['errors'],
        'sample_count': min(len(result['rows']), 20),
        'sample_rows': result['rows'][:20],
    }


# ── POST /api/data-visualization/import/integrated/commit ──────────────

@router.post('/data-visualization/import/integrated/commit')
async def import_integrated_commit(
    payload: ImportRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit('data_visualization_data', user)

    import base64
    file_bytes = base64.b64decode(payload.file_data)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = _parse_integrated_excel(tmp_path)
    finally:
        os.unlink(tmp_path)

    rows = result['rows']
    if result['errors']:
        raise HTTPException(status_code=400, detail=f'整合 Excel 存在 {len(result["errors"])} 条错误，无法导入')

    batch_id = _import_integrated_points(rows, payload.file_name, user['name'])
    return {
        'batch_id': batch_id,
        'summary': result['summary'],
        'message': f'已导入 {len(rows)} 条数据',
    }

# ── POST /api/data-visualization/import/preview ───────────────────────
@router.post("/data-visualization/import/preview")

async def import_preview(
    payload: ImportRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit("data_visualization_data", user)

    import base64
    file_bytes = base64.b64decode(payload.file_data)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        parsed = parse_excel(tmp_path)
    finally:
        os.unlink(tmp_path)

    shipments = parsed["shipment"]
    inventories = parsed["inventory"]
    all_dates = sorted(set([r["date"] for r in shipments] + [r["date"] for r in inventories]))

    null_count = sum(1 for r in shipments if r["value"] is None) + sum(
        1 for r in inventories if r["value"] is None
    )

    with db.connect() as conn:
        merged = match_and_merge_weeks(parsed, conn)
        cur = conn.cursor()

        manual_protected = []
        history_changes = []
        for pair in merged["pairs"]:
            wk_id = pair["week_key_id"]
            for metric_type, row_key in [("shipment", "shipment_row"), ("inventory", "inventory_row")]:
                row = pair.get(row_key)
                if row is None or row["value"] is None:
                    continue
                existing = db._exec(
                    cur,
                    """SELECT id, display_value, is_manual_override
                       FROM dv_data_points
                       WHERE week_key_id = ? AND product = ? AND metric_type = ?""",
                    (wk_id, PRODUCT, metric_type),
                ).fetchone()
                if existing:
                    if existing["is_manual_override"]:
                        manual_protected.append({
                            "data_point_id": existing["id"],
                            "metric_type": metric_type,
                            "week_key_id": wk_id,
                            "date": row["date"],
                            "current_value": existing["display_value"],
                            "new_value": row["value"],
                        })
                    elif existing["display_value"] != row["value"]:
                        history_changes.append({
                            "metric_type": metric_type,
                            "week_key_id": wk_id,
                            "date": row["date"],
                            "current_value": existing["display_value"],
                            "new_value": row["value"],
                            "is_manual_protected": False,
                        })

    insert_count = sum(
        1 for p in merged["pairs"]
        if (p.get("shipment_row") and p["shipment_row"]["value"] is not None)
        or (p.get("inventory_row") and p["inventory_row"]["value"] is not None)
    )

    return {
        "file_name": payload.file_name,
        "metric_types": "inventory,shipment",
        "date_start": all_dates[0] if all_dates else "",
        "date_end": all_dates[-1] if all_dates else "",
        "total_rows": len(shipments) + len(inventories),
        "insert_count": insert_count,
        "overwrite_count": len(history_changes),
        "null_count": null_count,
        "error_count": 0,
        "manual_protected_count": len(manual_protected),
        "anomalies": [],
        "history_changes": history_changes,
        "manual_protected": manual_protected,
    }


# ── POST /api/data-visualization/import/commit ────────────────────────

@router.post("/data-visualization/import/commit")
async def import_commit(
    payload: ImportRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit("data_visualization_data", user)

    import base64
    file_bytes = base64.b64decode(payload.file_data)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        parsed = parse_excel(tmp_path)
    finally:
        os.unlink(tmp_path)

    overwrite_ids = set(payload.overwrite_manual_ids)
    stats = {"insert_count": 0, "overwrite_count": 0, "error_count": 0, "manual_protected_count": 0}
    user_name = user["name"]

    with db.connect() as conn:
        cur = conn.cursor()
        merged = match_and_merge_weeks(parsed, conn)

        all_dates = sorted(
            set([r["date"] for r in parsed["shipment"]] + [r["date"] for r in parsed["inventory"]])
        )
        batch_id = db._last_insert_id(
            cur,
            """INSERT INTO dv_import_batches
               (file_name, metric_types, date_start, date_end, status, created_by)
               VALUES (?, 'inventory,shipment', ?, ?, 'processing', ?)""",
            (payload.file_name, all_dates[0] if all_dates else "",
             all_dates[-1] if all_dates else "", user_name),
        )

        for pair in merged["pairs"]:
            wk_id = pair["week_key_id"]
            for metric_type, row_key in [("shipment", "shipment_row"), ("inventory", "inventory_row")]:
                row = pair.get(row_key)
                if row is None or row["value"] is None:
                    continue
                value = row["value"]

                existing = db._exec(
                    cur,
                    """SELECT id, display_value, is_manual_override
                       FROM dv_data_points
                       WHERE week_key_id = ? AND product = ? AND metric_type = ?""",
                    (wk_id, PRODUCT, metric_type),
                ).fetchone()

                if existing:
                    dp_id = existing["id"]
                    if existing["is_manual_override"]:
                        if dp_id in overwrite_ids:
                            db._exec(
                                cur,
                                """UPDATE dv_data_points
                                   SET imported_value = ?, display_value = ?, is_manual_override = 0,
                                       manual_value = NULL, source = 'Excel导入覆盖人工修正',
                                       source_batch_id = ?, updated_by = ?
                                   WHERE id = ?""",
                                (value, value, batch_id, user_name, dp_id),
                            )
                            stats["overwrite_count"] += 1
                        else:
                            stats["manual_protected_count"] += 1
                            continue
                    else:
                        old_val = existing["display_value"]
                        db._exec(
                            cur,
                            """UPDATE dv_data_points
                               SET imported_value = ?, display_value = ?, source = '导入',
                                   source_batch_id = ?, updated_by = ?
                               WHERE id = ?""",
                            (value, value, batch_id, user_name, dp_id),
                        )
                        if old_val != value:
                            stats["overwrite_count"] += 1
                else:
                    db._last_insert_id(
                        cur,
                        """INSERT INTO dv_data_points
                           (week_key_id, product, metric_type, imported_value, display_value,
                            is_manual_override, is_missing_filled, source, source_batch_id, created_by)
                           VALUES (?, ?, ?, ?, ?, 0, 0, '导入', ?, ?)""",
                        (wk_id, PRODUCT, metric_type, value, value, batch_id, user_name),
                    )
                    stats["insert_count"] += 1

        db._exec(
            cur,
            """UPDATE dv_import_batches
               SET insert_count = ?, overwrite_count = ?, manual_protected_count = ?,
                   status = 'completed'
               WHERE id = ?""",
            (stats["insert_count"], stats["overwrite_count"], stats["manual_protected_count"], batch_id),
        )

        recalc_apparent_demand(conn)

    return {"ok": True, "batch_id": batch_id, "stats": stats}
# ── GET /api/data-visualization/table ─────────────────────────────────

@router.get("/data-visualization/table")
async def get_table(
    metric: str = Query(..., pattern="^(inventory|shipment|arrival|apparent_demand)$"),
    years: str = "",
    products: str = "",
    categories: str = "",
    source_countries: str = "",
    mainstream_status: str = "",
    product_pool: str = "",
    user=Depends(dv_current_user),
):
    year_list: List[int] = []
    years_empty_requested = False
    if years:
        for part in years.split(","):
            part = part.strip()
            if part:
                if part == "__EMPTY__":
                    years_empty_requested = True
                    continue
                try:
                    year_list.append(int(part))
                except ValueError:
                    pass

    product_list = _split_filter_values(products) if products else []
    category_list = _split_filter_values(categories) if categories else []
    country_list = _split_filter_values(source_countries) if source_countries else []
    mainstream_list = _split_filter_values(mainstream_status) if mainstream_status else []
    empty_filter_requested = years_empty_requested or "__EMPTY__" in product_list or "__EMPTY__" in category_list or "__EMPTY__" in country_list or "__EMPTY__" in mainstream_list
    product_list = [item for item in product_list if item != "__EMPTY__"]
    category_list = [item for item in category_list if item != "__EMPTY__"]
    country_list = [item for item in country_list if item != "__EMPTY__"]
    mainstream_list = [item for item in mainstream_list if item != "__EMPTY__"]

    with db.connect() as conn:
        cur = conn.cursor()
        integrated_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_integrated_points").fetchone()["c"]
        if integrated_count:
            if empty_filter_requested:
                return {"metric": metric, "products": [], "data": []}
            sql = """SELECT week_start, week_label, business_year, business_week,
                            display_date, source_country, product, category,
                            mainstream_status, value, is_calculable, validation_status, note
                     FROM dv_integrated_points WHERE metric_type = ?"""
            params: List[Any] = [metric]
            if year_list:
                placeholders = ",".join("?" for _ in year_list)
                sql += f" AND business_year IN ({placeholders})"
                params.extend(year_list)
            if product_list:
                placeholders = ",".join("?" for _ in product_list)
                sql += f" AND product IN ({placeholders})"
                params.extend(product_list)
            if category_list:
                placeholders = ",".join("?" for _ in category_list)
                sql += f" AND category IN ({placeholders})"
                params.extend(category_list)
            if country_list:
                placeholders = ",".join("?" for _ in country_list)
                sql += f" AND source_country IN ({placeholders})"
                params.extend(country_list)
            if mainstream_list:
                placeholders = ",".join("?" for _ in mainstream_list)
                sql += f" AND mainstream_status IN ({placeholders})"
                params.extend(mainstream_list)
            sql += " ORDER BY week_start, product, category"
            rows = db._exec(cur, sql, tuple(params)).fetchall()

            if product_pool == "aggregate":
                products_ordered = ["主流矿合计", "非主流矿合计"]
                week_map: Dict[str, Dict] = {}
                for row in rows:
                    ws = row["week_start"]
                    if ws not in week_map:
                        week_map[ws] = {
                            "date": row["display_date"],
                            "week": row["week_label"] or f"{row['business_year']} W{row['business_week']:02d}",
                        }
                        for p in products_ordered:
                            week_map[ws][p] = {
                                "id": None, "value": None,
                                "is_manual_override": False, "is_missing_filled": False,
                                "source": None, "updated_by": None, "updated_at": None,
                            }
                    status = row["mainstream_status"] or "非主流"
                    label = "主流矿合计" if status == "主流" else "非主流矿合计"
                    current = week_map[ws][label]["value"] or 0
                    week_map[ws][label] = {
                        "id": None,
                        "value": current + float(row["value"] or 0),
                        "is_manual_override": False,
                        "is_missing_filled": False,
                        "source": "整合数据汇总",
                        "updated_by": row["validation_status"],
                        "updated_at": None,
                    }
                return {"metric": metric, "products": products_ordered, "data": list(week_map.values())}

            # 按 (product, category) 构建稳定标签
            product_categories: Dict[str, set] = {}
            for row in rows:
                product_categories.setdefault(row["product"], set()).add(row["category"])

            label_map: Dict[tuple, str] = {}
            products_ordered: List[str] = []
            for row in rows:
                key = (row["product"], row["category"])
                if key not in label_map:
                    if len(product_categories[row["product"]]) > 1:
                        label = f"{row['product']}（{row['category']}）"
                    else:
                        label = row["product"]
                    label_map[key] = label
                    products_ordered.append(label)

            week_map: Dict[str, Dict] = {}
            for row in rows:
                ws = row["week_start"]
                if ws not in week_map:
                    week_map[ws] = {
                        "date": row["display_date"],
                        "week": row["week_label"] or f"{row['business_year']} W{row['business_week']:02d}",
                    }
                    for p in products_ordered:
                        week_map[ws][p] = {
                            "id": None, "value": None,
                            "is_manual_override": False, "is_missing_filled": False,
                            "source": None, "updated_by": None, "updated_at": None,
                        }
                label = label_map[(row["product"], row["category"])]
                week_map[ws][label] = {
                    "id": None,
                    "value": row["value"],
                    "is_manual_override": False,
                    "is_missing_filled": not bool(row["is_calculable"]) and metric == "apparent_demand",
                    "source": f"{row['source_country']} / {row['category']} / {row['mainstream_status']}",
                    "updated_by": row["validation_status"],
                    "updated_at": row["note"],
                }
            return {"metric": metric, "products": products_ordered, "data": list(week_map.values())}

        # fallback: old dv_data_points path
        base_sql = """SELECT wk.display_date, wk.year, wk.week_no, dp.id, dp.product,
                        dp.display_value, dp.is_manual_override, dp.is_missing_filled,
                        dp.source, dp.updated_by, dp.updated_at
                 FROM dv_data_points dp
                 JOIN dv_week_keys wk ON wk.id = dp.week_key_id
                 WHERE dp.metric_type = ?"""
        params: List[Any] = [metric]
        if year_list:
            placeholders = ",".join("?" for _ in year_list)
            base_sql += f" AND wk.year IN ({placeholders})"
            params.extend(year_list)
        if product_list:
            placeholders = ",".join("?" for _ in product_list)
            base_sql += f" AND dp.product IN ({placeholders})"
            params.extend(product_list)
        base_sql += " ORDER BY wk.display_date, dp.product"
        rows = db._exec(cur, base_sql, tuple(params)).fetchall()

    products = product_list if product_list else list(DV_PRODUCTS)
    week_map: Dict[str, Dict] = {}
    for r in rows:
        key = r["display_date"]
        if key not in week_map:
            week_map[key] = {
                "date": r["display_date"],
                "week": f"{r['year']} W{r['week_no']:02d}",
            }
            for p in products:
                week_map[key][p] = {"id": None, "value": None, "is_manual_override": False, "is_missing_filled": False, "source": None, "updated_by": None, "updated_at": None}
        week_map[key][r["product"]] = {
            "id": r["id"],
            "value": r["display_value"],
            "is_manual_override": bool(r["is_manual_override"]),
            "is_missing_filled": bool(r["is_missing_filled"]),
            "source": r["source"],
            "updated_by": r["updated_by"],
            "updated_at": r["updated_at"],
        }

    return {
        "metric": metric,
        "products": products,
        "data": list(week_map.values()),
    }


@router.put("/data-visualization/value")
async def update_value(
    payload: ManualEditRequest,
    user=Depends(dv_current_user),
):
    dv_require_edit("data_visualization_data", user)

    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(
            cur,
            """SELECT id, display_value, metric_type
               FROM dv_data_points WHERE id = ?""",
            (payload.data_point_id,),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="数据点不存在")

        old_val = existing["display_value"]
        metric_type = existing["metric_type"]
        user_name = user["name"]

        db._exec(
            cur,
            """UPDATE dv_data_points
               SET manual_value = ?, display_value = ?, is_manual_override = 1,
                   source = '手工修改', updated_by = ?
               WHERE id = ?""",
            (payload.new_value, payload.new_value, user_name, payload.data_point_id),
        )

        if metric_type in ("shipment", "inventory"):
            recalc_apparent_demand(conn)

    return {"ok": True, "data_point_id": payload.data_point_id, "new_value": payload.new_value}


# ── GET /api/data-visualization/chart ─────────────────────────────────

@router.get("/data-visualization/chart")
async def get_chart(
    metric: str = Query(..., pattern="^(inventory|shipment|arrival|apparent_demand)$"),
    years: str = "",
    products: str = "",
    categories: str = "",
    source_countries: str = "",
    mainstream_status: str = "",
    product_pool: str = "",
    user=Depends(dv_current_user),
):
    year_list: List[int] = []
    years_empty_requested = False
    if years:
        for part in years.split(","):
            part = part.strip()
            if part:
                if part == "__EMPTY__":
                    years_empty_requested = True
                    continue
                try:
                    year_list.append(int(part))
                except ValueError:
                    pass

    product_list = _split_filter_values(products) if products else []
    category_list = _split_filter_values(categories) if categories else []
    country_list = _split_filter_values(source_countries) if source_countries else []
    mainstream_list = _split_filter_values(mainstream_status) if mainstream_status else []
    empty_filter_requested = years_empty_requested or "__EMPTY__" in product_list or "__EMPTY__" in category_list or "__EMPTY__" in country_list or "__EMPTY__" in mainstream_list
    product_list = [item for item in product_list if item != "__EMPTY__"]
    category_list = [item for item in category_list if item != "__EMPTY__"]
    country_list = [item for item in country_list if item != "__EMPTY__"]
    mainstream_list = [item for item in mainstream_list if item != "__EMPTY__"]

    with db.connect() as conn:
        cur = conn.cursor()
        integrated_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_integrated_points").fetchone()["c"]
        if integrated_count:
            if empty_filter_requested:
                return {"metric": metric, "series": {}}
            params_i: List[Any] = [metric]
            sql_i = """SELECT week_start, display_date, business_year, business_week,
                              source_country, product, category, mainstream_status, value,
                              is_calculable, validation_status
                       FROM dv_integrated_points WHERE metric_type = ?"""
            if product_list:
                placeholders_p = ",".join("?" for _ in product_list)
                sql_i += f" AND product IN ({placeholders_p})"
                params_i.extend(product_list)
            if year_list:
                placeholders_y = ",".join("?" for _ in year_list)
                sql_i += f" AND business_year IN ({placeholders_y})"
                params_i.extend(year_list)
            if category_list:
                placeholders_c = ",".join("?" for _ in category_list)
                sql_i += f" AND category IN ({placeholders_c})"
                params_i.extend(category_list)
            if country_list:
                placeholders_n = ",".join("?" for _ in country_list)
                sql_i += f" AND source_country IN ({placeholders_n})"
                params_i.extend(country_list)
            if mainstream_list:
                placeholders_m = ",".join("?" for _ in mainstream_list)
                sql_i += f" AND mainstream_status IN ({placeholders_m})"
                params_i.extend(mainstream_list)
            sql_i += " ORDER BY product, category, week_start"
            rows_i = db._exec(cur, sql_i, tuple(params_i)).fetchall()
            if product_pool == "aggregate":
                aggregate: Dict[tuple, Dict[str, Any]] = {}
                for row in rows_i:
                    status = row["mainstream_status"] or "非主流"
                    label = "主流矿合计" if status == "主流" else "非主流矿合计"
                    year = str(row["business_year"]) if row["business_year"] else row["week_start"][:4]
                    key = (label, year, row["business_week"], row["display_date"])
                    if key not in aggregate:
                        aggregate[key] = {
                            "week_no": row["business_week"] if row["business_week"] else 0,
                            "display_date": row["display_date"],
                            "value": 0.0,
                            "is_manual_override": False,
                            "is_missing_filled": False,
                            "_has_value": False,
                        }
                    is_missing = row["value"] is None or (not bool(row["is_calculable"]) and metric == "apparent_demand")
                    if is_missing:
                        aggregate[key]["is_missing_filled"] = True
                    else:
                        aggregate[key]["value"] += float(row["value"])
                        aggregate[key]["_has_value"] = True
                result_agg: Dict[str, Dict[str, List[Dict]]] = {}
                for (label, year, _week_no, _display_date), item in sorted(aggregate.items()):
                    has_value = item.pop("_has_value", False)
                    if item["is_missing_filled"] or not has_value:
                        item["value"] = None
                    result_agg.setdefault(label, {}).setdefault(year, []).append(item)
                return {"metric": metric, "series": result_agg}

            product_categories: Dict[str, set] = {}
            for row in rows_i:
                product_categories.setdefault(row["product"], set()).add(row["category"])
            result_i: Dict[str, Dict[str, List[Dict]]] = {}
            for row in rows_i:
                label = row["product"]
                if len(product_categories[row["product"]]) > 1:
                    label = f"{row['product']}（{row['category']}）"
                year = str(row["business_year"]) if row["business_year"] else row["week_start"][:4]
                if label not in result_i:
                    result_i[label] = {}
                if year not in result_i[label]:
                    result_i[label][year] = []
                result_i[label][year].append({
                    "week_no": row["business_week"] if row["business_week"] else 0,
                    "display_date": row["display_date"],
                    "value": row["value"],
                    "is_manual_override": False,
                    "is_missing_filled": not bool(row["is_calculable"]) and metric == "apparent_demand",
                })
            return {"metric": metric, "series": result_i}

        params: List[Any] = [metric]
        sql = """SELECT wk.year, wk.week_no, wk.display_date, dp.product,
                        dp.display_value, dp.is_manual_override, dp.is_missing_filled
                 FROM dv_data_points dp
                 JOIN dv_week_keys wk ON wk.id = dp.week_key_id
                 WHERE dp.metric_type = ?"""

        if product_list:
            placeholders_p = ",".join("?" for _ in product_list)
            sql += f" AND dp.product IN ({placeholders_p})"
            params.extend(product_list)

        if year_list:
            placeholders_y = ",".join("?" for _ in year_list)
            sql += f" AND wk.year IN ({placeholders_y})"
            params.extend(year_list)

        sql += " ORDER BY dp.product, wk.year, wk.week_no"
        rows = db._exec(cur, sql, tuple(params)).fetchall()

    by_product_year: Dict[str, Dict[int, List[Dict]]] = {}
    for r in rows:
        prod = r["product"]
        yr = r["year"]
        if prod not in by_product_year:
            by_product_year[prod] = {}
        if yr not in by_product_year[prod]:
            by_product_year[prod][yr] = []
        by_product_year[prod][yr].append({
            "week_no": r["week_no"],
            "display_date": r["display_date"],
            "value": r["display_value"],
            "is_manual_override": bool(r["is_manual_override"]),
            "is_missing_filled": bool(r["is_missing_filled"]),
        })

    # Sort by year within each product
    result: Dict[str, Dict[str, List[Dict]]] = {}
    for prod, by_year in by_product_year.items():
        result[prod] = {str(k): v for k, v in sorted(by_year.items())}

    return {"metric": metric, "series": result}



# ── GET /api/data-visualization/import-batches ────────────────────────

@router.get("/data-visualization/import-batches")
async def get_import_batches(user=Depends(dv_current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            """SELECT id, file_name, metric_types, date_start, date_end,
                      insert_count, overwrite_count, error_count, manual_protected_count,
                      status, created_by, created_at
        FROM dv_import_batches ORDER BY created_at DESC LIMIT 50""",
        ).fetchall()
    return {"batches": [_row_to_dict(r) for r in rows]}


# ── Seed test data ─────────────────────────────────────────────────

def seed_dv_data():
    """从真实 Excel 数据播种 DV 表（发运 + 卡粉库存 + 表需）。
    
    数据来源: 建龙/期货组/本地数据库/副本铁矿data base.xlsx
    使用 {parse_excel + match_and_merge_weeks + recalc_apparent_demand} 流程，
    替代旧的随机合成数据。
    """
    from .dv_seed_data import DV_SEED_SHIPMENT, DV_SEED_INVENTORY

    with db.connect() as conn:
        cur = conn.cursor()
        existing_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_week_keys").fetchone()["c"]
        if existing_count > 0:
            old_seed = db._exec(
                cur, "SELECT COUNT(*) AS c FROM dv_data_points WHERE source = '种子数据'"
            ).fetchone()["c"]
            if old_seed > 0:
                print("[seed_dv_data] 检测到旧种子数据，清除后重新生成（真实数据）...")
                db._exec(cur, "DELETE FROM dv_change_log")
                db._exec(cur, "DELETE FROM dv_data_points")
                db._exec(cur, "DELETE FROM dv_week_keys")
                db._exec(cur, "DELETE FROM dv_import_batches")
            else:
                return

        # 构建 parse_excel 兼容的 dict
        parsed = {
            "shipment": [{"date": s[0], "value": s[1]} for s in DV_SEED_SHIPMENT],
            "inventory": [{"date": i[0], "value": i[1]} for i in DV_SEED_INVENTORY],
        }

        result = match_and_merge_weeks(parsed, conn)
        week_keys = result["week_keys"]
        pairs = result["pairs"]

        total_weeks = len(week_keys)
        print(f"[seed_dv_data] 真实数据：{total_weeks} 个业务周")

        # 插入发运和库存数据点
        for pair in pairs:
            wk_id = pair["week_key_id"]
            if pair["shipment_row"]:
                s_val = pair["shipment_row"]["value"]
                db._exec(
                    cur,
                    """INSERT INTO dv_data_points
                       (week_key_id, product, metric_type, imported_value, calculated_value,
                        display_value, source, created_by)
                       VALUES (?, ?, 'shipment', ?, ?, ?, '种子数据', 'system')""",
                    (wk_id, PRODUCT, s_val, s_val, s_val),
                )
            if pair["inventory_row"]:
                i_val = pair["inventory_row"]["value"]
                db._exec(
                    cur,
                    """INSERT INTO dv_data_points
                       (week_key_id, product, metric_type, imported_value, calculated_value,
                        display_value, source, created_by)
                       VALUES (?, ?, 'inventory', ?, ?, ?, '种子数据', 'system')""",
                    (wk_id, PRODUCT, i_val, i_val, i_val),
                )

        # 计算表需
        recalc_apparent_demand(conn)

        point_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_data_points WHERE source = '种子数据'").fetchone()["c"]
        print(f"[seed_dv_data] 已完成：{total_weeks} 周，共 {point_count} 条数据点")
