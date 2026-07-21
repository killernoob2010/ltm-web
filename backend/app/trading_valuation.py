from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import math
import os
import re
import threading
import time
from typing import Any, Callable, Optional, Protocol, Union


SH_JUNNENG_RULE_VERSION = "sh_junneng_v1"
OPTION_RISK_FREE_RATE = 0.015
TQSDK_FETCH_TIMEOUT_SECONDS = 5
_DCE_OPTION_CONTRACT_RE = re.compile(
    r"^(?P<product>[a-z]+)(?P<year>\d{2})(?P<month>\d{2})-"
    r"(?P<option_class>c|p)-(?P<strike>\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)


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
    market_data_status: str = ""
    market_data_message: str = ""


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


def calculate_option_display_greeks(
    *,
    direction: str,
    unit_greeks: dict[str, Optional[float]],
) -> dict[str, Optional[float]]:
    direction_factor = 1.0 if direction == "买" else -1.0
    divisors = {
        "delta": 1.0,
        "gamma": 1.0,
        "theta": 360.0,
        "vega": 100.0,
        "rho": 100.0,
    }
    return {
        name: (
            float(unit_greeks[name]) * direction_factor / divisor
            if unit_greeks.get(name) is not None
            else None
        )
        for name, divisor in divisors.items()
    }


def _standard_dce_option_expiry(contract: str) -> Optional[date]:
    match = _DCE_OPTION_CONTRACT_RE.match(str(contract or "").strip())
    if not match:
        return None
    delivery_year = 2000 + int(match.group("year"))
    delivery_month = int(match.group("month"))
    previous_month_end = date(delivery_year, delivery_month, 1) - timedelta(days=1)
    day = previous_month_end.replace(day=1)
    trading_days = 0
    while day.month == previous_month_end.month:
        if day.weekday() < 5:
            trading_days += 1
            if trading_days == 12:
                return day
        day += timedelta(days=1)
    return None


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2 * math.pi)


def _black76_price(
    futures_price: float,
    strike_price: float,
    risk_free_rate: float,
    volatility: float,
    time_to_expiry: float,
    option_class: str,
) -> float:
    sqrt_time = math.sqrt(time_to_expiry)
    d1 = (
        math.log(futures_price / strike_price)
        + 0.5 * volatility * volatility * time_to_expiry
    ) / (volatility * sqrt_time)
    d2 = d1 - volatility * sqrt_time
    option_sign = 1 if option_class == "CALL" else -1
    discount_factor = math.exp(-risk_free_rate * time_to_expiry)
    return discount_factor * option_sign * (
        futures_price * _normal_cdf(option_sign * d1)
        - strike_price * _normal_cdf(option_sign * d2)
    )


def calculate_black76_option_metrics(
    *,
    option_price: float,
    underlying_price: float,
    strike_price: float,
    risk_free_rate: float,
    time_to_expiry: float,
    option_class: str,
) -> dict[str, Optional[float]]:
    futures_value = _valid_price(underlying_price)
    option_value = _valid_price(option_price)
    strike_value = _valid_price(strike_price)
    rate_value = _finite_number(risk_free_rate)
    time_value = _valid_price(time_to_expiry)
    option_kind = str(option_class or "").upper()
    if (
        futures_value is None
        or option_value is None
        or strike_value is None
        or rate_value is None
        or time_value is None
        or option_kind not in {"CALL", "PUT"}
    ):
        return {}

    low = 1e-6
    high = 5.0
    low_price = _black76_price(
        futures_value, strike_value, rate_value, low, time_value, option_kind
    )
    high_price = _black76_price(
        futures_value, strike_value, rate_value, high, time_value, option_kind
    )
    if option_value < low_price - 1e-8 or option_value > high_price + 1e-8:
        return {}
    for _ in range(100):
        mid = (low + high) / 2
        model_price = _black76_price(
            futures_value,
            strike_value,
            rate_value,
            mid,
            time_value,
            option_kind,
        )
        if abs(model_price - option_value) < 1e-10:
            low = high = mid
            break
        if model_price < option_value:
            low = mid
        else:
            high = mid
    iv = (low + high) / 2
    sqrt_time = math.sqrt(time_value)
    d1 = (
        math.log(futures_value / strike_value)
        + 0.5 * iv * iv * time_value
    ) / (iv * sqrt_time)
    option_sign = 1 if option_kind == "CALL" else -1
    discount_factor = math.exp(-rate_value * time_value)
    model_price = _black76_price(
        futures_value,
        strike_value,
        rate_value,
        iv,
        time_value,
        option_kind,
    )
    density = _normal_pdf(d1)
    return {
        "iv": iv,
        "delta": discount_factor
        * option_sign
        * _normal_cdf(option_sign * d1),
        "gamma": discount_factor
        * density
        / (futures_value * iv * sqrt_time),
        "theta": rate_value * model_price
        - discount_factor * futures_value * density * iv / (2 * sqrt_time),
        "vega": discount_factor * futures_value * sqrt_time * density,
        "rho": -time_value * model_price,
    }


def calculate_statement_option_metrics(
    *,
    contract: str,
    option_price: float,
    underlying_price: float,
    valuation_date: Union[str, date],
    exchange: str,
    risk_free_rate: float = OPTION_RISK_FREE_RATE,
) -> dict[str, Optional[float] | str]:
    match = _DCE_OPTION_CONTRACT_RE.match(str(contract or "").strip())
    if not match or str(exchange or "").strip().lower() not in {
        "dce", "大商所",
    }:
        return {}
    as_of = _as_date(valuation_date)
    expiry = _standard_dce_option_expiry(contract)
    if expiry is None or expiry <= as_of:
        return {}
    option_value = _valid_price(option_price)
    underlying_value = _valid_price(underlying_price)
    strike_price = _valid_price(match.group("strike"))
    if (
        option_value is None
        or underlying_value is None
        or strike_price is None
    ):
        return {}
    option_class = "CALL" if match.group("option_class").lower() == "c" else "PUT"
    time_to_expiry = (expiry - as_of).days / 360
    metrics = calculate_black76_option_metrics(
        option_price=option_value,
        underlying_price=underlying_value,
        strike_price=strike_price,
        risk_free_rate=risk_free_rate,
        time_to_expiry=time_to_expiry,
        option_class=option_class,
    )
    if not metrics:
        return {}
    return {
        "underlying_symbol": match.group("product").lower()
        + match.group("year")
        + match.group("month"),
        "underlying_price": underlying_value,
        "expiry_date": expiry.isoformat(),
        "valuation_date": as_of.isoformat(),
        **metrics,
    }


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
    """One market-data session without a live brokerage account or order calls."""

    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="tqsdk-market-data"
        )
        self._fetch_future: Optional[Future] = None
        self._future_lock = threading.Lock()
        initialization = self._executor.submit(self._initialize)
        try:
            self._api = initialization.result(
                timeout=TQSDK_FETCH_TIMEOUT_SECONDS
            )
        except FutureTimeoutError:
            def close_late_api(future: Future) -> None:
                try:
                    future.result().close()
                except Exception:
                    pass

            initialization.add_done_callback(close_late_api)
            self._executor.shutdown(wait=False, cancel_futures=True)
            raise
        except Exception:
            initialization.cancel()
            self._executor.shutdown(wait=False, cancel_futures=True)
            raise

    def _initialize(self):
        from tqsdk import TqApi, TqAuth

        return TqApi(
            auth=TqAuth(self._username, self._password),
            web_gui=False,
            disable_print=True,
        )

    def fetch(self, requests: list[QuoteRequest]) -> dict[str, QuoteSnapshot]:
        with self._future_lock:
            if self._fetch_future is not None:
                if not self._fetch_future.done():
                    raise RuntimeError("TqSdk market refresh is still in progress")
                completed = self._fetch_future
                self._fetch_future = None
                return completed.result()
            future = self._executor.submit(self._fetch, requests)
            self._fetch_future = future
        try:
            result = future.result(timeout=TQSDK_FETCH_TIMEOUT_SECONDS)
        except FutureTimeoutError as exc:
            raise RuntimeError("TqSdk market refresh timed out") from exc
        except Exception:
            with self._future_lock:
                if self._fetch_future is future:
                    self._fetch_future = None
            raise
        with self._future_lock:
            if self._fetch_future is future:
                self._fetch_future = None
        return result

    def _fetch(self, requests: list[QuoteRequest]) -> dict[str, QuoteSnapshot]:
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
        self._api.wait_update(deadline=min(deadline, time.time() + 1))
        while any(not getattr(quote, "datetime", "") for quote in quote_objects):
            if not self._api.wait_update(deadline=deadline):
                break
        results: dict[str, QuoteSnapshot] = {}
        for request in requests:
            quote = quote_by_contract[request.contract]
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
            settlement_price = request.settlement_price or _valid_price(
                getattr(quote, "settlement", None)
            )
            option_price = last_price or (
                (bid_price + ask_price) / 2
                if bid_price is not None and ask_price is not None else None
            ) or settlement_price
            strike_price = _valid_price(getattr(quote, "strike_price", None))
            option_class = str(getattr(quote, "option_class", "") or "").upper()
            metrics: dict[str, Optional[float]] = {}
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
                metrics = calculate_black76_option_metrics(
                    option_price=option_price,
                    underlying_price=underlying_price,
                    strike_price=strike_price,
                    risk_free_rate=OPTION_RISK_FREE_RATE,
                    time_to_expiry=time_to_expiry,
                    option_class=option_class,
                )
            results[request.contract] = QuoteSnapshot(
                last_price=last_price,
                bid_price=bid_price,
                ask_price=ask_price,
                settlement_price=settlement_price,
                market_time=str(getattr(quote, "datetime", "") or "") or None,
                source="tqsdk",
                multiplier=_valid_price(getattr(quote, "volume_multiple", None)),
                underlying_symbol=underlying_symbol,
                underlying_price=underlying_price,
                expiry_date=expiry_date,
                iv=metrics.get("iv"),
                delta=metrics.get("delta"),
                gamma=metrics.get("gamma"),
                theta=metrics.get("theta"),
                vega=metrics.get("vega"),
                rho=metrics.get("rho"),
                expired=bool(getattr(quote, "expired", False)),
            )
        return results

    def close(self) -> None:
        try:
            self._executor.submit(self._api.close).result()
        finally:
            self._executor.shutdown(wait=True)


class MarketDataService:
    def __init__(
        self,
        provider: Optional[QuoteProvider] = None,
        provider_factory: Optional[Callable[[], QuoteProvider]] = None,
        provider_retry_seconds: float = 30,
        ttl_seconds: float = 10,
    ):
        self.provider = provider
        self.provider_factory = provider_factory
        self.provider_retry_seconds = provider_retry_seconds
        self.provider_status = "live" if provider is not None else "not_configured"
        self.provider_message = (
            "" if provider is not None else "天勤行情认证未配置"
        )
        self._next_provider_retry = 0.0
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[float, QuoteSnapshot]] = {}
        self._lock = threading.Lock()
        self._fetch_lock = threading.Lock()

    def _ensure_provider(self, now: float) -> None:
        if (
            self.provider is not None
            or self.provider_factory is None
            or now < self._next_provider_retry
        ):
            return
        try:
            self.provider = self.provider_factory()
        except Exception:
            self.provider_status = "provider_error"
            self.provider_message = "天勤行情连接失败，系统将自动重试"
            self._next_provider_retry = now + self.provider_retry_seconds
            return
        self.provider_status = "live"
        self.provider_message = ""

    def get_quotes(
        self, requests: list[QuoteRequest]
    ) -> dict[str, QuoteSnapshot]:
        with self._fetch_lock:
            now = time.monotonic()
            self._ensure_provider(now)
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
                    self.provider_status = "live"
                    self.provider_message = ""
                except Exception:
                    fetched = {}
                    self.provider_status = "provider_error"
                    self.provider_message = "天勤行情读取失败"
            with self._lock:
                for request in missing:
                    snapshot = fetched.get(request.contract)
                    if snapshot is not None:
                        snapshot.market_data_status = "live"
                        snapshot.market_data_message = ""
                    else:
                        snapshot = QuoteSnapshot(
                            settlement_price=request.settlement_price,
                            source=(
                                "settlement_statement"
                                if request.settlement_price else ""
                            ),
                            market_data_status=self.provider_status,
                            market_data_message=self.provider_message,
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
    if not username or not password:
        return MarketDataService()
    return MarketDataService(
        provider_factory=lambda: TqSdkQuoteProvider(username, password)
    )


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
