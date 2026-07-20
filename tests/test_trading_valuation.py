from datetime import date

import pytest

from backend.app.trading_valuation import (
    calculate_position_floating_pnl,
    calculate_sh_junneng_settlement,
)


@pytest.mark.parametrize(
    ("direction", "open_price", "market_price", "expected"),
    [
        ("买", 3100, 3120, 394),
        ("卖", 3100, 3080, 394),
    ],
)
def test_futures_floating_pnl_uses_direction_multiplier_and_remaining_fee(
    direction, open_price, market_price, expected
):
    assert calculate_position_floating_pnl(
        open_price=open_price,
        market_price=market_price,
        direction=direction,
        remaining_quantity=2,
        multiplier=10,
        remaining_open_fee=6,
    ) == expected


def test_junneng_same_day_settlement_charges_one_day_interest():
    result = calculate_sh_junneng_settlement(
        gross_pnl=400,
        allocated_open_fee=6,
        allocated_close_fee=6,
        open_date=date(2026, 6, 20),
        close_date=date(2026, 6, 20),
        matched_quantity=2,
        open_price=3100,
        multiplier=10,
    )

    assert result == {
        "net_close_pnl": 388.0,
        "fund_interest": 0.84,
        "settlement_80": 309.73,
        "settlement_20": 77.43,
        "settlement_rule_version": "sh_junneng_v1",
    }


def test_junneng_loss_still_charges_interest_without_profit_sharing():
    result = calculate_sh_junneng_settlement(
        gross_pnl=-100,
        allocated_open_fee=3,
        allocated_close_fee=2,
        open_date="20260620",
        close_date="20260630",
        matched_quantity=1,
        open_price=3000,
        multiplier=10,
    )

    assert result["net_close_pnl"] == -105
    assert result["fund_interest"] == 4.08
    assert result["settlement_80"] == 0
    assert result["settlement_20"] == 0
    assert result["settlement_rule_version"] == "sh_junneng_v1"
