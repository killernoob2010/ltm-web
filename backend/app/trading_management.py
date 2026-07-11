"""交易管理模块。

P0 只读事实、业务归类和业务开平关系均通过本独立路由扩展。
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from openpyxl import load_workbook

from . import db


router = APIRouter()

TRADING_MODULES = {
    "overview": "trading_overview",
    "positions": "trading_positions",
    "junneng": "trading_sh_junneng",
    "options": "trading_options",
    "export": "trading_export",
}

TRADE_REQUIRED_HEADERS = {"交易所", "合约", "买卖", "开平", "手数", "成交价", "手续费"}
CLOSE_REQUIRED_HEADERS = {"交易所", "合约", "开仓日期", "买入", "卖出", "手数", "价格", "逐笔平仓盈亏"}
POSITION_REQUIRED_HEADERS = {"交易所", "开仓日期", "合约", "买卖", "手数", "价格", "占用保证金"}


def _text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _is_date_marker(value: Any) -> bool:
    text = _text(value)
    return len(text) == 8 and text.isdigit() and text.startswith("20")


def _asset_type(contract: str) -> str:
    normalized = contract.upper()
    return "option" if "-C-" in normalized or "-P-" in normalized else "future"


def _open_close(value: Any) -> str:
    text = _text(value)
    if text.startswith("开"):
        return "开仓"
    if text.startswith("平"):
        return "平仓"
    return text


def _read_grouped_sheet(path: Path, sheet_name: str, required_headers: set[str]) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"缺少必要工作表：{sheet_name}")
        sheet = workbook[sheet_name]
        rows = sheet.iter_rows(values_only=True)
        try:
            header_values = next(rows)
        except StopIteration as exc:
            raise ValueError(f"工作表为空：{sheet_name}") from exc
        headers = [_text(value) for value in header_values]
        missing = sorted(required_headers - set(headers))
        if missing:
            raise ValueError(f"{sheet_name}缺少必要字段：{', '.join(missing)}")
        current_date = ""
        records: list[dict[str, Any]] = []
        for row_no, values in enumerate(rows, start=2):
            if _is_date_marker(values[0] if values else None):
                current_date = _text(values[0])
                continue
            if not any(value not in (None, "") for value in values):
                continue
            record = {
                header: values[index] if index < len(values) else None
                for index, header in enumerate(headers)
                if header
            }
            record["_date"] = current_date
            record["_row_no"] = row_no
            records.append(record)
        return records
    finally:
        workbook.close()


def _raw_data(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in row.items():
        if key.startswith("_"):
            continue
        result[key] = _text(value) if isinstance(value, (date, datetime)) else value
    return result


def parse_trade_workbook(path: Path) -> list[dict[str, Any]]:
    rows = _read_grouped_sheet(Path(path), "成交记录", TRADE_REQUIRED_HEADERS)
    result = []
    for row in rows:
        contract = _text(row.get("合约"))
        if not contract:
            continue
        result.append(
            {
                "date": row["_date"],
                "trade_time": _text(row.get("成交时间")),
                "exchange": _text(row.get("交易所")).upper(),
                "contract": contract.lower(),
                "asset_type": _asset_type(contract),
                "side": _text(row.get("买卖")),
                "open_close_raw": _text(row.get("开平")),
                "open_close": _open_close(row.get("开平")),
                "quantity": _number(row.get("手数")),
                "price": _number(row.get("成交价")),
                "turnover": _number(row.get("成交额")),
                "source_close_pnl": _number(row.get("平仓盈亏")),
                "fee": _number(row.get("手续费")),
                "hedge_flag": _text(row.get("投保")),
                "premium_cashflow": _number(row.get("权利金收支")),
                "source_row_no": row["_row_no"],
                "raw_data": _raw_data(row),
            }
        )
    return result


def parse_close_workbook(path: Path) -> list[dict[str, Any]]:
    rows = _read_grouped_sheet(Path(path), "平仓记录", CLOSE_REQUIRED_HEADERS)
    result = []
    pending: Optional[dict[str, Any]] = None
    for row in rows:
        contract = _text(row.get("合约"))
        if contract:
            pending = row
            continue
        if pending is None or not (row.get("买入") or row.get("卖出")):
            continue
        contract = _text(pending.get("合约"))
        open_side = "买" if pending.get("买入") not in (None, "", 0) else "卖"
        close_side = "买" if row.get("买入") not in (None, "", 0) else "卖"
        result.append(
            {
                "open_date": _text(pending.get("开仓日期")),
                "close_date": row["_date"],
                "exchange": _text(pending.get("交易所")).upper(),
                "contract": contract.lower(),
                "asset_type": _asset_type(contract),
                "open_side": open_side,
                "close_side": close_side,
                "quantity": _number(row.get("手数")),
                "open_price": _number(pending.get("价格")),
                "close_price": _number(row.get("价格")),
                "fact_close_pnl": _number(row.get("逐笔平仓盈亏")),
                "mark_close_pnl": _number(row.get("盯市平仓盈亏")),
                "fee": _number(row.get("手续费"), None),
                "premium_cashflow": _number(row.get("权利金收支")),
                "source_row_no": row["_row_no"],
                "raw_data": {"open": _raw_data(pending), "close": _raw_data(row)},
            }
        )
        pending = None
    return result


def parse_position_workbook(path: Path) -> list[dict[str, Any]]:
    rows = _read_grouped_sheet(Path(path), "期末持仓", POSITION_REQUIRED_HEADERS)
    result = []
    for row in rows:
        contract = _text(row.get("合约"))
        if not contract:
            continue
        result.append(
            {
                "snapshot_date": row["_date"],
                "exchange": _text(row.get("交易所")).upper(),
                "open_date": _text(row.get("开仓日期")),
                "contract": contract.lower(),
                "asset_type": _asset_type(contract),
                "direction": _text(row.get("买卖")),
                "quantity": _number(row.get("手数")),
                "average_price": _number(row.get("价格")),
                "source_floating_pnl": _number(row.get("浮动盈亏"), None),
                "mark_pnl": _number(row.get("盯市盈亏"), None),
                "margin": _number(row.get("占用保证金")),
                "hedge_flag": _text(row.get("投保")),
                "valuation_price": None,
                "floating_pnl": None,
                "valuation_status": "pending_calculation",
                "source_row_no": row["_row_no"],
                "raw_data": _raw_data(row),
            }
        )
    return result


def _canonical(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float, Decimal)) or str(value).replace(".", "", 1).isdigit():
        try:
            decimal = Decimal(str(value).replace(",", "").strip()).normalize()
            return format(decimal, "f")
        except InvalidOperation:
            pass
    return _text(value).strip().lower()


SIGNATURE_FIELDS = {
    "trade": ("date", "trade_time", "exchange", "contract", "side", "open_close_raw", "quantity", "price", "turnover", "fee"),
    "close": ("open_date", "close_date", "exchange", "contract", "open_side", "close_side", "quantity", "open_price", "close_price", "fact_close_pnl"),
    "position": ("snapshot_date", "exchange", "contract", "direction", "open_date", "quantity", "average_price", "margin"),
}


def build_fact_signature(fact_type: str, account_code: str, row: dict[str, Any]) -> str:
    if fact_type not in SIGNATURE_FIELDS:
        raise ValueError(f"未知事实类型：{fact_type}")
    values = [_canonical(account_code)] + [_canonical(row.get(field)) for field in SIGNATURE_FIELDS[fact_type]]
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preview_trading_import(
    account_id: int,
    trade_path: Optional[Path],
    close_path: Optional[Path],
    position_path: Optional[Path],
    actor: str,
) -> dict[str, Any]:
    if not trade_path or not close_path or not position_path:
        raise ValueError("成交、平仓、持仓三表必须齐全")
    trade_path, close_path, position_path = map(Path, (trade_path, close_path, position_path))
    trades = parse_trade_workbook(trade_path)
    closes = parse_close_workbook(close_path)
    positions = parse_position_workbook(position_path)
    dates = [row["date"] for row in trades] + [row["close_date"] for row in closes] + [row["snapshot_date"] for row in positions]
    dates = [value for value in dates if value]
    range_start = min(dates) if dates else None
    range_end = max(dates) if dates else None
    snapshot_dates = [row["snapshot_date"] for row in positions if row["snapshot_date"]]
    summary = {
        "paths": {"trade": str(trade_path), "close": str(close_path), "position": str(position_path)},
        "counts": {"trade": len(trades), "close": len(closes), "position": len(positions)},
        "range_start": range_start,
        "range_end": range_end,
    }
    with db.connect() as conn:
        cur = conn.cursor()
        account = db._exec(
            cur,
            "SELECT id FROM trading_accounts WHERE id = ? AND is_active = 1",
            (account_id,),
        ).fetchone()
        if not account:
            raise ValueError("交易账户不存在或已停用")
        overlaps = (
            db._exec(
                cur,
                """
                SELECT id, range_start, range_end FROM trading_import_batches
                WHERE account_id = ? AND status = 'active'
                  AND NOT (range_end < ? OR range_start > ?)
                """,
                (account_id, range_start, range_end),
            ).fetchall()
            if range_start and range_end
            else []
        )
        if any(row["range_start"] != range_start or row["range_end"] != range_end for row in overlaps):
            raise ValueError("新批次与有效批次日期范围部分重叠")
        batch_id = db._last_insert_id(
            cur,
            """
            INSERT INTO trading_import_batches
                (account_id, range_start, range_end, position_snapshot_date, status,
                 trade_file_name, close_file_name, position_file_name,
                 trade_file_sha256, close_file_sha256, position_file_sha256,
                 trade_count, close_count, position_count, parse_summary, created_by)
            VALUES (?, ?, ?, ?, 'preview', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id, range_start, range_end, max(snapshot_dates) if snapshot_dates else None,
                trade_path.name, close_path.name, position_path.name,
                _file_sha256(trade_path), _file_sha256(close_path), _file_sha256(position_path),
                len(trades), len(closes), len(positions), json.dumps(summary, ensure_ascii=False), actor,
            ),
        )
        conn.commit()
    return {"preview_batch_id": batch_id, **summary}


def _rows_with_stable_keys(fact_type: str, account_code: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signatures = [build_fact_signature(fact_type, account_code, row) for row in rows]
    counts: dict[str, int] = {}
    totals: dict[str, int] = {}
    for signature in signatures:
        totals[signature] = totals.get(signature, 0) + 1
    result = []
    for row, signature in zip(rows, signatures):
        counts[signature] = counts.get(signature, 0) + 1
        stable_key = signature if totals[signature] == 1 else f"{signature}#{counts[signature]}"
        result.append({**row, "base_signature": signature, "stable_key": stable_key})
    return result


def _identity_id(cur, account_id: int, fact_type: str, stable_key: str) -> int:
    existing = db._exec(
        cur,
        "SELECT id FROM trading_fact_identities WHERE account_id = ? AND fact_type = ? AND stable_key = ?",
        (account_id, fact_type, stable_key),
    ).fetchone()
    if existing:
        return existing["id"]
    return db._last_insert_id(
        cur,
        "INSERT INTO trading_fact_identities (account_id, fact_type, stable_key) VALUES (?, ?, ?)",
        (account_id, fact_type, stable_key),
    )


def _insert_source_row(cur, batch_id: int, source_type: str, source_file: str, source_sheet: str, row: dict[str, Any]) -> int:
    raw_json = json.dumps(row["raw_data"], ensure_ascii=False, sort_keys=True)
    return db._last_insert_id(
        cur,
        """
        INSERT INTO trading_source_rows
            (batch_id, source_type, source_file, source_sheet, source_row_no, raw_hash, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            source_type,
            source_file,
            source_sheet,
            row["source_row_no"],
            hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
            raw_json,
        ),
    )


def confirm_trading_import(preview_batch_id: int, actor: str) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        batch = db._exec(
            cur,
            "SELECT * FROM trading_import_batches WHERE id = ?",
            (preview_batch_id,),
        ).fetchone()
        if not batch or batch["status"] != "preview":
            raise ValueError("预览批次已确认或不可用")
        summary = json.loads(batch["parse_summary"] or "{}")
        paths = {key: Path(value) for key, value in summary.get("paths", {}).items()}
        if set(paths) != {"trade", "close", "position"}:
            raise ValueError("预览批次缺少三表文件信息")
        expected_hashes = {
            "trade": batch["trade_file_sha256"],
            "close": batch["close_file_sha256"],
            "position": batch["position_file_sha256"],
        }
        if any(not paths[key].exists() or _file_sha256(paths[key]) != expected_hashes[key] for key in paths):
            raise ValueError("预览文件已变化，请重新预览")

        account = db._exec(
            cur,
            "SELECT account_code FROM trading_accounts WHERE id = ? AND is_active = 1",
            (batch["account_id"],),
        ).fetchone()
        if not account:
            raise ValueError("交易账户不存在或已停用")
        account_code = account["account_code"]
        trades = _rows_with_stable_keys("trade", account_code, parse_trade_workbook(paths["trade"]))
        closes = _rows_with_stable_keys("close", account_code, parse_close_workbook(paths["close"]))
        positions = _rows_with_stable_keys("position", account_code, parse_position_workbook(paths["position"]))

        previous = db._exec(
            cur,
            """
            SELECT id FROM trading_import_batches
            WHERE account_id = ? AND status = 'active' AND range_start = ? AND range_end = ? AND id <> ?
            """,
            (batch["account_id"], batch["range_start"], batch["range_end"], preview_batch_id),
        ).fetchone()

        for row in trades:
            source_row_id = _insert_source_row(cur, preview_batch_id, "trade", batch["trade_file_name"], "成交记录", row)
            identity_id = _identity_id(cur, batch["account_id"], "trade", row["stable_key"])
            inheritance_review = None
            if previous:
                inheritance_review = db._exec(
                    cur,
                    """
                    SELECT old_tf.identity_id
                    FROM trading_trade_facts old_tf
                    JOIN trading_source_rows old_sr ON old_sr.id = old_tf.source_row_id
                    JOIN trading_business_assignments old_ba ON old_ba.trade_identity_id = old_tf.identity_id
                    WHERE old_tf.batch_id = ? AND old_sr.source_row_no = ?
                      AND old_tf.trade_date = ? AND old_tf.exchange = ? AND old_tf.contract = ?
                      AND old_tf.side = ? AND old_tf.open_close = ? AND old_tf.identity_id <> ?
                    LIMIT 1
                    """,
                    (
                        previous["id"], row["source_row_no"], row["date"], row["exchange"],
                        row["contract"], row["side"], row["open_close"], identity_id,
                    ),
                ).fetchone()
            verification_status = "inheritance_review_required" if inheritance_review else "file_imported"
            db._exec(
                cur,
                """
                INSERT INTO trading_trade_facts
                    (identity_id, batch_id, source_row_id, trade_date, trade_time, exchange, contract,
                     asset_type, side, open_close_raw, open_close, quantity, price, turnover, fee,
                     hedge_flag, premium_cashflow, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity_id, preview_batch_id, source_row_id, row["date"], row["trade_time"],
                    row["exchange"], row["contract"], row["asset_type"], row["side"],
                    row["open_close_raw"], row["open_close"], row["quantity"], row["price"],
                    row["turnover"], row["fee"], row["hedge_flag"], row["premium_cashflow"],
                    verification_status,
                ),
            )

        for row in closes:
            source_row_id = _insert_source_row(cur, preview_batch_id, "close", batch["close_file_name"], "平仓记录", row)
            identity_id = _identity_id(cur, batch["account_id"], "close", row["stable_key"])
            db._exec(
                cur,
                """
                INSERT INTO trading_close_facts
                    (identity_id, batch_id, source_row_id, open_date, close_date, exchange, contract,
                     asset_type, open_side, close_side, quantity, open_price, close_price,
                     fact_close_pnl, matched_fee, fee_status, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'file_imported')
                """,
                (
                    identity_id, preview_batch_id, source_row_id, row["open_date"], row["close_date"],
                    row["exchange"], row["contract"], row["asset_type"], row["open_side"],
                    row["close_side"], row["quantity"], row["open_price"], row["close_price"],
                    row["fact_close_pnl"], row["fee"], "matched" if row["fee"] is not None else "pending_match",
                ),
            )

        for row in positions:
            source_row_id = _insert_source_row(cur, preview_batch_id, "position", batch["position_file_name"], "期末持仓", row)
            identity_id = _identity_id(cur, batch["account_id"], "position", row["stable_key"])
            db._exec(
                cur,
                """
                INSERT INTO trading_position_snapshots
                    (identity_id, batch_id, source_row_id, snapshot_date, exchange, contract, asset_type,
                     direction, open_date, quantity, average_price, margin, valuation_price, floating_pnl,
                     valuation_status, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'file_imported')
                """,
                (
                    identity_id, preview_batch_id, source_row_id, row["snapshot_date"], row["exchange"],
                    row["contract"], row["asset_type"], row["direction"], row["open_date"],
                    row["quantity"], row["average_price"], row["margin"], row["valuation_price"],
                    row["floating_pnl"], row["valuation_status"],
                ),
            )

        if previous:
            db._exec(cur, "UPDATE trading_import_batches SET status = 'superseded' WHERE id = ?", (previous["id"],))
        db._exec(
            cur,
            """
            UPDATE trading_import_batches
            SET status = 'active', supersedes_batch_id = ?, confirmed_by = ?, confirmed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (previous["id"] if previous else None, actor, preview_batch_id),
        )
        conn.commit()
    return {
        "batch_id": preview_batch_id,
        "status": "active",
        "counts": {"trade": len(trades), "close": len(closes), "position": len(positions)},
        "supersedes_batch_id": previous["id"] if previous else None,
    }


async def trading_management_current_user(
    authorization: Optional[str] = Header(default=None),
) -> dict:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user
