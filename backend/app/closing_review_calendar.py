"""Versioned mainland China futures trading-day calendar for the Agent.

This is intentionally a checked-in, dependency-free snapshot.  The 2026
holiday closure dates were checked on 2026-09-04 against the State Council
holiday notice and the Shanghai Futures Exchange 2026 closure schedule; DCE
iron-ore options follow the mainland futures holiday closure dates.  A future
calendar update must change the version and the checked-in date set together.
"""

from __future__ import annotations

from datetime import date, timedelta


CALENDAR_VERSION = "china-futures-2026-v1"
CALENDAR_SOURCE = (
    "国务院办公厅国办发明电〔2025〕7号；上海期货交易所2026年休市安排"
)
SUPPORTED_START = date(2025, 12, 31)
SUPPORTED_END = date(2026, 12, 31)

# Weekdays inside the official 2026 mainland futures closure periods.  Weekends
# are closed independently; exchange make-up workdays remain non-trading days.
_CLOSED_WEEKDAYS_2026 = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 2, 23),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    }
)


class CalendarUnavailable(ValueError):
    """The requested date is outside the checked-in authoritative snapshot."""


def _ensure_supported(value: date) -> None:
    if value < SUPPORTED_START or value > SUPPORTED_END:
        raise CalendarUnavailable(
            f"交易日历版本 {CALENDAR_VERSION} 不覆盖 {value.isoformat()}"
        )


def is_trading_day(value: date) -> bool:
    _ensure_supported(value)
    if value.weekday() >= 5:
        return False
    if value.year == 2026 and value in _CLOSED_WEEKDAYS_2026:
        return False
    return True


def resolve_previous_trading_day(reference_date: date) -> date:
    _ensure_supported(reference_date)
    candidate = reference_date - timedelta(days=1)
    for _ in range(370):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise CalendarUnavailable(
        f"无法从交易日历版本 {CALENDAR_VERSION} 解析 {reference_date.isoformat()} 的上一交易日"
    )
