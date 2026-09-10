from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.trading_agent.answer_contracts import ViewRequest
from app.trading_agent.contracts import ToolEnvelope


REF = UUID("00000000-0000-0000-0000-000000000001")


def _saved(rows, *, kind="dataset_rows", dataset="spot_series", payload=None):
    body = {"kind": kind, "dataset": dataset, "unit": "万吨"}
    if payload:
        body.update(payload)
    return SimpleNamespace(
        rows=rows,
        envelope=ToolEnvelope(
            status="complete",
            captured_at=datetime(2026, 9, 10, 3, 22, 46, tzinfo=timezone.utc),
            calculation_version="presentation-test",
            payload=body,
        ),
    )


def _request(**overrides):
    body = {
        "id": "v1",
        "kind": "line",
        "result_ref": REF,
        "fields": ["observation_date", "value", "product", "business_year"],
        "title": "现货全品种图谱",
        "layout": "atlas",
        "x_field": "observation_date",
        "series_by": ["business_year"],
        "facet_by": ["product"],
        "axis_mode": "chronological",
    }
    body.update(overrides)
    return ViewRequest.model_validate(body)


def test_view_request_accepts_registered_layout_controls_and_keeps_old_defaults():
    request = ViewRequest(
        id="v1", kind="table", result_ref=REF, title="旧表格", fields=["quantity"]
    )
    assert request.layout == "standard"
    assert request.series_by == []
    assert request.facet_by == []
    assert _request().layout == "atlas"


def test_dataset_row_projection_uses_dataset_registry_instead_of_trading_fields():
    from app.trading_agent.facts import project_dataset_row

    row = {"value": "2", "product": "PB粉", "secret_value": "99"}
    assert project_dataset_row("spot_series", row, ["product", "value"]) == {"product": "PB粉", "value": "2"}
    with pytest.raises(ValueError, match="字段不在当前数据集目录中"):
        project_dataset_row("spot_series", row, ["secret_value"])


def test_dataset_chart_supports_three_years_without_old_500_point_fallback():
    from app.trading_agent.presentation import build_dataset_chart

    rows = []
    for year in (2024, 2025, 2026):
        for day in range(366):
            rows.append({
                "row_ref": f"r{year}-{day}",
                "observation_date": (datetime(year, 1, 1) + timedelta(days=day)).date().isoformat(),
                "business_year": year,
                "product": "PB粉",
                "value": str(day),
            })
    chart = build_dataset_chart(_saved(rows), _request())
    assert chart["version"] == 2
    assert chart["layout"] == "atlas"
    assert len(chart["facets"]) == 1
    assert len(chart["facets"][0]["series"]) == 3
    assert len(chart["facets"][0]["series"][0]["points"]) == 366


def test_dataset_chart_allows_more_than_366_points_until_payload_limits():
    from app.trading_agent.presentation import build_dataset_chart

    rows = [
        {
            "row_ref": f"r{day}",
            "observation_date": (datetime(2024, 1, 1) + timedelta(days=day)).date().isoformat(),
            "business_year": 2024,
            "product": "PB粉",
            "value": str(day),
        }
        for day in range(730)
    ]

    chart = build_dataset_chart(_saved(rows), _request())

    assert chart["version"] == 2
    assert len(chart["facets"][0]["series"][0]["points"]) == 730


def test_dataset_chart_allows_same_x_for_different_series_but_rejects_duplicate_series_x():
    from app.trading_agent.presentation import build_dataset_chart

    rows = [
        {"row_ref": "r1", "observation_date": "2026-09-01", "business_year": 2025, "product": "PB粉", "value": "1"},
        {"row_ref": "r2", "observation_date": "2026-09-01", "business_year": 2026, "product": "PB粉", "value": "2"},
    ]
    chart = build_dataset_chart(_saved(rows), _request())
    assert chart["facets"][0]["series"][0]["points"][0]["x"] == "2026-09-01"
    assert len(chart["facets"][0]["series"]) == 2

    duplicate = build_dataset_chart(
        _saved(rows + [dict(rows[0], row_ref="r3", value="3")]), _request()
    )
    assert duplicate["fallback"] == "table"
    assert duplicate["reason"] == "duplicate_point"


def test_dataset_chart_facets_are_server_paginated_and_not_sampled():
    from app.trading_agent.presentation import build_dataset_chart

    rows = [
        {
            "row_ref": f"r{index}",
            "observation_date": "2026-09-01",
            "business_year": 2026,
            "product": f"品种{index}",
            "value": str(index),
        }
        for index in range(7)
    ]
    first = build_dataset_chart(_saved(rows), _request(), facet_page=1)
    second = build_dataset_chart(_saved(rows), _request(), facet_page=2)
    assert first["facet_pagination"] == {"page": 1, "page_size": 6, "total": 7, "has_more": True}
    assert len(first["facets"]) == 6
    assert second["facet_pagination"] == {"page": 2, "page_size": 6, "total": 7, "has_more": False}
    assert len(second["facets"]) == 1


def test_matrix_has_fixed_row_dimensions_and_twelve_column_pages():
    from app.trading_agent.presentation import build_matrix

    rows = [
        {"row_ref": f"r{index}", "port": "日照港", "product": f"品种{index:02d}", "delta": str(index)}
        for index in range(13)
    ]
    request = ViewRequest(
        id="v1", kind="table", result_ref=REF, title="港口变化矩阵",
        fields=["port", "product", "delta"], layout="matrix",
        series_by=["product"], facet_by=["port"],
    )
    matrix = build_matrix(_saved(
        rows,
        kind="dataset_comparison",
        dataset="iron_ore_basis",
        payload={"group_by": ["port", "product"], "value_fields": ["delta"]},
    ), request, matrix_column_page=2)
    assert matrix["row_dimensions"] == ["port"]
    assert len(matrix["columns"]) == 1
    assert matrix["columns"][0]["key"] == "c13"
    assert matrix["column_pagination"] == {"page": 2, "page_size": 12, "total": 13, "has_more": False}
    assert matrix["rows"][0]["cells"]["c13"]["value"] == "12"


def test_dataset_summary_does_not_expose_trade_only_coverage_terms():
    from app.trading_agent.presentation import build_view

    saved = _saved([
        {"row_ref": "r1", "observation_date": "2026-09-01", "product": "PB粉", "value": "2"},
    ])
    result = build_view(None, ViewRequest(
        id="v1", kind="table", result_ref=REF,
        fields=["observation_date", "product", "value"], title="现货数据",
    ), SimpleNamespace(), saved=saved)
    assert "eligible_contracts" not in result["coverage"]
    assert "covered_contracts" not in result["coverage"]
    assert "eligible_quantity" not in result["coverage"]
    assert "row_count" in result["summary"]


def test_dataset_summary_default_view_includes_registered_group_dimensions():
    from app.trading_agent.presentation import build_view

    saved = _saved([
        {
            "row_ref": "summary:1", "business_year": 2022, "value": "100",
            "covered_rows": 4, "eligible_rows": 4, "status": "complete",
        },
    ], kind="dataset_summary", dataset="inventory_summary", payload={
        "group_by": ["business_year"], "value_fields": ["value"],
    })
    result = build_view(None, ViewRequest(
        id="v1", kind="table", result_ref=REF, fields=[], title="年度库存",
    ), SimpleNamespace(), saved=saved)

    assert [column["key"] for column in result["columns"]][:2] == ["business_year", "value"]
    assert result["rows"][0]["business_year"] == 2022


def test_dataset_row_delta_reference_is_allowed_but_hidden_or_invalid_values_are_not():
    from app.trading_agent.answer_v21 import validate_answer21

    ref = str(uuid4())
    saved = _saved([{"row_ref": "r1", "delta": "-1.25", "private_value": "9"}], kind="dataset_comparison", payload={
        "group_by": ["product"], "value_fields": ["current_value", "previous_value", "delta", "delta_pct"],
    })
    api = SimpleNamespace(load_result=lambda *args: saved)
    good = validate_answer21(None, {"schema_version": "2.1", "blocks": [{
        "id": "s1", "kind": "fact", "text": "变化为{{fact:" + ref + "#/rows/0/delta}}。",
        "refs": [ref + "#/rows/0/delta"],
    }], "views": []}, api)
    assert good.delivery_status == "complete"
    assert "-1.25" in good.body_markdown

    hidden = validate_answer21(None, {"schema_version": "2.1", "blocks": [{
        "id": "s1", "kind": "fact", "text": "隐藏值为{{fact:" + ref + "#/rows/0/private_value}}。",
        "refs": [ref + "#/rows/0/private_value"],
    }], "views": []}, api)
    assert hidden.delivery_status != "complete"
    assert "private_value" not in hidden.body_markdown

    saved.rows[0]["delta"] = "NaN"
    invalid = validate_answer21(None, {"schema_version": "2.1", "blocks": [{
        "id": "s1", "kind": "fact", "text": "变化为{{fact:" + ref + "#/rows/0/delta}}。",
        "refs": [ref + "#/rows/0/delta"],
    }], "views": []}, api)
    assert invalid.delivery_status != "complete"
    assert "NaN" not in invalid.body_markdown


def test_dataset_metadata_can_explain_scope_but_cannot_back_an_unbound_number():
    from app.trading_agent.answer_v21 import validate_answer21

    ref = str(uuid4())
    saved = _saved([], payload={"kind": "dataset_rows", "dataset": "spot_series"})
    api = SimpleNamespace(load_result=lambda *args: saved)
    good = validate_answer21(None, {"schema_version": "2.1", "blocks": [{
        "id": "s1", "kind": "fact", "text": "来源范围已核对。", "refs": [ref + "#/metadata"],
    }], "views": []}, api)
    assert good.delivery_status == "complete"

    bad = validate_answer21(None, {"schema_version": "2.1", "blocks": [{
        "id": "s1", "kind": "fact", "text": "覆盖范围为123行。", "refs": [ref + "#/metadata"],
    }], "views": []}, api)
    assert bad.delivery_status != "complete"
    assert "123" not in bad.body_markdown


def test_invalid_dataset_view_field_is_removed_instead_of_marked_complete():
    from app.trading_agent.answer_v21 import validate_answer21

    ref = str(uuid4())
    saved = _saved([{"row_ref": "r1", "value": "1", "secret_value": "99"}])
    api = SimpleNamespace(load_result=lambda *args: saved)
    result = validate_answer21(None, {"schema_version": "2.1", "blocks": [], "views": [{
        "id": "v1", "kind": "table", "result_ref": ref,
        "fields": ["value", "secret_value"], "title": "不应展示",
    }]}, api)
    assert result.views == []
    assert any(item.code == "invalid_view" for item in result.limitations)
