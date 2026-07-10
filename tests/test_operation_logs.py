import os
import inspect
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


def insert_log(user_id, operation_type, created_at, description="测试日志"):
    with db.connect() as conn:
        cur = conn.cursor()
        db._exec(
            cur,
            """
            INSERT INTO operation_logs
                (user_id, module_code, operation_type, description, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, "user_management", operation_type, description, created_at),
        )
        return db.last_insert_id(conn)


def test_operation_logs_use_stable_cursor_without_duplicates(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    for index in range(5):
        insert_log(admin["id"], "编辑用户", "2026-07-10 10:00:00", f"日志 {index}")

    first = main.list_operation_logs(limit=2, user=admin)
    second = main.list_operation_logs(limit=2, cursor=first["next_cursor"], user=admin)

    assert len(first["logs"]) == 2
    assert first["has_more"] is True
    assert first["next_cursor"]
    assert len(second["logs"]) == 2
    assert {row["id"] for row in first["logs"]}.isdisjoint(row["id"] for row in second["logs"])
    assert first["logs"][0]["id"] > first["logs"][1]["id"]
    assert "pagination" not in first


def test_operation_logs_default_to_100_and_cap_at_200(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)
    admin = admin_user()
    for index in range(205):
        insert_log(admin["id"], "登录", f"2026-07-10 10:{index // 60:02d}:{index % 60:02d}")

    default_page = main.list_operation_logs(user=admin)
    capped_page = main.list_operation_logs(limit=999, user=admin)

    assert len(default_page["logs"]) == 100
    assert len(capped_page["logs"]) == 200
    assert capped_page["has_more"] is True


def test_operation_logs_filter_by_user_type_and_inclusive_dates(tmp_path, monkeypatch):
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
            ("筛选用户", "filter-user", "贸易处", db.password_hash("filter-user"), "用户"),
        )
        target_id = db.last_insert_id(conn)
    insert_log(target_id, "添加用户", "2026-07-09 23:59:59", "范围外")
    expected_id = insert_log(target_id, "添加用户", "2026-07-10 23:59:59", "范围内")
    insert_log(target_id, "编辑用户", "2026-07-10 12:00:00", "类型不符")
    insert_log(admin["id"], "添加用户", "2026-07-10 12:00:00", "用户不符")
    insert_log(target_id, "添加用户", "2026-07-11 00:00:00", "结束日之后")

    result = main.list_operation_logs(
        operation_type="添加用户",
        user_name="筛选用户",
        start_date="2026-07-10",
        end_date="2026-07-10",
        user=admin,
    )

    assert [row["id"] for row in result["logs"]] == [expected_id]


def test_operation_logs_reject_invalid_cursor(tmp_path, monkeypatch):
    use_temp_db(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as exc:
        main.list_operation_logs(cursor="not-a-valid-cursor", user=admin_user())

    assert exc.value.status_code == 400
    assert "游标" in exc.value.detail


def test_operation_log_route_avoids_count_and_offset():
    source = inspect.getsource(main.list_operation_logs).upper()

    assert "COUNT(*)" not in source
    assert "OFFSET" not in source
