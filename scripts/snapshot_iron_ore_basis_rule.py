#!/usr/bin/env python3
"""Build a deterministic current-rule snapshot from audited basis detail rows."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import db  # noqa: E402


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


def _records(headers: list[str], rows: Iterable[list[Any]]) -> Iterable[dict[str, Any]]:
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(f"计算明细列数不一致: expected={len(headers)} actual={len(row)}")
        yield dict(zip(headers, row))


def _date_text(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def build_rule_payload(headers: list[str], rows: Iterable[list[Any]]) -> dict[str, Any]:
    required = set(DETAIL_HEADERS)
    missing = sorted(required - set(headers))
    if missing:
        raise ValueError(f"计算明细缺少字段: {', '.join(missing)}")

    latest_products: dict[str, tuple[str, dict[str, Any]]] = {}
    indicator_identities: dict[str, tuple[Any, ...]] = {}
    mappings: dict[str, tuple[str, dict[str, Any]]] = {}
    port_products: dict[tuple[str, str], str] = {}
    rule_versions: set[str] = set()

    for row in _records(headers, rows):
        business_date = _date_text(row["日期"])
        product = str(row["品种"]).strip()
        port = str(row["港口"]).strip()
        rule_version = str(row["规则版本"]).strip()
        if not product or not port or not rule_version:
            raise ValueError("计算明细存在空品种、港口或规则版本")
        rule_versions.add(rule_version)

        product_payload = {
            "product": product,
            "parameter_year": int(row["参数年份"]),
            "parameter_type": str(row["参数类型"]).strip(),
            "fe": float(row["Fe"]),
            "sio2": float(row["SiO2"]),
            "al2o3": float(row["Al2O3"]),
            "phosphorus": float(row["P"]),
            "sulfur": float(row["S"]),
            "h2o": float(row["H2O"]),
            "sulfur_defaulted": bool(row["S缺失默认0"]),
            "brand_adjustment": float(row["品牌升贴水"]),
            "parameter_source": str(row["参数来源"]).strip(),
            "parameter_version": str(row["参数版本"]).strip(),
        }
        previous_product = latest_products.get(product)
        if previous_product is None or business_date > previous_product[0]:
            latest_products[product] = (business_date, product_payload)
        elif business_date == previous_product[0] and product_payload != previous_product[1]:
            raise ValueError(f"同日品种参数冲突: {product}|{business_date}")

        code = str(row["EBC指标编码"] or "").strip()
        if not code:
            continue
        mapping = {
            "indicator_code": code,
            "indicator_name": str(row["EBC原始指标名"]).strip(),
            "port": port,
            "product": product,
            "ebc_price_fe": float(row["EBC价格规格Fe"]) if row["EBC价格规格Fe"] not in (None, "") else None,
            "price_proxy_indicator": str(row["价格代理指标"] or "").strip() or None,
            "price_parameter_spec_diff": bool(row["价格规格与参数规格不同"]),
            "ebc_original_port": str(row["EBC原始港口名"] or "").strip() or None,
        }
        identity = (
            code,
            mapping["indicator_name"],
            port,
            product,
            mapping["ebc_price_fe"],
            mapping["ebc_original_port"],
        )
        if code in indicator_identities and indicator_identities[code] != identity:
            raise ValueError(f"EBC指标映射冲突: {code}")
        mapping_key = (port, product)
        if mapping_key in port_products and port_products[mapping_key] != code:
            raise ValueError(f"EBC港口品种映射冲突: {port}|{product}")
        indicator_identities[code] = identity
        port_products[mapping_key] = code
        previous_mapping = mappings.get(code)
        if previous_mapping is None or business_date > previous_mapping[0]:
            mappings[code] = (business_date, mapping)
        elif business_date == previous_mapping[0] and mapping != previous_mapping[1]:
            raise ValueError(f"同日EBC指标映射冲突: {code}|{business_date}")

    if len(rule_versions) != 1:
        raise ValueError(f"规则版本不唯一: {sorted(rule_versions)}")
    return {
        "rule_version": next(iter(rule_versions)),
        "effective_from": "2026-07-13",
        "products": [latest_products[key][1] for key in sorted(latest_products)],
        "indicators": sorted(
            (item[1] for item in mappings.values()),
            key=lambda item: (item["port"], item["product"], item["indicator_code"]),
        ),
    }


def _load_workbook_data(path: Path) -> tuple[list[str], list[list[Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    detail = payload["sheets"]["计算明细"]
    return detail["headers"], detail["rows"]


def _load_database_data() -> tuple[list[str], list[list[Any]]]:
    columns = [
        "business_date", "week_label", "business_year", "port", "product",
        "ebc_indicator_code", "ebc_indicator_name", "ebc_price_fe", "wet_spot_price",
        "parameter_year", "parameter_type", "fe", "sio2", "al2o3", "phosphorus",
        "sulfur", "h2o", "sulfur_defaulted", "price_proxy_indicator",
        "price_parameter_spec_diff", "fe_adjustment_x", "brand_adjustment",
        "futures_series", "futures_close", "fe_adjustment", "sio2_adjustment",
        "al2o3_adjustment", "phosphorus_adjustment", "sulfur_adjustment",
        "quality_adjustment", "dry_spot_price", "standardized_spot_price", "basis",
        "data_status", "note", "rule_version", "parameter_source", "parameter_version",
        "ebc_original_port",
    ]
    with db.connect() as conn:
        cur = conn.cursor()
        fetched = db._exec(
            cur,
            f"SELECT {', '.join(columns)} FROM iron_ore_basis_details ORDER BY business_date, port, product",
        ).fetchall()
    return DETAIL_HEADERS, [[row[column] for column in columns] for row in fetched]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook-data", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    headers, rows = _load_workbook_data(args.workbook_data) if args.workbook_data else _load_database_data()
    payload = build_rule_payload(headers, rows)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.output:
        args.output.write_bytes(encoded)
    print(
        json.dumps(
            {
                "rule_version": payload["rule_version"],
                "products": len(payload["products"]),
                "indicator_mappings": len(payload["indicators"]),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "output": str(args.output) if args.output else None,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
