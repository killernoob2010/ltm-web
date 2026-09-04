"""Durable local SQLite outbox owned by the collector, never by WH6."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from .models import FillRecord, ParseIssue, PositionSnapshot
from .policy import CollectionPolicy


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalOutbox:
    def __init__(self, db_path: Path, *, policy=None):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        from .migrations import migrate_local_store

        self.migration_result = migrate_local_store(self.db_path, policy=policy)
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
                CREATE TABLE IF NOT EXISTS collection_policy (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload_json TEXT NOT NULL,
                    policy_revision TEXT NOT NULL,
                    fetched_at TEXT NOT NULL
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

    def _release(self, event_keys: Sequence[str], error: str, *, retryable: bool) -> None:
        if not event_keys:
            return
        now = datetime.now(timezone.utc)
        now_text = now.isoformat()
        with self._connect() as connection:
            placeholders = ",".join("?" for _ in event_keys)
            rows = connection.execute(
                f"SELECT event_key, attempts FROM outbox WHERE event_key IN ({placeholders})",
                tuple(event_keys),
            ).fetchall()
            for row in rows:
                attempts = int(row["attempts"] or 0)
                delay = 300 if not retryable else min(300, 5 * (2 ** min(max(attempts - 1, 0), 6)))
                available_at = (now + timedelta(seconds=delay)).isoformat()
                connection.execute(
                    """
                    UPDATE outbox
                    SET status='pending', available_at=?, last_error=?, updated_at=?
                    WHERE event_key=?
                    """,
                    (available_at, str(error)[:500], now_text, row["event_key"]),
                )

    def release(self, event_keys: Sequence[str], error: str, *, retryable: bool = True) -> None:
        self._release(event_keys, error, retryable=retryable)

    def ack_results(
        self,
        results: Sequence[Mapping[str, str]],
        *,
        expected_event_keys: Optional[Sequence[str]] = None,
    ) -> Dict[str, int]:
        """Apply only terminal per-item receipts and retry unresolved rows safely."""
        expected = {str(key) for key in expected_event_keys} if expected_event_keys is not None else None
        seen = set()
        invalid_keys = set()
        actions: Dict[str, str] = {}
        counts = {"acked": 0, "covered_by_monthly": 0, "conflict": 0, "quarantined": 0, "invalid": 0}
        status_map = {
            "accepted": "acked",
            "duplicate": "acked",
            "settlement_covered": "covered_by_monthly",
            "conflict": "conflict",
            "quarantined": "quarantined",
        }
        for result in results or ():
            if not isinstance(result, Mapping):
                counts["invalid"] += 1
                continue
            key = str(result.get("event_key") or "").strip()
            status = str(result.get("status") or "").strip().lower()
            if not key or (expected is not None and key not in expected) or key in seen:
                counts["invalid"] += 1
                if key:
                    invalid_keys.add(key)
                continue
            seen.add(key)
            action = status_map.get(status)
            if action is None:
                counts["invalid"] += 1
                invalid_keys.add(key)
                continue
            actions[key] = action

        unresolved = set(invalid_keys)
        if expected is not None:
            missing = expected - seen
            unresolved.update(missing)
            counts["invalid"] += len(missing)
        # A duplicate or malformed receipt for a key invalidates that key's
        # otherwise valid receipt; do not accidentally acknowledge it.
        actions = {key: action for key, action in actions.items() if key not in unresolved}
        now = _now()
        with self._connect() as connection:
            for key, action in actions.items():
                connection.execute(
                    "UPDATE outbox SET status=?, updated_at=? WHERE event_key=?",
                    (action, now, key),
                )
                counts[action] += 1
        if unresolved:
            self._release(sorted(unresolved), "invalid_server_receipt", retryable=True)
        return counts

    def save_collection_policy(self, policy, *, fetched_at: Optional[str] = None) -> CollectionPolicy:
        validated = policy if isinstance(policy, CollectionPolicy) else CollectionPolicy.from_payload(policy)
        timestamp = fetched_at or _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO collection_policy
                    (id, payload_json, policy_revision, fetched_at)
                VALUES (1, ?, ?, ?)
                """,
                (
                    json.dumps(validated.to_payload(), ensure_ascii=False, sort_keys=True),
                    validated.policy_revision,
                    timestamp,
                ),
            )
        return validated

    def load_collection_policy(self, *, max_age_seconds: int = 300, now: Optional[datetime] = None) -> Optional[CollectionPolicy]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json, fetched_at FROM collection_policy WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        try:
            fetched_at = datetime.fromisoformat(str(row["fetched_at"]).replace("Z", "+00:00"))
            current = now or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = max(0.0, (current - fetched_at).total_seconds())
            if age > max(0, int(max_age_seconds)):
                return None
            payload = json.loads(str(row["payload_json"]))
            return CollectionPolicy.from_payload(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def apply_collection_policy(self, policy) -> int:
        validated = policy if isinstance(policy, CollectionPolicy) else CollectionPolicy.from_payload(policy)
        now = _now()
        changed = 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_key, payload_json, status
                FROM outbox
                WHERE item_type = 'fill'
                  AND status IN ('pending', 'claimed', 'covered_by_monthly')
                """
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(str(row["payload_json"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                trade_date = str(payload.get("trade_date") or "") if isinstance(payload, dict) else ""
                covered = validated.covers(trade_date)
                status = str(row["status"])
                if covered and status in {"pending", "claimed"}:
                    connection.execute(
                        "UPDATE outbox SET status='covered_by_monthly', available_at=?, updated_at=? WHERE event_key=?",
                        (now, now, row["event_key"]),
                    )
                    changed += 1
                elif not covered and status == "covered_by_monthly":
                    connection.execute(
                        "UPDATE outbox SET status='pending', available_at=?, updated_at=? WHERE event_key=?",
                        (now, now, row["event_key"]),
                    )
                    changed += 1
        return changed

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
