import inspect

import pytest
from fastapi import HTTPException

from backend.app import futures_market_readonly, trading_valuation


def _bars():
    return [
        {
            "datetime": "2026-08-10T09:00:00+08:00",
            "datetime_nano": 1_786_324_800_000_000_000,
            "symbol": "DCE.i2609",
            "open": 800.0,
            "high": 810.0,
            "low": 795.0,
            "close": 805.0,
            "volume": 100.0,
        },
        {
            "datetime": "2026-08-11T09:00:00+08:00",
            "datetime_nano": 1_786_411_200_000_000_000,
            "symbol": "DCE.i2610",
            "open": 805.0,
            "high": 812.0,
            "low": 801.0,
            "close": 809.0,
            "volume": 120.0,
        },
    ]


def test_readonly_endpoint_requires_dedicated_secret(monkeypatch):
    monkeypatch.setenv("FUTURES_MARKET_READONLY_SHARED_SECRET", "expected")

    for authorization in (None, "Bearer wrong"):
        with pytest.raises(HTTPException) as exc_info:
            futures_market_readonly.get_futures_market_klines(
                symbol="KQ.m@DCE.i",
                duration_seconds=86400,
                data_length=20,
                authorization=authorization,
            )
        assert exc_info.value.status_code == 404


def test_readonly_endpoint_returns_only_market_bars(monkeypatch):
    monkeypatch.setenv("FUTURES_MARKET_READONLY_SHARED_SECRET", "expected")
    monkeypatch.setattr(
        futures_market_readonly,
        "get_kline_bars",
        lambda symbol, duration_seconds, data_length: _bars(),
    )

    payload = futures_market_readonly.get_futures_market_klines(
        symbol="KQ.m@DCE.i",
        duration_seconds=86400,
        data_length=20,
        authorization="Bearer expected",
    )

    assert payload["read_only"] is True
    assert payload["source"] == "tqsdk"
    assert payload["bars_count"] == 2
    assert payload["main_contract_mapping"] == [
        {"effective_from": _bars()[0]["datetime"], "symbol": "DCE.i2609"},
        {"effective_from": _bars()[1]["datetime"], "symbol": "DCE.i2610"},
    ]
    assert set(payload["bars"][0]) == {
        "datetime",
        "datetime_nano",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }


def test_readonly_endpoint_rejects_symbols_and_periods_outside_gate_a(monkeypatch):
    monkeypatch.setenv("FUTURES_MARKET_READONLY_SHARED_SECRET", "expected")

    for symbol, duration_seconds in (
        ("DCE.jm2609", 86400),
        ("KQ.m@DCE.i", 300),
    ):
        with pytest.raises(HTTPException) as exc_info:
            futures_market_readonly.get_futures_market_klines(
                symbol=symbol,
                duration_seconds=duration_seconds,
                data_length=20,
                authorization="Bearer expected",
            )
        assert exc_info.value.status_code == 422


def test_readonly_endpoint_fails_closed_without_market_rows(monkeypatch):
    monkeypatch.setenv("FUTURES_MARKET_READONLY_SHARED_SECRET", "expected")
    monkeypatch.setattr(
        futures_market_readonly,
        "get_kline_bars",
        lambda symbol, duration_seconds, data_length: [],
    )

    with pytest.raises(HTTPException) as exc_info:
        futures_market_readonly.get_futures_market_klines(
            symbol="KQ.m@DCE.i",
            duration_seconds=3600,
            data_length=20,
            authorization="Bearer expected",
        )
    assert exc_info.value.status_code == 503


def test_readonly_router_has_no_mutating_method_or_business_data_import():
    route = next(
        route
        for route in futures_market_readonly.router.routes
        if route.path == "/internal/futures-market/klines"
    )
    assert route.methods == {"GET"}
    source = inspect.getsource(futures_market_readonly)
    assert "backend.app.db" not in source
    assert "trading_management" not in source
    for forbidden in (
        "TqAccount",
        "get_account",
        "get_position",
        "insert_order",
        "cancel_order",
    ):
        assert forbidden not in source


def test_tqsdk_provider_normalizes_kline_rows_in_its_existing_session(monkeypatch):
    frame = {
        "datetime": [1_700_000_000_000_000_000],
        "symbol": ["DCE.i2401"],
        "open": [800],
        "high": [810],
        "low": [790],
        "close": [805],
        "volume": [123],
    }

    class FakeApi:
        def get_kline_serial(self, symbol, duration_seconds, data_length):
            assert (symbol, duration_seconds, data_length) == (
                "KQ.m@DCE.i",
                86400,
                20,
            )
            return frame

        def wait_update(self, deadline):
            return True

        def close(self):
            return None

    monkeypatch.setattr(
        trading_valuation.TqSdkQuoteProvider,
        "_initialize",
        lambda self: FakeApi(),
    )
    provider = trading_valuation.TqSdkQuoteProvider("user", "password")
    try:
        rows = provider.fetch_klines("KQ.m@DCE.i", 86400, 20)
    finally:
        provider.close()

    assert rows[0]["symbol"] == "DCE.i2401"
    assert rows[0]["close"] == 805.0

