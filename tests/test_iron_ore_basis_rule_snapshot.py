import os
import sys

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.snapshot_iron_ore_basis_rule import build_rule_payload  # noqa: E402


HEADERS = [
    "日期", "周次", "年份", "港口", "品种", "EBC指标编码", "EBC原始指标名",
    "EBC价格规格Fe", "湿吨现货价", "参数年份", "参数类型", "Fe", "SiO2",
    "Al2O3", "P", "S", "H2O", "S缺失默认0", "价格代理指标",
    "价格规格与参数规格不同", "Fe调整X", "品牌升贴水", "期货序列",
    "主力连续收盘价", "Fe升贴水", "SiO2升贴水", "Al2O3升贴水",
    "P升贴水", "S升贴水", "质量升贴水", "干吨现货价", "标准化现货价",
    "基差", "数据状态", "备注", "规则版本", "参数来源", "参数版本",
    "EBC原始港口名",
]


def _row(
    date_value,
    *,
    indicator="ID-PB",
    port="日照港",
    parameter_year=2026,
    spec_diff=False,
):
    return [
        date_value, "2026 W28", 2026, port, "PB粉", indicator,
        f"PB粉：61.5%Fe：{port}", 0.615, 780, parameter_year, "典型值",
        0.615, 0.04, 0.023, 0.0011, 0.0002, 0.08, False, "", spec_diff,
        1.5, 15, "I0", 751.5, 7.5, 0, 4, 3.5, 0, 15,
        847.8260869565217, 817.5, 66, "有效", "直接映射",
        "I2312 / F-DCE I004-2021", f"source-{parameter_year}",
        f"{parameter_year}-典型值", port,
    ]


def test_snapshot_uses_latest_product_parameters_and_unique_mapping():
    payload = build_rule_payload(HEADERS, [_row("2025-07-10", parameter_year=2025), _row("2026-07-10")])

    assert payload["effective_from"] == "2026-07-13"
    assert payload["rule_version"] == "I2312 / F-DCE I004-2021"
    assert payload["products"] == [
        {
            "product": "PB粉",
            "parameter_year": 2026,
            "parameter_type": "典型值",
            "fe": 0.615,
            "sio2": 0.04,
            "al2o3": 0.023,
            "phosphorus": 0.0011,
            "sulfur": 0.0002,
            "h2o": 0.08,
            "sulfur_defaulted": False,
            "brand_adjustment": 15.0,
            "parameter_source": "source-2026",
            "parameter_version": "2026-典型值",
        }
    ]
    assert len(payload["indicators"]) == 1
    assert payload["indicators"][0]["indicator_code"] == "ID-PB"


def test_snapshot_rejects_indicator_mapping_conflict():
    with pytest.raises(ValueError, match="映射冲突"):
        build_rule_payload(
            HEADERS,
            [_row("2026-07-10"), _row("2026-07-10", port="青岛港")],
        )


def test_snapshot_uses_latest_parameter_dependent_mapping_metadata():
    payload = build_rule_payload(
        HEADERS,
        [
            _row("2025-07-10", parameter_year=2025, spec_diff=True),
            _row("2026-07-10", parameter_year=2026, spec_diff=False),
        ],
    )

    assert payload["indicators"][0]["price_parameter_spec_diff"] is False
