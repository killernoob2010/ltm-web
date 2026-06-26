"""数据可视化管理 — 单元测试"""
import sys, os
# Make backend/app a package by adding backend/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.data_visualization import (
    compute_business_week,
    _parse_integrated_excel,
    _import_integrated_points,
    _save_integrated_points,
    _load_integrated_preview_cache,
    _save_integrated_preview_cache,
    _split_filter_values,
    _to_date,
    _to_float,
    build_integrated_workbook_bytes,
    integrate_mysteel_files,
    _local_mysteel_files,
)

from datetime import date
from openpyxl import Workbook, load_workbook
from io import BytesIO


def test_compute_business_week_w01():
    bw = compute_business_week(date(2025, 1, 1))
    assert bw["year"] == 2025, f"year expected 2025 got {bw['year']}"
    assert bw["week_no"] == 1, f"week_no expected 1 got {bw['week_no']}"

def test_compute_business_week_w02():
    bw = compute_business_week(date(2025, 1, 6))
    assert bw["year"] == 2025
    assert bw["week_no"] == 2

def test_compute_business_week_dec31():
    bw = compute_business_week(date(2024, 12, 31))
    assert bw["year"] == 2025
    assert bw["week_no"] == 1

def test_compute_business_week_dec30():
    bw = compute_business_week(date(2024, 12, 30))
    assert bw["year"] == 2025
    assert bw["week_no"] == 1

def test_to_date():
    import datetime as dtmod
    assert _to_date(dtmod.datetime(2025, 6, 15)) == date(2025, 6, 15)
    assert _to_date(date(2025, 6, 15)) == date(2025, 6, 15)
    assert _to_date("2025-06-15") is None

def test_to_float():
    assert _to_float(3.14) == 3.14
    assert _to_float(None) is None
    assert _to_float("abc") is None


def test_split_filter_values_supports_multi_select():
    assert _split_filter_values("主流,非主流") == ["主流", "非主流"]
    assert _split_filter_values(" 主流 , , 非主流 ") == ["主流", "非主流"]
    assert _split_filter_values("") == []


def test_parse_integrated_excel_reports_invalid_rows(tmp_path):
    path = tmp_path / "bad_integrated.xlsx"
    headers = [
        "统计周一", "统计周日", "业务年份", "业务周次", "周次标签", "展示日期",
        "数据类型", "来源/国家", "品种", "种类", "主流/非主流", "数值",
        "单位", "来源文件", "来源Sheet", "来源区域", "是否参与表需", "校验状态", "备注",
    ]
    row = [
        "2026-06-01", "2026-06-07", 2026, 23, "2026 W23", "2026-06-02",
        "库存", "澳洲", "PB粉", "粉矿", "主流", 100,
        "万吨", "file.xlsx", "sheet", "区域", "是", "ok", "",
    ]
    invalid_metric = list(row)
    invalid_metric[6] = "非法类型"
    invalid_value = list(row)
    invalid_value[8] = "纽曼粉"
    invalid_value[11] = "abc"

    wb = Workbook()
    ws = wb.active
    ws.title = "整合明细"
    ws.append(headers)
    ws.append(row)
    ws.append(list(row))
    ws.append(invalid_metric)
    ws.append(invalid_value)
    wb.save(path)

    result = _parse_integrated_excel(path)
    messages = [item["message"] for item in result["errors"]]
    assert result["summary"]["duplicate_key_count"] == 1
    assert any("数据类型无效" in message for message in messages)
    assert any("数值不是数字" in message for message in messages)
    assert len(result["rows"]) == 2


def test_parse_integrated_excel_accepts_arrival_metric(tmp_path):
    path = tmp_path / "arrival_integrated.xlsx"
    headers = [
        "统计周一", "统计周日", "业务年份", "业务周次", "周次标签", "展示日期",
        "数据类型", "来源/国家", "品种", "种类", "主流/非主流", "数值",
        "单位", "来源文件", "来源Sheet", "来源区域", "是否参与表需", "校验状态", "备注",
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "整合明细"
    ws.append(headers)
    ws.append([
        "2026-06-01", "2026-06-07", 2026, 23, "2026 W23", "2026-06-02",
        "到港", "澳洲", "PB粉", "粉矿", "主流", 100,
        "万吨", "file.xlsx", "sheet", "区域", "是", "ok", "",
    ])
    wb.save(path)

    result = _parse_integrated_excel(path)

    assert result["errors"] == []
    assert result["rows"][0]["metric_type"] == "arrival"
    assert result["summary"]["arrival_count"] == 1


def test_integrated_import_replaces_previous_batch(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    db.init_db()
    rows = [
        {
            "week_start": "2026-06-01",
            "week_end": "2026-06-07",
            "business_year": 2026,
            "business_week": 23,
            "week_label": "2026 W23",
            "display_date": "2026-06-02",
            "metric_type": "inventory",
            "source_country": "澳洲",
            "product": "PB粉",
            "category": "粉矿",
            "mainstream_status": "主流",
            "value": 100.0,
            "unit": "万吨",
            "source_file": "first.xlsx",
            "source_sheet": "整合明细",
            "source_section": "测试",
            "is_calculable": 1,
            "validation_status": "ok",
            "note": "",
        }
    ]
    _import_integrated_points(rows, "first.xlsx", "pytest")
    second_rows = [dict(rows[0], value=120.0, source_file="second.xlsx")]

    _import_integrated_points(second_rows, "second.xlsx", "pytest")

    with db.connect() as conn:
        cur = conn.cursor()
        point_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_integrated_points").fetchone()["c"]
        batch_count = db._exec(cur, "SELECT COUNT(*) AS c FROM dv_integration_batches").fetchone()["c"]
        point = db._exec(cur, "SELECT value, source_file FROM dv_integrated_points").fetchone()

    assert point_count == 1
    assert batch_count == 1
    assert point["value"] == 120.0
    assert point["source_file"] == "second.xlsx"


def test_save_integrated_points_merges_weekly_uploads(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    db.init_db()

    base_point = {
        "week_start": "2026-06-01",
        "week_end": "2026-06-07",
        "business_year": 2026,
        "business_week": 23,
        "week_label": "2026 W23",
        "display_date": "2026-06-02",
        "metric_type": "inventory",
        "source_country": "澳洲",
        "product": "PB粉",
        "category": "粉矿",
        "mainstream_status": "主流",
        "value": 100.0,
        "unit": "万吨",
        "source_file": "history.xlsx",
        "source_sheet": "库存",
        "source_section": "历史",
        "is_calculable": 1,
        "validation_status": "ok",
        "note": "",
    }
    _save_integrated_points([base_point], ["history.xlsx"], "pytest")

    changed_duplicate = dict(base_point, value=120.0, source_file="week1.xlsx", source_section="周度修正")
    blank_duplicate = dict(base_point, value=None, source_file="blank.xlsx", source_section="空值")
    new_week = dict(base_point, business_week=24, week_start="2026-06-08", week_end="2026-06-14",
                    week_label="2026 W24", display_date="2026-06-09", value=130.0,
                    source_file="week1.xlsx")
    batch_id = _save_integrated_points([changed_duplicate, blank_duplicate, new_week], ["week1.xlsx"], "pytest")

    with db.connect() as conn:
        cur = conn.cursor()
        points = db._exec(
            cur,
            """SELECT business_week, value, source_file, source_section
               FROM dv_integrated_points ORDER BY business_week""",
        ).fetchall()
        batch = db._exec(
            cur,
            "SELECT point_count, validation_summary FROM dv_integration_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()

    summary = __import__("json").loads(batch["validation_summary"])
    assert len(points) == 2
    assert points[0]["business_week"] == 23
    assert points[0]["value"] == 120.0
    assert points[0]["source_file"] == "week1.xlsx"
    assert points[1]["business_week"] == 24
    assert points[1]["value"] == 130.0
    assert batch["point_count"] == 2
    assert summary["inserted"] == 1
    assert summary["updated"] == 1
    assert summary["skipped"] == 1
    assert summary["skipped_blank_overwrite"] == 1


def test_integrated_export_downloads_current_full_history(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    db.init_db()

    point = {
        "week_start": "2026-06-01",
        "week_end": "2026-06-07",
        "business_year": 2026,
        "business_week": 23,
        "week_label": "2026 W23",
        "display_date": "2026-06-02",
        "metric_type": "inventory",
        "source_country": "澳洲",
        "product": "PB粉",
        "category": "粉矿",
        "mainstream_status": "主流",
        "value": 100.0,
        "unit": "万吨",
        "source_file": "history.xlsx",
        "source_sheet": "库存",
        "source_section": "历史",
        "is_calculable": 1,
        "validation_status": "ok",
        "note": "",
    }
    _save_integrated_points([point], ["history.xlsx"], "pytest")
    _save_integrated_points([
        dict(point, business_week=24, week_start="2026-06-08", week_end="2026-06-14",
             week_label="2026 W24", display_date="2026-06-09", value=130.0,
             source_file="week1.xlsx")
    ], ["week1.xlsx"], "pytest")

    workbook_bytes = build_integrated_workbook_bytes()
    wb = load_workbook(BytesIO(workbook_bytes), read_only=True)
    ws = wb["整合明细"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    assert len(rows) == 3
    assert [row[3] for row in rows[1:]] == [23, 24]


def test_integrated_preview_cache_roundtrip():
    result = {
        "rows": [{"week_start": "2026-06-01", "metric_type": "inventory"}],
        "errors": [],
        "summary": {"total_points": 1},
    }

    preview_id = _save_integrated_preview_cache(result, "historical.xlsx")
    cached = _load_integrated_preview_cache(preview_id)

    assert cached["file_name"] == "historical.xlsx"
    assert cached["rows"] == result["rows"]
    assert cached["errors"] == []
    assert cached["summary"]["total_points"] == 1


def test_integrate_mysteel_files_local_templates():
    files = _local_mysteel_files()
    assert len(files) == 3
    result = integrate_mysteel_files(files)
    metrics = result["summary"]["metrics"]
    sample = result["points"][0]
    assert metrics["inventory"] > 0
    assert metrics["shipment"] > 0
    assert metrics["arrival"] > 0
    assert metrics["apparent_demand"] > 0
    assert all(point["metric_type"] != "shipment" for point in result["points"] if "到中国" in point["source_section"])
    assert sample["week_end"]
    assert sample["business_year"]
    assert sample["business_week"]
    assert sample["week_label"]
    assert not result["summary"]["warnings"]


def test_integrate_mysteel_files_limits_fixed_mysteel_template_sections():
    result = integrate_mysteel_files(_local_mysteel_files())
    australia_arrivals = [
        point for point in result["points"]
        if point["metric_type"] == "arrival" and point["source_country"] == "澳洲"
    ]
    brazil_arrivals = [
        point for point in result["points"]
        if point["metric_type"] == "arrival" and point["source_country"] == "巴西"
    ]

    assert len(australia_arrivals) == 181
    assert len(brazil_arrivals) == 6
    assert sorted({point["week_start"] for point in brazil_arrivals}) == [
        "2026-06-22", "2026-06-29", "2026-07-06",
        "2026-07-13", "2026-07-20", "2026-07-27",
    ]

print("All tests passed!")
