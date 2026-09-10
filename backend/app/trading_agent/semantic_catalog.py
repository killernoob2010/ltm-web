"""Versioned business-semantic registry for read-only data-visualization facts.

The registry is intentionally immutable.  It describes what a dataset means and
which dimensions can be selected; it never contains SQL supplied by a model or
by a user.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Literal, Mapping


DatasetId = Literal[
    "spot_series",
    "port_inventory",
    "inventory_summary",
    "inventory_grade",
    "source_mainstream_inventory",
    "arrival_detail",
    "iron_ore_basis",
]


SENSITIVE_FIELDS = frozenset({
    "source_file", "source_sheet", "source_section", "source_row", "source_column", "source_cell",
    "source_workbook_name", "source_workbook_sha256", "mapping_version", "package_id",
})


@dataclass(frozen=True)
class DatasetSpec:
    dataset: str
    kind: str
    required_resources: tuple[str, ...]
    allowed_fields: tuple[str, ...]
    filter_keys: tuple[str, ...]
    dimension_keys: tuple[str, ...]
    measure_fields: tuple[str, ...]
    unit: str
    grain: str
    source_selector: str
    aggregation_rule: str
    missing_rule: str
    alias_version: str
    semantic_version: str
    supported_relations: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        value = asdict(self)
        for key in (
            "required_resources",
            "allowed_fields",
            "filter_keys",
            "dimension_keys",
            "measure_fields",
            "supported_relations",
        ):
            value[key] = list(value[key])
        return value


_COMMON = {
    "required_resources": ("data_visualization.display",),
    "semantic_version": "trade-semantics-v1",
}


def _spec(**kwargs) -> DatasetSpec:
    return DatasetSpec(**_COMMON, **kwargs)


DATASET_SPECS: Mapping[str, DatasetSpec] = MappingProxyType({
    "spot_series": _spec(
        dataset="spot_series",
        kind="dataset_rows",
        allowed_fields=(
            "observation_date", "period_start", "period_end", "business_year", "business_week",
            "week_label", "metric", "source_country", "product", "category", "mainstream_status",
            "product_pool", "value", "unit", "source_kind", "value_state", "source_file",
            "source_sheet", "source_section", "mapping_version", "package_id", "row_ref",
        ),
        filter_keys=(
            "metric", "years", "business_weeks", "products", "categories", "source_countries",
            "mainstream_status", "product_pool", "data_states",
        ),
        dimension_keys=(
            "business_year", "business_week", "period_start", "period_end", "source_country",
            "product", "category", "mainstream_status",
        ),
        measure_fields=("value",),
        unit="万吨",
        grain="metric_type × 业务周 × 来源/国家 × 品种 × 形态 × 主流状态",
        source_selector="dv_integrated_points:activated_legacy",
        aggregation_rule="registered_aggregate_only",
        missing_rule="preserve_value_state_and_never_fill_zero",
        alias_version="spot-alias-v1",
        supported_relations=("inventory_basis_observation", "inventory_wet_price_observation"),
    ),
    "port_inventory": _spec(
        dataset="port_inventory",
        kind="dataset_rows",
        allowed_fields=(
            "observation_date", "period_start", "week_start", "port", "region", "scope_type",
            "source_country", "product", "category", "mainstream_status", "raw_product", "value",
            "unit", "value_state", "source_file", "source_sheet", "source_row", "source_column",
            "source_cell", "mapping_version", "package_id", "row_ref",
        ),
        filter_keys=(
            "years", "business_weeks", "products", "categories", "source_countries", "ports",
            "regions", "mainstream_status", "product_pool", "scope_type", "data_states",
        ),
        dimension_keys=("observation_date", "port", "region", "scope_type", "source_country", "product", "category", "mainstream_status"),
        measure_fields=("value",),
        unit="万吨",
        grain="观察日 × scope_type × 港口/样本 × 国家 × 品种 × 形态",
        source_selector="dv_port_inventory_facts JOIN dv_source_packages(status=activated)",
        aggregation_rule="source_total_and_leaf_are_separate",
        missing_rule="preserve_value_status_and_real_zero",
        alias_version="port-inventory-alias-v1",
        supported_relations=("inventory_basis_observation", "inventory_wet_price_observation"),
    ),
    "inventory_summary": _spec(
        dataset="inventory_summary",
        kind="dataset_rows",
        allowed_fields=(
            "observation_date", "period_start", "week_start", "port", "region", "scope_type",
            "summary_metric", "value", "unit", "value_state", "source_file", "source_sheet",
            "source_row", "source_column", "source_cell", "mapping_version", "package_id", "row_ref",
        ),
        filter_keys=("years", "business_weeks", "ports", "regions", "scope_type", "summary_metrics", "data_states"),
        dimension_keys=("observation_date", "port", "region", "scope_type", "summary_metric"),
        measure_fields=("value",),
        unit="万吨",
        grain="观察日 × 港口或总样本 × metric",
        source_selector="dv_inventory_summary_facts JOIN dv_source_packages(status=activated)",
        aggregation_rule="registered_summary_members_only",
        missing_rule="preserve_value_status_and_never_create_product_dimension",
        alias_version="inventory-summary-alias-v1",
    ),
    "inventory_grade": _spec(
        dataset="inventory_grade",
        kind="dataset_rows",
        allowed_fields=(
            "observation_date", "period_start", "week_start", "port", "region", "scope_type",
            "raw_grade", "grade", "category", "value", "unit", "value_state", "source_file",
            "source_sheet", "source_row", "source_column", "source_cell", "mapping_version", "package_id", "row_ref",
        ),
        filter_keys=("years", "business_weeks", "ports", "regions", "scope_type", "grades", "categories", "data_states"),
        dimension_keys=("observation_date", "port", "region", "scope_type", "grade", "category"),
        measure_fields=("value",),
        unit="万吨",
        grain="观察日 × scope_type × 港口 × grade/category",
        source_selector="dv_inventory_grade_facts JOIN dv_source_packages(status=activated)",
        aggregation_rule="registered_grade_scheme_only",
        missing_rule="preserve_value_status_and_never_invent_product_dimension",
        alias_version="inventory-grade-alias-v1",
    ),
    "source_mainstream_inventory": _spec(
        dataset="source_mainstream_inventory",
        kind="dataset_rows",
        allowed_fields=(
            "observation_date", "period_start", "week_start", "port", "region", "scope_type",
            "raw_product", "product", "category", "source_country", "value", "unit", "value_state",
            "source_file", "source_sheet", "source_row", "source_column", "source_cell", "mapping_version", "package_id", "row_ref",
        ),
        filter_keys=("years", "business_weeks", "ports", "regions", "scope_type", "products", "categories", "source_countries", "data_states"),
        dimension_keys=("observation_date", "port", "region", "scope_type", "source_country", "product", "category"),
        measure_fields=("value",),
        unit="万吨",
        grain="观察日 × 港口/样本 × 原表品种",
        source_selector="dv_inventory_mainstream_facts JOIN dv_source_packages(status=activated)",
        aggregation_rule="source_mainstream_rows_only",
        missing_rule="preserve_value_status_and_source_label",
        alias_version="source-mainstream-alias-v1",
    ),
    "arrival_detail": _spec(
        dataset="arrival_detail",
        kind="dataset_rows",
        allowed_fields=(
            "observation_date", "period_start", "week_start", "arrival_kind", "method", "port",
            "scope_type", "slice_type", "dimension", "product", "category", "grade", "source_country",
            "mainstream_status", "value", "unit", "value_state", "source_file", "source_sheet",
            "source_row", "source_column", "source_cell", "mapping_version", "package_id", "row_ref",
        ),
        filter_keys=(
            "years", "business_weeks", "ports", "regions", "scope_type", "slice_type", "arrival_kind",
            "products", "categories", "grades", "source_countries", "mainstream_status", "product_pool", "data_states",
        ),
        dimension_keys=("observation_date", "arrival_kind", "port", "scope_type", "slice_type", "dimension", "product", "category", "grade", "source_country", "mainstream_status"),
        measure_fields=("value",),
        unit="万吨",
        grain="arrival_kind × 业务周/观察日 × scope_type × 港口 × slice_type × dimension",
        source_selector="dv_arrival_facts JOIN dv_source_packages(status=activated)",
        aggregation_rule="one_slice_type_per_query",
        missing_rule="separate_actual_forecast_estimate_and_preserve_missing",
        alias_version="arrival-detail-alias-v1",
    ),
    "iron_ore_basis": _spec(
        dataset="iron_ore_basis",
        kind="dataset_rows",
        allowed_fields=(
            "observation_date", "business_date", "business_year", "business_week", "week_label", "port",
            "product", "futures_series", "wet_spot_price", "quality_adjustment", "brand_adjustment",
            "standardized_spot_price", "futures_close", "basis", "data_status", "rule_version",
            "parameter_version", "source_file", "source_workbook_name", "source_workbook_sha256", "value_state", "row_ref",
        ),
        filter_keys=("years", "business_weeks", "ports", "products", "start_date", "end_date", "data_states"),
        dimension_keys=("business_date", "port", "product", "futures_series", "data_status"),
        measure_fields=("wet_spot_price", "quality_adjustment", "brand_adjustment", "standardized_spot_price", "futures_close", "basis"),
        unit="按字段登记单位",
        grain="business_date × port × product × rule_version × parameter_version",
        source_selector="iron_ore_basis_results:valid_saved_rows",
        aggregation_rule="field_unit_specific_no_cross_unit_sum",
        missing_rule="preserve_data_status_and_missing_values",
        alias_version="iron-ore-basis-alias-v1",
        supported_relations=("port_spread_vs_rizhao", "inventory_basis_observation", "inventory_wet_price_observation"),
    ),
})


def get_dataset_spec(dataset: str) -> DatasetSpec:
    try:
        return DATASET_SPECS[dataset]
    except KeyError:
        raise ValueError(f"unsupported_dataset:{dataset}") from None


def dataset_registry() -> list[dict]:
    return [spec.as_dict() for spec in DATASET_SPECS.values()]


def allowed_fields(dataset: str) -> tuple[str, ...]:
    return get_dataset_spec(dataset).allowed_fields


def public_allowed_fields(dataset: str) -> tuple[str, ...]:
    """Fields safe for the default Agent projection without source-data access."""
    return tuple(field for field in get_dataset_spec(dataset).allowed_fields if field not in SENSITIVE_FIELDS)


__all__ = [
    "DatasetId", "DatasetSpec", "DATASET_SPECS", "SENSITIVE_FIELDS", "get_dataset_spec",
    "dataset_registry", "allowed_fields", "public_allowed_fields",
]
