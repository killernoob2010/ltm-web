"""Protected, market-only futures K-line access for the CZSC feasibility gate."""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from .trading_valuation import get_kline_data


router = APIRouter()
SCHEMA_VERSION = 1
ALLOWED_SYMBOLS = {
    "KQ.m@DCE.i",
    "KQ.m@SHFE.hc",
    "KQ.m@SHFE.rb",
}
ALLOWED_DURATIONS = {30 * 60, 60 * 60, 24 * 60 * 60}
MAX_DATA_LENGTH = 2_000


def _require_readonly_secret(authorization: Optional[str]) -> None:
    expected = (os.getenv("FUTURES_MARKET_READONLY_SHARED_SECRET") or "").strip()
    prefix = "Bearer "
    supplied = (
        authorization[len(prefix):].strip()
        if authorization and authorization.startswith(prefix)
        else ""
    )
    if (
        not expected
        or not supplied
        or not hmac.compare_digest(expected, supplied)
    ):
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/internal/futures-market/klines")
def get_futures_market_klines(
    symbol: str = Query(...),
    duration_seconds: int = Query(...),
    data_length: int = Query(500, ge=1, le=MAX_DATA_LENGTH),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    _require_readonly_secret(authorization)
    if symbol not in ALLOWED_SYMBOLS or duration_seconds not in ALLOWED_DURATIONS:
        raise HTTPException(status_code=422, detail="Unsupported market data request")
    try:
        data = get_kline_data(symbol, duration_seconds, data_length)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Market data unavailable") from exc
    rows = data.get("bars") or []
    mapping = data.get("main_contract_mapping") or []
    if not rows or not mapping:
        raise HTTPException(status_code=503, detail="Market data unavailable")
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "tqsdk",
        "read_only": True,
        "requested_symbol": symbol,
        "duration_seconds": duration_seconds,
        "bars_count": len(rows),
        "first_datetime": rows[0]["datetime"],
        "last_datetime": rows[-1]["datetime"],
        "main_contract_mapping": mapping,
        "bars": rows,
    }
