"""OCR provider adapters and strict Platts table parsing.

The business layer consumes the small, provider-neutral ``cells`` structure
returned by this module.  The real vendor response is never persisted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Iterable
from urllib.parse import quote

import requests


RAW_FIELDS = (
    "platts_lp",
    "platts_61",
    "platts_58",
    "platts_65",
    "spread_61_62",
)
FIELD_SCALES = {
    "platts_lp": 4,
    "platts_61": 2,
    "platts_58": 2,
    "platts_65": 2,
    "spread_61_62": 2,
}
LOW_CONFIDENCE_THRESHOLD = Decimal("0.80")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_OCR_RESPONSE_BYTES = 5 * 1024 * 1024

HEADER_ALIASES = {
    "business_date": (
        "日期",
        "评估日",
        "assessmentdate",
        "businessdate",
        "date",
    ),
    "platts_lp": (
        "plattslp",
        "lp",
        "块矿溢价",
    ),
    "platts_61": (
        "platts61",
        "61fe",
        "61%fe",
        "61iodex",
    ),
    "platts_58": (
        "platts58",
        "58fe",
        "58%fe",
    ),
    "platts_65": (
        "platts65",
        "65fe",
        "65%fe",
    ),
    "spread_61_62": (
        "platts6261",
        "platts6162",
        "6261",
        "6162",
        "transitionalbasisspread",
        "过渡价差",
        "61/62过渡价差",
        "62/61过渡价差",
    ),
}


class OCRProviderError(RuntimeError):
    """A vendor failure with a secret-safe, actionable error summary."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        vendor_code: str | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.vendor_code = vendor_code


class OCRProviderUnavailable(OCRProviderError):
    pass


def _normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"[\s%％:/\\()（）\[\]【】._\-]+", "", text)


def _header_field(value: Any) -> str | None:
    normalized = _normalized_text(value)
    if not normalized:
        return None
    exact = {
        _normalized_text(alias): field
        for field, aliases in HEADER_ALIASES.items()
        for alias in aliases
    }
    if normalized in exact:
        return exact[normalized]
    candidates = [
        (len(_normalized_text(alias)), field)
        for field, aliases in HEADER_ALIASES.items()
        for alias in aliases
        if len(_normalized_text(alias)) >= 3 and _normalized_text(alias) in normalized
    ]
    return max(candidates)[1] if candidates else None


def _parse_date(value: Any) -> str | None:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"\s+", "", text)
    matched = re.fullmatch(r"(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?", text)
    if not matched:
        return None
    try:
        return date(int(matched.group(1)), int(matched.group(2)), int(matched.group(3))).isoformat()
    except ValueError:
        return None


def _infer_business_date_column(
    rows_by_number: dict[int, list[dict[str, Any]]],
    header_row: int,
) -> int | None:
    date_counts: dict[int, int] = defaultdict(int)
    for row_number, row_cells in rows_by_number.items():
        if row_number <= header_row:
            continue
        for cell in row_cells:
            if _parse_date(cell.get("text")):
                _, col = _cell_coordinates(cell)
                date_counts[col] += 1
    if not date_counts:
        return None
    max_count = max(date_counts.values())
    candidates = [col for col, count in date_counts.items() if count == max_count and count >= 2]
    return candidates[0] if len(candidates) == 1 else None


def parse_decimal(value: Any, field: str = "value") -> Decimal:
    """Parse a numeric OCR cell without guessing or filling values."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u00a0", "")
    text = re.sub(r"\s+", "", text)
    text = text.replace("，", ",")
    if "," in text:
        text = text.replace(",", "")
    if not text or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", text):
        raise InvalidOperation(f"{field}: invalid numeric OCR value")
    return Decimal(text)


def quantize_display(value: Decimal, field: str) -> Decimal:
    scale = FIELD_SCALES.get(field, 2)
    return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


def _cell_coordinates(cell: dict[str, Any]) -> tuple[int, int]:
    row = cell.get("row", cell.get("ysc", cell.get("y", 0)))
    col = cell.get("col", cell.get("xsc", cell.get("x", 0)))
    return int(row or 0), int(col or 0)


def _cell_confidence(cell: dict[str, Any]) -> Decimal | None:
    value = cell.get("confidence", cell.get("prob"))
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if result > 1:
        result = result / Decimal("100")
    return result


def _data_object(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("Data", payload.get("data", payload))
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def normalize_ocr_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert Mock and Aliyun table responses to provider-neutral cells."""
    data = _data_object(payload)
    request_id = payload.get("RequestId") or payload.get("request_id") or data.get("RequestId")
    direct_cells = data.get("cells")
    if isinstance(direct_cells, list):
        cells = []
        for raw in direct_cells:
            if not isinstance(raw, dict):
                continue
            row, col = _cell_coordinates(raw)
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "text": raw.get("text", raw.get("word", "")),
                    "confidence": _cell_confidence(raw),
                }
            )
        return {"request_id": request_id, "cells": cells}

    words = {}
    for word in data.get("prism_wordsInfo", []) or []:
        if not isinstance(word, dict):
            continue
        key = (word.get("tableId", 0), word.get("tableCellId"))
        words[key] = word.get("prob")
    cells = []
    for table in data.get("prism_tablesInfo", []) or []:
        if not isinstance(table, dict):
            continue
        table_id = table.get("tableId", 0)
        for raw in table.get("cellInfos", []) or []:
            if not isinstance(raw, dict):
                continue
            cells.append(
                {
                    "row": int(raw.get("ysc", 0) or 0),
                    "col": int(raw.get("xsc", 0) or 0),
                    "text": raw.get("word", ""),
                    "confidence": _cell_confidence({"prob": words.get((table_id, raw.get("tableCellId")))}),
                }
            )
    return {"request_id": request_id, "cells": cells}


def _raw_mtd(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    if not rows:
        return {}
    return {
        field: sum((row[field] for row in rows), Decimal("0")) / Decimal(len(rows))
        for field in RAW_FIELDS
    }


def _relative_warnings(rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    warnings = []
    for row in rows:
        if row["platts_65"] <= row["platts_61"]:
            warnings.append({"code": "relative_order", "message": f"{row['business_date']} 的 65% 不高于 61%"})
        if row["platts_61"] <= row["platts_58"]:
            warnings.append({"code": "relative_order", "message": f"{row['business_date']} 的 61% 不高于 58%"})
        if abs(row["spread_61_62"]) > Decimal("20"):
            warnings.append({"code": "spread_range", "message": f"{row['business_date']} 的过渡价差超出常见范围"})
    return warnings


def parse_table_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_ocr_payload(payload)
    cells = normalized["cells"]
    rows_by_number: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        row, _ = _cell_coordinates(cell)
        rows_by_number[row].append(cell)

    header_candidates: dict[int, dict[str, list[int]]] = {}
    for row_number, row_cells in rows_by_number.items():
        row_fields: dict[str, list[int]] = defaultdict(list)
        for cell in row_cells:
            field = _header_field(cell.get("text"))
            if field:
                _, col = _cell_coordinates(cell)
                row_fields[field].append(col)
        header_candidates[row_number] = row_fields
    header_row = max(
        header_candidates,
        key=lambda row: (len(header_candidates[row]), -row),
        default=None,
    )
    issues: list[dict[str, str]] = []
    if header_row is None or len(header_candidates.get(header_row, {})) < 2:
        issues.append({"code": "missing_header", "message": "未定位到目标表头"})
        return {
            "request_id": normalized.get("request_id"),
            "rows": [],
            "mtd": {},
            "issues": issues,
            "warnings": [],
            "headers": {},
        }

    header_fields = header_candidates[header_row]
    if "business_date" not in header_fields:
        inferred_date_column = _infer_business_date_column(rows_by_number, header_row)
        if inferred_date_column is not None:
            header_fields["business_date"] = [inferred_date_column]
    headers: dict[str, int] = {}
    for field in ("business_date", *RAW_FIELDS):
        columns = header_fields.get(field, [])
        if not columns:
            issues.append({"code": "missing_header", "message": f"缺少表头: {field}"})
        elif len(columns) > 1:
            issues.append({"code": "duplicate_header", "message": f"表头不唯一: {field}"})
        else:
            headers[field] = columns[0]
    if issues:
        return {
            "request_id": normalized.get("request_id"),
            "rows": [],
            "mtd": {},
            "issues": issues,
            "warnings": [],
            "headers": headers,
        }

    cells_by_row_col = {
        (_cell_coordinates(cell)[0], _cell_coordinates(cell)[1]): cell
        for cell in cells
    }
    mtd_row = None
    for row_number, row_cells in rows_by_number.items():
        if row_number <= header_row:
            continue
        if any(_normalized_text(cell.get("text")) in {"mtd", "monthtodate", "月均", "月均值", "月内均值"} for cell in row_cells):
            mtd_row = row_number
            break

    rows: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for row_number in sorted(rows_by_number):
        if row_number <= header_row or row_number == mtd_row:
            continue
        date_cell = cells_by_row_col.get((row_number, headers["business_date"]))
        business_date = _parse_date(date_cell.get("text") if date_cell else "")
        if not business_date:
            continue
        target_cells = [
            cells_by_row_col.get((row_number, headers[field]))
            for field in RAW_FIELDS
        ]
        if not any(str(cell.get("text") or "").strip() for cell in target_cells if cell):
            # A date-only row is commonly the next empty/future row in the
            # cloud sheet and is not an OCR anomaly.
            continue
        values: dict[str, Decimal] = {}
        row_issues: list[dict[str, str]] = []
        for field in RAW_FIELDS:
            cell = cells_by_row_col.get((row_number, headers[field]))
            text = cell.get("text") if cell else ""
            if not str(text or "").strip():
                row_issues.append({"code": "missing_value", "message": f"{business_date} 缺少 {field}"})
                continue
            confidence = _cell_confidence(cell or {})
            if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
                row_issues.append({"code": "low_confidence", "message": f"{business_date} 的 {field} 置信度过低"})
            try:
                values[field] = parse_decimal(text, field)
                if values[field] != quantize_display(values[field], field):
                    row_issues.append({"code": "precision", "message": f"{business_date} 的 {field} 超出展示精度"})
            except InvalidOperation:
                row_issues.append({"code": "invalid_number", "message": f"{business_date} 的 {field} 不是有效数字"})
        if row_issues:
            issues.extend(row_issues)
            continue
        if business_date in seen_dates:
            issues.append({"code": "duplicate_date", "message": f"重复日期: {business_date}"})
            continue
        seen_dates.add(business_date)
        values["business_date"] = business_date
        rows.append(values)

    if not rows:
        issues.append({"code": "no_data_rows", "message": "未识别到完整日期行"})

    mtd: dict[str, Decimal] = {}
    if mtd_row is None:
        issues.append({"code": "missing_mtd", "message": "未识别到底部 MTD 行"})
    else:
        for field in RAW_FIELDS:
            cell = cells_by_row_col.get((mtd_row, headers[field]))
            text = cell.get("text") if cell else ""
            if not str(text or "").strip():
                issues.append({"code": "missing_mtd_value", "message": f"MTD 缺少 {field}"})
                continue
            try:
                mtd[field] = parse_decimal(text, f"MTD {field}")
                if mtd[field] != quantize_display(mtd[field], field):
                    issues.append({"code": "mtd_precision", "message": f"MTD 的 {field} 超出展示精度"})
            except InvalidOperation:
                issues.append({"code": "invalid_mtd", "message": f"MTD 的 {field} 不是有效数字"})

    if rows and len(mtd) == len(RAW_FIELDS):
        expected_mtd = _raw_mtd(rows)
        for field in RAW_FIELDS:
            if quantize_display(expected_mtd[field], field) != quantize_display(mtd[field], field):
                issues.append({"code": "mtd_mismatch", "message": f"{field} 的截图 MTD 与日期行平均值不一致"})

    warnings = _relative_warnings(rows)
    return {
        "request_id": normalized.get("request_id"),
        "rows": sorted(rows, key=lambda row: row["business_date"]),
        "mtd": mtd,
        "issues": issues,
        "warnings": warnings,
        "headers": headers,
    }


def _png_dimensions(image: bytes) -> tuple[int, int] | None:
    if len(image) < 24 or not image.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return int.from_bytes(image[16:20], "big"), int.from_bytes(image[20:24], "big")


def _jpeg_dimensions(image: bytes) -> tuple[int, int] | None:
    if not image.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 9 < len(image):
        if image[index] != 0xFF:
            index += 1
            continue
        marker = image[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(image):
            break
        length = int.from_bytes(image[index:index + 2], "big")
        if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
            if index + 7 <= len(image):
                return int.from_bytes(image[index + 5:index + 7], "big"), int.from_bytes(image[index + 3:index + 5], "big")
            break
        index += max(length, 2)
    return None


def validate_image_bytes(image: bytes) -> str:
    if not image or len(image) > MAX_IMAGE_BYTES:
        raise ValueError("图片不能为空且不能超过 10MB")
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
        dimensions = _png_dimensions(image)
    elif image.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
        dimensions = _jpeg_dimensions(image)
    else:
        raise ValueError("仅支持 PNG、JPG、JPEG 图片")
    if dimensions:
        width, height = dimensions
        if width <= 15 or height <= 15 or width >= 8192 or height >= 8192 or max(width / height, height / width) >= 50:
            raise ValueError("图片尺寸不符合 OCR 服务要求")
    return mime


class MockTableOCRProvider:
    name = "mock"

    def __init__(self, payload: dict[str, Any] | Callable[[bytes], dict[str, Any]]):
        self.payload = payload

    def recognize(self, image_bytes: bytes) -> dict[str, Any]:
        return self.payload(image_bytes) if callable(self.payload) else self.payload


def _vendor_error_details(payload: Any) -> tuple[str | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    code = payload.get("Code") or payload.get("code") or payload.get("ErrorCode")
    message = payload.get("Message") or payload.get("message") or payload.get("ErrorMessage")
    return (
        str(code).strip()[:120] if code else None,
        str(message).strip()[:240] if message else None,
    )


def _safe_vendor_error_message(
    fallback: str,
    payload: Any,
    *,
    access_key_id: str = "",
    access_key_secret: str = "",
) -> tuple[str, str | None]:
    code, message = _vendor_error_details(payload)
    if message:
        message = re.sub(r"[\r\n]+", " ", message)
        for secret in (access_key_id, access_key_secret):
            if secret:
                message = message.replace(secret, "[已隐藏]")
    detail = "：".join(part for part in (code, message) if part)
    return (f"{fallback}（{detail}）" if detail else fallback), code


class AliyunRecognizeTableOcrProvider:
    """Small HTTP adapter for Aliyun RecognizeTableOcr.

    Credentials are read only at call time from environment variables and are
    never included in logs, batch rows, or exception messages.
    """

    name = "aliyun"

    def __init__(self, *, request_fn: Callable[..., Any] | None = None, timeout: float | None = None):
        self.access_key_id = os.getenv("PLATTS_OCR_ACCESS_KEY_ID", "").strip()
        self.access_key_secret = os.getenv("PLATTS_OCR_ACCESS_KEY_SECRET", "").strip()
        self.endpoint = os.getenv("PLATTS_OCR_ENDPOINT", "https://ocr-api.cn-hangzhou.aliyuncs.com").strip()
        self.timeout = timeout or float(os.getenv("PLATTS_OCR_TIMEOUT_SECONDS", "30"))
        self.request_fn = request_fn or requests.post

    @staticmethod
    def _encode(value: Any) -> str:
        return quote(str(value), safe="-_.~")

    def _signed_params(self) -> dict[str, str]:
        params = {
            "AccessKeyId": self.access_key_id,
            "Action": "RecognizeTableOcr",
            "Format": "JSON",
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": secrets.token_hex(16),
            "SignatureVersion": "1.0",
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "Version": "2021-07-07",
            "NeedRotate": "true",
            "LineLess": "false",
            "SkipDetection": "false",
        }
        canonical = "&".join(f"{self._encode(key)}={self._encode(params[key])}" for key in sorted(params))
        string_to_sign = "POST&%2F&" + self._encode(canonical)
        signature = base64.b64encode(
            hmac.new(
                (self.access_key_secret + "&").encode("utf-8"),
                string_to_sign.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("ascii")
        params["Signature"] = signature
        return params

    def recognize(self, image_bytes: bytes) -> dict[str, Any]:
        if not self.access_key_id or not self.access_key_secret:
            raise OCRProviderUnavailable("OCR 供应商未配置", retryable=False)
        try:
            response = self.request_fn(
                self.endpoint,
                params=self._signed_params(),
                data=image_bytes,
                headers={"Content-Type": "application/octet-stream"},
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise OCRProviderError("OCR 供应商超时", retryable=True) from exc
        except requests.RequestException as exc:
            raise OCRProviderError("OCR 供应商连接失败", retryable=True) from exc
        response_payload = None
        try:
            response_payload = response.json()
        except (AttributeError, TypeError, ValueError):
            pass
        if response.status_code >= 500:
            message, vendor_code = _safe_vendor_error_message(
                "OCR 供应商暂时不可用",
                response_payload,
                access_key_id=self.access_key_id,
                access_key_secret=self.access_key_secret,
            )
            raise OCRProviderError(
                message,
                retryable=True,
                status_code=response.status_code,
                vendor_code=vendor_code,
            )
        if response.status_code >= 400:
            message, vendor_code = _safe_vendor_error_message(
                "OCR 供应商请求失败",
                response_payload,
                access_key_id=self.access_key_id,
                access_key_secret=self.access_key_secret,
            )
            raise OCRProviderError(
                message,
                retryable=False,
                status_code=response.status_code,
                vendor_code=vendor_code,
            )
        content_length = (getattr(response, "headers", {}) or {}).get("Content-Length")
        try:
            if content_length and int(content_length) > MAX_OCR_RESPONSE_BYTES:
                raise OCRProviderError("OCR 供应商响应过大", retryable=False)
        except (TypeError, ValueError):
            pass
        response_content = getattr(response, "content", None)
        if response_content is not None and len(response_content) > MAX_OCR_RESPONSE_BYTES:
            raise OCRProviderError("OCR 供应商响应过大", retryable=False)
        if response_payload is None:
            try:
                response_payload = response.json()
            except ValueError as exc:
                raise OCRProviderError("OCR 供应商返回无法解析", retryable=False) from exc
        payload = response_payload
        if not isinstance(payload, dict):
            raise OCRProviderError("OCR 供应商返回无法解析", retryable=False)
        if payload.get("Code"):
            message, vendor_code = _safe_vendor_error_message(
                "OCR 供应商拒绝请求",
                payload,
                access_key_id=self.access_key_id,
                access_key_secret=self.access_key_secret,
            )
            raise OCRProviderError(message, retryable=False, vendor_code=vendor_code)
        return payload


def get_ocr_provider() -> Any:
    if os.getenv("PLATTS_OCR_PROVIDER", "aliyun").strip().lower() == "mock":
        raise OCRProviderUnavailable("Mock OCR 仅可由测试注入", retryable=False)
    return AliyunRecognizeTableOcrProvider()


def image_sha256(image: bytes) -> str:
    return hashlib.sha256(image).hexdigest()


def serialize_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def json_safe_result(result: dict[str, Any]) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        if isinstance(value, Decimal):
            return serialize_decimal(value)
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    return convert(result)
