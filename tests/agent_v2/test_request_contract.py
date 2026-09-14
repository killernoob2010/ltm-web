from types import SimpleNamespace
from uuid import uuid4

from app.trading_agent.answer_contracts import ValidatedAnswer21
from app.trading_agent.request_contract import assess_position_coverage, presentation_policy, resolve_request


def _position_envelope(*, month="2701", option_type="call"):
    return SimpleNamespace(
        result_ref=uuid4(),
        status="complete",
        payload={
            "kind": "positions",
            "selection": {
                "asset_type": "option",
                "direction": "all",
                "filters": {"contract_months": [month], "option_type": option_type},
            },
        },
    )


def test_request_coverage_rejects_wrong_month_and_missing_put():
    envelope = _position_envelope()
    result = ValidatedAnswer21(
        delivery_status="complete",
        body_markdown="Call 净卖手数 6",
        plain_text="Call 净卖手数 6",
    )
    request, coverage = assess_position_coverage(
        "请用纯文字回答2705铁矿石期权 Call/Put 净卖和浮盈",
        [envelope],
        result,
    )

    assert request.filters["contract_months"] == ["2705"]
    assert request.filters["products"] == ["铁矿石"]
    assert coverage.status == "partial"
    assert "filter.contract_months" in coverage.missing
    assert "groups.option_type" in coverage.missing
    assert "metric.floating_pnl" in coverage.missing


def test_followup_request_inherits_month_and_clears_option_scope():
    first = resolve_request("2701铁矿石期权 Call/Put 净卖和浮盈", None)
    second = resolve_request("全部期权，只说数量", first)

    assert second.filters["contract_months"] == ["2701"]
    assert second.filters["option_type"] == "all"
    assert second.required_metrics == ["quantity"]


def test_followup_put_and_month_change_keep_the_position_domain():
    first = resolve_request("2701铁矿石期权 Call/Put 净卖和浮盈", None)
    put = resolve_request("只看Put", first)
    changed = resolve_request("换成2705", put)

    assert put.domain == "positions"
    assert put.filters["contract_months"] == ["2701"]
    assert put.filters["asset_type"] == "option"
    assert put.filters["option_type"] == "put"
    assert changed.domain == "positions"
    assert changed.filters["contract_months"] == ["2705"]
    assert changed.filters["option_type"] == "put"


def test_all_options_clears_inherited_direction_and_text_negation_keeps_constraint():
    first = resolve_request("2701期权只看卖出", None)
    all_options = resolve_request("全部期权", first)
    no_table = resolve_request("不要表格", {"domain": "positions", "presentation": "table"})

    assert all_options.filters["option_type"] == "all"
    assert "direction" not in all_options.filters
    assert no_table.presentation == "auto"
    assert no_table.prohibited_presentations == ["table"]


def test_inventory_quantity_is_not_misclassified_as_a_position_request():
    request = resolve_request("查询日照港库存数量", None)

    assert request.domain == "general"


def test_presentation_modes_and_negations_are_separate():
    assert presentation_policy("用列表说明") == {"mode": "text", "prohibited_presentations": []}
    assert presentation_policy("不要表格，画图") == {"mode": "chart", "prohibited_presentations": ["table"]}
    assert presentation_policy("请用表格") == {"mode": "table", "prohibited_presentations": []}
