from datetime import date
import inspect
import threading

import pytest

from backend.app import trading_valuation

from backend.app.trading_valuation import (
    MarketDataService,
    QuoteRequest,
    QuoteSnapshot,
    TqSdkQuoteProvider,
    calculate_option_display_greeks,
    calculate_option_position_valuation,
    calculate_position_floating_pnl,
    calculate_statement_option_metrics,
    calculate_sh_junneng_settlement,
    select_valuation_price,
)


@pytest.mark.parametrize(
    ("strike_price", "option_price", "expected_delta"),
    [
        (790, 2.8, 0.1421),
        (800, 2.2, 0.1107),
        (810, 1.5, 0.0790),
        (850, 0.7, 0.0344),
        (890, 0.5, 0.0217),
    ],
)
def test_black76_call_delta_matches_wh_same_screen_sample(
    strike_price, option_price, expected_delta
):
    result = trading_valuation.calculate_black76_option_metrics(
        option_price=option_price,
        underlying_price=745.5,
        strike_price=strike_price,
        risk_free_rate=0.015,
        time_to_expiry=28 / 360,
        option_class="CALL",
    )

    assert result["delta"] == pytest.approx(expected_delta, abs=5e-5)


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


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        (
            "买",
            {
                "delta": 0.4,
                "gamma": 0.01,
                "theta": -0.2,
                "vega": 0.3,
                "rho": 0.04,
            },
        ),
        (
            "卖",
            {
                "delta": -0.4,
                "gamma": -0.01,
                "theta": 0.2,
                "vega": -0.3,
                "rho": -0.04,
            },
        ),
    ],
)
def test_option_display_greeks_are_signed_per_lot_daily_and_per_vol_point(
    direction, expected
):
    result = calculate_option_display_greeks(
        direction=direction,
        unit_greeks={
            "delta": 0.4,
            "gamma": 0.01,
            "theta": -72,
            "vega": 30,
            "rho": 4,
        },
    )

    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    ("contract", "option_price", "underlying_price", "expected_expiry", "expected_iv"),
    [
        ("i2608-c-780", 2.9, 748, "2026-07-16", 0.20469),
        ("i2609-p-700", 4.7, 745.5, "2026-08-18", 0.18445),
    ],
)
def test_statement_option_metrics_use_same_snapshot_underlying_and_dce_expiry(
    contract, option_price, underlying_price, expected_expiry, expected_iv
):
    result = calculate_statement_option_metrics(
        contract=contract,
        option_price=option_price,
        underlying_price=underlying_price,
        valuation_date="20260630",
        exchange="大商所",
    )

    assert result["underlying_symbol"] == contract.split("-")[0]
    assert result["underlying_price"] == underlying_price
    assert result["expiry_date"] == expected_expiry
    assert result["valuation_date"] == "2026-06-30"
    assert result["iv"] == pytest.approx(expected_iv, abs=5e-5)
    for greek in ("delta", "gamma", "theta", "vega", "rho"):
        assert result[greek] is not None


def test_real_statement_sample_displays_per_lot_greeks_in_business_units():
    metrics = calculate_statement_option_metrics(
        contract="i2608-c-780",
        option_price=2.9,
        underlying_price=748,
        valuation_date="20260630",
        exchange="大商所",
    )

    display = calculate_option_display_greeks(
        direction="卖",
        unit_greeks=metrics,
    )

    assert display["delta"] == pytest.approx(-0.171144, abs=1e-6)
    assert display["gamma"] == pytest.approx(-0.007872, abs=1e-6)
    assert display["theta"] == pytest.approx(0.256163, abs=1e-6)
    assert display["vega"] == pytest.approx(-0.400666, abs=1e-6)


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


def test_market_data_service_merges_concurrent_cache_misses():
    class BlockingProvider:
        def __init__(self):
            self.fetch_count = 0
            self.started = threading.Event()
            self.release = threading.Event()

        def fetch(self, requests):
            self.fetch_count += 1
            self.started.set()
            self.release.wait(timeout=1)
            return {request.contract: QuoteSnapshot(last_price=12.5) for request in requests}

        def close(self):
            pass

    provider = BlockingProvider()
    service = MarketDataService(provider=provider, ttl_seconds=10)
    request = QuoteRequest(contract="rb2610", exchange="SHFE")
    results = []
    first = threading.Thread(
        target=lambda: results.append(service.get_quotes([request]))
    )
    second = threading.Thread(
        target=lambda: results.append(service.get_quotes([request]))
    )

    first.start()
    assert provider.started.wait(timeout=1)
    second.start()
    provider.release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert len(results) == 2
    assert provider.fetch_count == 1
    assert all(result["rb2610"].last_price == 12.5 for result in results)


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
    assert snapshot.market_data_status == "provider_error"
    assert snapshot.market_data_message == "天勤行情读取失败"


def test_market_data_service_retries_provider_initialization():
    class Provider:
        def fetch(self, requests):
            return {
                request.contract: QuoteSnapshot(last_price=12.5)
                for request in requests
            }

        def close(self):
            pass

    attempts = 0

    def provider_factory():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary authentication timeout")
        return Provider()

    service = MarketDataService(
        provider_factory=provider_factory,
        provider_retry_seconds=0,
        ttl_seconds=0,
    )
    request = QuoteRequest(contract="rb2610", exchange="SHFE")

    first = service.get_quotes([request])["rb2610"]
    second = service.get_quotes([request])["rb2610"]

    assert first.market_data_status == "provider_error"
    assert first.market_data_message == "天勤行情连接失败，系统将自动重试"
    assert second.last_price == 12.5
    assert second.market_data_status == "live"
    assert attempts == 2


def test_tqsdk_provider_contains_no_live_trading_account_or_order_operations():
    source = inspect.getsource(TqSdkQuoteProvider)
    for forbidden in ("TqAccount", "insert_order", "cancel_order"):
        assert forbidden not in source


def test_tqsdk_provider_calculates_black76_from_the_same_quote_snapshot(monkeypatch):
    now = 1_774_073_600.0
    monkeypatch.setattr(trading_valuation.time, "time", lambda: now)

    class Quote:
        def __init__(self, **values):
            self.__dict__.update(values)

    option = Quote(
        underlying_symbol="DCE.i2609",
        datetime="2026-03-20 14:00:00.000000",
        expire_datetime=now + 28 * 24 * 60 * 60,
        last_price=2.2,
        bid_price1=2.1,
        ask_price1=2.2,
        settlement=2.0,
        strike_price=800,
        option_class="CALL",
        volume_multiple=100,
        expired=False,
    )
    underlying = Quote(
        datetime="2026-03-20 14:00:00.000000",
        last_price=747,
    )

    class Api:
        def get_quote(self, symbol):
            return option if symbol == "DCE.i2609-C-800" else underlying

        def wait_update(self, deadline):
            return True

        def query_option_greeks(self, *args, **kwargs):
            raise AssertionError("Black-76 must not reuse TqSdk BS Greeks")

    provider = TqSdkQuoteProvider.__new__(TqSdkQuoteProvider)
    provider._api = Api()
    result = provider._fetch([
        QuoteRequest(
            contract="i2609-c-800",
            exchange="DCE",
            asset_type="option",
        )
    ])["i2609-c-800"]

    assert result.iv == pytest.approx(0.19811, abs=5e-5)
    assert result.delta == pytest.approx(0.11243, abs=5e-5)
    assert result.gamma == pytest.approx(0.00463, abs=5e-5)
    assert result.theta / 360 == pytest.approx(-0.14063, abs=5e-5)
    assert result.vega / 100 == pytest.approx(0.39777, abs=5e-5)
