import os
import sys
from datetime import datetime

import pytest
from openpyxl import Workbook


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db
from app.iron_ore_basis_import import import_basis_workbook, validate_basis_workbook


RESULT_HEADERS = [
    "日期", "周次", "年份", "港口", "品种", "湿吨现货价", "质量升贴水",
    "品牌升贴水", "主力连续收盘价", "基差", "数据状态",
]
DETAIL_HEADERS = [
    "日期", "周次", "年份", "港口", "品种", "EBC指标编码", "EBC原始指标名",
    "EBC价格规格Fe", "湿吨现货价", "参数年份", "参数类型", "Fe", "SiO2",
    "Al2O3", "P", "S", "H2O", "S缺失默认0", "价格代理指标",
    "价格规格与参数规格不同", "Fe调整X", "品牌升贴水", "期货序列",
    "主力连续收盘价", "Fe升贴水", "SiO2升贴水", "Al2O3升贴水",
    "P升贴水", "S升贴水", "质量升贴水", "干吨现货价", "标准化现货价",
    "基差", "数据状态", "备注", "规则版本", "参数来源", "参数版本",
    "EBC原始港口名",
]


def make_workbook(path, *, mismatch=False, duplicate=False, include_detail=True):
    wb = Workbook()
    result_ws = wb.active
    result_ws.title = "期现数据"
    result_ws.append(RESULT_HEADERS)
    result_rows = [
        [datetime(2026, 7, 10), "2026 W28", 2026, "京唐港", "昆巴粉", 746, 28, 0, 751.5, -12.01028806584361, "有效"],
        [datetime(2026, 7, 10), "2026 W28", 2026, "日照港", "PB粉", 780, 15, 15, 751.5, 90, "有效"],
    ]
    for row in result_rows:
        result_ws.append(row)
    if duplicate:
        result_ws.append(result_rows[0])

    if include_detail:
        detail_ws = wb.create_sheet("计算明细")
        detail_ws.append(DETAIL_HEADERS)
        detail_rows = [
            [datetime(2026, 7, 10), "2026 W28", 2026, "京唐港", "昆巴粉", "ID-KUMBA", "南非粉：63%Fe：京唐港", 0.63, 746, 2026, "典型值", 0.64, 0.04, 0.02, 0.0008, 0.0002, 0.03, False, "南非粉63%Fe", True, 1.5, 0, "I0", 751.5, 30, 0, 0, -2, 0, 28, 769.0721649484536, 739.4897119341564, -12.01028806584361, "有效", "代理映射", "I2312 / F-DCE I004-2021", "主要矿山货物指标-2026年!1", "2026-典型值", "京唐港"],
            [datetime(2026, 7, 10), "2026 W28", 2026, "日照港", "PB粉", "ID-PB", "PB粉：61.5%Fe：日照港", 0.615, 780, 2026, "典型值", 0.615, 0.04, 0.023, 0.0011, 0.0002, 0.08, False, None, False, 1.5, 15, "I0", 751.5, 7.5, 0, 4, 3.5, 0, 15, 847.8260869565217, 817.5, 90, "有效", "直接映射", "I2312 / F-DCE I004-2021", "主要矿山货物指标-2026年!2", "2026-典型值", "日照港"],
        ]
        if mismatch:
            detail_rows[0][32] = -99
        for row in detail_rows:
            detail_ws.append(row)

    wb.save(path)


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "import.db")


def test_validate_basis_workbook_requires_both_sheets(tmp_path):
    path = tmp_path / "missing.xlsx"
    make_workbook(path, include_detail=False)

    with pytest.raises(ValueError, match="计算明细"):
        validate_basis_workbook(path)


def test_validate_basis_workbook_rejects_duplicate_and_mismatch(tmp_path):
    duplicate_path = tmp_path / "duplicate.xlsx"
    make_workbook(duplicate_path, duplicate=True)
    with pytest.raises(ValueError, match="重复"):
        validate_basis_workbook(duplicate_path)

    mismatch_path = tmp_path / "mismatch.xlsx"
    make_workbook(mismatch_path, mismatch=True)
    with pytest.raises(ValueError, match="不一致"):
        validate_basis_workbook(mismatch_path)


def test_validate_basis_workbook_checks_expected_sha(tmp_path):
    path = tmp_path / "valid.xlsx"
    make_workbook(path)

    with pytest.raises(ValueError, match="SHA-256"):
        validate_basis_workbook(path, expected_sha256="0" * 64)


def test_import_basis_workbook_is_transactional_and_idempotent(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    path = tmp_path / "valid.xlsx"
    make_workbook(path)

    dry_run = import_basis_workbook(path, apply=False)
    assert dry_run["result_rows"] == 2
    assert dry_run["detail_rows"] == 2
    assert dry_run["applied"] is False

    first = import_basis_workbook(path, apply=True)
    second = import_basis_workbook(path, apply=True)

    assert first["applied"] is True
    assert second["applied"] is True
    with db.connect() as conn:
        cur = conn.cursor()
        result_count = cur.execute("SELECT COUNT(*) AS c FROM iron_ore_basis_results").fetchone()["c"]
        detail_count = cur.execute("SELECT COUNT(*) AS c FROM iron_ore_basis_details").fetchone()["c"]
        linked_count = cur.execute(
            """SELECT COUNT(*) AS c
               FROM iron_ore_basis_details d
               JOIN iron_ore_basis_results r ON r.id = d.result_id
               WHERE r.business_key = d.business_key"""
        ).fetchone()["c"]

    assert result_count == 2
    assert detail_count == 2
    assert linked_count == 2
