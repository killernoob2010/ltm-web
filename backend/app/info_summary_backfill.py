from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from datetime import datetime
import json
import re
import statistics
from typing import Optional, Protocol

import requests

from .cache_service import get_prices_for_info_contracts, save_calculated_data, save_daily_prices_batch

_last_backfill_result = None
_last_backfill_time = None


INFO_SUMMARY_TYPES = ["卷螺差", "螺矿比", "煤矿比", "盘面钢厂利润", "月差", "掉期月差", "内外盘差", "内外盘差2"]

def get_last_backfill_status():
    """返回最近一次回填的状态摘要，供 status API 使用。"""
    if _last_backfill_result is None:
        return None
    return {
        "time": _last_backfill_time,
        "results": [
            {
                "info_type": r.info_type,
                "status": r.status,
                "message": r.message,
                "price_rows_written": r.price_rows_written,
                "calculated_rows_written": r.calculated_rows_written,
                "latest_price_date": r.latest_price_date,
                "latest_calculated_date": r.latest_calculated_date,
            }
            for r in _last_backfill_result
        ],
    }


@dataclass
class BackfillRequest:
    info_type: Optional[str] = None
    calc_date: str = field(default_factory=lambda: date.today().isoformat())
    force: bool = False


@dataclass
class BackfillJob:
    info_type: str
    payload: object


@dataclass
class BackfillResult:
    info_type: str
    status: str
    price_rows_written: int = 0
    calculated_rows_written: int = 0
    latest_price_date: Optional[str] = None
    latest_calculated_date: Optional[str] = None
    message: str = ""


class HistoryProvider(Protocol):
    def history(self, contract_code: str) -> dict[str, float]:
        """Return close prices keyed by YYYY-MM-DD."""


class StaticHistoryProvider:
    def __init__(self, histories: dict[str, dict[str, float]]):
        self.histories = histories

    def history(self, contract_code: str) -> dict[str, float]:
        return self.histories.get(contract_code.upper(), {})


class SinaHistoryProvider:
    def history(self, contract_code: str) -> dict[str, float]:
        if contract_code.upper().startswith("FE"):
            return {}

        symbol = contract_code.lower()
        url = (
            "https://stock2.finance.sina.com.cn/futures/api/jsonp.php"
            f"/var%20_{symbol}=/InnerFuturesNewService.getDailyKLine?symbol={symbol}"
        )
        try:
            response = requests.get(url, timeout=4)
            response.raise_for_status()
        except Exception:
            return {}

        match = re.search(r"=\((\[.*\])\)", response.text, flags=re.S)
        if not match:
            return {}

        try:
            rows = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

        result = {}
        for row in rows:
            calc_date = row.get("d")
            close_price = row.get("c")
            if not calc_date or close_price in [None, ""]:
                continue
            result[calc_date] = float(close_price)
        return result


def build_backfill_jobs(request: BackfillRequest) -> list[BackfillJob]:
    from .main import InfoCalculateIn, default_info_contracts

    defaults = default_info_contracts()
    info_types = [request.info_type] if request.info_type else INFO_SUMMARY_TYPES
    jobs = []
    for info_type in info_types:
        if info_type in ["月差", "掉期月差"]:
            yuecha = defaults["yuecha_defaults"]
            payload = InfoCalculateIn(
                info_type=info_type,
                year=defaults["default_year"],
                calc_date=request.calc_date,
                year1=yuecha["year1"],
                month1=yuecha["month1"],
                year2=yuecha["year2"],
                month2=yuecha["month2"],
            )
        else:
            payload = InfoCalculateIn(
                info_type=info_type,
                year=defaults["default_year"],
                month=defaults["default_month"],
                calc_date=request.calc_date,
            )
        jobs.append(BackfillJob(info_type=info_type, payload=payload))
    return jobs


def run_all_info_summary_backfills(
    request: BackfillRequest,
    provider: Optional[HistoryProvider] = None,
) -> list[BackfillResult]:
    results = []
    for job in build_backfill_jobs(request):
        try:
            results.append(run_info_summary_backfill(job.payload, provider=provider, force=request.force))
        except Exception as exc:
            results.append(BackfillResult(info_type=job.info_type, status="failed", message=str(exc)))
    global _last_backfill_result, _last_backfill_time
    _last_backfill_result = results
    _last_backfill_time = datetime.now().isoformat()
    return results


def run_info_summary_backfill(payload: object, provider: Optional[HistoryProvider] = None, force: bool = False) -> BackfillResult:
    from .main import cache_month_key, indicator_contracts_for_cache, value_from_cached_prices, MONTH_DIFF_TYPES

    try:
        provider = provider or SinaHistoryProvider()
        info_type = payload.info_type
        contract_codes = indicator_contracts_for_cache(payload)
        if not contract_codes:
            if info_type in ["内外盘差", "内外盘差2"]:
                return BackfillResult(info_type=info_type, status="skipped", message="内外盘差历史回填尚未实现（需 FE 历史源和汇率历史）")
            return BackfillResult(info_type=info_type, status="skipped", message="暂无可回填的历史合约规则")

        if not force:
            existing = get_prices_for_info_contracts(info_type, contract_codes)
            if existing and all(code in existing for code in contract_codes):
                all_dates = set.intersection(*(set(existing[code]) for code in contract_codes))
                if all_dates:
                    latest = max(all_dates)
                    calc_dt = date.fromisoformat(payload.calc_date)
                    if (calc_dt - date.fromisoformat(latest)).days <= 2:
                        return BackfillResult(info_type=info_type, status="skipped", message="无需回填，缓存已是最新", latest_price_date=latest)

        histories = {code: provider.history(code) for code in contract_codes}
        if any(not histories[code] for code in contract_codes):
            missing = [code for code in contract_codes if not histories[code]]
            if all(code.upper().startswith("FE") for code in missing):
                return BackfillResult(info_type=info_type, status="skipped", message="FE 历史行情源未就绪，暂无法回填")
            return BackfillResult(
                info_type=info_type,
                status="failed",
                message=f"历史行情缺失: {', '.join(missing)}",
            )

        common_dates = sorted(set.intersection(*(set(histories[code]) for code in contract_codes)))
        if len(common_dates) < 2:
            return BackfillResult(info_type=info_type, status="failed", message="共同历史日期不足")

        price_rows = []
        for code in contract_codes:
            for calc_date in common_dates:
                close_price = histories[code].get(calc_date)
                if close_price is not None:
                    price_rows.append((info_type, code, calc_date, close_price))
        if price_rows:
            save_daily_prices_batch(price_rows)

        price_data = get_prices_for_info_contracts(info_type, contract_codes) or {}
        calculation_dates = _common_dates_for_contracts(price_data, contract_codes)
        results_written = _calculate_and_save_results(
            payload=payload,
            contract_codes=contract_codes,
            calculation_dates=calculation_dates,
            price_data=price_data,
            value_from_cached_prices=value_from_cached_prices,
            month_key=cache_month_key(payload),
        )

        latest_price_date = common_dates[-1] if common_dates else None
        return BackfillResult(
            info_type=info_type,
            status="success" if results_written else "partial",
            price_rows_written=len(price_rows),
            calculated_rows_written=results_written,
            latest_price_date=latest_price_date,
            latest_calculated_date=calculation_dates[-1] if results_written else None,
            message="已更新" if results_written else "已写入价格，但历史窗口不足",
        )
    except Exception as exc:
        return BackfillResult(info_type=payload.info_type, status="failed", message=str(exc))


def _common_dates_for_contracts(price_data: dict, contract_codes: list[str]) -> list[str]:
    if any(code not in price_data for code in contract_codes):
        return []
    return sorted(set.intersection(*(set(price_data[code]) for code in contract_codes)))


def _calculate_and_save_results(
    payload: object,
    contract_codes: list[str],
    calculation_dates: list[str],
    price_data: dict,
    value_from_cached_prices,
    month_key: Optional[str],
) -> int:
    written = 0
    for calc_index, calc_date in enumerate(calculation_dates):
        if calc_index < 1:
            continue

        def value_for_date(day: str) -> Optional[float]:
            prices = [price_data[code].get(day) for code in contract_codes]
            if any(price is None for price in prices):
                return None
            return value_from_cached_prices(payload.info_type, prices)

        t_1_value = value_for_date(calculation_dates[calc_index - 1])
        t_2_value = value_for_date(calculation_dates[calc_index - 2]) if calc_index >= 2 else None
        window_dates = calculation_dates[max(0, calc_index - 180):calc_index]
        values = [value_for_date(day) for day in window_dates]
        values = [value for value in values if value is not None]
        if not values:
            continue

        save_calculated_data(
            payload.info_type,
            payload.year,
            month_key,
            calc_date,
            t_1_value=t_1_value,
            t_2_value=t_2_value,
            mean_value=statistics.mean(values),
            min_value=min(values),
            max_value=max(values),
            std_value=statistics.stdev(values) if len(values) >= 10 else None,
        )
        written += 1
    return written
