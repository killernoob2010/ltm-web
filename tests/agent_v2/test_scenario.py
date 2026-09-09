from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.trading_agent import risk, store
from app.trading_agent.contracts import Shock, ToolEnvelope
from test_store import queued


def snapshot(queued, rows):
    task = store.claim_next("scenario-worker")
    principal = store.principal_for_task(task)
    ref = store.save_result(
        principal,
        ToolEnvelope(
            status="complete",
            captured_at=datetime.now(timezone.utc),
            calculation_version="synthetic",
            payload={"kind": "positions", "valuation_basis": "latest_trade"},
        ),
        rows,
    )
    return principal, ref


def option_row(quantity=2):
    return {
        "contract": "i2609-c-750",
        "asset_type": "option",
        "direction": "买",
        "quantity": quantity,
        "contract_multiplier": 100,
        "underlying_symbol": "DCE.i2609",
        "valuation_price": 25,
        "_quote": {
            "underlying_price": 800,
            "strike_price": 750,
            "iv": 0.3,
            "time_to_expiry": 0.5,
            "risk_free_rate": 0.015,
            "option_class": "CALL",
            "greeks_method": "black76",
            "expiry_source": "provider_expire_datetime",
        },
    }


def test_zero_shock_is_zero_for_verified_black76_inputs(queued):
    principal, ref = snapshot(queued, [option_row()])
    result = risk.scenario(principal, ref, [Shock(underlying_symbol="DCE.i2609", price_change_pct=0)])
    outcome = result.payload["scenarios"][0]["scenario_pnl_change"]
    assert result.status == "complete"
    assert Decimal(outcome["value"]) == 0
    assert outcome["covered_rows"] == outcome["eligible_rows"] == 1


def test_futures_scenario_uses_linear_price_change(queued):
    principal, ref = snapshot(queued, [{
        "contract": "i2609", "asset_type": "future", "direction": "买", "quantity": 3,
        "contract_multiplier": 100, "underlying_symbol": "DCE.i2609", "valuation_price": 800,
        "_quote": {},
    }])
    result = risk.scenario(principal, ref, [Shock(underlying_symbol="DCE.i2609", price_change_pct=10)])
    outcome = result.payload["scenarios"][0]["scenario_pnl_change"]
    assert Decimal(outcome["value"]) == 24000


def test_invalid_shock_is_unsupported_without_fabricating_price(queued):
    principal, ref = snapshot(queued, [option_row()])
    result = risk.scenario(principal, ref, [Shock(underlying_symbol="DCE.i2609", price_change_pct=-101)])
    assert result.status == "unsupported"
    assert result.payload["scenarios"][0]["scenario_pnl_change"]["value"] is None
