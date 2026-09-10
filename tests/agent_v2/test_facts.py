from datetime import datetime, timezone
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


def test_position_summary_persists_contract_direction_metric_groups(queued):
    principal, result = capture(queued, 2)
    summary = facts.summarize(
        principal,
        result.result_ref,
        ["account", "contract", "direction"],
        ["quantity"],
    )

    assert summary.payload["group_count"] == 2
    assert summary.payload["groups_truncated"] is False
    assert [group["dimensions"]["contract"] for group in summary.payload["groups"]] == ["i1000", "i1001"]
    assert all(group["metrics"]["quantity"]["status"] == "complete" for group in summary.payload["groups"])
    assert {Decimal(group["metrics"]["quantity"]["value"]) for group in summary.payload["groups"]} == {Decimal("1")}


def test_large_position_summary_marks_group_preview_without_losing_total(queued):
    principal, result = capture(queued, 205)
    summary = facts.summarize(principal, result.result_ref, ["contract"], ["quantity"])

    assert summary.payload["group_count"] == 205
    assert summary.payload["groups_truncated"] is True
    assert len(summary.payload["groups"]) == 100
    assert Decimal(summary.metrics["quantity"].value) == 205


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


def test_position_filters_cover_future_option_contract_and_direction_without_merging(queued):
    task = store.claim_next("mixed-position-worker")
    principal = store.principal_for_task(task)
    _insert_wh6_snapshot(
        principal.account_ids[0],
        rows=[
            dict(contract="i2609", asset_type="future", exchange="DCE", direction="买", quantity=2, average_price=700),
            dict(contract="i2701", asset_type="future", exchange="DCE", direction="卖", quantity=3, average_price=710),
            dict(contract="i2609-p-650", asset_type="option", exchange="DCE", direction="卖", quantity=4, average_price=4),
            dict(contract="i2609-p-700", asset_type="option", exchange="DCE", direction="买", quantity=5, average_price=5),
        ],
    )

    def quotes(requests):
        return {request.contract: QuoteSnapshot(last_price=710, multiplier=100) for request in requests}

    result = facts.capture_positions(principal, FactQuery(), quotes)
    assert result.payload["count"] == 4
    assert result.metrics["quantity"].value == "14.0"
    assert {group["dimensions"]["account"] for group in result.payload["groups"]} == {"宏源期货"}
    assert {(group["dimensions"]["contract"], group["dimensions"]["direction"])
            for group in result.payload["groups"]} == {
                ("i2609", "买"), ("i2701", "卖"), ("i2609-p-650", "卖"), ("i2609-p-700", "买"),
            }

    option_only = facts.capture_positions(principal, FactQuery(asset_type="option"), quotes)
    assert option_only.payload["count"] == 2
    sell_only = facts.capture_positions(principal, FactQuery(direction="sell"), quotes)
    assert sell_only.payload["count"] == 2
    exact_contract = facts.capture_positions(principal, FactQuery(contracts=["i2609-p-650"]), quotes)
    assert exact_contract.payload["count"] == 1
    assert exact_contract.payload["groups"][0]["dimensions"]["direction"] == "卖"


def test_quote_failure_keeps_position_quantity_and_marks_quote_metrics_unavailable(queued):
    principal, result = capture(queued, 1)
    failed = facts.capture_positions(principal, FactQuery(), lambda requests: (_ for _ in ()).throw(RuntimeError("quote down")))

    assert failed.status == "partial"
    assert failed.metrics["quantity"].status == "complete"
    assert failed.metrics["floating_pnl"].status == "unavailable"
    assert failed.payload["groups"][0]["metrics"]["quantity"]["status"] == "complete"
    assert failed.payload["groups"][0]["metrics"]["floating_pnl"]["status"] == "unavailable"


def test_capture_does_not_infer_data_as_of_from_capture_time(queued, monkeypatch):
    fixed = datetime.now(timezone.utc).replace(microsecond=0)
    task = store.claim_next("facts-time-worker")
    principal = store.principal_for_task(task)
    _insert_wh6_snapshot(
        principal.account_ids[0],
        rows=[dict(contract="i2609", asset_type="future", exchange="DCE",
                   direction="买", quantity=1, average_price=700)],
    )
    monkeypatch.setattr(store, "now", lambda: fixed)
    result = facts.capture_positions(principal, FactQuery(), lambda reqs: {})
    assert result.captured_at == fixed
    assert result.data_as_of is None


def test_capture_exposes_wh6_source_observation_without_calling_it_global_as_of(queued):
    principal, result = capture(queued, 1)
    provenance = result.payload["provenance"]
    assert result.data_as_of is None
    assert provenance["data_as_of"] is None
    assert provenance["precision"] is None
    assert provenance["source_observations"] == [{
        "source_kind": "wh6",
        "source_label": "WH6完整快照",
        "observed_at": "2026-09-02T09:00:00+08:00",
        "coverage_date_start": "2026-09-02",
        "coverage_date_end": "2026-09-02",
        "row_count": 1,
        "fact_status": "provisional",
        "freshness_status": "stale",
        "environment": "unknown",
    }]


def test_historical_settlement_provenance_keeps_date_precision(queued):
    from test_trading_effective_facts import _baseline_row, _insert_settlement_position_batch

    task = store.claim_next("historical-provenance-worker")
    principal = store.principal_for_task(task)
    _insert_settlement_position_batch(
        principal.account_ids[0], "20260831",
        rows=[_baseline_row("i2609", "买", 2, 700)],
    )
    result = facts.capture_positions(
        principal,
        FactQuery(as_of={"mode": "settlement_date", "date": "2026-08-31"}),
        lambda reqs: {},
    )
    provenance = result.payload["provenance"]
    assert result.data_as_of is None
    assert provenance["source_observations"][0]["source_kind"] == "settlement"
    assert provenance["source_observations"][0]["coverage_date_start"] == "2026-08-31"
    assert provenance["source_observations"][0]["coverage_date_end"] == "2026-08-31"
    assert provenance["source_observations"][0]["observed_at"] is None


def test_inferred_position_provenance_keeps_baseline_and_fill_dates(queued):
    from test_trading_collector_reconciliation import insert_wh6_fill
    from test_trading_effective_facts import _baseline_row, _insert_settlement_position_batch

    task = store.claim_next("derived-provenance-worker")
    principal = store.principal_for_task(task)
    _insert_settlement_position_batch(
        principal.account_ids[0], "20260831",
        rows=[_baseline_row("i2609", "买", 2, 700)],
    )
    insert_wh6_fill(
        principal.account_ids[0], event_key="derived-1", trade_date="2026-09-01",
        contract="i2609", asset_type="future", side="买", open_close="开", quantity=1,
        price="705",
    )
    result = facts.capture_positions(principal, FactQuery(), lambda reqs: {})
    observations = result.payload["provenance"]["source_observations"]
    assert result.data_as_of is None
    assert observations[0]["source_kind"] == "derived"
    assert observations[0]["coverage_date_start"] == "2026-08-31"
    assert observations[0]["coverage_date_end"] == "2026-09-01"
