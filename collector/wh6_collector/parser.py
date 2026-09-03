"""Read-only, conservative decoder for WH6 match/fill cache files."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from .formats import (
    MatchLayout,
    PositionLayout,
    detect_layout,
    detect_order_layout,
    detect_position_layout,
)
from .models import AccountIdentity, FillRecord, ParseIssue, PositionRow, PositionSnapshot, SourceFile

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
FUTURE_RE = re.compile(r"^[a-z]{1,8}\d{3,6}$", re.IGNORECASE)
DATE_RE = re.compile(r"(?P<date>\d{8})(?:[^/]*)match(?:[-_][^/]*)?\.dat$", re.IGNORECASE)
ANY_DATE_RE = re.compile(r"(?P<date>\d{8})")

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


def classify_contract(contract: str) -> Optional[str]:
    normalized = normalize_contract(contract)
    if OPTION_RE.match(normalized):
        return "option"
    if FUTURE_RE.match(normalized):
        return "future"
    return None


def is_option_contract(contract: str) -> bool:
    return classify_contract(contract) == "option"


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


def _source_trading_date(path: Path, source_file: SourceFile) -> str:
    if source_file.trading_date:
        return source_file.trading_date
    match = ANY_DATE_RE.search(path.name)
    if match:
        return datetime.strptime(match.group("date"), "%Y%m%d").date().isoformat()
    return ""


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
    asset_types: Optional[Sequence[str]] = None,
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
    allowed_asset_types = set(asset_types or ("future", "option"))
    unknown_asset_types = allowed_asset_types - {"future", "option"}
    if unknown_asset_types:
        raise ValueError("不支持的成交资产类型过滤: %s" % ",".join(sorted(unknown_asset_types)))
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
        asset_type = classify_contract(contract)
        if asset_type not in allowed_asset_types or not quantity:
            continue
        if not exchange:
            parts = _option_parts(contract)
            underlying = str(parts.get("underlying") or "")
            if not underlying:
                future_match = FUTURE_RE.match(normalize_contract(contract))
                underlying = future_match.group(0).rstrip("0123456789") if future_match else ""
            exchange = EXCHANGE_BY_UNDERLYING.get(underlying.lower(), "")
        if (
            not trade_time
            or not price
            or not exchange
            or side not in {"买", "卖", "buy", "sell", "1", "3"}
            or open_close not in {"开", "平", "开仓", "平仓", "0", "1"}
        ):
            issues.append(_record_issue("missing_required_field", "成交缺少可验证的时间、价格、买卖、开平或交易所字段", path, index, file_hash))
            continue
        side = {"buy": "买", "sell": "卖", "1": "买", "3": "卖"}.get(side, side)
        open_close = {"0": "开", "1": "平", "开仓": "开", "平仓": "平"}.get(open_close, open_close)
        parts = _option_parts(contract) if asset_type == "option" else {}
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
                    "asset_type": asset_type,
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
        if trade_id:
            source_event_key = "tradeid:%s:%s:%s" % (
                str(values["trade_date"]),
                str(values["exchange"]).strip().lower(),
                str(trade_id).strip().lower(),
            )
        else:
            source_event_key = "signature:" + signature + ":" + str(occurrence)
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
                asset_type=str(values["asset_type"]),
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


def _position_issue(code: str, message: str, path: Path, index: Optional[int], file_hash: str, *, severity="warning") -> ParseIssue:
    return ParseIssue(code=code, message=message, path=str(path), record_index=index, file_sha256=file_hash, severity=severity)


def _parse_position_timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def _nonnegative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _canonical_position_row(
    raw: Mapping[str, Any],
    *,
    index: int,
    path: Path,
    file_hash: str,
) -> Tuple[Optional[PositionRow], Optional[ParseIssue]]:
    raw_contract = str(raw.get("raw_contract") or raw.get("contract") or "").strip()
    contract = normalize_contract(str(raw.get("contract") or raw_contract))
    asset_type = classify_contract(contract)
    direction_value = str(raw.get("direction") or raw.get("side") or "").strip().lower()
    direction = {
        "多": "long",
        "空": "short",
        "long": "long",
        "short": "short",
        "buy": "long",
        "sell": "short",
        "1": "long",
        "2": "short",
    }.get(direction_value)
    quantity = _nonnegative_int(raw.get("quantity"))
    today_quantity = _nonnegative_int(raw.get("today_quantity")) if raw.get("today_quantity") is not None else None
    yesterday_quantity = _nonnegative_int(raw.get("yesterday_quantity")) if raw.get("yesterday_quantity") is not None else None
    if raw.get("today_quantity") is not None and today_quantity is None:
        quantity = None
    if raw.get("yesterday_quantity") is not None and yesterday_quantity is None:
        quantity = None
    exchange = str(raw.get("exchange") or "").strip().upper()
    if not exchange:
        parts = _option_parts(contract)
        underlying = str(parts.get("underlying") or "")
        if not underlying:
            future_match = FUTURE_RE.match(contract)
            underlying = future_match.group(0).rstrip("0123456789") if future_match else ""
        exchange = EXCHANGE_BY_UNDERLYING.get(underlying.lower(), "")
    average_price = _parse_decimal(str(raw.get("average_price") or "")) if raw.get("average_price") is not None else None
    if asset_type is None or not direction or quantity is None or not exchange:
        return None, _position_issue(
            "invalid_position_row",
            "持仓快照含有无法验证的合约、方向、数量或交易所字段，整份快照不入队",
            path,
            index,
            file_hash,
            severity="error",
        )
    parts = _option_parts(contract) if asset_type == "option" else {}
    row_payload = dict(raw)
    row_payload.update(
        {
            "contract": contract,
            "raw_contract": raw_contract,
            "asset_type": asset_type,
            "exchange": exchange,
            "direction": direction,
            "quantity": quantity,
            "today_quantity": today_quantity,
            "yesterday_quantity": yesterday_quantity,
            "average_price": average_price,
            **parts,
        }
    )
    return (
        PositionRow(
            contract=contract,
            raw_contract=raw_contract,
            asset_type=asset_type,
            exchange=exchange,
            direction=direction,
            quantity=quantity,
            today_quantity=today_quantity,
            yesterday_quantity=yesterday_quantity,
            average_price=average_price,
            hedge_flag=str(raw.get("hedge_flag") or "").strip() or None,
            option_kind=parts.get("option_kind"),
            underlying=parts.get("underlying"),
            expiry_month=parts.get("expiry_month"),
            strike=parts.get("strike"),
            source_record_index=index,
            source_record_sha256=hashlib.sha256(
                json.dumps(row_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        ),
        None,
    )


def _position_snapshot_from_rows(
    *,
    path: Path,
    source_file: SourceFile,
    account: AccountIdentity,
    file_hash: str,
    parser_version: str,
    snapshot_value: Any,
    trade_date: str,
    raw_rows: Sequence[Any],
    complete: bool,
    source_snapshot_key: Optional[str] = None,
) -> Tuple[Optional[PositionSnapshot], List[ParseIssue]]:
    issues: List[ParseIssue] = []
    if not complete:
        issues.append(_position_issue("incomplete_position_snapshot", "持仓缓存未写入完整结束标记，暂停本次快照", path, None, file_hash, severity="error"))
        return None, issues
    parsed_time = _parse_position_timestamp(snapshot_value)
    if parsed_time is None:
        issues.append(_position_issue("missing_snapshot_time", "持仓快照缺少可验证的快照时间", path, None, file_hash, severity="error"))
        return None, issues
    if not isinstance(raw_rows, (list, tuple)):
        issues.append(_position_issue("invalid_position_rows", "持仓快照 rows 不是列表，整份快照不入队", path, None, file_hash, severity="error"))
        return None, issues
    rows: List[PositionRow] = []
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            issues.append(_position_issue("invalid_position_row", "持仓快照行不是对象，整份快照不入队", path, index, file_hash, severity="error"))
            continue
        row, issue = _canonical_position_row(raw, index=index, path=path, file_hash=file_hash)
        if issue:
            issues.append(issue)
        elif row:
            rows.append(row)
    if issues:
        return None, issues
    timestamp = parsed_time.isoformat()
    key = str(source_snapshot_key or "").strip() or "snapshot:" + timestamp
    return (
        PositionSnapshot(
            source_snapshot_key=key,
            account_fingerprint=account.fingerprint,
            trade_date=trade_date,
            snapshot_time=parsed_time.strftime("%H:%M:%S"),
            snapshot_timestamp=timestamp,
            rows=tuple(rows),
            complete=True,
            source_path=str(path),
            source_snapshot_sha256=file_hash,
            parser_version=parser_version,
            source_version=source_file.validation_reason or None,
        ),
        issues,
    )


def _parse_position_json(
    data: bytes,
    *,
    path: Path,
    source_file: SourceFile,
    account: AccountIdentity,
    file_hash: str,
) -> Tuple[Optional[PositionSnapshot], List[ParseIssue]]:
    try:
        envelope = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("WH6 持仓 JSON 缓存无法解析") from exc
    if not isinstance(envelope, Mapping) or envelope.get("format") != "wh6-position-v1":
        raise ValueError("未知或无法验证的 WH6 持仓缓存格式")
    raw_rows = envelope.get("rows")
    if not isinstance(raw_rows, list):
        return None, [_position_issue("invalid_position_rows", "持仓快照 rows 不是列表，整份快照不入队", path, None, file_hash, severity="error")]
    declared = envelope.get("declared_count")
    if declared is not None and declared != len(raw_rows):
        return None, [_position_issue("truncated_position_snapshot", "持仓快照声明数量与实际行数不一致", path, None, file_hash, severity="error")]
    snapshot_time = envelope.get("snapshot_at") or envelope.get("snapshot_timestamp")
    trade_date = str(envelope.get("trade_date") or _source_trading_date(path, source_file) or "")
    parsed = _parse_position_timestamp(snapshot_time)
    if not trade_date and parsed:
        trade_date = business_trading_day(parsed)
    if not trade_date:
        return None, [_position_issue("missing_trade_date", "持仓快照缺少可验证的交易日", path, None, file_hash, severity="error")]
    return _position_snapshot_from_rows(
        path=path,
        source_file=source_file,
        account=account,
        file_hash=file_hash,
        parser_version="wh6-position-json-v1",
        snapshot_value=snapshot_time,
        trade_date=trade_date,
        raw_rows=raw_rows,
        complete=envelope.get("complete") is True,
        source_snapshot_key=envelope.get("source_snapshot_key"),
    )


def _parse_position_binary(
    data: bytes,
    *,
    layout: PositionLayout,
    path: Path,
    source_file: SourceFile,
    account: AccountIdentity,
    file_hash: str,
) -> Tuple[Optional[PositionSnapshot], List[ParseIssue]]:
    declared = struct.unpack_from("<I", data, layout.declared_count_offset)[0]
    body = data[layout.header_size:]
    expected_size = declared * layout.record_size
    if len(body) != expected_size:
        return None, [_position_issue("truncated_position_snapshot", "持仓快照尾部尚未完整写入", path, None, file_hash, severity="error")]
    complete = bool(struct.unpack_from("<I", data, layout.complete_offset)[0])
    if not complete:
        return None, [_position_issue("incomplete_position_snapshot", "持仓缓存未写入完整结束标记，暂停本次快照", path, None, file_hash, severity="error")]
    epoch_ms = struct.unpack_from("<q", data, layout.snapshot_epoch_ms_offset)[0]
    snapshot_time = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(SHANGHAI).isoformat()
    raw_rows: List[Dict[str, Any]] = []
    for index in range(declared):
        start = layout.header_size + index * layout.record_size
        record = data[start:start + layout.record_size]
        raw_rows.append(
            {
                "contract": _decode_shifted(record[slice(*layout.contract)]),
                "direction": _decode_shifted(record[slice(*layout.direction)]),
                "quantity": struct.unpack_from("<I", record, layout.quantity_offset)[0],
                "today_quantity": struct.unpack_from("<I", record, layout.today_quantity_offset)[0],
                "yesterday_quantity": struct.unpack_from("<I", record, layout.yesterday_quantity_offset)[0],
                "average_price": _decode_shifted(record[slice(*layout.average_price)]),
                "exchange": _decode_shifted(record[slice(*layout.exchange)]),
                "hedge_flag": _decode_shifted(record[slice(*layout.hedge_flag)]),
            }
        )
    trade_date = _source_trading_date(path, source_file) or business_trading_day(datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc))
    return _position_snapshot_from_rows(
        path=path,
        source_file=source_file,
        account=account,
        file_hash=file_hash,
        parser_version=layout.parser_version,
        snapshot_value=snapshot_time,
        trade_date=trade_date,
        raw_rows=raw_rows,
        complete=True,
    )


def parse_position_snapshot(
    path: Path,
    *,
    account: AccountIdentity,
    source_file: SourceFile,
) -> Tuple[Optional[PositionSnapshot], List[ParseIssue]]:
    """Parse one complete, explicitly registered WH6 position cache."""
    path = Path(path)
    if source_file.kind != "position":
        raise ValueError("仅支持 WH6 position 持仓缓存")
    data = path.read_bytes()
    file_hash = hashlib.sha256(data).hexdigest()
    layout = detect_position_layout(data)
    if layout is None:
        raise ValueError("未知或无法验证的 WH6 持仓缓存格式")
    if layout.format == "json":
        return _parse_position_json(data, path=path, source_file=source_file, account=account, file_hash=file_hash)
    return _parse_position_binary(
        data,
        layout=layout,
        path=path,
        source_file=source_file,
        account=account,
        file_hash=file_hash,
    )
