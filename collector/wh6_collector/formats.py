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


MATCH_V1 = MatchLayout("match-v1", "wh6-match-v1", 268)
MATCH_V2_PADDED = MatchLayout("match-v2-padded", "wh6-match-v2-padded", 269)
SUPPORTED_MATCH_LAYOUTS: Sequence[MatchLayout] = (MATCH_V1, MATCH_V2_PADDED)

ORDER_V1 = OrderLayout("order-v1", "wh6-order-v1", 231)
ORDER_V2_PADDED = OrderLayout("order-v2-padded", "wh6-order-v2-padded", 232)
SUPPORTED_ORDER_LAYOUTS: Sequence[OrderLayout] = (ORDER_V1, ORDER_V2_PADDED)


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
