"""Deterministic server-side services for WH6 provisional option fills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import secrets
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import db


OPTION_RE = re.compile(
    r"^[a-z]{1,8}\d{3,6}-[cp]-\d+(?:\.\d+)?$",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
ALLOWED_OBSERVATION_FIELDS = {
    "source_event_key",
    "trade_date",
    "trade_time",
    "trade_timestamp",
    "exchange",
    "contract",
    "raw_contract",
    "asset_type",
    "side",
    "open_close",
    "quantity",
    "price",
    "fee",
    "turnover",
    "premium_cashflow",
    "close_profit",
    "trade_id",
    "order_id",
    "option_kind",
    "underlying",
    "expiry_month",
    "strike",
    "source_path",
    "source_record_index",
    "source_record_sha256",
    "parser_version",
    "source_version",
    "data_status",
    "verification_status",
    "observed_at",
}
REQUIRED_OBSERVATION_FIELDS = {
    "source_event_key",
    "trade_date",
    "trade_time",
    "trade_timestamp",
    "exchange",
    "contract",
    "raw_contract",
    "asset_type",
    "side",
    "open_close",
    "quantity",
    "price",
    "source_record_sha256",
    "parser_version",
}


class CollectorServiceError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class IngestResult:
    accepted: int = 0
    duplicates: int = 0
    conflicts: int = 0
    quarantined: int = 0
    observations: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "conflicts": self.conflicts,
            "quarantined": self.quarantined,
            "observations": self.observations,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Dict[str, Any]) -> str:
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_source_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text:
        return ""
    parts = [part for part in text.split("/") if part and part not in {".", ".."}]
    marker_index = next((index for index, part in enumerate(parts) if part.lower() == "record"), None)
    if marker_index is not None:
        text = "/".join(parts[marker_index:])
    else:
        text = "/".join(parts[-3:])
    return text[:500]


def _account(account_id: int) -> Dict[str, Any]:
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM trading_accounts WHERE id = ? AND is_active = 1", (account_id,)).fetchone()
    if not row:
        raise CollectorServiceError("account_not_found", "绑定的交易账户不存在或已停用", 404)
    return dict(row)


def issue_pairing_code(account_id: int, actor_id: int, ttl_seconds: int = 900) -> Dict[str, Any]:
    account = _account(account_id)
    if account.get("account_code") != "hongyuan_futures":
        raise CollectorServiceError("unsupported_account", "第一版采集器只允许绑定宏源期货账户", 400)
    if ttl_seconds < 60 or ttl_seconds > 3600:
        raise CollectorServiceError("invalid_ttl", "设备连接码有效期必须在 1 至 60 分钟之间")
    code = "WH6-" + secrets.token_urlsafe(10)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            "INSERT INTO trading_collector_pairing_codes (account_id, code_hash, expires_at, created_by) VALUES (?, ?, ?, ?)",
            (account_id, _hash(code), expires_at, actor_id),
        )
    return {
        "code": code,
        "expires_at": expires_at,
        "account_id": account_id,
        "account_label": account.get("masked_name") or account.get("display_name") or "宏源期货账户",
    }


def activate_device(pairing_code: str, device_name: str, client_version: str, fingerprint: str) -> Dict[str, Any]:
    code_hash = _hash(str(pairing_code or "").strip())
    device_name = str(device_name or "").strip()[:120]
    fingerprint = str(fingerprint or "").strip()[:200]
    if not device_name or not fingerprint:
        raise CollectorServiceError("invalid_device", "设备名称和设备指纹不能为空")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT * FROM trading_collector_pairing_codes WHERE code_hash = ? AND used_at IS NULL",
            (code_hash,),
        ).fetchone()
        if not row:
            raise CollectorServiceError("pairing_code_invalid", "设备连接码无效或已使用", 401)
        expires_at = _parse_datetime(row["expires_at"])
        if not expires_at or expires_at <= datetime.now(timezone.utc):
            raise CollectorServiceError("pairing_code_expired", "设备连接码已过期", 401)
        token = secrets.token_urlsafe(32)
        consumed = db._exec(
            cur,
            "UPDATE trading_collector_pairing_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
            (_now(), row["id"]),
        )
        if consumed.rowcount != 1:
            raise CollectorServiceError("pairing_code_invalid", "设备连接码无效或已使用", 401)
        db._exec(
            cur,
            """
            INSERT INTO trading_collector_devices
                (account_id, device_name, client_version, fingerprint, token_hash, status, bound_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (row["account_id"], device_name, str(client_version or "")[:40], fingerprint, _hash(token), _now(), _now()),
        )
        device_id = db.last_insert_id(conn)
        if not device_id:
            device = db._exec(
                cur,
                "SELECT id FROM trading_collector_devices WHERE token_hash = ?",
                (_hash(token),),
            ).fetchone()
            device_id = device["id"] if device else None
        account = db._exec(cur, "SELECT display_name, masked_name FROM trading_accounts WHERE id = ?", (row["account_id"],)).fetchone()
    return {
        "device_id": device_id,
        "account_id": row["account_id"],
        "device_name": device_name,
        "client_version": str(client_version or "")[:40],
        "status": "active",
        "token": token,
        "account_label": (account["masked_name"] or account["display_name"]) if account else "宏源期货账户",
    }


def get_device_by_token(token: str) -> Dict[str, Any]:
    token_hash = _hash(str(token or "").strip())
    with db.connect() as conn:
        row = db._exec(
            conn.cursor(),
            """
            SELECT d.*, a.display_name AS account_display_name, a.masked_name AS account_masked_name
            FROM trading_collector_devices d
            JOIN trading_accounts a ON a.id = d.account_id
            WHERE d.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
    if not row:
        raise CollectorServiceError("device_invalid", "设备令牌无效", 401)
    result = dict(row)
    if result.get("status") != "active":
        raise CollectorServiceError("device_revoked", "设备已暂停或撤销", 401)
    return result


def heartbeat_device(token: str, client_version: Optional[str] = None) -> Dict[str, Any]:
    device = get_device_by_token(token)
    return heartbeat_device_id(device["id"], client_version)


def heartbeat_device_id(device_id: int, client_version: Optional[str] = None) -> Dict[str, Any]:
    with db.connect() as conn:
        device = db._exec(conn.cursor(), "SELECT id, account_id, status FROM trading_collector_devices WHERE id = ?", (device_id,)).fetchone()
        if not device or device["status"] != "active":
            raise CollectorServiceError("device_revoked", "设备已暂停或撤销", 401)
        db._exec(
            conn.cursor(),
            "UPDATE trading_collector_devices SET last_seen_at = ?, client_version = COALESCE(?, client_version), last_error = NULL WHERE id = ? AND status = 'active'",
            (_now(), str(client_version or "")[:40] or None, device_id),
        )
    return {"device_id": device_id, "account_id": device["account_id"], "status": "active", "last_seen_at": _now()}


def revoke_device(device_id: int, actor_id: int) -> Dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(cur, "SELECT id, account_id, status FROM trading_collector_devices WHERE id = ?", (device_id,)).fetchone()
        if not row:
            raise CollectorServiceError("device_not_found", "设备不存在", 404)
        db._exec(
            cur,
            "UPDATE trading_collector_devices SET status = 'revoked', revoked_at = ?, last_error = ? WHERE id = ?",
            (_now(), "管理员撤销", device_id),
        )
    return {"device_id": device_id, "account_id": row["account_id"], "status": "revoked"}


def list_devices(account_id: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = """
        SELECT d.id AS device_id, d.account_id, d.device_name, d.client_version,
               d.status, d.bound_at, d.last_seen_at, d.revoked_at,
               a.display_name AS account_display_name, a.masked_name AS account_masked_name
        FROM trading_collector_devices d
        JOIN trading_accounts a ON a.id = d.account_id
    """
    params: List[Any] = []
    if account_id is not None:
        sql += " WHERE d.account_id = ?"
        params.append(account_id)
    sql += " ORDER BY d.bound_at DESC, d.id DESC"
    with db.connect() as conn:
        rows = db._exec(conn.cursor(), sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def _validate_observation(raw: Dict[str, Any]) -> Dict[str, Any]:
    missing = [field for field in REQUIRED_OBSERVATION_FIELDS if raw.get(field) in (None, "")]
    if missing:
        raise CollectorServiceError("invalid_observation", "成交记录缺少必需字段：" + ", ".join(sorted(missing)))
    data = {key: raw.get(key) for key in ALLOWED_OBSERVATION_FIELDS if key in raw}
    data["source_event_key"] = str(data["source_event_key"]).strip()[:240]
    if not data["source_event_key"]:
        raise CollectorServiceError("invalid_observation", "成交记录身份不能为空")
    data["trade_date"] = str(data["trade_date"]).strip()[:10]
    data["trade_time"] = str(data.get("trade_time") or "").strip()[:32]
    data["trade_timestamp"] = str(data.get("trade_timestamp") or "").strip()[:64]
    data["exchange"] = str(data["exchange"]).strip()[:40]
    data["contract"] = str(data["contract"]).strip().lower()[:80]
    data["raw_contract"] = str(data["raw_contract"]).strip()[:80]
    data["asset_type"] = str(data["asset_type"]).strip().lower()
    data["side"] = str(data["side"]).strip()[:8]
    data["open_close"] = str(data["open_close"]).strip()[:8]
    try:
        data["quantity"] = int(data["quantity"])
    except (TypeError, ValueError):
        raise CollectorServiceError("invalid_observation", "成交手数必须是正整数")
    if data["quantity"] <= 0:
        raise CollectorServiceError("invalid_observation", "成交手数必须大于 0")
    data["price"] = str(data["price"]).strip()[:40]
    try:
        parsed_price = Decimal(data["price"])
        if not data["price"] or not parsed_price.is_finite() or parsed_price < 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise CollectorServiceError("invalid_observation", "成交价格必须是非负数字")
    if data["asset_type"] != "option" or not OPTION_RE.match(data["contract"]):
        raise CollectorServiceError("unsupported_asset", "第一版只接收明确识别的期权成交")
    if data["side"] not in {"买", "卖", "buy", "sell"} or data["open_close"] not in {"开", "平", "开仓", "平仓", "open", "close"}:
        raise CollectorServiceError("invalid_observation", "买卖和开平字段无法验证")
    if not SHA256_RE.match(str(data["source_record_sha256"])):
        raise CollectorServiceError("invalid_observation", "原始记录哈希格式不正确")
    data["source_path"] = _safe_source_path(data.get("source_path"))
    try:
        data["source_record_index"] = int(data.get("source_record_index") or 0)
    except (TypeError, ValueError):
        raise CollectorServiceError("invalid_observation", "原始记录序号格式不正确")
    if data["source_record_index"] < 0:
        raise CollectorServiceError("invalid_observation", "原始记录序号不能为负数")
    data["data_status"] = "provisional"
    # Clients cannot promote intraday evidence to a settlement-confirmed fact.
    data["verification_status"] = "pending"
    data["observed_at"] = str(data.get("observed_at") or _now())[:64]
    return data


def _canonical_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: data.get(key)
        for key in (
            "source_event_key", "trade_date", "trade_time", "trade_timestamp", "exchange", "contract",
            "raw_contract", "asset_type", "side", "open_close", "quantity", "price", "fee", "turnover",
            "premium_cashflow", "close_profit", "trade_id", "order_id", "option_kind", "underlying",
            "expiry_month", "strike", "parser_version", "data_status", "verification_status",
        )
    }


def _observation_hash(data: Dict[str, Any]) -> str:
    """Exclude receive-time metadata so a replay is idempotent."""
    return _hash_json({key: value for key, value in data.items() if key not in {"observed_at"}})


def ingest_observations(device_token: str, observations: Sequence[Dict[str, Any]]) -> IngestResult:
    if len(observations) > 500:
        raise CollectorServiceError("batch_too_large", "单次最多上传 500 条成交")
    device = get_device_by_token(device_token)
    account_id = int(device["account_id"])
    accepted = duplicates = conflicts = quarantined = observation_count = 0
    with db.connect() as conn:
        cur = conn.cursor()
        for raw in observations:
            raw_map = dict(raw) if isinstance(raw, dict) else {}
            try:
                data = _validate_observation(raw_map)
            except CollectorServiceError as exc:
                quarantined += 1
                db._exec(
                    cur,
                    "INSERT INTO trading_collector_issues (device_id, account_id, issue_code, source_event_key, message, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (device["id"], account_id, exc.code, str(raw_map.get("source_event_key") or "")[:240], exc.message, json.dumps(raw_map, ensure_ascii=False, default=str)[:8000]),
                )
                continue
            payload_json = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            observation_hash = _observation_hash(data)
            obs_cur = db._exec(
                cur,
                """
                INSERT OR IGNORE INTO trading_intraday_fill_observations
                    (device_id, account_id, source_event_key, observation_hash, payload_json, status, observed_at)
                VALUES (?, ?, ?, ?, ?, 'accepted', ?)
                """,
                (device["id"], account_id, data["source_event_key"], observation_hash, payload_json, data.get("observed_at")),
            )
            inserted_observation = obs_cur.rowcount == 1
            observation_id = db.last_insert_id(conn) if inserted_observation else None
            if inserted_observation:
                observation_count += 1
            canonical_hash = _hash_json(_canonical_fields(data))
            existing = db._exec(
                cur,
                "SELECT * FROM trading_intraday_fills WHERE account_id = ? AND source_event_key = ?",
                (account_id, data["source_event_key"]),
            ).fetchone()
            if not existing:
                db._exec(
                    cur,
                    """
                    INSERT OR IGNORE INTO trading_intraday_fills
                        (account_id, source_event_key, trade_date, trade_time, trade_timestamp, exchange,
                         contract, raw_contract, asset_type, side, open_close, quantity, price, fee, turnover,
                         premium_cashflow, close_profit, trade_id, order_id, option_kind, underlying, expiry_month,
                         strike, parser_version, source_record_sha256, source_path, source_record_index, data_status,
                         verification_status, first_received_at, last_observed_at, canonical_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id, data["source_event_key"], data["trade_date"], data["trade_time"], data["trade_timestamp"],
                        data["exchange"], data["contract"], data["raw_contract"], data["asset_type"], data["side"],
                        data["open_close"], data["quantity"], data["price"], data.get("fee"), data.get("turnover"),
                        data.get("premium_cashflow"), data.get("close_profit"), data.get("trade_id"), data.get("order_id"),
                        data.get("option_kind"), data.get("underlying"), data.get("expiry_month"), data.get("strike"),
                        data["parser_version"], data["source_record_sha256"], data.get("source_path", ""), data.get("source_record_index", 0),
                        data["data_status"], data["verification_status"], _now(), _now(), canonical_hash,
                    ),
                )
                existing = db._exec(
                    cur,
                    "SELECT * FROM trading_intraday_fills WHERE account_id = ? AND source_event_key = ?",
                    (account_id, data["source_event_key"]),
                ).fetchone()
                if existing and existing["canonical_hash"] == canonical_hash:
                    accepted += 1
                    continue
            if existing and existing["canonical_hash"] == canonical_hash:
                duplicates += 1
                if observation_id:
                    db._exec(cur, "UPDATE trading_intraday_fill_observations SET status = 'duplicate_observation' WHERE id = ?", (observation_id,))
                continue
            conflicts += 1
            if observation_id:
                db._exec(cur, "UPDATE trading_intraday_fill_observations SET status = 'conflict' WHERE id = ?", (observation_id,))
            db._exec(
                cur,
                "INSERT INTO trading_collector_issues (device_id, account_id, issue_code, source_event_key, message, payload_json) VALUES (?, ?, 'fill_conflict', ?, ?, ?)",
                (device["id"], account_id, data["source_event_key"], "同一成交编号的关键字段不一致，保留首次标准事实", payload_json),
            )
        db._exec(cur, "UPDATE trading_collector_devices SET last_seen_at = ? WHERE id = ? AND status = 'active'", (_now(), device["id"]))
    return IngestResult(accepted, duplicates, conflicts, quarantined, observation_count)


def query_intraday_fills(
    account_id: int,
    *,
    start: str = "",
    end: str = "",
    contract: str = "",
    status: str = "accepted",
    limit: int = 500,
) -> Dict[str, Any]:
    _account(account_id)
    limit = max(1, min(int(limit), 500))
    sql = "SELECT * FROM trading_intraday_fills WHERE account_id = ?"
    params: List[Any] = [account_id]
    if start:
        sql += " AND trade_date >= ?"
        params.append(start[:10])
    if end:
        sql += " AND trade_date <= ?"
        params.append(end[:10])
    if contract:
        sql += " AND contract = ?"
        params.append(contract.strip().lower()[:80])
    if status:
        sql += " AND data_status = ?"
        params.append("provisional" if status == "accepted" else status)
    sql += " ORDER BY trade_date DESC, trade_time DESC, id DESC LIMIT ?"
    params.append(limit)
    with db.connect() as conn:
        rows = db._exec(conn.cursor(), sql, tuple(params)).fetchall()
    items = [dict(row) for row in rows]
    return {"items": items, "total": len(items), "account_id": account_id, "data_status": "provisional"}
