import os
import sys
from datetime import date

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.iron_ore_basis_calculation import (  # noqa: E402
    BasisCalculationError,
    BasisCalculationInput,
    calculate_basis_row,
    calculate_quality_adjustments,
)
from app.iron_ore_basis_rules import BasisRulePack, IndicatorMapping, ProductRule  # noqa: E402


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("fe", 0.565, -120.0),
        ("fe", 0.623, 19.5),
        ("fe", 0.6554, 88.5),
        ("sio2", 0.022, 11.5),
        ("sio2", 0.075, -35.0),
        ("al2o3", 0.0011, 30.0),
        ("al2o3", 0.0345, -28.5),
        ("phosphorus", 0.0011, -10.0),
        ("phosphorus", 0.0013, -35.0),
        ("sulfur", 0.00035, -0.5),
        ("sulfur", 0.001, -7.0),
    ],
)
def test_quality_adjustments_match_audited_history(field, value, expected):
    values = {
        "fe": 0.61,
        "sio2": 0.045,
        "al2o3": 0.025,
        "phosphorus": 0.001,
        "sulfur": 0.0003,
    }
    values[field] = value

    adjustments = calculate_quality_adjustments(**values, fe_adjustment_x=1.5)

    assert getattr(adjustments, field) == pytest.approx(expected)


def _rule_pack(h2o=0.09):
    product = ProductRule(
        product="超特粉",
        parameter_year=2026,
        parameter_type="典型值",
        fe=0.565,
        sio2=0.064,
        al2o3=0.032,
        phosphorus=0.0006,
        sulfur=0.00035,
        h2o=h2o,
        sulfur_defaulted=False,
        brand_adjustment=0,
        parameter_source="主要矿山货物指标-2026年!27",
        parameter_version="2026-典型值",
    )
    mapping = IndicatorMapping(
        indicator_code="ID00103968",
        indicator_name="超特粉：56.5%Fe：品牌价格：曹妃甸港：FMG（日）",
        port="曹妃甸港",
        product="超特粉",
        ebc_price_fe=0.565,
        price_proxy_indicator=None,
        price_parameter_spec_diff=False,
        ebc_original_port="曹妃甸港",
    )
    return BasisRulePack(
        rule_version="I2312 / F-DCE I004-2021",
        effective_from=date(2026, 7, 13),
        products={product.product: product},
        indicators={mapping.indicator_code: mapping},
    ), product, mapping


def test_calculate_basis_row_matches_audited_formula_chain():
    pack, product, mapping = _rule_pack()

    calculated = calculate_basis_row(
        BasisCalculationInput(
            business_date=date(2026, 7, 13),
            mapping=mapping,
            product_rule=product,
            wet_spot_price=937,
            futures_close=1002,
        ),
        pack,
    )

    assert calculated.detail["fe_adjustment"] == pytest.approx(-120)
    assert calculated.detail["sio2_adjustment"] == pytest.approx(-19)
    assert calculated.detail["al2o3_adjustment"] == pytest.approx(-21)
    assert calculated.detail["phosphorus_adjustment"] == pytest.approx(0)
    assert calculated.detail["sulfur_adjustment"] == pytest.approx(-0.5)
    assert calculated.detail["quality_adjustment"] == pytest.approx(-160.5)
    assert calculated.detail["dry_spot_price"] == pytest.approx(1029.6703296703297)
    assert calculated.result["standardized_spot_price"] == pytest.approx(1190.1703296703297)
    assert calculated.result["basis"] == pytest.approx(188.1703296703297)
    assert calculated.result["business_key"].startswith("2026-07-13|曹妃甸港|超特粉|")
    assert calculated.result["source_workbook_name"] == "API:EBC+Sina"
    assert len(calculated.result["source_workbook_sha256"]) == 64
    assert calculated.detail["source_workbook_sha256"] == calculated.result["source_workbook_sha256"]


def test_calculate_basis_row_rejects_invalid_moisture():
    pack, product, mapping = _rule_pack(h2o=1)

    with pytest.raises(BasisCalculationError, match="H2O"):
        calculate_basis_row(
            BasisCalculationInput(
                business_date=date(2026, 7, 13),
                mapping=mapping,
                product_rule=product,
                wet_spot_price=937,
                futures_close=1002,
            ),
            pack,
        )
