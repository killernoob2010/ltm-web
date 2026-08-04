from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
import threading
import time
import uuid
from typing import Any, Iterable, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import db, option_research, trading_valuation


router = APIRouter()

DAILY_SECONDS = 24 * 60 * 60
MAX_RUNS = 20
RUN_TYPE = "backtest_a0_daily"
BACKTEST_CODE_VERSION = "a0-daily-a9e622a"
STALE_RUN_MINUTES = max(5, int(os.getenv("OPTION_RESEARCH_BACKTEST_STALE_MINUTES", "5")))
MAX_RUN_SECONDS = max(60, int(os.getenv("OPTION_RESEARCH_BACKTEST_MAX_SECONDS", "600")))
BACKTEST_SCHEMA = ("option_research_results",)
_RUN_LOCK = threading.Lock()
_RUN_THREAD: Optional[threading.Thread] = None


class BacktestStartIn(BaseModel):
    """Only bounded research options are exposed; no trading controls exist."""

    max_options: int = Field(default=0, ge=0, le=5000)
    max_futures: int = Field(default=0, ge=0, le=200)


@dataclass(frozen=True)
class Contract:
    symbol: str
    underlying_symbol: str
    option_class: str
    strike_price: float
    expire_datetime: str
    volume_multiple: float
    price_tick: float


@dataclass
class Position:
    symbol: str
    underlying_symbol: str
    option_class: str
    strike_price: float
    expiry: date
    quantity: int
    entry_price: float
    multiplier: float
    entry_date: str
    close_pending: bool = False
    profit_stage: int = 0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: Any, *, positive: bool = False) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    return number


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("T", " ")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized[:19], fmt).date()
        except ValueError:
            continue
    return None


def _datetime_text(value: Any) -> str:
    number = _finite(value)
    if number is not None and number > 10**15:
        return datetime.fromtimestamp(number / 1_000_000_000, timezone.utc).isoformat()
    if number is not None and number > 10**12:
        return datetime.fromtimestamp(number / 1_000_000, timezone.utc).isoformat()
    if number is not None and number > 10**9:
        return datetime.fromtimestamp(number, timezone.utc).isoformat()
    return str(value or "")


def _beijing_date(datetime_nano: int) -> str:
    return datetime.fromtimestamp(
        int(datetime_nano) / 1_000_000_000,
        timezone(timedelta(hours=8)),
    ).date().isoformat()


def _frame_value(frame: Any, name: str, index: int) -> Any:
    column = frame[name]
    try:
        return column.iloc[index]
    except AttributeError:
        return column[index]


def frame_rows(
    frame: Any,
    *,
    symbol: str,
    duration_seconds: int,
    run_key: str,
) -> list[dict[str, Any]]:
    """Convert a TqSdk KlineSeries into stable rows without retaining SDK objects."""

    if frame is None or "datetime" not in frame:
        return []
    try:
        length = len(frame["datetime"]) if isinstance(frame, dict) else len(frame)
    except (TypeError, KeyError):
        return []
    rows: list[dict[str, Any]] = []
    for index in range(length):
        try:
            datetime_nano = int(_frame_value(frame, "datetime", index))
        except (TypeError, ValueError, OverflowError):
            continue
        if datetime_nano <= 0:
            continue
        values: dict[str, Any] = {}
        for source, target in (
            ("open", "open_price"),
            ("high", "high_price"),
            ("low", "low_price"),
            ("close", "close_price"),
            ("settlement", "settlement_price"),
            ("volume", "volume"),
            ("open_oi", "open_interest"),
        ):
            try:
                value = _frame_value(frame, source, index)
            except (KeyError, IndexError, TypeError):
                value = None
            values[target] = _finite(value)
        if values["close_price"] is None:
            continue
        rows.append(
            {
                "symbol": symbol,
                "duration_seconds": duration_seconds,
                "datetime_nano": datetime_nano,
                "datetime_text": _datetime_text(datetime_nano),
                "trading_date": _beijing_date(datetime_nano),
                **values,
                "source": "tqsdk",
                "run_key": run_key,
            }
        )
    rows.sort(key=lambda row: row["datetime_nano"])
    return rows


def parse_expire_datetime(value: Any) -> Optional[str]:
    """Normalize TqSdk expire_datetime to an ISO string without guessing a date."""

    number = _finite(value)
    if number is not None:
        if number > 10**15:
            return datetime.fromtimestamp(number / 1_000_000_000, timezone.utc).isoformat()
        if number > 10**12:
            return datetime.fromtimestamp(number / 1_000_000, timezone.utc).isoformat()
        if number > 10**9:
            return datetime.fromtimestamp(number, timezone.utc).isoformat()
    text = str(value or "").strip()
    return text or None


def parse_contract_quote(symbol: str, quote: Any) -> Optional[Contract]:
    parsed = option_research.parse_dce_iron_ore_option_symbol(symbol)
    if not parsed:
        return None
    expiry_text = parse_expire_datetime(getattr(quote, "expire_datetime", None))
    expiry = _parse_date(expiry_text)
    multiplier = _finite(getattr(quote, "volume_multiple", None), positive=True)
    tick = _finite(getattr(quote, "price_tick", None), positive=True)
    if not expiry_text or expiry is None or multiplier is None or tick is None:
        return None
    underlying = str(
        getattr(quote, "underlying_symbol", None)
        or parsed["underlying_symbol"]
    )
    return Contract(
        symbol=str(symbol),
        underlying_symbol=underlying,
        option_class=str(parsed["option_class"]),
        strike_price=float(parsed["strike_price"]),
        expire_datetime=expiry_text,
        volume_multiple=float(multiplier),
        price_tick=float(tick),
    )


def discover_contracts(
    api: Any,
    *,
    max_options: int = 0,
    max_futures: int = 0,
) -> tuple[list[str], list[Contract], bool]:
    """Discover concrete iron-ore futures and real option contracts."""

    expired = list(
        api.query_quotes(
            ins_class="FUTURE",
            exchange_id="DCE",
            product_id="i",
            expired=True,
        )
    )
    active = list(
        api.query_quotes(
            ins_class="FUTURE",
            exchange_id="DCE",
            product_id="i",
            expired=False,
        )
    )
    # Recent expired contracts provide the cleanest historical sample for a rolling
    # monthly strategy; active contracts follow for the current/next-month slice.
    expired_recent = sorted(
        {str(symbol) for symbol in expired if str(symbol).startswith("DCE.i")},
        reverse=True,
    )
    active_recent = sorted(
        {str(symbol) for symbol in active if str(symbol).startswith("DCE.i")},
        reverse=True,
    )
    futures = list(dict.fromkeys(expired_recent + active_recent))
    if max_futures:
        futures = futures[:max_futures]
    contracts: dict[str, Contract] = {}
    for underlying in futures:
        for raw_symbol in api.query_options(underlying):
            symbol = str(raw_symbol)
            if symbol in contracts:
                continue
            contract = parse_contract_quote(symbol, api.get_quote(symbol))
            if contract is None:
                continue
            contracts[symbol] = contract
    all_contracts = sorted(contracts.values(), key=lambda item: item.symbol)
    if not max_options or len(all_contracts) <= max_options:
        return futures, all_contracts, False

    # A small capped probe must still contain both sides. Taking the first
    # lexicographic symbols can silently produce calls-only or puts-only data.
    grouped = {
        "CALL": [item for item in all_contracts if item.option_class == "CALL"],
        "PUT": [item for item in all_contracts if item.option_class == "PUT"],
    }
    counts = {"CALL": max_options // 2, "PUT": max_options // 2}
    for option_class in ("CALL", "PUT"):
        if max_options % 2 and grouped[option_class]:
            counts[option_class] += 1
    selected: list[Contract] = []
    for option_class in ("CALL", "PUT"):
        candidates = grouped[option_class]
        take = min(counts[option_class], len(candidates))
        center = (len(candidates) - 1) / 2
        ranked = sorted(
            enumerate(candidates),
            key=lambda pair: abs(pair[0] - center),
        )
        selected.extend(
            sorted(
                [item for _, item in ranked[:take]],
                key=lambda item: item.symbol,
            )
        )
    if len(selected) < max_options:
        selected_symbols = {item.symbol for item in selected}
        selected.extend(
            item for item in all_contracts
            if item.symbol not in selected_symbols
        )
        selected = selected[:max_options]
    return futures, sorted(selected, key=lambda item: item.symbol), True


def create_schema() -> None:
    option_research.ensure_schema()
    id_column = "SERIAL PRIMARY KEY" if db._is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS option_research_results (
                id {id_column},
                run_key TEXT NOT NULL,
                result_type TEXT NOT NULL,
                result_key TEXT NOT NULL,
                metric_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_key, result_type, result_key)
            )
            """
        )
        if db._is_pg():
            db._secure_postgres_tables(cur, BACKTEST_SCHEMA)


def _create_run(run_key: str, max_options: int, max_futures: int) -> None:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO option_research_runs
            (run_key, run_type, status, source, started_at, requested_start,
             requested_end, duration_seconds, checks_json, message)
            VALUES (?, ?, 'running', 'tqsdk', ?, ?, ?, ?, '{}', ?)
            """,
            (
                run_key,
                RUN_TYPE,
                _utc_now(),
                "available_history",
                "available_history",
                DAILY_SECONDS,
                json.dumps(
                    {
                        "mode": "daily_a0_screen",
                        "max_options": max_options,
                        "max_futures": max_futures,
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def _update_run(
    run_key: str,
    *,
    status: str,
    checks: Optional[dict[str, Any]] = None,
    futures_count: int = 0,
    options_count: int = 0,
    bars_count: int = 0,
    gaps_count: int = 0,
    message: str = "",
    error_code: Optional[str] = None,
) -> None:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            UPDATE option_research_runs
               SET status = ?, finished_at = ?, futures_count = ?, options_count = ?,
                   bars_count = ?, gaps_count = ?, checks_json = ?, error_code = ?,
                   message = ?, updated_at = CURRENT_TIMESTAMP
             WHERE run_key = ?
            """,
            (
                status,
                _utc_now() if status != "running" else None,
                futures_count,
                options_count,
                bars_count,
                gaps_count,
                json.dumps(checks or {}, ensure_ascii=False, sort_keys=True),
                error_code,
                message,
                run_key,
            ),
        )


def _insert_contracts(contracts: Iterable[Contract]) -> None:
    rows = [
        (
            item.symbol,
            "DCE",
            "i",
            item.underlying_symbol,
            item.option_class,
            item.strike_price,
            item.expire_datetime,
            0,
            item.volume_multiple,
            item.price_tick,
            "tqsdk",
            _utc_now(),
            "{}",
        )
        for item in contracts
    ]
    if not rows:
        return
    with db.connect() as conn:
        cur = conn.cursor()
        db._executemany(
            cur,
            """
            INSERT INTO option_research_contracts
            (symbol, exchange_id, product_id, underlying_symbol, option_class,
             strike_price, expire_datetime, expired, volume_multiple, price_tick,
             source, discovered_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                exchange_id = excluded.exchange_id,
                product_id = excluded.product_id,
                underlying_symbol = excluded.underlying_symbol,
                option_class = excluded.option_class,
                strike_price = excluded.strike_price,
                expire_datetime = excluded.expire_datetime,
                expired = excluded.expired,
                volume_multiple = excluded.volume_multiple,
                price_tick = excluded.price_tick,
                source = excluded.source,
                discovered_at = excluded.discovered_at,
                metadata_json = excluded.metadata_json
            """,
            rows,
        )


def _insert_bars(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    values = [
        (
            row["symbol"],
            row["duration_seconds"],
            row["datetime_nano"],
            row["datetime_text"],
            row["trading_date"],
            row.get("open_price"),
            row.get("high_price"),
            row.get("low_price"),
            row.get("close_price"),
            row.get("settlement_price"),
            row.get("volume"),
            row.get("open_interest"),
            row.get("bid_price1"),
            row.get("ask_price1"),
            row.get("source", "tqsdk"),
            row["run_key"],
        )
        for row in rows
    ]
    with db.connect() as conn:
        cur = conn.cursor()
        for start in range(0, len(values), 500):
            db._executemany(
                cur,
                """
                INSERT INTO option_research_bars
                (symbol, duration_seconds, datetime_nano, datetime_text, trading_date,
                 open_price, high_price, low_price, close_price, settlement_price,
                 volume, open_interest, bid_price1, ask_price1, source, run_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, duration_seconds, datetime_nano) DO NOTHING
                """,
                values[start : start + 500],
            )


def _wait_for_rows(api: Any, frame: Any, timeout_seconds: int) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        rows = frame_rows(
            frame,
            symbol="__placeholder__",
            duration_seconds=DAILY_SECONDS,
            run_key="__placeholder__",
        )
        if rows:
            return rows
        api.wait_update(deadline=min(deadline, time.time() + 1))
    return frame_rows(
        frame,
        symbol="__placeholder__",
        duration_seconds=DAILY_SECONDS,
        run_key="__placeholder__",
    )


def fetch_daily_rows(api: Any, symbol: str, run_key: str) -> list[dict[str, Any]]:
    data_length = max(30, min(500, int(os.getenv("OPTION_RESEARCH_DAILY_BARS", "500"))))
    timeout = max(5, min(60, int(os.getenv("OPTION_RESEARCH_SYMBOL_TIMEOUT_SECONDS", "20"))))
    frame = api.get_kline_serial(symbol, DAILY_SECONDS, data_length=data_length)
    deadline = time.time() + timeout
    rows: list[dict[str, Any]] = []
    while time.time() < deadline and not rows:
        rows = frame_rows(
            frame,
            symbol=symbol,
            duration_seconds=DAILY_SECONDS,
            run_key=run_key,
        )
        if rows:
            return rows
        api.wait_update(deadline=min(deadline, time.time() + 1))
    return frame_rows(
        frame,
        symbol=symbol,
        duration_seconds=DAILY_SECONDS,
        run_key=run_key,
    )


def fetch_daily_rows_batch(
    api: Any,
    symbols: list[str],
    run_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read a bounded symbol batch through one wait loop.

    TqSdk updates all subscribed serials together. Waiting symbol-by-symbol makes
    a batch with unavailable history take one timeout per symbol, so use one
    bounded subscription batch and return both rows and symbols with no rows.
    """

    if not symbols:
        return [], []
    data_length = max(30, min(500, int(os.getenv("OPTION_RESEARCH_DAILY_BARS", "500"))))
    timeout = max(5, min(60, int(os.getenv("OPTION_RESEARCH_SYMBOL_TIMEOUT_SECONDS", "20"))))
    frames: dict[str, Any] = {}
    missing = set(symbols)
    for symbol in symbols:
        try:
            frames[symbol] = api.get_kline_serial(symbol, DAILY_SECONDS, data_length=data_length)
        except Exception:
            frames[symbol] = None

    rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    deadline = time.time() + timeout
    while time.time() < deadline and missing:
        for symbol in list(missing):
            rows = frame_rows(
                frames[symbol],
                symbol=symbol,
                duration_seconds=DAILY_SECONDS,
                run_key=run_key,
            )
            if rows:
                rows_by_symbol[symbol] = rows
                missing.remove(symbol)
        if not missing:
            break
        try:
            api.wait_update(deadline=min(deadline, time.time() + 1))
        except Exception:
            break

    for symbol in list(missing):
        rows = frame_rows(
            frames[symbol],
            symbol=symbol,
            duration_seconds=DAILY_SECONDS,
            run_key=run_key,
        )
        if rows:
            rows_by_symbol[symbol] = rows
            missing.remove(symbol)
    rows = [row for symbol in symbols for row in rows_by_symbol.get(symbol, [])]
    return rows, sorted(missing)


def _black76_metrics(
    *,
    option_price: float,
    underlying_price: float,
    strike_price: float,
    expiry: date,
    as_of: date,
    option_class: str,
) -> dict[str, Optional[float]]:
    days = (expiry - as_of).days
    if days <= 0:
        return {}
    return trading_valuation.calculate_black76_option_metrics(
        option_price=option_price,
        underlying_price=underlying_price,
        strike_price=strike_price,
        risk_free_rate=float(os.getenv("OPTION_RESEARCH_RISK_FREE_RATE", "0.02")),
        time_to_expiry=days / 360,
        option_class=option_class,
    )


def _row_map(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row["symbol"]), str(row["trading_date"])): row
        for row in rows
        if row.get("close_price") is not None
    }


def _next_date_map(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], str]:
    dates_by_symbol: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        symbol = str(row["symbol"])
        trading_date = str(row["trading_date"])
        if trading_date not in dates_by_symbol[symbol]:
            dates_by_symbol[symbol].append(trading_date)
    result: dict[tuple[str, str], str] = {}
    for symbol, dates in dates_by_symbol.items():
        dates.sort()
        for previous, current in zip(dates, dates[1:]):
            result[(symbol, previous)] = current
    return result


def _position_pnl(position: Position, mark: float) -> float:
    return (position.entry_price - mark) * position.quantity * position.multiplier


def _position_delta(position: Position, metrics: dict[str, Optional[float]]) -> float:
    delta = metrics.get("delta")
    if delta is None:
        return 0.0
    return -float(delta) * position.quantity


def simulate_daily_a0(
    *,
    contracts: list[Contract],
    bars: list[dict[str, Any]],
) -> dict[str, Any]:
    """Conservative daily screening model; not the final 5-minute execution model."""

    contract_map = {contract.symbol: contract for contract in contracts}
    option_rows = [row for row in bars if row["symbol"] in contract_map]
    future_rows = [row for row in bars if row["symbol"].startswith("DCE.i") and row["symbol"] not in contract_map]
    option_by_key = _row_map(option_rows)
    future_by_key = _row_map(future_rows)
    all_dates = sorted({row["trading_date"] for row in future_rows})
    next_option_date = _next_date_map(option_rows)
    positions: dict[str, Position] = {}
    pending_close: dict[tuple[str, str], int] = defaultdict(int)
    realized_by_month: dict[str, float] = defaultdict(float)
    daily: list[dict[str, Any]] = []
    total_entries = 0
    total_exits = 0
    risk_triggers = {"18w": 0, "24w": 0, "30w": 0}
    filter_counts = defaultdict(int)
    risk_latched_level = 0
    delta_unknown_days = 0
    monthly_activity: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "observed_days": 0,
            "days_with_positions": 0,
            "entry_quantity": 0,
            "exit_quantity": 0,
        }
    )
    entries_by_month: dict[str, int] = defaultdict(int)
    exits_by_month: dict[str, int] = defaultdict(int)
    quantity_sequence = [100, 200, 300, 400, 500, 600]

    def schedule_risk_close(trading_date: str, fraction: float) -> None:
        for symbol, position in positions.items():
            action_date = next_option_date.get((symbol, trading_date), trading_date)
            close_quantity = position.quantity if fraction >= 1 else max(
                1,
                math.ceil(position.quantity * fraction),
            )
            pending_close[(symbol, action_date)] = max(
                pending_close[(symbol, action_date)],
                close_quantity,
            )

    for index, trading_date in enumerate(all_dates):
        month_key = trading_date[:7]
        monthly_activity[month_key]["observed_days"] += 1
        # Execute only orders generated on the previous end-of-day observation.
        for (symbol, action_date), quantity in list(pending_close.items()):
            if action_date != trading_date:
                continue
            row = option_by_key.get((symbol, trading_date))
            position = positions.get(symbol)
            if row is None or position is None or position.quantity <= 0:
                pending_close.pop((symbol, action_date), None)
                continue
            fill = _finite(row.get("open_price"), positive=True) or _finite(row.get("close_price"), positive=True)
            if fill is None:
                continue
            close_quantity = min(position.quantity, quantity)
            realized_by_month[trading_date[:7]] += _position_pnl(position, fill) * close_quantity / position.quantity
            position.quantity -= close_quantity
            total_exits += close_quantity
            exits_by_month[trading_date[:7]] += close_quantity
            if position.quantity <= 0:
                positions.pop(symbol, None)
            pending_close.pop((symbol, action_date), None)

        floating = 0.0
        net_delta = 0.0
        missing_metrics = 0
        for symbol, position in list(positions.items()):
            row = option_by_key.get((symbol, trading_date))
            underlying_row = future_by_key.get((position.underlying_symbol, trading_date))
            if row is None or underlying_row is None:
                continue
            mark = _finite(row.get("close_price"), positive=True)
            underlying = _finite(underlying_row.get("close_price"), positive=True)
            if mark is None or underlying is None:
                continue
            floating += _position_pnl(position, mark)
            metrics = _black76_metrics(
                option_price=mark,
                underlying_price=underlying,
                strike_price=position.strike_price,
                expiry=position.expiry,
                as_of=_parse_date(trading_date) or date.min,
                option_class=position.option_class,
            )
            if not metrics:
                missing_metrics += 1
            net_delta += _position_delta(position, metrics)
            action_date = next_option_date.get((symbol, trading_date), trading_date)
            as_of = _parse_date(trading_date)
            close_quantity = 0
            if as_of is not None and position.expiry <= as_of:
                close_quantity = position.quantity
                position.profit_stage = 3
            elif mark <= 0.3 and position.profit_stage < 3:
                close_quantity = position.quantity
                position.profit_stage = 3
            elif mark <= 0.4 and position.profit_stage < 2:
                close_quantity = max(1, math.ceil(position.quantity / 3))
                position.profit_stage = 2
            elif mark <= 0.5 and position.profit_stage < 1:
                close_quantity = max(1, math.ceil(position.quantity / 3))
                position.profit_stage = 1
            if close_quantity:
                pending_close[(symbol, action_date)] = max(
                    pending_close[(symbol, action_date)],
                    close_quantity,
                )

        if missing_metrics:
            delta_unknown_days += 1
        if floating <= -180_000:
            risk_triggers["18w"] += 1
        if floating <= -240_000:
            risk_triggers["24w"] += 1
        if floating <= -300_000:
            risk_triggers["30w"] += 1
        if floating <= -300_000 and risk_latched_level < 3:
            schedule_risk_close(trading_date, 1.0)
            risk_latched_level = 3
        elif floating <= -240_000 and risk_latched_level < 2:
            schedule_risk_close(trading_date, 0.5)
            risk_latched_level = 2
        elif floating <= -180_000 and risk_latched_level < 1:
            schedule_risk_close(trading_date, 1 / 3)
            risk_latched_level = 1

        # Daily screening entries use only the current close and fill next date.
        if index + 1 < len(all_dates) and floating > -180_000 and risk_latched_level == 0:
            next_trading_date = all_dates[index + 1]
            active_contracts: list[
                tuple[Contract, dict[str, Any], dict[str, Any], dict[str, Optional[float]]]
            ] = []
            for contract in contracts:
                option_row = option_by_key.get((contract.symbol, trading_date))
                next_option_row = option_by_key.get((contract.symbol, next_trading_date))
                underlying_row = future_by_key.get((contract.underlying_symbol, trading_date))
                if option_row is None:
                    filter_counts["missing_option_row"] += 1
                    continue
                if next_option_row is None:
                    filter_counts["missing_next_option_row"] += 1
                    continue
                if underlying_row is None:
                    filter_counts["missing_underlying_row"] += 1
                    continue
                expiry = _parse_date(contract.expire_datetime)
                as_of = _parse_date(trading_date)
                premium = _finite(option_row.get("close_price"), positive=True)
                underlying = _finite(underlying_row.get("close_price"), positive=True)
                if expiry is None or as_of is None or premium is None or underlying is None:
                    filter_counts["missing_price_or_expiry"] += 1
                    continue
                if (expiry - as_of).days <= 10 or premium < 1:
                    filter_counts["expiry_or_premium_filter"] += 1
                    continue
                distance = (
                    contract.strike_price - underlying
                    if contract.option_class == "CALL"
                    else underlying - contract.strike_price
                )
                if distance < 30:
                    filter_counts["distance_below_30"] += 1
                    continue
                metrics = _black76_metrics(
                    option_price=premium,
                    underlying_price=underlying,
                    strike_price=contract.strike_price,
                    expiry=expiry,
                    as_of=as_of,
                    option_class=contract.option_class,
                )
                if metrics.get("delta") is None:
                    filter_counts["missing_delta"] += 1
                    continue
                filter_counts["eligible_candidate"] += 1
                active_contracts.append((contract, option_row, next_option_row, metrics))
            for option_class, side_sign in (("CALL", -1), ("PUT", 1)):
                side_candidates = sorted(
                    [item for item in active_contracts if item[0].option_class == option_class],
                    key=lambda item: abs(item[0].strike_price - (_finite(future_by_key.get((item[0].underlying_symbol, trading_date), {}).get("close_price")) or 0)),
                )
                used = sum(
                    position.quantity
                    for position in positions.values()
                    if position.option_class == option_class
                )
                tranche_index = 0
                for contract, option_row, next_option_row, metrics in side_candidates:
                    if used >= 2500:
                        break
                    if contract.symbol in positions:
                        continue
                    proposed = min(quantity_sequence[min(tranche_index, len(quantity_sequence) - 1)], 2500 - used)
                    fill = _finite(next_option_row.get("open_price"), positive=True) or _finite(next_option_row.get("close_price"), positive=True)
                    if fill is None:
                        continue
                    expiry = _parse_date(contract.expire_datetime)
                    if expiry is None:
                        continue
                    candidate_delta = -float(metrics["delta"]) * proposed
                    if abs(net_delta + candidate_delta) > 20:
                        filter_counts["delta_limit"] += 1
                        continue
                    positions[contract.symbol] = Position(
                        symbol=contract.symbol,
                        underlying_symbol=contract.underlying_symbol,
                        option_class=option_class,
                        strike_price=contract.strike_price,
                        expiry=expiry,
                        quantity=int(proposed),
                        entry_price=fill,
                        multiplier=contract.volume_multiple,
                        entry_date=next_trading_date,
                    )
                    used += int(proposed)
                    net_delta += candidate_delta
                    tranche_index += 1
                    total_entries += int(proposed)
                    entries_by_month[next_trading_date[:7]] += int(proposed)

        if positions:
            monthly_activity[month_key]["days_with_positions"] += 1
        daily.append(
            {
                "trading_date": trading_date,
                "floating_pnl": round(floating, 2),
                "net_delta": round(net_delta, 6),
                "open_positions": len(positions),
                "open_short_quantity": sum(position.quantity for position in positions.values()),
                "missing_metrics": missing_metrics,
            }
        )

    # Settle any remaining open position at the last available real option mark
    # so the monthly figure is a settled mark-to-market figure, not an omitted
    # residual position.
    if all_dates:
        for symbol, position in list(positions.items()):
            available_rows = [
                row
                for row in option_rows
                if row["symbol"] == symbol and _finite(row.get("close_price"), positive=True) is not None
            ]
            if not available_rows:
                continue
            row = max(available_rows, key=lambda item: str(item["trading_date"]))
            settlement_date = str(row["trading_date"])
            fill = _finite(row.get("close_price"), positive=True)
            if fill is None:
                continue
            realized_by_month[settlement_date[:7]] += _position_pnl(position, fill)
            total_exits += position.quantity
            exits_by_month[settlement_date[:7]] += position.quantity
            positions.pop(symbol, None)

    monthly = [
        {
            "month": month,
            "observed_days": int(monthly_activity[month]["observed_days"]),
            "days_with_positions": int(monthly_activity[month]["days_with_positions"]),
            "entry_quantity": int(entries_by_month.get(month, 0)),
            "exit_quantity": int(exits_by_month.get(month, 0)),
            "settled_pnl": round(realized_by_month.get(month, 0.0), 2),
        }
        for month in sorted(monthly_activity)
    ]
    floating_values = [item["floating_pnl"] for item in daily]
    return {
        "granularity": "daily",
        "final_eligible": False,
        "days": len(daily),
        "first_date": all_dates[0] if all_dates else None,
        "last_date": all_dates[-1] if all_dates else None,
        "total_entries": total_entries,
        "total_exits": total_exits,
        "max_floating_loss": round(min(floating_values or [0]), 2),
        "max_floating_profit": round(max(floating_values or [0]), 2),
        "risk_triggers": risk_triggers,
        "risk_latched_level": risk_latched_level,
        "delta_unknown_days": delta_unknown_days,
        "entry_filter_counts": dict(sorted(filter_counts.items())),
        "qualified_months_400k": sum(1 for value in realized_by_month.values() if value >= 400_000),
        "target_months_500k": sum(1 for value in realized_by_month.values() if value >= 500_000),
        "monthly": monthly,
        "daily_tail": daily[-20:],
    }


def _run_worker(run_key: str, max_options: int, max_futures: int) -> None:
    api = None
    all_rows: list[dict[str, Any]] = []
    started_monotonic = time.monotonic()
    try:
        from tqsdk import TqApi, TqAuth

        username = os.getenv("TQSDK_USERNAME", "").strip()
        password = os.getenv("TQSDK_PASSWORD", "").strip()
        if not username or not password:
            raise RuntimeError("credentials_missing")
        api = TqApi(auth=TqAuth(username, password), web_gui=False, disable_print=True)
        futures, contracts, capped = discover_contracts(
            api,
            max_options=max_options,
            max_futures=max_futures,
        )
        if not futures or not contracts:
            raise RuntimeError("no_option_universe")
        _update_run(
            run_key,
            status="running",
            checks={
                "phase": "discovery_complete",
                "futures_count": len(futures),
                "options_count": len(contracts),
                "universe_capped": capped,
            },
            futures_count=len(futures),
            options_count=len(contracts),
            message="合约发现完成，开始逐合约读取日线。",
        )
        _insert_contracts(contracts)
        symbols = futures + [contract.symbol for contract in contracts]
        seen: set[str] = set()
        unique_symbols: list[str] = []
        for symbol in symbols:
            if symbol in seen:
                continue
            seen.add(symbol)
            unique_symbols.append(symbol)
        batch_size = 25
        missing_symbols: list[str] = []
        for start in range(0, len(unique_symbols), batch_size):
            if time.monotonic() - started_monotonic > MAX_RUN_SECONDS:
                raise RuntimeError("backtest_timeout")
            batch = unique_symbols[start : start + batch_size]
            rows, missing = fetch_daily_rows_batch(api, batch, run_key)
            missing_symbols.extend(missing)
            if rows:
                all_rows.extend(rows)
                _insert_bars(rows)
            _update_run(
                run_key,
                status="running",
                checks={
                    "phase": "bar_fetch",
                    "futures_count": len(futures),
                    "options_count": len(contracts),
                    "symbols_total": len(unique_symbols),
                    "symbols_done": min(start + len(batch), len(unique_symbols)),
                    "symbols_with_rows": len(unique_symbols) - len(missing_symbols),
                    "symbols_without_rows": len(missing_symbols),
                },
                futures_count=len(futures),
                options_count=len(contracts),
                bars_count=len(all_rows),
                message="逐合约读取日线中，已完成小批量订阅。",
            )
        option_research.bar_quality_issues(all_rows[:10000])
        quality_issues = option_research.bar_quality_issues(all_rows)
        contract_map = {contract.symbol: contract for contract in contracts}
        result = simulate_daily_a0(contracts=contracts, bars=all_rows)
        result["data_quality_issues"] = quality_issues[:100]
        result["universe_capped"] = capped
        result["futures_count"] = len(futures)
        result["options_count"] = len(contracts)
        result["bars_count"] = len(all_rows)
        result["daily_option_symbols"] = sum(1 for symbol in contract_map if any(row["symbol"] == symbol for row in all_rows))
        result["symbols_without_rows"] = sorted(set(missing_symbols))[:200]
        result["notes"] = [
            "这是日线真实期权初筛，不是协议要求的5分钟最终回测。",
            "由于普通K线接口和专业下载权限限制，逐合约分钟覆盖仍需单独检查。",
        ]
        with db.connect() as conn:
            cur = conn.cursor()
            db._exec(
                cur,
                """
                INSERT INTO option_research_results
                (run_key, result_type, result_key, metric_json)
                VALUES (?, 'summary', 'a0_daily', ?)
                ON CONFLICT(run_key, result_type, result_key) DO UPDATE SET metric_json = excluded.metric_json
                """,
                (run_key, json.dumps(result, ensure_ascii=False, sort_keys=True)),
            )
        _update_run(
            run_key,
            status="partial",
            checks={
                "mode": "daily_a0_screen",
                "final_eligible": False,
                "universe_capped": capped,
                "futures_count": len(futures),
                "options_count": len(contracts),
                "bars_count": len(all_rows),
                "data_quality_issue_count": len(quality_issues),
                "summary": result,
            },
            futures_count=len(futures),
            options_count=len(contracts),
            bars_count=len(all_rows),
            gaps_count=len(quality_issues),
            message="日线真实期权 A0 初筛已完成；等待5分钟逐合约覆盖检查后才能形成最终收益结论。",
            error_code="daily_screen_only",
        )
    except Exception as exc:
        code = str(exc) if str(exc) in {
            "credentials_missing",
            "no_option_universe",
            "backtest_timeout",
        } else "backtest_failed"
        _update_run(
            run_key,
            status="blocked",
            checks={"mode": "daily_a0_screen", "error": code},
            bars_count=len(all_rows),
            message="历史期权 A0 回测执行被阻断，未生成收益结论。",
            error_code=code,
        )
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass


def _authorized(authorization: Optional[str]) -> dict[str, Any]:
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def _latest_run() -> Optional[dict[str, Any]]:
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            """
            SELECT run_key, run_type, status, started_at, finished_at,
                   futures_count, options_count, bars_count, gaps_count,
                   checks_json, error_code, message
              FROM option_research_runs
             WHERE run_type = ?
             ORDER BY id DESC
             LIMIT 1
            """,
            (RUN_TYPE,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["checks"] = json.loads(result.pop("checks_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        result["checks"] = {}
    if result.get("status") == "running":
        started_text = str(result.get("started_at") or "")
        try:
            started_at = datetime.fromisoformat(started_text)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            age_minutes = (
                datetime.now(timezone.utc) - started_at
            ).total_seconds() / 60
        except ValueError:
            age_minutes = STALE_RUN_MINUTES + 1
        if age_minutes > STALE_RUN_MINUTES:
            _update_run(
                str(result["run_key"]),
                status="blocked",
                checks={**(result.get("checks") or {}), "stale_minutes": round(age_minutes, 2)},
                futures_count=int(result.get("futures_count") or 0),
                options_count=int(result.get("options_count") or 0),
                bars_count=int(result.get("bars_count") or 0),
                gaps_count=int(result.get("gaps_count") or 0),
                message="回测任务超过单批等待时间，已标记为数据链路阻断；请拆分期货批次后重试。",
                error_code="stale_run",
            )
            result["status"] = "blocked"
            result["error_code"] = "stale_run"
            result["message"] = "回测任务超过单批等待时间，已标记为数据链路阻断；请拆分期货批次后重试。"
    return result


@router.post("/option-research/backtest/start")
def start_backtest(
    payload: BacktestStartIn,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _authorized(authorization)
    if not option_research.is_staging_environment():
        raise HTTPException(status_code=403, detail="历史期权回测只在 Staging 启用")
    global _RUN_THREAD
    with _RUN_LOCK:
        latest = _latest_run()
        if latest and latest.get("status") == "running":
            return {"started": False, "run": latest}
        create_schema()
        run_key = f"a0-daily-{uuid.uuid4().hex}"
        max_options = payload.max_options or max(0, int(os.getenv("OPTION_RESEARCH_MAX_OPTIONS", "0")))
        max_futures = payload.max_futures or max(0, int(os.getenv("OPTION_RESEARCH_MAX_FUTURES", "0")))
        _create_run(run_key, max_options, max_futures)
        _RUN_THREAD = threading.Thread(
            target=_run_worker,
            args=(run_key, max_options, max_futures),
            name="option-research-a0-daily",
            daemon=True,
        )
        _RUN_THREAD.start()
        return {
            "started": True,
            "run_key": run_key,
            "mode": "daily_a0_screen",
            "code_version": BACKTEST_CODE_VERSION,
            "max_options": max_options,
            "max_futures": max_futures,
        }


@router.get("/option-research/backtest/status")
def backtest_status(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _authorized(authorization)
    latest = _latest_run()
    return {
        "enabled": option_research.is_staging_environment(),
        "code_version": BACKTEST_CODE_VERSION,
        "run": latest,
    }


@router.get("/option-research/backtest/results")
def backtest_results(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _authorized(authorization)
    latest = _latest_run()
    if not latest:
        return {"run": None, "result": None}
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            """
            SELECT metric_json
              FROM option_research_results
             WHERE run_key = ? AND result_type = 'summary' AND result_key = 'a0_daily'
             LIMIT 1
            """,
            (latest["run_key"],),
        ).fetchone()
    result: Optional[dict[str, Any]] = None
    if row:
        try:
            result = json.loads(row["metric_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            result = None
    return {"run": latest, "result": result}


def ensure_schema() -> None:
    create_schema()
