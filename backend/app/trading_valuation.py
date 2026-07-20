from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor
import math
import os
import threading
import time
from typing import Any, Optional, Protocol, Union


SH_JUNNENG_RULE_VERSION = "sh_junneng_v1"
OPTION_RISK_FREE_RATE = 0.015


@dataclass(frozen=True)
class QuoteRequest:
    contract: str
    exchange: str = ""
    asset_type: str = ""
    settlement_price: Optional[float] = None


@dataclass
class QuoteSnapshot:
    last_price: Optional[float] = None
    bid_price: Optional[float] = None
    ask_price: Optional[float] = None
    settlement_price: Optional[float] = None
    market_time: Optional[str] = None
    source: str = ""
    multiplier: Optional[float] = None
    underlying_symbol: Optional[str] = None
    underlying_price: Optional[float] = None
    expiry_date: Optional[str] = None
    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None
    expired: bool = False


def _valid_price(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def select_valuation_price(
    snapshot: QuoteSnapshot,
) -> tuple[Optional[float], str, str]:
    if snapshot.expired:
        return None, "expired", "expired"
    last_price = _valid_price(snapshot.last_price)
    if last_price is not None:
        return last_price, "last_trade", "live"
    bid_price = _valid_price(snapshot.bid_price)
    ask_price = _valid_price(snapshot.ask_price)
    if bid_price is not None and ask_price is not None:
        return (bid_price + ask_price) / 2, "bid_ask_midpoint", "live"
    settlement_price = _valid_price(snapshot.settlement_price)
    if settlement_price is not None:
        return settlement_price, "settlement_reference", "settlement_reference"
    return None, "unavailable", "unavailable"


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


def calculate_option_position_valuation(
    *,
    open_price: float,
    valuation_price: float,
    direction: str,
    remaining_quantity: float,
    multiplier: float,
    remaining_open_fee: float,
    unit_greeks: dict[str, Optional[float]],
) -> dict[str, Optional[float]]:
    direction_factor = 1.0 if direction == "买" else -1.0
    scale = direction_factor * float(remaining_quantity) * float(multiplier)
    result: dict[str, Optional[float]] = {
        "floating_pnl": calculate_position_floating_pnl(
            open_price=open_price,
            market_price=valuation_price,
            direction=direction,
            remaining_quantity=remaining_quantity,
            multiplier=multiplier,
            remaining_open_fee=remaining_open_fee,
        ),
    }
    for name in ("delta", "gamma", "theta", "vega", "rho"):
        value = unit_greeks.get(name)
        result[f"{name}_exposure"] = (
            round(float(value) * scale, 8) if value is not None else None
        )
    return result


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


class QuoteProvider(Protocol):
    def fetch(self, requests: list[QuoteRequest]) -> dict[str, QuoteSnapshot]:
        ...

    def close(self) -> None:
        ...


_EXCHANGE_CODES = {
    "shfe": "SHFE",
    "上期所": "SHFE",
    "dce": "DCE",
    "大商所": "DCE",
    "czce": "CZCE",
    "郑商所": "CZCE",
    "cffex": "CFFEX",
    "中金所": "CFFEX",
    "ine": "INE",
    "上期能源": "INE",
    "gfex": "GFEX",
    "广期所": "GFEX",
}


def _tqsdk_symbol(request: QuoteRequest) -> str:
    exchange = _EXCHANGE_CODES.get(request.exchange.lower(), request.exchange.upper())
    contract = request.contract
    if request.asset_type == "option":
        parts = contract.split("-")
        if len(parts) == 3:
            contract = f"{parts[0]}-{parts[1].upper()}-{parts[2]}"
    return f"{exchange}.{contract}" if exchange else contract


class TqSdkQuoteProvider:
    """One read-only TqSdk market-data session; no trading account is constructed."""

    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tqsdk-market-data"
        )
        self._api = self._executor.submit(self._initialize).result()

    def _initialize(self):
        from tqsdk import TqApi, TqAuth

        return TqApi(
            auth=TqAuth(self._username, self._password),
            web_gui=False,
            disable_print=True,
        )

    def fetch(self, requests: list[QuoteRequest]) -> dict[str, QuoteSnapshot]:
        return self._executor.submit(self._fetch, requests).result()

    def _fetch(self, requests: list[QuoteRequest]) -> dict[str, QuoteSnapshot]:
        from pandas import Series
        from tqsdk import tafunc

        symbol_by_contract = {
            request.contract: _tqsdk_symbol(request) for request in requests
        }
        quote_by_contract = {
            contract: self._api.get_quote(symbol)
            for contract, symbol in symbol_by_contract.items()
        }
        option_contracts = [
            request.contract for request in requests if request.asset_type == "option"
        ]
        underlying_quotes: dict[str, Any] = {}
        for contract in option_contracts:
            underlying_symbol = getattr(
                quote_by_contract[contract], "underlying_symbol", ""
            )
            if underlying_symbol:
                underlying_quotes[underlying_symbol] = self._api.get_quote(
                    underlying_symbol
                )
        quote_objects = list(quote_by_contract.values()) + list(
            underlying_quotes.values()
        )
        deadline = time.time() + 3
        while any(not getattr(quote, "datetime", "") for quote in quote_objects):
            if not self._api.wait_update(deadline=deadline):
                break
        greek_by_symbol: dict[str, dict[str, Any]] = {}
        if option_contracts:
            symbols = [symbol_by_contract[contract] for contract in option_contracts]
            frame = self._api.query_option_greeks(
                symbols, v=None, r=OPTION_RISK_FREE_RATE
            )
            for _, row in frame.iterrows():
                greek_by_symbol[str(row["instrument_id"])] = dict(row)
        results: dict[str, QuoteSnapshot] = {}
        for request in requests:
            quote = quote_by_contract[request.contract]
            symbol = symbol_by_contract[request.contract]
            expiry_timestamp = _valid_price(
                getattr(quote, "expire_datetime", None)
            )
            expiry_date = (
                datetime.fromtimestamp(expiry_timestamp).date().isoformat()
                if expiry_timestamp is not None else None
            )
            underlying_symbol = getattr(quote, "underlying_symbol", None)
            underlying_quote = underlying_quotes.get(underlying_symbol)
            underlying_price = (
                _valid_price(getattr(underlying_quote, "last_price", None))
                if underlying_quote is not None else None
            )
            last_price = _valid_price(getattr(quote, "last_price", None))
            bid_price = _valid_price(getattr(quote, "bid_price1", None))
            ask_price = _valid_price(getattr(quote, "ask_price1", None))
            option_price = last_price or (
                (bid_price + ask_price) / 2
                if bid_price is not None and ask_price is not None else None
            )
            iv = None
            strike_price = _valid_price(getattr(quote, "strike_price", None))
            option_class = str(getattr(quote, "option_class", "") or "").upper()
            if (
                option_price is not None
                and underlying_price is not None
                and strike_price is not None
                and expiry_timestamp is not None
                and option_class in {"CALL", "PUT"}
            ):
                time_to_expiry = max(
                    (expiry_timestamp - time.time()) / (360 * 24 * 60 * 60),
                    1 / 360,
                )
                iv_series = tafunc.get_impv(
                    Series([underlying_price]),
                    Series([option_price]),
                    strike_price,
                    OPTION_RISK_FREE_RATE,
                    0.2,
                    time_to_expiry,
                    option_class,
                )
                iv = _valid_price(iv_series.iloc[-1])
            greek = greek_by_symbol.get(symbol, {})
            results[request.contract] = QuoteSnapshot(
                last_price=last_price,
                bid_price=bid_price,
                ask_price=ask_price,
                settlement_price=(
                    _valid_price(getattr(quote, "settlement", None))
                    or request.settlement_price
                ),
                market_time=str(getattr(quote, "datetime", "") or "") or None,
                source="tqsdk",
                multiplier=_valid_price(getattr(quote, "volume_multiple", None)),
                underlying_symbol=underlying_symbol,
                underlying_price=underlying_price,
                expiry_date=expiry_date,
                iv=iv,
                delta=_finite_number(greek.get("delta")),
                gamma=_finite_number(greek.get("gamma")),
                theta=_finite_number(greek.get("theta")),
                vega=_finite_number(greek.get("vega")),
                rho=_finite_number(greek.get("rho")),
                expired=bool(getattr(quote, "expired", False)),
            )
        return results

    def close(self) -> None:
        self._executor.submit(self._api.close).result()
        self._executor.shutdown(wait=True)


class MarketDataService:
    def __init__(
        self,
        provider: Optional[QuoteProvider] = None,
        ttl_seconds: float = 10,
    ):
        self.provider = provider
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, QuoteSnapshot]] = {}
        self._lock = threading.Lock()

    def get_quotes(
        self, requests: list[QuoteRequest]
    ) -> dict[str, QuoteSnapshot]:
        now = time.monotonic()
        result: dict[str, QuoteSnapshot] = {}
        missing: list[QuoteRequest] = []
        with self._lock:
            for request in requests:
                cached = self._cache.get(request.contract)
                if cached and cached[0] > now:
                    result[request.contract] = cached[1]
                else:
                    missing.append(request)
        fetched: dict[str, QuoteSnapshot] = {}
        if missing and self.provider is not None:
            try:
                fetched = self.provider.fetch(missing)
            except Exception:
                fetched = {}
        with self._lock:
            for request in missing:
                snapshot = fetched.get(request.contract) or QuoteSnapshot(
                    settlement_price=request.settlement_price,
                    source="settlement_statement" if request.settlement_price else "",
                )
                if snapshot.settlement_price is None:
                    snapshot.settlement_price = request.settlement_price
                self._cache[request.contract] = (
                    now + self.ttl_seconds,
                    snapshot,
                )
                result[request.contract] = snapshot
        return result

    def close(self) -> None:
        if self.provider is not None:
            self.provider.close()


def _default_market_data_service() -> MarketDataService:
    username = os.getenv("TQSDK_USERNAME", "").strip()
    password = os.getenv("TQSDK_PASSWORD", "").strip()
    provider: Optional[QuoteProvider] = None
    if username and password:
        try:
            provider = TqSdkQuoteProvider(username, password)
        except Exception:
            provider = None
    return MarketDataService(provider=provider)


_market_data_service: Optional[MarketDataService] = None
_service_lock = threading.Lock()


def get_quote_snapshots(
    requests: list[QuoteRequest],
) -> dict[str, QuoteSnapshot]:
    global _market_data_service
    if _market_data_service is None:
        with _service_lock:
            if _market_data_service is None:
                _market_data_service = _default_market_data_service()
    return _market_data_service.get_quotes(requests)


def close_market_data_service() -> None:
    global _market_data_service
    with _service_lock:
        service = _market_data_service
        _market_data_service = None
    if service is not None:
        service.close()
