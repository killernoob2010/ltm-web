"""Durable local SQLite outbox owned by the collector, never by WH6."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Dict, Iterable, List, Optional, Sequence

from .models import FillRecord, ParseIssue, PositionSnapshot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalOutbox:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _init_schema(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS outbox (
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
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_claim
                    ON outbox(status, priority, available_at, created_at);
                CREATE TABLE IF NOT EXISTS file_checkpoints (
                    source_path TEXT PRIMARY KEY,
                    checkpoint_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS issues (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    message TEXT NOT NULL,
                    path TEXT,
                    record_index INTEGER,
                    file_sha256 TEXT,
                    severity TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(outbox)").fetchall()}
            if "item_type" not in columns:
                connection.execute("ALTER TABLE outbox ADD COLUMN item_type TEXT NOT NULL DEFAULT 'fill'")
            if "priority" not in columns:
                connection.execute("ALTER TABLE outbox ADD COLUMN priority TEXT NOT NULL DEFAULT 'history'")

    def put(self, fill: FillRecord, *, priority: str = "history") -> bool:
        if priority not in {"realtime", "history"}:
            raise ValueError("未知队列优先级")
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO outbox
                    (event_key, payload_json, item_type, priority, status, attempts, available_at, created_at, updated_at)
                VALUES (?, ?, 'fill', ?, 'pending', 0, ?, ?, ?)
                """,
                (fill.source_event_key, json.dumps(fill.to_payload(), ensure_ascii=False), priority, now, now, now),
            )
            return cursor.rowcount == 1

    def put_many(self, fills: Iterable[FillRecord], *, priority: str = "history") -> int:
        return sum(1 for fill in fills if self.put(fill, priority=priority))

    def put_position(self, snapshot: PositionSnapshot, *, priority: str = "realtime") -> bool:
        if priority not in {"realtime", "history"}:
            raise ValueError("未知队列优先级")
        now = _now()
        event_key = "position:%s:%s" % (snapshot.source_snapshot_key, snapshot.source_snapshot_sha256)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO outbox
                    (event_key, payload_json, item_type, priority, status, attempts, available_at, created_at, updated_at)
                VALUES (?, ?, 'position_snapshot', ?, 'pending', 0, ?, ?, ?)
                """,
                (event_key, json.dumps(snapshot.to_payload(), ensure_ascii=False), priority, now, now, now),
            )
            return cursor.rowcount == 1

    def put_many_positions(self, snapshots: Iterable[PositionSnapshot], *, priority: str = "realtime") -> int:
        return sum(1 for snapshot in snapshots if self.put_position(snapshot, priority=priority))

    def claim(self, limit: int = 100, *, priority: Optional[str] = None) -> List[Dict[str, object]]:
        if limit <= 0:
            return []
        if priority is not None and priority not in {"realtime", "history"}:
            raise ValueError("未知队列优先级")
        now = _now()
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A process can terminate after claiming but before the HTTP
            # acknowledgement.  Make those rows retryable on the next scan.
            connection.execute(
                "UPDATE outbox SET status='pending', available_at=?, last_error=COALESCE(last_error, 'claim expired'), updated_at=? WHERE status='claimed' AND updated_at < ?",
                (now, now, stale_before),
            )
            where_priority = " AND priority = ?" if priority is not None else ""
            query_args = [now]
            if priority is not None:
                query_args.append(priority)
            query_args.append(int(limit))
            rows = connection.execute(
                """
                SELECT event_key, payload_json, item_type, priority, attempts
                FROM outbox
                WHERE status = 'pending' AND available_at <= ?
                """ + where_priority + """
                ORDER BY CASE WHEN priority = 'realtime' THEN 0 ELSE 1 END, created_at, event_key
                LIMIT ?
                """,
                tuple(query_args),
            ).fetchall()
            keys = [row["event_key"] for row in rows]
            if keys:
                placeholders = ",".join("?" for _ in keys)
                connection.execute(
                    f"UPDATE outbox SET status='claimed', attempts=attempts+1, updated_at=? WHERE event_key IN ({placeholders})",
                    (now, *keys),
                )
            return [dict(row) for row in rows]

    def ack(self, event_keys: Sequence[str]) -> None:
        if not event_keys:
            return
        placeholders = ",".join("?" for _ in event_keys)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE outbox SET status='acked', updated_at=? WHERE event_key IN ({placeholders})",
                (_now(), *event_keys),
            )

    def release(self, event_keys: Sequence[str], error: str) -> None:
        if not event_keys:
            return
        placeholders = ",".join("?" for _ in event_keys)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE outbox SET status='pending', available_at=?, last_error=?, updated_at=? WHERE event_key IN ({placeholders})",
                (_now(), str(error)[:500], _now(), *event_keys),
            )

    def _checkpoint_key(self, source_path: str, kind: str) -> str:
        return source_path if kind == "match" else "%s:%s" % (kind, source_path)

    def save_checkpoint(self, source_path: str, checkpoint: Dict[str, object], *, kind: str = "match") -> None:
        now = _now()
        key = self._checkpoint_key(source_path, kind)
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO file_checkpoints(source_path, checkpoint_json, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(checkpoint, ensure_ascii=False, sort_keys=True), now),
            )

    def load_checkpoint(self, source_path: str, *, kind: str = "match") -> Optional[Dict[str, object]]:
        key = self._checkpoint_key(source_path, kind)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT checkpoint_json FROM file_checkpoints WHERE source_path = ?", (key,)
            ).fetchone()
        return json.loads(row["checkpoint_json"]) if row else None

    def add_issue(self, issue: ParseIssue) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO issues(code, message, path, record_index, file_sha256, severity, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (issue.code, issue.message, issue.path, issue.record_index, issue.file_sha256, issue.severity, _now()),
            )

    def status(self) -> Dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM outbox GROUP BY status").fetchall()
            issue_count = connection.execute("SELECT COUNT(*) AS count FROM issues").fetchone()["count"]
        result = {"pending": 0, "claimed": 0, "acked": 0, "issues": int(issue_count)}
        result.update({str(row["status"]): int(row["count"]) for row in rows})
        return result
