"""交易管理模块。

P0 只读事实、业务归类和业务开平关系均通过本独立路由扩展。
"""
from datetime import date, datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import base64
import binascii
import hashlib
import json
from pathlib import Path
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from openpyxl import load_workbook
from pydantic import BaseModel

from . import db
from .permissions import require_permission


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


def _prepare_import_rows(
    cur,
    batch_id: int,
    account_id: int,
    fact_type: str,
    source_file: str,
    source_sheet: str,
    rows: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], int, int]]:
    source_values = []
    for row in rows:
        raw_json = json.dumps(row["raw_data"], ensure_ascii=False, sort_keys=True)
        source_values.append((
            batch_id, fact_type, source_file, source_sheet, row["source_row_no"],
            hashlib.sha256(raw_json.encode("utf-8")).hexdigest(), raw_json,
        ))
    if source_values:
        db._executemany(
            cur,
            """
            INSERT INTO trading_source_rows
                (batch_id, source_type, source_file, source_sheet, source_row_no, raw_hash, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            source_values,
        )
    source_ids = {
        row["source_row_no"]: row["id"]
        for row in db._exec(
            cur,
            "SELECT id, source_row_no FROM trading_source_rows WHERE batch_id = ? AND source_type = ?",
            (batch_id, fact_type),
        ).fetchall()
    }
    identities = {
        row["stable_key"]: row["id"]
        for row in db._exec(
            cur,
            "SELECT id, stable_key FROM trading_fact_identities WHERE account_id = ? AND fact_type = ?",
            (account_id, fact_type),
        ).fetchall()
    }
    missing = [
        (account_id, fact_type, row["stable_key"])
        for row in rows if row["stable_key"] not in identities
    ]
    if missing:
        db._executemany(
            cur,
            """
            INSERT INTO trading_fact_identities (account_id, fact_type, stable_key)
            VALUES (?, ?, ?) ON CONFLICT(account_id, fact_type, stable_key) DO NOTHING
            """,
            missing,
        )
        identities = {
            row["stable_key"]: row["id"]
            for row in db._exec(
                cur,
                "SELECT id, stable_key FROM trading_fact_identities WHERE account_id = ? AND fact_type = ?",
                (account_id, fact_type),
            ).fetchall()
        }
    return [(row, source_ids[row["source_row_no"]], identities[row["stable_key"]]) for row in rows]


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

        previous_assignments: dict[tuple[Any, ...], list[int]] = {}
        if previous:
            inheritance_rows = db._exec(
                cur,
                """
                SELECT old_tf.identity_id, old_sr.source_row_no, old_tf.trade_date,
                       old_tf.exchange, old_tf.contract, old_tf.side, old_tf.open_close
                FROM trading_trade_facts old_tf
                JOIN trading_source_rows old_sr ON old_sr.id = old_tf.source_row_id
                JOIN trading_business_assignments old_ba ON old_ba.trade_identity_id = old_tf.identity_id
                WHERE old_tf.batch_id = ?
                """,
                (previous["id"],),
            ).fetchall()
            for old_row in inheritance_rows:
                key = (
                    old_row["source_row_no"], old_row["trade_date"], old_row["exchange"],
                    old_row["contract"], old_row["side"], old_row["open_close"],
                )
                previous_assignments.setdefault(key, []).append(old_row["identity_id"])

        prepared_trades = _prepare_import_rows(
            cur, preview_batch_id, batch["account_id"], "trade", batch["trade_file_name"], "成交记录", trades
        )
        trade_values = []
        for row, source_row_id, identity_id in prepared_trades:
            inheritance_review = None
            if previous:
                key = (
                    row["source_row_no"], row["date"], row["exchange"],
                    row["contract"], row["side"], row["open_close"],
                )
                inheritance_review = next(
                    (old_identity_id for old_identity_id in previous_assignments.get(key, []) if old_identity_id != identity_id),
                    None,
                )
            verification_status = "inheritance_review_required" if inheritance_review else "file_imported"
            trade_values.append((
                identity_id, preview_batch_id, source_row_id, row["date"], row["trade_time"],
                row["exchange"], row["contract"], row["asset_type"], row["side"],
                row["open_close_raw"], row["open_close"], row["quantity"], row["price"],
                row["turnover"], row["fee"], row["hedge_flag"], row["premium_cashflow"],
                verification_status,
            ))
        if trade_values:
            db._executemany(
                cur,
                """
                INSERT INTO trading_trade_facts
                    (identity_id, batch_id, source_row_id, trade_date, trade_time, exchange, contract,
                     asset_type, side, open_close_raw, open_close, quantity, price, turnover, fee,
                     hedge_flag, premium_cashflow, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                trade_values,
            )

        prepared_closes = _prepare_import_rows(
            cur, preview_batch_id, batch["account_id"], "close", batch["close_file_name"], "平仓记录", closes
        )
        close_values = [
            (
                identity_id, preview_batch_id, source_row_id, row["open_date"], row["close_date"],
                row["exchange"], row["contract"], row["asset_type"], row["open_side"],
                row["close_side"], row["quantity"], row["open_price"], row["close_price"],
                row["fact_close_pnl"], row["fee"], "matched" if row["fee"] is not None else "pending_match",
            )
            for row, source_row_id, identity_id in prepared_closes
        ]
        if close_values:
            db._executemany(
                cur,
                """
                INSERT INTO trading_close_facts
                    (identity_id, batch_id, source_row_id, open_date, close_date, exchange, contract,
                     asset_type, open_side, close_side, quantity, open_price, close_price,
                     fact_close_pnl, matched_fee, fee_status, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'file_imported')
                """,
                close_values,
            )

        prepared_positions = _prepare_import_rows(
            cur, preview_batch_id, batch["account_id"], "position", batch["position_file_name"], "期末持仓", positions
        )
        position_values = [
            (
                identity_id, preview_batch_id, source_row_id, row["snapshot_date"], row["exchange"],
                row["contract"], row["asset_type"], row["direction"], row["open_date"],
                row["quantity"], row["average_price"], row["margin"], row["valuation_price"],
                row["floating_pnl"], row["valuation_status"],
            )
            for row, source_row_id, identity_id in prepared_positions
        ]
        if position_values:
            db._executemany(
                cur,
                """
                INSERT INTO trading_position_snapshots
                    (identity_id, batch_id, source_row_id, snapshot_date, exchange, contract, asset_type,
                     direction, open_date, quantity, average_price, margin, valuation_price, floating_pnl,
                     valuation_status, verification_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'file_imported')
                """,
                position_values,
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


def match_imported_facts(batch_id: int) -> dict[str, int]:
    with db.connect() as conn:
        cur = conn.cursor()
        closes = db._exec(
            cur,
            "SELECT * FROM trading_close_facts WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
        trades = db._exec(
            cur,
            "SELECT * FROM trading_trade_facts WHERE batch_id = ? ORDER BY trade_date, trade_time, source_row_id, id",
            (batch_id,),
        ).fetchall()
        db._exec(
            cur,
            """
            DELETE FROM trading_close_trade_links
            WHERE close_identity_id IN (
                SELECT identity_id FROM trading_close_facts WHERE batch_id = ?
            )
            """,
            (batch_id,),
        )
        db._exec(
            cur,
            """
            DELETE FROM trading_fact_close_allocations
            WHERE close_identity_id IN (
                SELECT identity_id FROM trading_close_facts WHERE batch_id = ?
            )
            """,
            (batch_id,),
        )

        close_trade_links = 0
        fact_allocations = 0
        pending = 0
        close_trade_rows = []
        close_fee_updates = []
        fact_allocation_rows = []
        remaining_close_trades = {row["id"]: float(row["quantity"]) for row in trades if row["open_close"] == "平仓"}
        remaining_open_trades = {row["id"]: float(row["quantity"]) for row in trades if row["open_close"] == "开仓"}
        for close in closes:
            matching_closes = [
                row for row in trades
                if row["open_close"] == "平仓"
                and row["trade_date"] == close["close_date"]
                and row["contract"] == close["contract"]
                and row["side"] == close["close_side"]
                and abs(float(row["price"]) - float(close["close_price"])) < 1e-8
                and remaining_close_trades.get(row["id"], 0) > 0
            ]
            needed = float(close["quantity"])
            if sum(remaining_close_trades[row["id"]] for row in matching_closes) + 1e-9 < needed:
                pending += 1
                continue
            allocated_fee = 0.0
            for trade in matching_closes:
                quantity = min(needed, remaining_close_trades[trade["id"]])
                if quantity <= 0:
                    continue
                fee = float(trade["fee"] or 0) * quantity / float(trade["quantity"])
                close_trade_rows.append((close["identity_id"], trade["identity_id"], quantity, fee))
                close_trade_links += 1
                allocated_fee += fee
                remaining_close_trades[trade["id"]] -= quantity
                needed -= quantity
                if needed <= 1e-9:
                    break
            close_fee_updates.append((allocated_fee, close["id"]))

            matching_opens = [
                row for row in trades
                if row["open_close"] == "开仓"
                and row["trade_date"] == close["open_date"]
                and row["contract"] == close["contract"]
                and row["side"] == close["open_side"]
                and abs(float(row["price"]) - float(close["open_price"])) < 1e-8
                and remaining_open_trades.get(row["id"], 0) > 0
            ]
            needed = float(close["quantity"])
            if sum(remaining_open_trades[row["id"]] for row in matching_opens) + 1e-9 < needed:
                pending += 1
                continue
            for trade in matching_opens:
                quantity = min(needed, remaining_open_trades[trade["id"]])
                if quantity <= 0:
                    continue
                fact_allocation_rows.append((close["identity_id"], trade["identity_id"], quantity))
                fact_allocations += 1
                remaining_open_trades[trade["id"]] -= quantity
                needed -= quantity
                if needed <= 1e-9:
                    break
        if close_trade_rows:
            db._executemany(
                cur,
                """
                INSERT INTO trading_close_trade_links
                    (close_identity_id, close_trade_identity_id, matched_quantity, allocated_fee, rule_version)
                VALUES (?, ?, ?, ?, 'wenhua-group-v1')
                """,
                close_trade_rows,
            )
        if close_fee_updates:
            db._executemany(
                cur,
                "UPDATE trading_close_facts SET matched_fee = ?, fee_status = 'matched' WHERE id = ?",
                close_fee_updates,
            )
        if fact_allocation_rows:
            db._executemany(
                cur,
                """
                INSERT INTO trading_fact_close_allocations
                    (close_identity_id, open_trade_identity_id, matched_quantity, match_rule_version)
                VALUES (?, ?, ?, 'wenhua-fifo-v1')
                """,
                fact_allocation_rows,
            )
        conn.commit()
    return {
        "close_trade_links": close_trade_links,
        "fact_close_allocations": fact_allocations,
        "pending_closes": pending,
    }


@dataclass
class FactFilters:
    contract: str = ""
    direction: str = ""
    asset_type: str = ""
    open_close: str = ""
    start_date: str = ""
    end_date: str = ""
    page: int = 1
    page_size: int = 20

    def __post_init__(self):
        if self.page_size not in {20, 50, 100}:
            raise ValueError("每页条数只允许 20、50、100")
        self.page = max(1, self.page)


def _page_result(items: list[dict[str, Any]], summary: dict[str, Any], filters: FactFilters, data_status: str = "ok") -> dict[str, Any]:
    total = len(items)
    total_pages = max(1, (total + filters.page_size - 1) // filters.page_size)
    page = min(filters.page, total_pages)
    start = (page - 1) * filters.page_size
    return {
        "items": items[start:start + filters.page_size],
        "summary": summary,
        "page": page,
        "page_size": filters.page_size,
        "total_items": total,
        "total_pages": total_pages,
        "data_status": data_status,
    }


def query_fact_rows(view: str, filters: FactFilters) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        if view == "positions":
            snapshot_date = filters.end_date
            if not snapshot_date:
                row = db._exec(
                    cur,
                    """
                    SELECT MAX(ps.snapshot_date) AS d
                    FROM trading_position_snapshots ps
                    JOIN trading_import_batches b ON b.id = ps.batch_id
                    WHERE b.status = 'active'
                    """,
                ).fetchone()
                snapshot_date = row["d"] if row else None
            rows = db._exec(
                cur,
                """
                SELECT ps.* FROM trading_position_snapshots ps
                JOIN trading_import_batches b ON b.id = ps.batch_id
                WHERE b.status = 'active' AND ps.snapshot_date = ?
                ORDER BY ps.contract, ps.direction, ps.id
                """,
                (snapshot_date,),
            ).fetchall() if snapshot_date else []
            items = [dict(row) for row in rows]
            if filters.contract:
                items = [row for row in items if filters.contract.lower() in row["contract"].lower()]
            if filters.direction:
                items = [row for row in items if row["direction"] == filters.direction]
            summary = {
                "record_count": len(items),
                "quantity": sum(float(row["quantity"]) for row in items),
                "margin": sum(float(row["margin"] or 0) for row in items),
            }
            status = "ok" if items else "no_position_snapshot"
            return _page_result(items, summary, filters, status)
        if view == "closes":
            rows = db._exec(
                cur,
                """
                SELECT cf.* FROM trading_close_facts cf
                JOIN trading_import_batches b ON b.id = cf.batch_id
                WHERE b.status = 'active'
                ORDER BY cf.close_date DESC, cf.id DESC
                """,
            ).fetchall()
            items = [dict(row) for row in rows]
            if filters.contract:
                items = [row for row in items if filters.contract.lower() in row["contract"].lower()]
            if filters.direction:
                items = [row for row in items if row["open_side"] == filters.direction]
            if filters.asset_type:
                items = [row for row in items if row["asset_type"] == filters.asset_type]
            if filters.start_date:
                items = [row for row in items if row["close_date"] >= filters.start_date]
            if filters.end_date:
                items = [row for row in items if row["close_date"] <= filters.end_date]
            summary = {
                "record_count": len(items),
                "quantity": sum(float(row["quantity"]) for row in items),
                "fact_close_pnl": sum(float(row["fact_close_pnl"] or 0) for row in items),
                "fee": sum(float(row["matched_fee"] or 0) for row in items),
            }
            return _page_result(items, summary, filters)
        if view != "trades":
            raise ValueError(f"未知事实视图：{view}")
        rows = db._exec(
            cur,
            """
            SELECT tf.*,
                   (SELECT SUM(cf.fact_close_pnl * l.matched_quantity / cf.quantity)
                    FROM trading_close_trade_links l
                    JOIN trading_close_facts cf ON cf.identity_id = l.close_identity_id
                    WHERE l.close_trade_identity_id = tf.identity_id) AS fact_close_pnl
            FROM trading_trade_facts tf
            JOIN trading_import_batches b ON b.id = tf.batch_id
            WHERE b.status = 'active'
            ORDER BY tf.trade_date DESC, tf.id DESC
            """,
        ).fetchall()
        items = [dict(row) for row in rows]
        if filters.contract:
            items = [row for row in items if filters.contract.lower() in row["contract"].lower()]
        if filters.direction:
            items = [row for row in items if row["side"] == filters.direction]
        if filters.asset_type:
            items = [row for row in items if row["asset_type"] == filters.asset_type]
        if filters.open_close:
            items = [row for row in items if row["open_close"] == filters.open_close]
        if filters.start_date:
            items = [row for row in items if row["trade_date"] >= filters.start_date]
        if filters.end_date:
            items = [row for row in items if row["trade_date"] <= filters.end_date]
        summary = {
            "record_count": len(items),
            "quantity": sum(float(row["quantity"]) for row in items),
            "fee": sum(float(row["fee"] or 0) for row in items),
            "fact_close_pnl": sum(float(row["fact_close_pnl"] or 0) for row in items),
        }
        return _page_result(items, summary, filters)


def build_overview(filters: FactFilters) -> dict[str, Any]:
    trades = query_fact_rows("trades", FactFilters(
        contract=filters.contract,
        direction=filters.direction,
        asset_type=filters.asset_type,
        open_close=filters.open_close,
        start_date=filters.start_date,
        end_date=filters.end_date,
        page=1,
        page_size=100,
    ))
    closes = query_fact_rows("closes", FactFilters(
        contract=filters.contract,
        direction=filters.direction,
        asset_type=filters.asset_type,
        start_date=filters.start_date,
        end_date=filters.end_date,
        page=1,
        page_size=100,
    ))
    positions = query_fact_rows("positions", FactFilters(
        contract=filters.contract,
        direction=filters.direction,
        asset_type=filters.asset_type,
        end_date=filters.end_date,
        page=1,
        page_size=100,
    ))
    snapshot_date = positions["items"][0]["snapshot_date"] if positions["items"] else None
    return {
        "trades": trades["summary"],
        "closes": closes["summary"],
        "positions": {
            **positions["summary"],
            "snapshot_date": snapshot_date,
            "floating_pnl": None,
            "floating_pnl_status": "pending_calculation",
        },
        "data_status": {
            "fact": "file_imported",
            "positions": positions["data_status"],
        },
    }


BUSINESS_TYPES = ("basic_hedging", "strategic_hedging")
_REMATCH_PREVIEWS: dict[str, dict[str, Any]] = {}


class TradingUploadFile(BaseModel):
    name: str
    content_base64: str


class TradingImportPreviewIn(BaseModel):
    account_id: int
    trade_file: TradingUploadFile
    close_file: TradingUploadFile
    position_file: TradingUploadFile


class BusinessSubjectIn(BaseModel):
    name: str


class StrategyIn(BaseModel):
    name: str


class BusinessAssignmentIn(BaseModel):
    identity_ids: list[int]
    business_subject_id: int
    business_type: str
    strategy_name: str = ""
    instruction_text: str = ""


class RematchSelectionIn(BaseModel):
    open_trade_identity_id: int
    quantity: float


class RematchPreviewIn(BaseModel):
    allocation_version: int
    selections: list[RematchSelectionIn]


class RematchConfirmIn(BaseModel):
    preview_token: str
    allocation_version: int
    reason: str = ""


class RestoreDefaultIn(BaseModel):
    allocation_version: int
    reason: str = "恢复事实层默认开平关系"


def _normalized_name(value: str) -> str:
    return " ".join(str(value or "").strip().split()).lower()


def create_business_subject(name: str, actor: str) -> dict[str, Any]:
    display_name = " ".join(str(name or "").strip().split())
    normalized = _normalized_name(display_name)
    if not normalized:
        raise ValueError("业务归属名称不能为空")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT * FROM trading_business_subjects WHERE normalized_name = ?", (normalized,)).fetchone()
        if row:
            return dict(row)
        subject_id = db._last_insert_id(
            cur,
            "INSERT INTO trading_business_subjects (name, normalized_name, created_by, updated_by) VALUES (?, ?, ?, ?)",
            (display_name, normalized, actor, actor),
        )
        conn.commit()
        row = db._exec(cur, "SELECT * FROM trading_business_subjects WHERE id = ?", (subject_id,)).fetchone()
    return dict(row)


def get_or_create_strategy(name: str, actor: str) -> Optional[dict[str, Any]]:
    display_name = " ".join(str(name or "").strip().split())
    if not display_name:
        return None
    normalized = _normalized_name(display_name)
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT * FROM trading_strategies WHERE normalized_name = ?", (normalized,)).fetchone()
        if row:
            return dict(row)
        strategy_id = db._last_insert_id(
            cur,
            "INSERT INTO trading_strategies (name, normalized_name, source, created_by, updated_by) VALUES (?, ?, 'manual', ?, ?)",
            (display_name, normalized, actor, actor),
        )
        conn.commit()
        row = db._exec(cur, "SELECT * FROM trading_strategies WHERE id = ?", (strategy_id,)).fetchone()
    return dict(row)


def list_trading_config() -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        subjects = db._exec(cur, "SELECT * FROM trading_business_subjects ORDER BY name").fetchall()
        strategies = db._exec(cur, "SELECT * FROM trading_strategies ORDER BY name").fetchall()
        accounts = db._exec(cur, "SELECT * FROM trading_accounts WHERE is_active = 1 ORDER BY display_name").fetchall()
    return {
        "business_types": list(BUSINESS_TYPES),
        "subjects": [dict(row) for row in subjects],
        "strategies": [dict(row) for row in strategies],
        "accounts": [dict(row) for row in accounts],
        "junneng_candidate_products": ["rb", "hc"],
    }


def _write_assignment_audit(cur, identity_id: int, operation_type: str, actor: str, before: Any, after: Any) -> None:
    db._exec(
        cur,
        """
        INSERT INTO operation_logs
            (module_code, entity_type, entity_id, operation_type, description, before_data, after_data)
        VALUES ('trading_positions', 'trading_business_assignment', ?, ?, ?, ?, ?)
        """,
        (
            identity_id,
            operation_type,
            f"{actor} {operation_type}",
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
        ),
    )


def classify_trade_identities(
    identity_ids: list[int],
    business_subject_id: int,
    business_type: str,
    strategy_name: str,
    instruction_text: str,
    actor: str,
    requested_quantity: Optional[float] = None,
) -> dict[str, Any]:
    if requested_quantity is not None:
        raise ValueError("一笔成交不允许按手数拆分归类")
    if business_type not in BUSINESS_TYPES:
        raise ValueError("业务类型只允许基础套保或战略套保")
    if not identity_ids:
        raise ValueError("请选择需要归类的完整成交")
    strategy = get_or_create_strategy(strategy_name, actor)
    with db.connect() as conn:
        cur = conn.cursor()
        subject = db._exec(
            cur,
            "SELECT id FROM trading_business_subjects WHERE id = ? AND is_active = 1",
            (business_subject_id,),
        ).fetchone()
        if not subject:
            raise ValueError("业务归属不存在或已停用")
        assigned = 0
        for identity_id in identity_ids:
            active_fact = db._exec(
                cur,
                """
                SELECT tf.identity_id FROM trading_trade_facts tf
                JOIN trading_import_batches b ON b.id = tf.batch_id
                WHERE b.status = 'active' AND tf.identity_id = ?
                LIMIT 1
                """,
                (identity_id,),
            ).fetchone()
            if not active_fact:
                raise ValueError(f"成交事实不存在或不是有效版本：{identity_id}")
            before_row = db._exec(
                cur,
                "SELECT * FROM trading_business_assignments WHERE trade_identity_id = ?",
                (identity_id,),
            ).fetchone()
            before = dict(before_row) if before_row else None
            if before_row:
                db._exec(
                    cur,
                    """
                    UPDATE trading_business_assignments
                    SET business_subject_id = ?, business_type = ?, strategy_id = ?, instruction_text = ?,
                        updated_by = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE trade_identity_id = ?
                    """,
                    (business_subject_id, business_type, strategy["id"] if strategy else None, instruction_text or None, actor, identity_id),
                )
            else:
                db._exec(
                    cur,
                    """
                    INSERT INTO trading_business_assignments
                        (trade_identity_id, business_subject_id, business_type, strategy_id,
                         instruction_text, assigned_by, updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (identity_id, business_subject_id, business_type, strategy["id"] if strategy else None, instruction_text or None, actor, actor),
                )
            after_row = db._exec(
                cur,
                "SELECT * FROM trading_business_assignments WHERE trade_identity_id = ?",
                (identity_id,),
            ).fetchone()
            _write_assignment_audit(cur, identity_id, "业务归类", actor, before, dict(after_row))
            linked_closes = db._exec(
                cur,
                "SELECT DISTINCT close_identity_id FROM trading_fact_close_allocations WHERE open_trade_identity_id = ?",
                (identity_id,),
            ).fetchall()
            business_config = (business_subject_id, business_type, strategy["id"] if strategy else None)
            for linked_close in linked_closes:
                _inherit_close_trade_assignment(cur, linked_close["close_identity_id"], business_config, actor)
            assigned += 1
        conn.commit()
    return {"assigned_count": assigned}


def remove_trade_assignment(identity_id: int, actor: str) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        before_row = db._exec(
            cur,
            "SELECT * FROM trading_business_assignments WHERE trade_identity_id = ?",
            (identity_id,),
        ).fetchone()
        if not before_row:
            return {"removed": False}
        db._exec(cur, "DELETE FROM trading_business_assignments WHERE trade_identity_id = ?", (identity_id,))
        _write_assignment_audit(cur, identity_id, "取消业务归类", actor, dict(before_row), None)
        conn.commit()
    return {"removed": True}


def calculate_business_pnl(
    open_price: float,
    close_price: float,
    side: str,
    quantity: float,
    multiplier: float,
) -> float:
    difference = close_price - open_price if side == "买" else open_price - close_price
    return round(difference * quantity * multiplier, 8)


def _product_code(contract: str) -> str:
    match = re.match(r"[a-zA-Z]+", contract or "")
    return match.group(0).lower() if match else ""


def rebuild_default_business_allocations(batch_id: int) -> dict[str, int]:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            DELETE FROM trading_business_close_allocations
            WHERE source = 'fact_default' AND close_identity_id IN (
                SELECT identity_id FROM trading_close_facts WHERE batch_id = ?
            )
            """,
            (batch_id,),
        )
        fact_rows = db._exec(
            cur,
            """
            SELECT fa.close_identity_id, fa.open_trade_identity_id, fa.matched_quantity,
                   cf.open_price, cf.close_price, cf.open_side, cf.exchange, cf.contract, cf.asset_type
            FROM trading_fact_close_allocations fa
            JOIN trading_close_facts cf ON cf.identity_id = fa.close_identity_id AND cf.batch_id = ?
            ORDER BY fa.id
            """,
            (batch_id,),
        ).fetchall()
        specs = db._exec(
            cur,
            """
            SELECT exchange, product_code, asset_type, contract_multiplier
            FROM trading_contract_specs WHERE is_active = 1
            """,
        ).fetchall()
        spec_by_contract = {
            (row["exchange"].lower(), row["product_code"].lower(), row["asset_type"]): float(row["contract_multiplier"])
            for row in specs
        }
        count = 0
        allocation_rows = []
        for row in fact_rows:
            multiplier = spec_by_contract.get(
                (row["exchange"].lower(), _product_code(row["contract"]), row["asset_type"])
            )
            business_pnl = None
            if multiplier is not None:
                business_pnl = calculate_business_pnl(
                    float(row["open_price"]), float(row["close_price"]), row["open_side"],
                    float(row["matched_quantity"]), multiplier,
                )
            allocation_rows.append(
                (row["close_identity_id"], row["open_trade_identity_id"], row["matched_quantity"], business_pnl)
            )
            count += 1
        if allocation_rows:
            db._executemany(
                cur,
                """
                INSERT INTO trading_business_close_allocations
                    (close_identity_id, open_trade_identity_id, matched_quantity, source,
                     business_pnl, rule_version)
                VALUES (?, ?, ?, 'fact_default', ?, 'business-default-v1')
                """,
                allocation_rows,
            )
        conn.commit()
    return {"allocation_count": count}


def query_business_rows(view: str, tab: str, filters: FactFilters) -> dict[str, Any]:
    if view not in {"junneng", "options"} or tab not in {"positions", "closes", "trades"}:
        raise ValueError("未知业务视图")
    if tab == "positions":
        with db.connect() as conn:
            rows = db._exec(
                conn.cursor(),
                """
                SELECT tf.identity_id, tf.contract, tf.asset_type, tf.side AS direction,
                       tf.price AS average_price, tf.quantity,
                       tf.quantity - COALESCE((
                           SELECT SUM(a.matched_quantity)
                           FROM trading_business_close_allocations a
                           WHERE a.open_trade_identity_id = tf.identity_id
                       ), 0) AS remaining_quantity,
                       s.name AS business_subject, ba.business_type, st.name AS strategy
                FROM trading_trade_facts tf
                JOIN trading_import_batches b ON b.id = tf.batch_id AND b.status = 'active'
                LEFT JOIN trading_business_assignments ba ON ba.trade_identity_id = tf.identity_id
                LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
                LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
                WHERE tf.open_close = '开仓'
                ORDER BY tf.contract, tf.id
                """,
            ).fetchall()
        all_items = [dict(row) for row in rows if float(row["remaining_quantity"] or 0) > 1e-9]
        if view == "junneng":
            items = [row for row in all_items if row["business_subject"] == "上海钧能"]
        else:
            items = [row for row in all_items if row["asset_type"] == "option"]
        if filters.contract:
            items = [row for row in items if filters.contract.lower() in row["contract"].lower()]
        for item in items:
            item["quantity"] = item.pop("remaining_quantity")
            item["floating_pnl"] = None
            item["floating_pnl_status"] = "pending_calculation"
        summary = {
            "record_count": len(items),
            "quantity": sum(float(row["quantity"]) for row in items),
            "business_pnl": 0,
            "floating_pnl": None,
            "floating_pnl_status": "pending_calculation",
        }
        return _page_result(items, summary, filters)

    with db.connect() as conn:
        cur = conn.cursor()
        if tab == "trades":
            rows = db._exec(
                cur,
                """
                SELECT tf.*, s.name AS business_subject, ba.business_type,
                       st.name AS strategy, ba.instruction_text,
                       CASE WHEN ba.id IS NULL THEN 'unclassified' ELSE 'classified' END AS assignment_status
                FROM trading_trade_facts tf
                JOIN trading_import_batches b ON b.id = tf.batch_id AND b.status = 'active'
                LEFT JOIN trading_business_assignments ba ON ba.trade_identity_id = tf.identity_id
                LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
                LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
                ORDER BY tf.trade_date DESC, tf.id DESC
                """,
            ).fetchall()
            all_items = [dict(row) for row in rows]
            candidates = [
                row for row in all_items
                if row["assignment_status"] == "unclassified" and _product_code(row["contract"]) in {"rb", "hc"}
            ]
            if view == "junneng":
                items = [row for row in all_items if row["business_subject"] == "上海钧能"]
            else:
                items = [row for row in all_items if row["asset_type"] == "option"]
            if filters.contract:
                items = [row for row in items if filters.contract.lower() in row["contract"].lower()]
            summary = {
                "record_count": len(items),
                "quantity": sum(float(row["quantity"]) for row in items),
                "fee": sum(float(row["fee"] or 0) for row in items),
            }
            result = _page_result(items, summary, filters)
            result["candidates"] = {
                "record_count": len(candidates) if view == "junneng" else 0,
                "quantity": sum(float(row["quantity"]) for row in candidates) if view == "junneng" else 0,
            }
            return result
        if view == "options":
            rows = db._exec(
                cur,
                """
                SELECT cf.*, a.business_pnl, COALESCE(a.matched_quantity, cf.quantity) AS matched_quantity,
                       COALESCE(a.allocation_version, 1) AS allocation_version,
                       a.source AS allocation_source, s.name AS business_subject,
                       ba.business_type, st.name AS strategy
                FROM trading_close_facts cf
                JOIN trading_import_batches b ON b.id = cf.batch_id AND b.status = 'active'
                LEFT JOIN trading_business_close_allocations a ON a.close_identity_id = cf.identity_id
                LEFT JOIN trading_business_assignments ba ON ba.trade_identity_id = a.open_trade_identity_id
                LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
                LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
                WHERE cf.asset_type = 'option'
                ORDER BY cf.close_date DESC, cf.id DESC
                """,
            ).fetchall()
        else:
            rows = db._exec(
                cur,
                """
                SELECT cf.*, a.business_pnl, a.matched_quantity, a.allocation_version, a.source AS allocation_source,
                       s.name AS business_subject, ba.business_type, st.name AS strategy
                FROM trading_business_close_allocations a
                JOIN trading_close_facts cf ON cf.identity_id = a.close_identity_id
                JOIN trading_import_batches b ON b.id = cf.batch_id AND b.status = 'active'
                LEFT JOIN trading_business_assignments ba ON ba.trade_identity_id = a.open_trade_identity_id
                LEFT JOIN trading_business_subjects s ON s.id = ba.business_subject_id
                LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
                ORDER BY cf.close_date DESC, cf.id DESC
                """,
            ).fetchall()
        all_items = [dict(row) for row in rows]
        if view == "junneng":
            items = [row for row in all_items if row["business_subject"] == "上海钧能"]
        else:
            items = [row for row in all_items if row["asset_type"] == "option"]
        if filters.contract:
            items = [row for row in items if filters.contract.lower() in row["contract"].lower()]
        summary = {
            "record_count": len(items),
            "quantity": sum(float(row["matched_quantity"]) for row in items),
            "business_pnl": sum(float(row["business_pnl"] or 0) for row in items),
            "fact_close_pnl": sum(float(row["fact_close_pnl"] or 0) for row in items),
            "fee": sum(float(row["matched_fee"] or 0) for row in items),
        }
        return _page_result(items, summary, filters)


def _current_allocation_version(cur, close_identity_id: int) -> int:
    row = db._exec(
        cur,
        "SELECT MAX(allocation_version) AS v FROM trading_business_close_allocations WHERE close_identity_id = ?",
        (close_identity_id,),
    ).fetchone()
    return int(row["v"] or 0)


def list_business_close_candidates(close_identity_id: int) -> list[dict[str, Any]]:
    with db.connect() as conn:
        cur = conn.cursor()
        close = db._exec(
            cur,
            """
            SELECT cf.*, fi.account_id FROM trading_close_facts cf
            JOIN trading_import_batches b ON b.id = cf.batch_id AND b.status = 'active'
            JOIN trading_fact_identities fi ON fi.id = cf.identity_id
            WHERE cf.identity_id = ?
            """,
            (close_identity_id,),
        ).fetchone()
        if not close:
            raise ValueError("平仓事实不存在或不是有效版本")
        rows = db._exec(
            cur,
            """
            SELECT tf.identity_id, tf.trade_date, tf.contract, tf.side, tf.quantity, tf.price,
                   tf.quantity - COALESCE((
                       SELECT SUM(a.matched_quantity) FROM trading_business_close_allocations a
                       WHERE a.open_trade_identity_id = tf.identity_id AND a.close_identity_id <> ?
                   ), 0) AS available_quantity,
                   ba.business_subject_id, ba.business_type, ba.strategy_id,
                   s.name AS business_subject, st.name AS strategy
            FROM trading_trade_facts tf
            JOIN trading_import_batches b ON b.id = tf.batch_id AND b.status = 'active'
            JOIN trading_fact_identities fi ON fi.id = tf.identity_id
            JOIN trading_business_assignments ba ON ba.trade_identity_id = tf.identity_id
            JOIN trading_business_subjects s ON s.id = ba.business_subject_id
            LEFT JOIN trading_strategies st ON st.id = ba.strategy_id
            WHERE fi.account_id = ? AND tf.open_close = '开仓'
              AND tf.contract = ? AND tf.side = ? AND tf.trade_date <= ?
            ORDER BY tf.trade_date, tf.id
            """,
            (close_identity_id, close["account_id"], close["contract"], close["open_side"], close["close_date"]),
        ).fetchall()
    return [dict(row) for row in rows if float(row["available_quantity"] or 0) > 1e-9]


def preview_business_rematch(
    close_identity_id: int,
    selections: list[dict[str, Any]],
    allocation_version: int,
) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        current_version = _current_allocation_version(cur, close_identity_id)
        if current_version != allocation_version:
            raise ValueError("业务开平数据已变化，请刷新后重试")
        close = db._exec(
            cur,
            "SELECT * FROM trading_close_facts WHERE identity_id = ?",
            (close_identity_id,),
        ).fetchone()
        if not close:
            raise ValueError("平仓事实不存在")
        candidates = {row["identity_id"]: row for row in list_business_close_candidates(close_identity_id)}
        if not selections:
            raise ValueError("请选择业务开仓记录")
        selected_rows = []
        total = 0.0
        configs = set()
        for selection in selections:
            identity_id = int(selection["open_trade_identity_id"])
            quantity = float(selection["quantity"])
            candidate = candidates.get(identity_id)
            if not candidate:
                raise ValueError("只能选择同账户、同合约、同方向且时间合规的业务开仓")
            if quantity <= 0 or quantity > float(candidate["available_quantity"]) + 1e-9:
                raise ValueError("业务可平手数不足")
            configs.add((candidate["business_subject_id"], candidate["business_type"], candidate["strategy_id"]))
            selected_rows.append({**selection, "candidate": candidate})
            total += quantity
        if len(configs) != 1:
            raise ValueError("多笔开仓的业务归属、业务类型和策略必须一致")
        if abs(total - float(close["quantity"])) > 1e-9:
            raise ValueError("业务匹配手数合计必须等于平仓手数")
        spec = db._exec(
            cur,
            """
            SELECT contract_multiplier FROM trading_contract_specs
            WHERE LOWER(exchange) = LOWER(?) AND LOWER(product_code) = ? AND asset_type = ? AND is_active = 1
            """,
            (close["exchange"], _product_code(close["contract"]), close["asset_type"]),
        ).fetchone()
        if not spec:
            raise ValueError("合约参数待核验")
        before_row = db._exec(
            cur,
            "SELECT SUM(business_pnl) AS pnl FROM trading_business_close_allocations WHERE close_identity_id = ?",
            (close_identity_id,),
        ).fetchone()
        after_pnl = sum(
            calculate_business_pnl(
                float(item["candidate"]["price"]), float(close["close_price"]), close["open_side"],
                float(item["quantity"]), float(spec["contract_multiplier"]),
            )
            for item in selected_rows
        )
        token = uuid.uuid4().hex
        payload = {
            "close_identity_id": close_identity_id,
            "allocation_version": allocation_version,
            "selections": [
                {"open_trade_identity_id": int(item["open_trade_identity_id"]), "quantity": float(item["quantity"])}
                for item in selected_rows
            ],
            "business_config": list(next(iter(configs))),
            "before_business_pnl": float(before_row["pnl"] or 0),
            "after_business_pnl": after_pnl,
        }
        _REMATCH_PREVIEWS[token] = payload
    return {"preview_token": token, **payload}


def reconcile_business_pnl(close_identity_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        close = db._exec(
            cur,
            """
            SELECT cf.contract, cf.open_side, fi.account_id
            FROM trading_close_facts cf
            JOIN trading_import_batches b ON b.id = cf.batch_id AND b.status = 'active'
            JOIN trading_fact_identities fi ON fi.id = cf.identity_id
            WHERE cf.identity_id = ?
            """,
            (close_identity_id,),
        ).fetchone()
        if not close:
            raise ValueError("平仓事实不存在或不是有效版本")
        open_row = db._exec(
            cur,
            """
            SELECT SUM(tf.quantity) AS quantity
            FROM trading_trade_facts tf
            JOIN trading_import_batches b ON b.id = tf.batch_id AND b.status = 'active'
            JOIN trading_fact_identities fi ON fi.id = tf.identity_id
            WHERE fi.account_id = ? AND tf.contract = ? AND tf.side = ? AND tf.open_close = '开仓'
            """,
            (close["account_id"], close["contract"], close["open_side"]),
        ).fetchone()
        allocation_row = db._exec(
            cur,
            """
            SELECT SUM(a.matched_quantity) AS quantity, SUM(a.business_pnl) AS business_pnl
            FROM trading_business_close_allocations a
            JOIN trading_close_facts cf ON cf.identity_id = a.close_identity_id
            JOIN trading_import_batches b ON b.id = cf.batch_id AND b.status = 'active'
            JOIN trading_fact_identities fi ON fi.id = cf.identity_id
            WHERE fi.account_id = ? AND cf.contract = ? AND cf.open_side = ?
            """,
            (close["account_id"], close["contract"], close["open_side"]),
        ).fetchone()
        fact_row = db._exec(
            cur,
            """
            SELECT SUM(cf.fact_close_pnl) AS fact_pnl
            FROM trading_close_facts cf
            JOIN trading_import_batches b ON b.id = cf.batch_id AND b.status = 'active'
            JOIN trading_fact_identities fi ON fi.id = cf.identity_id
            WHERE fi.account_id = ? AND cf.contract = ? AND cf.open_side = ?
            """,
            (close["account_id"], close["contract"], close["open_side"]),
        ).fetchone()
    open_quantity = float(open_row["quantity"] or 0)
    allocated_quantity = float(allocation_row["quantity"] or 0)
    business_pnl = float(allocation_row["business_pnl"] or 0)
    fact_pnl = float(fact_row["fact_pnl"] or 0)
    remaining_quantity = max(0.0, open_quantity - allocated_quantity)
    difference = round(business_pnl - fact_pnl, 8)
    if open_quantity <= 0 or remaining_quantity > 1e-9:
        status = "not_fully_closed"
    elif abs(difference) <= 1e-8:
        status = "reconciled"
    else:
        status = "business_pnl_reconciliation_failed"
    return {
        "status": status,
        "account_id": close["account_id"],
        "contract": close["contract"],
        "direction": close["open_side"],
        "open_quantity": open_quantity,
        "allocated_quantity": allocated_quantity,
        "remaining_quantity": remaining_quantity,
        "fact_pnl": fact_pnl,
        "business_pnl": business_pnl,
        "difference": difference,
    }


def _inherit_close_trade_assignment(cur, close_identity_id: int, business_config: tuple, actor: str) -> None:
    business_subject_id, business_type, strategy_id = business_config
    close_trade_rows = db._exec(
        cur,
        "SELECT DISTINCT close_trade_identity_id FROM trading_close_trade_links WHERE close_identity_id = ?",
        (close_identity_id,),
    ).fetchall()
    for row in close_trade_rows:
        trade_identity_id = row["close_trade_identity_id"]
        before_row = db._exec(
            cur,
            "SELECT * FROM trading_business_assignments WHERE trade_identity_id = ?",
            (trade_identity_id,),
        ).fetchone()
        before = dict(before_row) if before_row else None
        if before_row:
            db._exec(
                cur,
                """
                UPDATE trading_business_assignments
                SET business_subject_id = ?, business_type = ?, strategy_id = ?,
                    updated_by = ?, updated_at = CURRENT_TIMESTAMP
                WHERE trade_identity_id = ?
                """,
                (business_subject_id, business_type, strategy_id, actor, trade_identity_id),
            )
        else:
            db._exec(
                cur,
                """
                INSERT INTO trading_business_assignments
                    (trade_identity_id, business_subject_id, business_type, strategy_id,
                     assigned_by, updated_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trade_identity_id, business_subject_id, business_type, strategy_id, actor, actor),
            )
        after = db._exec(
            cur,
            "SELECT * FROM trading_business_assignments WHERE trade_identity_id = ?",
            (trade_identity_id,),
        ).fetchone()
        _write_assignment_audit(cur, trade_identity_id, "业务平仓自动继承", actor, before, dict(after))


def confirm_business_rematch(
    close_identity_id: int,
    preview_token: str,
    allocation_version: int,
    actor: str,
    reason: str = "",
) -> dict[str, Any]:
    payload = _REMATCH_PREVIEWS.pop(preview_token, None)
    if not payload or payload["close_identity_id"] != close_identity_id:
        raise ValueError("重配预览已失效，请重新预览")
    if payload["allocation_version"] != allocation_version:
        raise ValueError("业务开平数据已变化，请刷新后重试")
    with db.connect() as conn:
        cur = conn.cursor()
        if _current_allocation_version(cur, close_identity_id) != allocation_version:
            raise ValueError("业务开平数据已变化，请刷新后重试")
        before_rows = [
            dict(row) for row in db._exec(
                cur,
                "SELECT * FROM trading_business_close_allocations WHERE close_identity_id = ? ORDER BY id",
                (close_identity_id,),
            ).fetchall()
        ]
        new_version = allocation_version + 1
        group_id = uuid.uuid4().hex
        db._exec(cur, "DELETE FROM trading_business_close_allocations WHERE close_identity_id = ?", (close_identity_id,))
        close = db._exec(cur, "SELECT * FROM trading_close_facts WHERE identity_id = ?", (close_identity_id,)).fetchone()
        spec = db._exec(
            cur,
            "SELECT contract_multiplier FROM trading_contract_specs WHERE LOWER(exchange)=LOWER(?) AND LOWER(product_code)=? AND asset_type=? AND is_active=1",
            (close["exchange"], _product_code(close["contract"]), close["asset_type"]),
        ).fetchone()
        after_rows = []
        for selection in payload["selections"]:
            open_fact = db._exec(
                cur,
                "SELECT price FROM trading_trade_facts tf JOIN trading_import_batches b ON b.id=tf.batch_id AND b.status='active' WHERE tf.identity_id=?",
                (selection["open_trade_identity_id"],),
            ).fetchone()
            pnl = calculate_business_pnl(
                float(open_fact["price"]), float(close["close_price"]), close["open_side"],
                float(selection["quantity"]), float(spec["contract_multiplier"]),
            )
            allocation_id = db._last_insert_id(
                cur,
                """
                INSERT INTO trading_business_close_allocations
                    (close_identity_id, open_trade_identity_id, matched_quantity, source,
                     override_group_id, business_pnl, rule_version, allocation_version, created_by, updated_by)
                VALUES (?, ?, ?, 'manual_override', ?, ?, 'business-manual-v1', ?, ?, ?)
                """,
                (close_identity_id, selection["open_trade_identity_id"], selection["quantity"], group_id, pnl, new_version, actor, actor),
            )
            after_rows.append({"id": allocation_id, **selection, "business_pnl": pnl})
        _inherit_close_trade_assignment(cur, close_identity_id, tuple(payload["business_config"]), actor)
        db._exec(
            cur,
            """
            INSERT INTO trading_business_allocation_audit
                (override_group_id, close_identity_id, before_allocations, after_allocations,
                 before_business_pnl, after_business_pnl, reason, operated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id, close_identity_id, json.dumps(before_rows, ensure_ascii=False),
                json.dumps(after_rows, ensure_ascii=False), payload["before_business_pnl"],
                payload["after_business_pnl"], reason or None, actor,
            ),
        )
        conn.commit()
    return {
        "allocation_version": new_version,
        "business_pnl": payload["after_business_pnl"],
        "reconciliation": reconcile_business_pnl(close_identity_id),
    }


def restore_default_business_match(
    close_identity_id: int,
    allocation_version: int,
    actor: str,
    reason: str = "恢复事实层默认开平关系",
) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        if _current_allocation_version(cur, close_identity_id) != allocation_version:
            raise ValueError("业务开平数据已变化，请刷新后重试")
        close = db._exec(
            cur,
            "SELECT * FROM trading_close_facts WHERE identity_id = ?",
            (close_identity_id,),
        ).fetchone()
        if not close:
            raise ValueError("平仓事实不存在")
        fact_rows = db._exec(
            cur,
            """
            SELECT fa.open_trade_identity_id, fa.matched_quantity, tf.price,
                   ba.business_subject_id, ba.business_type, ba.strategy_id
            FROM trading_fact_close_allocations fa
            JOIN trading_trade_facts tf ON tf.identity_id = fa.open_trade_identity_id
            JOIN trading_import_batches b ON b.id = tf.batch_id AND b.status = 'active'
            JOIN trading_business_assignments ba ON ba.trade_identity_id = fa.open_trade_identity_id
            WHERE fa.close_identity_id = ?
            ORDER BY fa.id
            """,
            (close_identity_id,),
        ).fetchall()
        if not fact_rows:
            raise ValueError("没有可恢复的事实层默认开平关系")
        configs = {
            (row["business_subject_id"], row["business_type"], row["strategy_id"])
            for row in fact_rows
        }
        if len(configs) != 1:
            raise ValueError("事实层开仓对应多个业务归属，无法自动继承到整笔平仓成交")
        spec = db._exec(
            cur,
            """
            SELECT contract_multiplier FROM trading_contract_specs
            WHERE LOWER(exchange)=LOWER(?) AND LOWER(product_code)=? AND asset_type=? AND is_active=1
            """,
            (close["exchange"], _product_code(close["contract"]), close["asset_type"]),
        ).fetchone()
        if not spec:
            raise ValueError("合约参数待核验")
        before_rows = [
            dict(row) for row in db._exec(
                cur,
                "SELECT * FROM trading_business_close_allocations WHERE close_identity_id = ? ORDER BY id",
                (close_identity_id,),
            ).fetchall()
        ]
        new_version = allocation_version + 1
        group_id = uuid.uuid4().hex
        db._exec(cur, "DELETE FROM trading_business_close_allocations WHERE close_identity_id = ?", (close_identity_id,))
        after_rows = []
        after_pnl = 0.0
        for row in fact_rows:
            pnl = calculate_business_pnl(
                float(row["price"]), float(close["close_price"]), close["open_side"],
                float(row["matched_quantity"]), float(spec["contract_multiplier"]),
            )
            allocation_id = db._last_insert_id(
                cur,
                """
                INSERT INTO trading_business_close_allocations
                    (close_identity_id, open_trade_identity_id, matched_quantity, source,
                     override_group_id, business_pnl, rule_version, allocation_version, created_by, updated_by)
                VALUES (?, ?, ?, 'fact_default', ?, ?, 'business-default-v1', ?, ?, ?)
                """,
                (close_identity_id, row["open_trade_identity_id"], row["matched_quantity"],
                 group_id, pnl, new_version, actor, actor),
            )
            after_rows.append({
                "id": allocation_id,
                "open_trade_identity_id": row["open_trade_identity_id"],
                "quantity": row["matched_quantity"],
                "business_pnl": pnl,
            })
            after_pnl += pnl
        _inherit_close_trade_assignment(cur, close_identity_id, next(iter(configs)), actor)
        before_pnl = sum(float(row.get("business_pnl") or 0) for row in before_rows)
        db._exec(
            cur,
            """
            INSERT INTO trading_business_allocation_audit
                (override_group_id, close_identity_id, before_allocations, after_allocations,
                 before_business_pnl, after_business_pnl, reason, operated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (group_id, close_identity_id, json.dumps(before_rows, ensure_ascii=False),
             json.dumps(after_rows, ensure_ascii=False), before_pnl, after_pnl, reason, actor),
        )
        conn.commit()
    return {
        "allocation_version": new_version,
        "business_pnl": after_pnl,
        "reconciliation": reconcile_business_pnl(close_identity_id),
    }


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or user.get("name") or user.get("id") or "unknown")


def _as_http_error(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _store_import_uploads(payload: TradingImportPreviewIn) -> dict[str, Path]:
    upload_dir = db.DATA_DIR / "trading_import_uploads" / uuid.uuid4().hex
    upload_dir.mkdir(parents=True, exist_ok=False)
    files = {
        "trade": payload.trade_file,
        "close": payload.close_file,
        "position": payload.position_file,
    }
    paths: dict[str, Path] = {}
    try:
        for key, item in files.items():
            suffix = Path(item.name).suffix.lower()
            if suffix not in {".xlsx", ".xlsm"}:
                raise ValueError("仅支持 xlsx 或 xlsm 文件")
            try:
                content = base64.b64decode(item.content_base64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"{item.name} 文件内容无效") from exc
            if not content:
                raise ValueError(f"{item.name} 文件为空")
            path = upload_dir / f"{key}{suffix}"
            path.write_bytes(content)
            paths[key] = path
        return paths
    except Exception:
        for path in upload_dir.glob("*"):
            path.unlink(missing_ok=True)
        upload_dir.rmdir()
        raise


def _cleanup_preview_files(batch_id: int) -> None:
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            "SELECT parse_summary FROM trading_import_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
    if not row:
        return
    paths = json.loads(row["parse_summary"] or "{}").get("paths", {})
    parents = set()
    for value in paths.values():
        path = Path(value)
        parents.add(path.parent)
        path.unlink(missing_ok=True)
    for parent in parents:
        if parent.name and parent.parent.name == "trading_import_uploads":
            try:
                parent.rmdir()
            except OSError:
                pass


def _api_filters(
    contract: str = "",
    direction: str = "",
    asset_type: str = "",
    open_close: str = "",
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    page_size: int = 20,
) -> FactFilters:
    return FactFilters(contract, direction, asset_type, open_close, start_date, end_date, page, page_size)


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


@router.get("/overview")
def get_trading_overview(
    filters: FactFilters = Depends(_api_filters),
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.overview", "view")
    return build_overview(filters)


def _get_trading_facts(
    view: str,
    filters: FactFilters = Depends(_api_filters),
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.facts", "view")
    if view not in {"positions", "closes", "trades"}:
        raise HTTPException(status_code=404, detail="未知事实视图")
    return query_fact_rows(view, filters)


@router.get("/facts/positions")
def get_trading_positions(
    filters: FactFilters = Depends(_api_filters),
    user=Depends(trading_management_current_user),
):
    return _get_trading_facts("positions", filters, user)


@router.get("/facts/closes")
def get_trading_closes(
    filters: FactFilters = Depends(_api_filters),
    user=Depends(trading_management_current_user),
):
    return _get_trading_facts("closes", filters, user)


@router.get("/facts/trades")
def get_trading_trades(
    filters: FactFilters = Depends(_api_filters),
    user=Depends(trading_management_current_user),
):
    return _get_trading_facts("trades", filters, user)


@router.get("/imports")
def get_trading_imports(user=Depends(trading_management_current_user)):
    require_permission(user, "trading.imports", "view")
    with db.connect() as conn:
        rows = db._exec(
            conn.cursor(),
            "SELECT * FROM trading_import_batches ORDER BY created_at DESC, id DESC",
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.get("/imports/{batch_id}/validation")
def get_trading_import_validation(batch_id: int, user=Depends(trading_management_current_user)):
    require_permission(user, "trading.imports", "view")
    with db.connect() as conn:
        batch = db._exec(
            conn.cursor(),
            "SELECT id, status, parse_summary FROM trading_import_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
    if not batch:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    return {
        "batch_id": batch["id"],
        "status": batch["status"],
        "summary": json.loads(batch["parse_summary"] or "{}"),
    }


@router.post("/imports/preview")
def post_trading_import_preview(
    payload: TradingImportPreviewIn,
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.imports", "import")
    paths: dict[str, Path] = {}
    try:
        paths = _store_import_uploads(payload)
        return preview_trading_import(
            payload.account_id, paths["trade"], paths["close"], paths["position"], _actor(user)
        )
    except ValueError as exc:
        for path in paths.values():
            path.unlink(missing_ok=True)
        if paths:
            try:
                next(iter(paths.values())).parent.rmdir()
            except OSError:
                pass
        raise _as_http_error(exc) from exc


@router.post("/imports/{preview_batch_id}/confirm")
def post_trading_import_confirm(
    preview_batch_id: int,
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.imports", "import")
    try:
        result = confirm_trading_import(preview_batch_id, _actor(user))
        result["matching"] = match_imported_facts(result["batch_id"])
        result["business_allocations"] = rebuild_default_business_allocations(result["batch_id"])
        _cleanup_preview_files(preview_batch_id)
        return result
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.get("/config")
def get_trading_config(user=Depends(trading_management_current_user)):
    require_permission(user, "trading.config", "view")
    return list_trading_config()


@router.post("/business-subjects")
def post_business_subject(payload: BusinessSubjectIn, user=Depends(trading_management_current_user)):
    require_permission(user, "trading.config", "edit")
    try:
        return create_business_subject(payload.name, _actor(user))
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.post("/strategies")
def post_strategy(payload: StrategyIn, user=Depends(trading_management_current_user)):
    require_permission(user, "trading.config", "edit")
    try:
        return get_or_create_strategy(payload.name, _actor(user))
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.post("/business-assignments/batch-confirm")
def post_business_assignments(
    payload: BusinessAssignmentIn,
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.config", "edit")
    try:
        return classify_trade_identities(
            payload.identity_ids, payload.business_subject_id, payload.business_type,
            payload.strategy_name, payload.instruction_text, _actor(user),
        )
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.delete("/business-assignments/{trade_identity_id}")
def delete_business_assignment(
    trade_identity_id: int,
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.config", "delete")
    return remove_trade_assignment(trade_identity_id, _actor(user))


def _get_business_rows(
    view: str,
    tab: str,
    filters: FactFilters,
    user: dict[str, Any],
):
    resource = "trading.junneng" if view == "junneng" else "trading.options"
    require_permission(user, resource, "view")
    try:
        return query_business_rows(view, tab, filters)
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.get("/business/junneng/{tab}")
def get_junneng_business_rows(
    tab: str,
    filters: FactFilters = Depends(_api_filters),
    user=Depends(trading_management_current_user),
):
    return _get_business_rows("junneng", tab, filters, user)


@router.get("/business/options/{tab}")
def get_option_business_rows(
    tab: str,
    filters: FactFilters = Depends(_api_filters),
    user=Depends(trading_management_current_user),
):
    return _get_business_rows("options", tab, filters, user)


@router.get("/business-closes/{close_identity_id}/candidates")
def get_rematch_candidates(close_identity_id: int, user=Depends(trading_management_current_user)):
    require_permission(user, "trading.config", "edit")
    try:
        return {"items": list_business_close_candidates(close_identity_id)}
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.post("/business-closes/{close_identity_id}/preview")
def post_rematch_preview(
    close_identity_id: int,
    payload: RematchPreviewIn,
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.config", "edit")
    try:
        return preview_business_rematch(
            close_identity_id,
            [item.dict() for item in payload.selections],
            payload.allocation_version,
        )
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.post("/business-closes/{close_identity_id}/confirm")
def post_rematch_confirm(
    close_identity_id: int,
    payload: RematchConfirmIn,
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.config", "edit")
    try:
        return confirm_business_rematch(
            close_identity_id, payload.preview_token, payload.allocation_version,
            _actor(user), payload.reason,
        )
    except ValueError as exc:
        raise _as_http_error(exc) from exc


@router.post("/business-closes/{close_identity_id}/restore-default")
def post_restore_default(
    close_identity_id: int,
    payload: RestoreDefaultIn,
    user=Depends(trading_management_current_user),
):
    require_permission(user, "trading.config", "edit")
    try:
        return restore_default_business_match(
            close_identity_id, payload.allocation_version, _actor(user), payload.reason,
        )
    except ValueError as exc:
        raise _as_http_error(exc) from exc
