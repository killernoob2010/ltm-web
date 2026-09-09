import pytest
from pydantic import ValidationError

from app.trading_agent.contracts import AsOf, FactQuery, MetricValue, Shock


def test_model_cannot_supply_identity_or_raw_sql():
    for extra in ({"user_id": 1}, {"account_ids": [1]}, {"sql": "select *"}):
        with pytest.raises(ValidationError):
            FactQuery(**extra)


def test_latest_and_historical_dates_cannot_be_confused():
    with pytest.raises(ValidationError):
        AsOf(mode="settlement_date")
    with pytest.raises(ValidationError):
        AsOf(date="2026-09-01")
    assert AsOf(mode="settlement_date", date="2026-09-01").date.isoformat() == "2026-09-01"


def test_unknown_metric_is_not_zero_or_complete():
    with pytest.raises(ValidationError):
        MetricValue(value=None, unit="CNY", status="complete", covered_rows=1, eligible_rows=2)
    with pytest.raises(ValidationError):
        MetricValue(value="NaN", unit="CNY", status="complete", covered_rows=2, eligible_rows=2)
    with pytest.raises(ValidationError):
        MetricValue(value="10", unit="", status="partial", covered_rows=1, eligible_rows=2)


def test_nonfinite_shock_rejected_before_calculation():
    with pytest.raises(ValidationError):
        Shock(underlying_symbol="i2609", price_change_pct=float("inf"))
