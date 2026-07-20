from datetime import date
import inspect

import pytest

from backend.app.trading_valuation import (
    MarketDataService,
    QuoteRequest,
    QuoteSnapshot,
    TqSdkQuoteProvider,
    calculate_option_position_valuation,
    calculate_position_floating_pnl,
    calculate_sh_junneng_settlement,
    select_valuation_price,
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


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (
            QuoteSnapshot(last_price=8.2, bid_price=8.0, ask_price=8.4, settlement_price=7.9),
            (8.2, "last_trade", "live"),
        ),
        (
            QuoteSnapshot(last_price=None, bid_price=8.0, ask_price=8.4, settlement_price=7.9),
            (8.2, "bid_ask_midpoint", "live"),
        ),
        (
            QuoteSnapshot(last_price=None, bid_price=None, ask_price=None, settlement_price=7.9),
            (7.9, "settlement_reference", "settlement_reference"),
        ),
        (
            QuoteSnapshot(),
            (None, "unavailable", "unavailable"),
        ),
    ],
)
def test_valuation_price_priority(snapshot, expected):
    assert select_valuation_price(snapshot) == expected


def test_option_position_valuation_scales_greeks_to_position_exposure():
    result = calculate_option_position_valuation(
        open_price=4.2,
        valuation_price=5,
        direction="买",
        remaining_quantity=2,
        multiplier=100,
        remaining_open_fee=2.4,
        unit_greeks={
            "delta": 0.4,
            "gamma": 0.01,
            "theta": -0.05,
            "vega": 0.1,
            "rho": 0.02,
        },
    )

    assert result["floating_pnl"] == pytest.approx(157.6)
    assert result["delta_exposure"] == pytest.approx(80)
    assert result["gamma_exposure"] == pytest.approx(2)
    assert result["theta_exposure"] == pytest.approx(-10)
    assert result["vega_exposure"] == pytest.approx(20)
    assert result["rho_exposure"] == pytest.approx(4)


def test_short_option_reverses_pnl_and_all_greek_exposures():
    result = calculate_option_position_valuation(
        open_price=5,
        valuation_price=4,
        direction="卖",
        remaining_quantity=3,
        multiplier=100,
        remaining_open_fee=3,
        unit_greeks={"delta": 0.4, "gamma": 0.01, "theta": -0.05, "vega": 0.1},
    )

    assert result["floating_pnl"] == pytest.approx(297)
    assert result["delta_exposure"] == pytest.approx(-120)
    assert result["gamma_exposure"] == pytest.approx(-3)
    assert result["theta_exposure"] == pytest.approx(15)
    assert result["vega_exposure"] == pytest.approx(-30)


def test_market_data_service_reuses_short_cache_and_closes_provider():
    class Provider:
        def __init__(self):
            self.fetch_count = 0
            self.closed = False

        def fetch(self, requests):
            self.fetch_count += 1
            return {request.contract: QuoteSnapshot(last_price=12.5) for request in requests}

        def close(self):
            self.closed = True

    provider = Provider()
    service = MarketDataService(provider=provider, ttl_seconds=10)
    request = QuoteRequest(contract="rb2610", exchange="SHFE")

    assert service.get_quotes([request])["rb2610"].last_price == 12.5
    assert service.get_quotes([request])["rb2610"].last_price == 12.5
    assert provider.fetch_count == 1

    service.close()
    assert provider.closed is True


def test_market_data_failure_uses_statement_settlement_without_fake_live_price():
    class FailingProvider:
        def fetch(self, requests):
            raise RuntimeError("market unavailable")

        def close(self):
            pass

    service = MarketDataService(provider=FailingProvider())
    snapshot = service.get_quotes([
        QuoteRequest(contract="i2609-C-700", settlement_price=4.8)
    ])["i2609-C-700"]

    assert snapshot.last_price is None
    assert snapshot.settlement_price == 4.8
    assert select_valuation_price(snapshot) == (
        4.8,
        "settlement_reference",
        "settlement_reference",
    )


def test_tqsdk_provider_contains_no_trading_account_or_order_operations():
    source = inspect.getsource(TqSdkQuoteProvider)
    for forbidden in ("TqAccount", "insert_order", "cancel_order"):
        assert forbidden not in source
