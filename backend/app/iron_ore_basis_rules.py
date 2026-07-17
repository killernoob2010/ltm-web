"""Versioned business rules for iron-ore spot-futures basis calculations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
from typing import Any


DEFAULT_RULE_PATH = Path(__file__).with_name("iron_ore_basis_rule_v1.json")


class RuleConfigurationError(ValueError):
    """Raised when a checked-in basis rule pack is incomplete or ambiguous."""


@dataclass(frozen=True)
class ProductRule:
    product: str
    parameter_year: int
    parameter_type: str
    fe: float
    sio2: float
    al2o3: float
    phosphorus: float
    sulfur: float
    h2o: float
    sulfur_defaulted: bool
    brand_adjustment: float
    parameter_source: str
    parameter_version: str


@dataclass(frozen=True)
class IndicatorMapping:
    indicator_code: str
    indicator_name: str
    port: str
    product: str
    ebc_price_fe: float | None
    price_proxy_indicator: str | None
    price_parameter_spec_diff: bool
    ebc_original_port: str | None


@dataclass(frozen=True)
class BasisRulePack:
    rule_version: str
    effective_from: date
    products: dict[str, ProductRule]
    indicators: dict[str, IndicatorMapping]


_PRODUCT_NUMBERS = (
    "fe",
    "sio2",
    "al2o3",
    "phosphorus",
    "sulfur",
    "h2o",
    "brand_adjustment",
)


def _required_text(row: dict[str, Any], field: str, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuleConfigurationError(f"{context} 缺少字段 {field}")
    return value.strip()


def _required_number(row: dict[str, Any], field: str, context: str) -> float:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleConfigurationError(f"{context} 缺少数值字段 {field}")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise RuleConfigurationError(f"{context} 数值字段 {field} 无效")
    return parsed


def load_rule_pack(path: str | Path = DEFAULT_RULE_PATH) -> BasisRulePack:
    rule_path = Path(path)
    try:
        payload = json.loads(rule_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleConfigurationError(f"无法读取规则文件: {rule_path.name}") from exc

    rule_version = _required_text(payload, "rule_version", "规则包")
    try:
        effective_from = date.fromisoformat(_required_text(payload, "effective_from", "规则包"))
    except ValueError as exc:
        raise RuleConfigurationError("规则包 effective_from 无效") from exc

    product_rules: dict[str, ProductRule] = {}
    for raw in payload.get("products", []):
        product = _required_text(raw, "product", "品种参数")
        if product in product_rules:
            raise RuleConfigurationError(f"重复品种参数: {product}")
        numbers = {field: _required_number(raw, field, product) for field in _PRODUCT_NUMBERS}
        parameter_year = raw.get("parameter_year")
        if isinstance(parameter_year, bool) or not isinstance(parameter_year, int):
            raise RuleConfigurationError(f"{product} 缺少数值字段 parameter_year")
        product_rules[product] = ProductRule(
            product=product,
            parameter_year=parameter_year,
            parameter_type=_required_text(raw, "parameter_type", product),
            fe=numbers["fe"],
            sio2=numbers["sio2"],
            al2o3=numbers["al2o3"],
            phosphorus=numbers["phosphorus"],
            sulfur=numbers["sulfur"],
            h2o=numbers["h2o"],
            sulfur_defaulted=bool(raw.get("sulfur_defaulted", False)),
            brand_adjustment=numbers["brand_adjustment"],
            parameter_source=_required_text(raw, "parameter_source", product),
            parameter_version=_required_text(raw, "parameter_version", product),
        )

    indicator_mappings: dict[str, IndicatorMapping] = {}
    port_products: set[tuple[str, str]] = set()
    for raw in payload.get("indicators", []):
        code = _required_text(raw, "indicator_code", "指标映射")
        if code in indicator_mappings:
            raise RuleConfigurationError(f"重复指标编码: {code}")
        port = _required_text(raw, "port", code)
        product = _required_text(raw, "product", code)
        mapping_key = (port, product)
        if mapping_key in port_products:
            raise RuleConfigurationError(f"重复港口品种映射: {port}|{product}")
        if product not in product_rules:
            raise RuleConfigurationError(f"指标映射引用未知品种: {product}")
        ebc_price_fe = raw.get("ebc_price_fe")
        if ebc_price_fe is not None:
            ebc_price_fe = _required_number(raw, "ebc_price_fe", code)
        indicator_mappings[code] = IndicatorMapping(
            indicator_code=code,
            indicator_name=_required_text(raw, "indicator_name", code),
            port=port,
            product=product,
            ebc_price_fe=ebc_price_fe,
            price_proxy_indicator=raw.get("price_proxy_indicator") or None,
            price_parameter_spec_diff=bool(raw.get("price_parameter_spec_diff", False)),
            ebc_original_port=raw.get("ebc_original_port") or None,
        )
        port_products.add(mapping_key)

    if not product_rules:
        raise RuleConfigurationError("规则包没有品种参数")
    if not indicator_mappings:
        raise RuleConfigurationError("规则包没有指标映射")
    return BasisRulePack(rule_version, effective_from, product_rules, indicator_mappings)


def load_active_rule_pack(business_date: date) -> BasisRulePack:
    pack = load_rule_pack()
    if business_date < pack.effective_from:
        raise RuleConfigurationError(
            f"规则 {pack.rule_version} 在 {business_date.isoformat()} 尚未生效"
        )
    return pack
