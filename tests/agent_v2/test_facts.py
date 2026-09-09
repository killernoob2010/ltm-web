from decimal import Decimal
from app import db
from app.trading_agent import facts, store
from app.trading_agent.contracts import FactQuery
from app.trading_valuation import QuoteSnapshot
from test_store import queued
from test_auth import pilot
from test_trading_effective_facts import _insert_wh6_snapshot


def capture(queued, quantity=205):
    task = store.claim_next("facts-worker")
    principal = store.principal_for_task(task)
    rows = [dict(contract=f"i{1000+i}",asset_type="future",exchange="DCE",direction="买",quantity=1,average_price=700) for i in range(quantity)]
    _insert_wh6_snapshot(principal.account_ids[0],rows=rows)
    result = facts.capture_positions(principal,FactQuery(),lambda reqs: {r.contract: QuoteSnapshot(last_price=710,multiplier=100) for r in reqs})
    return principal,result


def test_summary_uses_all_rows_not_preview(queued):
    principal,result = capture(queued)
    assert len(result.payload["preview"]) == 20
    summary = facts.summarize(principal,result.result_ref,["asset_type"],["quantity","floating_pnl"])
    assert Decimal(summary.metrics["quantity"].value) == 205
    assert Decimal(summary.metrics["floating_pnl"].value) == 205000
    assert summary.metrics["floating_pnl"].covered_rows == 205


def test_missing_quotes_do_not_turn_into_zero_pnl(queued):
    task = store.claim_next("facts-worker")
    principal = store.principal_for_task(task)
    _insert_wh6_snapshot(principal.account_ids[0],rows=[dict(contract="i2609",asset_type="future",exchange="DCE",direction="买",quantity=1,average_price=700)])
    result = facts.capture_positions(principal,FactQuery(),lambda reqs: {})
    assert result.metrics["floating_pnl"].value is None
    assert result.metrics["floating_pnl"].status == "unavailable"


def test_no_strategy_is_fabricated_as_group(queued):
    principal,result = capture(queued,1)
    assert result.payload["preview"][0]["group_key"] is None
    import pytest
    with pytest.raises(ValueError):
        facts.summarize(principal,result.result_ref,["group_key"],["quantity"])


def test_later_quote_change_does_not_reprice_snapshot(queued):
    principal,result = capture(queued,2)
    loaded = facts.read_page(principal,result.result_ref,1,20,["contract","floating_pnl"])
    assert all(row["floating_pnl"] == 1000.0 for row in loaded.payload["preview"])


def test_empty_snapshot_metrics_are_unavailable_not_zero(queued):
    task = store.claim_next("facts-worker")
    principal = store.principal_for_task(task)
    result = facts.capture_positions(principal, FactQuery(), lambda reqs: {})
    assert result.metrics["quantity"].value is None
    assert result.metrics["quantity"].status == "unavailable"


def test_position_direction_filter_uses_public_buy_sell_values(queued):
    principal, result = capture(queued, 2)
    selected = facts.capture_positions(
        principal, FactQuery(direction="buy"),
        lambda reqs: {request.contract: QuoteSnapshot(last_price=710, multiplier=100) for request in reqs},
    )
    assert selected.payload["count"] == 2
