"""Backup-first migrations for the collector-owned SQLite state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Dict, List, Mapping, Optional

from .version import LOCAL_SCHEMA_VERSION, PARSER_GENERATION


EXCHANGE_ALIASES = {
    "dce": "dce",
    "大商所": "dce",
    "大连商品交易所": "dce",
    "shfe": "shfe",
    "上期所": "shfe",
    "上海期货交易所": "shfe",
    "czce": "czce",
    "郑商所": "czce",
    "郑州商品交易所": "czce",
    "cffex": "cffex",
    "中金所": "cffex",
    "中国金融期货交易所": "cffex",
    "ine": "ine",
    "能源中心": "ine",
    "上海国际能源交易中心": "ine",
    "gfex": "gfex",
    "广期所": "gfex",
    "广州期货交易所": "gfex",
}
TERMINAL_STATE_ORDER = {
    "acked": 5,
    "covered_by_monthly": 4,
    "quarantined": 3,
    "conflict": 3,
    "pending": 2,
    "claimed": 1,
}
DATE_RE = re.compile(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})")


@dataclass(frozen=True)
class LocalMigrationResult:
    backup_path: Optional[Path]
    old_version: int
    new_version: int
    keys_rewritten: int = 0
    duplicates_merged: int = 0
    claims_released: int = 0
    monthly_covered: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_date(value: object) -> str:
    match = DATE_RE.match(str(value or "").strip())
    if not match:
        return ""
    return "%s-%s-%s" % match.groups()


def _normalize_exchange(value: object) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    return EXCHANGE_ALIASES.get(text, text)


def _normalize_id(value: object) -> str:
    text = str(value or "").strip().lower()
    return str(int(text)) if text.isdigit() else text


def canonical_fill_event_key(payload: Mapping[str, object]) -> str:
    """Build the V2.1 identity key, retaining a legacy key when evidence is incomplete."""
    trade_id = payload.get("trade_id") or payload.get("transaction_no") or payload.get("成交编号")
    source_event_key = str(payload.get("source_event_key") or "").strip()
    if not trade_id and source_event_key.lower().startswith("tradeid:"):
        trade_id = source_event_key.rsplit(":", 1)[-1]
    trade_date = _normalize_date(payload.get("trade_date"))
    exchange = _normalize_exchange(payload.get("exchange"))
    normalized_id = _normalize_id(trade_id)
    if not trade_date or not exchange or not normalized_id:
        return source_event_key
    return "tradeid:%s:%s:%s" % (trade_date, exchange, normalized_id)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone() is not None


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS outbox (
            event_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            item_type TEXT NOT NULL DEFAULT 'fill',
            priority TEXT NOT NULL DEFAULT 'history',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TEXT NOT NULL,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS file_checkpoints (
            source_path TEXT PRIMARY KEY,
            checkpoint_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            message TEXT NOT NULL,
            path TEXT,
            record_index INTEGER,
            file_sha256 TEXT,
            severity TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS local_schema_meta (
            version INTEGER PRIMARY KEY,
            migrated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS collection_policy (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload_json TEXT NOT NULL,
            policy_revision TEXT NOT NULL,
            fetched_at TEXT NOT NULL
        )"""
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(outbox)")}
    if "item_type" not in columns:
        connection.execute("ALTER TABLE outbox ADD COLUMN item_type TEXT NOT NULL DEFAULT 'fill'")
    if "priority" not in columns:
        connection.execute("ALTER TABLE outbox ADD COLUMN priority TEXT NOT NULL DEFAULT 'history'")


def _read_version(connection: sqlite3.Connection) -> int:
    if not _table_exists(connection, "local_schema_meta"):
        return 2 if _table_exists(connection, "outbox") else 0
    row = connection.execute("SELECT MAX(version) AS version FROM local_schema_meta").fetchone()
    return int(row[0] or 0)


def _backup_database(db_path: Path, old_version: int) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / ("collector-v%d-%s.sqlite3" % (old_version, stamp))
    if backup_path.exists():
        backup_path = backup_dir / ("collector-v%d-%s-%s.sqlite3" % (
            old_version,
            stamp,
            datetime.now(timezone.utc).strftime("%f"),
        ))
    source = sqlite3.connect(str(db_path))
    target = sqlite3.connect(str(backup_path))
    try:
        source.backup(target)
        target.commit()
        quick_check = target.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError("本地 SQLite 备份校验失败")
    finally:
        target.close()
        source.close()
    return backup_path


def _payload(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        value = json.loads(str(row["payload_json"]))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _status_rank(status: str) -> int:
    return TERMINAL_STATE_ORDER.get(str(status or "").lower(), 0)


def _merge_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    strongest = max(
        rows,
        key=lambda row: (_status_rank(str(row["status"])), str(row["updated_at"]), str(row["event_key"])),
    )
    statuses = [str(row["status"]) for row in rows]
    status = max(statuses, key=_status_rank)
    payload = _payload(strongest)
    payload["source_event_key"] = strongest["event_key"]
    errors = [str(row["last_error"]) for row in rows if row["last_error"]]
    created_at = min(str(row["created_at"]) for row in rows)
    updated_at = max(str(row["updated_at"]) for row in rows)
    available_at = min(str(row["available_at"]) for row in rows)
    return {
        "event_key": strongest["event_key"],
        "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "item_type": strongest["item_type"],
        "priority": "realtime" if any(row["priority"] == "realtime" for row in rows) else "history",
        "status": status,
        "attempts": max(int(row["attempts"] or 0) for row in rows),
        "available_at": available_at,
        "last_error": errors[-1] if errors else None,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def migrate_local_store(db_path: Path, *, policy: Optional[object] = None) -> LocalMigrationResult:
    """Migrate one local store atomically; a failed migration leaves its source usable."""
    db_path = Path(db_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        sqlite3.connect(str(db_path)).close()
    probe = sqlite3.connect(str(db_path))
    try:
        old_version = _read_version(probe)
    finally:
        probe.close()
    if old_version >= LOCAL_SCHEMA_VERSION:
        return LocalMigrationResult(None, old_version, LOCAL_SCHEMA_VERSION)

    backup_path = _backup_database(db_path, old_version)
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    keys_rewritten = duplicates_merged = claims_released = monthly_covered = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        _ensure_schema(connection)
        now = _now()
        claimed = connection.execute("SELECT event_key FROM outbox WHERE status = 'claimed'").fetchall()
        if claimed:
            connection.execute(
                "UPDATE outbox SET status = 'pending', available_at = ?, updated_at = ? WHERE status = 'claimed'",
                (now, now),
            )
            claims_released = len(claimed)

        rows = [dict(row) for row in connection.execute("SELECT * FROM outbox ORDER BY created_at, event_key")]
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            payload = _payload(row)
            target_key = row["event_key"]
            if row["item_type"] == "fill" and payload:
                target_key = canonical_fill_event_key(payload) or target_key
                if target_key != row["event_key"]:
                    keys_rewritten += 1
                payload["source_event_key"] = target_key
                row["payload_json"] = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            row["event_key"] = target_key
            if policy is not None and row["item_type"] == "fill":
                trade_date = _normalize_date(payload.get("trade_date"))
                if trade_date and bool(policy.covers(trade_date)) and _status_rank(row["status"]) <= _status_rank("pending"):
                    row["status"] = "covered_by_monthly"
                    row["available_at"] = now
                    row["updated_at"] = now
                    monthly_covered += 1
            groups.setdefault(target_key, []).append(row)

        merged = [_merge_rows(items) for items in groups.values()]
        duplicates_merged = len(rows) - len(merged)
        connection.execute("DELETE FROM outbox")
        connection.executemany(
            """
            INSERT INTO outbox
                (event_key, payload_json, item_type, priority, status, attempts,
                 available_at, last_error, created_at, updated_at)
            VALUES (:event_key, :payload_json, :item_type, :priority, :status,
                    :attempts, :available_at, :last_error, :created_at, :updated_at)
            """,
            merged,
        )

        checkpoints = connection.execute("SELECT source_path, checkpoint_json FROM file_checkpoints").fetchall()
        for row in checkpoints:
            source_path = str(row["source_path"])
            if source_path.startswith("position:"):
                continue
            try:
                checkpoint = json.loads(row["checkpoint_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                checkpoint = {}
            if not isinstance(checkpoint, dict) or int(checkpoint.get("parser_generation") or 0) >= PARSER_GENERATION:
                continue
            checkpoint["parser_generation"] = PARSER_GENERATION - 1
            connection.execute(
                "UPDATE file_checkpoints SET checkpoint_json = ?, updated_at = ? WHERE source_path = ?",
                (json.dumps(checkpoint, ensure_ascii=False, sort_keys=True), now, source_path),
            )
        connection.execute(
            "INSERT OR REPLACE INTO local_schema_meta(version, migrated_at) VALUES (?, ?)",
            (LOCAL_SCHEMA_VERSION, now),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return LocalMigrationResult(
        backup_path,
        old_version,
        LOCAL_SCHEMA_VERSION,
        keys_rewritten,
        duplicates_merged,
        claims_released,
        monthly_covered,
    )
