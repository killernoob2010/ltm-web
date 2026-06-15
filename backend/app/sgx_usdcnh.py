from __future__ import annotations

import html
import json
import re
import threading
import time
from dataclasses import dataclass, replace
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener


BARCHART_BASE_URL = "https://www.barchart.com/futures/quotes"
SGX_CACHE_TTL_SECONDS = 10 * 60
SGX_STALE_TTL_SECONDS = 6 * 60 * 60
MONTH_CODES = {
    1: "F",
    2: "G",
    3: "H",
    4: "J",
    5: "K",
    6: "M",
    7: "N",
    8: "Q",
    9: "U",
    10: "V",
    11: "X",
    12: "Z",
}


@dataclass
class SgxUsdCnhQuote:
    year: int
    month: int
    symbol: str
    contract_name: str
    last_price: float
    price_change: Optional[str]
    percent_change: Optional[str]
    trade_time: Optional[str]
    exchange: Optional[str]
    url: str
    source: str = "sgx_live"
    cached_at: Optional[float] = None


class SgxUsdCnhFetchError(RuntimeError):
    pass


_QUOTE_CACHE: dict[str, SgxUsdCnhQuote] = {}
_QUOTE_CACHE_LOCK = threading.Lock()


def barchart_symbol(year: int, month: int) -> str:
    if month not in MONTH_CODES:
        raise ValueError(f"Invalid month: {month}")
    return f"I${MONTH_CODES[month]}{year % 100:02d}"


def quote_url(symbol: str) -> str:
    return f"{BARCHART_BASE_URL}/{quote(symbol, safe='')}"


def cached_quote(symbol: str, max_age: float, source: str) -> Optional[SgxUsdCnhQuote]:
    now = time.time()
    with _QUOTE_CACHE_LOCK:
        item = _QUOTE_CACHE.get(symbol)
    if not item or item.cached_at is None or now - item.cached_at > max_age:
        return None
    return replace(item, source=source)


def save_cached_quote(quote_item: SgxUsdCnhQuote) -> SgxUsdCnhQuote:
    cached = replace(quote_item, cached_at=time.time())
    with _QUOTE_CACHE_LOCK:
        _QUOTE_CACHE[cached.symbol] = cached
    return cached


def contract_year_month(contract: str) -> tuple[int, int]:
    match = re.search(r"(\d{2})(\d{2})$", (contract or "").strip())
    if not match:
        raise ValueError(f"Invalid USD/CNH contract: {contract}")
    return 2000 + int(match.group(1)), int(match.group(2))


def extract_dynamic_config(page_html: str) -> dict:
    match = re.search(
        r'<script[^>]+id=["\']bc-dynamic-config["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.DOTALL,
    )
    if not match:
        raise SgxUsdCnhFetchError("Barchart dynamic config was not found in the page.")

    try:
        return json.loads(html.unescape(match.group(1)).strip())
    except json.JSONDecodeError as exc:
        raise SgxUsdCnhFetchError(f"Could not parse Barchart dynamic config: {exc}") from exc


def fetch_page(opener, url: str, timeout: float) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        },
    )
    with opener.open(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def fetch_sgx_usdcnh_quote(
    year: int,
    month: int,
    timeout: float = 20.0,
    retries: int = 2,
) -> SgxUsdCnhQuote:
    symbol = barchart_symbol(year, month)
    cached = cached_quote(symbol, SGX_CACHE_TTL_SECONDS, "sgx_cache")
    if cached is not None:
        return cached

    url = quote_url(symbol)
    opener = build_opener()
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            page_html = fetch_page(opener, url, timeout)
            config = extract_dynamic_config(page_html)
            current = config.get("currentSymbol") or {}
            raw = current.get("raw") or {}

            if current.get("symbol") != symbol:
                raise SgxUsdCnhFetchError(
                    f"Expected symbol {symbol}, got {current.get('symbol') or 'missing'}."
                )

            last_price = raw.get("lastPrice", current.get("lastPrice"))
            if last_price in (None, "", "N/A"):
                raise SgxUsdCnhFetchError(f"No lastPrice found for {symbol}.")

            return save_cached_quote(
                SgxUsdCnhQuote(
                    year=year,
                    month=month,
                    symbol=symbol,
                    contract_name=current.get("contractName") or f"SGX USD/CNH ({symbol})",
                    last_price=float(last_price),
                    price_change=current.get("priceChange"),
                    percent_change=current.get("percentChange"),
                    trade_time=current.get("tradeTime"),
                    exchange=current.get("exchange"),
                    url=url,
                )
            )
        except (HTTPError, URLError, TimeoutError, SgxUsdCnhFetchError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1 + attempt)

    cached = cached_quote(symbol, SGX_STALE_TTL_SECONDS, "sgx_stale")
    if cached is not None:
        return cached

    raise SgxUsdCnhFetchError(f"Failed to fetch {symbol}: {last_error}") from last_error


def fetch_sgx_usdcnh_rate(contract: str, timeout: float = 20.0, retries: int = 2) -> Optional[float]:
    try:
        year, month = contract_year_month(contract)
        return fetch_sgx_usdcnh_quote(year, month, timeout=timeout, retries=retries).last_price
    except (SgxUsdCnhFetchError, ValueError):
        return None
