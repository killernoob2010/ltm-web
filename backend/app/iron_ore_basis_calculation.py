"""Pure calculation functions for iron-ore spot-futures basis rows."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import math

from .iron_ore_basis_rules import BasisRulePack, IndicatorMapping, ProductRule


class BasisCalculationError(ValueError):
    """Raised when a basis row cannot be calculated from complete valid inputs."""


@dataclass(frozen=True)
class QualityAdjustments:
    fe: float
    sio2: float
    al2o3: float
    phosphorus: float
    sulfur: float

    @property
    def total(self) -> float:
        return _clean(self.fe + self.sio2 + self.al2o3 + self.phosphorus + self.sulfur)


@dataclass(frozen=True)
class BasisCalculationInput:
    business_date: date
    mapping: IndicatorMapping
    product_rule: ProductRule
    wet_spot_price: float
    futures_close: float
    futures_series: str = "I0"


@dataclass(frozen=True)
class BasisCalculation:
    result: dict[str, object]
    detail: dict[str, object]


def _clean(value: float) -> float:
    rounded = round(float(value), 10)
    return 0.0 if rounded == -0.0 else rounded


def _finite(name: str, value: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise BasisCalculationError(f"{name} 不是有效数值")
    return parsed


def calculate_quality_adjustments(
    *,
    fe: float,
    sio2: float,
    al2o3: float,
    phosphorus: float,
    sulfur: float,
    fe_adjustment_x: float = 1.5,
) -> QualityAdjustments:
    fe = _finite("Fe", fe)
    sio2 = _finite("SiO2", sio2)
    al2o3 = _finite("Al2O3", al2o3)
    phosphorus = _finite("P", phosphorus)
    sulfur = _finite("S", sulfur)
    x = _finite("Fe调整X", fe_adjustment_x)

    if fe < 0.60:
        fe_adjustment = -10 * x + ((fe - 0.60) / 0.001) * (x + 1.5)
    elif fe <= 0.635:
        fe_adjustment = ((fe - 0.61) / 0.001) * x
    else:
        fe_adjustment = 25 * x + ((fe - 0.635) / 0.001) * (x + 1.0)

    if sio2 < 0.045:
        sio2_adjustment = ((0.045 - sio2) / 0.001) * 0.5
    elif sio2 <= 0.065:
        sio2_adjustment = -((sio2 - 0.045) / 0.001)
    else:
        sio2_adjustment = -20 - ((sio2 - 0.065) / 0.001) * 1.5

    priced_al2o3 = max(al2o3, 0.01)
    if priced_al2o3 <= 0.025:
        al2o3_adjustment = ((0.025 - priced_al2o3) / 0.001) * 2.0
    else:
        al2o3_adjustment = -((priced_al2o3 - 0.025) / 0.001) * 3.0

    if phosphorus <= 0.001:
        phosphorus_adjustment = 0.0
    elif phosphorus <= 0.0012:
        phosphorus_adjustment = -((phosphorus - 0.001) / 0.0001) * 10.0
    else:
        phosphorus_adjustment = -20.0 - ((phosphorus - 0.0012) / 0.0001) * 15.0

    if sulfur <= 0.0003:
        sulfur_adjustment = 0.0
    elif sulfur <= 0.001:
        sulfur_adjustment = -((sulfur - 0.0003) / 0.0001)
    else:
        sulfur_adjustment = -7.0 - ((sulfur - 0.001) / 0.0001) * 5.0

    return QualityAdjustments(
        fe=_clean(fe_adjustment),
        sio2=_clean(sio2_adjustment),
        al2o3=_clean(al2o3_adjustment),
        phosphorus=_clean(phosphorus_adjustment),
        sulfur=_clean(sulfur_adjustment),
    )


def _source_hash(value: BasisCalculationInput, rule_pack: BasisRulePack) -> str:
    canonical = {
        "business_date": value.business_date.isoformat(),
        "indicator_code": value.mapping.indicator_code,
        "wet_spot_price": float(value.wet_spot_price),
        "futures_series": value.futures_series,
        "futures_close": float(value.futures_close),
        "rule_version": rule_pack.rule_version,
        "parameter_version": value.product_rule.parameter_version,
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def calculate_basis_row(
    value: BasisCalculationInput,
    rule_pack: BasisRulePack,
) -> BasisCalculation:
    if value.business_date < rule_pack.effective_from:
        raise BasisCalculationError("业务日期早于规则生效日期")
    if value.mapping.product != value.product_rule.product:
        raise BasisCalculationError("指标映射与品种参数不一致")
    wet_spot_price = _finite("湿吨现货价", value.wet_spot_price)
    futures_close = _finite("期货收盘价", value.futures_close)
    h2o = _finite("H2O", value.product_rule.h2o)
    if h2o < 0 or h2o >= 1:
        raise BasisCalculationError("H2O 必须大于等于0且小于1")
    if wet_spot_price <= 0 or futures_close <= 0:
        raise BasisCalculationError("现货价和期货收盘价必须大于0")

    adjustments = calculate_quality_adjustments(
        fe=value.product_rule.fe,
        sio2=value.product_rule.sio2,
        al2o3=value.product_rule.al2o3,
        phosphorus=value.product_rule.phosphorus,
        sulfur=value.product_rule.sulfur,
        fe_adjustment_x=1.5,
    )
    dry_spot_price = wet_spot_price / (1 - h2o)
    standardized_spot_price = (
        dry_spot_price - adjustments.total - value.product_rule.brand_adjustment
    )
    basis = standardized_spot_price - futures_close
    iso_year, iso_week, _ = value.business_date.isocalendar()
    week_label = f"{iso_year} W{iso_week:02d}"
    business_key = "|".join(
        (
            value.business_date.isoformat(),
            value.mapping.port,
            value.mapping.product,
            rule_pack.rule_version,
            value.product_rule.parameter_version,
        )
    )
    source_sha256 = _source_hash(value, rule_pack)

    common = {
        "business_key": business_key,
        "business_date": value.business_date.isoformat(),
        "week_label": week_label,
        "business_year": value.business_date.year,
        "port": value.mapping.port,
        "product": value.mapping.product,
        "wet_spot_price": wet_spot_price,
        "futures_series": value.futures_series,
        "futures_close": futures_close,
        "basis": _clean(basis),
        "data_status": "有效",
        "rule_version": rule_pack.rule_version,
        "parameter_version": value.product_rule.parameter_version,
        "source_workbook_name": "API:EBC+Sina",
        "source_workbook_sha256": source_sha256,
    }
    result = {
        **common,
        "business_week": iso_week,
        "quality_adjustment": adjustments.total,
        "brand_adjustment": value.product_rule.brand_adjustment,
        "standardized_spot_price": _clean(standardized_spot_price),
    }
    detail = {
        **common,
        "ebc_indicator_code": value.mapping.indicator_code,
        "ebc_indicator_name": value.mapping.indicator_name,
        "ebc_price_fe": value.mapping.ebc_price_fe,
        "parameter_year": value.product_rule.parameter_year,
        "parameter_type": value.product_rule.parameter_type,
        "fe": value.product_rule.fe,
        "sio2": value.product_rule.sio2,
        "al2o3": value.product_rule.al2o3,
        "phosphorus": value.product_rule.phosphorus,
        "sulfur": value.product_rule.sulfur,
        "h2o": h2o,
        "sulfur_defaulted": int(value.product_rule.sulfur_defaulted),
        "price_proxy_indicator": value.mapping.price_proxy_indicator,
        "price_parameter_spec_diff": int(value.mapping.price_parameter_spec_diff),
        "fe_adjustment_x": 1.5,
        "brand_adjustment": value.product_rule.brand_adjustment,
        "fe_adjustment": adjustments.fe,
        "sio2_adjustment": adjustments.sio2,
        "al2o3_adjustment": adjustments.al2o3,
        "phosphorus_adjustment": adjustments.phosphorus,
        "sulfur_adjustment": adjustments.sulfur,
        "quality_adjustment": adjustments.total,
        "dry_spot_price": _clean(dry_spot_price),
        "standardized_spot_price": _clean(standardized_spot_price),
        "note": "API自动同步",
        "parameter_source": value.product_rule.parameter_source,
        "ebc_original_port": value.mapping.ebc_original_port,
    }
    return BasisCalculation(result=result, detail=detail)
