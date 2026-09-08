"""Deterministic weekly iron-ore report service.

The report is generated from a frozen database snapshot.  The renderer is
deliberately rule based: calculations and labels are stable between runs and
the service never asks an online model to invent a conclusion.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db
from .permissions import require_permission


TEMPLATE_KEY = "iron_ore_weekly"
TEMPLATE_VERSION = "V1.0"
TEMPLATE_NAME = "铁矿石周报（46页基线）"
RENDERER_VERSION = "iron-ore-weekly-renderer-1"
RULES_REFERENCE = "docs/2026-09-08-iron-ore-weekly-report-rules.md"

TEMPLATE_CONFIG = {
    "sections": [
        "概览", "矿种结构", "品位结构", "全品种增减", "主流与非主流", "港口库存与港差",
        "到港与表需", "附录季节性",
    ],
    "mainstream_products": "system_24",
    "mnpj_label": "MNPJ",
    "port_base": "日照",
    "actual_arrival_separate": True,
    "legacy_apparent_demand_unchanged": True,
}


class ReportGenerateRequest(BaseModel):
    report_week: str
    template_version: str = TEMPLATE_VERSION
    force_new_revision: bool = False


async def _report_user(authorization: Optional[str] = Header(default=None)):
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else None
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def _require_report_view(user: dict) -> None:
    require_permission(user, "data_visualization.report", "view")


def _require_report_edit(user: dict) -> None:
    require_permission(user, "data_visualization.report", "edit")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def register_builtin_templates() -> Dict[str, Any]:
    """Register V1.0 once; available templates are immutable after creation."""
    config_json = _canonical_json(TEMPLATE_CONFIG)
    config_sha256 = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT * FROM dv_report_templates WHERE template_key = ? AND version = ?",
            (TEMPLATE_KEY, TEMPLATE_VERSION),
        ).fetchone()
        if not row:
            template_id = db._last_insert_id(
                cur,
                """INSERT INTO dv_report_templates
                   (template_key, version, name, status, is_default, change_summary,
                    config_json, config_sha256, renderer_version, rules_reference, created_by)
                   VALUES (?, ?, ?, 'available', 1, ?, ?, ?, ?, ?, ?)""",
                (
                    TEMPLATE_KEY, TEMPLATE_VERSION, TEMPLATE_NAME,
                    "当前确认的46页周报基线；实际页数随数据完整性变化",
                    config_json, config_sha256, RENDERER_VERSION, RULES_REFERENCE, "system",
                ),
            )
            row = db._exec(cur, "SELECT * FROM dv_report_templates WHERE id = ?", (template_id,)).fetchone()
        return dict(row)


def _parse_report_week(value: str) -> tuple[str, int, int]:
    text = (value or "").strip().upper().replace(" ", "")
    match = re.fullmatch(r"(\d{4})[-/]?W(\d{1,2})", text)
    if match:
        year, week_no = int(match.group(1)), int(match.group(2))
    else:
        try:
            parsed = date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ValueError("报告周需使用 YYYY-WNN 或 YYYY-MM-DD") from exc
        from .data_visualization import compute_business_week

        business_week = compute_business_week(parsed)
        year, week_no = business_week["year"], business_week["week_no"]
    if week_no < 1 or week_no > 54:
        raise ValueError("业务周次无效")
    # Business weeks are defined by the existing data-visualization rule. A
    # short scan avoids silently substituting ISO-week semantics at year end.
    from .data_visualization import compute_business_week

    start = date(year, 1, 1) - timedelta(days=14)
    end = date(year, 12, 31) + timedelta(days=14)
    while start <= end:
        info = compute_business_week(start)
        if info["year"] == year and info["week_no"] == week_no:
            return info["week_start_date"], year, week_no
        start += timedelta(days=1)
    raise ValueError("找不到报告周的起始日期")


def _rows(cur, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    return [dict(row) for row in db._exec(cur, sql, params).fetchall()]


def _week_rows(cur, table: str, weeks: tuple[str, str]) -> List[Dict[str, Any]]:
    return _rows(cur, f"SELECT * FROM {table} WHERE week_start IN (?, ?)", weeks)


def _metric_dates(rows: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    values = [row.get("display_date") or row.get("observed_date") for row in rows]
    values = [str(value) for value in values if value]
    return {"min": min(values) if values else "", "max": max(values) if values else ""}


def _count_for_week(rows: Iterable[Dict[str, Any]], week_start: str) -> int:
    return sum(1 for row in rows if row.get("week_start") == week_start)


def _report_inventory(legacy, detailed):
    """Use the complete source totals for V2 weeks; retain older V1 history."""
    totals = [dict(row, metric_type="inventory", display_date=row.get("observed_date"))
              for row in detailed if row.get("scope_type") == "total"]
    detailed_weeks = {row["week_start"] for row in totals}
    return [row for row in legacy if row.get("metric_type") == "inventory"
            and row.get("week_start") not in detailed_weeks] + totals


def _load_report_input(report_week: str) -> Dict[str, Any]:
    week_start, year, week_no = _parse_report_week(report_week)
    current_week = week_start
    previous_week = (date.fromisoformat(week_start) - timedelta(days=7)).isoformat()
    weeks = (previous_week, current_week)
    with db.connect() as conn:
        cur = conn.cursor()
        legacy = _rows(
            cur,
            """SELECT * FROM dv_integrated_points
               WHERE week_start IN (?, ?)
                 AND metric_type IN ('inventory', 'arrival', 'apparent_demand')
               ORDER BY week_start, metric_type, source_country, category, product, id""",
            weeks,
        )
        history_arrival_actual = _rows(
            cur,
            """SELECT * FROM dv_arrival_facts
               WHERE arrival_kind = 'actual' AND week_start <= ?
               ORDER BY week_start, port_name, slice_type, dimension, id""",
            (current_week,),
        )
        available_actual_weeks = [
            row["week_start"] for row in history_arrival_actual
            if row.get("slice_type") == "country" and row.get("dimension") == "总计"
            and row.get("value") is not None
        ]
        actual_current_week = max(available_actual_weeks, default=current_week)
        actual_previous_week = (date.fromisoformat(actual_current_week) - timedelta(days=7)).isoformat()
        arrival_actual = [row for row in history_arrival_actual
                          if row.get("week_start") in (actual_previous_week, actual_current_week)]
        port_inventory = _week_rows(cur, "dv_port_inventory_facts", weeks)
        inventory_summary = _week_rows(cur, "dv_inventory_summary_facts", weeks)
        inventory_grade = _week_rows(cur, "dv_inventory_grade_facts", weeks)
        inventory_mainstream = _week_rows(cur, "dv_inventory_mainstream_facts", weeks)
        history_port_inventory = _rows(
            cur,
            """SELECT * FROM dv_port_inventory_facts
               ORDER BY week_start, port_name, product, id""",
        )
        history_inventory_summary = _rows(
            cur,
            """SELECT * FROM dv_inventory_summary_facts
               ORDER BY week_start, port_name, metric, id""",
        )
        history_inventory_mainstream = _rows(
            cur,
            """SELECT * FROM dv_inventory_mainstream_facts
               ORDER BY week_start, port_name, product, id""",
        )
        history_legacy = _rows(
            cur,
            """SELECT * FROM dv_integrated_points
               WHERE metric_type IN ('inventory', 'arrival', 'apparent_demand')
               ORDER BY week_start, source_country, category, product, id""",
        )
        history_grade = _rows(
            cur,
            """SELECT * FROM dv_inventory_grade_facts
               ORDER BY week_start, grade, category, port_name, id""",
        )
        try:
            prices = _rows(
                cur,
                """SELECT * FROM iron_ore_basis_results
                   WHERE business_year = ? AND business_week IN (?, ?)
                   ORDER BY business_date, port, product, id""",
                (year, week_no - 1, week_no),
            )
        except Exception:
            prices = []
        source_batch_ids = sorted({str(row.get("batch_id")) for row in legacy if row.get("batch_id") is not None})
        source_batch_ids.extend(sorted({str(row.get("package_id")) for row in arrival_actual + port_inventory + inventory_grade}))
    input_data = {
        "report_week": f"{year}-W{week_no:02d}",
        "week_start": current_week,
        "previous_week_start": previous_week,
        "inventory": _report_inventory(legacy, port_inventory),
        "arrival_estimated": [row for row in legacy if row.get("metric_type") == "arrival"],
        "legacy_apparent_demand": [row for row in legacy if row.get("metric_type") == "apparent_demand"],
        "arrival_actual": arrival_actual,
        "actual_current_week_start": actual_current_week,
        "actual_previous_week_start": actual_previous_week,
        "history_arrival_actual": history_arrival_actual,
        "port_inventory": port_inventory,
        "inventory_summary": inventory_summary,
        "inventory_grade": inventory_grade,
        "inventory_mainstream": inventory_mainstream,
        "history_port_inventory": history_port_inventory,
        "history_inventory_summary": history_inventory_summary,
        "history_inventory_mainstream": history_inventory_mainstream,
        "history_inventory": _report_inventory(history_legacy, history_port_inventory),
        "history_grade": history_grade,
        "history_arrival_estimated": [row for row in history_legacy if row.get("metric_type") == "arrival"],
        "history_apparent_demand": [row for row in history_legacy if row.get("metric_type") == "apparent_demand"],
        "prices": prices,
    }
    inventory = input_data["inventory"]
    actual = input_data["arrival_actual"]
    estimated = input_data["arrival_estimated"]
    demand = input_data["legacy_apparent_demand"]
    validation = {
        "inventory": {
            "current_count": _count_for_week(inventory, current_week),
            "previous_count": _count_for_week(inventory, previous_week),
            "dates": _metric_dates(inventory),
        },
        "actual_arrival": {
            "current_week_start": actual_current_week,
            "previous_week_start": actual_previous_week,
            "latest_available": actual_current_week != current_week,
            "current_count": sum(
                1 for row in actual if row.get("week_start") == actual_current_week
                and row.get("slice_type") == "country" and row.get("dimension") == "总计"
                and row.get("value") is not None
            ),
            "previous_count": sum(
                1 for row in actual if row.get("week_start") == actual_previous_week
                and row.get("slice_type") == "country" and row.get("dimension") == "总计"
                and row.get("value") is not None
            ),
            "dates": _metric_dates(actual),
        },
        "estimated_arrival": {
            "current_count": _count_for_week(estimated, current_week),
            "previous_count": _count_for_week(estimated, previous_week),
            "dates": _metric_dates(estimated),
        },
        "legacy_apparent_demand": {
            "current_count": _count_for_week(demand, current_week),
            "previous_count": _count_for_week(demand, previous_week),
            "dates": _metric_dates(demand),
        },
        "prices": {
            "current_count": sum(1 for row in input_data["prices"] if int(row.get("business_week") or 0) == week_no),
            "previous_count": sum(1 for row in input_data["prices"] if int(row.get("business_week") or 0) == week_no - 1),
            "dates": _metric_dates(input_data["prices"]),
        },
    }
    warnings = []
    if not validation["inventory"]["current_count"] or not validation["inventory"]["previous_count"]:
        warnings.append("库存未同时取得本期和上期汇总，无法完成库存周变比较")
    if not validation["actual_arrival"]["current_count"]:
        warnings.append("实际到港没有可用记录；保留独立板块并显示缺失，不回填预计到港")
    elif actual_current_week != current_week:
        actual_iso = date.fromisoformat(actual_current_week).isocalendar()
        warnings.append(f"实际到港使用最新可用周 {actual_iso[0]}-W{actual_iso[1]:02d}，并非报告周")
    if not validation["prices"]["current_count"]:
        warnings.append("期现价格没有报告周记录；港差/基差板块仅显示可取得数据")
    validation["warnings"] = warnings
    validation["ready"] = bool(
        validation["inventory"]["current_count"] and validation["inventory"]["previous_count"]
    )
    input_hash = hashlib.sha256(_canonical_json(input_data).encode("utf-8")).hexdigest()
    return {
        "report_week": input_data["report_week"],
        "week_start": current_week,
        "previous_week_start": previous_week,
        "input": input_data,
        "validation": validation,
        "input_sha256": input_hash,
        "source_batch_ids": sorted(set(source_batch_ids)),
    }


def build_report_snapshot(report_week: str, template_id: Optional[int] = None, created_by: str = "") -> Dict[str, Any]:
    """Freeze the inputs used by a report; repeated identical inputs reuse it."""
    loaded = _load_report_input(report_week)
    if not loaded["validation"]["ready"]:
        raise ValueError(json.dumps({"message": "报告周数据未就绪", "validation": loaded["validation"]}, ensure_ascii=False))
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT id FROM dv_report_snapshots WHERE report_week = ? AND input_sha256 = ?",
            (loaded["report_week"], loaded["input_sha256"]),
        ).fetchone()
        if row:
            snapshot_id = row["id"]
        else:
            snapshot_id = db._last_insert_id(
                cur,
                """INSERT INTO dv_report_snapshots
                   (report_week, input_sha256, input_json, validation_json, source_batch_ids, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    loaded["report_week"], loaded["input_sha256"], _canonical_json(loaded["input"]),
                    _canonical_json(loaded["validation"]), _canonical_json(loaded["source_batch_ids"]), created_by,
                ),
            )
        conn.commit()
    loaded["snapshot_id"] = snapshot_id
    return loaded


def report_readiness(report_week: str) -> Dict[str, Any]:
    try:
        loaded = _load_report_input(report_week)
    except ValueError as exc:
        return {"report_week": report_week, "ready": False, "warnings": [str(exc)]}
    return {
        "report_week": loaded["report_week"],
        "week_start": loaded["week_start"],
        "previous_week_start": loaded["previous_week_start"],
        "ready": loaded["validation"]["ready"],
        "validation": loaded["validation"],
        "source_batch_ids": loaded["source_batch_ids"],
    }


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _signed(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):+,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _group_change(rows: Iterable[Dict[str, Any]], key: str, current: str, previous: str) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, float]] = {}
    for row in rows:
        label = str(row.get(key) or "未知")
        value = row.get("value")
        if value is None:
            continue
        bucket = groups.setdefault(label, {"previous": 0.0, "current": 0.0})
        if row.get("week_start") == previous:
            bucket["previous"] += float(value)
        elif row.get("week_start") == current:
            bucket["current"] += float(value)
    result = []
    for label, values in groups.items():
        values["delta"] = values["current"] - values["previous"]
        result.append({"label": label, **values})
    return sorted(result, key=lambda item: (item["delta"], item["label"]))


def _register_pdf_fonts() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"),
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc"),
    ]
    for regular_path, bold_path in candidates:
        if Path(regular_path).exists():
            try:
                pdfmetrics.registerFont(TTFont("IronOreCJK", regular_path, subfontIndex=0))
                if Path(bold_path).exists():
                    pdfmetrics.registerFont(TTFont("IronOreCJKB", bold_path, subfontIndex=0))
                else:
                    pdfmetrics.registerFont(TTFont("IronOreCJKB", regular_path, subfontIndex=0))
                return "IronOreCJK", "IronOreCJKB"
            except Exception:
                continue
    return "Helvetica", "Helvetica-Bold"


def _render_pdf(snapshot: Dict[str, Any], output_path: Path, revision_no: int) -> None:
    """Render the fixed V1.0 page sequence from one frozen input snapshot.

    The page count follows the accepted 46-page baseline.  A page remains in
    the sequence when a metric is unavailable; the page then carries a clear
    missing-data explanation instead of changing the report's meaning by
    silently substituting another week or another arrival definition.
    """
    from collections import defaultdict
    import calendar

    from reportlab.lib.colors import Color, HexColor, white
    from reportlab.pdfgen import canvas

    regular, bold = _register_pdf_fonts()
    width, height = 595.276, 841.89
    margin = 42
    content_width = width - margin * 2
    navy, teal, red, gray, light, ink = (
        HexColor("#16324F"), HexColor("#087F8C"), HexColor("#C15A46"),
        HexColor("#637489"), HexColor("#EDF3F7"), HexColor("#25364A"),
    )
    grid = HexColor("#B8C5CF")
    palette = [
        "#A8B8C5", "#8295A6", "#BE9C60", "#819773", "#947EA4",
        "#417FA2", "#BC804C", "#218C84", "#5B57A2", "#CF4737",
    ]
    c = canvas.Canvas(str(output_path), pagesize=(width, height))
    c.setTitle(f"铁矿石周报 {snapshot['report_week']} 模板{TEMPLATE_VERSION} R{revision_no}")
    c.setAuthor("数据可视化管理")
    page_no = 0
    current = snapshot["week_start"]
    previous = snapshot["previous_week_start"]
    data = snapshot["input"]

    def text(x, y, value, size=10, color=ink, is_bold=False):
        c.setFillColor(color)
        c.setFont(bold if is_bold else regular, size)
        c.drawString(x, height - y, str(value))

    def centered(x, y, value, size=9, color=ink, is_bold=False):
        c.setFillColor(color)
        c.setFont(bold if is_bold else regular, size)
        c.drawCentredString(x, height - y, str(value))

    def right(x, y, value, size=10, color=ink):
        c.setFillColor(color)
        c.setFont(regular, size)
        c.drawRightString(x, height - y, str(value))

    def rect(x, y, w, h, color):
        c.setFillColor(color)
        c.rect(x, height - y - h, w, h, fill=1, stroke=0)

    def para(value, y, size=10, color=ink, w=content_width, x=margin, leading=None):
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph

        style = ParagraphStyle(
            "iron-ore-report", fontName=regular, fontSize=size,
            leading=leading or size * 1.55, textColor=color,
        )
        paragraph = Paragraph(str(value).replace("<0.01", "&lt;0.01"), style)
        _, rendered_height = paragraph.wrap(w, 700)
        paragraph.drawOn(c, x, height - y - rendered_height)
        return y + rendered_height

    def start(section, title, subtitle):
        nonlocal page_no
        if page_no:
            c.showPage()
        page_no += 1
        rect(0, 0, width, 8, teal)
        text(margin, 34, "IRON ORE / WEEKLY BRIEF", 9, teal, True)
        right(width - margin, 34, f"{snapshot['report_week']} / 模板{TEMPLATE_VERSION} / R{revision_no}", 8, gray)
        text(margin, 76, f"{section}  {title}", 21, navy, True)
        text(margin, 101, subtitle, 9, gray)
        rect(margin, 117, content_width, 1, light)
        text(margin, 806, "来源：系统冻结数据快照；实际到港、预计到港、估算到港分开记录", 8, gray)
        right(width - margin, 806, f"{page_no:02d} / 46", 9, gray)
        try:
            bookmark = f"iron-ore-page-{page_no}"
            c.bookmarkPage(bookmark)
            c.addOutlineEntry(f"{page_no:02d} {section} {title}", bookmark, level=0)
        except Exception:
            pass

    def table(headers, rows, y, widths=None, rowh=24, size=8.5):
        widths = widths or [content_width / len(headers)] * len(headers)
        rect(margin, y, content_width, 26, navy)
        x = margin
        for header, w in zip(headers, widths):
            text(x + 6, y + 17, header, size, white, True)
            x += w
        y += 26
        for idx, row in enumerate(rows):
            if idx % 2 == 0:
                rect(margin, y, content_width, rowh, light)
            x = margin
            for col, (value, w) in enumerate(zip(row, widths)):
                if col == 0:
                    text(x + 6, y + rowh * 0.68, value, size)
                else:
                    right(x + w - 6, y + rowh * 0.68, value, size)
                x += w
            y += rowh
        return y

    def change_rows(rows, limit=12):
        selected = sorted(rows, key=lambda item: (abs(item["delta"]), item["label"]), reverse=True)[:limit]
        return [[item["label"], _fmt(item["previous"]), _fmt(item["current"]), _signed(item["delta"])] for item in selected]

    def bars(items, y, h=145, label_width=230):
        items = [(str(label), float(value)) for label, value in items if value is not None]
        if not items:
            para("暂无可比数据", y, 10, gray)
            return y + h
        max_value = max(abs(item[1]) for item in items) or 1
        zero = margin + label_width
        span = content_width - label_width - 40
        row_h = h / max(1, len(items))
        c.setStrokeColor(grid)
        c.line(zero, height - y, zero, height - y - h)
        for idx, (label, value) in enumerate(items):
            yy = y + idx * row_h + row_h * 0.2
            text(margin, yy + 12, label[:32], 8.3)
            bar_w = abs(value) / max_value * (span / 2)
            rect(zero if value >= 0 else zero - bar_w, yy, bar_w, max(9, row_h * 0.5), red if value >= 0 else teal)
            if value >= 0:
                text(zero + bar_w + 4, yy + 11, _signed(value), 8, red)
            else:
                right(zero - bar_w - 4, yy + 11, _signed(value), 8, teal)
        return y + h

    def _number(value):
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def _date_key(row, preferred="week_start"):
        value = row.get(preferred) or row.get("observed_date") or row.get("display_date")
        try:
            return date.fromisoformat(str(value)[:10]) if value else None
        except ValueError:
            return None

    def _rows_series(rows, label_fn, preferred="week_start", filter_fn=None):
        output = defaultdict(lambda: defaultdict(float))
        for row in rows or []:
            if filter_fn and not filter_fn(row):
                continue
            value = _number(row.get("value"))
            key = _date_key(row, preferred)
            if value is None or key is None:
                continue
            label = str(label_fn(row) or "未知")
            output[label][key] += value
        return {label: dict(values) for label, values in output.items()}

    def _series_value(series_map, label, week):
        values = series_map.get(label, {})
        return values.get(date.fromisoformat(week))

    def _change(rows, key, previous_week=previous, current_week=current):
        return _group_change(rows or [], key, current_week, previous_week)

    def _inventory_rows(rows):
        from . import data_visualization as dv

        result = []
        for source in rows or []:
            row = dict(source)
            if not row.get("mainstream_status"):
                row["mainstream_status"] = dv._mainstream_status(row.get("product", ""), row.get("category", ""))
            product = str(row.get("product") or "未知")
            category = str(row.get("category") or "")
            country = str(row.get("source_country") or "")
            if product == "其他" and country:
                label = f"{country}（其他{category}）"
            elif product in {"印度", "乌克兰", "南非", "几内亚"} and category:
                label = f"{product}（{category}）"
            else:
                label = product
            row["report_product"] = label
            result.append(row)
        return result

    inventory_rows = _inventory_rows(data.get("inventory"))
    history_inventory_rows = _inventory_rows(data.get("history_inventory") or inventory_rows)
    inventory_total = _change(inventory_rows, "category")
    categories = ["粉矿", "块矿", "球团", "精粉"]
    total_previous = sum(item["previous"] for item in inventory_total)
    total_current = sum(item["current"] for item in inventory_total)

    def _arrival_actual_rows(rows, week, slice_type="country", dimension=None, scope="47港"):
        candidates = [
            row for row in rows or []
            if row.get("week_start") == week and row.get("arrival_kind") == "actual"
            and row.get("slice_type") == slice_type and (dimension is None or row.get("dimension") == dimension)
            and row.get("value") is not None
        ]
        total_rows = [row for row in candidates if row.get("scope_type") == "total"]
        preferred = [row for row in total_rows if scope in str(row.get("sample_name") or row.get("port_name") or "")]
        if preferred:
            return preferred
        if total_rows:
            return total_rows[:1]
        return [row for row in candidates if row.get("scope_type") == "port"]

    def _actual_total(week):
        rows = _arrival_actual_rows(data.get("arrival_actual"), week, "country", "总计")
        if not rows:
            rows = _arrival_actual_rows(data.get("arrival_actual"), week, "country", "总计", scope="")
        return sum(_number(row.get("value")) or 0 for row in rows) if rows else None

    actual_current_week = data.get("actual_current_week_start", current)
    actual_previous_week = data.get("actual_previous_week_start", previous)
    actual_iso = date.fromisoformat(actual_current_week).isocalendar()
    actual_previous_iso = date.fromisoformat(actual_previous_week).isocalendar()
    actual_period = f"{actual_iso[0]}-W{actual_iso[1]:02d} 对 {actual_previous_iso[0]}-W{actual_previous_iso[1]:02d}"
    actual_current = _actual_total(actual_current_week)
    actual_previous = _actual_total(actual_previous_week)

    def _actual_series(slice_type="country", dimension=None, label_fn=None):
        rows = data.get("history_arrival_actual") or data.get("arrival_actual") or []
        grouped = defaultdict(dict)
        weeks = sorted({str(row.get("week_start")) for row in rows if row.get("week_start")})
        for week in weeks:
            if slice_type == "country" and dimension is None:
                selected = _arrival_actual_rows(rows, week, "country", "总计")
            else:
                selected = _arrival_actual_rows(rows, week, slice_type, dimension)
            if not selected:
                continue
            key = _date_key(selected[0])
            if key is None:
                continue
            label = str(label_fn(selected[0]) if label_fn else (dimension or "47港到港"))
            grouped[label][key] = sum(_number(row.get("value")) or 0 for row in selected)
        return {label: values for label, values in grouped.items()}

    def _actual_dimension_total(week, slice_type, dimension):
        selected = _arrival_actual_rows(data.get("arrival_actual"), week, slice_type, dimension)
        return sum(_number(row.get("value")) or 0 for row in selected) if selected else None

    displayed_ports = ["江阴", "太仓", "舟山", "湛江", "岚桥", "岚山", "连云港", "青岛", "日照", "曹妃甸", "黄骅", "京唐", "天津"]
    port_facts = [row for row in data.get("port_inventory", []) if row.get("scope_type") == "sample"]
    history_port_facts = [row for row in data.get("history_port_inventory", []) if row.get("scope_type") == "sample"]
    port_rows = _change(port_facts, "port_name")

    def _port_product_value(port, product, week, rows=None):
        selected = rows if rows is not None else port_facts
        values = [
            _number(row.get("value")) for row in selected
            if row.get("port_name") == port and row.get("week_start") == week and row.get("product") == product
        ]
        values = [value for value in values if value is not None]
        return sum(values) if values else None

    def _port_total_value(port, week):
        values = [
            _number(row.get("value")) for row in port_facts
            if row.get("port_name") == port and row.get("week_start") == week
        ]
        values = [value for value in values if value is not None]
        return sum(values) if values else None

    def _port_grade_value(port, grade, week):
        selected = [
            row for row in data.get("inventory_grade", [])
            if row.get("port_name") == port and row.get("week_start") == week
            and row.get("scope_type") == "sample" and row.get("grade") == grade and row.get("value") is not None
        ]
        totals = [row for row in selected if row.get("category") == "全品种"]
        return sum(_number(row.get("value")) or 0 for row in (totals or selected)) if selected else None

    def _port_product_series(port):
        return _rows_series(
            history_port_facts,
            lambda row: row.get("product") or "未知",
            filter_fn=lambda row: row.get("port_name") == port,
        )

    def _port_series():
        return _rows_series(history_port_facts, lambda row: row.get("port_name") or "未知")

    def _category_series():
        return _rows_series(history_inventory_rows, lambda row: row.get("category") or "未知")

    def _mnpj_series(rows):
        names = {"纽曼粉", "麦克粉", "PB粉", "金布巴粉"}
        output = defaultdict(dict)
        for row in rows:
            if row.get("report_product") not in names or row.get("value") is None:
                continue
            key = _date_key(row)
            if key is None:
                continue
            bucket = output.setdefault("MNPJ", {})
            bucket[key] = bucket.get(key, 0.0) + float(row["value"])
        return dict(output)

    def _grade_series():
        rows = [row for row in data.get("history_grade", []) if row.get("scope_type") == "total" and row.get("category") == "全品种"]
        return _rows_series(rows, lambda row: row.get("grade") or "未知")

    def _mainstream_series():
        rows = history_inventory_rows
        main = _rows_series(rows, lambda row: row.get("mainstream_status") or "非主流")
        total = _rows_series(rows, lambda row: "总库存")
        if "主流" in main:
            non = {}
            for day, value in total.get("总库存", {}).items():
                non[day] = value - main.get("主流", {}).get(day, 0.0)
            main["非主流"] = non
        return main

    def seasonal_chart(label, series_map, x, y, w=244, h=268, note=""):
        text(x, y, label, 10, navy, True)
        values = [float(value) for values in series_map.values() for value in values.values() if value is not None]
        if not values:
            para("暂无可取得的历史序列；缺失不补零。", y + 30, 8.5, gray, w=w, x=x)
            return
        max_value = max(values) * 1.09 or 1
        chart_x, chart_y = x + 33, y + 24
        plot_w, plot_h = w - 42, h - 92
        for fraction in (0, 0.5, 1):
            yy = chart_y + plot_h * (1 - fraction)
            c.setStrokeColor(light)
            c.setLineWidth(0.4)
            c.line(chart_x, height - yy, chart_x + plot_w, height - yy)
            right(chart_x - 4, yy + 3, f"{max_value * fraction:.0f}", 7, gray)
        for month in range(1, 13):
            xx = chart_x + (month - 1) / 12 * plot_w
            text(xx - 1, chart_y + plot_h + 12, str(month), 7, gray)
            if month in (4, 7, 10):
                c.setStrokeColor(light)
                c.line(xx, height - chart_y, xx, height - chart_y - plot_h)
        for year_index, (year, points) in enumerate(sorted((year, sorted(values.items())) for year, values in series_map.items())):
            if not 2017 <= int(year) <= 2030:
                continue
            color = HexColor(palette[(int(year) - 2017) % len(palette)])
            c.setStrokeColor(color)
            c.setLineWidth(1.7 if int(year) == current_year else 0.65)
            previous_point = None
            for day, value in points:
                if value is None:
                    previous_point = None
                    continue
                day = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
                xx = chart_x + ((day.month - 1) + (day.day - 1) / calendar.monthrange(day.year, day.month)[1]) / 12 * plot_w
                yy = chart_y + plot_h * (1 - min(float(value) / max_value, 1))
                if previous_point:
                    gap = (day - previous_point[0]).days
                    if gap <= 21:
                        c.setDash(2, 2) if gap > 7 else c.setDash()
                        c.line(previous_point[1], height - previous_point[2], xx, height - yy)
                if int(year) == current_year:
                    c.setFillColor(color)
                    c.circle(xx, height - yy, 1.1, fill=1, stroke=0)
                previous_point = (day, xx, yy)
            c.setDash()
        latest_day = max((day for values in series_map.values() for day in values), default=None)
        if latest_day:
            latest_value = max((values.get(latest_day) for values in series_map.values() if latest_day in values), default=None)
            text(x, y + h - 35, f"末期 {_fmt(latest_value)} 万吨 | {sum(len(values) for values in series_map.values())} 个观察期", 7.7, gray)
        text(x, y + h - 20, note or "横轴：月份；缺口断线；纵轴：万吨", 7.2, gray)

    # The report baseline uses 2026 for the current source package.  It is
    # derived from the selected report week so the same renderer works later.
    current_year = int(str(snapshot["report_week"])[:4])

    def season_page(section, title, labels, series_map, note, pages):
        for page_index in range(pages):
            selected = labels[page_index * 4:(page_index + 1) * 4]
            start(section, f"{title} {page_index + 1}/{pages}", note)
            years = sorted({int(day.year) for label in selected for day in series_map.get(label, {})})
            if years:
                for index, year in enumerate(years[:10]):
                    x = margin + index % 5 * 101
                    yy = 141 + index // 5 * 18
                    rect(x, yy - 5, 15, 2, HexColor(palette[(year - 2017) % len(palette)]))
                    text(x + 20, yy, str(year), 8, gray)
            else:
                text(margin, 146, "暂无历史年份可用", 8, gray)
            text(margin, 181, "横轴：月份；2026年（或报告年度）加粗；8—21天短缺口虚线连接，超过21天断开。", 8, gray)
            if not selected:
                para("本页保留在固定版式中；当前快照没有该图表的可用记录。", 210, 10, gray)
            for index, label in enumerate(selected):
                by_year = defaultdict(dict)
                for day, value in series_map.get(label, {}).items():
                    by_year[str(day.year)][day] = value
                seasonal_chart(label, by_year, margin + index % 2 * 266, 210 + index // 2 * 285, note=note)

    # 01 — inventory categories and MNPJ.
    start("01", "本周库存概览", f"库存 {previous} 对比 {current}；实际到港与旧表需按各自原口径展示")
    cards = [
        ("库存总量", _fmt(total_current), f"周变 {_signed(total_current - total_previous)} 万吨"),
        (f"实际到港 W{actual_iso[1]:02d}", _fmt(actual_current) if actual_current is not None else "—", f"周变 {_signed(actual_current - actual_previous) if actual_current is not None and actual_previous is not None else None}"),
        ("模板/修订", f"{TEMPLATE_VERSION} / R{revision_no}", "数据快照已冻结"),
    ]
    for index, (title, value, sub) in enumerate(cards):
        x = margin + index * (content_width / 3 + 3)
        w = content_width / 3 - 7
        rect(x, 143, w, 84, light)
        text(x + 12, 164, title, 10, gray)
        text(x + 12, 196, value, 20, navy, True)
        text(x + 12, 215, sub, 8, gray)
    para("本页先判断总量和大类方向。负值表示降库，正值表示垒库；库存下降不直接等同钢厂消耗增加。", 250, 10.5)
    text(margin, 342, "图1  库存大类变化", 12, navy, True)
    bars([(item["label"], item["delta"]) for item in inventory_total], 360, 125)
    table(["种类", "上期", "本期", "增减量"], [[item["label"], _fmt(item["previous"]), _fmt(item["current"]), _signed(item["delta"])] for item in inventory_total], 507, [130, 120, 120, content_width - 370], 24)
    para("MNPJ是纽曼粉、麦克粉、PB粉、金布巴粉的粉矿子组合，不能与四类形态再次相加。", 700, 9, gray)
    category_history = _category_series()
    category_history["MNPJ"] = _mnpj_series(history_inventory_rows).get("MNPJ", {})
    season_page("01", "矿种大类季节性", categories + ["MNPJ"], category_history, "原表15港范围；MNPJ为粉矿子组合，不重复计入总库存", 2)

    # 02 — four grade buckets and their historical panels.
    start("02", "品位分析与大类数据对照", "四档品位绝对库存；历史旧三档与新版四档按来源字段衔接")
    grade_rows = _change([row for row in data.get("inventory_grade", []) if row.get("scope_type") == "total" and row.get("category") == "全品种"], "grade")
    text(margin, 145, "图2  四档品位周变化", 12, navy, True)
    bars([(item["label"], item["delta"]) for item in grade_rows], 164, 145)
    if grade_rows:
        table(["品位档", "上期", "本期", "增减量"], change_rows(grade_rows, 6), 337, [150, 120, 120, content_width - 390], 24)
    else:
        para("当前快照没有完整的总计品位档；明细仍保留，缺失不补零。", 350, 10, gray)
    text(margin, 532, "全样本大类对照", 12, navy, True)
    table(["大类", "上期", "本期", "增减量"], [[item["label"], _fmt(item["previous"]), _fmt(item["current"]), _signed(item["delta"])] for item in inventory_total], 554, [150, 120, 120, content_width - 390], 24)
    para("表与图均使用高品、中高品、中低品、低品绝对库存；球团、块矿、粉矿、精粉属于形态维度，不把非球合并成一项。", 720, 9, gray)
    grade_history = _grade_series()
    season_page("02", "四档品位｜历史与近期走势", ["高品", "中高品", "中低品", "低品"], grade_history, "高品旧口径60%以上、新口径64%以上；中高品自近期单列，口径切换处断线", 1)

    # 03 — mainstream versus non-mainstream.
    start("03", "主流与非主流｜库存结构", "主流沿用系统24项字典；非主流=同周库存总量－主流")
    main_change = _change([row for row in inventory_rows if row.get("mainstream_status") == "主流"], "mainstream_status")
    main_value = {"previous": sum(item["previous"] for item in main_change if item["label"] == "主流"), "current": sum(item["current"] for item in main_change if item["label"] == "主流")}
    main_value["delta"] = main_value["current"] - main_value["previous"]
    non_value = {"previous": total_previous - main_value["previous"], "current": total_current - main_value["current"]}
    non_value["delta"] = non_value["current"] - non_value["previous"]
    scope_rows = [["主流", _fmt(main_value["previous"]), _fmt(main_value["current"]), _signed(main_value["delta"])], ["非主流", _fmt(non_value["previous"]), _fmt(non_value["current"]), _signed(non_value["delta"])]]
    bars([("主流", main_value["delta"]), ("非主流", non_value["delta"])], 148, 88)
    table(["范围", "上期", "本期", "增减量"], scope_rows, 268, [150, 120, 120, content_width - 390], 27)
    main_share_previous = main_value["previous"] / total_previous * 100 if total_previous else None
    main_share_current = main_value["current"] / total_current * 100 if total_current else None
    para(f"主流占比由 {_fmt(main_share_previous)}% 变为 {_fmt(main_share_current)}%；历史曲线按各期可取得的系统分类汇总，成员数量变化处断线。", 430, 10.5)
    para("先看主流/非主流结构，再看下一章的具体品种贡献；主流标签绑定快照中的规则版本，不随未来字典更新而改写历史报告。", 535, 10, gray)
    mainstream_history = _mainstream_series()
    season_page("03", "主流与非主流历史走势", ["主流", "非主流"], mainstream_history, "历史按系统各期分类；覆盖项数变化处断线，不把历史强行当作固定24项篮子", 1)

    # 04 — all products and the system mainstream comparison.
    product_changes = _change(inventory_rows, "report_product")
    start("04", "全品种增减排名", "全品种页面只出现一次；MNPJ在此作为粉矿子组合说明")
    rank_items = [(item["label"], item["delta"]) for item in sorted(product_changes, key=lambda item: item["delta"])[:6] + sorted(product_changes, key=lambda item: item["delta"], reverse=True)[:6]]
    bars(rank_items, 148, 260, 250)
    table(["品种", "上期", "本期", "增减量"], change_rows(product_changes, 14), 446, [170, 115, 115, content_width - 400], 23, 8)
    mnpj_changes = [item for item in product_changes if item["label"] in {"纽曼粉", "麦克粉", "PB粉", "金布巴粉"}]
    para("MNPJ：上期 %s 万吨，本期 %s 万吨，周变 %s 万吨。" % (_fmt(sum(item["previous"] for item in mnpj_changes)), _fmt(sum(item["current"] for item in mnpj_changes)), _signed(sum(item["delta"] for item in mnpj_changes))), 780, 8.5, gray)
    from . import data_visualization as dv
    mainstream_labels = list(dv.MAINSTREAM_PRODUCT_ORDER)
    mainstream_product_rows = []
    for label in mainstream_labels:
        matching = next((item for item in product_changes if item["label"] == label), None)
        if matching:
            mainstream_product_rows.append(matching)
    for page_index in range(2):
        start("04", f"主流品种数据对照 {page_index + 1}/2", "系统24项主流完整列示；库存单位：万吨；顺序与系统字典一致")
        rows = mainstream_product_rows[page_index * 12:(page_index + 1) * 12]
        table(["品种", "上期", "本期", "增减量"], change_rows(rows, 12), 156, [170, 115, 115, content_width - 400], 27)
        if rows:
            down = min(rows, key=lambda item: item["delta"])
            up = max(rows, key=lambda item: item["delta"])
            para(f"本页最大降库：{down['label']} {_signed(down['delta'])} 万吨；最大累库：{up['label']} {_signed(up['delta'])} 万吨。", 535, 9.5)
        para("附录按同一24项逐一绘图；没有可靠历史的品种显示空图与覆盖说明，不为了画满多年强行拼接。", 650, 9, gray)

    # 05 — port overview, history, matrices, basis and spread.
    start("05", "港口总览｜变化及品种贡献", "13港重点展示；沿江仅看舟山、太仓、江阴；港差以日照为基准")
    filtered_port_rows = [item for item in port_rows if item["label"] in displayed_ports]
    bars([(item["label"], item["delta"]) for item in filtered_port_rows], 148, 250, 220)
    port_table_rows = []
    for item in filtered_port_rows:
        port_table_rows.append([item["label"], _fmt(item["previous"]), _fmt(item["current"]), _signed(item["delta"])])
    table(["港口", "上期", "本期", "增减量"], port_table_rows, 424, [160, 115, 115, content_width - 390], 23, 8)
    para("港口总量来自港口×品种明细汇总；逐港页面再拆分品种、形态、品位和主流属性。南通、福州不在重点展示范围，原表数据仍保留。", 760, 8.7, gray)
    port_history = _port_series()
    season_page("05", "重点港口总库存季节性", displayed_ports, port_history, "13个重点港口，每港一图；总库存绝对水平，不细分品种", 4)

    matrix_products = ["PB粉", "卡粉", "巴混", "纽曼粉", "麦克粉", "金布巴粉", "超特粉", "混合粉", "罗伊山粉", "SP10粉", "PB块", "纽曼块"]

    def matrix_cell(x, y, w, h, value, scale=25, label=None):
        if value is None:
            fill = light
            display = "—"
        else:
            strength = min(abs(float(value)) / scale, 1)
            base_color = red if value > 0 else teal
            fill = Color(1 - (1 - base_color.red) * (0.15 + 0.65 * strength), 1 - (1 - base_color.green) * (0.15 + 0.65 * strength), 1 - (1 - base_color.blue) * (0.15 + 0.65 * strength))
            display = label or _signed(value, 1)
        rect(x, y, w - 2, h - 2, fill)
        centered(x + (w - 2) / 2, y + h * 0.65, display, 7.5, ink)

    for page_index in range(2):
        selected = matrix_products[page_index * 6:(page_index + 1) * 6]
        start("05", f"港口×品种库存变化 {page_index + 1}/2", "本期减上期；万吨；蓝绿为降库，橙色为累库；仅展示13个重点港口")
        x0 = margin + 74
        cell_width = (content_width - 74) / max(len(selected), 1)
        for index, product in enumerate(selected):
            centered(x0 + index * cell_width + (cell_width - 2) / 2, 153, product, 7.5, gray)
        row_index = 0
        for port in displayed_ports:
            text(margin, 169 + row_index * 30 + 19, port, 8.5)
            for index, product in enumerate(selected):
                current_value = _port_product_value(port, product, current)
                previous_value = _port_product_value(port, product, previous)
                value = current_value - previous_value if current_value is not None and previous_value is not None else None
                matrix_cell(x0 + index * cell_width, 169 + row_index * 30, cell_width, 30, value)
            row_index += 1
        para("格内数值是库存变化量；没有对应样本或任一期为空时显示“—”，不把未知品种分摊到其他品种。", 650, 9, gray)

    price_rows = data.get("prices") or []
    target_week = int(str(snapshot["report_week"]).split("W")[-1])

    def _short_port(value):
        text_value = str(value or "")
        return text_value.replace("港", "")

    def _price(port, product, week_no):
        aliases = {"连云港": {"连云港", "连云"}, "日照": {"日照", "日照港"}}
        wanted_ports = aliases.get(port, {port, f"{port}港"})
        candidates = [row for row in price_rows if _short_port(row.get("port")) in {_short_port(item) for item in wanted_ports} and row.get("product") == product and int(row.get("business_week") or -1) == week_no]
        return candidates[0] if candidates else None

    def _basis(row):
        if not row:
            return None
        value = _number(row.get("basis"))
        if value is not None:
            return value
        standardized = _number(row.get("standardized_spot_price"))
        futures = _number(row.get("futures_close"))
        return standardized - futures if standardized is not None and futures is not None else None

    def _wet(row):
        return _number(row.get("wet_spot_price")) if row else None

    price_products = ["PB粉", "BRBF", "FMG混合粉", "IOC6", "卡拉加斯粉", "纽曼粉", "罗伊山粉", "超特粉", "金布巴粉", "麦克粉", "SP10粉", "昆巴粉", "乌克兰精粉", "卡拉拉精粉"]
    price_ports = ["日照", "青岛", "岚山", "连云港", "江阴", "太仓", "京唐", "曹妃甸"]
    start("05", "港口×品种基差变化", "基差＝标准化现货价－系统I0参考价；单位：元/标准化吨；“—”表示没有可比报价")
    x0 = margin + 100
    cell_width = (content_width - 100) / len(price_ports)
    for index, port in enumerate(price_ports):
        centered(x0 + index * cell_width + (cell_width - 2) / 2, 153, port, 7.5, gray)
    for row_index, product in enumerate(price_products):
        yy = 169 + row_index * 27
        text(margin, yy + 18, product, 8)
        for index, port in enumerate(price_ports):
            now_row = _price(port, product, target_week)
            old_row = _price(port, product, target_week - 1)
            now_basis, old_basis = _basis(now_row), _basis(old_row)
            delta = now_basis - old_basis if now_basis is not None and old_basis is not None else None
            matrix_cell(x0 + index * cell_width, yy, cell_width, 27, delta, scale=16)
    para("基差只描述现货与盘面的相对变化；这里不把港差混称为基差，报价缺失不补零。", 585, 9.5, gray)

    start("05", "港口×品种港差", "港差＝本港同品种湿吨现货价－日照同品种湿吨现货价；括号为周变化")
    for index, port in enumerate(price_ports):
        centered(x0 + index * cell_width + (cell_width - 2) / 2, 151, port, 7.5, gray)
    for row_index, product in enumerate(price_products):
        yy = 166 + row_index * 34
        text(margin, yy + 20, product, 8)
        ref_now, ref_old = _price("日照", product, target_week), _price("日照", product, target_week - 1)
        for index, port in enumerate(price_ports):
            now_row, old_row = _price(port, product, target_week), _price(port, product, target_week - 1)
            spread = _wet(now_row) - _wet(ref_now) if _wet(now_row) is not None and _wet(ref_now) is not None else None
            old_spread = _wet(old_row) - _wet(ref_old) if _wet(old_row) is not None and _wet(ref_old) is not None else None
            spread_delta = spread - old_spread if spread is not None and old_spread is not None else None
            label = "—" if spread is None else f"{spread:+.0f}\n({spread_delta:+.0f})" if spread_delta is not None else f"{spread:+.0f}\n(—)"
            rect(x0 + index * cell_width, yy, cell_width - 2, 32, light)
            centered(x0 + index * cell_width + (cell_width - 2) / 2, yy + 13, label.split("\n")[0], 7.5, ink)
            centered(x0 + index * cell_width + (cell_width - 2) / 2, yy + 26, label.split("\n")[1] if "\n" in label else "", 7, teal if spread_delta is not None and spread_delta < 0 else red)
    para("日照列为基准0；日照缺少报价时该品种整行不计算。港差未扣运费，不等于调货净收益。", 671, 9.5, gray)

    start("05", "库存、基差与港差交叉观察", "同品种跨指标对应；港差以日照为基准；不把相关性写成因果")
    examples = [("纽曼粉", "江阴"), ("PB粉", "江阴"), ("巴混", "青岛"), ("卡粉", "日照"), ("SP10粉", "青岛"), ("卡拉拉精粉", "连云港")]
    price_alias = {"纽曼粉": "纽曼粉", "PB粉": "PB粉", "巴混": "BRBF", "卡粉": "卡拉加斯粉", "SP10粉": "SP10粉", "卡拉拉精粉": "卡拉拉精粉"}
    y = 145
    for product, port in examples:
        text(margin, y, f"{product} / {port}", 11, navy, True)
        port_now = _port_product_value(port, product, current)
        port_old = _port_product_value(port, product, previous)
        now_row, old_row = _price(port, price_alias[product], target_week), _price(port, price_alias[product], target_week - 1)
        ref_now, ref_old = _price("日照", price_alias[product], target_week), _price("日照", price_alias[product], target_week - 1)
        if now_row:
            spread = _wet(now_row) - _wet(ref_now) if _wet(now_row) is not None and _wet(ref_now) is not None else None
            old_spread = _wet(old_row) - _wet(ref_old) if _wet(old_row) is not None and _wet(ref_old) is not None else None
            spread_delta = spread - old_spread if spread is not None and old_spread is not None else None
            para(f"库存 {_fmt(port_old)} → {_fmt(port_now)} 万吨（周变 {_signed(port_now - port_old) if port_now is not None and port_old is not None else '—'}）；现货 {_fmt(_wet(now_row))} 元/湿吨；基差 {_fmt(_basis(now_row))} 元/标准化吨；港差 {_fmt(spread)} 元/湿吨，周变 {_signed(spread_delta)}。", y + 12, 8.8)
        else:
            para("港口库存事实可取得，但本期没有可比报价；价格项保留为“—”。", y + 12, 8.8, gray)
        y += 91
    para("交叉观察用于定位库存发生地与相对价格方向，不能单独推出需求、调货或利润结论。", 720, 9, gray)

    # Per-port details: one stable page per displayed port.
    grade_labels = ["高品", "中高品", "中低品", "低品"]
    for port in displayed_ports:
        start("05", f"{port}｜品种库存明细", "重点展示港口；单位：万吨；港口总量先于品种贡献阅读")
        now_total, old_total = _port_total_value(port, current), _port_total_value(port, previous)
        text(margin, 145, f"本期 {_fmt(now_total)}    上期 {_fmt(old_total)}    净变化 {_signed(now_total - old_total) if now_total is not None and old_total is not None else '—'}", 13, navy, True)
        current_port_rows = [row for row in port_facts if row.get("port_name") == port]
        port_product_rows = _change(current_port_rows, "product")
        top_rows = change_rows(port_product_rows, 8)
        top_labels = {row["label"] for row in sorted(port_product_rows, key=lambda item: (abs(item["delta"]), item["label"]), reverse=True)[:8]}
        remainder = [row for row in port_product_rows if row["label"] not in top_labels]
        remainder_row = []
        if remainder:
            remainder_previous = sum(row["previous"] for row in remainder)
            remainder_current = sum(row["current"] for row in remainder)
            remainder_row = [["其余品种合计", _fmt(remainder_previous), _fmt(remainder_current), _signed(remainder_current - remainder_previous)]]
        table(["品种", "上期", "本期", "增减量"], top_rows + remainder_row, 177, [170, 115, 115, content_width - 400], 24, 8)
        text(margin, 448, "矿种形态与MNPJ", 11, navy, True)
        shape_rows = []
        for category in categories:
            old_value = sum(_number(row.get("value")) or 0 for row in port_facts if row.get("port_name") == port and row.get("week_start") == previous and row.get("category") == category)
            new_value = sum(_number(row.get("value")) or 0 for row in port_facts if row.get("port_name") == port and row.get("week_start") == current and row.get("category") == category)
            shape_rows.append([category, _fmt(old_value), _fmt(new_value), _signed(new_value - old_value)])
        mnpj_values_old = [_port_product_value(port, product, previous) for product in ("纽曼粉", "麦克粉", "PB粉", "金布巴粉")]
        mnpj_values_new = [_port_product_value(port, product, current) for product in ("纽曼粉", "麦克粉", "PB粉", "金布巴粉")]
        mnpj_old = sum(mnpj_values_old) if all(value is not None for value in mnpj_values_old) else None
        mnpj_new = sum(mnpj_values_new) if all(value is not None for value in mnpj_values_new) else None
        shape_rows.append(["MNPJ（粉矿子组合）", _fmt(mnpj_old), _fmt(mnpj_new), _signed(mnpj_new - mnpj_old) if mnpj_old is not None and mnpj_new is not None else "—"])
        table(["分类", "上期", "本期", "增减量"], shape_rows, 470, [170, 115, 115, content_width - 400], 23, 8)
        text(margin, 641, "品位与主流范围", 10.5, navy, True)
        grade_table = []
        for grade in grade_labels:
            old_value, new_value = _port_grade_value(port, grade, previous), _port_grade_value(port, grade, current)
            grade_table.append([grade, _fmt(old_value), _fmt(new_value), _signed(new_value - old_value) if old_value is not None and new_value is not None else "—"])
        table(["分档", "上期", "本期", "增减量"], grade_table, 660, [170, 115, 115, content_width - 400], 20, 7.7)

    # 06 — actual arrival, estimated arrival and legacy apparent demand.
    start("06", "实际到港统计｜最新可用周对照", f"{actual_period}；来源方47港实际统计，保留真实周次")
    actual_country = []
    for dimension in ["总计", "澳大利亚", "巴西", "南非", "印度"]:
        old_value = _actual_dimension_total(actual_previous_week, "country", dimension)
        new_value = _actual_dimension_total(actual_current_week, "country", dimension)
        if old_value is not None or new_value is not None:
            actual_country.append([dimension, _fmt(old_value), _fmt(new_value), _signed(new_value - old_value) if old_value is not None and new_value is not None else "—"])
    table(["来源", "上期", "本期", "增减量"], actual_country, 151, [155, 105, 105, content_width - 365], 27, 8.5)
    if actual_current is None:
        para("报告周没有47港实际到港总计；系统保留独立板块并显示缺失，不回填预计/估算到港。", 430, 10, gray)
    else:
        para(f"47港实际到港本期 {_fmt(actual_current)} 万吨，周变 {_signed(actual_current - actual_previous) if actual_previous is not None else '—'} 万吨。该数值来自来源方到达统计，不等同卸货入库或钢厂实际消耗。", 430, 10)
    port_arrival_changes = []
    for row in data.get("arrival_actual", []):
        if row.get("slice_type") == "country" and row.get("dimension") == "总计" and row.get("scope_type") == "port" and row.get("value") is not None and row.get("week_start") == actual_current_week:
            old = next((item.get("value") for item in data.get("arrival_actual", []) if item.get("slice_type") == "country" and item.get("dimension") == "总计" and item.get("scope_type") == "port" and item.get("port_name") == row.get("port_name") and item.get("week_start") == actual_previous_week), None)
            if old is not None:
                port_arrival_changes.append((row.get("port_name") or "未知", float(row["value"]) - float(old)))
    port_arrival_changes.sort(key=lambda item: abs(item[1]), reverse=True)
    text(margin, 525, "到港增减较大的港口（绝对变化前6）", 11, navy, True)
    bars(port_arrival_changes[:6], 550, 145, 260)
    para("国家、品种、货种品位是同一47港到港总量的不同切片，不把三张表相加；45港、26港合计不混入47港总计。", 735, 8.8, gray)
    actual_history = _actual_series()
    season_page("06", "实际到港｜近期走势", ["47港到港"], actual_history, "当前已入库的实际到港历史按月份定位；没有多年序列时标注近期走势", 1)

    start("06", "实际到港结构｜品种与形态", f"{actual_period}；47港实际到港；品种未知单列")
    product_rows = []
    actual_product_dimensions = sorted({row.get("dimension") for row in data.get("arrival_actual", []) if row.get("slice_type") == "product" and row.get("dimension") and row.get("dimension") != "总计"})
    for dimension in actual_product_dimensions:
        old_value = _actual_dimension_total(actual_previous_week, "product", dimension)
        new_value = _actual_dimension_total(actual_current_week, "product", dimension)
        if old_value is not None or new_value is not None:
            product_rows.append((dimension, old_value, new_value, new_value - old_value if old_value is not None and new_value is not None else None))
    product_rows.sort(key=lambda item: abs(item[3] or 0), reverse=True)
    table(["品种（变化前10）", "上期", "本期", "增减量"], [[name, _fmt(old), _fmt(new), _signed(delta)] for name, old, new, delta in product_rows[:10]], 151, [155, 105, 105, content_width - 365], 25, 8)
    shape_arrival = []
    for dimension in ["粉矿 汇总", "块矿 汇总", "球团 汇总", "精粉 汇总", "未知 汇总"]:
        old_value = _actual_dimension_total(actual_previous_week, "form", dimension)
        new_value = _actual_dimension_total(actual_current_week, "form", dimension)
        if old_value is not None or new_value is not None:
            shape_arrival.append([dimension.replace(" 汇总", ""), _fmt(old_value), _fmt(new_value), _signed(new_value - old_value) if old_value is not None and new_value is not None else "—"])
    table(["货种形态", "上期", "本期", "增减量"], shape_arrival, 480, [155, 105, 105, content_width - 365], 25, 8)
    unknown = next((item for item in product_rows if item[0] == "未知"), None)
    if unknown and actual_previous:
        para(f"未知品种占比：上期 {_fmt((unknown[1] or 0) / actual_previous * 100)}%，本期 {_fmt((unknown[2] or 0) / (actual_current or 1) * 100)}%；未知不归入非主流，也不按比例分摊。", 650, 9)
    para("两期都有数值才计算变化；空白不补0。到港货种品位沿用来源方分档，与库存四档不是同一分类。", 735, 8.8, gray)

    estimated_changes = _change(data.get("arrival_estimated"), "source_country")
    demand_changes = _change(data.get("legacy_apparent_demand"), "source_country")
    start("06", "预计／估算到港与原口径表需", "W周对照；澳洲来源预计、巴西卡粉估算和旧表需继续分开")
    arrival_rows = [[f"预计/估算-{item['label']}", _fmt(item["previous"]), _fmt(item["current"]), _signed(item["delta"])] for item in estimated_changes]
    table(["口径", "上期", "本期", "增减量"], arrival_rows[:12], 151, [180, 110, 110, content_width - 400], 25, 8.3)
    text(margin, 525, "旧表需对照", 11, navy, True)
    if demand_changes:
        table(["来源", "上期", "本期", "增减量"], change_rows(demand_changes, 8), 550, [180, 110, 110, content_width - 400], 23, 8)
    else:
        para("当前报告周没有旧表需记录；不使用实际到港事实重新计算旧表需。", 560, 10, gray)
    para("旧表需仍沿用预计/估算到港＋上周库存－本周库存的原系统路径；实际到港只作为独立事实展示。", 735, 8.8, gray)
    estimated_history = _rows_series(data.get("history_arrival_estimated"), lambda row: row.get("source_country") or "未知")
    season_page("06", "预计／估算到港｜历史与近期走势", sorted(estimated_history), estimated_history, "澳洲来源预计与巴西卡粉估算分别记录；历史来源规则变化处断线", 1)

    start("06", "系统估算表需｜变化组成", "表需＝预计/估算到港＋库存减少项；不把实际到港代入旧公式")
    composition_rows = []
    for source in sorted({row.get("source_country") or "未知" for row in data.get("legacy_apparent_demand", [])}):
        demand_item = next((item for item in demand_changes if item["label"] == source), None)
        arrival_item = next((item for item in estimated_changes if item["label"] == source), None)
        if demand_item:
            composition_rows.append([source + "表需", _fmt(demand_item["previous"]), _fmt(demand_item["current"]), _signed(demand_item["delta"])])
        if arrival_item:
            composition_rows.append([source + "预计/估算到港", _fmt(arrival_item["previous"]), _fmt(arrival_item["current"]), _signed(arrival_item["delta"])])
    table(["指标", "上期", "本期", "增减量"], composition_rows[:10], 151, [190, 110, 110, content_width - 410], 26, 8.3)
    para("到港变化和库存减少项是表需变化的组成，不能单独把任一项当作钢厂实际消耗。", 500, 10.5)
    demand_history = _rows_series(data.get("history_apparent_demand"), lambda row: row.get("source_country") or "未知")
    if demand_history:
        text(margin, 580, "历史表需覆盖", 11, navy, True)
        coverage_rows = [[label, str(len(values)), str(min(values).isoformat()), str(max(values).isoformat())] for label, values in sorted(demand_history.items())]
        table(["来源", "观察期数", "起始", "末期"], coverage_rows[:8], 606, [180, 110, 130, content_width - 420], 23, 8)
    else:
        para("当前快照没有旧表需历史序列。", 600, 10, gray)

    # 07 — definitions and data boundaries.
    start("07", "数据口径与阅读说明", "按业务周阅读；不同范围和日期的指标不直接相加推算消耗")
    definition_rows = [
        ["库存及品种结构", "原表15个样本港", f"{current}／{previous}", "库存时点统计"],
        ["重点港口展示", "13港；沿江3港", f"{current}／{previous}", "不含南通、福州"],
        ["基差与港差", "期现可比报价组合", "价格周次", "基差折标；港差湿吨"],
        ["实际到港", "全国47港", "来源实际周", "到达统计，非卸货入库"],
        ["预计／估算到港", "澳洲来源预计＋巴西卡粉", f"{current}／{previous}", "来源预计／发运推算"],
        ["系统估算表需", "原系统可计算品种", f"{current}／{previous}", "旧公式保留"],
    ]
    table(["指标", "统计范围", "本期／对比期", "数据性质"], definition_rows, 151, [120, 145, 115, content_width - 380], 42, 8.2)
    notes = [
        "实际到港、预计到港、估算到港分别记录；47港实际到港不进入旧表需计算。",
        "MNPJ＝纽曼粉＋麦克粉＋PB粉＋金布巴粉，只是粉矿子组合；主流/非主流使用系统24项字典。",
        "基差＝标准化现货价－系统I0参考价；港差＝本港同品种湿吨现货价－日照同品种湿吨现货价。",
        "空白、未知、不同统计切片和汇总层级均保留，不把空白转成零，不把总计与明细相加。",
        "快照哈希：%s；生成身份：%s / 模板%s / R%s。" % (snapshot["input_sha256"][:16], snapshot["report_week"], TEMPLATE_VERSION, revision_no),
    ]
    y = 470
    for note in notes:
        y = para("• " + note, y, 9.5) + 15
    para("；".join(snapshot["validation"].get("warnings", [])) or "本次快照未发现阻止生成的缺失项。", 720, 8.8, gray)

    # Appendix — 24 fixed system mainstream products, four charts per page.
    main_series = _rows_series(
        history_inventory_rows,
        lambda row: row.get("report_product") or "未知",
    )
    canonical_series = {}
    for label in mainstream_labels:
        values = main_series.get(label, {})
        canonical_series[label] = values
    season_page("附录", "主流库存｜季节性与近期走势", mainstream_labels, canonical_series, "系统24项完整覆盖；各年份分线，报告年度加粗；横轴为月份、纵轴为万吨", 6)

    if page_no != 46:
        raise RuntimeError(f"V1.0渲染页数异常：{page_no}")
    c.save()


def generate_report(report_week: str, template_version: str = TEMPLATE_VERSION, created_by: str = "", force_new_revision: bool = False) -> Dict[str, Any]:
    template = register_builtin_templates()
    if template_version != template["version"]:
        raise ValueError(f"模板版本不可用: {template_version}")
    snapshot = build_report_snapshot(report_week, template["id"], created_by)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(
            cur,
            """SELECT r.*, a.file_path, a.sha256, a.file_size
               FROM dv_report_runs r LEFT JOIN dv_report_artifacts a ON a.run_id = r.id
               WHERE r.report_week = ? AND r.template_id = ? AND r.snapshot_id = ? AND r.status = 'succeeded'
               ORDER BY r.revision_no ASC, r.id ASC LIMIT 1""",
            (snapshot["report_week"], template["id"], snapshot["snapshot_id"]),
        ).fetchone()
        if existing and not force_new_revision:
            result = dict(existing)
            result["run_id"] = result.get("id")
            return result
        max_row = db._exec(
            cur,
            "SELECT MAX(revision_no) AS max_revision FROM dv_report_runs WHERE report_week = ? AND template_id = ?",
            (snapshot["report_week"], template["id"]),
        ).fetchone()
        revision_no = int(max_row["max_revision"] or 0) + 1
        job_id = uuid.uuid4().hex
        run_id = db._last_insert_id(
            cur,
            """INSERT INTO dv_report_runs
               (report_week, template_id, template_version, snapshot_id, revision_no, job_id, status, created_by)
               VALUES (?, ?, ?, ?, ?, ?, 'generating', ?)""",
            (snapshot["report_week"], template["id"], template["version"], snapshot["snapshot_id"], revision_no, job_id, created_by),
        )
        conn.commit()
    output_dir = db.DATA_DIR / "iron_ore_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"铁矿石周报_{snapshot['report_week']}_模板{template['version']}_R{revision_no}.pdf"
    try:
        _render_pdf(snapshot, output_path, revision_no)
        content = output_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with db.connect() as conn:
            cur = conn.cursor()
            db._exec(cur, "UPDATE dv_report_runs SET status = 'succeeded', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (run_id,))
            db._exec(
                cur,
                "INSERT INTO dv_report_artifacts (run_id, file_path, sha256, file_size) VALUES (?, ?, ?, ?)",
                (run_id, str(output_path), digest, len(content)),
            )
            row = db._exec(
                cur,
                "SELECT r.*, a.file_path, a.sha256, a.file_size FROM dv_report_runs r JOIN dv_report_artifacts a ON a.run_id = r.id WHERE r.id = ?",
                (run_id,),
            ).fetchone()
            conn.commit()
        result = dict(row)
        result["run_id"] = result.get("id")
        return result
    except Exception as exc:
        with db.connect() as conn:
            cur = conn.cursor()
            db._exec(cur, "UPDATE dv_report_runs SET status = 'failed', error_message = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?", (str(exc), run_id))
            conn.commit()
        raise


router = APIRouter()


@router.get("/data-visualization/reports/templates")
async def report_templates(user=Depends(_report_user)):
    _require_report_view(user)
    register_builtin_templates()
    with db.connect() as conn:
        cur = conn.cursor()
        rows = _rows(cur, "SELECT * FROM dv_report_templates ORDER BY template_key, version")
    return {"templates": rows}


@router.get("/data-visualization/reports/readiness")
async def report_readiness_api(report_week: str = Query(...), user=Depends(_report_user)):
    _require_report_view(user)
    return report_readiness(report_week)


@router.get("/data-visualization/reports/runs")
async def report_runs(limit: int = Query(default=30), user=Depends(_report_user)):
    _require_report_view(user)
    limit = max(1, min(int(limit), 100))
    with db.connect() as conn:
        cur = conn.cursor()
        rows = _rows(
            cur,
            """SELECT r.*, a.file_path, a.sha256, a.file_size
               FROM dv_report_runs r LEFT JOIN dv_report_artifacts a ON a.run_id = r.id
               ORDER BY r.created_at DESC, r.id DESC LIMIT ?""",
            (limit,),
        )
    return {"runs": rows}


@router.post("/data-visualization/reports/generate")
async def report_generate_api(payload: ReportGenerateRequest, user=Depends(_report_user)):
    _require_report_edit(user)
    try:
        return generate_report(payload.report_week, payload.template_version, user.get("name", ""), payload.force_new_revision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/data-visualization/reports/jobs/{job_id}")
async def report_job(job_id: str, user=Depends(_report_user)):
    _require_report_view(user)
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """SELECT r.*, a.file_path, a.sha256, a.file_size
               FROM dv_report_runs r LEFT JOIN dv_report_artifacts a ON a.run_id = r.id
               WHERE r.job_id = ?""",
            (job_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="周报任务不存在")
    return dict(row)


@router.get("/data-visualization/reports/{run_id}/download")
async def report_download(run_id: int, user=Depends(_report_user)):
    _require_report_view(user)
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """SELECT a.file_path, r.report_week, r.template_version, r.revision_no
               FROM dv_report_artifacts a JOIN dv_report_runs r ON r.id = a.run_id
               WHERE a.run_id = ?""",
            (run_id,),
        ).fetchone()
    if not row or not row["file_path"] or not Path(row["file_path"]).exists():
        raise HTTPException(status_code=404, detail="周报文件不存在")
    filename = f"铁矿石周报_{row['report_week']}_模板{row['template_version']}_R{row['revision_no']}.pdf"
    return FileResponse(row["file_path"], media_type="application/pdf", filename=filename)
