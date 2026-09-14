from decimal import Decimal

import pytest

from app.trading_agent.contracts import FactQuery
from app.trading_agent.position_semantics import (
    aggregate_positions,
    metric_for_positions,
    normalize_position,
    parse_contract,
    presentation_preference,
    select_positions,
)


def _row(contract, direction, quantity, *, pnl=None, multiplier=100, account_id=1):
    return {
        "account_id": account_id,
        "snapshot_date": "20260914",
        "exchange": "DCE",
        "contract": contract,
        "asset_type": "option" if "-" in contract else "future",
        "direction": direction,
        "quantity": quantity,
        "average_price": 10,
        "floating_pnl": pnl,
        "contract_multiplier": multiplier,
    }


def test_contract_parser_normalizes_month_option_and_product_aliases():
    parts = parse_contract("I2701-P-700")

    assert parts.parse_status == "ok"
    assert parts.product == "i"
    assert parts.contract_month == "2701"
    assert parts.option_type == "put"
    assert parts.strike_price == Decimal("700")
    assert normalize_position(_row("i2701-c-700", "买", 2))["product"] == "i"


def test_2701_iron_ore_option_scope_excludes_future_and_other_month():
    rows = [
        _row("i2701-c-700", "买", 2),
        _row("i2701-p-740", "卖", 4),
        _row("i2705-c-700", "卖", 6),
        _row("i2701", "买", 100),
    ]
    query = FactQuery(
        asset_type="option",
        filters={"products": ["铁矿石"], "contract_months": ["2701"]},
    )

    selected, unresolved = select_positions(rows, query)

    assert unresolved == []
    assert [row["contract"] for row in selected] == ["i2701-c-700", "i2701-p-740"]
    assert metric_for_positions(selected, "quantity").value == "6"


def test_call_put_net_is_sell_minus_buy_and_keeps_both_sides():
    rows = [
        _row("i2701-c-700", "买", 2, pnl=200),
        _row("i2701-c-720", "卖", 8, pnl=1700),
        _row("i2701-p-700", "买", 3, pnl=-900),
        _row("i2701-p-740", "卖", 4, pnl=-500),
    ]
    aggregate = aggregate_positions(
        rows,
        group_by=("option_type",),
        metrics=("quantity", "gross_buy_quantity", "gross_sell_quantity", "net_quantity", "floating_pnl"),
    )

    assert aggregate["metrics"]["quantity"].value == "17"
    assert aggregate["metrics"]["net_quantity"].value == "7"
    groups = {group["dimensions"]["option_type"]: group["metrics"] for group in aggregate["groups"]}
    assert groups["call"]["gross_buy_quantity"]["value"] == "2"
    assert groups["call"]["gross_sell_quantity"]["value"] == "8"
    assert groups["call"]["net_quantity"]["value"] == "6"
    assert groups["call"]["floating_pnl"]["value"] == "1900"
    assert groups["put"]["net_quantity"]["value"] == "1"
    assert groups["put"]["floating_pnl"]["value"] == "-1400"


def test_gross_side_without_that_side_is_known_zero_but_unknown_direction_is_partial():
    sells = [_row("i2701-c-700", "卖", 8, pnl=100)]
    buy_metric = metric_for_positions(sells, "gross_buy_quantity")
    assert buy_metric.value == "0"
    assert buy_metric.status == "complete"

    unknown = [_row("i2701-c-700", "未知", 8, pnl=100)]
    assert metric_for_positions(unknown, "net_quantity").status == "unavailable"


def test_strike_range_is_inclusive_and_net_tons_preserves_direction():
    rows = [
        _row("i2701-c-700", "卖", 2, multiplier=100),
        _row("i2701-c-740", "买", 1, multiplier=100),
        _row("i2701-c-750", "卖", 4, multiplier=100),
    ]
    query = FactQuery(
        asset_type="option",
        filters={"contract_months": ["2701"], "strike_min": 700, "strike_max": 740},
    )
    selected, _ = select_positions(rows, query)

    assert [row["contract"] for row in selected] == ["i2701-c-700", "i2701-c-740"]
    assert metric_for_positions(selected, "net_tons").value == "100"
    assert metric_for_positions(selected, "net_wan_tons").value == "0.0100"


def test_missing_quote_is_partial_and_empty_scope_can_be_explicitly_complete():
    rows = [_row("i2701-c-700", "买", 2, pnl=100), _row("i2701-c-720", "卖", 8, pnl=None)]
    pnl = metric_for_positions(rows, "floating_pnl")
    assert pnl.value == "100"
    assert pnl.status == "partial"
    assert (pnl.covered_rows, pnl.eligible_rows) == (1, 2)

    empty = metric_for_positions([], "quantity", empty_status="complete")
    assert empty.value == "0"
    assert empty.status == "complete"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("请纯文字说明", "text"), ("请用列表说明", "text"), ("请用表格", "table"),
     ("不要表格，画图看变化", "chart"), ("查一下", "auto")],
)
def test_presentation_preference_is_explicit_and_flexible(text, expected):
    assert presentation_preference(text) == expected
