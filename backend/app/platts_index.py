"""Platts index OCR import, review, revision, and read-only chart APIs."""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from . import db
from .permissions import require_permission
from .platts_ocr import (
    RAW_FIELDS,
    OCRProviderError,
    MockTableOCRProvider,
    get_ocr_provider,
    image_sha256,
    json_safe_result,
    parse_decimal,
    parse_table_payload,
    quantize_display,
    validate_image_bytes,
)


router = APIRouter()
PLATTS_RESOURCE = "platts_index.data"
IMPORT_RESOURCE = "platts_index.imports"
MANAGE_RESOURCE = "platts_index.manage"
MONTH_PATTERN = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
TIMESTAMP_SECONDS_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:\.\d+)?(.*)$"
)

SERIES = (
    ("platts_lp", "Platts LP", "美元/吨"),
    ("platts_61", "Platts 61%", "美元/吨"),
    ("platts_58", "Platts 58%", "美元/吨"),
    ("platts_65", "Platts 65%", "美元/吨"),
    ("spread_65_62", "Platts 65/62", "美元/吨"),
    ("spread_65_61", "Platts 65/61", "美元/吨"),
)
COUNT_KEYS = ("added", "backfilled", "same_skipped", "overwritten", "pending_review")


def _timestamp_to_seconds(value: Any) -> Any:
    if value is None:
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    match = TIMESTAMP_SECONDS_PATTERN.fullmatch(text.strip())
    return f"{match.group(1)}{match.group(2)}" if match else value


class PlattsUploadIn(BaseModel):
    file_name: str = Field(default="wechat-screenshot.png", max_length=255)
    file_data: str = Field(min_length=1)


class PlattsConfirmIn(BaseModel):
    draft_token: str = Field(min_length=1, max_length=128)
    rows: list[dict[str, Any]] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class PlattsDailyPatchIn(BaseModel):
    platts_lp: Any
    platts_61: Any
    platts_58: Any
    platts_65: Any
    spread_61_62: Any
    reason: str = Field(min_length=1, max_length=500)


def _current_user(authorization: Optional[str] = Header(default=None)) -> dict:
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def _user_id(user: dict) -> int | None:
    value = user.get("id")
    return int(value) if value is not None else None


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else parse_decimal(value, field)
    except InvalidOperation as exc:
        raise ValueError(f"{field} 不是有效数字") from exc
    return quantize_display(result, field)


def calculate_derived(row: dict[str, Any]) -> dict[str, Decimal]:
    """Calculate derived spreads using Decimal-only arithmetic."""
    platts_61 = _decimal(row["platts_61"], "platts_61")
    spread_61_62 = _decimal(row["spread_61_62"], "spread_61_62")
    platts_65 = _decimal(row["platts_65"], "platts_65")
    return {
        "platts_62_equivalent": platts_61 + spread_61_62,
        "spread_65_62": platts_65 - platts_61 - spread_61_62,
        "spread_65_61": platts_65 - platts_61,
    }


def calculate_mtd(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    if not rows:
        return {}
    result: dict[str, Decimal] = {}
    for field in RAW_FIELDS:
        values = [_decimal(row[field], field) for row in rows]
        result[field] = quantize_display(
            sum(values, Decimal("0")) / Decimal(len(values)),
            field,
        )
    result.update(calculate_derived(result))
    return result


def _empty_counts() -> dict[str, int]:
    return {key: 0 for key in COUNT_KEYS}


def _month_key(business_date: str) -> str:
    return str(business_date)[:7]


def _row_with_derived(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {"business_date": str(row["business_date"])}
    for field in RAW_FIELDS:
        normalized[field] = _decimal(row[field], field)
    normalized.update(calculate_derived(normalized))
    return normalized


def _normalize_confirm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    seen: set[str] = set()
    for row in rows:
        business_date = str(row.get("business_date", "")).strip()
        try:
            business_date = date.fromisoformat(business_date).isoformat()
        except ValueError as exc:
            raise ValueError(f"日期无效: {business_date}") from exc
        if business_date in seen:
            raise ValueError(f"重复日期: {business_date}")
        seen.add(business_date)
        normalized.append(_row_with_derived({**row, "business_date": business_date}))
    if not normalized:
        raise ValueError("至少需要一行数据")
    return sorted(normalized, key=lambda item: item["business_date"])


def _db_decimal(value: Any, field: str) -> Decimal:
    if value is None:
        raise ValueError(f"数据库缺少 {field}")
    return _decimal(Decimal(str(value)), field)


def _db_row(row: Any) -> dict[str, Any]:
    result = {"business_date": str(row["business_date"])}
    for field in RAW_FIELDS:
        result[field] = _db_decimal(row[field], field)
    result.update(calculate_derived(result))
    return result


def _same_raw(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(_decimal(left[field], field) == _decimal(right[field], field) for field in RAW_FIELDS)


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_date": row["business_date"],
        **{field: row[field] for field in RAW_FIELDS},
    }


def _json_text(value: Any) -> str:
    return json.dumps(json_safe_result(value), ensure_ascii=False, sort_keys=True)


def _sql_value(value: Any) -> Any:
    return format(value, "f") if isinstance(value, Decimal) else value


def _parsed_preview(
    parsed: dict[str, Any],
    rows: list[dict[str, Any]],
    conflicts: list[dict[str, Any]] | None = None,
    counts: dict[str, int] | None = None,
    mtd_checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return json_safe_result({
        "rows": [_row_with_derived(row) for row in rows],
        "mtd": parsed.get("mtd", {}),
        "calculated_mtd": calculate_mtd(rows) if rows else {},
        "merged_mtd": (mtd_checks or {}).get("merged_month_mtd", {}),
        "mtd_checks": mtd_checks or {},
        "issues": parsed.get("issues", []),
        "warnings": parsed.get("warnings", []),
        "conflicts": conflicts or [],
        "counts": counts or _empty_counts(),
    })


def _month_rows(conn, month: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    rows = db._exec(
        cur,
        "SELECT * FROM platts_index_daily WHERE business_date LIKE ? ORDER BY business_date",
        (f"{month}-%",),
    ).fetchall()
    return [_db_row(row) for row in rows]


def _available_months(conn) -> list[str]:
    cur = conn.cursor()
    rows = db._exec(cur, "SELECT business_date FROM platts_index_daily").fetchall()
    return sorted({_month_key(row["business_date"]) for row in rows}, reverse=True)


def _merged_month_rows(conn, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not incoming:
        return []
    months = {_month_key(row["business_date"]) for row in incoming}
    merged: dict[str, dict[str, Any]] = {}
    for month in months:
        for row in _month_rows(conn, month):
            merged[row["business_date"]] = row
    for row in incoming:
        merged[row["business_date"]] = _row_with_derived(row)
    return [merged[key] for key in sorted(merged)]


def _mtd_matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if not all(field in left for field in RAW_FIELDS) or not all(field in right for field in RAW_FIELDS):
        return False
    return all(
        quantize_display(parse_decimal(left[field], field), field)
        == quantize_display(parse_decimal(right[field], field), field)
        for field in RAW_FIELDS
    )


def _insert_batch(
    conn,
    *,
    source_hash: str,
    status: str,
    provider_name: str,
    request_id: str | None,
    normalized_payload: dict[str, Any],
    detected_count: int,
    error_summary: str | None,
    user: dict,
) -> int:
    cur = conn.cursor()
    db._exec(
        cur,
        """
        INSERT INTO platts_index_import_batches
            (draft_token, source_hash, status, ocr_provider, provider_request_id,
             normalized_payload, detected_count, error_summary, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            secrets.token_urlsafe(32),
            source_hash,
            status,
            provider_name,
            request_id,
            _json_text(normalized_payload),
            detected_count,
            error_summary,
            _user_id(user),
        ),
    )
    return int(db.last_insert_id(conn))


def _batch_token(conn, batch_id: int) -> str:
    cur = conn.cursor()
    row = db._exec(cur, "SELECT draft_token FROM platts_index_import_batches WHERE id = ?", (batch_id,)).fetchone()
    return str(row["draft_token"])


def _insert_daily(conn, row: dict[str, Any], *, batch_id: int, source_hash: str, user: dict) -> None:
    cur = conn.cursor()
    db._exec(
        cur,
        """
        INSERT INTO platts_index_daily
            (business_date, platts_lp, platts_61, platts_58, platts_65, spread_61_62,
             source_hash, import_batch_id, created_by, updated_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["business_date"],
            *(_sql_value(row[field]) for field in RAW_FIELDS),
            source_hash,
            batch_id,
            _user_id(user),
            _user_id(user),
        ),
    )


def _update_daily(conn, row: dict[str, Any], *, batch_id: int, source_hash: str, user: dict) -> None:
    cur = conn.cursor()
    db._exec(
        cur,
        """
        UPDATE platts_index_daily
        SET platts_lp = ?, platts_61 = ?, platts_58 = ?, platts_65 = ?, spread_61_62 = ?,
            source_hash = ?, import_batch_id = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
        WHERE business_date = ?
        """,
        (
            *(_sql_value(row[field]) for field in RAW_FIELDS),
            source_hash,
            batch_id,
            _user_id(user),
            row["business_date"],
        ),
    )


def _classify_rows(conn, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cur = conn.cursor()
    conflicts = []
    counts = _empty_counts()
    month_max_dates: dict[str, str | None] = {}
    for row in rows:
        existing = db._exec(
            cur,
            "SELECT * FROM platts_index_daily WHERE business_date = ?",
            (row["business_date"],),
        ).fetchone()
        if existing:
            stored = _db_row(existing)
            if _same_raw(stored, row):
                counts["same_skipped"] += 1
            else:
                conflicts.append({
                    "business_date": row["business_date"],
                    "existing": stored,
                    "incoming": row,
                })
            continue
        month = _month_key(row["business_date"])
        if month not in month_max_dates:
            latest = db._exec(
                cur,
                "SELECT MAX(business_date) AS latest_date FROM platts_index_daily WHERE business_date LIKE ?",
                (f"{month}-%",),
            ).fetchone()
            month_max_dates[month] = str(latest["latest_date"] or "") or None
        latest_date = month_max_dates[month]
        if latest_date and row["business_date"] < latest_date:
            counts["backfilled"] += 1
        else:
            counts["added"] += 1
    counts["pending_review"] = len(conflicts)
    return conflicts, counts


def _daily_conflicts(conn, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _classify_rows(conn, rows)[0]


def _mtd_validation(conn, parsed: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    screenshot_mtd = parsed.get("mtd", {})
    date_rows_mtd = calculate_mtd(rows) if rows else {}
    merged_rows = _merged_month_rows(conn, rows)
    merged_month_mtd = calculate_mtd(merged_rows) if merged_rows else {}
    date_rows_match = _mtd_matches(screenshot_mtd, date_rows_mtd)
    merged_month_match = _mtd_matches(screenshot_mtd, merged_month_mtd)
    months = sorted({_month_key(row["business_date"]) for row in rows})
    return {
        "screenshot_mtd": screenshot_mtd,
        "date_rows_mtd": date_rows_mtd,
        "merged_month_mtd": merged_month_mtd,
        "date_rows_match": date_rows_match,
        "merged_month_match": merged_month_match,
        "months": months,
    }


def _error_message(error: Exception) -> str:
    if isinstance(error, OCRProviderError):
        return str(error)
    if isinstance(error, TimeoutError):
        return "OCR 供应商超时"
    return "OCR 供应商异常"


def _stored_counts(stored: dict[str, Any], imported_count: Any = 0, skipped_count: Any = 0) -> dict[str, int]:
    counts = _empty_counts()
    raw_counts = stored.get("counts") if isinstance(stored, dict) else None
    if isinstance(raw_counts, dict):
        for key in COUNT_KEYS:
            try:
                counts[key] = int(raw_counts.get(key, 0) or 0)
            except (TypeError, ValueError):
                counts[key] = 0
    if not any(counts.values()):
        try:
            counts["added"] = int(imported_count or 0)
        except (TypeError, ValueError):
            counts["added"] = 0
        try:
            counts["same_skipped"] = int(skipped_count or 0)
        except (TypeError, ValueError):
            counts["same_skipped"] = 0
    return counts


def process_platts_import(image_bytes: bytes, *, user: dict, provider: Any | None = None) -> dict[str, Any]:
    """Recognize one image and either import it atomically or create a review draft."""
    validate_image_bytes(image_bytes)
    source_hash = image_sha256(image_bytes)

    with db.connect() as conn:
        cur = conn.cursor()
        reused = db._exec(
            cur,
            """
            SELECT id, draft_token, normalized_payload, imported_count, skipped_count
            FROM platts_index_import_batches
            WHERE source_hash = ? AND status = 'imported'
            ORDER BY id DESC
            LIMIT 1
            """,
            (source_hash,),
        ).fetchone()
        if reused:
            stored = json.loads(reused["normalized_payload"] or "{}")
            stored_rows = [
                _row_with_derived(row)
                for row in stored.get("rows", [])
                if isinstance(row, dict) and row.get("business_date")
            ]
            if stored_rows:
                with db.connect() as conn:
                    conflicts, counts = _classify_rows(conn, stored_rows)
                counts["pending_review"] = len(conflicts)
                stored = {
                    **stored,
                    "conflicts": conflicts,
                    "counts": counts,
                }
                imported_count = 0
                skipped_count = counts["same_skipped"]
            else:
                counts = _stored_counts(stored, reused["imported_count"], reused["skipped_count"])
                imported_count = reused["imported_count"]
                skipped_count = reused["skipped_count"]
            return {
                "status": "imported",
                "reused": True,
                "batch_id": reused["id"],
                "imported_count": imported_count,
                "skipped_count": skipped_count,
                "counts": counts,
                "preview": stored,
            }

    provider = provider or None
    provider_name = getattr(provider, "name", "aliyun")
    try:
        provider = provider or get_ocr_provider()
        provider_name = getattr(provider, "name", provider.__class__.__name__.lower())
        attempts = 0
        while True:
            attempts += 1
            try:
                vendor_payload = provider.recognize(image_bytes)
                break
            except Exception as exc:
                if attempts == 1 and getattr(exc, "retryable", False):
                    continue
                raise
    except Exception as exc:
        with db.connect() as conn:
            batch_id = _insert_batch(
                conn,
                source_hash=source_hash,
                status="failed",
                provider_name=provider_name,
                request_id=None,
                normalized_payload={"rows": [], "mtd": {}, "issues": [], "warnings": []},
                detected_count=0,
                error_summary=_error_message(exc),
                user=user,
            )
            token = _batch_token(conn, batch_id)
            return {
                "status": "failed",
                "reused": False,
                "batch_id": batch_id,
                "draft_token": token,
                "counts": _empty_counts(),
                "issues": [{"code": "provider_error", "message": _error_message(exc)}],
            }

    parsed = parse_table_payload(vendor_payload)
    rows = [_row_with_derived(row) for row in parsed.get("rows", [])]
    with db.connect() as conn:
        conflicts, counts = _classify_rows(conn, rows)
        mtd_checks = _mtd_validation(conn, parsed, rows)
        effective_issues = list(parsed.get("issues") or [])
        if mtd_checks["merged_month_match"]:
            effective_issues = [issue for issue in effective_issues if issue.get("code") != "mtd_mismatch"]
        elif (
            rows
            and len(parsed.get("mtd", {})) == len(RAW_FIELDS)
            and not mtd_checks["date_rows_match"]
            and not any(issue.get("code") == "merged_mtd_mismatch" for issue in effective_issues)
        ):
            effective_issues.append({
                "code": "merged_mtd_mismatch",
                "message": "截图 MTD 与合并后的月度有效日期平均值均不一致",
            })
        effective_parsed = {**parsed, "issues": effective_issues}
        requires_review = bool(effective_issues or parsed.get("warnings") or conflicts)
        if requires_review:
            counts["pending_review"] = len(conflicts) or max(1, len(rows))
        else:
            counts["pending_review"] = 0
        preview = _parsed_preview(effective_parsed, rows, conflicts, counts, mtd_checks)
        status = "review_required" if requires_review else "imported"
        batch_id = _insert_batch(
            conn,
            source_hash=source_hash,
            status=status,
            provider_name=provider_name,
            request_id=parsed.get("request_id"),
            normalized_payload=preview,
            detected_count=len(rows),
            error_summary=_json_text({"issues": effective_issues, "warnings": parsed.get("warnings", []), "conflicts": conflicts}) if requires_review else None,
            user=user,
        )
        if not requires_review:
            cur = conn.cursor()
            for row in rows:
                existing = db._exec(
                    cur,
                    "SELECT * FROM platts_index_daily WHERE business_date = ?",
                    (row["business_date"],),
                ).fetchone()
                if existing:
                    continue
                _insert_daily(conn, row, batch_id=batch_id, source_hash=source_hash, user=user)
            imported_count = counts["added"] + counts["backfilled"]
            skipped_count = counts["same_skipped"]
            preview["counts"] = counts
            db._exec(
                cur,
                "UPDATE platts_index_import_batches SET imported_count = ?, skipped_count = ?, normalized_payload = ? WHERE id = ?",
                (imported_count, skipped_count, _json_text(preview), batch_id),
            )
        else:
            imported_count = 0
            skipped_count = counts["same_skipped"]
        token = _batch_token(conn, batch_id)
    result = {
        "status": status,
        "reused": False,
        "batch_id": batch_id,
        "draft_token": token,
        "preview": preview,
        "counts": counts,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
    }
    return json_safe_result(result)


def confirm_platts_import(draft_token: str, rows: list[dict[str, Any]], *, user: dict, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("人工复核必须填写确认原因")
    normalized = _normalize_confirm_rows(rows)
    with db.connect() as conn:
        cur = conn.cursor()
        batch = db._exec(
            cur,
            "SELECT * FROM platts_index_import_batches WHERE draft_token = ?",
            (draft_token,),
        ).fetchone()
        if not batch:
            raise ValueError("复核草稿不存在或已过期")
        if batch["status"] != "review_required":
            raise ValueError("当前草稿不是待复核状态")
        _, classified = _classify_rows(conn, normalized)
        counts = _empty_counts()
        counts["added"] = classified["added"]
        counts["backfilled"] = classified["backfilled"]
        for row in normalized:
            existing = db._exec(
                cur,
                "SELECT * FROM platts_index_daily WHERE business_date = ?",
                (row["business_date"],),
            ).fetchone()
            if not existing:
                _insert_daily(
                    conn,
                    row,
                    batch_id=batch["id"],
                    source_hash=batch["source_hash"],
                    user=user,
                )
                continue
            previous = _db_row(existing)
            if _same_raw(previous, row):
                counts["same_skipped"] += 1
                continue
            db._exec(
                cur,
                """
                INSERT INTO platts_index_revisions
                    (business_date, import_batch_id, previous_payload, new_payload, reason, changed_by)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["business_date"],
                    batch["id"],
                    _json_text(_row_payload(previous)),
                    _json_text(_row_payload(row)),
                    reason.strip(),
                    _user_id(user),
                ),
            )
            _update_daily(
                conn,
                row,
                batch_id=batch["id"],
                source_hash=batch["source_hash"],
                user=user,
            )
            counts["overwritten"] += 1
        counts["pending_review"] = 0
        imported_count = counts["added"] + counts["backfilled"] + counts["overwritten"]
        skipped_count = counts["same_skipped"]
        stored_preview = json.loads(batch["normalized_payload"] or "{}")
        stored_preview["counts"] = counts
        stored_preview["review_status"] = "confirmed"
        db._exec(
            cur,
            """
            UPDATE platts_index_import_batches
            SET status = 'imported', imported_count = ?, skipped_count = ?, normalized_payload = ?,
                error_summary = NULL, confirmed_by = ?, confirmed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (imported_count, skipped_count, _json_text(stored_preview), _user_id(user), batch["id"]),
        )
        months = sorted({_month_key(row["business_date"]) for row in normalized})
        monthly_mtd = {
            month: calculate_mtd(_month_rows(conn, month))
            for month in months
        }
    return json_safe_result({
        "status": "imported",
        "batch_id": batch["id"],
        "counts": counts,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "revision_count": counts["overwritten"],
        "mtd": monthly_mtd[months[0]] if len(months) == 1 else {},
        "monthly_mtd": monthly_mtd,
    })


def patch_platts_daily(business_date: str, payload: dict[str, Any], *, user: dict) -> dict[str, Any]:
    """Apply a single audited correction to an existing daily record."""
    try:
        normalized_date = date.fromisoformat(business_date).isoformat()
    except ValueError as exc:
        raise ValueError(f"日期无效: {business_date}") from exc
    reason = str(payload.get("reason", "")).strip()
    if not reason:
        raise ValueError("纠错必须填写原因")
    row = _row_with_derived({**payload, "business_date": normalized_date})
    with db.connect() as conn:
        cur = conn.cursor()
        existing = db._exec(
            cur,
            "SELECT * FROM platts_index_daily WHERE business_date = ?",
            (normalized_date,),
        ).fetchone()
        if not existing:
            raise ValueError("该日期尚无已入库数据")
        previous = _db_row(existing)
        if _same_raw(previous, row):
            return {"status": "skipped", "business_date": normalized_date}
        batch_id = existing["import_batch_id"]
        source_hash = existing["source_hash"] or f"manual:{image_sha256(normalized_date.encode('utf-8'))}"
        if not batch_id:
            batch_id = _insert_batch(
                conn,
                source_hash=source_hash,
                status="imported",
                provider_name="manual",
                request_id=None,
                normalized_payload={"rows": [_row_payload(row)]},
                detected_count=1,
                error_summary=None,
                user=user,
            )
        db._exec(
            cur,
            """
            INSERT INTO platts_index_revisions
                (business_date, import_batch_id, previous_payload, new_payload, reason, changed_by)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_date,
                batch_id,
                _json_text(_row_payload(previous)),
                _json_text(_row_payload(row)),
                reason,
                _user_id(user),
            ),
        )
        _update_daily(conn, row, batch_id=batch_id, source_hash=source_hash, user=user)
    return json_safe_result({
        "status": "updated",
        "business_date": normalized_date,
        "revision_count": 1,
        "row": row,
    })


def _check_month(month: str) -> str:
    if not MONTH_PATTERN.fullmatch(month):
        raise HTTPException(status_code=400, detail="月份格式必须为 YYYY-MM")
    return month


def _summary(month: str) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            "SELECT * FROM platts_index_daily WHERE business_date LIKE ? ORDER BY business_date",
            (f"{month}-%",),
        ).fetchall()
        last_success = db._exec(
            cur,
            """
            SELECT COALESCE(confirmed_at, created_at) AS last_success_at
            FROM platts_index_import_batches
            WHERE status = 'imported'
            ORDER BY COALESCE(confirmed_at, created_at) DESC, id DESC
            LIMIT 1
            """,
        ).fetchone()
        available_months = _available_months(conn)
    normalized = [_db_row(row) for row in rows]
    mtd = calculate_mtd(normalized)
    series = {}
    for key, label, unit in SERIES:
        latest_row = normalized[-1] if normalized else None
        series[key] = {
            "label": label,
            "unit": unit,
            "points": [
                {"date": row["business_date"], "value": row[key]} for row in normalized
            ],
            "latest": (
                {"date": latest_row["business_date"], "value": latest_row[key]}
                if latest_row else None
            ),
            "mtd": mtd.get(key),
        }
    return {
        "month": month,
        "latest_month": available_months[0] if available_months else None,
        "available_months": available_months,
        "last_success_at": _timestamp_to_seconds(last_success["last_success_at"]) if last_success else None,
        "count": len(normalized),
        "mtd": mtd,
        "series": series,
        "rows": normalized,
    }


def _serialize_batch(row: Any) -> dict[str, Any]:
    stored = json.loads(row["normalized_payload"] or "{}")
    counts = _stored_counts(stored, row["imported_count"], row["skipped_count"])
    return {
        "id": row["id"],
        "draft_token": row["draft_token"],
        "source_hash": row["source_hash"],
        "status": row["status"],
        "ocr_provider": row["ocr_provider"],
        "provider_request_id": row["provider_request_id"],
        "detected_count": row["detected_count"],
        "imported_count": row["imported_count"],
        "skipped_count": row["skipped_count"],
        "counts": counts,
        "error_summary": row["error_summary"],
        "created_at": _timestamp_to_seconds(row["created_at"]),
        "confirmed_at": _timestamp_to_seconds(row["confirmed_at"]),
    }


@router.post("/platts-index/import/recognize")
def recognize_platts_image(payload: PlattsUploadIn, user=Depends(_current_user)):
    require_permission(user, IMPORT_RESOURCE, "import")
    encoded = payload.file_data.split(",", 1)[-1]
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="图片编码无效") from exc
    try:
        return process_platts_import(image_bytes, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/platts-index/import/confirm")
def confirm_platts_image(payload: PlattsConfirmIn, user=Depends(_current_user)):
    require_permission(user, IMPORT_RESOURCE, "import")
    try:
        return confirm_platts_import(payload.draft_token, payload.rows, user=user, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/platts-index/summary")
def get_platts_summary(month: Optional[str] = None, user=Depends(_current_user)):
    require_permission(user, PLATTS_RESOURCE, "view")
    selected_month = month or date.today().strftime("%Y-%m")
    return _summary(_check_month(selected_month))


@router.get("/platts-index/months")
def get_platts_months(user=Depends(_current_user)):
    require_permission(user, PLATTS_RESOURCE, "view")
    with db.connect() as conn:
        months = _available_months(conn)
    return {"months": months, "latest_month": months[0] if months else None}


@router.get("/platts-index/daily")
def get_platts_daily(month: Optional[str] = None, user=Depends(_current_user)):
    require_permission(user, PLATTS_RESOURCE, "view")
    selected_month = _check_month(month or date.today().strftime("%Y-%m"))
    return _summary(selected_month)["rows"]


@router.get("/platts-index/imports")
def list_platts_imports(limit: int = 20, user=Depends(_current_user)):
    require_permission(user, MANAGE_RESOURCE, "manage")
    limit = max(1, min(limit, 100))
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            "SELECT * FROM platts_index_import_batches ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_serialize_batch(row) for row in rows]


@router.patch("/platts-index/daily/{business_date}")
def patch_platts_daily_route(business_date: str, payload: PlattsDailyPatchIn, user=Depends(_current_user)):
    require_permission(user, MANAGE_RESOURCE, "manage")
    try:
        values = payload.model_dump(exclude={"reason"}) if hasattr(payload, "model_dump") else payload.dict(exclude={"reason"})
        values["reason"] = payload.reason
        return patch_platts_daily(business_date, values, user=user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


__all__ = [
    "MockTableOCRProvider",
    "calculate_derived",
    "calculate_mtd",
    "confirm_platts_import",
    "patch_platts_daily",
    "process_platts_import",
    "router",
]
