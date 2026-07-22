from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import base64
import calendar
import csv
import io
import json
import os
from typing import List, Literal, Optional
import re
import statistics
import threading
import time
import uuid

import requests

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from pypinyin import lazy_pinyin
except ImportError:  # Local environments must install requirements before using username suggestions.
    lazy_pinyin = None

from . import db
from .permissions import (
    ACTIVE_BUSINESS_MODULES,
    DEPARTMENTS,
    RETIRED_MODULE_CODES,
    USER_ROLES,
    default_permission_levels,
    get_user_permissions as list_user_permissions,
    require_permission,
)
from .user_policy import temporary_password_policy
from .cache_service import (
    cache_counts,
    get_cached_data,
    get_all_prices_for_info_type,
    get_latest_cached_data,
    import_desktop_cache,
    save_calculated_data,
)
from .cache_ttl import ttl_cached
from .info_summary_backfill import (
    BackfillRequest,
    get_last_backfill_status,
    get_last_close_cache_update_status,
    run_all_info_summary_backfills,
    start_daily_close_cache_scheduler,
)
from .iron_ore_basis_snapshot_sync import start_iron_ore_basis_sync_scheduler
from .monitoring import get_monitoring_status, start_monitoring_loop
from .order_finance_snapshot_sync import start_order_finance_sync_scheduler
from .sgx_usdcnh import fetch_sgx_usdcnh_rate
from . import (
    data_visualization,
    iron_ore_basis,
    iron_ore_basis_snapshot_sync,
    operation_log_archive,
    order_finance,
    order_finance_snapshot_sync,
    trading_management,
    trading_valuation,
)


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"

app = FastAPI(title="轻量化交易管理系统 Web", version="0.1.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)


def configured_allowed_origins() -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [
        "https://ltm-web-gt13.onrender.com",
        "https://ltm-web-staging.onrender.com",
        "http://localhost:8000",
        "http://localhost:8001",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8001",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=configured_allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_performance_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    if request.url.path.startswith("/api"):
        log = {
            "event": "api_request",
            "request_id": request_id,
            "endpoint": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
            "response_size_approx": response.headers.get("content-length"),
        }
        print(json.dumps(log, ensure_ascii=False))
    response.headers["x-request-id"] = request_id
    return response

HISTORY_CACHE_MAX_STALE_DAYS = 7
LOGIN_FAILURES: dict[str, list[float]] = {}
LOGIN_FAILURE_LIMIT = int(os.getenv("LOGIN_FAILURE_LIMIT", "5"))
LOGIN_FAILURE_WINDOW_SECONDS = int(os.getenv("LOGIN_FAILURE_WINDOW_SECONDS", "300"))
USER_SESSION_TTL_HOURS = int(os.getenv("USER_SESSION_TTL_HOURS", str(24 * 7)))
GUEST_SESSION_TTL_HOURS = int(os.getenv("GUEST_SESSION_TTL_HOURS", "8"))
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.include_router(data_visualization.router, prefix="/api")
app.include_router(iron_ore_basis.router, prefix="/api")
app.include_router(iron_ore_basis_snapshot_sync.router, prefix="/api")
app.include_router(order_finance.router, prefix="/api")
app.include_router(order_finance_snapshot_sync.router, prefix="/api")
app.include_router(trading_management.router, prefix="/api/trading-management")


class LoginRequest(BaseModel):
    username: str
    password: str


class AlertSettingIn(BaseModel):
    info_type: str = Field(min_length=1)
    contract_year: str = "2026"
    contract_month: str = Field(min_length=1)
    alert_value: float
    direction: str = "above"
    status: str = "enabled"


class InfoIndicatorIn(BaseModel):
    info_type: str = Field(min_length=1)
    year: int = 2026
    month: str = Field(min_length=1)
    calc_date: str = Field(min_length=1)
    t_1_value: float = 0
    t_2_value: float = 0
    mean_value: float = 0
    min_value: float = 0
    max_value: float = 0
    std_value: float = 0


class StrategyGroupIn(BaseModel):
    group_name: str = Field(min_length=1)


class StrategyPositionIn(BaseModel):
    variety: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    open_price: float
    quantity: int
    contract: str = ""


class ShJunnengTradeIn(BaseModel):
    contract_month: str = Field(min_length=1)
    direction: str = Field(min_length=1)
    open_price: float
    trade_quantity: float = Field(gt=0)
    open_fee: float = 0
    open_date: str = Field(default_factory=lambda: date.today().isoformat())
    current_price: Optional[float] = None
    close_price: Optional[float] = None
    close_fee: Optional[float] = None
    close_date: Optional[str] = None
    is_closed: str = "未平仓"


class ShJunnengTradeCloseIn(BaseModel):
    close_quantity: Optional[float] = Field(default=None, gt=0)
    close_price: float
    close_fee: float = 0
    close_date: str = Field(default_factory=lambda: date.today().isoformat())


class ShJunnengManualPricesIn(BaseModel):
    prices: dict[str, float]


class InfoCalculateIn(BaseModel):
    info_type: str = Field(min_length=1)
    year: int = 2026
    month: str = "09"
    calc_date: str = Field(default_factory=lambda: date.today().isoformat())
    year1: Optional[int] = None
    month1: Optional[str] = None
    year2: Optional[int] = None
    month2: Optional[str] = None


class InfoCalculateAllIn(BaseModel):
    items: List[InfoCalculateIn]
    audit_source: Literal["automatic", "manual"] = "automatic"


class InfoBackfillIn(BaseModel):
    info_type: Optional[str] = None
    calc_date: str = Field(default_factory=lambda: date.today().isoformat())
    force: bool = False


VARIETY_CONFIG = {
    "I": {"name": "铁矿石（连铁）", "multiplier": 100, "sina_prefix": "nf_I", "has_contract": True},
    "J": {"name": "焦炭", "multiplier": 100, "sina_prefix": "nf_J", "has_contract": True},
    "JM": {"name": "焦煤", "multiplier": 60, "sina_prefix": "nf_JM", "has_contract": True},
    "RB": {"name": "螺纹钢", "multiplier": 10, "sina_prefix": "nf_RB", "has_contract": True},
    "HC": {"name": "热卷", "multiplier": 10, "sina_prefix": "nf_HC", "has_contract": True},
    "FE": {"name": "铁矿石（新加坡）", "multiplier": 100, "sina_prefix": "hf_FEF", "has_contract": True},
    "USD/CNY": {"name": "离岸人民币（USD/CNH）", "multiplier": 1, "sina_prefix": "fx_susdcnh", "has_contract": True},
}

INFO_TYPES = ["卷螺差", "螺矿比", "煤矿比", "盘面钢厂利润", "月差", "掉期月差", "内外盘差", "内外盘差2"]
MONTH_DIFF_TYPES = ["月差", "掉期月差"]
SPECIAL_MONTH_OPTIONS = {
    "螺矿比": ["01", "05", "09"],
    "盘面钢厂利润": ["01", "05", "09"],
}
INNER_OUTER_MONTH_COUNT = 5
REALTIME_PRICES = {}
ALERT_LAST_VALUES = {}
ALERT_MONITOR_STARTED = False
MOCK_PRICES = {
    "HC2609": 3380.0,
    "HC2610": 3400.0,
    "RB2609": 3260.0,
    "RB2610": 3290.0,
    "I2609": 805.0,
    "JM2609": 1230.0,
    "J2609": 1760.0,
    "FE2609": 103.5,
    "FE2701": 99.0,
    "USD/CNY": 7.18,
}


def inner_outer_contract_months(year: int, calc_date: str) -> list[dict[str, object]]:
    try:
        start_month = date.fromisoformat(calc_date).month
    except ValueError:
        start_month = date.today().month
    return [
        {
            "year": year + (start_month - 1 + offset) // 12,
            "month": str((start_month - 1 + offset) % 12 + 1).zfill(2),
        }
        for offset in range(INNER_OUTER_MONTH_COUNT)
    ]


SH_JUNNENG_DEFAULT_VARIETY = "RB"


def contract_options() -> List[str]:
    now = datetime.now()
    options = []
    for i in range(12):
        month = now.month + i
        year = now.year
        while month > 12:
            month -= 12
            year += 1
        options.append(f"{str(year)[-2:]}{str(month).zfill(2)}")
    return options


def two_digit_year(year: int) -> int:
    return int(year) % 100


def default_info_contracts(today_value: Optional[date] = None) -> dict:
    today_value = today_value or date.today()
    current_year = today_value.year
    current_month = today_value.month

    def nth_to_last_weekday(year: int, month: int, n: int) -> Optional[date]:
        last_day = calendar.monthrange(year, month)[1]
        dates = []
        for day in range(last_day, 0, -1):
            candidate = date(year, month, day)
            if candidate.weekday() < 5:
                dates.append(candidate)
            if len(dates) == n:
                return dates[-1]
        return None

    default_year = current_year
    default_month = "05"
    yuecha_year1 = current_year
    yuecha_month1 = "05"
    yuecha_year2 = current_year
    yuecha_month2 = "09"

    if current_month == 11:
        nov_last_7th = nth_to_last_weekday(current_year, 11, 7)
        if nov_last_7th and today_value >= nov_last_7th:
            default_year = current_year + 1
            default_month = "05"
            yuecha_year1 = current_year
            yuecha_month1 = "05"
            yuecha_year2 = current_year
            yuecha_month2 = "09"
    elif current_month == 12:
        default_year = current_year + 1
        default_month = "05"
        yuecha_year1 = current_year + 1
        yuecha_month1 = "05"
        yuecha_year2 = current_year + 1
        yuecha_month2 = "09"
    elif current_month in [1, 2]:
        default_year = current_year
        default_month = "05"
        yuecha_year1 = current_year
        yuecha_month1 = "05"
        yuecha_year2 = current_year
        yuecha_month2 = "09"
    elif current_month == 3:
        mar_last_7th = nth_to_last_weekday(current_year, 3, 7)
        if mar_last_7th and today_value >= mar_last_7th:
            default_month = "09"
            yuecha_year1 = current_year
            yuecha_month1 = "09"
            yuecha_year2 = current_year + 1
            yuecha_month2 = "01"
        else:
            default_month = "05"
            yuecha_year1 = current_year
            yuecha_month1 = "05"
            yuecha_year2 = current_year
            yuecha_month2 = "09"
    elif current_month in [4, 5, 6]:
        default_year = current_year
        default_month = "09"
        yuecha_year1 = current_year
        yuecha_month1 = "09"
        yuecha_year2 = current_year + 1
        yuecha_month2 = "01"
    elif current_month == 7:
        jul_last_7th = nth_to_last_weekday(current_year, 7, 7)
        if jul_last_7th and today_value >= jul_last_7th:
            default_month = "01"
            default_year = current_year + 1
            yuecha_year1 = current_year + 1
            yuecha_month1 = "01"
            yuecha_year2 = current_year + 1
            yuecha_month2 = "05"
        else:
            default_month = "09"
            yuecha_year1 = current_year
            yuecha_month1 = "09"
            yuecha_year2 = current_year + 1
            yuecha_month2 = "01"
    elif current_month in [8, 9, 10]:
        default_year = current_year
        default_month = "01"
        yuecha_year1 = current_year + 1
        yuecha_month1 = "01"
        yuecha_year2 = current_year + 1
        yuecha_month2 = "05"

    return {
        "default_year": default_year,
        "default_month": default_month,
        "yuecha_defaults": {
            "year1": yuecha_year1,
            "month1": yuecha_month1,
            "year2": yuecha_year2,
            "month2": yuecha_month2,
        },
    }


def sina_symbol(variety: str, contract: str) -> str:
    config = VARIETY_CONFIG.get(variety, {})
    if variety == "FE":
        return f"hf_FEF{contract}" if contract else "hf_FEF"
    if variety == "USD/CNY":
        return "fx_susdcnh"
    return f"{config.get('sina_prefix', 'nf_' + variety.upper())}{contract}"


def fetch_sina_price(variety: str, contract: str = "", mock: bool = False) -> Optional[float]:
    key = "USD/CNY" if variety == "USD/CNY" else f"{variety}{contract}"
    if mock:
        return MOCK_PRICES.get(key)

    symbol = sina_symbol(variety, contract)
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    try:
        response = requests.get(f"https://hq.sinajs.cn/list={symbol}", headers=headers, timeout=5)
        if response.status_code != 200 or "hq_str_" not in response.text:
            return None
        parts = response.text.split('"')
        if len(parts) < 2:
            return None
        data = parts[1].split(",")
        if variety == "USD/CNY" and len(data) > 2:
            return (float(data[1].strip()) + float(data[2].strip())) / 2
        if variety == "FE" and len(data) > 2:
            price = data[2].strip()
        elif len(data) > 8:
            price = data[8].strip()
        else:
            return None
        if not price or price == "-":
            return None
        return float(price)
    except Exception:
        return None


def adjusted_exchange_rate(base_rate: Optional[float], contract: str) -> Optional[float]:
    if base_rate is None or not contract:
        return base_rate
    try:
        now = datetime.now()
        contract_year = 2000 + int(contract[:2])
        contract_month = int(contract[2:])
        if contract_year == now.year:
            month_diff = contract_month - now.month
        else:
            month_diff = (12 - now.month) + contract_month
        return base_rate - month_diff * 0.015
    except Exception:
        return base_rate


def exchange_rate_key(contract: str) -> str:
    return f"USD/CNH{contract}" if contract else "USD/CNH"


def fetch_usdcnh_rate(contract: str = "", mock: bool = False) -> Optional[float]:
    if mock:
        return adjusted_exchange_rate(MOCK_PRICES.get("USD/CNY"), contract)
    if contract:
        rate = fetch_sgx_usdcnh_rate(contract)
        if rate is not None:
            return rate
    return adjusted_exchange_rate(fetch_sina_price("USD/CNY", "", mock), contract)


class RealtimeQuoteProvider:
    def __init__(self, mock: bool = False):
        self.mock = mock
        self._prices = {}
        self._fx = {}

    def get_price(self, variety: str, contract: str = "") -> Optional[float]:
        key = (variety, contract)
        if key not in self._prices:
            self._prices[key] = fetch_sina_price(variety, contract, self.mock)
        return self._prices[key]

    def get_fx(self, contract: str = "") -> Optional[float]:
        key = contract or ""
        if key in self._fx:
            return self._fx[key]
        if self.mock:
            rate = adjusted_exchange_rate(MOCK_PRICES.get("USD/CNY"), contract)
        else:
            rate = fetch_sgx_usdcnh_rate(contract, force_refresh=True) if contract else None
            if rate is None:
                rate = adjusted_exchange_rate(self.get_price("USD/CNY", ""), contract)
        self._fx[key] = rate
        return rate

    def prefetch(self, dependencies: list[tuple[str, str, str]]) -> None:
        unique_dependencies = list(dict.fromkeys(dependencies))
        if not unique_dependencies:
            return

        def load(item: tuple[str, str, str]) -> None:
            kind, variety, contract = item
            if kind == "fx":
                self.get_fx(contract)
            else:
                self.get_price(variety, contract)

        with ThreadPoolExecutor(max_workers=min(8, len(unique_dependencies))) as executor:
            list(executor.map(load, unique_dependencies))


def calculate_pnl(variety: str, direction: str, open_price: float, current_price: Optional[float], quantity: int, multiplier: int, contract: str) -> Optional[float]:
    if current_price is None:
        return None
    dir_factor = 1 if direction == "long" else -1
    if variety == "FE" and contract:
        rate = REALTIME_PRICES.get(exchange_rate_key(contract))
        if rate is None:
            rate = adjusted_exchange_rate(REALTIME_PRICES.get("USD/CNY"), contract)
        if rate is None:
            return None
        return (current_price - open_price) * rate * quantity * multiplier * dir_factor
    return (current_price - open_price) * quantity * multiplier * dir_factor


def normalize_contract(variety: str, contract: Optional[str]) -> str:
    value = (contract or "").strip().upper()
    if not value:
        return ""
    if variety != "USD/CNY" and value.startswith(variety.upper()):
        value = value[len(variety):]
    match = re.search(r"(\d{4})$", value)
    return match.group(1) if match else value


def normalize_sh_junneng_contract(contract_month: str) -> str:
    value = (contract_month or "").strip().upper()
    value = re.sub(r"\s+", "", value)
    variety_aliases = {
        "热卷": "HC",
        "热轧卷板": "HC",
        "螺纹": "RB",
        "螺纹钢": "RB",
    }
    for alias, variety in variety_aliases.items():
        if value.startswith(alias):
            value = f"{variety}{value[len(alias):]}"
            break
    return value


def sh_junneng_contract_code(contract_month: str) -> str:
    return normalize_sh_junneng_contract(contract_month)


def sh_junneng_direction_factor(direction: str) -> int:
    return 1 if direction in {"long", "多", "多头"} else -1


def normalize_sh_junneng_direction(direction: str) -> str:
    return "多头" if direction in {"long", "多", "多头"} else "空头"


def is_sh_junneng_closed(row_or_item) -> bool:
    close_date = row_or_item["close_date"]
    close_price = row_or_item["close_price"]
    close_fee = row_or_item["close_fee"]
    status = row_or_item["status"]
    is_closed = row_or_item["is_closed"]
    return (
        is_closed in {1, "1", True, "已平仓"}
        or status == "已结算"
        or (close_date and close_price and close_price > 0 and close_fee and close_fee > 0)
    )


def calculate_sh_junneng_profit(
    direction: str,
    open_price: float,
    quantity: float,
    open_fee: float,
    close_price: Optional[float] = None,
    close_fee: float = 0,
    current_price: Optional[float] = None,
) -> Optional[float]:
    price = close_price if close_price is not None else current_price
    if price is None:
        return None
    direction_factor = sh_junneng_direction_factor(direction)
    gross_profit = (price - open_price) * quantity * direction_factor
    return round(gross_profit - open_fee - close_fee, 2)


def sh_junneng_contract_multiplier(contract_month: str) -> float:
    variety, _ = split_sh_junneng_contract(contract_month)
    return VARIETY_CONFIG.get(variety, {}).get("multiplier", 10)


def calculate_sh_junneng_realized_profit(
    contract_month: str,
    direction: str,
    open_price: float,
    close_price: float,
    quantity: float,
    open_fee_allocated: float,
    close_fee: float,
) -> float:
    direction_factor = sh_junneng_direction_factor(direction)
    multiplier = sh_junneng_contract_multiplier(contract_month)
    gross_profit = (close_price - open_price) * quantity * multiplier * direction_factor
    return round(gross_profit - open_fee_allocated - close_fee, 2)


def sh_junneng_business_code(position_id: int) -> str:
    return f"SHJN-{position_id:06d}"


def sh_junneng_status(is_closed: bool) -> str:
    return "已平仓" if is_closed else "未平仓"


def sh_junneng_trade_snapshot(row) -> dict:
    item = row_to_dict(row)
    item["contract_month"] = normalize_sh_junneng_contract(item["contract_month"])
    item["contract_code"] = sh_junneng_contract_code(item["contract_month"])
    item["direction"] = normalize_sh_junneng_direction(item["direction"])
    item["direction_label"] = item["direction"]
    item["is_closed"] = 1 if is_sh_junneng_closed(item) else 0
    item["status"] = "已结算" if item["is_closed"] else "持仓"
    item["is_closed_label"] = sh_junneng_status(bool(item["is_closed"]))
    item["display_close_price"] = item["close_price"] if item["close_price"] and item["close_price"] > 0 else "未平仓"
    item["display_close_fee"] = item["close_fee"] if item["close_fee"] and item["close_fee"] > 0 else "未平仓"
    item["display_close_date"] = item["close_date"] if item["close_date"] else "未平仓"
    item["capital_used"] = (item["open_price"] or 0) * (item["trade_quantity"] or 0)
    item["profit"] = calculate_sh_junneng_profit(
        item["direction"],
        item["open_price"],
        item["trade_quantity"],
        item["open_fee"] or 0,
        item.get("close_price"),
        item.get("close_fee") or 0,
        item.get("current_price"),
    )
    item["profit_rate"] = (
        item["profit"] / item["capital_used"]
        if item["profit"] is not None and item["capital_used"]
        else None
    )
    return item


def calculate_sh_junneng_fund_profit(profit: Optional[float], open_date: str, close_date: Optional[str], quantity: float, open_price: float) -> tuple[float, float, float]:
    if profit is None or not close_date:
        return 0.0, 0.0, 0.0
    try:
        open_dt = datetime.strptime(open_date, "%Y-%m-%d")
        close_dt = datetime.strptime(close_date, "%Y-%m-%d")
        days = (close_dt - open_dt).days or 1
        interest = quantity * open_price * (0.07 * 0.07) * days / 360
        if profit <= 0:
            return round(interest, 2), 0.0, 0.0
        net_profit = profit - interest
        if net_profit <= 0:
            return round(interest, 2), 0.0, 0.0
        return round(interest, 2), round(net_profit * 0.8, 2), round(net_profit * 0.2, 2)
    except Exception:
        return 0.0, 0.0, 0.0


def with_sh_junneng_fund_fields(item: dict) -> dict:
    interest, profit_80, profit_20 = calculate_sh_junneng_fund_profit(
        item.get("profit"),
        item.get("open_date") or "",
        item.get("close_date"),
        item.get("trade_quantity") or 0,
        item.get("open_price") or 0,
    )
    return {**item, "interest": interest, "profit_80": profit_80, "profit_20": profit_20}


def sh_junneng_position_snapshot(row, closed_quantity: float = 0) -> dict:
    item = row_to_dict(row)
    item["contract_month"] = normalize_sh_junneng_contract(item["contract_month"])
    item["contract_code"] = sh_junneng_contract_code(item["contract_month"])
    item["direction"] = normalize_sh_junneng_direction(item["direction"])
    item["direction_label"] = item["direction"]
    item["open_quantity"] = item.get("open_quantity") or 0
    item["remaining_quantity"] = item.get("remaining_quantity") or 0
    item["closed_quantity"] = closed_quantity
    item["trade_quantity"] = item["open_quantity"]
    item["hold_quantity"] = item["remaining_quantity"]
    item["open_fee"] = item.get("open_fee") or 0
    item["close_fee"] = 0
    item["close_price"] = None
    item["close_date"] = None
    item["display_close_price"] = "未平仓"
    item["display_close_fee"] = "未平仓"
    item["display_close_date"] = "未平仓"
    item["is_closed"] = 1 if item["remaining_quantity"] <= 0 else 0
    item["status"] = "已结算" if item["is_closed"] else ("部分平仓" if closed_quantity else "持仓")
    item["position_status"] = "已全平" if item["is_closed"] else ("部分平仓" if closed_quantity else "未平仓")
    item["is_closed_label"] = sh_junneng_status(bool(item["is_closed"]))
    current_price = item.get("current_price")
    if current_price is None:
        item["profit"] = None
    else:
        remaining_open_fee = item["open_fee"] * item["remaining_quantity"] / item["open_quantity"] if item["open_quantity"] else 0
        item["profit"] = calculate_sh_junneng_profit(
            item["direction"],
            item["open_price"],
            item["remaining_quantity"],
            remaining_open_fee,
            current_price=current_price,
        )
    item["capital_used"] = (item["open_price"] or 0) * item["remaining_quantity"]
    item["profit_rate"] = (
        item["profit"] / item["capital_used"]
        if item["profit"] is not None and item["capital_used"]
        else None
    )
    item["row_type"] = "position"
    return item


def sh_junneng_close_snapshot(row) -> dict:
    item = row_to_dict(row)
    item["close_trade_id"] = item["id"]
    item["id"] = item["position_id"]
    item["contract_month"] = normalize_sh_junneng_contract(item["contract_month"])
    item["contract_code"] = sh_junneng_contract_code(item["contract_month"])
    item["direction"] = normalize_sh_junneng_direction(item["direction"])
    item["direction_label"] = item["direction"]
    item["open_quantity"] = item.get("open_quantity") or 0
    item["remaining_quantity"] = item.get("remaining_quantity") or 0
    item["closed_quantity"] = item["open_quantity"] - item["remaining_quantity"]
    item["trade_quantity"] = item.get("close_quantity") or 0
    item["hold_quantity"] = 0
    item["open_fee"] = item.get("open_fee_allocated") or 0
    item["profit"] = item.get("realized_profit")
    item["close_price"] = item.get("close_price")
    item["close_fee"] = item.get("close_fee") or 0
    item["display_close_price"] = item["close_price"]
    item["display_close_fee"] = item["close_fee"]
    item["display_close_date"] = item.get("close_date")
    item["is_closed"] = 1
    item["status"] = "已结算"
    item["position_status"] = "已全平" if item["remaining_quantity"] <= 0 else "部分平仓"
    item["is_closed_label"] = "已平仓"
    item["capital_used"] = (item["open_price"] or 0) * item["trade_quantity"]
    item["profit_rate"] = (
        item["profit"] / item["capital_used"]
        if item["profit"] is not None and item["capital_used"]
        else None
    )
    item["row_type"] = "close"
    return item


def summarize_sh_junneng_table(trades: list[dict]) -> dict:
    return {
        "trade_quantity": round(sum(item.get("trade_quantity") or 0 for item in trades), 2),
        "hold_quantity": round(sum(item.get("hold_quantity") or 0 for item in trades), 2),
        "open_fee": round(sum(item.get("open_fee") or 0 for item in trades), 2),
        "close_fee": round(sum(item.get("close_fee") or 0 for item in trades), 2),
        "profit": round(sum(item.get("profit") or 0 for item in trades), 2),
        "interest": round(sum(item.get("interest") or 0 for item in trades), 2),
        "profit_80": round(sum(item.get("profit_80") or 0 for item in trades), 2),
        "profit_20": round(sum(item.get("profit_20") or 0 for item in trades), 2),
    }


def summarize_sh_junneng(rows) -> dict:
    trades = [sh_junneng_trade_snapshot(row) for row in rows]
    total_profit = sum(item["profit"] for item in trades if item["profit"] is not None)
    return {
        "trades": trades,
        "summary": {
            "total_count": len(trades),
            "open_count": sum(1 for item in trades if not item["is_closed"]),
            "closed_count": sum(1 for item in trades if item["is_closed"]),
            "total_holding": sum(item["hold_quantity"] for item in trades),
            "total_profit": total_profit,
        },
    }


def sh_junneng_sections(rows, selected_date: str) -> dict:
    selected_month = selected_date[:7]
    today_trades = []
    current_trades = []
    settled_trades = []
    for row in rows:
        item = sh_junneng_trade_snapshot(row)
        close_date = item.get("close_date")
        is_future_close = False
        if close_date:
            try:
                is_future_close = datetime.strptime(close_date, "%Y-%m-%d") > datetime.strptime(selected_date, "%Y-%m-%d")
            except ValueError:
                is_future_close = False
        is_settled = bool(item["is_closed"]) and not is_future_close
        if item["open_date"] == selected_date or close_date == selected_date:
            today_trades.append(item)
        if not is_settled:
            current_trades.append({**item, "display_close_price": "未平仓", "display_close_fee": "未平仓", "display_close_date": "未平仓", "is_closed_label": "未平仓"})
        elif close_date and close_date[:7] == selected_month:
            settled_trades.append(with_sh_junneng_fund_fields(item))
    return {
        "today_trades": today_trades,
        "current_trades": current_trades,
        "settled_trades": settled_trades,
        "totals": {
            "today": summarize_sh_junneng_table(today_trades),
            "current": summarize_sh_junneng_table(current_trades),
            "settled": summarize_sh_junneng_table(settled_trades),
        },
    }


def split_sh_junneng_contract(contract_month: str) -> tuple[str, str]:
    value = normalize_sh_junneng_contract(contract_month)
    match = re.match(r"([A-Z]+)?(\d{4})$", value)
    if not match:
        return SH_JUNNENG_DEFAULT_VARIETY, value
    return match.group(1) or SH_JUNNENG_DEFAULT_VARIETY, match.group(2)


def fetch_sh_junneng_current_price(contract_month: str) -> Optional[float]:
    variety, contract = split_sh_junneng_contract(contract_month)
    return fetch_sina_price(variety, contract)


def refresh_sh_junneng_trade_prices(mock: bool = False) -> dict:
    today_value = date.today().isoformat()
    refreshed = 0
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT DISTINCT contract_month
            FROM sh_junneng_positions
            WHERE remaining_quantity > 0
            """
        ).fetchall()
        for row in rows:
            contract_month = normalize_sh_junneng_contract(row["contract_month"])
            variety, contract = split_sh_junneng_contract(contract_month)
            price = fetch_sina_price(variety, contract, mock)
            if price is None:
                continue
            db._exec(cur, 
                """
                UPDATE sh_junneng_positions
                SET current_price = ?, updated_at = CURRENT_TIMESTAMP
                WHERE contract_month = ? AND remaining_quantity > 0
                """,
                (price, contract_month),
            )
            db._exec(cur, 
                """
                INSERT INTO daily_prices (info_type, contract_code, calc_date, close_price)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(info_type, contract_code, calc_date) DO UPDATE SET
                    close_price = excluded.close_price,
                    created_at = CURRENT_TIMESTAMP
                """,
                ("sh_junneng", sh_junneng_contract_code(contract_month), today_value, price),
            )
            refreshed += 1
        open_rows = db._exec(cur, 
            """
            SELECT id, direction, open_price, remaining_quantity, open_quantity, open_fee, current_price
            FROM sh_junneng_positions
            WHERE remaining_quantity > 0
            """
        ).fetchall()
        for row in open_rows:
            remaining_open_fee = (row["open_fee"] or 0) * (row["remaining_quantity"] or 0) / row["open_quantity"] if row["open_quantity"] else 0
            profit = calculate_sh_junneng_profit(
                row["direction"],
                row["open_price"],
                row["remaining_quantity"],
                remaining_open_fee,
                current_price=row["current_price"],
            )
            db._exec(cur, 
                "UPDATE sh_junneng_positions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
    return {"refreshed_contracts": refreshed}


def price_key_for_position(variety: str, contract: Optional[str]) -> str:
    if variety == "USD/CNY":
        return exchange_rate_key(contract or "")
    return f"{variety}{normalize_contract(variety, contract)}"


def position_snapshot(row) -> dict:
    item = row_to_dict(row)
    contract = normalize_contract(item["variety"], item.get("contract"))
    config = VARIETY_CONFIG.get(item["variety"], {})
    multiplier = config.get("multiplier", item.get("multiplier") or 100)
    current_price = REALTIME_PRICES.get(price_key_for_position(item["variety"], contract))
    display_price = current_price
    pnl = calculate_pnl(
        item["variety"],
        item["direction"],
        item["open_price"],
        current_price,
        item["quantity"],
        multiplier,
        contract,
    )
    item["variety_name"] = config.get("name", item.get("variety_name") or item["variety"])
    item["multiplier"] = multiplier
    item["contract"] = contract
    item["current_price"] = display_price
    item["floating_pnl"] = pnl
    return item


def summarize_positions(rows) -> dict:
    positions = [position_snapshot(row) for row in rows]
    total_pnl = 0
    has_missing_price = False
    for item in positions:
        if item["floating_pnl"] is None:
            has_missing_price = True
        else:
            total_pnl += item["floating_pnl"]
    return {
        "positions": positions,
        "total_pnl": None if has_missing_price else total_pnl,
        "has_missing_price": has_missing_price,
    }


def group_pnl(group_id: int) -> Optional[float]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT id, group_id, variety, variety_name, direction, open_price,
                   quantity, multiplier, contract, created_at, updated_at
            FROM strategy_positions
            WHERE group_id = ?
            """,
            (group_id,),
        ).fetchall()
    return summarize_positions(rows)["total_pnl"]


def all_groups_total_pnl() -> Optional[float]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, "SELECT id FROM strategy_groups").fetchall()
    total = 0
    for row in rows:
        pnl = group_pnl(row["id"])
        if pnl is None:
            return None
        total += pnl
    return total


def realtime_dependencies_for_payload(payload: InfoCalculateIn) -> list[tuple[str, str, str]]:
    yy = two_digit_year(payload.year)
    month = payload.month.zfill(2)
    if payload.info_type == "卷螺差":
        return [("price", "HC", f"{yy}{month}"), ("price", "RB", f"{yy}{month}")]
    if payload.info_type == "螺矿比":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        return [("price", "RB", f"{yy}{rb_month}"), ("price", "I", f"{yy}{i_month}")]
    if payload.info_type == "煤矿比":
        return [("price", "JM", f"{yy}{month}"), ("price", "I", f"{yy}{month}")]
    if payload.info_type == "盘面钢厂利润":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        j_month = "09" if month == "09" else month
        return [("price", "RB", f"{yy}{rb_month}"), ("price", "I", f"{yy}{i_month}"), ("price", "J", f"{yy}{j_month}")]
    if payload.info_type in MONTH_DIFF_TYPES:
        variety = "FE" if payload.info_type == "掉期月差" else "I"
        year1 = two_digit_year(payload.year1 or payload.year)
        year2 = two_digit_year(payload.year2 or payload.year)
        month1 = (payload.month1 or "09").zfill(2)
        month2 = (payload.month2 or "01").zfill(2)
        return [("price", variety, f"{year1}{month1}"), ("price", variety, f"{year2}{month2}")]
    if payload.info_type in ["内外盘差", "内外盘差2"]:
        dependencies = []
        for contract_month in inner_outer_contract_months(payload.year, payload.calc_date):
            yy = two_digit_year(int(contract_month["year"]))
            inner_month = str(contract_month["month"])
            dependencies.extend([
                ("price", "I", f"{yy}{inner_month}"),
                ("price", "FE", f"{yy}{inner_month}"),
                ("fx", "USD/CNH", f"{yy}{inner_month}"),
            ])
        return dependencies
    return []


def calculate_today_indicator(payload: InfoCalculateIn, mock: bool = False, quote_provider: Optional[RealtimeQuoteProvider] = None) -> dict:
    yy = two_digit_year(payload.year)
    month = payload.month.zfill(2)
    value = None
    contracts = {}

    def price(variety: str, contract: str = "") -> Optional[float]:
        if quote_provider:
            return quote_provider.get_price(variety, contract)
        return fetch_sina_price(variety, contract, mock)

    def fx_rate(contract: str = "") -> Optional[float]:
        if quote_provider:
            return quote_provider.get_fx(contract)
        return fetch_usdcnh_rate(contract, mock)

    if payload.info_type == "卷螺差":
        hc = price("HC", f"{yy}{month}")
        rb = price("RB", f"{yy}{month}")
        contracts = {"HC": f"HC{yy}{month}", "RB": f"RB{yy}{month}"}
        value = hc - rb if hc is not None and rb is not None else None
    elif payload.info_type == "螺矿比":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        rb = price("RB", f"{yy}{rb_month}")
        i = price("I", f"{yy}{i_month}")
        contracts = {"RB": f"RB{yy}{rb_month}", "I": f"I{yy}{i_month}"}
        value = rb / i if rb is not None and i else None
    elif payload.info_type == "煤矿比":
        jm = price("JM", f"{yy}{month}")
        i = price("I", f"{yy}{month}")
        contracts = {"JM": f"JM{yy}{month}", "I": f"I{yy}{month}"}
        value = 1.88 * jm / i if jm is not None and i else None
    elif payload.info_type == "盘面钢厂利润":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        j_month = "09" if month == "09" else month
        rb = price("RB", f"{yy}{rb_month}")
        i = price("I", f"{yy}{i_month}")
        j = price("J", f"{yy}{j_month}")
        contracts = {"RB": f"RB{yy}{rb_month}", "I": f"I{yy}{i_month}", "J": f"J{yy}{j_month}"}
        value = (rb - 1.6 * i - 0.45 * j - 375) / 1.13 - 1035 if rb is not None and i is not None and j is not None else None
    elif payload.info_type in MONTH_DIFF_TYPES:
        variety = "FE" if payload.info_type == "掉期月差" else "I"
        year1 = two_digit_year(payload.year1 or payload.year)
        year2 = two_digit_year(payload.year2 or payload.year)
        month1 = (payload.month1 or "09").zfill(2)
        month2 = (payload.month2 or "01").zfill(2)
        p1 = price(variety, f"{year1}{month1}")
        p2 = price(variety, f"{year2}{month2}")
        contracts = {f"{variety}1": f"{variety}{year1}{month1}", f"{variety}2": f"{variety}{year2}{month2}"}
        value = p1 - p2 if p1 is not None and p2 is not None else None
    elif payload.info_type == "内外盘差":
        i = price("I", f"{yy}{month}")
        fe = price("FE", f"{yy}{month}")
        fx = fx_rate(f"{yy}{month}")
        contracts = {"I": f"I{yy}{month}", "FE": f"FE{yy}{month}", "USD/CNH": f"USD/CNH{yy}{month}"}
        value = (fx * 1.13 * fe - i) + 30 if i is not None and fe is not None and fx is not None else None
    elif payload.info_type == "内外盘差2":
        i = price("I", f"{yy}{month}")
        fe = price("FE", f"{yy}{month}")
        fx = fx_rate(f"{yy}{month}")
        contracts = {"I": f"I{yy}{month}", "FE": f"FE{yy}{month}", "USD/CNH": f"USD/CNH{yy}{month}"}
        value = round((i / fx * 0.88 - fe), 2) if i is not None and fe is not None and fx else None

    return {
        "info_type": payload.info_type,
        "today_value": value,
        "contracts": contracts,
        "calc_date": payload.calc_date,
    }


def calculate_inner_outer_months(payload: InfoCalculateIn, mock: bool = False, quote_provider: Optional[RealtimeQuoteProvider] = None) -> dict:
    month_values = {}
    contracts = {}
    for contract_month in inner_outer_contract_months(payload.year, payload.calc_date):
        contract_year = int(contract_month["year"])
        month = str(contract_month["month"])
        key = f"{contract_year}-{month}"
        monthly_payload = payload.model_copy(update={"year": contract_year, "month": month})
        result = calculate_today_indicator(monthly_payload, mock=mock, quote_provider=quote_provider)
        month_values[key] = result["today_value"]
        contracts[key] = result["contracts"]
    return {"month_values": month_values, "contracts": contracts}


def calculate_alert_current_value(row, mock: bool = False) -> Optional[float]:
    contract_month = row["contract_month"]
    payload = InfoCalculateIn(
        info_type=row["info_type"],
        year=int(row["contract_year"]),
        month=contract_month,
        calc_date=date.today().isoformat(),
    )
    if row["info_type"] == "月差":
        parts = re.split(r"[_-]", contract_month)
        if len(parts) != 2:
            return None
        payload = payload.copy(
            update={
                "month1": parts[0].zfill(2),
                "month2": parts[1].zfill(2),
                "year1": int(row["contract_year"]),
                "year2": int(row["contract_year"]),
            }
        )
    return calculate_today_indicator(payload, mock=mock).get("today_value")


def trigger_alert(row, current_value: float, direction_text: str) -> None:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, 
            """
            INSERT INTO alert_history (alert_id, current_value, alert_value, direction, status)
            VALUES (?, ?, ?, ?, 'unread')
            """,
            (row["id"], current_value, row["alert_value"], direction_text),
        )


def scan_risk_alerts_once(
    mock: bool = False,
    creator_user_id: Optional[int] = None,
) -> dict:
    with db.connect() as conn:
        cur = conn.cursor()
        sql = """
            SELECT *
            FROM alert_settings
            WHERE status = 'enabled' AND archived_at IS NULL
        """
        params = []
        if creator_user_id is not None:
            sql += " AND creator_user_id = ?"
            params.append(creator_user_id)
        sql += " ORDER BY id"
        rows = db._exec(cur, sql, tuple(params)).fetchall()

    triggered = 0
    checked = 0
    for row in rows:
        current_value = calculate_alert_current_value(row, mock=mock)
        if current_value is None:
            continue
        checked += 1
        key = str(row["id"])
        last_value = ALERT_LAST_VALUES.get(key)
        alert_value = row["alert_value"]
        if last_value is not None:
            if row["direction"] == "above" and last_value < alert_value <= current_value:
                trigger_alert(row, current_value, "向上突破")
                triggered += 1
            elif row["direction"] == "below" and last_value > alert_value >= current_value:
                trigger_alert(row, current_value, "向下突破")
                triggered += 1
        ALERT_LAST_VALUES[key] = current_value
    return {"checked": checked, "triggered": triggered}


def alert_monitor_loop() -> None:
    while True:
        try:
            scan_risk_alerts_once()
        except Exception:
            pass
        time.sleep(5)


def start_alert_monitor() -> None:
    global ALERT_MONITOR_STARTED
    if ALERT_MONITOR_STARTED:
        return
    ALERT_MONITOR_STARTED = True
    thread = threading.Thread(target=alert_monitor_loop, daemon=True)
    thread.start()


def cache_month_key(payload: InfoCalculateIn) -> Optional[str]:
    if payload.info_type in MONTH_DIFF_TYPES:
        return f"{(payload.month1 or '09').zfill(2)}_{(payload.month2 or '01').zfill(2)}"
    if payload.info_type in ["内外盘差", "内外盘差2"]:
        return payload.month.zfill(2)
    return payload.month.zfill(2)


def response_from_cache(
    payload: InfoCalculateIn,
    cached: Optional[dict],
    realtime: dict,
    history_calc_date: Optional[str] = None,
) -> dict:
    history_calc_date = history_calc_date or (cached.get("calc_date") if cached else None)
    return {
        "info_type": payload.info_type,
        "calc_date": payload.calc_date,
        "cache_hit": cached is not None and cached.get("t_1_value") is not None,
        "history_calc_date": history_calc_date,
        "history_stale": bool(history_calc_date and history_calc_date != payload.calc_date),
        "today_value": realtime.get("today_value"),
        "contracts": realtime.get("contracts", {}),
        "t_1_value": cached.get("t_1_value") if cached else None,
        "t_2_value": cached.get("t_2_value") if cached else None,
        "mean_value": cached.get("mean_value") if cached else None,
        "min_value": cached.get("min_value") if cached else None,
        "max_value": cached.get("max_value") if cached else None,
        "std_value": cached.get("std_value") if cached else None,
    }


def indicator_contracts_for_cache(payload: InfoCalculateIn) -> list[str]:
    yy = two_digit_year(payload.year)
    month = payload.month.zfill(2)
    if payload.info_type == "卷螺差":
        return [f"HC{yy}{month}", f"RB{yy}{month}"]
    if payload.info_type == "螺矿比":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        return [f"RB{yy}{rb_month}", f"I{yy}{i_month}"]
    if payload.info_type == "煤矿比":
        return [f"JM{yy}{month}", f"I{yy}{month}"]
    if payload.info_type == "盘面钢厂利润":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        j_month = "09" if month == "09" else month
        return [f"RB{yy}{rb_month}", f"I{yy}{i_month}", f"J{yy}{j_month}"]
    if payload.info_type in MONTH_DIFF_TYPES:
        variety = "FE" if payload.info_type == "掉期月差" else "I"
        y1 = two_digit_year(payload.year1 or payload.year)
        y2 = two_digit_year(payload.year2 or payload.year)
        return [f"{variety}{y1}{(payload.month1 or '09').zfill(2)}", f"{variety}{y2}{(payload.month2 or '01').zfill(2)}"]
    return []


def value_from_cached_prices(info_type: str, prices: list[float]) -> Optional[float]:
    if info_type == "卷螺差":
        return prices[0] - prices[1]
    if info_type == "螺矿比":
        return prices[0] / prices[1] if prices[1] else None
    if info_type == "煤矿比":
        return 1.88 * prices[0] / prices[1] if prices[1] else None
    if info_type == "盘面钢厂利润":
        return (prices[0] - 1.6 * prices[1] - 0.45 * prices[2] - 375) / 1.13 - 1035
    if info_type in MONTH_DIFF_TYPES:
        return prices[0] - prices[1]
    return None


def calculate_missing_cache_from_prices(payload: InfoCalculateIn) -> Optional[dict]:
    contract_codes = indicator_contracts_for_cache(payload)
    if not contract_codes:
        return None

    price_data = get_all_prices_for_info_type(payload.info_type)
    if not price_data or any(code not in price_data for code in contract_codes):
        return None

    dates = sorted(set.intersection(*(set(price_data[code].keys()) for code in contract_codes)))
    dates = [day for day in dates if day < payload.calc_date]
    if not dates:
        return None
    latest_price_date = date.fromisoformat(dates[-1])
    if latest_price_date < date.fromisoformat(payload.calc_date) - timedelta(days=HISTORY_CACHE_MAX_STALE_DAYS):
        return None

    def value_for_date(day: str) -> Optional[float]:
        prices = [price_data[code].get(day) for code in contract_codes]
        if any(price is None for price in prices):
            return None
        return value_from_cached_prices(payload.info_type, prices)

    t_1_value = value_for_date(dates[-1])
    t_2_value = value_for_date(dates[-2]) if len(dates) >= 2 else None
    window_dates = dates[-180:]
    values = [value_for_date(day) for day in window_dates]
    values = [value for value in values if value is not None]
    if not values:
        return None

    calculated = {
        "t_1_value": t_1_value,
        "t_2_value": t_2_value,
        "mean_value": statistics.mean(values),
        "min_value": min(values),
        "max_value": max(values),
        "std_value": statistics.stdev(values) if len(values) >= 10 else None,
    }
    save_calculated_data(
        payload.info_type,
        payload.year,
        cache_month_key(payload),
        payload.calc_date,
        **calculated,
    )
    return calculated


@app.on_event("startup")
def startup() -> None:
    def initialize_database() -> None:
        try:
            db.init_db()
            data_visualization.seed_dv_data()
            start_iron_ore_basis_sync_scheduler()
            start_order_finance_sync_scheduler()
        except Exception as exc:
            print(f"[startup] database initialization skipped: {exc}")

    threading.Thread(target=initialize_database, daemon=True).start()
    start_alert_monitor()
    start_daily_close_cache_scheduler()
    start_monitoring_loop()


@app.on_event("shutdown")
def shutdown() -> None:
    trading_valuation.close_market_data_service()


def current_user(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_edit(module_code: str, user):
    if user.get("role") == "管理员":
        return
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, 
            """
            SELECT can_edit FROM module_permissions
            WHERE user_id = ? AND module_code = ?
            """,
            (user["id"], module_code),
        ).fetchone()
    if not row or not row["can_edit"]:
        raise HTTPException(status_code=403, detail="没有编辑权限")


def require_view(module_code: str, user):
    if user.get("role") == "管理员":
        return
    if "role" not in user and user.get("id"):
        with db.connect() as conn:
            cur = conn.cursor()
            full_user = db._exec(cur, "SELECT role FROM users WHERE id = ?", (user["id"],)).fetchone()
        if full_user and full_user["role"] == "管理员":
            return
        user = {**user, "role": full_user["role"] if full_user else ""}
    if user.get("role") == "管理员":
        return
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """
            SELECT can_view FROM module_permissions
            WHERE user_id = ? AND module_code = ?
            """,
            (user["id"], module_code),
        ).fetchone()
    if not row or not row["can_view"]:
        raise HTTPException(status_code=403, detail="没有访问权限")


def row_to_dict(row):
    return dict(row) if row else None


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.head("/")
def index_head():
    return Response(status_code=200)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/monitoring/status")
def monitoring_status(user=Depends(current_user)):
    require_permission(user, "monitoring.status", "view")
    return get_monitoring_status()


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    key = payload.username.strip().lower()
    now = time.time()
    recent_failures = [
        ts for ts in LOGIN_FAILURES.get(key, [])
        if now - ts < LOGIN_FAILURE_WINDOW_SECONDS
    ]
    if len(recent_failures) >= LOGIN_FAILURE_LIMIT:
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后再试")
    with db.connect() as conn:
        cur = conn.cursor()
        user = db._exec(cur,
            """
            SELECT * FROM users
            WHERE username = ? AND status = '启用'
            """,
            (payload.username,),
        ).fetchone()
    if not user or not db.verify_password(payload.password, user["password_hash"]):
        recent_failures.append(now)
        LOGIN_FAILURES[key] = recent_failures[-LOGIN_FAILURE_LIMIT:]
        db.log_operation(None, "auth", "登录失败", f"{payload.username} 登录失败")
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    LOGIN_FAILURES.pop(key, None)
    if db.needs_password_upgrade(user["password_hash"]):
        db.upgrade_user_password(user["id"], payload.password)
    token = db.create_session(user["id"], ttl_hours=USER_SESSION_TTL_HOURS)
    db.log_operation(user["id"], "auth", "登录", f"{payload.username} 登录系统")
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "username": user["username"],
            "department": user["department"],
            "role": user["role"],
            "password_change_recommended": bool(user["password_change_recommended"]),
        },
    }


@app.post("/api/auth/guest-login")
def guest_login():
    user = db.ensure_guest_user()
    token = db.create_session(user["id"], ttl_hours=GUEST_SESSION_TTL_HOURS)
    db.log_operation(user["id"], "auth", "访客登录", "guest 以访客身份访问")
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "name": "访客",
            "department": user["department"],
            "role": "guest",
            "is_guest": True,
        },
        "permissions": list_user_permissions(user),
        "modules": modules(user),
    }


@app.post("/api/auth/logout")
def logout(user=Depends(current_user), authorization: Optional[str] = Header(default=None)):
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "UPDATE user_sessions SET status = '已注销' WHERE token = ?", (token,))
    db.log_operation(user["id"], "auth", "退出", f"{user['name']} 退出系统")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    display_name = "访客" if user.get("is_guest") or user.get("role") == "guest" else user["name"]
    display_role = "访客" if user.get("is_guest") or user.get("role") == "guest" else user["role"]
    return {
        "id": user["id"],
        "name": display_name,
        "username": user.get("username", user["name"]),
        "department": user["department"],
        "role": display_role,
        "is_guest": bool(user.get("is_guest")),
        "password_change_recommended": bool(user.get("password_change_recommended")),
        "permissions": list_user_permissions(user),
    }


@app.get("/api/auth/modules")
def modules(user=Depends(current_user)):
    module_rows = [row for row in db.MODULES if row[1] not in RETIRED_MODULE_CODES]
    if user["role"] == "管理员":
        visible = {
            code: {"can_view": True, "can_edit": True, "can_sensitive": True}
            for _, code, _ in module_rows
        }
    else:
        with db.connect() as conn:
            cur = conn.cursor()
            rows = db._exec(cur, 
                """
                SELECT module_code, can_view, can_edit, can_sensitive
                FROM module_permissions
                WHERE user_id = ? AND can_view = 1
                """,
                (user["id"],),
            ).fetchall()
        visible = {
            row["module_code"]: {
                "can_view": bool(row["can_view"]),
                "can_edit": bool(row["can_edit"]),
                "can_sensitive": bool(row["can_sensitive"]),
            }
            for row in rows
        }

    groups = {}
    for group, code, name in module_rows:
        if code in visible:
            groups.setdefault(group, []).append(
                {"code": code, "name": name, **visible[code]}
            )
    return [{"group": group, "items": items} for group, items in groups.items()]


def is_risk_alert_admin(user: dict) -> bool:
    return user.get("role") in {"管理员", "admin"}


def load_risk_alert_for_action(
    cur,
    alert_id: int,
    user: dict,
    include_archived: bool = False,
):
    sql = "SELECT * FROM alert_settings WHERE id = ?"
    params = [alert_id]
    if not include_archived:
        sql += " AND archived_at IS NULL"
    if not is_risk_alert_admin(user):
        sql += " AND creator_user_id = ?"
        params.append(user["id"])
    row = db._exec(cur, sql, tuple(params)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="预警规则不存在")
    return row


def log_risk_alert_admin_action(
    user: dict,
    setting,
    operation_type: str,
    description: str,
    deleted_count: Optional[int] = None,
) -> None:
    if (
        not is_risk_alert_admin(user)
        or setting["creator_user_id"] == user["id"]
    ):
        return
    suffix = f"，删除历史 {deleted_count} 条" if deleted_count is not None else ""
    db.log_operation(
        user["id"],
        "risk_alert",
        operation_type,
        f"{description}；原设置人用户 ID {setting['creator_user_id']}{suffix}",
        "alert_settings",
        setting["id"],
    )


@app.get("/api/risk-alert/settings")
def list_alert_settings(
    limit: int = 200,
    offset: int = 0,
    creator_user_id: Optional[int] = None,
    user=Depends(current_user),
):
    require_view("risk_alert", user)
    limit = max(1, min(limit or 200, 200))
    offset = max(0, offset or 0)
    if not is_risk_alert_admin(user):
        creator_user_id = user["id"]
    with db.connect() as conn:
        cur = conn.cursor()
        where = "WHERE archived_at IS NULL"
        params = []
        if creator_user_id is not None:
            where += " AND creator_user_id = ?"
            params.append(creator_user_id)
        total_row = db._exec(
            cur,
            f"SELECT COUNT(*) AS c FROM alert_settings {where}",
            tuple(params),
        ).fetchone()
        rows = db._exec(
            cur,
            f"""
            SELECT id, info_type, contract_year, contract_month, alert_value,
                   direction, status, creator_user_id, creator, created_at, updated_at
            FROM alert_settings
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    total = int(total_row["c"] or 0)
    return {
        "items": [row_to_dict(row) for row in rows],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        },
    }


@app.post("/api/risk-alert/settings")
def create_alert_setting(payload: AlertSettingIn, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            """
            INSERT INTO alert_settings
                (info_type, contract_year, contract_month, alert_value, direction, status, creator_user_id, creator)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.info_type,
                payload.contract_year,
                payload.contract_month,
                payload.alert_value,
                payload.direction,
                payload.status,
                user["id"],
                user["name"],
            ),
        )
        alert_id = db.last_insert_id(conn)
    db.log_operation(user["id"], "risk_alert", "新增预警", "新增风险预警规则", "alert_settings", alert_id)
    return {"id": alert_id}


@app.put("/api/risk-alert/settings/{alert_id}")
def update_alert_setting(alert_id: int, payload: AlertSettingIn, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        setting = load_risk_alert_for_action(cur, alert_id, user)
        db._exec(cur,
            """
            UPDATE alert_settings
            SET info_type = ?, contract_year = ?, contract_month = ?,
                alert_value = ?, direction = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload.info_type,
                payload.contract_year,
                payload.contract_month,
                payload.alert_value,
                payload.direction,
                payload.status,
                alert_id,
            ),
        )
    db.log_operation(user["id"], "risk_alert", "编辑预警", "编辑风险预警规则", "alert_settings", alert_id)
    log_risk_alert_admin_action(
        user,
        setting,
        "管理员编辑他人预警",
        "管理员编辑他人风险预警规则",
    )
    return {"ok": True}


@app.post("/api/risk-alert/settings/{alert_id}/toggle")
def toggle_alert_setting(alert_id: int, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        setting = load_risk_alert_for_action(cur, alert_id, user)
        next_status = "disabled" if setting["status"] == "enabled" else "enabled"
        db._exec(cur, 
            "UPDATE alert_settings SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (next_status, alert_id),
        )
    db.log_operation(user["id"], "risk_alert", "切换预警状态", f"预警状态改为 {next_status}", "alert_settings", alert_id)
    log_risk_alert_admin_action(
        user,
        setting,
        "管理员切换他人预警状态",
        f"管理员将他人预警状态改为 {next_status}",
    )
    return {"status": next_status}


@app.delete("/api/risk-alert/settings/{alert_id}")
def delete_alert_setting(alert_id: int, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        setting = load_risk_alert_for_action(cur, alert_id, user)
        count = db._exec(
            cur,
            "SELECT COUNT(*) AS c FROM alert_history WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()["c"]
        if count:
            db._exec(
                cur,
                """
                UPDATE alert_settings
                SET status = 'disabled',
                    archived_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (alert_id,),
            )
            archived = True
        else:
            db._exec(
                cur,
                "DELETE FROM alert_settings WHERE id = ?",
                (alert_id,),
            )
            archived = False
    db.log_operation(user["id"], "risk_alert", "删除预警", "删除风险预警规则", "alert_settings", alert_id)
    log_risk_alert_admin_action(
        user,
        setting,
        "管理员删除他人预警",
        "管理员删除他人风险预警规则",
    )
    return {"ok": True, "archived": archived}


@app.delete("/api/risk-alert/history/rules/{alert_id}")
def delete_alert_history_group(alert_id: int, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        setting = load_risk_alert_for_action(
            cur,
            alert_id,
            user,
            include_archived=True,
        )
        count = db._exec(
            cur,
            "SELECT COUNT(*) AS c FROM alert_history WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()["c"]
        if not count:
            raise HTTPException(status_code=404, detail="没有可删除的预警历史")
        db._exec(
            cur,
            "DELETE FROM alert_history WHERE alert_id = ?",
            (alert_id,),
        )
        rule_deleted = bool(setting["archived_at"])
        if rule_deleted:
            db._exec(
                cur,
                "DELETE FROM alert_settings WHERE id = ?",
                (alert_id,),
            )
    db.log_operation(
        user["id"],
        "risk_alert",
        "删除预警历史",
        f"删除规则 {alert_id} 的 {count} 条预警历史",
        "alert_settings",
        alert_id,
    )
    log_risk_alert_admin_action(
        user,
        setting,
        "管理员删除他人预警历史",
        "管理员删除他人预警历史",
        deleted_count=int(count),
    )
    return {
        "ok": True,
        "deleted": int(count),
        "rule_deleted": rule_deleted,
    }


@app.get("/api/risk-alert/history/summary")
def list_alert_history_summary(
    limit: int = 10,
    offset: int = 0,
    creator_user_id: Optional[int] = None,
    user=Depends(current_user),
):
    require_view("risk_alert", user)
    limit = max(1, min(limit or 10, 50))
    offset = max(0, offset or 0)
    if not is_risk_alert_admin(user):
        creator_user_id = user["id"]

    where = ""
    params = []
    if creator_user_id is not None:
        where = "WHERE s.creator_user_id = ?"
        params.append(creator_user_id)

    with db.connect() as conn:
        cur = conn.cursor()
        total_row = db._exec(
            cur,
            f"""
            SELECT COUNT(*) AS c
            FROM alert_settings s
            JOIN (
                SELECT DISTINCT alert_id
                FROM alert_history
            ) history_rules ON history_rules.alert_id = s.id
            {where}
            """,
            tuple(params),
        ).fetchone()
        rows = db._exec(
            cur,
            f"""
            WITH ranked_history AS (
                SELECT h.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY h.alert_id
                           ORDER BY h.alert_time DESC, h.id DESC
                       ) AS row_num,
                       COUNT(*) OVER (
                           PARTITION BY h.alert_id
                       ) AS alert_count,
                       SUM(
                           CASE WHEN h.status = 'unread' THEN 1 ELSE 0 END
                       ) OVER (
                           PARTITION BY h.alert_id
                       ) AS unread_count
                FROM alert_history h
            )
            SELECT s.id AS alert_id, s.info_type, s.contract_year,
                   s.contract_month, s.creator_user_id, s.creator,
                   s.archived_at, s.status AS rule_status,
                   h.current_value AS latest_current_value,
                   h.alert_value AS latest_alert_value,
                   h.direction AS latest_direction,
                   h.alert_time AS latest_alert_time,
                   h.alert_count, h.unread_count
            FROM alert_settings s
            JOIN ranked_history h
              ON h.alert_id = s.id AND h.row_num = 1
            {where}
            ORDER BY h.alert_time DESC, h.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
        if is_risk_alert_admin(user):
            owner_rows = db._exec(
                cur,
                """
                SELECT DISTINCT s.creator_user_id AS id, s.creator AS name
                FROM alert_settings s
                JOIN alert_history h ON h.alert_id = s.id
                WHERE s.creator_user_id IS NOT NULL
                ORDER BY s.creator, s.creator_user_id
                """,
            ).fetchall()
        else:
            owner_rows = []

    total = int(total_row["c"] or 0)
    return {
        "items": [row_to_dict(row) for row in rows],
        "owners": [row_to_dict(row) for row in owner_rows],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        },
    }


@app.get("/api/risk-alert/history/rules/{alert_id}")
def list_alert_history_details(
    alert_id: int,
    limit: int = 20,
    offset: int = 0,
    user=Depends(current_user),
):
    require_view("risk_alert", user)
    limit = max(1, min(limit or 20, 100))
    offset = max(0, offset or 0)
    with db.connect() as conn:
        cur = conn.cursor()
        load_risk_alert_for_action(
            cur,
            alert_id,
            user,
            include_archived=True,
        )
        total_row = db._exec(
            cur,
            "SELECT COUNT(*) AS c FROM alert_history WHERE alert_id = ?",
            (alert_id,),
        ).fetchone()
        rows = db._exec(
            cur,
            """
            SELECT id, alert_id, alert_time, current_value, alert_value,
                   direction, status
            FROM alert_history
            WHERE alert_id = ?
            ORDER BY alert_time DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (alert_id, limit, offset),
        ).fetchall()
    total = int(total_row["c"] or 0)
    return {
        "items": [row_to_dict(row) for row in rows],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        },
    }


@app.get("/api/risk-alert/history")
def list_alert_history(
    limit: int = 200,
    offset: int = 0,
    user=Depends(current_user),
):
    require_view("risk_alert", user)
    limit = max(1, min(limit or 200, 200))
    offset = max(0, offset or 0)
    with db.connect() as conn:
        cur = conn.cursor()
        owner_where = ""
        params = []
        if not is_risk_alert_admin(user):
            owner_where = "WHERE s.creator_user_id = ?"
            params.append(user["id"])
        total_row = db._exec(
            cur,
            f"""
            SELECT COUNT(*) AS c
            FROM alert_history h
            JOIN alert_settings s ON s.id = h.alert_id
            {owner_where}
            """,
            tuple(params),
        ).fetchone()
        rows = db._exec(
            cur,
            f"""
            SELECT h.id, h.alert_id, h.alert_time, h.current_value, h.alert_value,
                   h.direction, h.status, s.info_type, s.contract_year,
                   s.contract_month, s.creator_user_id
            FROM alert_history h
            JOIN alert_settings s ON s.id = h.alert_id
            {owner_where}
            ORDER BY h.alert_time DESC, h.id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    total = int(total_row["c"] or 0)
    return {
        "items": [row_to_dict(row) for row in rows],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        },
    }


@app.get("/api/risk-alert/notifications")
def list_alert_notifications(user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT h.*, s.info_type, s.contract_year, s.contract_month,
                   s.creator, s.creator_user_id
            FROM alert_history h
            JOIN alert_settings s ON s.id = h.alert_id
            WHERE h.status = 'unread' AND s.creator_user_id = ?
            ORDER BY h.alert_time DESC, h.id DESC
            LIMIT 20
            """,
            (user["id"],),
        ).fetchall()
    result = [row_to_dict(row) for row in rows]
    return {"count": len(result), "items": result}


@app.post("/api/risk-alert/history/{history_id}/read")
def mark_alert_history_read(history_id: int, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """
            SELECT s.*
            FROM alert_history h
            JOIN alert_settings s ON s.id = h.alert_id
            WHERE h.id = ?
            """,
            (history_id,),
        ).fetchone()
        if not row or (
            not is_risk_alert_admin(user)
            and row["creator_user_id"] != user["id"]
        ):
            raise HTTPException(status_code=404, detail="预警历史不存在")
        db._exec(
            cur,
            "UPDATE alert_history SET status = 'read' WHERE id = ?",
            (history_id,),
        )
    log_risk_alert_admin_action(
        user,
        row,
        "管理员确认他人预警",
        "管理员将他人预警标记为已读",
    )
    return {"ok": True}


@app.post("/api/risk-alert/history/read-all")
def mark_all_alert_history_read(user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            UPDATE alert_history
            SET status = 'read'
            WHERE status = 'unread'
              AND alert_id IN (
                  SELECT id FROM alert_settings WHERE creator_user_id = ?
              )
            """,
            (user["id"],),
        )
    return {"ok": True}


@app.post("/api/risk-alert/scan")
def scan_risk_alerts(user=Depends(current_user)):
    require_view("risk_alert", user)
    creator_user_id = None if is_risk_alert_admin(user) else user["id"]
    result = scan_risk_alerts_once(creator_user_id=creator_user_id)
    db.log_operation(user["id"], "risk_alert", "扫描预警", f"检查 {result['checked']} 条，触发 {result['triggered']} 条")
    return result


@app.post("/api/risk-alert/settings/{alert_id}/simulate-trigger")
def simulate_alert_trigger(alert_id: int, current_value: float, user=Depends(current_user)):
    require_view("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        setting = load_risk_alert_for_action(cur, alert_id, user)
        db._exec(cur, 
            """
            INSERT INTO alert_history (alert_id, current_value, alert_value, direction)
            VALUES (?, ?, ?, ?)
            """,
            (alert_id, current_value, setting["alert_value"], setting["direction"]),
        )
    db.log_operation(user["id"], "risk_alert", "模拟触发", "手动写入预警历史", "alert_settings", alert_id)
    log_risk_alert_admin_action(
        user,
        setting,
        "管理员模拟触发他人预警",
        "管理员模拟触发他人风险预警规则",
    )
    return {"ok": True}


@app.get("/api/info-summary/indicators")
def list_indicators(user=Depends(current_user)):
    require_view("info_summary", user)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT id, info_type, year, month, calc_date, t_1_value, t_2_value,
                   mean_value, min_value, max_value, std_value, created_at
            FROM calculated_data
            ORDER BY calc_date DESC, id DESC
            LIMIT 100
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.get("/api/info-summary/config")
def info_summary_config(user=Depends(current_user)):
    require_view("info_summary", user)
    defaults = default_info_contracts()
    inner_contract_months = inner_outer_contract_months(defaults["default_year"], date.today().isoformat())
    return {
        "info_types": INFO_TYPES,
        "default_year": defaults["default_year"],
        "default_month": defaults["default_month"],
        "yuecha_defaults": defaults["yuecha_defaults"],
        "contract_months": [str(i).zfill(2) for i in range(1, 13)],
        "special_months": ["01", "05", "09"],
        "month_options_by_type": SPECIAL_MONTH_OPTIONS,
        "inner_months": [item["month"] for item in inner_contract_months],
        "cache_counts": cache_counts(),
    }


def calculate_info_summary_payload(
    payload: InfoCalculateIn,
    mock: bool = False,
    quote_provider: Optional[RealtimeQuoteProvider] = None,
    fill_missing_history: bool = True,
) -> dict:
    if payload.info_type in ["内外盘差", "内外盘差2"]:
        realtime = calculate_inner_outer_months(payload, mock=mock, quote_provider=quote_provider)
        month_results = {}
        for contract_month in inner_outer_contract_months(payload.year, payload.calc_date):
            contract_year = int(contract_month["year"])
            month = str(contract_month["month"])
            key = f"{contract_year}-{month}"
            cached = get_cached_data(payload.info_type, contract_year, month, payload.calc_date)
            history_calc_date = cached.get("calc_date") if cached else None
            if not fill_missing_history and (not cached or cached.get("t_1_value") is None):
                latest_cached = get_latest_cached_data(payload.info_type, contract_year, month, payload.calc_date)
                if latest_cached and latest_cached.get("calc_date") == payload.calc_date:
                    cached = latest_cached
                    history_calc_date = latest_cached.get("calc_date")
                elif latest_cached and not cached:
                    history_calc_date = latest_cached.get("calc_date")
            history_stale = bool(history_calc_date and history_calc_date != payload.calc_date)
            month_results[key] = {
                **(cached or {}),
                "year": contract_year,
                "month": month,
                "cache_hit": cached is not None and cached.get("t_1_value") is not None,
                "history_calc_date": history_calc_date,
                "history_stale": history_stale,
                "today_value": realtime["month_values"].get(key),
                "contracts": realtime["contracts"].get(key, {}),
            }
        return {
            "info_type": payload.info_type,
            "calc_date": payload.calc_date,
            "month_values": realtime["month_values"],
            "month_results": month_results,
            "contracts": realtime["contracts"],
            "cache_hit": any(item["cache_hit"] for item in month_results.values()),
        }

    realtime = calculate_today_indicator(payload, mock=mock, quote_provider=quote_provider)
    month_key = cache_month_key(payload)
    cached = get_cached_data(payload.info_type, payload.year, month_key, payload.calc_date)
    history_calc_date = cached.get("calc_date") if cached else None
    if not fill_missing_history and (not cached or cached.get("t_1_value") is None or cached.get("std_value") is None):
        calculated = calculate_missing_cache_from_prices(payload)
        if calculated:
            cached = {**calculated, "calc_date": payload.calc_date}
            history_calc_date = payload.calc_date
        else:
            latest_cached = get_latest_cached_data(payload.info_type, payload.year, month_key, payload.calc_date)
            if latest_cached and latest_cached.get("calc_date") == payload.calc_date:
                cached = latest_cached
                history_calc_date = latest_cached.get("calc_date")
            elif latest_cached and not cached:
                history_calc_date = latest_cached.get("calc_date")
    if fill_missing_history and (not cached or cached.get("t_1_value") is None or cached.get("std_value") is None):
        calculated = calculate_missing_cache_from_prices(payload)
        if calculated:
            cached = {**calculated, "calc_date": payload.calc_date}
            history_calc_date = payload.calc_date
        else:
            latest_cached = get_latest_cached_data(payload.info_type, payload.year, month_key, payload.calc_date)
            if latest_cached and latest_cached.get("calc_date") == payload.calc_date:
                cached = latest_cached
                history_calc_date = latest_cached.get("calc_date")
            elif latest_cached and not cached:
                history_calc_date = latest_cached.get("calc_date")
    return response_from_cache(payload, cached, realtime, history_calc_date=history_calc_date)


@app.post("/api/info-summary/calculate-all")
def calculate_info_summary_all(payload: InfoCalculateAllIn, mock: bool = False, user=Depends(current_user)):
    require_view("info_summary", user)
    provider = RealtimeQuoteProvider(mock=mock)
    dependencies = []
    for item in payload.items:
        dependencies.extend(realtime_dependencies_for_payload(item))
    provider.prefetch(dependencies)
    cards = [
        calculate_info_summary_payload(
            item,
            mock=mock,
            quote_provider=provider,
            fill_missing_history=False,
        )
        for item in payload.items
    ]
    if payload.audit_source == "manual":
        db.log_operation(user["id"], "info_summary", "批量计算指标", "全部", "calculated_data", None)
    return {
        "calc_date": payload.items[0].calc_date if payload.items else date.today().isoformat(),
        "cards": cards,
        "quote_status": {
            "requested": len(dependencies),
            "unique": len(set(dependencies)),
        },
    }


@app.post("/api/info-summary/calculate")
def calculate_info_summary(payload: InfoCalculateIn, mock: bool = False, user=Depends(current_user)):
    require_view("info_summary", user)
    result = calculate_info_summary_payload(payload, mock=mock)
    db.log_operation(user["id"], "info_summary", "计算指标", payload.info_type, "calculated_data", None)
    return result


@app.get("/api/info-summary/cache/counts")
def info_summary_cache_counts(user=Depends(current_user)):
    require_view("info_summary", user)
    return cache_counts()


@app.get("/api/info-summary/cache/status")
def info_summary_cache_status(user=Depends(current_user)):
    require_view("info_summary", user)
    return ttl_cached("info_summary:cache_status", 30, _info_summary_cache_status_payload)


def _info_summary_cache_status_payload():
    with db.connect() as conn:
        cur = conn.cursor()
        price_rows = db._exec(cur,
            """
            SELECT info_type, MAX(calc_date) AS latest_price_date
            FROM daily_prices
            GROUP BY info_type
            """
        ).fetchall()
        calculated_rows = db._exec(cur,
            """
            SELECT info_type, MAX(calc_date) AS latest_calculated_date
            FROM calculated_data
            GROUP BY info_type
            """
        ).fetchall()

    indicators = {info_type: {"info_type": info_type} for info_type in INFO_TYPES}
    for row in price_rows:
        info_type = row["info_type"]
        indicators.setdefault(info_type, {"info_type": info_type})["latest_price_date"] = row["latest_price_date"]
    for row in calculated_rows:
        info_type = row["info_type"]
        indicators.setdefault(info_type, {"info_type": info_type})["latest_calculated_date"] = row["latest_calculated_date"]

    return {
        "cache_counts": cache_counts(),
        "indicators": [indicators[key] for key in indicators],
        "last_backfill": get_last_backfill_status(),
        "last_close_cache_update": get_last_close_cache_update_status(),
    }


@app.post("/api/info-summary/cache/backfill")
def backfill_info_summary_cache(payload: InfoBackfillIn, user=Depends(current_user)):
    require_edit("info_summary", user)
    request = BackfillRequest(info_type=payload.info_type, calc_date=payload.calc_date, force=payload.force)
    threading.Thread(target=run_all_info_summary_backfills, args=(request,), daemon=True).start()
    status = "started"
    db.log_operation(user["id"], "info_summary", "启动历史缓存回填", payload.info_type or "全部指标", "daily_prices", None)
    return {
        "status": status,
        "results": [],
        "message": "历史缓存回填已启动",
        "cache_counts": cache_counts(),
    }


@app.post("/api/info-summary/cache/import")
def import_info_summary_cache(user=Depends(current_user)):
    require_permission(user, "info_summary", "import")
    try:
        result = import_desktop_cache()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.log_operation(user["id"], "info_summary", "导入缓存", "从旧版 data_cache.db 导入缓存", "calculated_data", None)
    return result


@app.post("/api/info-summary/indicators")
def create_indicator(payload: InfoIndicatorIn, user=Depends(current_user)):
    require_edit("info_summary", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            """
            INSERT INTO calculated_data
                (info_type, year, month, calc_date, t_1_value, t_2_value,
                 mean_value, min_value, max_value, std_value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(info_type, year, month, calc_date) DO UPDATE SET
                t_1_value = excluded.t_1_value,
                t_2_value = excluded.t_2_value,
                mean_value = excluded.mean_value,
                min_value = excluded.min_value,
                max_value = excluded.max_value,
                std_value = excluded.std_value,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                payload.info_type,
                payload.year,
                payload.month,
                payload.calc_date,
                payload.t_1_value,
                payload.t_2_value,
                payload.mean_value,
                payload.min_value,
                payload.max_value,
                payload.std_value,
            ),
        )
    db.log_operation(user["id"], "info_summary", "保存指标", payload.info_type, "calculated_data", db.last_insert_id(conn))
    return {"ok": True}


@app.delete("/api/info-summary/indicators/{indicator_id}")
def delete_indicator(indicator_id: int, user=Depends(current_user)):
    require_permission(user, "info_summary", "delete")
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, "DELETE FROM calculated_data WHERE id = ?", (indicator_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="指标记录不存在")
    db.log_operation(user["id"], "info_summary", "删除指标", "删除实时信息汇总记录", "calculated_data", indicator_id)
    return {"ok": True}


@app.get("/api/ledgers/sh-junneng/config")
def sh_junneng_config(user=Depends(current_user)):
    require_view("sh_junneng", user)
    return {
        "contracts": contract_options(),
        "default_contract": contract_options()[0] if contract_options() else "",
        "default_open_date": date.today().isoformat(),
    }


@app.get("/api/ledgers/sh-junneng/trades")
def list_sh_junneng_trades(
    status: Optional[str] = None,
    direction: Optional[str] = None,
    contract_month: Optional[str] = None,
    keyword: Optional[str] = None,
    selected_date: Optional[str] = None,
    limit: int = 5000,
    offset: int = 0,
    user=Depends(current_user),
):
    require_view("sh_junneng", user)
    limit = max(1, min(limit or 5000, 5000))
    offset = max(0, offset or 0)
    clauses = []
    params: list = []
    selected_date = selected_date or date.today().isoformat()

    if direction in {"long", "short", "多头", "空头", "多", "空"}:
        clauses.append("direction = ?")
        params.append(normalize_sh_junneng_direction(direction))
    if contract_month:
        normalized = normalize_sh_junneng_contract(contract_month)
        clauses.append("contract_month LIKE ?")
        params.append(f"%{normalized}%")
    if keyword:
        clauses.append("(contract_month LIKE ? OR open_date LIKE ? OR business_code LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    clauses.append("open_date <= ?")
    params.append(selected_date)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connect() as conn:
        cur = conn.cursor()
        total_row = db._exec(
            cur,
            f"SELECT COUNT(*) AS c FROM sh_junneng_positions {where_clause}",
            params,
        ).fetchone()
        rows = db._exec(cur, 
            f"""
            SELECT id, source_trade_id, contract_month, direction, open_price,
                   open_quantity, remaining_quantity, open_amount, open_fee,
                   open_date, current_price, business_code, status,
                   created_by, created_at, updated_by, updated_at
            FROM sh_junneng_positions
            {where_clause}
            ORDER BY open_date DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        position_ids = [row["id"] for row in rows]
        close_rows = []
        closed_quantity_by_position = {}
        if position_ids:
            placeholders = ",".join(["?"] * len(position_ids))
            quantity_rows = db._exec(cur,
                f"""
                SELECT position_id, SUM(close_quantity) AS closed_quantity
                FROM sh_junneng_close_trades
                WHERE position_id IN ({placeholders}) AND close_date <= ?
                GROUP BY position_id
                """,
                [*position_ids, selected_date],
            ).fetchall()
            closed_quantity_by_position = {
                row["position_id"]: row["closed_quantity"] or 0
                for row in quantity_rows
            }
            close_rows = db._exec(cur,
                f"""
                SELECT c.*, p.contract_month, p.direction, p.open_price, p.open_quantity,
                       p.remaining_quantity, p.open_date, p.current_price
                FROM sh_junneng_close_trades c
                JOIN sh_junneng_positions p ON p.id = c.position_id
                WHERE c.position_id IN ({placeholders}) AND c.close_date <= ?
                ORDER BY c.close_date DESC, c.id DESC
                """,
                [*position_ids, selected_date],
            ).fetchall()

    positions = [
        sh_junneng_position_snapshot(row, closed_quantity_by_position.get(row["id"], 0))
        for row in rows
    ]
    close_trades = [with_sh_junneng_fund_fields(sh_junneng_close_snapshot(row)) for row in close_rows]
    selected_month = selected_date[:7]
    today_trades = [
        item for item in positions if item.get("open_date") == selected_date
    ] + [
        item for item in close_trades if item.get("close_date") == selected_date
    ]
    current_trades = [
        item for item in positions
        if item.get("remaining_quantity", 0) > 0
    ]
    settled_trades = [
        item for item in close_trades
        if item.get("close_date") and item["close_date"][:7] == selected_month
    ]
    if status == "未平仓":
        filtered_positions = current_trades
    elif status == "已平仓":
        filtered_positions = [item for item in positions if item.get("remaining_quantity", 0) <= 0]
    else:
        filtered_positions = positions
    return {
        "trades": filtered_positions,
        "summary": {
            "total_count": len(filtered_positions),
            "open_count": len(current_trades),
            "closed_count": len([item for item in positions if item.get("remaining_quantity", 0) <= 0]),
            "total_holding": sum(item.get("remaining_quantity") or 0 for item in current_trades),
            "total_profit": sum(item.get("profit") or 0 for item in [*current_trades, *settled_trades]),
        },
        "today_trades": today_trades,
        "current_trades": current_trades,
        "settled_trades": settled_trades,
        "totals": {
            "today": summarize_sh_junneng_table(today_trades),
            "current": summarize_sh_junneng_table(current_trades),
            "settled": summarize_sh_junneng_table(settled_trades),
        },
        "selected_date": selected_date,
        "pagination": {
            "total": int(total_row["c"] or 0),
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < int(total_row["c"] or 0),
        },
    }


LEGACY_LEDGER_RETIRED_MESSAGE = "旧台账管理已退役，请使用交易管理"


def reject_retired_legacy_ledger_operation() -> None:
    raise HTTPException(status_code=410, detail=LEGACY_LEDGER_RETIRED_MESSAGE)


@app.post("/api/ledgers/sh-junneng/trades")
def create_sh_junneng_trade(payload: ShJunnengTradeIn, user=Depends(current_user)):
    reject_retired_legacy_ledger_operation()
    require_edit("sh_junneng", user)
    if payload.direction not in {"long", "short", "多头", "空头", "多", "空"}:
        raise HTTPException(status_code=400, detail="无效的交易方向")
    contract_month = normalize_sh_junneng_contract(payload.contract_month)
    direction = normalize_sh_junneng_direction(payload.direction)
    is_closed = payload.is_closed == "已平仓" or (payload.close_price is not None and payload.close_fee is not None and payload.close_date)
    if is_closed and (payload.close_price is None or not payload.close_date):
        raise HTTPException(status_code=400, detail="已平仓交易必须填写平仓价格和平仓日期")
    current_price = payload.close_price if is_closed else payload.current_price
    if not is_closed and current_price is None:
        current_price = fetch_sh_junneng_current_price(contract_month)
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur,
            """
            INSERT INTO sh_junneng_positions
                (contract_month, direction, open_price, open_quantity, remaining_quantity,
                 open_amount, open_fee, open_date, current_price, status, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract_month,
                direction,
                payload.open_price,
                payload.trade_quantity,
                0 if is_closed else payload.trade_quantity,
                payload.open_price * payload.trade_quantity,
                payload.open_fee,
                payload.open_date,
                current_price,
                "closed" if is_closed else "open",
                user["name"],
                user["name"],
            ),
        )
        trade_id = db.last_insert_id(conn)
        business_code = sh_junneng_business_code(trade_id)
        db._exec(cur, "UPDATE sh_junneng_positions SET business_code = ? WHERE id = ?", (business_code, trade_id))
        if is_closed:
            close_fee = payload.close_fee or 0
            realized_profit = calculate_sh_junneng_realized_profit(
                contract_month,
                direction,
                payload.open_price,
                payload.close_price,
                payload.trade_quantity,
                payload.open_fee,
                close_fee,
            )
            db._exec(cur,
                """
                INSERT INTO sh_junneng_close_trades
                    (position_id, close_date, close_quantity, close_price, close_amount,
                     close_fee, open_fee_allocated, close_sequence, business_code,
                     realized_profit, created_by, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    payload.close_date,
                    payload.trade_quantity,
                    payload.close_price,
                    payload.close_price * payload.trade_quantity,
                    close_fee,
                    payload.open_fee,
                    1,
                    business_code,
                    realized_profit,
                    user["name"],
                    user["name"],
                ),
            )
    db.log_operation(user["id"], "sh_junneng", "新增交易", f"新增上海钧能交易 {contract_month}", "sh_junneng_positions", trade_id)
    return {"id": trade_id}


@app.put("/api/ledgers/sh-junneng/trades/{trade_id}")
def update_sh_junneng_trade(trade_id: int, payload: ShJunnengTradeIn, user=Depends(current_user)):
    reject_retired_legacy_ledger_operation()
    require_edit("sh_junneng", user)
    if payload.direction not in {"long", "short", "多头", "空头", "多", "空"}:
        raise HTTPException(status_code=400, detail="无效的交易方向")
    contract_month = normalize_sh_junneng_contract(payload.contract_month)
    direction = normalize_sh_junneng_direction(payload.direction)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT * FROM sh_junneng_positions WHERE id = ?", (trade_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="交易记录不存在")
        closed_row = db._exec(cur,
            "SELECT SUM(close_quantity) AS closed_quantity FROM sh_junneng_close_trades WHERE position_id = ?",
            (trade_id,),
        ).fetchone()
        closed_quantity = (closed_row["closed_quantity"] if closed_row else 0) or 0
        if payload.trade_quantity < closed_quantity:
            raise HTTPException(status_code=400, detail="开仓数量不能小于已平数量")
        current_price = payload.current_price
        if current_price is None:
            current_price = fetch_sh_junneng_current_price(contract_month)
        remaining_quantity = payload.trade_quantity - closed_quantity
        db._exec(cur, 
            """
            UPDATE sh_junneng_positions
            SET contract_month = ?, direction = ?, open_price = ?, open_quantity = ?,
                remaining_quantity = ?, open_amount = ?, open_fee = ?, open_date = ?,
                current_price = ?, status = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                contract_month,
                direction,
                payload.open_price,
                payload.trade_quantity,
                remaining_quantity,
                payload.open_price * payload.trade_quantity,
                payload.open_fee,
                payload.open_date,
                current_price,
                "closed" if remaining_quantity <= 0 else ("partial_closed" if closed_quantity else "open"),
                user["name"],
                trade_id,
            ),
        )
    db.log_operation(user["id"], "sh_junneng", "编辑交易", f"编辑上海钧能交易 {contract_month}", "sh_junneng_positions", trade_id)
    return {"ok": True}


@app.delete("/api/ledgers/sh-junneng/trades/{trade_id}")
def delete_sh_junneng_trade(trade_id: int, user=Depends(current_user)):
    reject_retired_legacy_ledger_operation()
    require_permission(user, "sh_junneng.trades", "delete")
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id FROM sh_junneng_positions WHERE id = ?", (trade_id,)).fetchone()
        if existing:
            db._exec(cur, "DELETE FROM sh_junneng_close_trades WHERE position_id = ?", (trade_id,))
            cursor = db._exec(cur, "DELETE FROM sh_junneng_positions WHERE id = ?", (trade_id,))
        else:
            cursor = db._exec(cur, "DELETE FROM sh_junneng_trades WHERE id = ?", (trade_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    db.log_operation(user["id"], "sh_junneng", "删除交易", "删除上海钧能交易", "sh_junneng_positions", trade_id)
    return {"ok": True}


@app.post("/api/ledgers/sh-junneng/trades/{trade_id}/close")
def close_sh_junneng_trade(trade_id: int, payload: ShJunnengTradeCloseIn, user=Depends(current_user)):
    reject_retired_legacy_ledger_operation()
    require_edit("sh_junneng", user)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT * FROM sh_junneng_positions WHERE id = ?", (trade_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="交易记录不存在")
        remaining_quantity = existing["remaining_quantity"] or 0
        if remaining_quantity <= 0:
            raise HTTPException(status_code=400, detail="该交易已平仓")
        close_quantity = payload.close_quantity or remaining_quantity
        if close_quantity > remaining_quantity:
            raise HTTPException(status_code=400, detail="平仓数量不能超过当前剩余数量")
        open_quantity = existing["open_quantity"] or 0
        open_fee_allocated = (existing["open_fee"] or 0) * close_quantity / open_quantity if open_quantity else 0
        sequence_row = db._exec(cur,
            "SELECT COALESCE(MAX(close_sequence), 0) AS max_sequence FROM sh_junneng_close_trades WHERE position_id = ?",
            (trade_id,),
        ).fetchone()
        close_sequence = (sequence_row["max_sequence"] or 0) + 1
        business_code = existing["business_code"] or sh_junneng_business_code(trade_id)
        realized_profit = calculate_sh_junneng_realized_profit(
            existing["contract_month"],
            existing["direction"],
            existing["open_price"],
            payload.close_price,
            close_quantity,
            open_fee_allocated,
            payload.close_fee,
        )
        db._exec(cur,
            """
            INSERT INTO sh_junneng_close_trades
                (position_id, close_date, close_quantity, close_price, close_amount,
                 close_fee, open_fee_allocated, close_sequence, business_code,
                 realized_profit, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
                payload.close_date,
                close_quantity,
                payload.close_price,
                payload.close_price * close_quantity,
                payload.close_fee,
                open_fee_allocated,
                close_sequence,
                business_code,
                realized_profit,
                user["name"],
                user["name"],
            ),
        )
        next_remaining = round(remaining_quantity - close_quantity, 10)
        status_value = "closed" if next_remaining <= 0 else "partial_closed"
        db._exec(cur,
            """
            UPDATE sh_junneng_positions
            SET remaining_quantity = ?, current_price = ?, status = ?,
                updated_by = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_remaining, payload.close_price, status_value, user["name"], trade_id),
        )
    db.log_operation(user["id"], "sh_junneng", "平仓", f"上海钧能交易平仓 {trade_id}", "sh_junneng_close_trades", trade_id)
    return {"ok": True}


@app.post("/api/ledgers/sh-junneng/prices/refresh")
def refresh_sh_junneng_prices(mock: bool = False, user=Depends(current_user)):
    reject_retired_legacy_ledger_operation()
    require_edit("sh_junneng", user)
    result = refresh_sh_junneng_trade_prices(mock=mock)
    db.log_operation(user["id"], "sh_junneng", "刷新价格", f"刷新 {result['refreshed_contracts']} 个上海钧能合约价格")
    return result


@app.post("/api/ledgers/sh-junneng/prices/manual")
def update_sh_junneng_prices_manually(payload: ShJunnengManualPricesIn, user=Depends(current_user)):
    reject_retired_legacy_ledger_operation()
    require_edit("sh_junneng", user)
    updated = 0
    with db.connect() as conn:
        cur = conn.cursor()
        for contract_month, price in payload.prices.items():
            normalized = normalize_sh_junneng_contract(contract_month)
            rows = db._exec(cur, 
                """
                SELECT id
                FROM sh_junneng_positions
                WHERE contract_month = ? AND remaining_quantity > 0
                """,
                (normalized,),
            ).fetchall()
            for row in rows:
                db._exec(cur, 
                    """
                    UPDATE sh_junneng_positions
                    SET current_price = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (price, user["name"], row["id"]),
                )
                updated += 1
    db.log_operation(user["id"], "sh_junneng", "手动更新价格", f"更新 {updated} 条上海钧能价格")
    return {"updated": updated}


@app.get("/api/ledgers/sh-junneng/settled-overview")
def sh_junneng_settled_overview(
    open_date_from: Optional[str] = None,
    open_date_to: Optional[str] = None,
    close_date_from: Optional[str] = None,
    close_date_to: Optional[str] = None,
    contracts: Optional[str] = None,
    user=Depends(current_user),
):
    require_view("sh_junneng", user)
    clauses = ["c.close_date IS NOT NULL", "c.close_price > 0"]
    params: list = []
    if open_date_from:
        clauses.append("p.open_date >= ?")
        params.append(open_date_from)
    if open_date_to:
        clauses.append("p.open_date <= ?")
        params.append(open_date_to)
    if close_date_from:
        clauses.append("c.close_date >= ?")
        params.append(close_date_from)
    if close_date_to:
        clauses.append("c.close_date <= ?")
        params.append(close_date_to)
    selected_contracts = [item.strip().upper() for item in (contracts or "").split(",") if item.strip()]
    if selected_contracts:
        clauses.append(f"p.contract_month IN ({','.join(['?'] * len(selected_contracts))})")
        params.extend(selected_contracts)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            f"""
            SELECT c.*, p.contract_month, p.direction, p.open_price, p.open_quantity,
                   p.remaining_quantity, p.open_date, p.current_price
            FROM sh_junneng_close_trades c
            JOIN sh_junneng_positions p ON p.id = c.position_id
            WHERE {' AND '.join(clauses)}
            ORDER BY c.close_date DESC, c.id DESC
            """,
            params,
        ).fetchall()
        contract_rows = db._exec(cur, 
            """
            SELECT DISTINCT contract_month
            FROM sh_junneng_positions
            WHERE contract_month != ''
            ORDER BY contract_month
            """
        ).fetchall()
    trades = [with_sh_junneng_fund_fields(sh_junneng_close_snapshot(row)) for row in rows]
    return {
        "trades": trades,
        "totals": summarize_sh_junneng_table(trades),
        "contracts": [row["contract_month"] for row in contract_rows],
    }


@app.get("/api/ledgers/sh-junneng/export")
def export_sh_junneng_trades(selected_date: Optional[str] = None, user=Depends(current_user)):
    reject_retired_legacy_ledger_operation()
    require_permission(user, "sh_junneng.trades", "export")
    selected_date = selected_date or date.today().isoformat()
    sections = list_sh_junneng_trades(selected_date=selected_date, limit=5000, offset=0, user=user)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["类别", "合约月份", "交易方向", "开仓均价", "平仓均价", "当日收盘价", "原始数量", "本次平仓数量", "剩余数量", "手续费(开)", "手续费(平)", "盈亏(含手续费)", "开仓日期", "平仓日期", "持仓状态", "业务编码", "资金利息", "利润按比例结算金额(80%)", "利润按比例结算金额(20%)"])
    for title, key in [("今日交易", "today_trades"), ("即期持仓", "current_trades"), ("已平仓数据", "settled_trades")]:
        for item in sections[key]:
            writer.writerow([
                title,
                item["contract_month"],
                item["direction_label"],
                item["open_price"],
                item["display_close_price"],
                item["current_price"],
                item.get("open_quantity", ""),
                item.get("close_quantity", ""),
                item.get("remaining_quantity", ""),
                item["open_fee"],
                item["display_close_fee"],
                item["profit"],
                item["open_date"],
                item["display_close_date"],
                item.get("position_status") or item["is_closed_label"],
                item.get("business_code", ""),
                item.get("interest", ""),
                item.get("profit_80", ""),
                item.get("profit_20", ""),
            ])
    db.log_operation(user["id"], "sh_junneng", "导出台账", f"导出上海钧能台账 {selected_date}", "sh_junneng_positions", None)
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=sh_junneng_trades_{selected_date}.csv"},
    )


@app.get("/api/mid-event/groups")
def list_strategy_groups(user=Depends(current_user)):
    require_view("mid_event_monitor", user)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            """
            SELECT id, group_name, created_by, created_at, updated_by, updated_at
            FROM strategy_groups
            ORDER BY updated_at DESC, id DESC
            LIMIT 200
            """,
        ).fetchall()
    groups = []
    for row in rows:
        item = row_to_dict(row)
        item["total_pnl"] = group_pnl(item["id"])
        groups.append(item)
    return {"groups": groups, "all_total_pnl": all_groups_total_pnl(), "prices": REALTIME_PRICES}


@app.get("/api/mid-event/config")
def mid_event_config(user=Depends(current_user)):
    require_view("mid_event_monitor", user)
    return {
        "varieties": [
            {"code": code, **config}
            for code, config in VARIETY_CONFIG.items()
        ],
        "contracts": contract_options(),
    }


@app.post("/api/mid-event/groups")
def create_strategy_group(payload: StrategyGroupIn, user=Depends(current_user)):
    require_edit("mid_event_monitor", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            "INSERT INTO strategy_groups (group_name, created_by) VALUES (?, ?)",
            (payload.group_name, user["name"]),
        )
        group_id = db.last_insert_id(conn)
    db.log_operation(user["id"], "mid_event_monitor", "新增策略组", payload.group_name, "strategy_groups", group_id)
    return {"id": group_id}


@app.put("/api/mid-event/groups/{group_id}")
def update_strategy_group(group_id: int, payload: StrategyGroupIn, user=Depends(current_user)):
    require_edit("mid_event_monitor", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            "UPDATE strategy_groups SET group_name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (payload.group_name, group_id),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="策略组不存在")
    db.log_operation(user["id"], "mid_event_monitor", "编辑策略组", payload.group_name, "strategy_groups", group_id)
    return {"ok": True}


@app.delete("/api/mid-event/groups/{group_id}")
def delete_strategy_group(group_id: int, user=Depends(current_user)):
    require_permission(user, "mid_event.monitor", "delete")
    with db.connect() as conn:
        cur = conn.cursor()
        group = db._exec(cur, "SELECT id FROM strategy_groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="策略组不存在")
        db._exec(cur, "DELETE FROM strategy_positions WHERE group_id = ?", (group_id,))
        db._exec(cur, "DELETE FROM strategy_groups WHERE id = ?", (group_id,))
    db.log_operation(user["id"], "mid_event_monitor", "删除策略组", "删除策略组及持仓", "strategy_groups", group_id)
    return {"ok": True}


@app.get("/api/mid-event/groups/{group_id}/positions")
def list_strategy_positions(group_id: int, user=Depends(current_user)):
    require_view("mid_event_monitor", user)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT id, group_id, variety, variety_name, direction, open_price,
                   quantity, multiplier, contract, created_at, updated_at
            FROM strategy_positions
            WHERE group_id = ?
            ORDER BY id DESC
            """,
            (group_id,),
        ).fetchall()
    summary = summarize_positions(rows)
    summary["all_total_pnl"] = all_groups_total_pnl()
    summary["prices"] = REALTIME_PRICES
    return summary


@app.post("/api/mid-event/groups/{group_id}/positions")
def create_strategy_position(group_id: int, payload: StrategyPositionIn, user=Depends(current_user)):
    require_edit("mid_event_monitor", user)
    if payload.variety not in VARIETY_CONFIG:
        raise HTTPException(status_code=400, detail="无效的品种")
    config = VARIETY_CONFIG[payload.variety]
    variety_name = config["name"]
    multiplier = config["multiplier"]
    contract = normalize_contract(payload.variety, payload.contract)
    with db.connect() as conn:
        cur = conn.cursor()
        group = db._exec(cur, "SELECT id FROM strategy_groups WHERE id = ?", (group_id,)).fetchone()
        if not group:
            raise HTTPException(status_code=404, detail="策略组不存在")
        cursor = db._exec(cur, 
            """
            INSERT INTO strategy_positions
                (group_id, variety, variety_name, direction, open_price, quantity, multiplier, contract)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id,
                payload.variety,
                variety_name,
                payload.direction,
                payload.open_price,
                payload.quantity,
                multiplier,
                contract,
            ),
        )
        position_id = db.last_insert_id(conn)
    db.log_operation(user["id"], "mid_event_monitor", "新增策略持仓", payload.variety, "strategy_positions", position_id)
    return {"id": position_id}


@app.put("/api/mid-event/positions/{position_id}")
def update_strategy_position(position_id: int, payload: StrategyPositionIn, user=Depends(current_user)):
    require_edit("mid_event_monitor", user)
    if payload.variety not in VARIETY_CONFIG:
        raise HTTPException(status_code=400, detail="无效的品种")
    config = VARIETY_CONFIG[payload.variety]
    contract = normalize_contract(payload.variety, payload.contract)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            """
            UPDATE strategy_positions
            SET variety = ?, variety_name = ?, direction = ?, open_price = ?,
                quantity = ?, multiplier = ?, contract = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload.variety,
                config["name"],
                payload.direction,
                payload.open_price,
                payload.quantity,
                config["multiplier"],
                contract,
                position_id,
            ),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="策略持仓不存在")
    db.log_operation(user["id"], "mid_event_monitor", "编辑策略持仓", payload.variety, "strategy_positions", position_id)
    return {"ok": True}


@app.post("/api/mid-event/prices/refresh")
def refresh_mid_event_prices(mock: bool = False, user=Depends(current_user)):
    require_edit("mid_event_monitor", user)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, "SELECT DISTINCT variety, contract FROM strategy_positions").fetchall()

    refreshed_contracts = []
    reused_contracts = []
    missing_contracts = []
    fx = fetch_usdcnh_rate("", mock)
    if fx is not None:
        REALTIME_PRICES["USD/CNY"] = fx
        refreshed_contracts.append("USD/CNY")
    elif "USD/CNY" in REALTIME_PRICES:
        reused_contracts.append("USD/CNY")
    for row in rows:
        variety = row["variety"]
        contract = normalize_contract(variety, row["contract"])
        key = price_key_for_position(variety, contract)
        if variety == "USD/CNY":
            price = fetch_usdcnh_rate(contract, mock)
        else:
            price = fetch_sina_price(variety, contract, mock)
        if price is not None:
            REALTIME_PRICES[key] = price
            refreshed_contracts.append(key)
        elif key in REALTIME_PRICES:
            reused_contracts.append(key)
        else:
            missing_contracts.append(key)
        if variety == "FE" and contract:
            rate_key = exchange_rate_key(contract)
            rate = fetch_usdcnh_rate(contract, mock)
            if rate is not None:
                REALTIME_PRICES[rate_key] = rate
                refreshed_contracts.append(rate_key)
            elif rate_key in REALTIME_PRICES:
                reused_contracts.append(rate_key)
            else:
                missing_contracts.append(rate_key)
    db.log_operation(user["id"], "mid_event_monitor", "刷新价格", f"刷新 {len(refreshed_contracts)} 个价格")
    return {
        "ok": True,
        "prices": REALTIME_PRICES,
        "refreshed_contracts": refreshed_contracts,
        "reused_contracts": reused_contracts,
        "missing_contracts": missing_contracts,
    }


@app.delete("/api/mid-event/positions/{position_id}")
def delete_strategy_position(position_id: int, user=Depends(current_user)):
    require_permission(user, "mid_event.monitor", "delete")
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, "DELETE FROM strategy_positions WHERE id = ?", (position_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="策略持仓不存在")
    db.log_operation(user["id"], "mid_event_monitor", "删除策略持仓", "删除策略持仓", "strategy_positions", position_id)
    return {"ok": True}


# ── 用户管理 ──────────────────────────────────────────────

class UserIn(BaseModel):
    name: str = Field(min_length=1)
    username: str = ""
    department: str = Field(min_length=1)
    role: str = "用户"
    permissions: Optional[list[dict[str, str]]] = None


class UserPreviewIn(UserIn):
    user_id: Optional[int] = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class AdminSetPasswordIn(BaseModel):
    new_password: str = Field(min_length=1)
    password_change_recommended: bool = True


class UserStatusIn(BaseModel):
    status: str


class PermissionsBatchIn(BaseModel):
    permissions: list[dict[str, object]]


PERMISSION_LEVEL_VALUES = {
    "none": (0, 0, 0),
    "view": (1, 0, 0),
    "operate": (1, 1, 0),
    "sensitive": (1, 1, 1),
}
PERMISSION_LEVEL_RANK = {level: rank for rank, level in enumerate(PERMISSION_LEVEL_VALUES)}


def _require_admin(user: dict) -> None:
    if user.get("role") not in {"管理员", "admin"}:
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")


def _suggest_username(name: str) -> str:
    if lazy_pinyin is None:
        raise HTTPException(status_code=503, detail="拼音组件未安装，请手工填写登录账号")
    return re.sub(r"[^a-z0-9]", "", "".join(lazy_pinyin(name.strip())).lower())


def _validate_user_identity(name: str, username: str, department: str, role: str) -> tuple[str, str]:
    clean_name = name.strip()
    clean_username = (username or "").strip().lower() or _suggest_username(clean_name)
    if not clean_name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if department not in DEPARTMENTS:
        raise HTTPException(status_code=400, detail="部门不在允许范围内")
    if role not in USER_ROLES:
        raise HTTPException(status_code=400, detail="用户类型不在允许范围内")
    if role == "管理员" and department != "管理部门":
        raise HTTPException(status_code=400, detail="只有管理部门用户可设为管理员")
    if not re.fullmatch(r"[a-z][a-z0-9._-]{1,63}", clean_username):
        raise HTTPException(status_code=400, detail="登录账号需以字母开头，仅能包含小写字母、数字、点、下划线或短横线")
    return clean_name, clean_username


def _final_permission_levels(department: str, role: str, overrides: Optional[list[dict]]) -> dict[str, str]:
    levels = default_permission_levels(department, role)
    if role == "管理员":
        return levels
    valid_codes = set(levels)
    for item in overrides or []:
        code = str(item.get("module_code", ""))
        level = str(item.get("level", ""))
        if code not in valid_codes or level not in PERMISSION_LEVEL_VALUES:
            raise HTTPException(status_code=400, detail="权限模块或级别无效")
        if role == "领导" and PERMISSION_LEVEL_RANK[level] < PERMISSION_LEVEL_RANK[levels[code]]:
            continue
        levels[code] = level
    return levels


def _write_permission_snapshot(cur, user_id: int, levels: dict[str, str]) -> None:
    for module_code, level in levels.items():
        can_view, can_edit, can_sensitive = PERMISSION_LEVEL_VALUES[level]
        db._exec(
            cur,
            """
            INSERT OR IGNORE INTO module_permissions
                (user_id, module_code, can_view, can_edit, can_sensitive)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, module_code, can_view, can_edit, can_sensitive),
        )
        db._exec(
            cur,
            """
            UPDATE module_permissions
            SET can_view = ?, can_edit = ?, can_sensitive = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND module_code = ?
            """,
            (can_view, can_edit, can_sensitive, user_id, module_code),
        )


def _permission_level(row: dict) -> str:
    if row.get("can_sensitive"):
        return "sensitive"
    if row.get("can_edit"):
        return "operate"
    if row.get("can_view"):
        return "view"
    return "none"


@app.post("/api/users/preview")
def preview_user(payload: UserPreviewIn, user=Depends(current_user)):
    _require_admin(user)
    name, username = _validate_user_identity(payload.name, payload.username, payload.department, payload.role)
    password_policy = temporary_password_policy(name, username, payload.department, payload.role)
    default_levels = default_permission_levels(payload.department, payload.role)
    final_levels = _final_permission_levels(payload.department, payload.role, payload.permissions)
    with db.connect() as conn:
        cur = conn.cursor()
        duplicate = db._exec(
            cur,
            "SELECT id FROM users WHERE username = ? AND (? IS NULL OR id != ?)",
            (username, payload.user_id, payload.user_id),
        ).fetchone()
        current_levels: dict[str, str] = {}
        if payload.user_id:
            rows = db._exec(
                cur,
                "SELECT module_code, can_view, can_edit, can_sensitive FROM module_permissions WHERE user_id = ?",
                (payload.user_id,),
            ).fetchall()
            current_levels = {row["module_code"]: _permission_level(dict(row)) for row in rows}
    changes = [
        {"module_code": code, "before": current_levels.get(code, "none"), "after": level}
        for code, level in final_levels.items()
        if current_levels.get(code, "none") != level
    ]
    return {
        "name": name,
        "username": username,
        "temporary_password": password_policy["temporary_password"],
        "password_rule": password_policy["password_rule"],
        "username_available": duplicate is None,
        "default_permissions": {code: level for code, level in default_levels.items() if code in ACTIVE_BUSINESS_MODULES},
        "final_permissions": {code: level for code, level in final_levels.items() if code in ACTIVE_BUSINESS_MODULES},
        "changes": [item for item in changes if item["module_code"] in ACTIVE_BUSINESS_MODULES],
    }


@app.get("/api/users")
def list_users(
    limit: int = 5000,
    offset: int = 0,
    user=Depends(current_user),
):
    require_permission(user, "users", "manage")
    limit = max(1, min(limit or 5000, 5000))
    offset = max(0, offset or 0)
    with db.connect() as conn:
        cur = conn.cursor()
        total_row = db._exec(cur, "SELECT COUNT(*) AS c FROM users WHERE COALESCE(is_guest, 0) = 0").fetchone()
        rows = db._exec(cur, 
            """
            SELECT id, name, username, department, role, status,
                   password_change_recommended, created_at
            FROM users
            WHERE COALESCE(is_guest, 0) = 0
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        summary_rows = db._exec(
            cur,
            """
            SELECT user_id,
                   SUM(CASE WHEN can_view = 1 THEN 1 ELSE 0 END) AS enabled,
                   SUM(CASE WHEN can_edit = 1 AND can_sensitive = 0 THEN 1 ELSE 0 END) AS operate,
                   SUM(CASE WHEN can_sensitive = 1 THEN 1 ELSE 0 END) AS sensitive
            FROM module_permissions
            GROUP BY user_id
            """,
        ).fetchall()
    summaries = {
        row["user_id"]: {
            "enabled": int(row["enabled"] or 0),
            "operate": int(row["operate"] or 0),
            "sensitive": int(row["sensitive"] or 0),
        }
        for row in summary_rows
    }
    users = []
    for row in rows:
        item = row_to_dict(row)
        item["permission_summary"] = summaries.get(item["id"], {"enabled": 0, "operate": 0, "sensitive": 0})
        users.append(item)
    total = int(total_row["c"] or 0)
    return {
        "users": users,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        },
    }


@app.post("/api/users")
def create_user(payload: UserIn, user=Depends(current_user)):
    _require_admin(user)
    name, username = _validate_user_identity(payload.name, payload.username, payload.department, payload.role)
    levels = _final_permission_levels(payload.department, payload.role, payload.permissions)
    password_policy = temporary_password_policy(name, username, payload.department, payload.role)
    temporary_password = password_policy["temporary_password"]
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="登录账号已存在")
        db._exec(cur,
            """
            INSERT INTO users
                (name, username, department, password_hash, role, password_change_recommended)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (name, username, payload.department, db.password_hash(temporary_password), payload.role),
        )
        new_id = db.last_insert_id(conn)
        _write_permission_snapshot(cur, new_id, levels)
    db.log_operation(user["id"], "user_management", "添加用户", f"添加用户: {name} ({username})", "users", new_id)
    return {"id": new_id, "username": username, "temporary_password": temporary_password}


@app.put("/api/users/{user_id}")
def update_user(user_id: int, payload: UserIn, user=Depends(current_user)):
    _require_admin(user)
    name, username = _validate_user_identity(payload.name, payload.username, payload.department, payload.role)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id, name, username, role, status, is_guest FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="用户不存在")
        if existing["is_guest"]:
            raise HTTPException(status_code=400, detail="系统访客不允许在用户管理中编辑")
        duplicate = db._exec(cur, "SELECT id FROM users WHERE username = ? AND id != ?", (username, user_id)).fetchone()
        if duplicate:
            raise HTTPException(status_code=400, detail="登录账号已存在")
        if existing["role"] == "管理员" and payload.role != "管理员" and existing["status"] == "启用":
            count = db._exec(cur, "SELECT COUNT(*) AS c FROM users WHERE role = '管理员' AND status = '启用'").fetchone()["c"]
            if int(count) <= 1:
                raise HTTPException(status_code=400, detail="不能降级最后一名启用管理员")
        db._exec(cur,
            "UPDATE users SET name = ?, username = ?, department = ?, role = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, username, payload.department, payload.role, user_id),
        )
        if payload.permissions is not None:
            _write_permission_snapshot(cur, user_id, _final_permission_levels(payload.department, payload.role, payload.permissions))
    db.log_operation(user["id"], "user_management", "编辑用户", f"编辑用户: {name} ({username})", "users", user_id)
    return {"ok": True}


@app.post("/api/auth/change-password")
def change_password(
    payload: ChangePasswordIn,
    user=Depends(current_user),
    authorization: Optional[str] = Header(default=None),
):
    if user.get("is_guest") or user.get("cannot_change_password"):
        raise HTTPException(status_code=400, detail="当前账号不允许修改密码")
    if not db.verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少8位")
    default_password = temporary_password_policy(
        user["name"], user["username"], user["department"], user["role"]
    )["temporary_password"]
    if payload.new_password in {payload.current_password, default_password}:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码或默认密码相同")
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "UPDATE users SET password_hash = ?, password_change_recommended = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (db.password_hash(payload.new_password), user["id"]))
        db._exec(cur, "UPDATE user_sessions SET status = '已注销' WHERE user_id = ? AND token != ?", (user["id"], token))
    db.log_operation(user["id"], "auth", "修改密码", f"{user['name']} 修改了自己的密码")
    return {"ok": True}


@app.post("/api/users/{user_id}/reset-password")
def reset_user_password(user_id: int, user=Depends(current_user)):
    _require_admin(user)
    with db.connect() as conn:
        cur = conn.cursor()
        target = db._exec(cur, "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        if target["is_guest"] or target["cannot_change_password"]:
            raise HTTPException(status_code=400, detail="系统访客不允许重置密码")
        password_policy = temporary_password_policy(
            target["name"], target["username"], target["department"], target["role"]
        )
        temporary_password = password_policy["temporary_password"]
        db._exec(cur, "UPDATE users SET password_hash = ?, password_change_recommended = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (db.password_hash(temporary_password), user_id))
        db._exec(cur, "UPDATE user_sessions SET status = '已注销' WHERE user_id = ?", (user_id,))
    db.log_operation(user["id"], "user_management", "重置密码", f"重置用户密码: {target['name']}", "users", user_id)
    return {"ok": True, "username": target["username"], "temporary_password": temporary_password}


@app.post("/api/users/{user_id}/set-password")
def set_user_password(user_id: int, payload: AdminSetPasswordIn, user=Depends(current_user)):
    _require_admin(user)
    with db.connect() as conn:
        cur = conn.cursor()
        target = db._exec(
            cur,
            "SELECT id, name, is_guest, cannot_change_password FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        if target["is_guest"] or target["cannot_change_password"]:
            raise HTTPException(status_code=400, detail="系统访客不允许设置密码")
        db._exec(
            cur,
            "UPDATE users SET password_hash = ?, password_change_recommended = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (db.password_hash(payload.new_password), int(payload.password_change_recommended), user_id),
        )
        db._exec(cur, "UPDATE user_sessions SET status = '已注销' WHERE user_id = ?", (user_id,))
    db.log_operation(
        user["id"],
        "user_management",
        "管理员设置密码",
        f"管理员设置用户密码: {target['name']}",
        "users",
        user_id,
    )
    return {"ok": True}


@app.patch("/api/users/{user_id}/status")
def set_user_status(user_id: int, payload: UserStatusIn, user=Depends(current_user)):
    _require_admin(user)
    if payload.status not in {"启用", "停用"}:
        raise HTTPException(status_code=400, detail="账号状态无效")
    with db.connect() as conn:
        cur = conn.cursor()
        target = db._exec(cur, "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        if target["is_guest"]:
            raise HTTPException(status_code=400, detail="系统访客不允许停用")
        if payload.status == "停用" and target["role"] == "管理员" and target["status"] == "启用":
            count = db._exec(cur, "SELECT COUNT(*) AS c FROM users WHERE role = '管理员' AND status = '启用'").fetchone()["c"]
            if int(count) <= 1:
                raise HTTPException(status_code=400, detail="不能停用最后一名管理员")
        db._exec(cur, "UPDATE users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (payload.status, user_id))
        if payload.status == "停用":
            db._exec(cur, "UPDATE user_sessions SET status = '已注销' WHERE user_id = ?", (user_id,))
    db.log_operation(user["id"], "user_management", "账号状态", f"{payload.status}用户: {target['name']}", "users", user_id)
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, user=Depends(current_user)):
    _require_admin(user)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="不能删除自己")
    with db.connect() as conn:
        cur = conn.cursor()
        target = db._exec(cur, "SELECT name, is_guest FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        if target["is_guest"]:
            raise HTTPException(status_code=400, detail="系统访客不允许在用户管理中删除")
        history = db._exec(
            cur,
            """
            SELECT
                (SELECT COUNT(*) FROM user_sessions WHERE user_id = ?) +
                (SELECT COUNT(*) FROM operation_logs WHERE user_id = ?) +
                (SELECT COUNT(*) FROM operation_log_archive_users WHERE user_id = ?) AS c
            """,
            (user_id, user_id, user_id),
        ).fetchone()
        if int(history["c"] or 0) > 0:
            raise HTTPException(status_code=400, detail="该账号已有会话或操作历史，请使用停用")
        db._exec(cur, "DELETE FROM module_permissions WHERE user_id = ?", (user_id,))
        db._exec(cur, "DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        db._exec(cur, "DELETE FROM users WHERE id = ?", (user_id,))
    db.log_operation(user["id"], "user_management", "删除用户", f"删除用户: {target['name']}", "users", user_id)
    return {"ok": True}


@app.get("/api/users/{user_id}/permissions")
def get_user_permissions(user_id: int, user=Depends(current_user)):
    _require_admin(user)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id, is_guest FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="用户不存在")
        if existing["is_guest"]:
            raise HTTPException(status_code=400, detail="系统访客权限由后端固定控制")
        for _, module_code, _ in db.MODULES:
            db._exec(cur, 
                "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit, can_sensitive) VALUES (?, ?, 0, 0, 0)",
                (user_id, module_code),
            )
        rows = db._exec(cur, 
            "SELECT module_code, can_view, can_edit, can_sensitive FROM module_permissions WHERE user_id = ? ORDER BY module_code",
            (user_id,),
        ).fetchall()
    return {
        "permissions": [
            {"module_code": row["module_code"], "level": _permission_level(dict(row))}
            for row in rows
            if row["module_code"] in ACTIVE_BUSINESS_MODULES
        ]
    }


@app.put("/api/users/{user_id}/permissions")
def set_user_permissions(user_id: int, payload: PermissionsBatchIn, user=Depends(current_user)):
    _require_admin(user)
    target_name = ""
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id, name, department, role, is_guest FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="用户不存在")
        if existing["is_guest"]:
            raise HTTPException(status_code=400, detail="系统访客权限由后端固定控制")
        target_name = existing["name"]
        levels = _final_permission_levels(existing["department"], existing["role"], payload.permissions)
        _write_permission_snapshot(cur, user_id, levels)
    db.log_operation(user["id"], "user_management", "设置权限", f"设置用户权限: {target_name}", "module_permissions", user_id)
    return {"ok": True}


def encode_operation_log_cursor(created_at: str, log_id: int) -> str:
    raw = json.dumps([created_at, int(log_id)], ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_operation_log_cursor(cursor: str) -> tuple[str, int]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8"))
        if not isinstance(payload, list) or len(payload) != 2:
            raise ValueError("cursor payload")
        created_at, log_id = payload
        if not isinstance(created_at, str) or not created_at:
            raise ValueError("cursor timestamp")
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if isinstance(log_id, bool) or int(log_id) < 1:
            raise ValueError("cursor id")
        return created_at, int(log_id)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="日志分页游标无效") from exc


@app.get("/api/operation-logs")
def list_operation_logs(
    operation_type: Optional[str] = None,
    user_name: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 100,
    user=Depends(current_user),
):
    require_permission(user, "operation_logs", "view")
    limit = max(1, min(limit or 100, 200))
    clauses = ["1=1"]
    params: list = []
    if operation_type:
        clauses.append("ol.operation_type = ?")
        params.append(operation_type)
    try:
        if start_date:
            start = date.fromisoformat(start_date)
            clauses.append("ol.created_at >= ?")
            params.append(f"{start.isoformat()} 00:00:00")
        if end_date:
            end_exclusive = date.fromisoformat(end_date) + timedelta(days=1)
            clauses.append("ol.created_at < ?")
            params.append(f"{end_exclusive.isoformat()} 00:00:00")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日志日期格式无效") from exc
    if start_date and end_date and start > date.fromisoformat(end_date):
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    if cursor:
        cursor_created_at, cursor_id = decode_operation_log_cursor(cursor)
        clauses.append("(ol.created_at < ? OR (ol.created_at = ? AND ol.id < ?))")
        params.extend([cursor_created_at, cursor_created_at, cursor_id])
    with db.connect() as conn:
        cur = conn.cursor()
        if user_name:
            user_rows = db._exec(cur, "SELECT id FROM users WHERE name = ?", (user_name,)).fetchall()
            user_ids = [row["id"] for row in user_rows]
            if not user_ids:
                return {"logs": [], "has_more": False, "next_cursor": None}
            clauses.append(f"ol.user_id IN ({','.join('?' for _ in user_ids)})")
            params.extend(user_ids)
        rows = db._exec(cur, 
            f"""
            SELECT ol.id, u.name AS user_name, ol.operation_type, ol.description,
                   ol.module_code, ol.entity_type, ol.entity_id, ol.created_at
            FROM operation_logs ol
            LEFT JOIN users u ON u.id = ol.user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY ol.created_at DESC, ol.id DESC
            LIMIT ?
            """,
            params + [limit + 1],
        ).fetchall()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more and page_rows:
        last_row = page_rows[-1]
        next_cursor = encode_operation_log_cursor(last_row["created_at"], last_row["id"])
    return {
        "logs": [row_to_dict(row) for row in page_rows],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def get_operation_log_archive_storage():
    try:
        return operation_log_archive.SupabaseArchiveStorage.from_env()
    except operation_log_archive.ArchiveConfigError as exc:
        raise HTTPException(status_code=503, detail="归档存储未配置") from exc


@app.get("/api/operation-log-archives")
def list_operation_log_archives(user=Depends(current_user)):
    _require_admin(user)
    with db.connect() as conn:
        rows = db._exec(
            conn.cursor(),
            """
            SELECT id, period_start, period_end, row_count, first_created_at,
                   last_created_at, compressed_bytes, created_at, restored_at
            FROM operation_log_archives
            ORDER BY period_start DESC, id DESC
            """,
        ).fetchall()
    return {"archives": [row_to_dict(row) for row in rows]}


@app.get("/api/operation-log-archives/{archive_id}/download")
def download_operation_log_archive(archive_id: int, user=Depends(current_user)):
    _require_admin(user)
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT id, period_start, object_path FROM operation_log_archives WHERE id = ?",
            (archive_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="归档记录不存在")
    storage = get_operation_log_archive_storage()
    filename = f"operation-logs-{row['period_start'][:7]}.ndjson.gz"
    return StreamingResponse(
        storage.iter_download(row["object_path"]),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
