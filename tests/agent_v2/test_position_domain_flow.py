from datetime import datetime, timezone

from app.trading_agent import answer, facts, store
from app.trading_agent.contracts import FactQuery, MetricValue, ToolEnvelope
from app.trading_valuation import QuoteSnapshot
from test_store import queued
from test_trading_effective_facts import _insert_wh6_snapshot


def _positions_snapshot(account_id):
    _insert_wh6_snapshot(
        account_id,
        rows=[
            dict(contract="i2701-c-700", asset_type="option", exchange="DCE", direction="买", quantity=2, average_price=10),
            dict(contract="i2701-c-720", asset_type="option", exchange="DCE", direction="卖", quantity=8, average_price=10),
            dict(contract="i2701-p-700", asset_type="option", exchange="DCE", direction="买", quantity=3, average_price=10),
            dict(contract="i2701-p-740", asset_type="option", exchange="DCE", direction="卖", quantity=4, average_price=10),
            dict(contract="i2705-c-700", asset_type="option", exchange="DCE", direction="卖", quantity=6, average_price=10),
            dict(contract="i2701", asset_type="future", exchange="DCE", direction="买", quantity=100, average_price=10),
        ],
    )


def _quotes(requests):
    values = {
        "i2701-c-700": 11.5,
        "i2701-c-720": 8,
        "i2701-p-700": 7,
        "i2701-p-740": 11.25,
        "i2705-c-700": 11,
        "i2701": 11,
    }
    return {
        request.contract: QuoteSnapshot(last_price=values[request.contract], multiplier=100)
        for request in requests
    }


def test_capture_positions_exposes_month_option_net_and_pnl_groups(queued):
    task = store.claim_next("domain-flow")
    principal = store.principal_for_task(task)
    _positions_snapshot(principal.account_ids[0])
    query = FactQuery(
        asset_type="option",
        filters={"products": ["铁矿石"], "contract_months": ["2701"]},
        required_metrics=["quantity", "net_quantity", "floating_pnl"],
        presentation="text",
    )

    result = facts.capture_positions(principal, query, _quotes)

    assert result.payload["count"] == 4
    assert result.metrics["quantity"].value == "17.0"
    assert result.metrics["net_quantity"].value == "7.0"
    assert result.metrics["floating_pnl"].value == "500.0"
    assert result.payload["presentation"] == "text"
    semantic = {group["dimensions"]["option_type"]: group for group in result.payload["semantic_groups"]}
    assert semantic["call"]["metrics"]["net_quantity"]["value"] == "6.0"
    assert semantic["call"]["metrics"]["floating_pnl"]["value"] == "1900.0"
    assert semantic["put"]["metrics"]["net_quantity"]["value"] == "1.0"
    assert semantic["put"]["metrics"]["floating_pnl"]["value"] == "-1400.0"

    ref = str(result.result_ref)
    body = f"Call净卖手数 {{{{fact:{ref}#/payload/semantic_groups/0/metrics/net_quantity}}}}。"
    draft = answer.parse_answer21({
        "schema_version": "2.1",
        "body_markdown": body,
        "spans": [{
            "id": "s1", "kind": "fact", "start": 0, "end": len(body),
            "refs": [f"{ref}#/payload/semantic_groups/0/metrics/net_quantity"],
        }],
        "views": [],
    })
    validated = answer.validate_answer21(principal, draft, store)
    assert validated.delivery_status == "complete"
    assert "6.0" in validated.plain_text
    assert not validated.views


def test_text_fallback_keeps_verified_net_and_pnl_without_view(queued):
    task = store.claim_next("domain-fallback")
    principal = store.principal_for_task(task)
    ref = store.save_result(
        principal,
        ToolEnvelope(
            status="partial",
            captured_at=datetime.now(timezone.utc),
            calculation_version="test",
            metrics={
                "quantity": MetricValue(value="17", unit="手", status="complete", covered_rows=4, eligible_rows=4),
                "net_quantity": MetricValue(value="7", unit="手", status="complete", covered_rows=4, eligible_rows=4),
                "floating_pnl": MetricValue(value="500", unit="CNY", status="partial", covered_rows=3, eligible_rows=4),
            },
            payload={
                "kind": "positions",
                "count": 4,
                "semantic_groups": [
                    {"dimensions": {"contract_month": "2701", "option_type": "call"}, "metrics": {
                        "net_quantity": {"value": "6", "unit": "手", "status": "complete", "covered_rows": 2, "eligible_rows": 2},
                        "floating_pnl": {"value": "1900", "unit": "CNY", "status": "complete", "covered_rows": 2, "eligible_rows": 2},
                    }},
                    {"dimensions": {"contract_month": "2701", "option_type": "put"}, "metrics": {
                        "net_quantity": {"value": "1", "unit": "手", "status": "complete", "covered_rows": 2, "eligible_rows": 2},
                        "floating_pnl": {"value": "-1400", "unit": "CNY", "status": "complete", "covered_rows": 2, "eligible_rows": 2},
                    }},
                ],
            },
        ),
        [],
        kind="positions",
    )

    result = answer.build_fallback21(
        principal, [str(ref)], {}, "budget_exhausted", store, presentation_preference="text"
    )

    assert result.delivery_status == "partial"
    assert result.views == []
    assert "总手数 17手" in result.plain_text
    assert "净卖手数 7手" in result.plain_text
    assert "浮盈亏 500CNY（覆盖3/4行）" in result.plain_text
    assert "Call 净卖手数 6手" in result.plain_text
    assert "Put 净卖手数 1手" in result.plain_text
    assert "表格" not in result.plain_text


def test_net_only_query_does_not_require_quotes(queued):
    task = store.claim_next("domain-net-only")
    principal = store.principal_for_task(task)
    _positions_snapshot(principal.account_ids[0])

    def no_quotes(_requests):
        raise AssertionError("net-only query must not fetch market quotes")

    result = facts.capture_positions(
        principal,
        FactQuery(asset_type="option", filters={"contract_months": ["2701"]}, required_metrics=["net_quantity"]),
        no_quotes,
    )

    assert result.status == "complete"
    assert result.metrics["net_quantity"].value == "7.0"
    assert result.metrics["floating_pnl"].status == "unavailable"
