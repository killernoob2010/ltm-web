from pathlib import Path
from datetime import date, datetime
import calendar
import csv
import io
from typing import List, Optional
import re
import statistics
import threading
import time

import requests

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .cache_service import (
    cache_counts,
    get_cached_data,
    get_all_prices_for_info_type,
    get_latest_cached_data,
    import_desktop_cache,
    save_calculated_data,
)
from .info_summary_backfill import BackfillRequest, run_all_info_summary_backfills
from .sgx_usdcnh import fetch_sgx_usdcnh_rate


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"

app = FastAPI(title="轻量化交易管理系统 Web", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


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
    reminder_users: str = ""


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
INNER_OUTER_MONTHS = ["05", "06", "07", "08", "09"]
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
            FROM sh_junneng_trades
            WHERE is_closed = 0
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
                UPDATE sh_junneng_trades
                SET current_price = ?, updated_at = CURRENT_TIMESTAMP
                WHERE contract_month = ? AND is_closed = 0
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
            SELECT id, direction, open_price, trade_quantity, open_fee, current_price
            FROM sh_junneng_trades
            WHERE is_closed = 0
            """
        ).fetchall()
        for row in open_rows:
            profit = calculate_sh_junneng_profit(
                row["direction"],
                row["open_price"],
                row["trade_quantity"],
                row["open_fee"] or 0,
                current_price=row["current_price"],
            )
            db._exec(cur, 
                "UPDATE sh_junneng_trades SET profit = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (profit, row["id"]),
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


def calculate_today_indicator(payload: InfoCalculateIn, mock: bool = False) -> dict:
    yy = two_digit_year(payload.year)
    month = payload.month.zfill(2)
    value = None
    contracts = {}

    if payload.info_type == "卷螺差":
        hc = fetch_sina_price("HC", f"{yy}{month}", mock)
        rb = fetch_sina_price("RB", f"{yy}{month}", mock)
        contracts = {"HC": f"HC{yy}{month}", "RB": f"RB{yy}{month}"}
        value = hc - rb if hc is not None and rb is not None else None
    elif payload.info_type == "螺矿比":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        rb = fetch_sina_price("RB", f"{yy}{rb_month}", mock)
        i = fetch_sina_price("I", f"{yy}{i_month}", mock)
        contracts = {"RB": f"RB{yy}{rb_month}", "I": f"I{yy}{i_month}"}
        value = rb / i if rb is not None and i else None
    elif payload.info_type == "煤矿比":
        jm = fetch_sina_price("JM", f"{yy}{month}", mock)
        i = fetch_sina_price("I", f"{yy}{month}", mock)
        contracts = {"JM": f"JM{yy}{month}", "I": f"I{yy}{month}"}
        value = 1.88 * jm / i if jm is not None and i else None
    elif payload.info_type == "盘面钢厂利润":
        rb_month = "10" if month == "09" else month
        i_month = "09" if month == "09" else month
        j_month = "09" if month == "09" else month
        rb = fetch_sina_price("RB", f"{yy}{rb_month}", mock)
        i = fetch_sina_price("I", f"{yy}{i_month}", mock)
        j = fetch_sina_price("J", f"{yy}{j_month}", mock)
        contracts = {"RB": f"RB{yy}{rb_month}", "I": f"I{yy}{i_month}", "J": f"J{yy}{j_month}"}
        value = (rb - 1.6 * i - 0.45 * j - 375) / 1.13 - 1035 if rb is not None and i is not None and j is not None else None
    elif payload.info_type in MONTH_DIFF_TYPES:
        variety = "FE" if payload.info_type == "掉期月差" else "I"
        year1 = two_digit_year(payload.year1 or payload.year)
        year2 = two_digit_year(payload.year2 or payload.year)
        month1 = (payload.month1 or "09").zfill(2)
        month2 = (payload.month2 or "01").zfill(2)
        p1 = fetch_sina_price(variety, f"{year1}{month1}", mock)
        p2 = fetch_sina_price(variety, f"{year2}{month2}", mock)
        contracts = {f"{variety}1": f"{variety}{year1}{month1}", f"{variety}2": f"{variety}{year2}{month2}"}
        value = p1 - p2 if p1 is not None and p2 is not None else None
    elif payload.info_type == "内外盘差":
        i = fetch_sina_price("I", f"{yy}{month}", mock)
        fe = fetch_sina_price("FE", f"{yy}{month}", mock)
        fx = fetch_usdcnh_rate(f"{yy}{month}", mock)
        contracts = {"I": f"I{yy}{month}", "FE": f"FE{yy}{month}", "USD/CNH": f"USD/CNH{yy}{month}"}
        value = (fx * 1.13 * fe - i) + 30 if i is not None and fe is not None and fx is not None else None
    elif payload.info_type == "内外盘差2":
        i = fetch_sina_price("I", f"{yy}{month}", mock)
        fe = fetch_sina_price("FE", f"{yy}{month}", mock)
        fx = fetch_usdcnh_rate(f"{yy}{month}", mock)
        contracts = {"I": f"I{yy}{month}", "FE": f"FE{yy}{month}", "USD/CNH": f"USD/CNH{yy}{month}"}
        value = round((i / fx * 0.88 - fe), 2) if i is not None and fe is not None and fx else None

    return {
        "info_type": payload.info_type,
        "today_value": value,
        "contracts": contracts,
        "calc_date": payload.calc_date,
    }


def calculate_inner_outer_months(payload: InfoCalculateIn, mock: bool = False) -> dict:
    month_values = {}
    contracts = {}
    for month in INNER_OUTER_MONTHS:
        monthly_payload = payload.copy(update={"month": month})
        result = calculate_today_indicator(monthly_payload, mock=mock)
        month_values[month] = result["today_value"]
        contracts[month] = result["contracts"]
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


def scan_risk_alerts_once(mock: bool = False) -> dict:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT *
            FROM alert_settings
            WHERE status = 'enabled'
            ORDER BY id
            """
        ).fetchall()

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
    return str(payload.year)


def response_from_cache(payload: InfoCalculateIn, cached: Optional[dict], realtime: dict) -> dict:
    return {
        "info_type": payload.info_type,
        "calc_date": payload.calc_date,
        "cache_hit": cached is not None and cached.get("t_1_value") is not None,
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
    if payload.info_type == "卷螺差":
        return ["HC0", "RB0"]
    if payload.info_type == "螺矿比":
        return ["RB0", "I0"]
    if payload.info_type == "煤矿比":
        return ["JM0", "I0"]
    if payload.info_type == "盘面钢厂利润":
        return ["RB0", "I0", "J0"]
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
    if payload.calc_date not in dates:
        return None

    calc_index = dates.index(payload.calc_date)
    if calc_index < 1:
        return None

    def value_for_date(day: str) -> Optional[float]:
        prices = [price_data[code].get(day) for code in contract_codes]
        if any(price is None for price in prices):
            return None
        return value_from_cached_prices(payload.info_type, prices)

    t_1_value = value_for_date(dates[calc_index - 1])
    t_2_value = value_for_date(dates[calc_index - 2]) if calc_index >= 2 else None
    window_dates = dates[max(0, calc_index - 180):calc_index]
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
    db.init_db()
    start_alert_monitor()


def current_user(authorization: Optional[str] = Header(default=None)):
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_edit(module_code: str, user):
    if user["role"] == "管理员":
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


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    with db.connect() as conn:
        cur = conn.cursor()
        user = db._exec(cur, 
            """
            SELECT * FROM users
            WHERE name = ? AND password_hash = ? AND status = '启用'
            """,
            (payload.username, db.password_hash(payload.password)),
        ).fetchone()
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = db.create_session(user["id"])
    db.log_operation(user["id"], "auth", "登录", f"{payload.username} 登录系统")
    return {
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "department": user["department"],
            "role": user["role"],
        },
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
    return {
        "id": user["id"],
        "name": user["name"],
        "department": user["department"],
        "role": user["role"],
    }


@app.get("/api/auth/modules")
def modules(user=Depends(current_user)):
    if user["role"] == "管理员":
        visible = {
            code: {"can_view": True, "can_edit": True}
            for _, code, _ in db.MODULES
        }
    else:
        with db.connect() as conn:
            cur = conn.cursor()
            rows = db._exec(cur, 
                """
                SELECT module_code, can_view, can_edit
                FROM module_permissions
                WHERE user_id = ? AND can_view = 1
                """,
                (user["id"],),
            ).fetchall()
        visible = {
            row["module_code"]: {
                "can_view": bool(row["can_view"]),
                "can_edit": bool(row["can_edit"]),
            }
            for row in rows
        }

    groups = {}
    for group, code, name in db.MODULES:
        if code in visible:
            groups.setdefault(group, []).append(
                {"code": code, "name": name, **visible[code]}
            )
    return [{"group": group, "items": items} for group, items in groups.items()]


@app.get("/api/risk-alert/settings")
def list_alert_settings(user=Depends(current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            "SELECT * FROM alert_settings ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.post("/api/risk-alert/settings")
def create_alert_setting(payload: AlertSettingIn, user=Depends(current_user)):
    require_edit("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            """
            INSERT INTO alert_settings
                (info_type, contract_year, contract_month, alert_value, direction, status, creator, reminder_users)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.info_type,
                payload.contract_year,
                payload.contract_month,
                payload.alert_value,
                payload.direction,
                payload.status,
                user["name"],
                payload.reminder_users,
            ),
        )
        alert_id = db.last_insert_id(conn)
    db.log_operation(user["id"], "risk_alert", "新增预警", "新增风险预警规则", "alert_settings", alert_id)
    return {"id": alert_id}


@app.put("/api/risk-alert/settings/{alert_id}")
def update_alert_setting(alert_id: int, payload: AlertSettingIn, user=Depends(current_user)):
    require_edit("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            """
            UPDATE alert_settings
            SET info_type = ?, contract_year = ?, contract_month = ?,
                alert_value = ?, direction = ?, status = ?, reminder_users = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload.info_type,
                payload.contract_year,
                payload.contract_month,
                payload.alert_value,
                payload.direction,
                payload.status,
                payload.reminder_users,
                alert_id,
            ),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="预警规则不存在")
    db.log_operation(user["id"], "risk_alert", "编辑预警", "编辑风险预警规则", "alert_settings", alert_id)
    return {"ok": True}


@app.post("/api/risk-alert/settings/{alert_id}/toggle")
def toggle_alert_setting(alert_id: int, user=Depends(current_user)):
    require_edit("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT status FROM alert_settings WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="预警规则不存在")
        next_status = "disabled" if row["status"] == "enabled" else "enabled"
        db._exec(cur, 
            "UPDATE alert_settings SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (next_status, alert_id),
        )
    db.log_operation(user["id"], "risk_alert", "切换预警状态", f"预警状态改为 {next_status}", "alert_settings", alert_id)
    return {"status": next_status}


@app.delete("/api/risk-alert/settings/{alert_id}")
def delete_alert_setting(alert_id: int, user=Depends(current_user)):
    require_edit("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT id FROM alert_settings WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="预警规则不存在")
        db._exec(cur, "DELETE FROM alert_history WHERE alert_id = ?", (alert_id,))
        db._exec(cur, "DELETE FROM alert_settings WHERE id = ?", (alert_id,))
    db.log_operation(user["id"], "risk_alert", "删除预警", "删除风险预警规则", "alert_settings", alert_id)
    return {"ok": True}


@app.get("/api/risk-alert/history")
def list_alert_history(user=Depends(current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT h.*, s.info_type, s.contract_year, s.contract_month
            FROM alert_history h
            LEFT JOIN alert_settings s ON s.id = h.alert_id
            ORDER BY h.alert_time DESC, h.id DESC
            LIMIT 200
            """
        ).fetchall()
    return [row_to_dict(row) for row in rows]


@app.get("/api/risk-alert/notifications")
def list_alert_notifications(user=Depends(current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT h.*, s.info_type, s.contract_year, s.contract_month, s.reminder_users
            FROM alert_history h
            LEFT JOIN alert_settings s ON s.id = h.alert_id
            WHERE h.status = 'unread'
            ORDER BY h.alert_time DESC, h.id DESC
            LIMIT 20
            """
        ).fetchall()
    result = []
    for row in rows:
        reminder_users = row["reminder_users"] or ""
        allowed_users = [item.strip() for item in reminder_users.split(",") if item.strip()]
        if allowed_users and user["name"] not in allowed_users:
            continue
        result.append(row_to_dict(row))
    return {"count": len(result), "items": result}


@app.post("/api/risk-alert/history/{history_id}/read")
def mark_alert_history_read(history_id: int, user=Depends(current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            "UPDATE alert_history SET status = 'read' WHERE id = ?",
            (history_id,),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="预警历史不存在")
    return {"ok": True}


@app.post("/api/risk-alert/history/read-all")
def mark_all_alert_history_read(user=Depends(current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(cur, "UPDATE alert_history SET status = 'read' WHERE status = 'unread'")
    return {"ok": True}


@app.post("/api/risk-alert/scan")
def scan_risk_alerts(user=Depends(current_user)):
    require_edit("risk_alert", user)
    result = scan_risk_alerts_once()
    db.log_operation(user["id"], "risk_alert", "扫描预警", f"检查 {result['checked']} 条，触发 {result['triggered']} 条")
    return result


@app.post("/api/risk-alert/settings/{alert_id}/simulate-trigger")
def simulate_alert_trigger(alert_id: int, current_value: float, user=Depends(current_user)):
    require_edit("risk_alert", user)
    with db.connect() as conn:
        cur = conn.cursor()
        setting = db._exec(cur, "SELECT * FROM alert_settings WHERE id = ?", (alert_id,)).fetchone()
        if not setting:
            raise HTTPException(status_code=404, detail="预警规则不存在")
        db._exec(cur, 
            """
            INSERT INTO alert_history (alert_id, current_value, alert_value, direction)
            VALUES (?, ?, ?, ?)
            """,
            (alert_id, current_value, setting["alert_value"], setting["direction"]),
        )
    db.log_operation(user["id"], "risk_alert", "模拟触发", "手动写入预警历史", "alert_settings", alert_id)
    return {"ok": True}


@app.get("/api/info-summary/indicators")
def list_indicators(user=Depends(current_user)):
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
    defaults = default_info_contracts()
    return {
        "info_types": INFO_TYPES,
        "default_year": defaults["default_year"],
        "default_month": defaults["default_month"],
        "yuecha_defaults": defaults["yuecha_defaults"],
        "contract_months": [str(i).zfill(2) for i in range(1, 13)],
        "special_months": ["01", "05", "09"],
        "month_options_by_type": SPECIAL_MONTH_OPTIONS,
        "inner_months": INNER_OUTER_MONTHS,
        "cache_counts": cache_counts(),
    }


@app.post("/api/info-summary/calculate")
def calculate_info_summary(payload: InfoCalculateIn, mock: bool = False, user=Depends(current_user)):
    require_edit("info_summary", user)
    if payload.info_type in ["内外盘差", "内外盘差2"]:
        realtime = calculate_inner_outer_months(payload, mock=mock)
        month_results = {}
        for month in INNER_OUTER_MONTHS:
            cached = get_cached_data(payload.info_type, payload.year, month, payload.calc_date)
            month_results[month] = {
                **(cached or {}),
                "cache_hit": cached is not None and cached.get("t_1_value") is not None,
                "today_value": realtime["month_values"].get(month),
                "contracts": realtime["contracts"].get(month, {}),
            }
        db.log_operation(user["id"], "info_summary", "计算指标", payload.info_type, "calculated_data", None)
        return {
            "info_type": payload.info_type,
            "calc_date": payload.calc_date,
            "month_values": realtime["month_values"],
            "month_results": month_results,
            "contracts": realtime["contracts"],
            "cache_hit": any(item["cache_hit"] for item in month_results.values()),
        }

    realtime = calculate_today_indicator(payload, mock=mock)
    month_key = cache_month_key(payload)
    cached = get_cached_data(payload.info_type, payload.year, month_key, payload.calc_date)
    if not cached or cached.get("t_1_value") is None:
        cached = calculate_missing_cache_from_prices(payload) or get_latest_cached_data(
            payload.info_type,
            payload.year,
            month_key,
            payload.calc_date,
        ) or cached
    db.log_operation(user["id"], "info_summary", "计算指标", payload.info_type, "calculated_data", None)
    return response_from_cache(payload, cached, realtime)


@app.get("/api/info-summary/cache/counts")
def info_summary_cache_counts(user=Depends(current_user)):
    return cache_counts()


@app.get("/api/info-summary/cache/status")
def info_summary_cache_status(user=Depends(current_user)):
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
        "last_backfill": None,
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
    require_edit("info_summary", user)
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
    require_edit("info_summary", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, "DELETE FROM calculated_data WHERE id = ?", (indicator_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="指标记录不存在")
    db.log_operation(user["id"], "info_summary", "删除指标", "删除实时信息汇总记录", "calculated_data", indicator_id)
    return {"ok": True}


@app.get("/api/ledgers/sh-junneng/config")
def sh_junneng_config(user=Depends(current_user)):
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
    user=Depends(current_user),
):
    clauses = []
    params: list = []
    selected_date = selected_date or date.today().isoformat()

    if status in {"未平仓", "已平仓"}:
        if status == "未平仓":
            clauses.append("(is_closed = 0 OR is_closed = '未平仓')")
        else:
            clauses.append("(is_closed = 1 OR is_closed = '已平仓' OR status = '已结算')")
    if direction in {"long", "short", "多头", "空头", "多", "空"}:
        clauses.append("direction = ?")
        params.append(normalize_sh_junneng_direction(direction))
    if contract_month:
        normalized = normalize_sh_junneng_contract(contract_month)
        clauses.append("contract_month LIKE ?")
        params.append(f"%{normalized}%")
    if keyword:
        clauses.append("(contract_month LIKE ? OR open_date LIKE ? OR IFNULL(close_date, '') LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
    clauses.append("open_date <= ?")
    params.append(selected_date)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            f"""
            SELECT *
            FROM sh_junneng_trades
            {where_clause}
            ORDER BY open_date DESC, id DESC
            """,
            params,
        ).fetchall()
    snapshot = summarize_sh_junneng(rows)
    return {**snapshot, **sh_junneng_sections(rows, selected_date), "selected_date": selected_date}


@app.post("/api/ledgers/sh-junneng/trades")
def create_sh_junneng_trade(payload: ShJunnengTradeIn, user=Depends(current_user)):
    require_edit("sh_junneng", user)
    if payload.direction not in {"long", "short", "多头", "空头", "多", "空"}:
        raise HTTPException(status_code=400, detail="无效的交易方向")
    contract_month = normalize_sh_junneng_contract(payload.contract_month)
    direction = normalize_sh_junneng_direction(payload.direction)
    is_closed = payload.is_closed == "已平仓" or (payload.close_price is not None and payload.close_fee is not None and payload.close_date)
    current_price = payload.close_price if is_closed else payload.current_price
    if not is_closed and current_price is None:
        current_price = fetch_sh_junneng_current_price(contract_month)
    profit = calculate_sh_junneng_profit(
        direction,
        payload.open_price,
        payload.trade_quantity,
        payload.open_fee,
        close_price=payload.close_price if is_closed else None,
        close_fee=payload.close_fee or 0,
        current_price=current_price,
    )
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, 
            """
            INSERT INTO sh_junneng_trades
                (contract_month, direction, open_price, current_price, trade_quantity, hold_quantity,
                 open_fee, close_price, close_fee, profit, open_date, close_date, status, is_closed, created_by, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract_month,
                direction,
                payload.open_price,
                current_price,
                payload.trade_quantity,
                0 if is_closed else payload.trade_quantity,
                payload.open_fee,
                payload.close_price if is_closed else None,
                payload.close_fee if is_closed else 0,
                profit,
                payload.open_date,
                payload.close_date if is_closed else None,
                "已结算" if is_closed else "持仓",
                1 if is_closed else 0,
                user["name"],
                user["name"],
            ),
        )
        trade_id = db.last_insert_id(conn)
    db.log_operation(user["id"], "sh_junneng", "新增交易", f"新增上海均能交易 {contract_month}", "sh_junneng_trades", trade_id)
    return {"id": trade_id}


@app.put("/api/ledgers/sh-junneng/trades/{trade_id}")
def update_sh_junneng_trade(trade_id: int, payload: ShJunnengTradeIn, user=Depends(current_user)):
    require_edit("sh_junneng", user)
    if payload.direction not in {"long", "short", "多头", "空头", "多", "空"}:
        raise HTTPException(status_code=400, detail="无效的交易方向")
    contract_month = normalize_sh_junneng_contract(payload.contract_month)
    direction = normalize_sh_junneng_direction(payload.direction)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT * FROM sh_junneng_trades WHERE id = ?", (trade_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="交易记录不存在")
        is_closed = payload.is_closed == "已平仓" or (payload.close_price is not None and payload.close_fee is not None and payload.close_date)
        close_price = payload.close_price if is_closed else None
        close_fee = payload.close_fee or 0
        current_price = close_price if is_closed else payload.current_price
        if not is_closed and current_price is None:
            current_price = fetch_sh_junneng_current_price(contract_month)
        profit = calculate_sh_junneng_profit(
            direction,
            payload.open_price,
            payload.trade_quantity,
            payload.open_fee,
            close_price=close_price,
            close_fee=close_fee,
            current_price=current_price,
        )
        hold_quantity = 0 if is_closed else payload.trade_quantity
        db._exec(cur, 
            """
            UPDATE sh_junneng_trades
            SET contract_month = ?, direction = ?, open_price = ?, current_price = ?,
                trade_quantity = ?, hold_quantity = ?, open_fee = ?, close_price = ?,
                close_fee = ?, profit = ?, open_date = ?, close_date = ?, status = ?,
                is_closed = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                contract_month,
                direction,
                payload.open_price,
                current_price,
                payload.trade_quantity,
                hold_quantity,
                payload.open_fee,
                close_price,
                payload.close_fee if is_closed else 0,
                profit,
                payload.open_date,
                payload.close_date if is_closed else None,
                "已结算" if is_closed else "持仓",
                1 if is_closed else 0,
                user["name"],
                trade_id,
            ),
        )
    db.log_operation(user["id"], "sh_junneng", "编辑交易", f"编辑上海均能交易 {contract_month}", "sh_junneng_trades", trade_id)
    return {"ok": True}


@app.delete("/api/ledgers/sh-junneng/trades/{trade_id}")
def delete_sh_junneng_trade(trade_id: int, user=Depends(current_user)):
    require_edit("sh_junneng", user)
    with db.connect() as conn:
        cur = conn.cursor()
        cursor = db._exec(cur, "DELETE FROM sh_junneng_trades WHERE id = ?", (trade_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    db.log_operation(user["id"], "sh_junneng", "删除交易", "删除上海均能交易", "sh_junneng_trades", trade_id)
    return {"ok": True}


@app.post("/api/ledgers/sh-junneng/trades/{trade_id}/close")
def close_sh_junneng_trade(trade_id: int, payload: ShJunnengTradeCloseIn, user=Depends(current_user)):
    require_edit("sh_junneng", user)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT * FROM sh_junneng_trades WHERE id = ?", (trade_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="交易记录不存在")
        if existing["is_closed"]:
            raise HTTPException(status_code=400, detail="该交易已平仓")
        profit = calculate_sh_junneng_profit(
            existing["direction"],
            existing["open_price"],
            existing["trade_quantity"],
            existing["open_fee"] or 0,
            close_price=payload.close_price,
            close_fee=payload.close_fee,
        )
        db._exec(cur, 
            """
            UPDATE sh_junneng_trades
            SET close_price = ?, close_fee = ?, close_date = ?, current_price = ?,
                hold_quantity = 0, profit = ?, status = ?, is_closed = 1,
                updated_by = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                payload.close_price,
                payload.close_fee,
                payload.close_date,
                payload.close_price,
                profit,
                "已结算",
                user["name"],
                trade_id,
            ),
        )
    db.log_operation(user["id"], "sh_junneng", "平仓", f"上海均能交易平仓 {trade_id}", "sh_junneng_trades", trade_id)
    return {"ok": True}


@app.post("/api/ledgers/sh-junneng/prices/refresh")
def refresh_sh_junneng_prices(mock: bool = False, user=Depends(current_user)):
    require_edit("sh_junneng", user)
    result = refresh_sh_junneng_trade_prices(mock=mock)
    db.log_operation(user["id"], "sh_junneng", "刷新价格", f"刷新 {result['refreshed_contracts']} 个上海均能合约价格")
    return result


@app.post("/api/ledgers/sh-junneng/prices/manual")
def update_sh_junneng_prices_manually(payload: ShJunnengManualPricesIn, user=Depends(current_user)):
    require_edit("sh_junneng", user)
    updated = 0
    with db.connect() as conn:
        cur = conn.cursor()
        for contract_month, price in payload.prices.items():
            normalized = normalize_sh_junneng_contract(contract_month)
            rows = db._exec(cur, 
                """
                SELECT id, direction, open_price, trade_quantity, open_fee, close_fee
                FROM sh_junneng_trades
                WHERE contract_month = ? AND is_closed = 0
                """,
                (normalized,),
            ).fetchall()
            for row in rows:
                profit = calculate_sh_junneng_profit(
                    row["direction"],
                    row["open_price"],
                    row["trade_quantity"],
                    row["open_fee"] or 0,
                    close_fee=row["close_fee"] or 0,
                    current_price=price,
                )
                db._exec(cur, 
                    """
                    UPDATE sh_junneng_trades
                    SET current_price = ?, profit = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (price, profit, user["name"], row["id"]),
                )
                updated += 1
    db.log_operation(user["id"], "sh_junneng", "手动更新价格", f"更新 {updated} 条上海均能价格")
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
    clauses = ["is_closed = 1", "close_date IS NOT NULL", "close_price > 0", "close_fee > 0"]
    params: list = []
    if open_date_from:
        clauses.append("open_date >= ?")
        params.append(open_date_from)
    if open_date_to:
        clauses.append("open_date <= ?")
        params.append(open_date_to)
    if close_date_from:
        clauses.append("close_date >= ?")
        params.append(close_date_from)
    if close_date_to:
        clauses.append("close_date <= ?")
        params.append(close_date_to)
    selected_contracts = [item.strip().upper() for item in (contracts or "").split(",") if item.strip()]
    if selected_contracts:
        clauses.append(f"contract_month IN ({','.join(['?'] * len(selected_contracts))})")
        params.extend(selected_contracts)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            f"""
            SELECT *
            FROM sh_junneng_trades
            WHERE {' AND '.join(clauses)}
            ORDER BY close_date DESC, id DESC
            """,
            params,
        ).fetchall()
        contract_rows = db._exec(cur, 
            """
            SELECT DISTINCT contract_month
            FROM sh_junneng_trades
            WHERE contract_month != ''
            ORDER BY contract_month
            """
        ).fetchall()
    trades = [with_sh_junneng_fund_fields(sh_junneng_trade_snapshot(row)) for row in rows]
    return {
        "trades": trades,
        "totals": summarize_sh_junneng_table(trades),
        "contracts": [row["contract_month"] for row in contract_rows],
    }


@app.get("/api/ledgers/sh-junneng/export")
def export_sh_junneng_trades(selected_date: Optional[str] = None, user=Depends(current_user)):
    selected_date = selected_date or date.today().isoformat()
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            """
            SELECT *
            FROM sh_junneng_trades
            WHERE open_date <= ?
            ORDER BY open_date DESC, id DESC
            """,
            (selected_date,),
        ).fetchall()
    sections = sh_junneng_sections(rows, selected_date)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["类别", "合约月份", "交易方向", "开仓均价", "平仓均价", "当日收盘价", "交易数量(吨)", "持仓数量(吨)", "手续费(开)", "手续费(平)", "盈亏(含手续费)", "开仓日期", "平仓日期", "是否平仓", "资金利息", "利润按比例结算金额(80%)", "利润按比例结算金额(20%)"])
    for title, key in [("今日交易", "today_trades"), ("即期持仓", "current_trades"), ("已平仓数据", "settled_trades")]:
        for item in sections[key]:
            writer.writerow([
                title,
                item["contract_month"],
                item["direction_label"],
                item["open_price"],
                item["display_close_price"],
                item["current_price"],
                item["trade_quantity"],
                item["hold_quantity"],
                item["open_fee"],
                item["display_close_fee"],
                item["profit"],
                item["open_date"],
                item["display_close_date"],
                item["is_closed_label"],
                item.get("interest", ""),
                item.get("profit_80", ""),
                item.get("profit_20", ""),
            ])
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=sh_junneng_trades_{selected_date}.csv"},
    )


@app.get("/api/mid-event/groups")
def list_strategy_groups(user=Depends(current_user)):
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, "SELECT * FROM strategy_groups ORDER BY updated_at DESC, id DESC").fetchall()
    groups = []
    for row in rows:
        item = row_to_dict(row)
        item["total_pnl"] = group_pnl(item["id"])
        groups.append(item)
    return {"groups": groups, "all_total_pnl": all_groups_total_pnl(), "prices": REALTIME_PRICES}


@app.get("/api/mid-event/config")
def mid_event_config(user=Depends(current_user)):
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
            "INSERT INTO strategy_groups (group_name, creator) VALUES (?, ?)",
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
    require_edit("mid_event_monitor", user)
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
    require_edit("mid_event_monitor", user)
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
    department: str = Field(min_length=1)
    password: str = Field(default="", min_length=0)
    role: str = "用户"


class PermissionsBatchIn(BaseModel):
    permissions: list[dict[str, object]]


@app.get("/api/users")
def list_users(user=Depends(current_user)):
    require_edit("user_management", user)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            "SELECT id, name, department, role, status, created_at FROM users ORDER BY id"
        ).fetchall()
    return {"users": [row_to_dict(row) for row in rows]}


@app.post("/api/users")
def create_user(payload: UserIn, user=Depends(current_user)):
    require_edit("user_management", user)
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="密码长度至少为6位")
    hashed = db.password_hash(payload.password)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id FROM users WHERE name = ?", (payload.name,)).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="用户名已存在")
        cursor = db._exec(cur, 
            "INSERT INTO users (name, department, password_hash, role) VALUES (?, ?, ?, ?)",
            (payload.name, payload.department, hashed, payload.role),
        )
        new_id = db.last_insert_id(conn)
        for _, module_code, _ in db.MODULES:
            db._exec(cur, 
                "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit) VALUES (?, ?, 1, ?)",
                (new_id, module_code, 1 if payload.role == "管理员" else 0),
            )
    db.log_operation(user["id"], "user_management", "添加用户", f"添加用户: {payload.name}", "users", new_id)
    return {"id": new_id}


@app.put("/api/users/{user_id}")
def update_user(user_id: int, payload: UserIn, user=Depends(current_user)):
    require_edit("user_management", user)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="用户不存在")
        if payload.name != existing["name"]:
            dup = db._exec(cur, "SELECT id FROM users WHERE name = ? AND id != ?", (payload.name, user_id)).fetchone()
            if dup:
                raise HTTPException(status_code=400, detail="用户名已存在")
        if payload.password:
            if len(payload.password) < 6:
                raise HTTPException(status_code=400, detail="密码长度至少为6位")
            hashed = db.password_hash(payload.password)
            db._exec(cur, 
                "UPDATE users SET name = ?, department = ?, password_hash = ?, role = ? WHERE id = ?",
                (payload.name, payload.department, hashed, payload.role, user_id),
            )
        else:
            db._exec(cur, 
                "UPDATE users SET name = ?, department = ?, role = ? WHERE id = ?",
                (payload.name, payload.department, payload.role, user_id),
            )
        if payload.role == "管理员":
            db._exec(cur, 
                "UPDATE module_permissions SET can_view = 1, can_edit = 1 WHERE user_id = ?",
                (user_id,),
            )
            for _, module_code, _ in db.MODULES:
                db._exec(cur, 
                    "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit) VALUES (?, ?, 1, 1)",
                    (user_id, module_code),
                )
        else:
            db._exec(cur, 
                "UPDATE module_permissions SET can_view = 1, can_edit = 0 WHERE user_id = ?",
                (user_id,),
            )
    db.log_operation(user["id"], "user_management", "编辑用户", f"编辑用户: {payload.name}", "users", user_id)
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, user=Depends(current_user)):
    require_edit("user_management", user)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="不能删除自己")
    with db.connect() as conn:
        cur = conn.cursor()
        target = db._exec(cur, "SELECT name FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="用户不存在")
        db._exec(cur, "DELETE FROM module_permissions WHERE user_id = ?", (user_id,))
        db._exec(cur, "DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        db._exec(cur, "DELETE FROM users WHERE id = ?", (user_id,))
    db.log_operation(user["id"], "user_management", "删除用户", f"删除用户: {target['name']}", "users", user_id)
    return {"ok": True}


@app.get("/api/users/{user_id}/permissions")
def get_user_permissions(user_id: int, user=Depends(current_user)):
    require_edit("user_management", user)
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="用户不存在")
        for _, module_code, _ in db.MODULES:
            db._exec(cur, 
                "INSERT OR IGNORE INTO module_permissions (user_id, module_code, can_view, can_edit) VALUES (?, ?, 1, 0)",
                (user_id, module_code),
            )
        rows = db._exec(cur, 
            "SELECT module_code, can_view, can_edit FROM module_permissions WHERE user_id = ? ORDER BY module_code",
            (user_id,),
        ).fetchall()
    return {"permissions": {row["module_code"]: {"can_view": row["can_view"], "can_edit": row["can_edit"]} for row in rows}}


@app.put("/api/users/{user_id}/permissions")
def set_user_permissions(user_id: int, payload: PermissionsBatchIn, user=Depends(current_user)):
    require_edit("user_management", user)
    target_name = ""
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(cur, "SELECT id, name FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="用户不存在")
        target_name = existing["name"]
        for perm in payload.permissions:
            db._exec(cur, 
                "UPDATE module_permissions SET can_view = ?, can_edit = ? WHERE user_id = ? AND module_code = ?",
                (int(perm["can_view"]), int(perm["can_edit"]), user_id, perm["module_code"]),
            )
    db.log_operation(user["id"], "user_management", "设置权限", f"设置用户权限: {target_name}", "module_permissions", user_id)
    return {"ok": True}


@app.get("/api/operation-logs")
def list_operation_logs(
    operation_type: Optional[str] = None,
    user_name: Optional[str] = None,
    limit: int = 200,
    user=Depends(current_user),
):
    require_edit("user_management", user)
    clauses = ["1=1"]
    params: list = []
    if operation_type:
        clauses.append("ol.operation_type = ?")
        params.append(operation_type)
    if user_name:
        clauses.append("u.name = ?")
        params.append(user_name)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, 
            f"""
            SELECT ol.id, u.name AS user_name, ol.operation_type, ol.description,
                   ol.module_code, ol.entity_type, ol.entity_id, ol.created_at
            FROM operation_logs ol
            LEFT JOIN users u ON u.id = ol.user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY ol.created_at DESC
            LIMIT ?
            """,
            params + [limit],
        ).fetchall()
    return {"logs": [row_to_dict(row) for row in rows]}
