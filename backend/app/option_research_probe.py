from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import math
import os
import re
import time
from typing import Any


PROBE_PREFIX = "OPTION_RESEARCH_PROBE="
PROBE_VERSION = "v2"
_IRON_ORE_FUTURE_RE = re.compile(r"^DCE\.i\d{4}$", re.IGNORECASE)
_OPTION_STRIKE_RE = re.compile(r"-[CP]-(?P<strike>\d+(?:\.\d+)?)$", re.IGNORECASE)
_KNOWN_ERROR_CODES = {
    "credentials_missing",
    "auth_failed",
    "no_iron_ore_futures",
    "no_option_contracts",
    "daily_history_unavailable",
    "five_minute_history_unavailable",
    "downloader_unavailable",
    "tqsdk_probe_failed",
}


def _base_checks(credentials_configured: bool) -> dict[str, Any]:
    return {
        "probe_version": PROBE_VERSION,
        "credentials_configured": credentials_configured,
        "iron_ore_futures_discovered": False,
        "option_contracts_discovered": False,
        "daily_history_available": False,
        "five_minute_history_available": False,
        "five_minute_full_lifecycle": False,
        "sample_five_minute_bars": 0,
        "professional_downloader_available": False,
    }


def _failure(code: str, checks: dict[str, Any], **counts: int) -> dict[str, Any]:
    return {
        "status": "blocked",
        "error_code": code if code in _KNOWN_ERROR_CODES else "tqsdk_probe_failed",
        "futures_count": int(counts.get("futures_count", 0)),
        "options_count": int(counts.get("options_count", 0)),
        "bars_count": int(counts.get("bars_count", 0)),
        "checks": checks,
    }


def _valid_kline_timestamps(frame: Any) -> list[int]:
    if frame is None or "datetime" not in frame or "close" not in frame:
        return []
    timestamps: list[int] = []
    for timestamp, close in zip(frame["datetime"], frame["close"]):
        try:
            timestamp_value = int(timestamp)
            close_value = float(close)
        except (TypeError, ValueError, OverflowError):
            continue
        if timestamp_value > 0 and math.isfinite(close_value) and close_value >= 0:
            timestamps.append(timestamp_value)
    return timestamps


def _beijing_date(timestamp_nano: int) -> str:
    beijing = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(timestamp_nano / 1_000_000_000, beijing).date().isoformat()


def _representative_option(symbols: list[str]) -> str:
    parsed = [
        (float(match.group("strike")), symbol)
        for symbol in symbols
        if (match := _OPTION_STRIKE_RE.search(symbol)) is not None
    ]
    if not parsed:
        return sorted(symbols)[len(symbols) // 2]
    strikes = sorted({strike for strike, _ in parsed})
    middle_strike = strikes[len(strikes) // 2]
    return sorted(symbol for strike, symbol in parsed if strike == middle_strike)[0]


def _error_code(exc: Exception) -> str:
    text = str(exc).lower()
    if any(token in text for token in ("auth", "登录", "认证", "密码", "账户")):
        return "auth_failed"
    return "tqsdk_probe_failed"


def run_probe() -> dict[str, Any]:
    username = os.getenv("TQSDK_USERNAME", "").strip()
    password = os.getenv("TQSDK_PASSWORD", "").strip()
    checks = _base_checks(bool(username and password))
    if not username or not password:
        return _failure("credentials_missing", checks)

    api = None
    try:
        output_buffer = io.StringIO()
        with contextlib.redirect_stdout(output_buffer):
            from tqsdk import TqApi, TqAuth

            api = TqApi(
                auth=TqAuth(username, password),
                web_gui=False,
                disable_print=True,
            )

        expired_futures = list(
            api.query_quotes(
                ins_class="FUTURE",
                exchange_id="DCE",
                product_id="i",
                expired=True,
            )
        )
        active_futures = list(
            api.query_quotes(
                ins_class="FUTURE",
                exchange_id="DCE",
                product_id="i",
                expired=False,
            )
        )
        futures = sorted(
            {
                symbol
                for symbol in expired_futures + active_futures
                if _IRON_ORE_FUTURE_RE.match(str(symbol or ""))
            }
        )
        if not futures:
            return _failure("no_iron_ore_futures", checks)
        checks["iron_ore_futures_discovered"] = True

        expired_candidates = sorted(
            (symbol for symbol in expired_futures if symbol in futures), reverse=True
        )[:12]
        active_candidates = sorted(
            (symbol for symbol in active_futures if symbol in futures)
        )[:4]
        sampled_options: list[str] = []
        sample_underlying_options: list[str] = []
        for underlying_symbol in expired_candidates + active_candidates:
            options = list(api.query_options(underlying_symbol))
            if options and not sample_underlying_options:
                sample_underlying_options = [str(symbol) for symbol in options]
            sampled_options.extend(str(symbol) for symbol in options)
            if len(sampled_options) >= 200:
                break
        sampled_options = sorted(set(sampled_options))
        if not sampled_options:
            return _failure(
                "no_option_contracts",
                checks,
                futures_count=len(futures),
            )
        checks["option_contracts_discovered"] = True

        sample_symbol = _representative_option(sample_underlying_options)
        frame = api.get_kline_serial(sample_symbol, 24 * 60 * 60, data_length=500)
        deadline = time.time() + 20
        daily_timestamps = _valid_kline_timestamps(frame)
        while not daily_timestamps and time.time() < deadline:
            api.wait_update(deadline=min(deadline, time.time() + 1))
            daily_timestamps = _valid_kline_timestamps(frame)
        bars_count = len(daily_timestamps)
        if bars_count == 0:
            return _failure(
                "daily_history_unavailable",
                checks,
                futures_count=len(futures),
                options_count=len(sampled_options),
            )
        checks["daily_history_available"] = True
        checks["sample_daily_first_date"] = _beijing_date(daily_timestamps[0])
        checks["sample_daily_last_date"] = _beijing_date(daily_timestamps[-1])

        five_minute_frame = api.get_kline_serial(
            sample_symbol, 5 * 60, data_length=10_000
        )
        five_minute_deadline = time.time() + 60
        five_minute_timestamps = _valid_kline_timestamps(five_minute_frame)
        while not five_minute_timestamps and time.time() < five_minute_deadline:
            api.wait_update(deadline=min(five_minute_deadline, time.time() + 1))
            five_minute_timestamps = _valid_kline_timestamps(five_minute_frame)
        if not five_minute_timestamps:
            return _failure(
                "five_minute_history_unavailable",
                checks,
                futures_count=len(futures),
                options_count=len(sampled_options),
                bars_count=bars_count,
            )
        checks["five_minute_history_available"] = True
        checks["sample_five_minute_bars"] = len(five_minute_timestamps)
        checks["sample_five_minute_first_date"] = _beijing_date(
            five_minute_timestamps[0]
        )
        checks["sample_five_minute_last_date"] = _beijing_date(
            five_minute_timestamps[-1]
        )
        checks["five_minute_full_lifecycle"] = (
            checks["sample_five_minute_first_date"] <= checks["sample_daily_first_date"]
            and checks["sample_five_minute_last_date"] >= checks["sample_daily_last_date"]
        )

        auth = getattr(api, "_auth", None)
        has_feature = getattr(auth, "_has_feature", None)
        downloader_available = bool(
            callable(has_feature) and has_feature("tq_dl")
        )
        checks["professional_downloader_available"] = downloader_available
        if not downloader_available:
            return {
                "status": "partial",
                "error_code": "downloader_unavailable",
                "futures_count": len(futures),
                "options_count": len(sampled_options),
                "bars_count": bars_count,
                "checks": checks,
            }
        return {
            "status": "ready",
            "error_code": None,
            "futures_count": len(futures),
            "options_count": len(sampled_options),
            "bars_count": bars_count,
            "checks": checks,
        }
    except Exception as exc:
        return _failure(_error_code(exc), checks)
    finally:
        if api is not None:
            try:
                api.close()
            except Exception:
                pass


def main() -> None:
    result = run_probe()
    print(PROBE_PREFIX + json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
