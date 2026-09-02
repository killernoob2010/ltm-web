"""Read-only, conservative decoder for WH6 match/fill cache files."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import re
import struct
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .formats import MatchLayout, detect_layout, detect_order_layout
from .models import AccountIdentity, FillRecord, ParseIssue, SourceFile

SHANGHAI = ZoneInfo("Asia/Shanghai")
REFERENCE_SESSIONS = (
    (time(9, 0), time(10, 15)),
    (time(10, 15), time(11, 30)),
    (time(13, 30), time(15, 0)),
    (time(21, 0), time(23, 0)),
)
OPTION_RE = re.compile(
    r"^(?P<underlying>[a-z]{1,8})(?P<expiry>\d{3,6})[-_]?(?P<kind>[cp])[-_]?(?P<strike>\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"(?P<date>\d{8})(?:[^/]*)match(?:[-_][^/]*)?\.dat$", re.IGNORECASE)

# WH6 match records do not carry direction/open-close/exchange in the builds
# observed so far.  These values are enriched from the paired order record;
# the map only fills a known exchange and never guesses for an unknown product.
EXCHANGE_BY_UNDERLYING = {
    "i": "DCE",
    "j": "DCE",
    "jm": "DCE",
    "m": "DCE",
    "y": "DCE",
    "a": "DCE",
    "b": "DCE",
    "c": "DCE",
    "cs": "DCE",
    "l": "DCE",
    "v": "DCE",
    "pp": "DCE",
    "eg": "DCE",
    "eb": "DCE",
    "pg": "DCE",
    "rr": "DCE",
    "rb": "SHFE",
    "hc": "SHFE",
    "ru": "SHFE",
    "bu": "SHFE",
    "fu": "SHFE",
    "sp": "SHFE",
    "ss": "SHFE",
    "cu": "SHFE",
    "al": "SHFE",
    "zn": "SHFE",
    "pb": "SHFE",
    "ni": "SHFE",
    "sn": "SHFE",
    "au": "SHFE",
    "ag": "SHFE",
    "sc": "INE",
    "nr": "INE",
    "lu": "INE",
    "bc": "INE",
    "if": "CFFEX",
    "ih": "CFFEX",
    "ic": "CFFEX",
    "im": "CFFEX",
    "io": "CFFEX",
    "mo": "CFFEX",
    "ec": "CFFEX",
    "ta": "CZCE",
    "ma": "CZCE",
    "sr": "CZCE",
    "cf": "CZCE",
    "oi": "CZCE",
    "rm": "CZCE",
    "fg": "CZCE",
    "sa": "CZCE",
    "pf": "CZCE",
    "cy": "CZCE",
    "ap": "CZCE",
    "cj": "CZCE",
    "ur": "CZCE",
    "px": "CZCE",
    "sh": "CZCE",
    "sm": "CZCE",
    "sf": "CZCE",
}


def _decode_shifted(raw: bytes) -> str:
    payload = bytes(value >> 1 for value in raw if value)
    return payload.decode("gb18030", errors="replace").strip(" \t\r\n\x00")


def _decode_ascii(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()


def _parse_decimal(value: str) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return format(Decimal(text), "f")
    except (InvalidOperation, ValueError):
        return None


def normalize_contract(contract: str) -> str:
    text = re.sub(r"[_]+", "-", str(contract or "").strip()).lower()
    match = OPTION_RE.match(text)
    if match:
        return "%s%s-%s-%s" % (
            match.group("underlying"),
            match.group("expiry"),
            match.group("kind").lower(),
            match.group("strike"),
        )
    return text


def is_option_contract(contract: str) -> bool:
    return bool(OPTION_RE.match(normalize_contract(contract).replace("-", "-")))


def _option_parts(contract: str) -> Dict[str, str]:
    normalized = normalize_contract(contract)
    match = OPTION_RE.match(normalized)
    if not match:
        return {}
    return {
        "underlying": match.group("underlying").lower(),
        "expiry_month": match.group("expiry"),
        "option_kind": match.group("kind").lower(),
        "strike": match.group("strike"),
    }


def _parse_timestamp(value: str, trading_date: str) -> Tuple[str, str]:
    text = str(value or "").strip()
    candidates = [text, text.replace("/", "-")]
    parsed: Optional[datetime] = None
    for candidate in candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S", "%H:%M:%S", "%H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(candidate, fmt)
                if fmt.startswith("%H"):
                    parsed = datetime.strptime(trading_date + " " + candidate, "%Y-%m-%d " + fmt)
                break
            except ValueError:
                continue
        if parsed:
            break
    if parsed is None:
        return trading_date, ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    local = parsed.astimezone(SHANGHAI)
    return business_trading_day(local), local.strftime("%H:%M:%S")


def business_trading_day(local_dt: datetime, *, timezone=SHANGHAI) -> str:
    """Return the exchange trading date; 00:00-05:00 belongs to prior night."""
    local = local_dt.astimezone(timezone) if local_dt.tzinfo else local_dt.replace(tzinfo=timezone)
    if local.hour < 5:
        local -= timedelta(days=1)
    return local.date().isoformat()


class Session:
    def __init__(self, start: time, end: time):
        self.start = start
        self.end = end


def reference_sessions() -> Sequence[Session]:
    return tuple(Session(start, end) for start, end in REFERENCE_SESSIONS)


def _filename_trading_date(path: Path, fallback: Optional[str]) -> str:
    match = DATE_RE.search(path.name)
    if match:
        raw = match.group("date")
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    if fallback:
        return fallback
    raise ValueError("成交缓存文件名缺少交易日")


def _record_issue(code: str, message: str, path: Path, index: Optional[int], file_hash: str) -> ParseIssue:
    return ParseIssue(code=code, message=message, path=str(path), record_index=index, file_sha256=file_hash)


def _companion_order_path(match_path: Path) -> Optional[Path]:
    name = match_path.name
    marker = name.lower().find("match")
    if marker < 0:
        return None
    candidate = match_path.with_name(name[:marker] + "order" + name[marker + len("match") :])
    return candidate if candidate.is_file() else None


def _order_layout_for_bytes(data: bytes):
    if len(data) < 16:
        return None
    declared = struct.unpack_from("<I", data, 8)[0]
    body_size = len(data) - 16
    for size in (231, 232):
        complete_count = body_size // size
        if body_size >= size and (body_size % size == 0 or (declared > complete_count and complete_count > 0)):
            return detect_order_layout(size)
    return None


def _load_order_enrichment(match_path: Path) -> Tuple[Dict[str, Dict[str, object]], Optional[ParseIssue]]:
    """Read a paired order cache only to enrich already-recorded fills."""
    order_path = _companion_order_path(match_path)
    if order_path is None:
        return {}, None
    try:
        data = order_path.read_bytes()
    except OSError as exc:
        return {}, ParseIssue(code="order_read_error", message="无法读取配套 order 成交关联文件: %s" % exc, path=str(order_path))
    file_hash = hashlib.sha256(data).hexdigest()
    layout = _order_layout_for_bytes(data)
    if layout is None:
        return {}, _record_issue("unknown_order_format", "配套 order 文件格式未验证，成交方向暂不猜测", order_path, None, file_hash)
    declared_count = struct.unpack_from("<I", data, 8)[0]
    available_count = max(0, (len(data) - 16) // layout.record_size)
    complete_count = min(declared_count, available_count)
    issue = None
    if available_count < declared_count or (len(data) - 16) % layout.record_size:
        issue = _record_issue("truncated_order_file", "配套 order 文件尾部尚未完整写入", order_path, None, file_hash)
    index: Dict[str, Dict[str, object]] = {}
    for record_index in range(complete_count):
        start = 16 + record_index * layout.record_size
        record = data[start : start + layout.record_size]
        reference = _decode_shifted(record[163:195])
        if not reference:
            continue
        code = record[120:123].decode("ascii", errors="replace").strip(" \x00")
        side = {"1": "买", "3": "卖"}.get(code[:1])
        open_close = {"0": "开", "1": "平"}.get(code[1:2])
        index[reference] = {
            "contract": _decode_shifted(record[32:64]),
            "price": _parse_decimal(_decode_shifted(record[147:163])),
            "side": side,
            "open_close": open_close,
            "parser_version": layout.parser_version,
        }
    return index, issue


def _layout_for_bytes(data: bytes) -> Optional[MatchLayout]:
    if len(data) < 16:
        return None
    declared = struct.unpack_from("<I", data, 8)[0]
    body_size = len(data) - 16
    if declared == 0 and body_size == 0:
        return None
    candidates = []
    for size in (268, 269):
        complete_count = body_size // size
        if body_size >= size and (body_size % size == 0 or (declared > complete_count and complete_count > 0)):
            candidates.append(size)
    if not candidates:
        return None
    return detect_layout(data[:16], candidates[0])


def _signature(values: Dict[str, object]) -> str:
    parts = [
        str(values.get("trade_date") or ""),
        str(values.get("trade_time") or ""),
        str(values.get("exchange") or ""),
        str(values.get("contract") or ""),
        str(values.get("side") or ""),
        str(values.get("open_close") or ""),
        str(values.get("price") or ""),
        str(values.get("quantity") or ""),
    ]
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


def parse_match_records(
    path: Path,
    *,
    account: AccountIdentity,
    source_file: SourceFile,
) -> Tuple[List[FillRecord], List[ParseIssue]]:
    """Read complete match records.  The selected WH6 file is never changed."""
    path = Path(path)
    if source_file.kind != "match" or path.name.lower().find("match") < 0:
        raise ValueError("仅支持 WH6 match 成交缓存")
    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    layout = _layout_for_bytes(data)
    if layout is None:
        raise ValueError("未知或无法验证的 WH6 成交缓存格式")
    order_index, order_issue = _load_order_enrichment(path)
    declared_count = struct.unpack_from("<I", data, 8)[0]
    available_count = max(0, (len(data) - 16) // layout.record_size)
    complete_count = min(declared_count, available_count)
    issues: List[ParseIssue] = []
    if order_issue is not None:
        issues.append(order_issue)
    if available_count < declared_count or (len(data) - 16) % layout.record_size:
        issues.append(_record_issue("truncated_file", "文件尾部尚未完整写入，已暂不读取缺失记录", path, None, file_hash))
    trading_date = _filename_trading_date(path, source_file.trading_date)
    provisional: List[Tuple[Dict[str, object], bytes, int]] = []
    for index in range(complete_count):
        record = data[16 + index * layout.record_size : 16 + (index + 1) * layout.record_size]
        raw_contract = _decode_shifted(record[slice(*layout.contract)])
        contract = normalize_contract(raw_contract)
        timestamp = _decode_shifted(record[slice(*layout.time)])
        record_date, trade_time = _parse_timestamp(timestamp, trading_date)
        quantity = struct.unpack_from("<I", record, layout.quantity_offset)[0]
        price = _parse_decimal(_decode_shifted(record[slice(*layout.price)]))
        side = _decode_shifted(record[slice(*layout.side)])
        open_close = _decode_shifted(record[slice(*layout.open_close)])
        exchange = _decode_shifted(record[slice(*layout.exchange)]).upper()
        reference = _decode_shifted(record[slice(*layout.order_id)])
        order_id = reference or None
        trade_id = _decode_shifted(record[slice(*layout.trade_id)]) or None
        fee = _parse_decimal(_decode_ascii(record[slice(*layout.fee)]))
        close_profit = _parse_decimal(_decode_shifted(record[slice(*layout.close_profit)]))
        order_values = order_index.get(reference, {})
        if order_values:
            side = side or str(order_values.get("side") or "")
            open_close = open_close or str(order_values.get("open_close") or "")
            price = price or order_values.get("price")
            contract = contract or str(order_values.get("contract") or "")
        if not is_option_contract(contract) or not quantity:
            continue
        if not exchange:
            parts = _option_parts(contract)
            exchange = EXCHANGE_BY_UNDERLYING.get(str(parts.get("underlying") or "").lower(), "")
        if (
            not trade_time
            or not price
            or not exchange
            or side not in {"买", "卖", "buy", "sell", "1", "3"}
            or open_close not in {"开", "平", "开仓", "平仓", "0", "1"}
        ):
            issues.append(_record_issue("missing_required_field", "期权成交缺少可验证的时间、价格、买卖、开平或交易所字段", path, index, file_hash))
            continue
        side = {"buy": "买", "sell": "卖", "1": "买", "3": "卖"}.get(side, side)
        open_close = {"0": "开", "1": "平", "开仓": "开", "平仓": "平"}.get(open_close, open_close)
        parts = _option_parts(contract)
        provisional.append(
            (
                {
                    "trade_date": record_date or trading_date,
                    "trade_time": trade_time,
                    "contract": contract,
                    "raw_contract": raw_contract,
                    "side": side,
                    "open_close": open_close,
                    "exchange": exchange,
                    "quantity": quantity,
                    "price": price,
                    "fee": fee,
                    "close_profit": close_profit,
                    "order_id": order_id,
                    "trade_id": trade_id,
                    **parts,
                },
                record,
                index,
            )
        )
    signature_counts: Dict[str, int] = {}
    fills: List[FillRecord] = []
    for values, record, index in provisional:
        signature = _signature(values)
        occurrence = signature_counts.get(signature, 0)
        signature_counts[signature] = occurrence + 1
        trade_id = values.get("trade_id") or ""
        source_event_key = "tradeid:" + str(trade_id).strip().lower() if trade_id else "signature:" + signature + ":" + str(occurrence)
        trade_time = str(values["trade_time"] or "")
        timestamp = (str(values["trade_date"]) + "T" + trade_time + "+08:00") if trade_time else str(values["trade_date"])
        fills.append(
            FillRecord(
                source_event_key=source_event_key,
                account_fingerprint=account.fingerprint,
                trade_date=str(values["trade_date"]),
                trade_time=trade_time,
                trade_timestamp=timestamp,
                exchange=str(values["exchange"]),
                contract=str(values["contract"]),
                raw_contract=str(values["raw_contract"]),
                asset_type="option",
                side=str(values["side"]),
                open_close=str(values["open_close"]),
                quantity=int(values["quantity"]),
                price=str(values["price"] or ""),
                fee=values.get("fee"),
                close_profit=values.get("close_profit"),
                trade_id=str(trade_id) or None,
                order_id=values.get("order_id"),
                option_kind=values.get("option_kind"),
                underlying=values.get("underlying"),
                expiry_month=values.get("expiry_month"),
                strike=values.get("strike"),
                source_path=str(path),
                source_record_index=index,
                source_record_sha256=hashlib.sha256(record).hexdigest(),
                parser_version=layout.parser_version,
                source_version=source_file.validation_reason or None,
                verification_status="pending",
            )
        )
    return fills, issues
