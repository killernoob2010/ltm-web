import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app import db, main


def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "operation-logs.db")
    db.init_db()


def admin_user():
    with db.connect() as conn:
        row = db._exec(conn.cursor(), "SELECT * FROM users WHERE username = ?", ("admin",)).fetchone()
    return dict(row)


def test_init_db_creates_operation_log_archive_schema_and_indexes(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    db.init_db()

    with db.connect() as conn:
        indexes = {row["name"] for row in conn.execute("PRAGMA index_list('operation_logs')")}
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert {
        "idx_operation_logs_created_id",
        "idx_operation_logs_user_created_id",
        "idx_operation_logs_type_created_id",
    } <= indexes
    assert {"operation_log_archives", "operation_log_archive_users"} <= tables


def test_archived_operation_history_prevents_physical_user_deletion(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO users (name, username, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("归档用户", "archive-user", "贸易处", db.password_hash("archive-user"), "用户"),
        )
        user_id = db.last_insert_id(conn)
        db._exec(
            cur,
            """
            INSERT INTO operation_log_archives
                (period_start, period_end, object_path, row_count, first_created_at,
                 last_created_at, sha256, compressed_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2025-01-01",
                "2025-02-01",
                "staging/2025/01/operation-logs-2025-01.ndjson.gz",
                1,
                "2025-01-02 10:00:00",
                "2025-01-02 10:00:00",
                "a" * 64,
                100,
            ),
        )
        archive_id = db.last_insert_id(conn)
        db._exec(
            cur,
            "INSERT INTO operation_log_archive_users (archive_id, user_id) VALUES (?, ?)",
            (archive_id, user_id),
        )

    with pytest.raises(HTTPException) as exc:
        main.delete_user(user_id, user=admin)

    assert exc.value.status_code == 400
    assert "会话或操作历史" in exc.value.detail

