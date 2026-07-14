#!/usr/bin/env python3
"""Replay audited iron-ore basis details through the pure calculation engine."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402
from backend.app.iron_ore_basis_calculation import BasisCalculationInput, calculate_basis_row  # noqa: E402
from backend.app.iron_ore_basis_rules import BasisRulePack, IndicatorMapping, ProductRule  # noqa: E402
from scripts.snapshot_iron_ore_basis_rule import DETAIL_HEADERS  # noqa: E402


COMPARE_FIELDS = (
    "fe_adjustment",
    "sio2_adjustment",
    "al2o3_adjustment",
    "phosphorus_adjustment",
    "sulfur_adjustment",
    "quality_adjustment",
    "dry_spot_price",
    "standardized_spot_price",
    "basis",
)


def _from_workbook_data(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sheet = payload["sheets"]["计算明细"]
    headers = sheet["headers"]
    return [dict(zip(headers, row)) for row in sheet["rows"]]


def _from_database() -> list[dict[str, Any]]:
    aliases = {
        "business_date": "日期",
        "week_label": "周次",
        "business_year": "年份",
        "port": "港口",
        "product": "品种",
        "ebc_indicator_code": "EBC指标编码",
        "ebc_indicator_name": "EBC原始指标名",
        "ebc_price_fe": "EBC价格规格Fe",
        "wet_spot_price": "湿吨现货价",
        "parameter_year": "参数年份",
        "parameter_type": "参数类型",
        "fe": "Fe",
        "sio2": "SiO2",
        "al2o3": "Al2O3",
        "phosphorus": "P",
        "sulfur": "S",
        "h2o": "H2O",
        "sulfur_defaulted": "S缺失默认0",
        "price_proxy_indicator": "价格代理指标",
        "price_parameter_spec_diff": "价格规格与参数规格不同",
        "fe_adjustment_x": "Fe调整X",
        "brand_adjustment": "品牌升贴水",
        "futures_series": "期货序列",
        "futures_close": "主力连续收盘价",
        "fe_adjustment": "Fe升贴水",
        "sio2_adjustment": "SiO2升贴水",
        "al2o3_adjustment": "Al2O3升贴水",
        "phosphorus_adjustment": "P升贴水",
        "sulfur_adjustment": "S升贴水",
        "quality_adjustment": "质量升贴水",
        "dry_spot_price": "干吨现货价",
        "standardized_spot_price": "标准化现货价",
        "basis": "基差",
        "data_status": "数据状态",
        "note": "备注",
        "rule_version": "规则版本",
        "parameter_source": "参数来源",
        "parameter_version": "参数版本",
        "ebc_original_port": "EBC原始港口名",
    }
    columns = list(aliases)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            f"SELECT {', '.join(columns)} FROM iron_ore_basis_details ORDER BY business_date, port, product",
        ).fetchall()
    return [{aliases[column]: row[column] for column in columns} for row in rows]


def _calculate(row: dict[str, Any]):
    business_date = date.fromisoformat(str(row["日期"]))
    product = ProductRule(
        product=str(row["品种"]),
        parameter_year=int(row["参数年份"]),
        parameter_type=str(row["参数类型"]),
        fe=float(row["Fe"]),
        sio2=float(row["SiO2"]),
        al2o3=float(row["Al2O3"]),
        phosphorus=float(row["P"]),
        sulfur=float(row["S"]),
        h2o=float(row["H2O"]),
        sulfur_defaulted=bool(row["S缺失默认0"]),
        brand_adjustment=float(row["品牌升贴水"]),
        parameter_source=str(row["参数来源"]),
        parameter_version=str(row["参数版本"]),
    )
    mapping = IndicatorMapping(
        indicator_code=str(row["EBC指标编码"]),
        indicator_name=str(row["EBC原始指标名"]),
        port=str(row["港口"]),
        product=str(row["品种"]),
        ebc_price_fe=float(row["EBC价格规格Fe"]) if row["EBC价格规格Fe"] not in (None, "") else None,
        price_proxy_indicator=str(row["价格代理指标"] or "") or None,
        price_parameter_spec_diff=bool(row["价格规格与参数规格不同"]),
        ebc_original_port=str(row["EBC原始港口名"] or "") or None,
    )
    pack = BasisRulePack(
        rule_version=str(row["规则版本"]),
        effective_from=date(2024, 1, 1),
        products={product.product: product},
        indicators={mapping.indicator_code: mapping},
    )
    return calculate_basis_row(
        BasisCalculationInput(
            business_date=business_date,
            mapping=mapping,
            product_rule=product,
            wet_spot_price=float(row["湿吨现货价"]),
            futures_close=float(row["主力连续收盘价"]),
            futures_series=str(row["期货序列"]),
        ),
        pack,
    )


def verify(rows: list[dict[str, Any]], tolerance: float = 1e-9) -> dict[str, Any]:
    mismatches = 0
    duplicate_keys = 0
    seen: set[str] = set()
    maximum_error = 0.0
    field_map = {
        "fe_adjustment": "Fe升贴水",
        "sio2_adjustment": "SiO2升贴水",
        "al2o3_adjustment": "Al2O3升贴水",
        "phosphorus_adjustment": "P升贴水",
        "sulfur_adjustment": "S升贴水",
        "quality_adjustment": "质量升贴水",
        "dry_spot_price": "干吨现货价",
        "standardized_spot_price": "标准化现货价",
        "basis": "基差",
    }
    for row in rows:
        calculated = _calculate(row)
        key = str(calculated.result["business_key"])
        if key in seen:
            duplicate_keys += 1
        seen.add(key)
        for field in COMPARE_FIELDS:
            actual = float(calculated.detail[field])
            expected = float(row[field_map[field]])
            error = abs(actual - expected)
            maximum_error = max(maximum_error, error)
            if error > tolerance:
                mismatches += 1
    return {
        "details": len(rows),
        "business_keys": len(seen),
        "duplicate_business_keys": duplicate_keys,
        "formula_mismatches": mismatches,
        "maximum_error": maximum_error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook-data", type=Path)
    args = parser.parse_args()
    rows = _from_workbook_data(args.workbook_data) if args.workbook_data else _from_database()
    summary = verify(rows)
    print(json.dumps(summary, ensure_ascii=False))
    return 1 if summary["duplicate_business_keys"] or summary["formula_mismatches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
