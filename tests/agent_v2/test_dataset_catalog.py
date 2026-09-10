import pytest
from pydantic import ValidationError

from app.trading_agent.dv_contracts import (
    DataFilters,
    DatasetQuery,
    validate_dataset_query,
)
from app.trading_agent.semantic_catalog import DATASET_SPECS, dataset_registry


def test_catalog_registers_all_supported_dataset_specs_as_immutable_contracts():
    assert set(DATASET_SPECS) == {
        "spot_series",
        "port_inventory",
        "inventory_summary",
        "inventory_grade",
        "source_mainstream_inventory",
        "arrival_detail",
        "iron_ore_basis",
    }
    registry = dataset_registry()
    assert {item["dataset"] for item in registry} == set(DATASET_SPECS)
    assert all(item["semantic_version"] == "trade-semantics-v1" for item in registry)
    with pytest.raises(TypeError):
        DATASET_SPECS["new_dataset"] = DATASET_SPECS["spot_series"]


def test_spot_series_rejects_unsupported_port_filter_without_touching_data():
    query = DatasetQuery(
        dataset="spot_series",
        filters={"metric": "inventory", "ports": ["日照"]},
    )
    with pytest.raises(ValueError, match="unsupported_filter:ports"):
        validate_dataset_query(query)


def test_arrival_country_slice_cannot_be_crossed_with_product_filter():
    query = DatasetQuery(
        dataset="arrival_detail",
        filters={
            "arrival_kind": "actual",
            "slice_type": "country",
            "source_countries": ["巴西"],
            "products": ["PB粉"],
        },
    )
    with pytest.raises(ValueError, match="unsupported_filter:products"):
        validate_dataset_query(query)


def test_inventory_grade_does_not_invent_product_dimension():
    query = DatasetQuery(dataset="inventory_grade", filters={"products": ["PB粉"]})
    with pytest.raises(ValueError, match="unsupported_filter:products"):
        validate_dataset_query(query)


def test_unknown_fields_are_rejected_by_dataset_semantics_and_schema_is_strict():
    with pytest.raises(ValueError, match="unsupported_field:secret_value"):
        validate_dataset_query(DatasetQuery(dataset="spot_series", filters={"metric": "inventory"}, fields=["secret_value"]))
    with pytest.raises(ValidationError):
        DataFilters.model_validate({"metric": "inventory", "sql": "select 1"})


def test_explicit_empty_selection_is_not_equivalent_to_omitted_selection():
    omitted = DatasetQuery(dataset="spot_series", filters={"metric": "inventory"})
    explicit_empty = DatasetQuery(dataset="spot_series", filters={"metric": "inventory", "products": []})
    normalized_omitted = validate_dataset_query(omitted)
    normalized_empty = validate_dataset_query(explicit_empty)
    assert normalized_omitted.filters.products is None
    assert normalized_empty.filters.products == []


def test_query_time_modes_allow_multi_year_ranges_with_explicit_boundaries():
    with pytest.raises(ValueError, match="latest_does_not_accept_dates"):
        validate_dataset_query(DatasetQuery(dataset="spot_series", filters={"metric": "inventory"}, start_date="2026-01-01"))
    with pytest.raises(ValueError, match="range_requires_dates"):
        validate_dataset_query(DatasetQuery(dataset="spot_series", mode="range", filters={"metric": "inventory"}))
    with pytest.raises(ValueError, match="seasonal_requires_years"):
        validate_dataset_query(DatasetQuery(dataset="spot_series", mode="seasonal", filters={"metric": "inventory"}))
    multi_year = validate_dataset_query(DatasetQuery(
        dataset="spot_series",
        mode="range",
        start_date="2020-01-01",
        end_date="2026-01-02",
        filters={"metric": "inventory"},
    ))
    assert multi_year.start_date.isoformat() == "2020-01-01"
    assert multi_year.end_date.isoformat() == "2026-01-02"

    seasonal = validate_dataset_query(DatasetQuery(
        dataset="spot_series",
        mode="seasonal",
        filters={"metric": "inventory", "years": [2020, 2021, 2022, 2023, 2024]},
    ))
    assert seasonal.filters.years == [2020, 2021, 2022, 2023, 2024]

    with pytest.raises(ValueError, match="date_range_reversed"):
        validate_dataset_query(DatasetQuery(
            dataset="spot_series",
            mode="range",
            start_date="2026-01-02",
            end_date="2020-01-01",
            filters={"metric": "inventory"},
        ))


def test_dataset_specific_required_filters_are_not_silently_defaulted():
    with pytest.raises(ValueError, match="missing_filter:metric"):
        validate_dataset_query(DatasetQuery(dataset="spot_series"))
    with pytest.raises(ValueError, match="needs_clarification:arrival_kind"):
        validate_dataset_query(DatasetQuery(dataset="arrival_detail"))
