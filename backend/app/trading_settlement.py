"""Parser for futures-company daily and monthly TXT settlement statements."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Iterable


DATE_RE = re.compile(r"^20\d{6}$")


def _decode_statement(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "Settlement Statement" in text and "交易结算单" in text:
            return text
    raise ValueError("无法识别结算单编码或结构")


def _number(value: Any, default: float = 0.0) -> float:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text:
        return default
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return default


def _asset_type(contract: str) -> str:
    normalized = contract.upper()
    return "option" if "-C-" in normalized or "-P-" in normalized else "future"


def _open_close(value: str) -> str:
    text = value.strip()
    if text.startswith("开"):
        return "开仓"
    if text.startswith("平"):
        return "平仓"
    return text


def _mask_account(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"


def _metadata(text: str, filename: str) -> dict[str, Any]:
    scope_match = re.search(
        r"日期\s*Date[：:]\s*(20\d{6})(?:\s*-\s*(20\d{6}))?", text
    )
    account_match = re.search(r"客户号\s*Client ID[：:]\s*([A-Za-z0-9_-]+)", text)
    creation_match = re.search(r"Creation Date[：:]\s*(20\d{6})", text)
    if not scope_match or not account_match:
        raise ValueError("无法识别结算单账户或日期")
    range_start = scope_match.group(1)
    range_end = scope_match.group(2) or range_start
    if range_end < range_start:
        raise ValueError("结算单日期范围不合法")
    account_code = account_match.group(1).strip()
    return {
        "filename": filename,
        "statement_type": "monthly" if scope_match.group(2) else "daily",
        "range_start": range_start,
        "range_end": range_end,
        "creation_date": creation_match.group(1) if creation_match else range_end,
        "account_code": account_code,
        "account_code_masked": _mask_account(account_code),
    }


def _label_number(text: str, english_label: str) -> float:
    match = re.search(
        rf"{re.escape(english_label)}[：:]\s*([-+]?\d[\d,]*(?:\.\d+)?)", text
    )
    return _number(match.group(1)) if match else 0.0


def _account_summary(text: str) -> dict[str, float]:
    labels = {
        "balance_bf": "Balance B/F",
        "deposit_withdrawal": "Deposit/Withdrawal",
        "balance_cf": "Balance C/F",
        "realized_pnl": "Realized P/L",
        "mtm_pnl": "MTM P/L",
        "commission": "Commission",
        "margin_occupied": "Margin Occupied",
        "client_equity": "Client Equity",
        "fund_available": "Fund Avail.",
        "premium_received": "Premium Received",
        "premium_paid": "Premium Paid",
    }
    return {key: _label_number(text, label) for key, label in labels.items()}


def _slice(text: str, starts: Iterable[str], ends: Iterable[str]) -> str:
    start_positions = [text.find(marker) for marker in starts if text.find(marker) >= 0]
    if not start_positions:
        return ""
    start = min(start_positions)
    end_positions = [
        text.find(marker, start + 1)
        for marker in ends
        if text.find(marker, start + 1) >= 0
    ]
    end = min(end_positions) if end_positions else len(text)
    return text[start:end]


def _pipe_rows(section: str) -> list[tuple[int, list[str]]]:
    rows = []
    for line_no, line in enumerate(section.splitlines(), start=1):
        if not line.startswith("|"):
            continue
        columns = [item.strip() for item in line.strip().strip("|").split("|")]
        if not columns or any("共" in item and "条" in item for item in columns[:1]):
            continue
        rows.append((line_no, columns))
    return rows


def _total_columns(section: str) -> list[str]:
    for line in section.splitlines():
        if line.startswith("|") and re.search(r"共\s*\d+\s*条", line):
            return [item.strip() for item in line.strip().strip("|").split("|")]
    return []


def _total_count(section: str) -> int | None:
    match = re.search(r"\|共\s*(\d+)\s*条", section)
    return int(match.group(1)) if match else None


def _trade_rows(text: str) -> tuple[list[dict[str, Any]], str]:
    section = _slice(
        text,
        ("成交记录 Transaction Record",),
        ("行权明细", "平仓明细 Position Closed"),
    )
    result = []
    for line_no, row in _pipe_rows(section):
        if len(row) < 17 or not DATE_RE.match(row[0]):
            continue
        contract = row[5].lower()
        result.append(
            {
                "date": row[0],
                "trade_time": "",
                "exchange": row[2],
                "trading_code": row[3],
                "product": row[4],
                "contract": contract,
                "asset_type": _asset_type(contract),
                "side": row[6],
                "hedge_flag": row[7],
                "price": _number(row[8]),
                "quantity": _number(row[9]),
                "turnover": _number(row[10]),
                "open_close_raw": row[11],
                "open_close": _open_close(row[11]),
                "fee": _number(row[12]),
                "source_close_pnl": _number(row[13]),
                "premium_cashflow": _number(row[14]),
                "transaction_no": row[15],
                "source_row_no": line_no,
                "raw_data": {"columns": row},
            }
        )
    return result, section


def _exercise_rows(text: str) -> tuple[list[dict[str, Any]], str]:
    section = _slice(
        text,
        ("行权明细  Exercise Statement", "行权明细 Exercise Statement"),
        ("平仓明细 Position Closed",),
    )
    result = []
    for line_no, row in _pipe_rows(section):
        if len(row) < 15 or not DATE_RE.match(row[0]):
            continue
        raw_type = row[8]
        event_type = _option_event_type(raw_type)
        result.append(
            {
                "event_date": row[0],
                "exchange": row[2],
                "trading_code": row[3],
                "product": row[4],
                "contract": row[5].lower(),
                "hedge_flag": row[6],
                "side": row[7],
                "event_type_raw": raw_type,
                "event_type": event_type,
                "quantity": _number(row[9]),
                "exercise_price": _number(row[10]),
                "exercise_amount": _number(row[11]),
                "exercise_pnl": _number(row[12]),
                "fee": _number(row[13]),
                "source_row_no": line_no,
                "raw_data": {"columns": row},
            }
        )
    return result, section


def _option_event_type(raw_type: str) -> str:
    if "放弃" in raw_type:
        return "expiry_abandon"
    if "履约" in raw_type:
        return "assignment"
    if "执行" in raw_type or "行权" in raw_type:
        return "exercise"
    return raw_type


def _close_rows(text: str) -> tuple[list[dict[str, Any]], str]:
    section = _slice(
        text, ("平仓明细 Position Closed",), ("持仓明细 Positions Detail",)
    )
    result = []
    for line_no, row in _pipe_rows(section):
        if len(row) < 16 or not DATE_RE.match(row[0]):
            continue
        contract = row[5].lower()
        close_side = row[8]
        result.append(
            {
                "close_date": row[0],
                "exchange": row[2],
                "trading_code": row[3],
                "product": row[4],
                "contract": contract,
                "asset_type": _asset_type(contract),
                "open_date": row[6],
                "hedge_flag": row[7],
                "open_side": "卖" if close_side == "买" else "买",
                "close_side": close_side,
                "quantity": _number(row[9]),
                "open_price": _number(row[10]),
                "previous_settlement": _number(row[11]),
                "close_price": _number(row[12]),
                "mark_close_pnl": _number(row[13]),
                "premium_cashflow": _number(row[14]),
                "fee": None,
                "source_row_no": line_no,
                "raw_data": {"columns": row},
            }
        )
    return result, section


def _position_rows(
    text: str, snapshot_date: str
) -> tuple[list[dict[str, Any]], str]:
    section = _slice(
        text, ("持仓明细 Positions Detail",), ("持仓汇总 Positions",)
    )
    result = []
    for line_no, row in _pipe_rows(section):
        if len(row) < 17 or not DATE_RE.match(row[5]):
            continue
        contract = row[4].lower()
        result.append(
            {
                "snapshot_date": snapshot_date,
                "exchange": row[1],
                "trading_code": row[2],
                "product": row[3],
                "contract": contract,
                "asset_type": _asset_type(contract),
                "open_date": row[5],
                "hedge_flag": row[6],
                "direction": row[7],
                "quantity": _number(row[8]),
                "average_price": _number(row[9]),
                "previous_settlement": _number(row[10]),
                "settlement_price": _number(row[11]),
                "source_floating_pnl": _number(row[12]),
                "mark_pnl": _number(row[13]),
                "margin": _number(row[14]),
                "option_market_value": _number(row[15]),
                "valuation_price": None,
                "floating_pnl": None,
                "valuation_status": "pending_calculation",
                "source_row_no": line_no,
                "raw_data": {"columns": row},
            }
        )
    return result, section


def _position_summary(text: str) -> tuple[list[dict[str, Any]], str]:
    section = _slice(text, ("持仓汇总 Positions",), ("公司盖章",))
    result = []
    for line_no, row in _pipe_rows(section):
        if len(row) < 16 or row[0] in {"投资单元", "InvestUnit"}:
            continue
        if not row[3]:
            continue
        result.append(
            {
                "contract": row[3].lower(),
                "long_quantity": _number(row[4]),
                "long_average_price": _number(row[5]),
                "short_quantity": _number(row[6]),
                "short_average_price": _number(row[7]),
                "mark_pnl": _number(row[10]),
                "margin": _number(row[11]),
                "hedge_flag": row[12],
                "long_option_market_value": _number(row[13]),
                "short_option_market_value": _number(row[14]),
                "source_row_no": line_no,
                "raw_data": {"columns": row},
            }
        )
    return result, section


def _cash_movements(text: str) -> list[dict[str, Any]]:
    section = _slice(
        text, ("出入金明细 Deposit/Withdrawal",), ("成交记录 Transaction Record",)
    )
    result = []
    for line_no, row in _pipe_rows(section):
        if len(row) < 7 or not DATE_RE.match(row[0]):
            continue
        result.append(
            {
                "date": row[0],
                "movement_type": row[1],
                "deposit": _number(row[2]),
                "withdrawal": _number(row[3]),
                "exchange_rate": _number(row[4]),
                "note": row[6],
                "source_row_no": line_no,
                "raw_data": {"columns": row},
            }
        )
    return result


def _assert_count(name: str, rows: list[dict[str, Any]], section: str) -> None:
    expected = _total_count(section)
    if expected is not None and expected != len(rows):
        raise ValueError(f"{name}条数汇总不一致")


def _validate(parsed: dict[str, Any], sections: dict[str, str]) -> None:
    for key, label in (
        ("trades", "成交"),
        ("closes", "平仓"),
        ("exercises", "行权"),
        ("positions", "持仓"),
    ):
        _assert_count(label, parsed[key], sections[key])
    if not sections["trades"] or not sections["closes"] or not sections["positions"]:
        raise ValueError("结算单缺少必要明细区段")

    trade_total = _total_columns(sections["trades"])
    if trade_total:
        expected_fee = _number(trade_total[12])
        actual_fee = sum(row["fee"] for row in parsed["trades"])
        if abs(expected_fee - actual_fee) > 0.02:
            raise ValueError("手续费汇总不一致")
    summary_fee = parsed["account_summary"]["commission"]
    actual_fee = sum(row["fee"] for row in parsed["trades"])
    if abs(summary_fee - actual_fee) > 0.02:
        raise ValueError("手续费汇总不一致")

    position_total = _total_columns(sections["positions"])
    if position_total:
        checks = (
            (8, sum(row["quantity"] for row in parsed["positions"]), "持仓手数"),
            (14, sum(row["margin"] for row in parsed["positions"]), "保证金"),
        )
        for index, actual, label in checks:
            if abs(_number(position_total[index]) - actual) > 0.02:
                raise ValueError(f"{label}汇总不一致")


def parse_settlement_statement(content: bytes, filename: str) -> dict[str, Any]:
    """Decode, normalize, and reconcile one daily or monthly statement."""

    text = _decode_statement(content)
    metadata = _metadata(text, filename)
    trades, trade_section = _trade_rows(text)
    exercises, exercise_section = _exercise_rows(text)
    closes, close_section = _close_rows(text)
    positions, position_section = _position_rows(text, metadata["range_end"])
    summaries, summary_section = _position_summary(text)
    parsed = {
        "metadata": metadata,
        "account_summary": _account_summary(text),
        "cash_movements": _cash_movements(text),
        "trades": trades,
        "exercises": exercises,
        "closes": closes,
        "positions": positions,
        "position_summary": summaries,
        "warnings": [],
    }
    parsed["counts"] = {
        "trade": len(trades),
        "close": len(closes),
        "exercise": len(exercises),
        "position": len(positions),
    }
    _validate(
        parsed,
        {
            "trades": trade_section,
            "exercises": exercise_section,
            "closes": close_section,
            "positions": position_section,
            "position_summary": summary_section,
        },
    )
    return parsed
