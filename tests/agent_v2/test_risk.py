from datetime import datetime, timezone
from decimal import Decimal

from app.trading_agent import risk, store
from app.trading_agent.contracts import ToolEnvelope, Shock
from test_store import queued
from test_auth import pilot


def snapshot(queued, rows):
    task = store.claim_next("risk-worker")
    principal = store.principal_for_task(task)
    ref = store.save_result(principal,ToolEnvelope(status="complete",captured_at=datetime.now(timezone.utc),calculation_version="synthetic",payload={"kind":"positions","valuation_basis":"latest_trade"}),rows)
    return principal,ref


def row(quantity, classified=False, delta=.4, underlying="DCE.i2609"):
    return dict(contract="i2609-c-750",asset_type="option",direction="买",quantity=quantity,
        average_price=20,valuation_price=25,contract_multiplier=100,underlying_symbol=underlying,
        assignment_status="classified" if classified else "unclassified",
        _quote=dict(delta=delta,gamma=.01,theta=-1,vega=2,rho=3,iv=.3,underlying_price=800,source="synthetic"))


def test_unclassified_options_are_included_in_exposure(queued):
    principal,ref = snapshot(queued,[row(2,True),row(3)])
    result = risk.position_risk(principal,ref)
    assert Decimal(result.metrics["delta_exposure"].value) == 200
    assert result.metrics["delta_exposure"].covered_rows == 2


def test_missing_delta_has_independent_coverage(queued):
    principal,ref = snapshot(queued,[row(2),row(3,delta=None)])
    result = risk.position_risk(principal,ref)
    assert result.metrics["delta_exposure"].status == "partial"
    assert result.metrics["gamma_exposure"].status == "complete"


def test_different_underlyings_are_never_added_together(queued):
    principal,ref = snapshot(queued,[row(2),row(3,underlying="DCE.i2701")])
    result = risk.position_risk(principal,ref)
    assert "delta_exposure" not in result.metrics
    assert len(result.payload["groups"]) == 2


def test_scenario_without_verified_model_inputs_is_unsupported(queued):
    principal,ref = snapshot(queued,[row(2)])
    result = risk.scenario(principal,ref,[Shock(underlying_symbol="DCE.i2609",price_change_pct=1)])
    assert result.status == "unsupported"
