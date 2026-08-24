"""现货业务台账的来源适配、完整扫描、调度和历史迁移。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as day_time
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from openpyxl import load_workbook

from . import db
from .spot_ledger import (
    FIELD_CODES,
    FIELD_NAME_TO_CODE,
    FIELD_DEFINITIONS,
    MANUAL_FIELDS,
    SHANGHAI_GROUPS,
    SYSTEM_PRIORITY_FIELDS,
    calculate_derived_fields,
    initialize_schema,
    missing_required_fields,
    normalize_sales_contract_record,
    record_to_public,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SPOT_LEDGER_SYNC_TIMES = tuple(day_time(hour, 0) for hour in range(9, 19))
CANDIDATE_SOURCE_URL = "https://tds-report.ejianlong.com/jmreport/show"
_scheduler_lock = threading.Lock()
_scheduler_started = False


class SalesContractSourceError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.stage = code
        super().__init__(f"{code}: {message}")


@dataclass
class FullScanResult:
    records: list[dict[str, Any]]
    page_count: int
    expected_page_count: Optional[int]
    total_count: int
    complete: bool
    errors: list[Any] = field(default_factory=list)
    source_mode: str = "fixture"


class SalesContractSource:
    def fetch_full_scan(self) -> FullScanResult:
        raise NotImplementedError


def _now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now(SHANGHAI_TZ)
    return current.astimezone(SHANGHAI_TZ) if current.tzinfo else current.replace(tzinfo=SHANGHAI_TZ)


def _timestamp(value: Optional[datetime] = None) -> str:
    return _now(value).isoformat(timespec="seconds")


def _raw_execute(cur, sql: str, params: tuple[Any, ...] = ()):
    if db._is_pg():
        sql = sql.replace("?", "%s")
    cur.execute(sql, params)
    return cur


def _empty(value: Any) -> bool:
    return value is None or value == ""


def _fixture_records(payload: dict[str, Any]) -> FullScanResult:
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise SalesContractSourceError("parse_error", "fixture.records 必须是数组")
    normalized = [normalize_sales_contract_record(item) for item in raw_records if isinstance(item, dict)]
    records = [item for item in normalized if item.get("eligible")]
    return FullScanResult(
        records=records,
        page_count=int(payload.get("page_count") or 1),
        expected_page_count=(int(payload["expected_page_count"]) if payload.get("expected_page_count") is not None else None),
        total_count=len(records),
        complete=bool(payload.get("complete", True)),
        errors=list(payload.get("errors") or []),
        source_mode="fixture",
    )


class FixtureSalesContractSource(SalesContractSource):
    """明确标记为本地 fixture 的只读 source。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch_full_scan(self) -> FullScanResult:
        if not self.path.exists():
            raise SalesContractSourceError("fixture_missing", f"fixture 不存在: {self.path}")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SalesContractSourceError("fixture_parse_error", "fixture 读取失败") from exc
        if payload.get("source_mode") != "fixture":
            raise SalesContractSourceError("fixture_unmarked", "fixture 必须明确 source_mode=fixture")
        return validate_full_scan(_fixture_records(payload))


def _extract_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split(".") if path else []:
        if isinstance(current, dict):
            if part not in current:
                raise KeyError(path)
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current


class ProfiledSalesContractSource(SalesContractSource):
    """仅执行外部提供的、已验证的 request/response profile。

    profile 不能省略认证 provider、请求体、分页路径和字段映射；本类不会根据网页
    或字段名称猜测协议。认证 provider 由调用方在运行时传入，不会写入仓库或日志。
    """

    def __init__(
        self,
        profile: Optional[dict[str, Any]],
        http: Any = requests,
        auth_provider: Optional[Callable[[], dict[str, str]]] = None,
    ):
        self.profile = profile or {}
        self.http = http
        self.auth_provider = auth_provider

    @classmethod
    def from_env(cls) -> "ProfiledSalesContractSource":
        profile_path = (os.getenv("SPOT_LEDGER_SOURCE_PROFILE") or "").strip()
        profile: dict[str, Any] = {}
        if profile_path:
            try:
                profile = json.loads(Path(profile_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SalesContractSourceError("auth_unavailable", "source profile 读取失败") from exc
        # 不从环境变量读取或拼装未验证的 cookie/header；认证 provider 需由部署代码显式注入。
        return cls(profile)

    def _validate_profile(self) -> None:
        required = ("request_body", "records_path", "total_path", "page_count_path", "field_map")
        if self.profile.get("url") != CANDIDATE_SOURCE_URL or any(key not in self.profile for key in required):
            raise SalesContractSourceError("auth_unavailable", "真实源 profile 或认证方式尚未确认")
        if not callable(self.auth_provider):
            raise SalesContractSourceError("auth_unavailable", "无人值守认证 provider 尚未提供")

    def fetch_full_scan(self) -> FullScanResult:
        self._validate_profile()
        try:
            headers = self.auth_provider() or {}
            response = self.http.post(
                self.profile["url"],
                json=self.profile["request_body"],
                headers=headers,
                timeout=float(self.profile.get("timeout_seconds", 30)),
            )
        except SalesContractSourceError:
            raise
        except Exception as exc:
            raise SalesContractSourceError("source_request", "真实源请求失败") from exc
        if getattr(response, "status_code", 200) in {401, 403}:
            raise SalesContractSourceError("auth_unavailable", "真实源认证失败")
        if getattr(response, "status_code", 200) >= 400:
            raise SalesContractSourceError("source_request", "真实源返回错误")
        try:
            payload = response.json()
            external_records = _extract_path(payload, self.profile["records_path"])
            total_count = int(_extract_path(payload, self.profile["total_path"]))
            page_count = int(_extract_path(payload, self.profile["page_count_path"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SalesContractSourceError("parse_error", "真实源响应不符合已确认 profile") from exc
        if not isinstance(external_records, list):
            raise SalesContractSourceError("parse_error", "真实源记录路径不是数组")
        field_map = self.profile["field_map"]
        if not isinstance(field_map, dict) or not field_map:
            raise SalesContractSourceError("auth_unavailable", "真实源字段映射尚未确认")
        standard_records: list[dict[str, Any]] = []
        for external in external_records:
            if not isinstance(external, dict):
                raise SalesContractSourceError("parse_error", "真实源记录不是对象")
            standard_records.append({target: _extract_path(external, path) for target, path in field_map.items()})
        records = [normalize_sales_contract_record(item) for item in standard_records]
        records = [item for item in records if item.get("eligible")]
        return validate_full_scan(
            FullScanResult(
                records=records,
                page_count=page_count,
                expected_page_count=page_count,
                total_count=total_count,
                complete=True,
                errors=[],
                source_mode="profiled_http",
            )
        )


def validate_full_scan(scan: FullScanResult) -> FullScanResult:
    errors = list(scan.errors or [])
    ids = [str(record.get("source_detail_id") or "") for record in scan.records]
    if scan.expected_page_count is not None and scan.page_count != scan.expected_page_count:
        errors.append("page_count_mismatch")
    if scan.total_count != len(scan.records):
        errors.append("total_count_mismatch")
    if any(not detail_id for detail_id in ids):
        errors.append("missing_detail_id")
    if len(ids) != len(set(ids)):
        errors.append("duplicate_detail_id")
    complete = bool(scan.complete and not errors)
    return FullScanResult(
        records=scan.records,
        page_count=scan.page_count,
        expected_page_count=scan.expected_page_count,
        total_count=scan.total_count,
        complete=complete,
        errors=errors,
        source_mode=scan.source_mode,
    )


def _record_id(detail_id: str) -> str:
    return f"spot:{detail_id}"


def _payload_for_storage(record: dict[str, Any]) -> str:
    return json.dumps({key: value for key, value in record.items() if key != "sync_errors"}, ensure_ascii=False, default=str)


def _merge_record(incoming: dict[str, Any], existing: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(incoming)
    if existing:
        for field in MANUAL_FIELDS:
            if field in existing and not _empty(existing.get(field)):
                merged[field] = existing[field]
        # K 是系统优先补录字段：源有有效船名时覆盖，否则保留既有人工值。
        if _empty(incoming.get("K")) and not _empty(existing.get("K")):
            merged["K"] = existing["K"]
        merged["record_id"] = existing.get("record_id") or merged.get("record_id")
    return calculate_derived_fields(merged)


def _record_columns() -> list[str]:
    return [
        "record_id", "source_detail_id", "record_source_type", *FIELD_CODES, "long_contract_object", "eligible",
        "is_active", "supplement_status", "missing_fields", "sync_status", "last_synced_at", "sync_error_summary",
        "source_payload_json", "source_mode",
    ]


def _record_values(record: dict[str, Any], *, existing: Optional[dict[str, Any]], source_mode: str, timestamp: str) -> list[Any]:
    serialized = dict(record)
    serialized["record_id"] = record.get("record_id") or _record_id(str(record["source_detail_id"]))
    serialized["source_detail_id"] = record.get("source_detail_id")
    serialized["record_source_type"] = "现货同步"
    serialized["long_contract_object"] = record.get("long_contract_object") or ""
    missing = missing_required_fields(record)
    sync_errors = record.get("sync_errors") or []
    serialized["eligible"] = 1 if record.get("eligible") else 0
    serialized["is_active"] = 1
    serialized["supplement_status"] = "待补录" if missing else "已完成"
    serialized["missing_fields"] = json.dumps(missing, ensure_ascii=False)
    serialized["sync_status"] = "异常" if sync_errors else "正常"
    serialized["last_synced_at"] = timestamp
    serialized["sync_error_summary"] = json.dumps(sync_errors, ensure_ascii=False) if sync_errors else ""
    serialized["source_payload_json"] = _payload_for_storage(record)
    serialized["source_mode"] = source_mode
    return [serialized.get(column, "") for column in _record_columns()]


def _upsert_record(cur, record: dict[str, Any], source_mode: str, timestamp: str) -> tuple[bool, bool]:
    detail_id = str(record.get("source_detail_id") or "")
    existing_row = _raw_execute(cur, "SELECT * FROM spot_ledger_records WHERE source_detail_id = ?", (detail_id,)).fetchone()
    existing = dict(existing_row) if existing_row else None
    record = _merge_record({**record, "record_id": (existing or {}).get("record_id") or _record_id(detail_id)}, existing)
    columns = _record_columns()
    values = _record_values(record, existing=existing, source_mode=source_mode, timestamp=timestamp)
    quoted_columns = [f'"{column}"' if column in FIELD_CODES else column for column in columns]
    if existing:
        assignments = ", ".join(f"{column} = ?" for column in quoted_columns if column != "record_id")
        update_values = [value for column, value in zip(quoted_columns, values) if column != "record_id"]
        _raw_execute(cur, f"UPDATE spot_ledger_records SET {assignments}, updated_at = ? WHERE record_id = ?", (*update_values, timestamp, existing["record_id"]))
        return False, bool(record.get("sync_errors"))
    _raw_execute(
        cur,
        f"INSERT INTO spot_ledger_records ({', '.join(quoted_columns)}, created_at, updated_at) VALUES ({', '.join('?' for _ in columns)}, ?, ?)",
        (*values, timestamp, timestamp),
    )
    return True, bool(record.get("sync_errors"))


def _insert_run(cur, slot_key: str, started_at: str, finished_at: str, scan: FullScanResult, result: dict[str, Any]) -> None:
    status = "成功" if scan.complete else "异常"
    errors = list(scan.errors or []) + list(result.get("record_errors") or [])
    _raw_execute(
        cur,
        """
        INSERT INTO spot_ledger_sync_runs
            (id, slot_key, started_at, finished_at, status, source_mode, page_count,
             expected_page_count, total_count, inserted_count, updated_count, hidden_count,
             error_count, error_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex, slot_key, started_at, finished_at, status, scan.source_mode,
            scan.page_count, scan.expected_page_count, scan.total_count, result.get("inserted", 0),
            result.get("updated", 0), result.get("hidden", 0), len(errors), json.dumps(errors, ensure_ascii=False),
        ),
    )


def apply_full_scan(scan: FullScanResult, slot_key: str, now: Optional[datetime] = None) -> dict[str, Any]:
    """Apply one normalized full scan atomically; only a validated scan may soft-hide."""
    scan = validate_full_scan(scan)
    started_at = _timestamp(now)
    finished_at = _timestamp(now)
    result: dict[str, Any] = {"status": "success" if scan.complete else "error", "inserted": 0, "updated": 0, "hidden": 0, "record_errors": []}
    initialize_needed = True
    with db.connect() as conn:
        if initialize_needed:
            initialize_schema(conn)
        cur = conn.cursor()
        for incoming in scan.records:
            if not incoming.get("eligible"):
                continue
            inserted, has_error = _upsert_record(cur, incoming, scan.source_mode, finished_at)
            if inserted:
                result["inserted"] += 1
            else:
                result["updated"] += 1
            if has_error:
                result["record_errors"].append(incoming.get("sync_errors") or [])
        if scan.complete:
            ids = [str(record["source_detail_id"]) for record in scan.records if record.get("eligible")]
            if ids:
                marks = ", ".join("?" for _ in ids)
                hide_sql = f"UPDATE spot_ledger_records SET is_active = 0, updated_at = ? WHERE record_source_type = '现货同步' AND is_active = 1 AND source_detail_id NOT IN ({marks})"
                cursor = _raw_execute(cur, hide_sql, (finished_at, *ids))
            else:
                cursor = _raw_execute(cur, "UPDATE spot_ledger_records SET is_active = 0, updated_at = ? WHERE record_source_type = '现货同步' AND is_active = 1", (finished_at,))
            result["hidden"] = max(0, int(cursor.rowcount or 0))
        _insert_run(cur, slot_key, started_at, finished_at, scan, result)
    return result


def get_active_records() -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = db._exec(conn.cursor(), "SELECT * FROM spot_ledger_records WHERE is_active = 1 ORDER BY \"U\" DESC, record_id").fetchall()
    return [record_to_public(dict(row)) for row in rows]


def get_sync_runs(limit: int = 20) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = db._exec(
            conn.cursor(), "SELECT * FROM spot_ledger_sync_runs ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 100)),)
        ).fetchall()
    return [record_to_public(dict(row)) for row in rows]


def _slot_key(current: datetime, slot: day_time) -> str:
    return datetime.combine(current.date(), slot, SHANGHAI_TZ).isoformat(timespec="minutes")


def due_spot_ledger_slots(now: datetime, attempted_slots: Optional[set[str]] = None) -> list[str]:
    current = _now(now)
    attempted = attempted_slots or set()
    return [
        key for slot in SPOT_LEDGER_SYNC_TIMES
        if (key := _slot_key(current, slot)) not in attempted and datetime.fromisoformat(key) <= current
    ]


def _source_from_env() -> SalesContractSource:
    mode = (os.getenv("SPOT_LEDGER_SOURCE_MODE") or "profiled_http").strip().lower()
    if mode == "fixture":
        path = (os.getenv("SPOT_LEDGER_FIXTURE_PATH") or "").strip()
        if not path:
            raise SalesContractSourceError("fixture_missing", "SPOT_LEDGER_FIXTURE_PATH 未配置")
        return FixtureSalesContractSource(path)
    return ProfiledSalesContractSource.from_env()


def run_spot_ledger_sync_once(slot_key: str, source: Optional[SalesContractSource] = None) -> dict[str, Any]:
    try:
        scan = (source or _source_from_env()).fetch_full_scan()
        return apply_full_scan(scan, slot_key)
    except SalesContractSourceError as exc:
        now = _timestamp()
        initialize_schema_for_error = True
        with db.connect() as conn:
            if initialize_schema_for_error:
                initialize_schema(conn)
            _insert_run(
                conn.cursor(), slot_key, now, now,
                FullScanResult([], 0, None, 0, False, [exc.code], "profiled_http"),
                {"inserted": 0, "updated": 0, "hidden": 0, "record_errors": []},
            )
        raise


def _scheduler_loop(interval_seconds: int) -> None:
    attempted: set[str] = set()
    while True:
        current = _now()
        for slot_key in due_spot_ledger_slots(current, attempted):
            attempted.add(slot_key)
            try:
                run_spot_ledger_sync_once(slot_key)
            except Exception:
                # 错误已写入 sync_runs；调度线程继续等待下一 slot。
                pass
        cutoff = (current.date().toordinal() - 3)
        attempted = {slot for slot in attempted if datetime.fromisoformat(slot).date().toordinal() >= cutoff}
        time.sleep(max(1, interval_seconds))


def start_spot_ledger_sync_scheduler(interval_seconds: int = 30) -> bool:
    global _scheduler_started
    if (os.getenv("SPOT_LEDGER_AUTO_SYNC_ENABLED") or "").strip().lower() != "true":
        return False
    with _scheduler_lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
        thread = threading.Thread(target=_scheduler_loop, args=(interval_seconds,), daemon=True, name="spot-ledger-sync")
        thread.start()
    return True


def _history_value(row: dict[str, Any], code: str) -> Any:
    if code in row:
        return row[code]
    return row.get(FIELD_BY_CODE_NAME.get(code, ""), "")


FIELD_BY_CODE_NAME = {item["code"]: item["name"] for item in FIELD_DEFINITIONS}


def _usable_history_value(value: Any) -> bool:
    if value is None or value == "":
        return False
    return str(value).strip() not in {"—", "——", "-", "--", "***"}


def _history_row_to_values(headers: list[Any], values: tuple[Any, ...]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for header, value in zip(headers, values):
        header_text = str(header or "").strip()
        code = header_text if header_text in FIELD_CODES else FIELD_NAME_TO_CODE.get(header_text)
        if code:
            row[code] = value
        elif header_text in {"长协对象", "long_contract_object"}:
            row["long_contract_object"] = value
        elif header_text == "销售合同商品明细 ID":
            row["source_detail_id"] = value
    return row


def _matches_history(row: dict[str, Any], candidate: dict[str, Any]) -> bool:
    contract = row.get("AD")
    product = row.get("H")
    price = row.get("Z")
    quantity = row.get("X") if _usable_history_value(row.get("X")) else row.get("L")
    if not all(_usable_history_value(value) for value in (contract, product, price, quantity)):
        return False
    if str(candidate.get("AD") or "").strip() != str(contract).strip():
        return False
    if str(candidate.get("H") or "").strip() != str(product).strip():
        return False
    try:
        if abs(float(candidate.get("Z")) - float(price)) > 0.000001:
            return False
        candidate_quantity = candidate.get("X") if candidate.get("X") is not None else candidate.get("L")
        return abs(float(candidate_quantity) - float(quantity)) <= 0.000001
    except (TypeError, ValueError):
        return False


def _split_long_contract(value: Any, explicit_object: Any) -> tuple[str, str]:
    explicit = str(explicit_object).strip() if _usable_history_value(explicit_object) else ""
    text = str(value).strip() if _usable_history_value(value) else ""
    if text in {"是", "否"}:
        return text, explicit
    if text.startswith("是"):
        object_value = explicit or re.sub(r"^[是：:、,，\s]+", "", text)
        return "是", object_value
    if text.startswith("否"):
        return "否", explicit
    return "", explicit


def migrate_history_workbook(path: str | Path, apply: bool = False) -> dict[str, Any]:
    workbook = load_workbook(Path(path), read_only=True, data_only=True)
    sheet = workbook["现货业务台账"] if "现货业务台账" in workbook.sheetnames else workbook.active
    rows = sheet.iter_rows(values_only=True)
    headers = list(next(rows, ()))
    history_rows = [_history_row_to_values(headers, values) for values in rows if any(_usable_history_value(value) for value in values)]
    summary: dict[str, Any] = {"matched": 0, "updated": 0, "ambiguous": 0, "unmatched": 0, "dry_run": not apply, "errors": []}
    with db.connect() as conn:
        initialize_schema(conn)
        cur = conn.cursor()
        candidates = [record_to_public(dict(row)) for row in db._exec(cur, "SELECT * FROM spot_ledger_records WHERE source_detail_id IS NOT NULL").fetchall()]
        updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for history in history_rows:
            matches = [candidate for candidate in candidates if _matches_history(history, candidate)]
            if len(matches) != 1:
                summary["ambiguous" if len(matches) > 1 else "unmatched"] += 1
                continue
            summary["matched"] += 1
            candidate = matches[0]
            update: dict[str, Any] = {}
            for field in MANUAL_FIELDS:
                if field == "long_contract_object":
                    continue
                value = history.get(field)
                if _usable_history_value(value):
                    update[field] = value
            p_value, object_value = _split_long_contract(history.get("P"), history.get("long_contract_object"))
            if p_value:
                update["P"] = p_value
            if object_value:
                update["long_contract_object"] = object_value
            if not update:
                continue
            updates.append((candidate, update))
            if apply:
                projected = {**candidate, **update}
                missing = missing_required_fields(projected)
                assignments = []
                values: list[Any] = []
                for field, value in update.items():
                    column = f'"{field}"' if field in FIELD_CODES else field
                    assignments.append(f"{column} = ?")
                    values.append(value)
                assignments.extend(["missing_fields = ?", "supplement_status = ?", "updated_at = ?"])
                values.extend([json.dumps(missing, ensure_ascii=False), "待补录" if missing else "已完成", _timestamp()])
                values.append(candidate["record_id"])
                _raw_execute(cur, f"UPDATE spot_ledger_records SET {', '.join(assignments)} WHERE record_id = ?", tuple(values))
                summary["updated"] += 1
        if not apply:
            summary["candidate_updates"] = len(updates)
    workbook.close()
    return summary
