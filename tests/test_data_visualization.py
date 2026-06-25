"""数据可视化管理 — 单元测试"""
import sys, os
# Make backend/app a package by adding backend/ to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.data_visualization import compute_business_week, _to_date, _to_float, integrate_mysteel_files, _local_mysteel_files

from datetime import date


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


def test_integrate_mysteel_files_local_templates():
    files = _local_mysteel_files()
    assert len(files) == 3
    result = integrate_mysteel_files(files)
    metrics = result["summary"]["metrics"]
    sample = result["points"][0]
    assert metrics["inventory"] > 0
    assert metrics["shipment"] > 0
    assert metrics["apparent_demand"] > 0
    assert sample["week_end"]
    assert sample["business_year"]
    assert sample["business_week"]
    assert sample["week_label"]
    assert not result["summary"]["warnings"]

print("All tests passed!")
