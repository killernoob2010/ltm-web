"""Schema and permission boundary tests for the closing review Agent."""

import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db


EXPECTED_AGENT_TABLES = (
    "closing_review_conversations",
    "closing_review_messages",
    "closing_review_tasks",
)


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "closing-review-agent.db")
    db.init_db()


def table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def unique_index_columns(conn, table_name):
    indexes = conn.execute(f"PRAGMA index_list({table_name})").fetchall()
    unique_columns = []
    for index in indexes:
        if not index["unique"]:
            continue
        columns = conn.execute(f"PRAGMA index_info({index['name']})").fetchall()
        unique_columns.append(tuple(column["name"] for column in columns))
    return unique_columns


def test_closing_review_agent_tables_and_indexes_are_created(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    with db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert set(EXPECTED_AGENT_TABLES) <= tables

        assert table_columns(conn, "closing_review_conversations") == {
            "id", "user_id", "channel", "kind", "title", "system_key", "status",
            "last_message_at", "created_at", "updated_at",
        }
        assert table_columns(conn, "closing_review_messages") == {
            "id", "conversation_id", "task_id", "role", "message_type", "content",
            "structured_payload", "status", "supersedes_message_id", "created_at",
            "redacted_at",
        }
        assert table_columns(conn, "closing_review_tasks") == {
            "id", "user_id", "conversation_id", "user_message_id", "client_request_id",
            "task_kind", "task_profile", "target_date", "state", "validated_intent",
            "result_projection", "model_provider", "model_name", "prompt_version",
            "model_usage", "model_finish_reason", "model_duration_seconds",
            "workflow_version", "calculation_version", "rule_version", "retry_count",
            "error_category", "source_signature", "started_at", "finished_at", "created_at",
        }

        assert ("user_id", "channel", "system_key") in unique_index_columns(
            conn, "closing_review_conversations"
        )
        assert ("user_id", "client_request_id") in unique_index_columns(
            conn, "closing_review_tasks"
        )
        index_names = {
            row["name"]
            for table in EXPECTED_AGENT_TABLES
            for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        }
        assert {
            "idx_closing_review_conversations_owner_last_message",
            "idx_closing_review_messages_conversation_id",
            "idx_closing_review_tasks_user_date",
            "idx_closing_review_tasks_source_signature",
        } <= index_names


def test_closing_review_agent_schema_is_idempotent(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    db.init_db()

    with db.connect() as conn:
        for table_name in EXPECTED_AGENT_TABLES:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table_name,),
            ).fetchone()["count"]
            assert count == 1


def test_agent_table_security_contract_is_declared():
    assert tuple(db.CLOSING_REVIEW_AGENT_TABLES) == EXPECTED_AGENT_TABLES
