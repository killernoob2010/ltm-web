"""Isolated persistence for the closing review Agent.

The store keeps ownership predicates in every read path.  It deliberately does
not cache business numbers for reuse by the model; the deterministic review
service remains the only source of numeric facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any, Optional, Union
from zoneinfo import ZoneInfo

from . import db


CHANNEL_WEB = "web"
CONVERSATION_KIND = "conversation"
DAILY_REVIEW_KIND = "daily_review"
DAILY_REVIEW_SYSTEM_KEY = "daily_review"
DEFAULT_CONVERSATION_TITLE = "新对话"
DAILY_REVIEW_TITLE = "日常复盘"
MESSAGE_ROLES = {"user", "assistant", "system"}
EXCLUDED_CONTEXT_MESSAGE_TYPES = {"suggestion", "loading", "status", "error"}
CONTENT_RETENTION_DAYS = 90
TASK_RETENTION_DAYS = 365
_UNSET = object()
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _now_iso(value: Optional[datetime] = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(_SHANGHAI).isoformat(timespec="seconds")


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _row_dict(row: Any) -> Optional[dict[str, Any]]:
    return dict(row) if row else None


def _validate_conversation(cur: Any, user_id: int, conversation_id: int) -> dict[str, Any]:
    row = db._exec(
        cur,
        """
        SELECT *
        FROM closing_review_conversations
        WHERE id = ? AND user_id = ? AND status = 'active'
        """,
        (conversation_id, user_id),
    ).fetchone()
    if not row:
        raise ValueError("conversation not found")
    return dict(row)


def create_conversation(user_id: int, title: str = DEFAULT_CONVERSATION_TITLE) -> dict[str, Any]:
    clean_title = str(title or "").strip() or DEFAULT_CONVERSATION_TITLE
    timestamp = _now_iso()
    with db.connect() as conn:
        cur = conn.cursor()
        conversation_id = db._last_insert_id(
            cur,
            """
            INSERT INTO closing_review_conversations
                (user_id, channel, kind, title, system_key, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (user_id, CHANNEL_WEB, CONVERSATION_KIND, clean_title, None, timestamp, timestamp),
        )
        row = db._exec(
            cur,
            """
            SELECT * FROM closing_review_conversations
            WHERE id = ? AND user_id = ? AND status = 'active'
            """,
            (conversation_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError("conversation could not be created")
    return dict(row)


def get_or_create_daily_conversation(user_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """
            SELECT *
            FROM closing_review_conversations
            WHERE user_id = ? AND channel = ? AND system_key = ? AND status = 'active'
            """,
            (user_id, CHANNEL_WEB, DAILY_REVIEW_SYSTEM_KEY),
        ).fetchone()
        if not row:
            timestamp = _now_iso()
            db._exec(
                cur,
                """
                INSERT OR IGNORE INTO closing_review_conversations
                    (user_id, channel, kind, title, system_key, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    user_id,
                    CHANNEL_WEB,
                    DAILY_REVIEW_KIND,
                    DAILY_REVIEW_TITLE,
                    DAILY_REVIEW_SYSTEM_KEY,
                    timestamp,
                    timestamp,
                ),
            )
            row = db._exec(
                cur,
                """
                SELECT *
                FROM closing_review_conversations
                WHERE user_id = ? AND channel = ? AND system_key = ? AND status = 'active'
                """,
                (user_id, CHANNEL_WEB, DAILY_REVIEW_SYSTEM_KEY),
            ).fetchone()
    if not row:
        raise ValueError("daily conversation could not be created")
    return dict(row)


def list_conversations(
    user_id: int,
    before_id: Optional[int] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 50), 100))
    sql = """
        SELECT *
        FROM closing_review_conversations
        WHERE user_id = ? AND status = 'active'
    """
    params: list[Any] = [user_id]
    if before_id is not None:
        sql += " AND id < ?"
        params.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(safe_limit)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = db._exec(cur, sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def get_owned_conversation(user_id: int, conversation_id: int) -> Optional[dict[str, Any]]:
    with db.connect() as conn:
        cur = conn.cursor()
        row = db._exec(
            cur,
            """
            SELECT *
            FROM closing_review_conversations
            WHERE id = ? AND user_id = ? AND status = 'active'
            """,
            (conversation_id, user_id),
        ).fetchone()
    return _row_dict(row)


def append_message(
    user_id: int,
    conversation_id: int,
    *,
    role: str,
    message_type: str,
    content: Optional[str] = None,
    structured_payload: Any = None,
    status: str = "active",
    task_id: Optional[int] = None,
    supersedes_message_id: Optional[int] = None,
    created_at: Optional[Union[datetime, str]] = None,
) -> dict[str, Any]:
    if role not in MESSAGE_ROLES:
        raise ValueError("invalid message role")
    with db.connect() as conn:
        cur = conn.cursor()
        _validate_conversation(cur, user_id, conversation_id)
        if task_id is not None:
            task = db._exec(
                cur,
                """
                SELECT id FROM closing_review_tasks
                WHERE id = ? AND user_id = ? AND conversation_id = ?
                """,
                (task_id, user_id, conversation_id),
            ).fetchone()
            if not task:
                raise ValueError("task not found")
        if supersedes_message_id is not None:
            previous = db._exec(
                cur,
                """
                SELECT m.id
                FROM closing_review_messages m
                JOIN closing_review_conversations c ON c.id = m.conversation_id
                WHERE m.id = ? AND m.conversation_id = ? AND c.user_id = ?
                """,
                (supersedes_message_id, conversation_id, user_id),
            ).fetchone()
            if not previous:
                raise ValueError("superseded message not found")
        timestamp = created_at
        if isinstance(timestamp, datetime) or timestamp is None:
            timestamp = _now_iso(timestamp)
        else:
            timestamp = str(timestamp)
        message_id = db._last_insert_id(
            cur,
            """
            INSERT INTO closing_review_messages
                (conversation_id, task_id, role, message_type, content,
                 structured_payload, status, supersedes_message_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                task_id,
                role,
                message_type,
                content,
                _json_text(structured_payload),
                status,
                supersedes_message_id,
                timestamp,
            ),
        )
        db._exec(
            cur,
            """
            UPDATE closing_review_conversations
            SET last_message_at = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND status = 'active'
            """,
            (timestamp, timestamp, conversation_id, user_id),
        )
        row = db._exec(
            cur,
            """
            SELECT * FROM closing_review_messages
            WHERE id = ? AND conversation_id = ?
            """,
            (message_id, conversation_id),
        ).fetchone()
    if not row:
        raise ValueError("message could not be created")
    return dict(row)


def list_messages(
    user_id: int,
    conversation_id: int,
    before_id: Optional[int] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 100), 200))
    sql = """
        SELECT m.*
        FROM closing_review_messages m
        JOIN closing_review_conversations c ON c.id = m.conversation_id
        WHERE m.conversation_id = ? AND c.user_id = ?
          AND c.status = 'active' AND m.status = 'active'
    """
    params: list[Any] = [conversation_id, user_id]
    if before_id is not None:
        sql += " AND m.id < ?"
        params.append(before_id)
    sql += " ORDER BY m.id DESC LIMIT ?"
    params.append(safe_limit)
    with db.connect() as conn:
        cur = conn.cursor()
        rows = [dict(row) for row in db._exec(cur, sql, tuple(params)).fetchall()]
    rows.reverse()
    return rows


def claim_user_task(
    user_id: int,
    conversation_id: int,
    client_request_id: str,
    *,
    task_kind: str = "user_message",
    task_profile: Optional[str] = None,
    target_date: Optional[str] = None,
    state: str = "processing",
    source_signature: Optional[str] = None,
) -> tuple[dict[str, Any], bool]:
    clean_request_id = str(client_request_id or "").strip()
    if not clean_request_id:
        raise ValueError("client request id is required")
    with db.connect() as conn:
        cur = conn.cursor()
        _validate_conversation(cur, user_id, conversation_id)
        existing = db._exec(
            cur,
            """
            SELECT * FROM closing_review_tasks
            WHERE user_id = ? AND client_request_id = ?
            """,
            (user_id, clean_request_id),
        ).fetchone()
        if existing:
            return dict(existing), False
        timestamp = _now_iso()
        db._exec(
            cur,
            """
            INSERT OR IGNORE INTO closing_review_tasks
                (user_id, conversation_id, client_request_id, task_kind, task_profile,
                 target_date, state, source_signature, started_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                conversation_id,
                clean_request_id,
                task_kind,
                task_profile,
                target_date,
                state,
                source_signature,
                timestamp,
                timestamp,
            ),
        )
        row = db._exec(
            cur,
            """
            SELECT * FROM closing_review_tasks
            WHERE user_id = ? AND client_request_id = ?
            """,
            (user_id, clean_request_id),
        ).fetchone()
    if not row:
        raise ValueError("task could not be claimed")
    return dict(row), True


def finish_task(
    task_id: int,
    *,
    state: str,
    user_id: Optional[int] = None,
    task_profile: Any = _UNSET,
    target_date: Any = _UNSET,
    validated_intent: Any = _UNSET,
    result_projection: Any = _UNSET,
    model_provider: Any = _UNSET,
    model_name: Any = _UNSET,
    prompt_version: Any = _UNSET,
    model_usage: Any = _UNSET,
    model_finish_reason: Any = _UNSET,
    model_duration_seconds: Any = _UNSET,
    workflow_version: Any = _UNSET,
    calculation_version: Any = _UNSET,
    rule_version: Any = _UNSET,
    retry_count: Any = _UNSET,
    error_category: Any = _UNSET,
    source_signature: Any = _UNSET,
    started_at: Any = _UNSET,
    finished_at: Any = _UNSET,
) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        sql = "SELECT * FROM closing_review_tasks WHERE id = ?"
        params: list[Any] = [task_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        row = db._exec(cur, sql, tuple(params)).fetchone()
        if not row:
            raise ValueError("task not found")

        values = {
            "state": state,
            "task_profile": task_profile,
            "target_date": target_date,
            "validated_intent": validated_intent,
            "result_projection": result_projection,
            "model_provider": model_provider,
            "model_name": model_name,
            "prompt_version": prompt_version,
            "model_usage": model_usage,
            "model_finish_reason": model_finish_reason,
            "model_duration_seconds": model_duration_seconds,
            "workflow_version": workflow_version,
            "calculation_version": calculation_version,
            "rule_version": rule_version,
            "retry_count": retry_count,
            "error_category": error_category,
            "source_signature": source_signature,
            "started_at": started_at,
            "finished_at": finished_at,
        }
        update_columns: list[str] = []
        update_values: list[Any] = []
        json_columns = {"validated_intent", "result_projection", "model_usage"}
        for column, value in values.items():
            if value is _UNSET:
                continue
            update_columns.append(f"{column} = ?")
            update_values.append(_json_text(value) if column in json_columns else value)
        if finished_at is _UNSET and state in {
            "succeeded", "failed", "temporarily_unavailable", "controlled",
        }:
            update_columns.append("finished_at = ?")
            update_values.append(_now_iso())
        if not update_columns:
            return dict(row)
        update_values.append(task_id)
        sql = f"UPDATE closing_review_tasks SET {', '.join(update_columns)} WHERE id = ?"
        db._exec(cur, sql, tuple(update_values))
        updated = db._exec(
            cur,
            "SELECT * FROM closing_review_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
    if not updated:
        raise ValueError("task could not be updated")
    return dict(updated)


def _safe_anchor(task: dict[str, Any]) -> Optional[dict[str, Any]]:
    projection = _json_value(task.get("result_projection"))
    if not isinstance(projection, dict):
        projection = {}
    labels = projection.get("displayed_metric_labels")
    if labels is None:
        labels = projection.get("metric_labels")
    if isinstance(labels, str):
        labels = [labels]
    if not isinstance(labels, list):
        labels = []
    anchor: dict[str, Any] = {
        "task_profile": task.get("task_profile"),
        "target_date": task.get("target_date"),
        "displayed_metric_labels": [str(item) for item in labels if str(item).strip()],
        "result_ref": projection.get("result_ref"),
    }
    if not anchor["task_profile"] and not anchor["target_date"] and not anchor["result_ref"]:
        return None
    return anchor


def build_context(user_id: int, conversation_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        cur = conn.cursor()
        _validate_conversation(cur, user_id, conversation_id)
        rows = db._exec(
            cur,
            """
            SELECT m.id, m.role, m.message_type, m.content, m.created_at
            FROM closing_review_messages m
            JOIN closing_review_conversations c ON c.id = m.conversation_id
            WHERE m.conversation_id = ? AND c.user_id = ?
              AND c.status = 'active' AND m.status = 'active'
              AND m.content IS NOT NULL
              AND m.message_type NOT IN ('suggestion', 'loading', 'status', 'error')
            ORDER BY m.id DESC
            LIMIT 12
            """,
            (conversation_id, user_id),
        ).fetchall()
        latest_task = db._exec(
            cur,
            """
            SELECT task_profile, target_date, result_projection
            FROM closing_review_tasks
            WHERE user_id = ? AND conversation_id = ? AND state = 'succeeded'
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, conversation_id),
        ).fetchone()
    messages = [
        {
            "id": row["id"],
            "role": row["role"],
            "message_type": row["message_type"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        for row in reversed(rows)
    ]
    return {
        "messages": messages,
        "anchor": _safe_anchor(dict(latest_task)) if latest_task else None,
    }


def redact_expired_content(now: datetime) -> dict[str, int]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    content_cutoff = now.astimezone(timezone.utc) - timedelta(days=CONTENT_RETENTION_DAYS)
    task_cutoff = now.astimezone(timezone.utc) - timedelta(days=TASK_RETENTION_DAYS)
    redaction_timestamp = _now_iso(now)
    redacted_messages = 0
    deleted_tasks = 0
    redacted_conversations = 0
    with db.connect() as conn:
        cur = conn.cursor()
        message_rows = db._exec(
            cur,
            """
            SELECT id, conversation_id, created_at
            FROM closing_review_messages
            WHERE redacted_at IS NULL
              AND (content IS NOT NULL OR structured_payload IS NOT NULL)
            """,
        ).fetchall()
        affected_conversations: set[int] = set()
        for row in message_rows:
            created_at = _parse_timestamp(row["created_at"])
            if created_at is None or created_at >= content_cutoff:
                continue
            db._exec(
                cur,
                """
                UPDATE closing_review_messages
                SET content = NULL, structured_payload = NULL,
                    status = 'redacted', redacted_at = ?
                WHERE id = ? AND redacted_at IS NULL
                """,
                (redaction_timestamp, row["id"]),
            )
            redacted_messages += 1
            affected_conversations.add(int(row["conversation_id"]))

        task_rows = db._exec(
            cur,
            "SELECT id, created_at FROM closing_review_tasks",
        ).fetchall()
        for row in task_rows:
            created_at = _parse_timestamp(row["created_at"])
            if created_at is None or created_at >= task_cutoff:
                continue
            db._exec(cur, "DELETE FROM closing_review_tasks WHERE id = ?", (row["id"],))
            deleted_tasks += 1

        for conversation_id in affected_conversations:
            active = db._exec(
                cur,
                """
                SELECT id FROM closing_review_messages
                WHERE conversation_id = ? AND status = 'active'
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if active:
                continue
            updated = db._exec(
                cur,
                """
                UPDATE closing_review_conversations
                SET status = 'redacted', updated_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (redaction_timestamp, conversation_id),
            )
            redacted_conversations += max(0, int(updated.rowcount or 0))

    db.log_operation(
        None,
        "closing_review_agent",
        "retention_cleanup",
        f"收盘复盘 Agent 保留清理：脱敏消息 {redacted_messages} 条，删除任务元数据 {deleted_tasks} 条",
    )
    return {
        "redacted_messages": redacted_messages,
        "deleted_tasks": deleted_tasks,
        "redacted_conversations": redacted_conversations,
    }
