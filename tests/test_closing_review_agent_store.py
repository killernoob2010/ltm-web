"""Behavior tests for isolated closing review Agent persistence."""

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db
from app import closing_review_agent_store as store


@pytest.fixture
def temp_agent_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "closing-review-agent-store.db")
    db.init_db()


def seed_user(username: str) -> int:
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO users (name, username, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, username, "期货组", db.password_hash("password"), "用户"),
        )
        return int(cur.lastrowid)


def test_conversation_owner_is_enforced(temp_agent_db):
    owner = seed_user("owner")
    stranger = seed_user("stranger")
    conversation = store.create_conversation(owner, "六月复盘")

    assert store.get_owned_conversation(owner, conversation["id"])
    assert store.get_owned_conversation(stranger, conversation["id"]) is None


def test_daily_conversation_is_unique_per_user(temp_agent_db):
    user_id = seed_user("tester")

    first = store.get_or_create_daily_conversation(user_id)
    second = store.get_or_create_daily_conversation(user_id)

    assert first["id"] == second["id"]
    assert first["kind"] == "daily_review"
    assert first["system_key"] == "daily_review"


def test_conversation_and_message_pagination_are_owner_scoped(temp_agent_db):
    owner = seed_user("owner")
    stranger = seed_user("stranger")
    conversations = [
        store.create_conversation(owner, f"复盘 {index}")
        for index in range(3)
    ]
    store.create_conversation(stranger, "他人的复盘")

    page = store.list_conversations(owner, before_id=None, limit=2)
    assert [item["id"] for item in page] == [conversations[2]["id"], conversations[1]["id"]]
    older = store.list_conversations(owner, before_id=page[-1]["id"], limit=2)
    assert [item["id"] for item in older] == [conversations[0]["id"]]

    for index in range(3):
        store.append_message(
            owner,
            conversations[0]["id"],
            role="user" if index % 2 == 0 else "assistant",
            message_type="user" if index % 2 == 0 else "answer",
            content=f"消息 {index}",
        )
    assert [item["content"] for item in store.list_messages(owner, conversations[0]["id"], None, 10)] == [
        "消息 0", "消息 1", "消息 2"
    ]
    assert store.list_messages(stranger, conversations[0]["id"], None, 10) == []


def test_claim_user_task_is_idempotent_per_user(temp_agent_db):
    owner = seed_user("owner")
    other = seed_user("other")
    owner_conversation = store.create_conversation(owner)
    other_conversation = store.create_conversation(other)

    first, created = store.claim_user_task(
        owner,
        owner_conversation["id"],
        "request-1",
        task_kind="user_message",
        task_profile="option_position_query",
    )
    duplicate, duplicate_created = store.claim_user_task(
        owner,
        owner_conversation["id"],
        "request-1",
        task_kind="user_message",
        task_profile="option_position_query",
    )
    other_task, other_created = store.claim_user_task(
        other,
        other_conversation["id"],
        "request-1",
        task_kind="user_message",
        task_profile="option_position_query",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    assert other_created is True
    assert other_task["id"] != first["id"]


def test_context_is_limited_to_recent_eligible_messages_and_safe_anchor(temp_agent_db):
    user_id = seed_user("context-user")
    conversation = store.create_conversation(user_id)
    for index in range(20):
        store.append_message(
            user_id,
            conversation["id"],
            role="user" if index % 2 == 0 else "assistant",
            message_type="user" if index % 2 == 0 else "answer",
            content=f"第 {index} 轮",
        )
    store.append_message(
        user_id,
        conversation["id"],
        role="system",
        message_type="suggestion",
        content="推荐问题不应进入模型上下文",
    )
    store.append_message(
        user_id,
        conversation["id"],
        role="system",
        message_type="loading",
        content="加载中",
    )
    task, _ = store.claim_user_task(
        user_id,
        conversation["id"],
        "anchor-request",
        task_kind="user_message",
        task_profile="option_realized_pnl_query",
        target_date="20260831",
    )
    store.finish_task(
        task["id"],
        state="succeeded",
        validated_intent={"task_profile": "option_realized_pnl_query"},
        result_projection={
            "displayed_metric_labels": ["真实平仓盈亏"],
            "result_ref": "task:anchor-request",
            "realized_close_pnl": 123456.78,
        },
    )

    context = store.build_context(user_id, conversation["id"])

    assert len(context["messages"]) == 12
    assert all(message["message_type"] not in {"suggestion", "loading"} for message in context["messages"])
    assert context["anchor"] == {
        "task_profile": "option_realized_pnl_query",
        "target_date": "20260831",
        "displayed_metric_labels": ["真实平仓盈亏"],
        "result_ref": "task:anchor-request",
    }
    assert "realized_close_pnl" not in context["anchor"]
    assert "123456.78" not in str(context["anchor"])


def test_retention_uses_strict_age_boundaries_and_redacts_content(temp_agent_db):
    user_id = seed_user("retention-user")
    fixed_now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    conversation = store.create_conversation(user_id)
    message_ids = {}
    task_ids = {}
    with db.connect() as conn:
        cur = conn.cursor()
        for age in (89, 90, 91):
            created_at = (fixed_now - timedelta(days=age)).isoformat(timespec="seconds")
            message_ids[age] = db._last_insert_id(
                cur,
                """
                INSERT INTO closing_review_messages
                    (conversation_id, role, message_type, content, structured_payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation["id"], "assistant", "answer", f"内容 {age}", '{"value": 1}', created_at),
            )
        for age in (364, 365, 366):
            created_at = (fixed_now - timedelta(days=age)).isoformat(timespec="seconds")
            task_ids[age] = db._last_insert_id(
                cur,
                """
                INSERT INTO closing_review_tasks
                    (user_id, conversation_id, client_request_id, task_kind, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, conversation["id"], f"retention-{age}", "user_message", "succeeded", created_at),
            )

    result = store.redact_expired_content(now=fixed_now)

    with db.connect() as conn:
        rows = {
            age: conn.execute(
                "SELECT content, structured_payload, redacted_at FROM closing_review_messages WHERE id = ?",
                (message_ids[age],),
            ).fetchone()
            for age in (89, 90, 91)
        }
        remaining_tasks = {
            age: conn.execute(
                "SELECT id FROM closing_review_tasks WHERE id = ?",
                (task_ids[age],),
            ).fetchone()
            for age in (364, 365, 366)
        }

    assert result["redacted_messages"] == 1
    assert rows[89]["content"] == "内容 89"
    assert rows[90]["content"] == "内容 90"
    assert rows[91]["content"] is None
    assert rows[91]["structured_payload"] is None
    assert rows[91]["redacted_at"] == "2026-09-03T12:00:00+08:00"
    assert remaining_tasks[364] is not None
    assert remaining_tasks[365] is not None
    assert remaining_tasks[366] is None
