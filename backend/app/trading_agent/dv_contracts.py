"""Strict model-facing contracts for composable data-visualization queries."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import StrictModel
from .semantic_catalog import DatasetId, dataset_registry, get_dataset_spec


class DataFilters(StrictModel):
    metric: Literal["inventory", "shipment", "arrival", "apparent_demand"] | None = None
    # Keep the request bounded by payload size, but do not impose a business
    # limit of three years on historical comparisons.
    years: list[int] | None = Field(default=None, max_length=50)
    business_weeks: list[int] | None = Field(default=None, max_length=53)
    products: list[str] | None = Field(default=None, max_length=200)
    categories: list[str] | None = Field(default=None, max_length=16)
    source_countries: list[str] | None = Field(default=None, max_length=100)
    ports: list[str] | None = Field(default=None, max_length=100)
    regions: list[str] | None = Field(default=None, max_length=30)
    mainstream_status: list[Literal["主流", "非主流"]] | None = None
    product_pool: Literal["mainstream", "non_mainstream", "aggregate", "custom"] = "custom"
    scope_type: str | None = None
    slice_type: str | None = None
    arrival_kind: Literal["actual", "source_forecast", "model_estimate"] | None = None
    grades: list[str] | None = Field(default=None, max_length=12)
    summary_metrics: list[str] | None = Field(default=None, max_length=12)
    data_states: list[Literal["observed", "missing", "legacy_zero_uncertain", "invalid"]] | None = Field(default=None, max_length=8)


class DatasetQuery(StrictModel):
    dataset: DatasetId
    mode: Literal["latest", "range", "seasonal"] = "latest"
    start_date: date | None = None
    end_date: date | None = None
    filters: DataFilters = Field(default_factory=DataFilters)
    fields: list[str] = Field(default_factory=list, max_length=16)
    cursor: str | None = Field(default=None, min_length=16, max_length=2048)
    batch_size: int = Field(default=20_000, ge=1, le=20_000)


class DatasetDescribeArgs(StrictModel):
    dataset: DatasetId


class DatasetCompare(StrictModel):
    result_ref: UUID
    method: Literal["previous_week", "previous_observation", "explicit_periods"]
    measure: str = Field(min_length=1, max_length=80)
    group_by: list[str] = Field(default_factory=list, max_length=6)
    current_date: date | None = None
    previous_date: date | None = None


class DatasetSummary(StrictModel):
    result_ref: UUID
    measure: str = Field(min_length=1, max_length=80)
    operation: Literal["sum", "mean", "min", "max", "count", "period_end"]
    group_by: list[str] = Field(default_factory=list, max_length=6)


class DatasetRelation(StrictModel):
    left_ref: UUID
    right_ref: UUID
    relation: Literal[
        "inventory_basis_observation",
        "inventory_wet_price_observation",
        "port_spread_vs_rizhao",
    ]


class OptimalWarrantArgs(StrictModel):
    scope: Literal["current_year_global_latest"] = "current_year_global_latest"


_LIST_FILTER_FIELDS = {
    "years", "business_weeks", "products", "categories", "source_countries", "ports",
    "regions", "mainstream_status", "grades", "summary_metrics", "data_states",
}


def _normalize_list(values, field: str):
    if values is None:
        return None
    normalized = []
    seen = set()
    for value in values:
        if field in {"years", "business_weeks"}:
            normalized_value = int(value)
        else:
            normalized_value = str(value).strip()
            if field == 'ports':
                normalized_value = {'日照港': '日照'}.get(normalized_value, normalized_value)
            if not normalized_value:
                raise ValueError(f"empty_filter:{field}")
        if normalized_value not in seen:
            normalized.append(normalized_value)
            seen.add(normalized_value)
    return normalized


def _normalize_filters(filters: DataFilters) -> DataFilters:
    updates = {}
    provided = filters.model_fields_set
    for field in _LIST_FILTER_FIELDS:
        if field in provided:
            updates[field] = _normalize_list(getattr(filters, field), field)
    for field in ("scope_type", "slice_type"):
        if field in provided:
            value = getattr(filters, field)
            updates[field] = value.strip() if isinstance(value, str) else value
            if updates[field] == "":
                raise ValueError(f"empty_filter:{field}")
    return filters.model_copy(update=updates)


def _validate_years_and_weeks(filters: DataFilters) -> None:
    if filters.years and any(year < 1900 or year > 2100 for year in filters.years):
        raise ValueError("year_out_of_range")
    if filters.business_weeks and any(week < 1 or week > 53 for week in filters.business_weeks):
        raise ValueError("business_week_out_of_range")


def _provided_filter_names(filters: DataFilters) -> set[str]:
    return set(filters.model_fields_set)


def validate_dataset_query(args: DatasetQuery) -> DatasetQuery:
    """Normalize and validate a query without opening a database connection."""
    if not isinstance(args, DatasetQuery):
        args = DatasetQuery.model_validate(args)
    spec = get_dataset_spec(args.dataset)
    # Preserve the caller's distinction between omitted and explicitly empty
    # selections before model_copy() adds normalized values to the model.
    provided = _provided_filter_names(args.filters)
    filters = _normalize_filters(args.filters)
    _validate_years_and_weeks(filters)

    unsupported = sorted(
        name for name in provided
        if name not in set(spec.filter_keys)
        and not (name == "product_pool" and filters.product_pool == "custom" and name in set(spec.filter_keys))
    )
    if unsupported:
        raise ValueError(f"unsupported_filter:{unsupported[0]}")

    if args.fields:
        unknown_fields = sorted(set(args.fields) - set(spec.allowed_fields))
        if unknown_fields:
            raise ValueError(f"unsupported_field:{unknown_fields[0]}")
        if len(args.fields) != len(set(args.fields)):
            raise ValueError("duplicate_field")

    if args.mode == "latest" and (args.start_date is not None or args.end_date is not None):
        raise ValueError("latest_does_not_accept_dates")
    if args.mode == "range":
        if args.start_date is None or args.end_date is None:
            raise ValueError("range_requires_dates")
        if args.start_date > args.end_date:
            raise ValueError("date_range_reversed")
    if args.mode == "seasonal":
        if args.start_date is not None or args.end_date is not None:
            raise ValueError("seasonal_does_not_accept_dates")
        if not filters.years:
            raise ValueError("seasonal_requires_years")

    if args.dataset == "spot_series":
        if filters.metric is None:
            raise ValueError("missing_filter:metric")
    elif filters.metric is not None or "metric" in provided:
        raise ValueError("unsupported_filter:metric")

    if args.dataset == "arrival_detail":
        if not filters.arrival_kind:
            raise ValueError("needs_clarification:arrival_kind")
        if not filters.slice_type:
            raise ValueError("needs_clarification:slice_type")
        country_slice = filters.slice_type.casefold() in {"country", "source_country", "国家", "来源国"}
        if country_slice and filters.products:
            raise ValueError("unsupported_filter:products")
    if args.dataset == "inventory_grade" and filters.products:
        raise ValueError("unsupported_filter:products")
    if args.dataset in {"inventory_summary", "inventory_grade"} and filters.product_pool != "custom":
        raise ValueError("unsupported_filter:product_pool")

    return args.model_copy(update={"filters": filters})


def registry_json_schema() -> dict:
    """Return only strict model schemas and the immutable semantic registry."""
    return {
        "datasets": dataset_registry(),
        "query": DatasetQuery.model_json_schema(),
        "compare": DatasetCompare.model_json_schema(),
        "summary": DatasetSummary.model_json_schema(),
        "relation": DatasetRelation.model_json_schema(),
    }


__all__ = [
    "DataFilters", "DatasetQuery", "DatasetDescribeArgs", "DatasetCompare", "DatasetSummary",
    "DatasetRelation", "OptimalWarrantArgs", "validate_dataset_query", "registry_json_schema",
]
