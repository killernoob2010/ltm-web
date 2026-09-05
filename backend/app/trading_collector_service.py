"""Deterministic server-side services for WH6 provisional fills and positions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import re
import secrets
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from . import db
from . import trading_collector_reconciliation as reconciliation


OPTION_RE = re.compile(
    r"^(?P<underlying>[a-z]{1,8})(?P<expiry>\d{3,6})-(?P<kind>[cp])-(?P<strike>\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)
FUTURE_RE = re.compile(r"^[a-z]{1,8}\d{3,6}$", re.IGNORECASE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
PAIRING_CODE_RE = re.compile(r"^LTM1-(?P<route>[SP])-(?P<secret>[A-Za-z0-9_-]{12,})$")
PAIRING_ENVIRONMENTS = {"S": "staging", "P": "production"}
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
ALLOWED_POSITION_FIELDS = {
    "source_snapshot_key",
    "trade_date",
    "snapshot_time",
    "snapshot_timestamp",
    "complete",
    "rows",
    "source_snapshot_sha256",
    "parser_version",
    "source_path",
    "source_version",
    "data_status",
    "verification_status",
    "observed_at",
}
REQUIRED_POSITION_FIELDS = {
    "source_snapshot_key",
    "trade_date",
    "snapshot_timestamp",
    "complete",
    "rows",
    "source_snapshot_sha256",
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
    fill_results: tuple[Dict[str, str], ...] = ()
    position_accepted: int = 0
    position_duplicates: int = 0
    position_conflicts: int = 0
    position_quarantined: int = 0
    position_observations: int = 0

    @property
    def positions_accepted(self) -> int:
        return self.position_accepted

    @property
    def positions_duplicates(self) -> int:
        return self.position_duplicates

    @property
    def positions_conflicts(self) -> int:
        return self.position_conflicts

    @property
    def positions_quarantined(self) -> int:
        return self.position_quarantined

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "conflicts": self.conflicts,
            "quarantined": self.quarantined,
            "observations": self.observations,
            "fill_results": list(self.fill_results),
            "position_accepted": self.position_accepted,
            "positions_accepted": self.position_accepted,
            "position_duplicates": self.position_duplicates,
            "position_conflicts": self.position_conflicts,
            "position_quarantined": self.position_quarantined,
            "position_observations": self.position_observations,
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Dict[str, Any]) -> str:
    return _hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _runtime_environment() -> str:
    configured = str(os.getenv("LTM_RUNTIME_ENVIRONMENT") or "").strip().lower()
    if not configured:
        service_name = str(os.getenv("RENDER_SERVICE_NAME") or "").strip().lower()
        configured = "production" if service_name == "ltm-web" else "staging"
    if configured not in {"staging", "production"}:
        raise CollectorServiceError("environment_invalid", "采集服务环境配置无效", 500)
    return configured


def _pairing_environment(pairing_code: str) -> Optional[str]:
    match = PAIRING_CODE_RE.fullmatch(str(pairing_code or "").strip())
    return PAIRING_ENVIRONMENTS.get(match.group("route")) if match else None


def _minimum_version_satisfied(value: object) -> bool:
    try:
        actual = tuple(int(part) for part in str(value or "").strip().split("."))
        required = tuple(int(part) for part in reconciliation.MINIMUM_CLIENT_VERSION.split("."))
    except (TypeError, ValueError):
        return False
    return actual >= required


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


def issue_pairing_code(
    account_id: int,
    actor_id: int,
    ttl_seconds: int = 900,
    *,
    environment: Optional[str] = None,
) -> Dict[str, Any]:
    account = _account(account_id)
    if account.get("account_code") != "hongyuan_futures":
        raise CollectorServiceError("unsupported_account", "第一版采集器只允许绑定宏源期货账户", 400)
    if ttl_seconds < 60 or ttl_seconds > 3600:
        raise CollectorServiceError("invalid_ttl", "设备连接码有效期必须在 1 至 60 分钟之间")
    selected_environment = str(environment or _runtime_environment()).strip().lower()
    if selected_environment not in {"staging", "production"}:
        raise CollectorServiceError("environment_invalid", "采集服务环境无效", 500)
    prefix = "S" if selected_environment == "staging" else "P"
    code = "LTM1-%s-%s" % (prefix, secrets.token_urlsafe(16))
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).replace(microsecond=0).isoformat()
    with db.connect() as conn:
        db._exec(
            conn.cursor(),
            "INSERT INTO trading_collector_pairing_codes (account_id, environment, code_hash, expires_at, created_by) VALUES (?, ?, ?, ?, ?)",
            (account_id, selected_environment, _hash(code), expires_at, actor_id),
        )
    return {
        "code": code,
        "expires_at": expires_at,
        "account_id": account_id,
        "environment": selected_environment,
        "account_label": account.get("masked_name") or account.get("display_name") or "宏源期货账户",
    }


def activate_device(pairing_code: str, device_name: str, client_version: str, fingerprint: str) -> Dict[str, Any]:
    code_environment = _pairing_environment(pairing_code)
    if not code_environment:
        raise CollectorServiceError("pairing_code_invalid", "设备连接码无效或已使用", 401)
    if code_environment != _runtime_environment():
        raise CollectorServiceError("environment_mismatch", "设备连接码不属于当前采集服务环境", 401)
    code_hash = _hash(str(pairing_code or "").strip())
    device_name = str(device_name or "").strip()[:120]
    fingerprint = str(fingerprint or "").strip()[:200]
    if not device_name or not fingerprint:
        raise CollectorServiceError("invalid_device", "设备名称和设备指纹不能为空")
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            "SELECT * FROM trading_collector_pairing_codes WHERE code_hash = ? AND environment = ? AND used_at IS NULL",
            (code_hash, code_environment),
        ).fetchone()
        if not row:
            raise CollectorServiceError("pairing_code_invalid", "设备连接码无效或已使用", 401)
        expires_at = _parse_datetime(row["expires_at"])
        if not expires_at or expires_at <= datetime.now(timezone.utc):
            raise CollectorServiceError("pairing_code_expired", "设备连接码已过期", 401)
        if not _minimum_version_satisfied(client_version):
            raise CollectorServiceError(
                "client_version_unsupported",
                "采集器版本低于当前服务端最低版本 %s" % reconciliation.MINIMUM_CLIENT_VERSION,
                426,
            )
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
                (account_id, environment, device_name, client_version, fingerprint, token_hash, status, bound_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (row["account_id"], row["environment"], device_name, str(client_version or "")[:40], fingerprint, _hash(token), _now(), _now()),
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
        "environment": row["environment"],
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
    device_environment = str(result.get("environment") or "staging").strip().lower()
    if device_environment != _runtime_environment():
        raise CollectorServiceError("environment_mismatch", "设备令牌不属于当前采集服务环境", 401)
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


def get_device_collection_policy(device_id: int) -> Dict[str, Any]:
    try:
        return reconciliation.get_device_collection_policy(device_id)
    except ValueError as exc:
        message = str(exc)
        if message == "设备已暂停或撤销":
            raise CollectorServiceError("device_revoked", message, 401) from exc
        if "环境" in message:
            raise CollectorServiceError("environment_mismatch", message, 500) from exc
        raise CollectorServiceError("policy_unavailable", message, 503) from exc


def get_account_collection_policy(account_id: int) -> Dict[str, Any]:
    _account(account_id)
    try:
        policy = reconciliation.build_collection_policy(account_id)
    except ValueError as exc:
        raise CollectorServiceError("policy_unavailable", str(exc), 503) from exc
    if str(policy.get("environment") or "").strip().lower() != _runtime_environment():
        raise CollectorServiceError("environment_mismatch", "账户采集策略不属于当前采集服务环境", 500)
    return policy


def reconcile_intraday_range(
    account_id: int,
    start_date: str = "",
    end_date: str = "",
    actor: str = "trading_collector_admin",
) -> Dict[str, int]:
    """Apply the auditable WH6 reconciliation pass in Staging only."""
    if _runtime_environment() != "staging":
        raise CollectorServiceError("staging_only", "WH6 历史协调接口仅允许在测试版执行", 403)
    _account(account_id)
    with db.connect() as conn:
        cur = conn.cursor()
        invalid_range = db._exec(
            cur,
            """
            SELECT id, range_start, range_end
            FROM trading_import_batches
            WHERE account_id = ? AND status = 'active' AND statement_type = 'monthly'
            """,
            (account_id,),
        ).fetchall()
        for batch in invalid_range:
            if reconciliation._complete_month_range(batch["range_start"], batch["range_end"]) is None:
                raise CollectorServiceError("invalid_monthly_range", "存在未覆盖完整自然月的 active 月结批次，已停止协调", 409)
        duplicate_audit = db._exec(
            cur,
            """
            SELECT intraday_fill_id
            FROM trading_intraday_fill_reconciliations
            WHERE account_id = ? AND is_current = 1
            GROUP BY intraday_fill_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if duplicate_audit:
            raise CollectorServiceError("duplicate_current_audit", "存在多条当前协调记录，已停止协调", 409)
        bounds = db._exec(
            cur,
            """
            SELECT MIN(trade_date) AS start_date, MAX(trade_date) AS end_date
            FROM trading_intraday_fills
            WHERE account_id = ?
            """,
            (account_id,),
        ).fetchone()
        start = start_date or str(bounds["start_date"] or "")
        end = end_date or str(bounds["end_date"] or "")
        transaction_numbers_backfilled = (
            reconciliation.backfill_settlement_transaction_numbers(cur, account_id=account_id)
            if start and end
            else 0
        )
        summary = reconciliation.reconcile_intraday_range(cur, account_id, start, end, actor)
        if summary.scanned != summary.changed + summary.unchanged:
            raise CollectorServiceError("reconciliation_invariant_failed", "协调结果无法通过变更/未变更守恒校验", 500)
        conn.commit()
    result = summary.to_dict()
    result["transaction_numbers_backfilled"] = transaction_numbers_backfilled
    return result


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
               d.environment, d.status, d.bound_at, d.last_seen_at, d.revoked_at,
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
    data["parser_version"] = str(data["parser_version"]).strip()[:80]
    try:
        datetime.strptime(data["trade_date"], "%Y-%m-%d")
        datetime.strptime(data["trade_time"], "%H:%M:%S")
    except ValueError:
        raise CollectorServiceError("invalid_observation", "成交日期或时间格式无法验证")
    if not data["trade_timestamp"] or _parse_datetime(data["trade_timestamp"]) is None:
        raise CollectorServiceError("invalid_observation", "成交时间戳格式无法验证")
    if not data["exchange"] or not data["raw_contract"] or not data["parser_version"]:
        raise CollectorServiceError("invalid_observation", "成交记录的交易所、原始合约或解析器版本不能为空")
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
    if data["asset_type"] == "option":
        supported_contract = bool(OPTION_RE.match(data["contract"]))
    elif data["asset_type"] == "future":
        supported_contract = bool(FUTURE_RE.match(data["contract"]))
    else:
        supported_contract = False
    if not supported_contract:
        raise CollectorServiceError("unsupported_asset", "只接收明确识别的期货或期权成交")
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
        "trade_date": data.get("trade_date"),
        "trade_time": data.get("trade_time"),
        "trade_timestamp": data.get("trade_timestamp"),
        "exchange": reconciliation.normalize_exchange(data.get("exchange")),
        "contract": str(data.get("contract") or "").strip().lower(),
        "asset_type": data.get("asset_type"),
        "side": str(data.get("side") or "").strip().lower(),
        "open_close": str(data.get("open_close") or "").strip().lower(),
        "quantity": data.get("quantity"),
        "price": data.get("price"),
        "fee": data.get("fee"),
        "turnover": data.get("turnover"),
        "premium_cashflow": data.get("premium_cashflow"),
        "close_profit": data.get("close_profit"),
        "trade_id": reconciliation.normalize_transaction_no(data.get("trade_id")),
        "order_id": str(data.get("order_id") or "").strip(),
        "option_kind": data.get("option_kind"),
        "underlying": data.get("underlying"),
        "expiry_month": data.get("expiry_month"),
        "strike": data.get("strike"),
    }


def _canonical_event_key(account_id: int, data: Dict[str, Any]) -> str:
    normalized_trade_id = reconciliation.normalize_transaction_no(data.get("trade_id"))
    exchange = reconciliation.normalize_exchange(data.get("exchange"))
    if normalized_trade_id:
        return "tradeid:%s:%s:%s:%s" % (
            account_id,
            data["trade_date"],
            exchange,
            normalized_trade_id,
        )
    return "signature:%s" % _hash_json(
        {
            "account_id": account_id,
            "trade_date": data["trade_date"],
            "trade_time": data["trade_time"],
            "trade_timestamp": data["trade_timestamp"],
            "exchange": exchange,
            "contract": data["contract"],
            "asset_type": data["asset_type"],
            "side": data["side"],
            "open_close": data["open_close"],
            "quantity": data["quantity"],
            "price": data["price"],
            "source_path": data.get("source_path", ""),
            "source_record_index": data.get("source_record_index", 0),
            "source_record_sha256": data["source_record_sha256"],
        }
    )


def _legacy_trade_event_keys(data: Mapping[str, Any]) -> tuple[str, ...]:
    normalized_trade_id = reconciliation.normalize_transaction_no(data.get("trade_id"))
    if not normalized_trade_id:
        return ()
    exchange = reconciliation.normalize_exchange(data.get("exchange"))
    return (
        "tradeid:%s:%s:%s" % (data["trade_date"], exchange, normalized_trade_id),
        "tradeid:%s" % normalized_trade_id,
    )


def _find_legacy_fill(cur, account_id: int, data: Mapping[str, Any]) -> Optional[Any]:
    legacy_keys = _legacy_trade_event_keys(data)
    if not legacy_keys:
        return None
    placeholders = ", ".join("?" for _ in legacy_keys)
    query = """
        SELECT * FROM trading_intraday_fills
        WHERE account_id = ? AND (
            source_event_key IN ({}) OR canonical_event_key IN ({})
        )
        ORDER BY id
        LIMIT 1
        """.format(placeholders, placeholders)
    return db._exec(
        cur,
        query,
        (account_id, *legacy_keys, *legacy_keys),
    ).fetchone()


def _observation_hash(data: Dict[str, Any]) -> str:
    """Exclude receive-time metadata so a replay is idempotent."""
    return _hash_json({key: value for key, value in data.items() if key not in {"observed_at"}})


def _result_event_key(raw_map: Mapping[str, Any], index: int) -> str:
    supplied = str(raw_map.get("source_event_key") or "").strip()[:240]
    return supplied or "request:%d:%s" % (index, secrets.token_urlsafe(6))


def _date_is_closed(cur, account_id: int, trade_date: str) -> bool:
    return any(
        str(item["range_start"]) <= trade_date <= str(item["range_end"])
        for item in reconciliation.get_active_monthly_ranges(cur, account_id)
    )


def _date_is_uploadable(cur, account_id: int, trade_date: str) -> bool:
    try:
        return reconciliation.is_date_uploadable(cur, account_id, trade_date)
    except ValueError as exc:
        raise CollectorServiceError("policy_unavailable", str(exc), 503) from exc


def _ingest_fill_observations(device_token: str, observations: Sequence[Dict[str, Any]]) -> IngestResult:
    if len(observations) > 500:
        raise CollectorServiceError("batch_too_large", "单次最多上传 500 条成交")
    device = get_device_by_token(device_token)
    if not _minimum_version_satisfied(device.get("client_version")):
        raise CollectorServiceError(
            "client_version_unsupported",
            "采集器版本低于当前服务端最低版本 %s" % reconciliation.MINIMUM_CLIENT_VERSION,
            426,
        )
    account_id = int(device["account_id"])
    accepted = duplicates = conflicts = quarantined = observation_count = 0
    fill_results: List[Dict[str, str]] = []
    with db.connect() as conn:
        cur = conn.cursor()
        for index, raw in enumerate(observations):
            raw_map = dict(raw) if isinstance(raw, dict) else {}
            result_event_key = _result_event_key(raw_map, index)
            try:
                data = _validate_observation(raw_map)
            except CollectorServiceError as exc:
                quarantined += 1
                fill_results.append({"event_key": result_event_key, "status": "quarantined"})
                db._exec(
                    cur,
                    "INSERT INTO trading_collector_issues (device_id, account_id, issue_code, source_event_key, message, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (device["id"], account_id, exc.code, str(raw_map.get("source_event_key") or "")[:240], exc.message, _safe_issue_payload(raw_map)),
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
            if _date_is_closed(cur, account_id, data["trade_date"]):
                if observation_id:
                    db._exec(
                        cur,
                        "UPDATE trading_intraday_fill_observations SET status = 'settlement_covered' WHERE id = ?",
                        (observation_id,),
                    )
                fill_results.append({"event_key": result_event_key, "status": "settlement_covered"})
                continue
            if not _date_is_uploadable(cur, account_id, data["trade_date"]):
                if observation_id:
                    db._exec(
                        cur,
                        "UPDATE trading_intraday_fill_observations SET status = 'outside_upload_policy' WHERE id = ?",
                        (observation_id,),
                    )
                fill_results.append({"event_key": result_event_key, "status": "outside_upload_policy"})
                continue
            canonical_event_key = _canonical_event_key(account_id, data)
            canonical_hash = _hash_json(_canonical_fields(data))
            existing = db._exec(
                cur,
                "SELECT * FROM trading_intraday_fills WHERE account_id = ? AND canonical_event_key = ?",
                (account_id, canonical_event_key),
            ).fetchone()
            if not existing:
                legacy = _find_legacy_fill(cur, account_id, data)
                if legacy:
                    existing = legacy
                    legacy_keys = _legacy_trade_event_keys(data)
                    legacy_canonical = str(legacy["canonical_event_key"] or "").strip()
                    if (
                        legacy["canonical_hash"] == canonical_hash
                        and legacy_canonical in {"", *legacy_keys}
                    ):
                        db._exec(
                            cur,
                            """
                            UPDATE trading_intraday_fills
                            SET canonical_event_key = ?
                            WHERE id = ? AND account_id = ?
                              AND (canonical_event_key IS NULL OR canonical_event_key = ''
                                   OR canonical_event_key = ? OR canonical_event_key = ?)
                            """,
                            (canonical_event_key, legacy["id"], account_id, legacy_keys[0], legacy_keys[1]),
                        )
                        existing = db._exec(
                            cur,
                            "SELECT * FROM trading_intraday_fills WHERE account_id = ? AND canonical_event_key = ?",
                            (account_id, canonical_event_key),
                        ).fetchone()
            if not existing:
                inserted_fill = db._exec(
                    cur,
                    """
                    INSERT OR IGNORE INTO trading_intraday_fills
                        (account_id, source_event_key, canonical_event_key, trade_date, trade_time, trade_timestamp, exchange,
                         contract, raw_contract, asset_type, side, open_close, quantity, price, fee, turnover,
                         premium_cashflow, close_profit, trade_id, order_id, option_kind, underlying, expiry_month,
                         strike, parser_version, source_record_sha256, source_path, source_record_index, data_status,
                         verification_status, first_received_at, last_observed_at, canonical_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id, canonical_event_key, canonical_event_key, data["trade_date"], data["trade_time"], data["trade_timestamp"],
                        data["exchange"], data["contract"], data["raw_contract"], data["asset_type"], data["side"],
                        data["open_close"], data["quantity"], data["price"], data.get("fee"), data.get("turnover"),
                        data.get("premium_cashflow"), data.get("close_profit"), data.get("trade_id"), data.get("order_id"),
                        data.get("option_kind"), data.get("underlying"), data.get("expiry_month"), data.get("strike"),
                        data["parser_version"], data["source_record_sha256"], data.get("source_path", ""), data.get("source_record_index", 0),
                        data["data_status"], data["verification_status"], _now(), _now(), canonical_hash,
                    ),
                )
                inserted_fill_created = inserted_fill.rowcount == 1
                existing = db._exec(
                    cur,
                    "SELECT * FROM trading_intraday_fills WHERE account_id = ? AND canonical_event_key = ?",
                    (account_id, canonical_event_key),
                ).fetchone()
                if inserted_fill_created and existing and existing["canonical_hash"] == canonical_hash:
                    accepted += 1
                    fill_results.append({"event_key": result_event_key, "status": "accepted"})
                    continue
            if existing and existing["canonical_hash"] == canonical_hash:
                duplicates += 1
                fill_results.append({"event_key": result_event_key, "status": "duplicate"})
                if observation_id:
                    db._exec(cur, "UPDATE trading_intraday_fill_observations SET status = 'duplicate_observation' WHERE id = ?", (observation_id,))
                continue
            conflicts += 1
            fill_results.append({"event_key": result_event_key, "status": "conflict"})
            if observation_id:
                db._exec(cur, "UPDATE trading_intraday_fill_observations SET status = 'conflict' WHERE id = ?", (observation_id,))
            db._exec(
                cur,
                "INSERT INTO trading_collector_issues (device_id, account_id, issue_code, source_event_key, message, payload_json) VALUES (?, ?, 'fill_conflict', ?, ?, ?)",
                (device["id"], account_id, data["source_event_key"], "同一成交编号的关键字段不一致，保留首次标准事实", payload_json),
            )
        db._exec(cur, "UPDATE trading_collector_devices SET last_seen_at = ? WHERE id = ? AND status = 'active'", (_now(), device["id"]))
    return IngestResult(
        accepted=accepted,
        duplicates=duplicates,
        conflicts=conflicts,
        quarantined=quarantined,
        observations=observation_count,
        fill_results=tuple(fill_results),
    )


def _validate_position_number(value: Any, *, allow_empty: bool = True) -> Optional[float]:
    if value is None and allow_empty:
        return None
    if isinstance(value, bool):
        raise CollectorServiceError("invalid_position_snapshot", "持仓数量不能是布尔值")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise CollectorServiceError("invalid_position_snapshot", "持仓数量必须是非负数字")
    if not number.is_finite() or number < 0:
        raise CollectorServiceError("invalid_position_snapshot", "持仓数量必须是非负数字")
    return float(number)


def _validate_position_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    missing = [field for field in REQUIRED_POSITION_FIELDS if raw.get(field) in (None, "")]
    if missing:
        raise CollectorServiceError("invalid_position_snapshot", "持仓快照缺少必需字段：" + ", ".join(sorted(missing)))
    data = {key: raw.get(key) for key in ALLOWED_POSITION_FIELDS if key in raw}
    data["source_snapshot_key"] = str(data["source_snapshot_key"]).strip()[:240]
    data["trade_date"] = str(data["trade_date"]).strip()[:10]
    data["snapshot_time"] = str(data.get("snapshot_time") or "").strip()[:32]
    data["snapshot_timestamp"] = str(data["snapshot_timestamp"]).strip()[:64]
    data["parser_version"] = str(data["parser_version"]).strip()[:80]
    if not data["source_snapshot_key"] or not data["parser_version"]:
        raise CollectorServiceError("invalid_position_snapshot", "持仓快照身份和解析器版本不能为空")
    try:
        datetime.strptime(data["trade_date"], "%Y-%m-%d")
    except ValueError:
        raise CollectorServiceError("invalid_position_snapshot", "持仓交易日格式无法验证")
    snapshot_dt = _parse_datetime(data["snapshot_timestamp"])
    if snapshot_dt is None:
        raise CollectorServiceError("invalid_position_snapshot", "持仓快照时间戳格式无法验证")
    if not data["snapshot_time"]:
        data["snapshot_time"] = snapshot_dt.astimezone(timezone.utc).strftime("%H:%M:%S")
    if data.get("complete") is not True:
        raise CollectorServiceError("incomplete_position_snapshot", "持仓快照没有完整结束标记")
    if not SHA256_RE.match(str(data["source_snapshot_sha256"])):
        raise CollectorServiceError("invalid_position_snapshot", "持仓快照哈希格式不正确")
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) > 10000:
        raise CollectorServiceError("invalid_position_snapshot", "持仓快照行列表无法验证")
    normalized_rows: List[Dict[str, Any]] = []
    row_keys = set()
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, Mapping):
            raise CollectorServiceError("invalid_position_snapshot", "持仓快照行不是对象")
        row = dict(raw_row)
        contract = str(row.get("contract") or "").strip().lower()[:80]
        raw_contract = str(row.get("raw_contract") or contract)[:80]
        asset_type = str(row.get("asset_type") or "").strip().lower()
        if asset_type == "option":
            supported_contract = bool(OPTION_RE.match(contract))
        elif asset_type == "future":
            supported_contract = bool(FUTURE_RE.match(contract))
        else:
            supported_contract = False
        direction = str(row.get("direction") or "").strip().lower()
        direction = {
            "多": "long",
            "空": "short",
            "long": "long",
            "short": "short",
            "1": "long",
            "2": "short",
        }.get(direction, "")
        exchange = str(row.get("exchange") or "").strip().upper()[:40]
        quantity = _validate_position_number(row.get("quantity"), allow_empty=False)
        today_quantity = _validate_position_number(row.get("today_quantity"))
        yesterday_quantity = _validate_position_number(row.get("yesterday_quantity"))
        average_price = row.get("average_price")
        if average_price not in (None, ""):
            try:
                parsed_average = Decimal(str(average_price))
                if not parsed_average.is_finite() or parsed_average < 0:
                    raise InvalidOperation
                average_price = format(parsed_average, "f")
            except (InvalidOperation, TypeError, ValueError):
                raise CollectorServiceError("invalid_position_snapshot", "持仓均价必须是非负数字")
        else:
            average_price = None
        try:
            source_index = int(row.get("source_record_index") if row.get("source_record_index") is not None else index)
        except (TypeError, ValueError):
            raise CollectorServiceError("invalid_position_snapshot", "持仓原始行号格式不正确")
        if source_index < 0 or not supported_contract or not direction or not exchange or quantity is None:
            raise CollectorServiceError("invalid_position_snapshot", "持仓快照含有无法验证的合约、方向、数量或交易所字段")
        option_match = OPTION_RE.match(contract)
        option_parts = {
            "option_kind": option_match.group("kind").lower(),
            "underlying": option_match.group("underlying").lower(),
            "expiry_month": option_match.group("expiry"),
            "strike": option_match.group("strike"),
        } if option_match else {
            "option_kind": None,
            "underlying": None,
            "expiry_month": None,
            "strike": None,
        }
        hedge_flag = str(row.get("hedge_flag") or "").strip()[:40] or None
        row_key = (contract, direction, hedge_flag or "")
        if row_key in row_keys:
            raise CollectorServiceError("invalid_position_snapshot", "持仓快照存在重复的合约方向行")
        row_keys.add(row_key)
        canonical_row = {
            "contract": contract,
            "raw_contract": raw_contract,
            "asset_type": asset_type,
            "exchange": exchange,
            "direction": direction,
            "quantity": quantity,
            "today_quantity": today_quantity,
            "yesterday_quantity": yesterday_quantity,
            "average_price": average_price,
            "hedge_flag": hedge_flag,
            "source_record_index": source_index,
            **option_parts,
        }
        canonical_row["source_record_sha256"] = str(row.get("source_record_sha256") or _hash_json(canonical_row))
        if not SHA256_RE.match(canonical_row["source_record_sha256"]):
            raise CollectorServiceError("invalid_position_snapshot", "持仓原始行哈希格式不正确")
        normalized_rows.append(canonical_row)
    data["rows"] = normalized_rows
    data["source_path"] = _safe_source_path(data.get("source_path"))
    data["source_snapshot_sha256"] = str(data["source_snapshot_sha256"]).lower()
    data["data_status"] = "provisional"
    data["verification_status"] = "pending"
    data["observed_at"] = str(data.get("observed_at") or _now())[:64]
    return data


def _position_canonical_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: data.get(key)
        for key in (
            "source_snapshot_key", "trade_date", "snapshot_time", "snapshot_timestamp", "complete",
            "rows", "source_snapshot_sha256", "parser_version", "data_status", "verification_status",
        )
    }


def _position_observation_hash(data: Dict[str, Any]) -> str:
    return _hash_json(_position_canonical_fields(data))


def _safe_issue_payload(raw_map: Dict[str, Any]) -> str:
    payload = dict(raw_map)
    if "source_path" in payload:
        payload["source_path"] = _safe_source_path(payload.get("source_path"))
    return json.dumps(payload, ensure_ascii=False, default=str)[:8000]


def _insert_position_rows(cur, snapshot_id: int, account_id: int, rows: Sequence[Dict[str, Any]]) -> None:
    for row in rows:
        db._exec(
            cur,
            """
            INSERT OR IGNORE INTO trading_intraday_position_rows
                (snapshot_id, account_id, contract, raw_contract, asset_type, exchange, direction,
                 quantity, today_quantity, yesterday_quantity, average_price, hedge_flag, option_kind,
                 underlying, expiry_month, strike, source_record_index, source_record_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id, account_id, row["contract"], row["raw_contract"], row["asset_type"], row["exchange"],
                row["direction"], row["quantity"], row.get("today_quantity"), row.get("yesterday_quantity"),
                row.get("average_price"), row.get("hedge_flag"), row.get("option_kind"), row.get("underlying"),
                row.get("expiry_month"), row.get("strike"), row.get("source_record_index", 0), row["source_record_sha256"],
            ),
        )


def _ingest_position_snapshots(device: Dict[str, Any], position_snapshots: Sequence[Dict[str, Any]]) -> IngestResult:
    account_id = int(device["account_id"])
    position_accepted = position_duplicates = position_conflicts = position_quarantined = position_observation_count = 0
    with db.connect() as conn:
        cur = conn.cursor()
        for raw in position_snapshots:
            raw_map = dict(raw) if isinstance(raw, dict) else {}
            try:
                data = _validate_position_snapshot(raw_map)
            except CollectorServiceError as exc:
                position_quarantined += 1
                db._exec(
                    cur,
                    "INSERT INTO trading_collector_issues (device_id, account_id, issue_code, source_event_key, message, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (device["id"], account_id, exc.code, str(raw_map.get("source_snapshot_key") or "")[:240], exc.message, _safe_issue_payload(raw_map)),
                )
                continue
            payload_json = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            observation_hash = _position_observation_hash(data)
            obs_cur = db._exec(
                cur,
                """
                INSERT OR IGNORE INTO trading_intraday_position_observations
                    (device_id, account_id, source_snapshot_key, snapshot_hash, payload_json, status, observed_at)
                VALUES (?, ?, ?, ?, ?, 'accepted', ?)
                """,
                (device["id"], account_id, data["source_snapshot_key"], observation_hash, payload_json, data.get("observed_at")),
            )
            inserted_observation = obs_cur.rowcount == 1
            observation_id = db.last_insert_id(conn) if inserted_observation else None
            if inserted_observation:
                position_observation_count += 1
            canonical_hash = _hash_json(_position_canonical_fields(data))
            existing = db._exec(
                cur,
                "SELECT * FROM trading_intraday_position_snapshots WHERE account_id = ? AND source_snapshot_key = ?",
                (account_id, data["source_snapshot_key"]),
            ).fetchone()
            if not existing:
                db._exec(
                    cur,
                    """
                    INSERT OR IGNORE INTO trading_intraday_position_snapshots
                        (account_id, source_snapshot_key, trade_date, snapshot_time, snapshot_timestamp, complete,
                         source_snapshot_sha256, parser_version, data_status, verification_status, first_received_at,
                         last_observed_at, canonical_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id, data["source_snapshot_key"], data["trade_date"], data["snapshot_time"],
                        data["snapshot_timestamp"], 1, data["source_snapshot_sha256"], data["parser_version"],
                        data["data_status"], data["verification_status"], _now(), _now(), canonical_hash,
                    ),
                )
                existing = db._exec(
                    cur,
                    "SELECT * FROM trading_intraday_position_snapshots WHERE account_id = ? AND source_snapshot_key = ?",
                    (account_id, data["source_snapshot_key"]),
                ).fetchone()
                if existing and existing["canonical_hash"] == canonical_hash:
                    _insert_position_rows(cur, int(existing["id"]), account_id, data["rows"])
                    position_accepted += 1
                    continue
            if existing and existing["canonical_hash"] == canonical_hash:
                position_duplicates += 1
                if observation_id:
                    db._exec(cur, "UPDATE trading_intraday_position_observations SET status = 'duplicate_observation' WHERE id = ?", (observation_id,))
                db._exec(
                    cur,
                    "UPDATE trading_intraday_position_snapshots SET conflict_status = 'none', conflict_detected_at = NULL, last_observed_at = ? WHERE id = ?",
                    (_now(), existing["id"]),
                )
                continue
            position_conflicts += 1
            if observation_id:
                db._exec(cur, "UPDATE trading_intraday_position_observations SET status = 'conflict' WHERE id = ?", (observation_id,))
            conflict_at = existing["conflict_detected_at"] or _now()
            db._exec(
                cur,
                "UPDATE trading_intraday_position_snapshots SET conflict_status = 'transient', conflict_detected_at = ?, last_observed_at = ? WHERE id = ?",
                (conflict_at, _now(), existing["id"]),
            )
            db._exec(
                cur,
                "INSERT INTO trading_collector_issues (device_id, account_id, issue_code, source_event_key, message, payload_json) VALUES (?, ?, 'position_conflict', ?, ?, ?)",
                (device["id"], account_id, data["source_snapshot_key"], "同一持仓快照编号的内容不一致，保留首次完整快照", payload_json),
            )
        db._exec(cur, "UPDATE trading_collector_devices SET last_seen_at = ? WHERE id = ? AND status = 'active'", (_now(), device["id"]))
    return IngestResult(
        position_accepted=position_accepted,
        position_duplicates=position_duplicates,
        position_conflicts=position_conflicts,
        position_quarantined=position_quarantined,
        position_observations=position_observation_count,
    )


def ingest_observations(
    device_token: str,
    observations: Sequence[Dict[str, Any]],
    position_snapshots: Sequence[Dict[str, Any]] = (),
) -> IngestResult:
    observations = list(observations or ())
    position_snapshots = list(position_snapshots or ())
    if len(observations) > 500:
        raise CollectorServiceError("batch_too_large", "单次最多上传 500 条成交")
    if len(position_snapshots) > 100:
        raise CollectorServiceError("batch_too_large", "单次最多上传 100 份持仓快照")
    device = get_device_by_token(device_token)
    fill_result = _ingest_fill_observations(device_token, observations) if observations else IngestResult()
    position_result = _ingest_position_snapshots(device, position_snapshots) if position_snapshots else IngestResult()
    return IngestResult(
        accepted=fill_result.accepted,
        duplicates=fill_result.duplicates,
        conflicts=fill_result.conflicts,
        quarantined=fill_result.quarantined,
        observations=fill_result.observations,
        fill_results=fill_result.fill_results,
        position_accepted=position_result.position_accepted,
        position_duplicates=position_result.position_duplicates,
        position_conflicts=position_result.position_conflicts,
        position_quarantined=position_result.position_quarantined,
        position_observations=position_result.position_observations,
    )


def _effective_fill_items(cur, rows: Sequence[Any]) -> List[Dict[str, Any]]:
    items = [dict(row) for row in rows]
    if not items:
        return items
    ids = [item["id"] for item in items]
    placeholders = ", ".join("?" for _ in ids)
    audit_rows = db._exec(
        cur,
        """
        SELECT intraday_fill_id, resolved_fields_json
        FROM trading_intraday_fill_reconciliations
        WHERE is_current = 1 AND intraday_fill_id IN (""" + placeholders + ")",
        tuple(ids),
    ).fetchall()
    resolved_by_id: Dict[int, Dict[str, Any]] = {}
    allowed = set(reconciliation.STATEMENT_OWNED_FIELDS + reconciliation.WH6_ONLY_FIELDS)
    for row in audit_rows:
        try:
            resolved = json.loads(row["resolved_fields_json"] or "{}")
        except (TypeError, ValueError):
            resolved = {}
        if isinstance(resolved, dict):
            resolved_by_id[int(row["intraday_fill_id"])] = {
                key: value for key, value in resolved.items() if key in allowed
            }
    for item in items:
        item.update(resolved_by_id.get(int(item["id"]), {}))
    return items


def query_intraday_fills(
    account_id: int,
    *,
    start: str = "",
    end: str = "",
    contract: str = "",
    status: str = "active",
    page: int = 1,
    page_size: int = 20,
    asset_type: Optional[str] = None,
) -> Dict[str, Any]:
    _account(account_id)
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError):
        raise CollectorServiceError("invalid_page", "成交明细页码格式不正确")
    if page < 1:
        raise CollectorServiceError("invalid_page", "成交明细页码必须从 1 开始")
    if page_size not in {20, 50, 100}:
        raise CollectorServiceError("invalid_page_size", "每页仅支持 20、50 或 100 条成交")
    where = ["account_id = ?"]
    params: List[Any] = [account_id]
    if start:
        where.append("trade_date >= ?")
        params.append(start[:10])
    if end:
        where.append("trade_date <= ?")
        params.append(end[:10])
    if contract:
        where.append("contract = ?")
        params.append(contract.strip().lower()[:80])
    if asset_type:
        where.append("asset_type = ?")
        params.append(asset_type.strip().lower()[:20])
    if status in {"active", ""}:
        where.append("data_status IN ('provisional', 'settlement_conflict')")
    elif status in {"accepted", "provisional"}:
        where.append("data_status = 'provisional'")
    elif status in {"settlement_covered", "settlement_conflict"}:
        where.append("data_status = ?")
        params.append(status)
    else:
        raise CollectorServiceError("invalid_status", "成交明细状态无法识别")
    predicate = " AND ".join(where)
    with db.connect() as conn:
        cur = conn.cursor()
        total_row = db._exec(
            cur, "SELECT COUNT(*) AS total_items FROM trading_intraday_fills WHERE " + predicate, tuple(params)
        ).fetchone()
        total_items = int(total_row["total_items"] or 0)
        offset = (page - 1) * page_size
        rows = db._exec(
            cur,
            "SELECT * FROM trading_intraday_fills WHERE " + predicate
            + " ORDER BY trade_date DESC, trade_time DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params + [page_size, offset]),
        ).fetchall()
        items = _effective_fill_items(cur, rows)
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    return {
        "items": items,
        "total": total_items,
        "total_items": total_items,
        "total_pages": total_pages,
        "page": page,
        "page_size": page_size,
        "account_id": account_id,
        "data_status": status or "active",
    }


def _sum_by(items: Sequence[Dict[str, Any]], key: str) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "未标明")
        result[value] = result.get(value, 0) + int(item.get("quantity") or 0)
    return result


def query_option_volume(
    account_id: int,
    *,
    trade_date: str = "",
    contract: str = "",
) -> Dict[str, Any]:
    _account(account_id)
    if not trade_date:
        trade_date = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    where = [
        "account_id = ?",
        "trade_date = ?",
        "asset_type = 'option'",
        "data_status NOT IN ('settlement_covered', 'settlement_conflict')",
    ]
    params: List[Any] = [account_id, trade_date]
    if contract:
        where.append("contract = ?")
        params.append(contract.strip().lower()[:80])
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(
            cur,
            "SELECT * FROM trading_intraday_fills WHERE " + " AND ".join(where)
            + " ORDER BY trade_date DESC, trade_time DESC, id DESC",
            tuple(params),
        ).fetchall()
        items = _effective_fill_items(cur, rows)
    return {
        "account_id": account_id,
        "trade_date": trade_date,
        "total_quantity": sum(int(item.get("quantity") or 0) for item in items),
        "by_contract": _sum_by(items, "contract"),
        "by_side": _sum_by(items, "side"),
        "by_open_close": _sum_by(items, "open_close"),
        "by_option_kind": _sum_by(items, "option_kind"),
        "items": items,
        "data_status": "provisional",
    }


def query_current_option_positions(account_id: int, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    _account(account_id)
    with db.connect() as conn:
        snapshot = db._exec(
            conn.cursor(),
            """
            SELECT * FROM trading_intraday_position_snapshots
            WHERE account_id = ? AND complete IS TRUE
            ORDER BY snapshot_timestamp DESC, id DESC
            LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        if not snapshot:
            return {
                "account_id": account_id,
                "items": [],
                "snapshot_timestamp": None,
                "source_status": "unavailable",
                "is_expired": True,
                "message": "当前没有可验证的完整持仓快照",
                "data_status": "provisional",
            }
        rows = db._exec(
            conn.cursor(),
            "SELECT * FROM trading_intraday_position_rows WHERE snapshot_id = ? AND account_id = ? AND asset_type = 'option' ORDER BY contract, direction, id",
            (snapshot["id"], account_id),
        ).fetchall()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    snapshot_dt = _parse_datetime(snapshot["snapshot_timestamp"])
    age_seconds = max(0, int((current - snapshot_dt).total_seconds())) if snapshot_dt else 10**9
    is_expired = age_seconds > 30
    conflict_age_seconds = 0
    conflict_dt = _parse_datetime(snapshot["conflict_detected_at"]) if snapshot["conflict_detected_at"] else None
    if conflict_dt:
        conflict_age_seconds = max(0, int((current - conflict_dt).total_seconds()))
    has_conflict = str(snapshot["conflict_status"] or "none") != "none"
    source_status = "multi_device_conflict" if has_conflict else "expired" if is_expired else "ok"
    message = "多设备持仓不一致" if has_conflict else "持仓数据可能已过期" if is_expired else ""
    items = [dict(row) for row in rows]
    return {
        "account_id": account_id,
        "items": items,
        "snapshot_timestamp": snapshot["snapshot_timestamp"],
        "trade_date": snapshot["trade_date"],
        "source_status": source_status,
        "conflict_status": "persistent" if has_conflict and conflict_age_seconds >= 30 else "transient" if has_conflict else "none",
        "conflict_age_seconds": conflict_age_seconds,
        "age_seconds": age_seconds,
        "is_expired": is_expired,
        "message": message,
        "data_status": "provisional",
        "verification_status": "pending",
    }
