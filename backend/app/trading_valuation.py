from __future__ import annotations

from datetime import date, datetime
from typing import Union


SH_JUNNENG_RULE_VERSION = "sh_junneng_v1"


def _as_date(value: Union[str, date]) -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    return datetime.strptime(text, "%Y%m%d" if "-" not in text else "%Y-%m-%d").date()


def calculate_position_floating_pnl(
    *,
    open_price: float,
    market_price: float,
    direction: str,
    remaining_quantity: float,
    multiplier: float,
    remaining_open_fee: float = 0,
) -> float:
    difference = (
        float(market_price) - float(open_price)
        if direction == "买"
        else float(open_price) - float(market_price)
    )
    return round(
        difference * float(remaining_quantity) * float(multiplier)
        - float(remaining_open_fee or 0),
        2,
    )


def calculate_sh_junneng_settlement(
    *,
    gross_pnl: float,
    allocated_open_fee: float,
    allocated_close_fee: float,
    open_date: Union[str, date],
    close_date: Union[str, date],
    matched_quantity: float,
    open_price: float,
    multiplier: float,
) -> dict[str, float | str]:
    holding_days = max((_as_date(close_date) - _as_date(open_date)).days, 1)
    net_close_pnl = round(
        float(gross_pnl)
        - float(allocated_open_fee or 0)
        - float(allocated_close_fee or 0),
        2,
    )
    fund_interest = round(
        float(matched_quantity)
        * float(multiplier)
        * float(open_price)
        * 0.07
        * 0.07
        * holding_days
        / 360,
        2,
    )
    distributable = max(net_close_pnl - fund_interest, 0)
    return {
        "net_close_pnl": net_close_pnl,
        "fund_interest": fund_interest,
        "settlement_80": round(distributable * 0.8, 2),
        "settlement_20": round(distributable * 0.2, 2),
        "settlement_rule_version": SH_JUNNENG_RULE_VERSION,
    }
