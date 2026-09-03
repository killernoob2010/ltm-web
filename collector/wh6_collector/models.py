"""Small, JSON-safe value objects shared by the collector modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class AccountIdentity:
    account_code: str
    display_name: str
    masked_label: str
    stable_id: Optional[str] = None
    fingerprint: Optional[str] = None
    binding_mode: str = "strong"
    confirmed: bool = False
    requires_manual_confirmation: bool = False

    def to_payload(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceFile:
    path: Path
    kind: str = "match"
    label: str = "WH6 成交缓存"
    account_clue: str = ""
    modified_ns: int = 0
    record_size: Optional[int] = None
    validation_reason: str = ""
    readable: bool = True
    trading_date: Optional[str] = None
    root_path: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["path"] = str(self.path)
        return payload


@dataclass(frozen=True)
class ParseIssue:
    code: str
    message: str
    path: str = ""
    record_index: Optional[int] = None
    file_sha256: str = ""
    severity: str = "warning"

    def to_payload(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FillRecord:
    source_event_key: str
    account_fingerprint: Optional[str]
    trade_date: str
    trade_time: str
    trade_timestamp: str
    exchange: str
    contract: str
    raw_contract: str
    asset_type: str
    side: str
    open_close: str
    quantity: int
    price: str
    fee: Optional[str] = None
    turnover: Optional[str] = None
    premium_cashflow: Optional[str] = None
    close_profit: Optional[str] = None
    trade_id: Optional[str] = None
    order_id: Optional[str] = None
    option_kind: Optional[str] = None
    underlying: Optional[str] = None
    expiry_month: Optional[str] = None
    strike: Optional[str] = None
    source_path: str = ""
    source_record_index: int = 0
    source_record_sha256: str = ""
    parser_version: str = ""
    source_version: Optional[str] = None
    data_status: str = "provisional"
    verification_status: str = "pending"
    observed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_payload(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionRow:
    """One row from a complete, read-only WH6 position snapshot."""

    contract: str
    raw_contract: str
    asset_type: str
    exchange: str
    direction: str
    quantity: int
    today_quantity: Optional[int] = None
    yesterday_quantity: Optional[int] = None
    average_price: Optional[str] = None
    hedge_flag: Optional[str] = None
    option_kind: Optional[str] = None
    underlying: Optional[str] = None
    expiry_month: Optional[str] = None
    strike: Optional[str] = None
    source_record_index: int = 0
    source_record_sha256: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PositionSnapshot:
    """A complete account snapshot; rows are never additive across devices."""

    source_snapshot_key: str
    account_fingerprint: Optional[str]
    trade_date: str
    snapshot_time: str
    snapshot_timestamp: str
    rows: Tuple[PositionRow, ...]
    complete: bool
    source_path: str = ""
    source_snapshot_sha256: str = ""
    parser_version: str = ""
    data_status: str = "provisional"
    verification_status: str = "pending"
    source_version: Optional[str] = None
    observed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_payload(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["rows"] = [row.to_payload() for row in self.rows]
        return payload


@dataclass(frozen=True)
class CollectorStatus:
    state: str
    message: str = ""
    account_label: str = ""
    source_path: str = ""
    last_scan_at: Optional[str] = None
    last_upload_at: Optional[str] = None
    queued_count: int = 0
    accepted_count: int = 0
    issue_count: int = 0

    def to_payload(self) -> Dict[str, Any]:
        return asdict(self)
