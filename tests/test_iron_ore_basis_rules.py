import json
import os
import sys
from datetime import date

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.iron_ore_basis_rules import (  # noqa: E402
    RuleConfigurationError,
    load_active_rule_pack,
    load_rule_pack,
)


def _minimal_rule_payload():
    return {
        "rule_version": "I2312 / F-DCE I004-2021",
        "effective_from": "2026-07-13",
        "products": [
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
                "parameter_source": "主要矿山货物指标-2026年!2",
                "parameter_version": "2026-典型值",
            }
        ],
        "indicators": [
            {
                "indicator_code": "ID-PB",
                "indicator_name": "PB粉：61.5%Fe：日照港",
                "port": "日照港",
                "product": "PB粉",
                "ebc_price_fe": 0.615,
                "price_proxy_indicator": None,
                "price_parameter_spec_diff": False,
                "ebc_original_port": "日照港",
            }
        ],
    }


def test_default_rule_pack_contains_audited_current_scope():
    pack = load_active_rule_pack(date(2027, 1, 5))

    assert pack.rule_version == "I2312 / F-DCE I004-2021"
    assert pack.effective_from == date(2026, 7, 13)
    assert len(pack.products) == 15
    assert len(pack.indicators) == 104
    assert pack.products["PB粉"].brand_adjustment == 15
    assert set(pack.products) == {
        "卡拉拉精粉", "卡拉加斯粉", "乌克兰精粉", "昆巴粉", "BRBF",
        "纽曼粉", "PB粉", "IOC6", "麦克粉", "罗伊山粉", "金布巴粉",
        "SP10粉", "FMG混合粉", "杨迪粉", "超特粉",
    }
    assert len({(item.port, item.product) for item in pack.indicators.values()}) == 104


def test_rule_pack_rejects_dates_before_api_effective_date():
    with pytest.raises(RuleConfigurationError, match="尚未生效"):
        load_active_rule_pack(date(2026, 7, 10))


def test_rule_pack_rejects_duplicate_indicator_codes(tmp_path):
    payload = _minimal_rule_payload()
    payload["indicators"].append(dict(payload["indicators"][0]))
    path = tmp_path / "duplicate-indicator.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuleConfigurationError, match="重复指标编码"):
        load_rule_pack(path)


def test_rule_pack_rejects_duplicate_port_product_mapping(tmp_path):
    payload = _minimal_rule_payload()
    duplicate = dict(payload["indicators"][0])
    duplicate["indicator_code"] = "ID-PB-SECOND"
    payload["indicators"].append(duplicate)
    path = tmp_path / "duplicate-mapping.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuleConfigurationError, match="重复港口品种映射"):
        load_rule_pack(path)


def test_rule_pack_rejects_missing_numeric_parameter(tmp_path):
    payload = _minimal_rule_payload()
    payload["products"][0]["h2o"] = None
    path = tmp_path / "missing-h2o.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuleConfigurationError, match="h2o"):
        load_rule_pack(path)
