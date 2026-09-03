"""Explicitly versioned WH6 match layouts.

The offsets are isolated here so a newly observed WH6 build can be added only
after a real sample proves the layout.  The parser never falls back to an
unknown record length.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class MatchLayout:
    name: str
    parser_version: str
    record_size: int
    time: tuple[int, int] = (0, 32)
    contract: tuple[int, int] = (32, 64)
    quantity_offset: int = 120
    price: tuple[int, int] = (124, 140)
    order_id: tuple[int, int] = (140, 172)
    side: tuple[int, int] = (172, 176)
    open_close: tuple[int, int] = (176, 180)
    exchange: tuple[int, int] = (180, 204)
    fee: tuple[int, int] = (204, 220)
    close_profit: tuple[int, int] = (220, 236)
    trade_id: tuple[int, int] = (236, 252)


@dataclass(frozen=True)
class OrderLayout:
    """Versioned companion order layout used only to enrich match records."""

    name: str
    parser_version: str
    record_size: int


@dataclass(frozen=True)
class PositionLayout:
    """A position-cache layout proved by an explicit envelope or magic header."""

    name: str
    parser_version: str
    format: str
    header_size: int = 0
    record_size: int = 0
    declared_count_offset: int = 0
    complete_offset: int = 0
    snapshot_epoch_ms_offset: int = 0
    contract: tuple[int, int] = (0, 32)
    direction: tuple[int, int] = (32, 40)
    quantity_offset: int = 40
    today_quantity_offset: int = 44
    yesterday_quantity_offset: int = 48
    average_price: tuple[int, int] = (52, 68)
    exchange: tuple[int, int] = (68, 92)
    hedge_flag: tuple[int, int] = (92, 100)


MATCH_V1 = MatchLayout("match-v1", "wh6-match-v1", 268)
MATCH_V2_PADDED = MatchLayout("match-v2-padded", "wh6-match-v2-padded", 269)
SUPPORTED_MATCH_LAYOUTS: Sequence[MatchLayout] = (MATCH_V1, MATCH_V2_PADDED)

ORDER_V1 = OrderLayout("order-v1", "wh6-order-v1", 231)
ORDER_V2_PADDED = OrderLayout("order-v2-padded", "wh6-order-v2-padded", 232)
SUPPORTED_ORDER_LAYOUTS: Sequence[OrderLayout] = (ORDER_V1, ORDER_V2_PADDED)

POSITION_MAGIC = b"WH6POS1\0"
POSITION_JSON_V1 = PositionLayout("position-json-v1", "wh6-position-json-v1", "json")
POSITION_BINARY_V1 = PositionLayout(
    "position-binary-v1",
    "wh6-position-binary-v1",
    "binary",
    header_size=32,
    record_size=256,
    declared_count_offset=12,
    complete_offset=24,
    snapshot_epoch_ms_offset=16,
)
POSITION_BINARY_V1_PADDED = PositionLayout(
    "position-binary-v1-padded",
    "wh6-position-binary-v1-padded",
    "binary",
    header_size=32,
    record_size=257,
    declared_count_offset=12,
    complete_offset=24,
    snapshot_epoch_ms_offset=16,
)
SUPPORTED_POSITION_BINARY_LAYOUTS: Sequence[PositionLayout] = (
    POSITION_BINARY_V1,
    POSITION_BINARY_V1_PADDED,
)


def detect_layout(header: bytes, record_size: int) -> Optional[MatchLayout]:
    """Select only a registered layout with a structurally valid WH6 header."""
    if len(header) < 16 or record_size <= 0:
        return None
    for layout in SUPPORTED_MATCH_LAYOUTS:
        if layout.record_size == record_size:
            return layout
    return None


def detect_order_layout(record_size: int) -> Optional[OrderLayout]:
    """Select only a registered companion order layout."""
    for layout in SUPPORTED_ORDER_LAYOUTS:
        if layout.record_size == record_size:
            return layout
    return None


def detect_position_layout(data: bytes) -> Optional[PositionLayout]:
    """Return a position layout only for the explicit registered envelopes."""
    stripped = data.lstrip()
    if stripped.startswith(b"{"):
        try:
            import json

            envelope = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
        if isinstance(envelope, dict) and envelope.get("format") == "wh6-position-v1" and envelope.get("version", 1) == 1:
            return POSITION_JSON_V1
        return None
    if len(data) < POSITION_BINARY_V1.header_size or not data.startswith(POSITION_MAGIC):
        return None
    version = int.from_bytes(data[8:12], "little")
    if version != 1:
        return None
    declared = int.from_bytes(data[POSITION_BINARY_V1.declared_count_offset:POSITION_BINARY_V1.declared_count_offset + 4], "little")
    body_size = max(0, len(data) - POSITION_BINARY_V1.header_size)
    if declared == 0 and body_size == 0:
        return POSITION_BINARY_V1
    for layout in SUPPORTED_POSITION_BINARY_LAYOUTS:
        if body_size == declared * layout.record_size:
            return layout
    # A recognized header with a short body still has a known layout, so the
    # parser can report a quarantinable truncation instead of guessing rows.
    return POSITION_BINARY_V1
