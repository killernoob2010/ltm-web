from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.trading_agent import dv_analysis
from app.trading_agent.contracts import ToolEnvelope
from app.trading_agent.dv_contracts import DatasetCompare, DatasetSummary


def test_compare_values_uses_decimal_and_protects_zero_or_negative_bases():
    assert dv_analysis.compare_values(
        dv_analysis.Decimal("100"), dv_analysis.Decimal("90"),
        allow_pct=True, micro_base=dv_analysis.MICRO_BASE,
    ) == {"delta": "10", "delta_pct": "11.11111111111111111111111111", "comparison_status": "complete"}
    assert dv_analysis.compare_values(
        dv_analysis.Decimal("1"), dv_analysis.Decimal("0"),
        allow_pct=True, micro_base=dv_analysis.MICRO_BASE,
    ) == {"delta": "1", "delta_pct": None, "comparison_status": "unstable_base"}
    assert dv_analysis.compare_values(
        dv_analysis.Decimal("-10"), dv_analysis.Decimal("-20"),
        allow_pct=False, micro_base=dv_analysis.MICRO_BASE,
    ) == {"delta": "10", "delta_pct": None, "comparison_status": "complete"}


def _saved(rows, dataset="port_inventory"):
    return SimpleNamespace(
        envelope=ToolEnvelope(
            status="complete", captured_at=datetime.now(timezone.utc), calculation_version="test",
            payload={"kind": "dataset_rows", "dataset": dataset, "required_resources": ["data_visualization.display"], "account_scope": []},
        ),
        rows=rows,
        expires_at=datetime.now(timezone.utc),
    )


def test_summary_rejects_inventory_cross_period_sum(monkeypatch):
    ref = uuid4()
    saved = _saved([
        {"observation_date": "2026-09-01", "port": "日照", "value": 100, "value_state": "observed"},
        {"observation_date": "2026-09-08", "port": "日照", "value": 90, "value_state": "observed"},
    ])
    monkeypatch.setattr(dv_analysis.store, "load_result", lambda *args, **kwargs: saved)
    monkeypatch.setattr(dv_analysis, "authorize", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="inventory_across_periods"):
        dv_analysis.summarize_dataset(
            object(), DatasetSummary(result_ref=ref, measure="value", operation="sum", group_by=["port"])
        )


def test_compare_dataset_returns_delta_and_missing_period_as_registered_rows(monkeypatch):
    ref = uuid4()
    saved = _saved([
        {"observation_date": "2026-09-08", "port": "日照", "value": 90, "unit": "万吨", "value_state": "observed"},
        {"observation_date": "2026-09-01", "port": "日照", "value": 100, "unit": "万吨", "value_state": "observed"},
        {"observation_date": "2026-09-08", "port": "青岛", "value": 80, "unit": "万吨", "value_state": "observed"},
    ])
    monkeypatch.setattr(dv_analysis.store, "load_result", lambda *args, **kwargs: saved)
    monkeypatch.setattr(dv_analysis, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(dv_analysis, "_save_derived", lambda principal, envelope, rows, **kwargs: envelope)
    # The analysis still exposes its deterministic rows before persistence; the
    # fake loader is enough to exercise the period matching contract here.
    result = dv_analysis.compare_dataset(
        object(), DatasetCompare(result_ref=ref, method="previous_week", measure="value", group_by=["port"])
    )
    assert result.payload["periods"] == {"current": "2026-09-08", "previous": "2026-09-01"}
    assert result.status == "partial"


def test_all_weekly_changes_include_every_observation_and_preserve_missing_baseline(monkeypatch):
    saved = _saved([
        {'observation_date': day, 'port': '日照', 'value': value, 'unit': '万吨', 'value_state': 'observed'}
        for day, value in [('2026-08-04', 100), ('2026-08-11', 97), ('2026-08-18', 90), ('2026-08-25', 86)]
    ])
    monkeypatch.setattr(dv_analysis.store, 'load_result', lambda *a, **kw: saved)
    monkeypatch.setattr(dv_analysis, 'authorize', lambda *a, **kw: None)
    monkeypatch.setattr(dv_analysis, '_save_derived', lambda principal, envelope, rows, **kw: envelope)
    result = dv_analysis.compare_dataset(object(), DatasetCompare(result_ref=uuid4(), method='all_previous_weeks', measure='value', group_by=['port']))
    rows = result.payload['preview']
    assert [r['current_date'] for r in rows] == ['2026-08-04', '2026-08-11', '2026-08-18', '2026-08-25']
    assert [r['delta'] for r in rows] == [None, '-3', '-7', '-4']
    assert rows[0]['comparison_status'] == 'missing_period'
    assert result.payload['coverage']['missing_previous'] == 1


def test_inventory_period_end_summary_groups_by_registered_business_year(monkeypatch):
    ref = uuid4()
    saved = _saved([
        {"observation_date": "2024-12-25", "business_year": 2024, "port": "江阴", "value": 100, "value_state": "observed"},
        {"observation_date": "2024-12-31", "business_year": 2024, "port": "江阴", "value": 110, "value_state": "observed"},
        {"observation_date": "2026-12-31", "business_year": 2026, "port": "江阴", "value": 130, "value_state": "observed"},
    ])
    monkeypatch.setattr(dv_analysis.store, "load_result", lambda *args, **kwargs: saved)
    monkeypatch.setattr(dv_analysis, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(dv_analysis, "_save_derived", lambda principal, envelope, rows, **kwargs: envelope)

    result = dv_analysis.summarize_dataset(
        object(), DatasetSummary(result_ref=ref, measure="value", operation="period_end", group_by=["business_year"])
    )

    assert result.payload["annual_method"] == "period_end"
    assert {(row["business_year"], row["value"]) for row in result.payload["preview"]} == {(2024, "110"), (2026, "130")}
    assert result.payload["missing_years"] == [2025]
